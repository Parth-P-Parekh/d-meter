#!/usr/bin/env python3
"""Summarise filtered Ethernet/IPv4/TCP traffic from a PCAPNG capture.

The tool is intentionally read-only.  It reports what a single Admin-PC capture
can prove: TCP-flow counts, captured payload volume, and TCP SYN-to-SYN/ACK
round trips.  It does not label a one-ended capture as one-way network latency.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import struct
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

EPB = 0x00000006
IDB = 0x00000001
SHB = 0x0A0D0D0A


def blocks(path: Path) -> Iterator[tuple[int, bytes]]:
    with path.open("rb") as handle:
        while header := handle.read(8):
            if len(header) != 8:
                raise ValueError("truncated PCAPNG block header")
            block_type, length = struct.unpack("<II", header)
            if length < 12:
                raise ValueError(f"invalid PCAPNG block length {length}")
            body = handle.read(length - 12)
            trailer = handle.read(4)
            if len(body) != length - 12 or len(trailer) != 4:
                raise ValueError("truncated PCAPNG block")
            if struct.unpack("<I", trailer)[0] != length:
                raise ValueError("PCAPNG block length mismatch")
            yield block_type, body


def options(data: bytes) -> Iterator[tuple[int, bytes]]:
    offset = 0
    while offset + 4 <= len(data):
        code, length = struct.unpack_from("<HH", data, offset)
        offset += 4
        value = data[offset:offset + length]
        offset += (length + 3) & ~3
        if code == 0:
            return
        yield code, value


def parse_tcp(packet: bytes) -> dict[str, Any] | None:
    if len(packet) < 14:
        return None
    ether_type = struct.unpack_from("!H", packet, 12)[0]
    offset = 14
    while ether_type in (0x8100, 0x88A8):
        if len(packet) < offset + 4:
            return None
        ether_type = struct.unpack_from("!H", packet, offset + 2)[0]
        offset += 4
    if ether_type != 0x0800 or len(packet) < offset + 20:
        return None
    version_ihl = packet[offset]
    if version_ihl >> 4 != 4:
        return None
    ip_length = (version_ihl & 0x0F) * 4
    if ip_length < 20 or len(packet) < offset + ip_length:
        return None
    total_length = struct.unpack_from("!H", packet, offset + 2)[0]
    if packet[offset + 9] != 6:
        return None
    source = str(ipaddress.IPv4Address(packet[offset + 12:offset + 16]))
    destination = str(ipaddress.IPv4Address(packet[offset + 16:offset + 20]))
    tcp_offset = offset + ip_length
    if len(packet) < tcp_offset + 20:
        return None
    source_port, destination_port, sequence, acknowledgement = struct.unpack_from("!HHII", packet, tcp_offset)
    tcp_length = (packet[tcp_offset + 12] >> 4) * 4
    if tcp_length < 20:
        return None
    payload_length = max(0, total_length - ip_length - tcp_length)
    return {
        "source": source, "destination": destination,
        "source_port": source_port, "destination_port": destination_port,
        "sequence": sequence, "acknowledgement": acknowledgement,
        "flags": packet[tcp_offset + 13], "payload_bytes": payload_length,
    }


def timestamp_factor(option_value: bytes | None) -> float:
    if not option_value:
        return 1e-6  # PCAPNG default: microseconds.
    resolution = option_value[0]
    return 2 ** -(resolution & 0x7F) if resolution & 0x80 else 10 ** -resolution


def direction(event: dict[str, Any], pc_ip: str) -> str:
    return "pc_to_peer" if event["source"] == pc_ip else "peer_to_pc"


def summarize(events: list[dict[str, Any]], pc_ip: str, peer_ip: str, port: int) -> dict[str, Any]:
    selected = [event for event in events if {event["source"], event["destination"]} == {pc_ip, peer_ip} and port in (event["source_port"], event["destination_port"])]
    selected.sort(key=lambda event: event["timestamp"])
    payload = defaultdict(int)
    flows: dict[tuple[str, int, str, int], list[dict[str, Any]]] = defaultdict(list)
    connections: dict[tuple[tuple[str, int], tuple[str, int]], list[dict[str, Any]]] = defaultdict(list)
    for event in selected:
        payload[direction(event, pc_ip)] += event["payload_bytes"]
        flows[(event["source"], event["source_port"], event["destination"], event["destination_port"])].append(event)
        endpoints = tuple(sorted(((event["source"], event["source_port"]), (event["destination"], event["destination_port"])) ))
        connections[endpoints].append(event)

    handshakes: list[float] = []
    seen_syn: set[tuple[str, int, str, int, int]] = set()
    for event in selected:
        syn = event["flags"] & 0x02
        ack = event["flags"] & 0x10
        if not (event["source"] == pc_ip and syn and not ack):
            continue
        key = (event["source"], event["source_port"], event["destination"], event["destination_port"], event["sequence"])
        if key in seen_syn:
            continue
        seen_syn.add(key)
        expected_ack = (event["sequence"] + 1) & 0xFFFFFFFF
        replies = [candidate for candidate in selected if candidate["source"] == peer_ip and candidate["destination"] == pc_ip and candidate["source_port"] == event["destination_port"] and candidate["destination_port"] == event["source_port"] and candidate["timestamp"] >= event["timestamp"] and candidate["flags"] & 0x12 == 0x12 and candidate["acknowledgement"] == expected_ack]
        if replies:
            handshakes.append((replies[0]["timestamp"] - event["timestamp"]) * 1000)

    def stats(values: list[float]) -> dict[str, float | int | None]:
        if not values:
            return {"count": 0, "min_ms": None, "median_ms": None, "max_ms": None}
        ordered = sorted(values)
        middle = len(ordered) // 2
        median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
        return {"count": len(values), "min_ms": round(min(values), 3), "median_ms": round(median, 3), "max_ms": round(max(values), 3)}

    payload_spans: list[float] = []
    for connection_events in connections.values():
        data_events = [event for event in connection_events if event["payload_bytes"]]
        if len(data_events) > 1:
            payload_spans.append((max(event["timestamp"] for event in data_events) - min(event["timestamp"] for event in data_events)) * 1000)

    return {
        "peer": peer_ip,
        "port": port,
        "captured_packets": len(selected),
        "observed_connections": len(connections),
        "observed_unidirectional_flows": len(flows),
        "captured_payload_bytes": dict(payload),
        "tcp_handshake_round_trip": stats(handshakes),
        "payload_transfer_span_at_pc": stats(payload_spans),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyse a single-host PCAPNG capture.")
    parser.add_argument("--pcap", type=Path, required=True)
    parser.add_argument("--pc-ip", default="192.168.1.17")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    interfaces: dict[int, float] = {}
    events: list[dict[str, Any]] = []
    for block_type, body in blocks(args.pcap):
        if block_type == SHB:
            interfaces.clear()
        elif block_type == IDB:
            interface_id = len(interfaces)
            interfaces[interface_id] = timestamp_factor(next((value for code, value in options(body[8:]) if code == 9), None))
        elif block_type == EPB:
            if len(body) < 20:
                continue
            interface_id, high, low, captured_length, _original_length = struct.unpack_from("<IIIII", body)
            packet = body[20:20 + captured_length]
            parsed = parse_tcp(packet)
            if parsed is None:
                continue
            factor = interfaces.get(interface_id, 1e-6)
            parsed["timestamp"] = ((high << 32) | low) * factor
            events.append(parsed)

    report = {
        "schema_version": "pcapng-network-summary-1.0",
        "capture": str(args.pcap),
        "scope": "Single Admin-PC capture. TCP handshake figures are observed round trips; payload timing does not establish one-way network latency.",
        "tcp_packets_parsed": len(events),
        "channels": {
            "admin_pc_to_sima_http": summarize(events, args.pc_ip, "192.168.1.20", 5001),
            "admin_pc_to_sima_scp": summarize(events, args.pc_ip, "192.168.1.20", 22),
            "admin_pc_to_plc_modbus": summarize(events, args.pc_ip, "192.168.1.7", 502),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
