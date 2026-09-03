#!/usr/bin/env python3
"""Time existing SiMa HTTP inference calls without changing the board service.

This client sends POST /run_inference requests to the already-running service.
It does not write to the board filesystem or issue any camera, PLC, or pipeline
start/stop command.  The service itself may retain its normal runtime reports.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min_ms": None, "median_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None, "mean_ms": None, "stdev_ms": None}
    return {
        "count": len(values),
        "min_ms": round(min(values), 3),
        "median_ms": round(statistics.median(values), 3),
        "p95_ms": round(percentile(values, 95), 3),
        "p99_ms": round(percentile(values, 99), 3),
        "max_ms": round(max(values), 3),
        "mean_ms": round(statistics.fmean(values), 3),
        "stdev_ms": round(statistics.stdev(values), 3) if len(values) > 1 else 0.0,
    }


def run_call(url: str, timeout: float, sequence: int) -> dict[str, Any]:
    started_utc = utc_now()
    started_ns = time.perf_counter_ns()
    record: dict[str, Any] = {"sequence": sequence, "started_utc": started_utc}
    request = Request(url, data=b"", method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            record["http_status"] = response.status
            record["response_bytes"] = len(body)
            try:
                payload = json.loads(body)
                record["service_status"] = payload.get("status", "ok")
                detections = payload.get("detections")
                if isinstance(detections, list):
                    record["detection_count"] = len(detections)
            except json.JSONDecodeError:
                record["response_json"] = "invalid"
    except HTTPError as error:
        body = error.read()
        record.update({"http_status": error.code, "response_bytes": len(body), "error": f"HTTP {error.code}"})
    except (URLError, OSError) as error:
        record["error"] = str(error)
    finally:
        record["completed_utc"] = utc_now()
        record["round_trip_ms"] = round((time.perf_counter_ns() - started_ns) / 1_000_000, 3)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure existing SiMa /run_inference HTTP round-trip time.")
    parser.add_argument("--base-url", default="http://192.168.1.20:5001")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--interval", type=float, default=0.5, help="Seconds between completed calls.")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.count < 1 or args.interval < 0 or args.timeout <= 0:
        parser.error("count must be >= 1, interval must be >= 0, and timeout must be > 0")

    endpoint = f"{args.base_url.rstrip('/')}/run_inference"
    records: list[dict[str, Any]] = []
    for sequence in range(1, args.count + 1):
        record = run_call(endpoint, args.timeout, sequence)
        records.append(record)
        state = record.get("error") or record.get("service_status", "ok")
        print(f"{sequence:02d}/{args.count}: {record['round_trip_ms']:.3f} ms ({state})", flush=True)
        if sequence != args.count and args.interval:
            time.sleep(args.interval)

    successful = [record["round_trip_ms"] for record in records if "error" not in record and record.get("http_status") == 200]
    batch = {
        "schema_version": "sima-api-timing-1.0",
        "measurement": "Admin-PC HTTP POST /run_inference round-trip; includes server polling, parsing, report generation, response transfer, and client overhead. It is not MLA-only inference time.",
        "endpoint": endpoint,
        "started_utc": records[0]["started_utc"],
        "completed_utc": records[-1]["completed_utc"],
        "requested_calls": args.count,
        "successful_http_200_calls": len(successful),
        "summary": summary(successful),
        "calls": records,
    }
    output = args.output or Path("results/timing") / f"sima-api-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(batch, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {output.resolve()}")
    print(json.dumps(batch["summary"], indent=2))
    return 0 if len(successful) == args.count else 1


if __name__ == "__main__":
    raise SystemExit(main())
