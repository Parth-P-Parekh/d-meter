#!/usr/bin/env python3
"""Persist unannotated frames exposed by the production multipart stream."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import struct
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator, Sequence


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8"
JPEG_END = b"\xff\xd9"
SUPPORTED_MEDIA_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}
PART_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
TEMP_SUFFIX = ".capture-tmp"


class CaptureError(RuntimeError):
    """A safe, user-facing capture failure."""


@dataclass(frozen=True, slots=True)
class SavedFrame:
    image: Path
    metadata: Path
    sha256: str
    width: int
    height: int
    media_type: str


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def validate_part_id(value: str) -> str:
    if not PART_ID_RE.fullmatch(value):
        raise CaptureError("part ID must contain only letters, digits, '.', '_' or '-'")
    return value


def png_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 24 or not payload.startswith(PNG_SIGNATURE) or payload[12:16] != b"IHDR":
        raise CaptureError("production stream returned an invalid PNG")
    width, height = struct.unpack(">II", payload[16:24])
    if width <= 0 or height <= 0:
        raise CaptureError("production stream returned invalid image dimensions")
    return width, height


def jpeg_dimensions(payload: bytes) -> tuple[int, int]:
    if not payload.startswith(JPEG_SIGNATURE) or not payload.endswith(JPEG_END):
        raise CaptureError("production stream returned an invalid JPEG")
    start_of_frame = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    offset = 2
    while offset < len(payload):
        if payload[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(payload) and payload[offset] == 0xFF:
            offset += 1
        if offset >= len(payload):
            break
        marker = payload[offset]
        offset += 1
        if marker in (0x00, 0x01, 0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(payload):
            break
        segment_length = struct.unpack(">H", payload[offset:offset + 2])[0]
        if segment_length < 2 or offset + segment_length > len(payload):
            break
        if marker in start_of_frame:
            if segment_length < 7:
                break
            height, width = struct.unpack(">HH", payload[offset + 3:offset + 7])
            if width <= 0 or height <= 0:
                break
            return width, height
        offset += segment_length
    raise CaptureError("production stream returned a JPEG without valid dimensions")


def image_dimensions(payload: bytes, media_type: str) -> tuple[int, int]:
    if media_type == "image/png":
        return png_dimensions(payload)
    if media_type == "image/jpeg":
        return jpeg_dimensions(payload)
    raise CaptureError(f"unsupported production image type: {media_type}")


def iter_multipart_frames(
    stream: BinaryIO, boundary: str | bytes, chunk_size: int = 64 * 1024
) -> Iterator[tuple[str, bytes]]:
    """Parse image parts, including production parts without Content-Length."""
    boundary_bytes = boundary.encode("ascii") if isinstance(boundary, str) else boundary
    boundary_bytes = boundary_bytes.removeprefix(b"--")
    if not boundary_bytes or b"\r" in boundary_bytes or b"\n" in boundary_bytes:
        raise CaptureError("production stream returned an invalid multipart boundary")
    marker = b"--" + boundary_bytes
    delimiter = b"\r\n" + marker
    buffer = bytearray()

    def receive() -> None:
        chunk = stream.read(chunk_size)
        if not chunk:
            raise CaptureError("production image stream ended mid-part")
        buffer.extend(chunk)

    while True:
        position = buffer.find(marker)
        while position < 0:
            receive()
            position = buffer.find(marker)
        if position:
            del buffer[:position]
        while len(buffer) < len(marker) + 2:
            receive()
        del buffer[:len(marker)]
        if buffer.startswith(b"--"):
            return
        if not buffer.startswith(b"\r\n"):
            raise CaptureError("malformed multipart boundary in production stream")
        del buffer[:2]

        header_end = buffer.find(b"\r\n\r\n")
        while header_end < 0:
            receive()
            header_end = buffer.find(b"\r\n\r\n")
        header_blob = bytes(buffer[:header_end])
        del buffer[:header_end + 4]
        headers: dict[str, str] = {}
        for line in header_blob.split(b"\r\n"):
            if b":" not in line:
                raise CaptureError("malformed image-part header in production stream")
            name, value = line.split(b":", 1)
            headers[name.decode("ascii").strip().lower()] = value.decode("ascii").strip()
        media_type = headers.get("content-type", "").split(";", 1)[0].lower()
        if media_type not in SUPPORTED_MEDIA_TYPES:
            raise CaptureError(f"unsupported production image type: {media_type or '<missing>'}")

        part_end = buffer.find(delimiter)
        while part_end < 0:
            receive()
            part_end = buffer.find(delimiter)
        payload = bytes(buffer[:part_end])
        del buffer[:part_end + 2]
        if "content-length" in headers:
            try:
                declared_length = int(headers["content-length"])
            except ValueError as error:
                raise CaptureError("invalid image-part Content-Length") from error
            if declared_length != len(payload):
                raise CaptureError("image-part Content-Length does not match payload")
        image_dimensions(payload, media_type)
        yield media_type, payload


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + TEMP_SUFFIX)
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise CaptureError(f"refusing to overwrite existing file: {path}")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def save_frame(
    root: Path,
    part_id: str,
    payload: bytes,
    media_type: str,
    source_url: str,
    extra_metadata: dict[str, object] | None = None,
) -> SavedFrame:
    captured = utc_now()
    capture_id = uuid.uuid4().hex[:12]
    stem = f"{part_id}_{captured.strftime('%Y%m%dT%H%M%S.%fZ')}_{capture_id}"
    width, height = image_dimensions(payload, media_type)
    digest = hashlib.sha256(payload).hexdigest()
    image = root / part_id / f"{stem}{SUPPORTED_MEDIA_TYPES[media_type]}"
    metadata = image.with_suffix(".json")
    document = {
        "schema_version": "production-source-frame-1.0",
        "capture_id": capture_id,
        "part_id": part_id,
        "captured_utc": captured.isoformat().replace("+00:00", "Z"),
        "source_url": source_url,
        "source_contract": "existing production display branch; unannotated encoded frame",
        "media_type": media_type,
        "width": width,
        "height": height,
        "image_sha256": digest,
        "image_file": image.name,
    }
    if extra_metadata:
        document.update(extra_metadata)
    atomic_write(image, payload)
    try:
        atomic_write(metadata, (json.dumps(document, indent=2) + "\n").encode("utf-8"))
    except Exception:
        # Never leave an undocumented image presented as a complete capture.
        image.unlink(missing_ok=True)
        raise
    return SavedFrame(image, metadata, digest, width, height, media_type)


def capture_frames(
    source_url: str,
    output_root: Path,
    part_id: str,
    count: int,
    interval_ms: int,
    timeout_seconds: float,
) -> list[SavedFrame]:
    validate_part_id(part_id)
    if count < 1:
        raise CaptureError("count must be at least one")
    if interval_ms < 0:
        raise CaptureError("interval must not be negative")
    request = urllib.request.Request(source_url, headers={"Accept": "multipart/x-mixed-replace"})
    saved: list[SavedFrame] = []
    next_save = 0.0
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get_content_type()
            if content_type != "multipart/x-mixed-replace":
                raise CaptureError(f"unexpected production response type: {content_type}")
            boundary = response.headers.get_param("boundary")
            if not boundary:
                raise CaptureError("production multipart response has no boundary")
            for media_type, payload in iter_multipart_frames(response, boundary):
                now = time.monotonic()
                if now < next_save:
                    continue
                saved.append(save_frame(output_root, part_id, payload, media_type, source_url))
                if len(saved) == count:
                    return saved
                next_save = now + interval_ms / 1000.0
    except CaptureError:
        raise
    except Exception as error:
        raise CaptureError(f"could not read production image stream: {error}") from error
    raise CaptureError(f"stream ended after {len(saved)} of {count} requested frames")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Save clean frames from the existing production worker")
    parser.add_argument("--part-id", required=True)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval-ms", type=int, default=500)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--source-url", default="http://192.168.1.20:5001/video_feed/camera")
    parser.add_argument("--output-root", type=Path, default=Path("C:/Users/Admin/TrainingImages/PowerBoard"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        frames = capture_frames(
            args.source_url,
            args.output_root,
            args.part_id,
            args.count,
            args.interval_ms,
            args.timeout_seconds,
        )
    except CaptureError as error:
        print(f"CAPTURE FAILED: {error}")
        return 1
    for frame in frames:
        print(f"SAVED {frame.image} {frame.width}x{frame.height} sha256={frame.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
