#!/usr/bin/env python3
"""Restart-safe collector for exact production inference source frames."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import struct
import time
import urllib.error
import urllib.request
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .capture_stream import CaptureError, PNG_SIGNATURE


SCHEMA_VERSION = "production-training-frame-1.0"
FRAME_ID_LENGTH = 32
TEMP_SUFFIX = ".part"


@dataclass(frozen=True)
class CollectorConfig:
    base_url: str
    output_root: Path
    timeout_seconds: float = 15.0
    idle_poll_seconds: float = 1.0
    busy_poll_seconds: float = 0.25
    retry_max_seconds: float = 10.0
    minimum_free_bytes: int = 5 * 1024**3
    session_name: str | None = None

    @classmethod
    def from_file(cls, path: Path) -> "CollectorConfig":
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CaptureError(f"could not read collector config {path}: {error}") from error
        if not isinstance(document, dict):
            raise CaptureError("collector config must be a JSON object")
        try:
            return cls(
                base_url=str(document["base_url"]).rstrip("/"),
                output_root=Path(document["output_root"]),
                timeout_seconds=float(document.get("timeout_seconds", 15.0)),
                idle_poll_seconds=float(document.get("idle_poll_seconds", 1.0)),
                busy_poll_seconds=float(document.get("busy_poll_seconds", 0.25)),
                retry_max_seconds=float(document.get("retry_max_seconds", 10.0)),
                minimum_free_bytes=int(document.get("minimum_free_bytes", 5 * 1024**3)),
                session_name=document.get("session_name"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CaptureError(f"invalid collector config: {error}") from error


def _is_frame_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == FRAME_ID_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CaptureError(f"production returned invalid {label} JSON") from error
    if not isinstance(value, dict):
        raise CaptureError(f"production returned non-object {label} JSON")
    return value


def _utc_text() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _captured_date(value: Any) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CaptureError("sidecar captured_utc is not a UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise CaptureError("sidecar captured_utc is invalid") from error
    return parsed.date().isoformat()


def _nearest_existing(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists():
        if candidate.parent == candidate:
            raise CaptureError(f"cannot resolve storage volume for {path}")
        candidate = candidate.parent
    return candidate


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _verify_png(payload: bytes) -> tuple[int, int]:
    """Verify the non-interlaced PNG structure and compressed pixel stream."""
    if not payload.startswith(PNG_SIGNATURE):
        raise CaptureError("capture is not a lossless PNG")
    offset = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    compressed = bytearray()
    saw_iend = False
    while offset + 12 <= len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        chunk_type = payload[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(payload):
            raise CaptureError("capture PNG contains a truncated chunk")
        chunk_data = payload[data_start:data_end]
        expected_crc = struct.unpack(">I", payload[data_end:crc_end])[0]
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            raise CaptureError("capture PNG contains a bad chunk checksum")
        if chunk_type == b"IHDR":
            if length != 13 or width is not None:
                raise CaptureError("capture PNG contains an invalid IHDR")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            if width <= 0 or height <= 0 or compression != 0 or filtering != 0:
                raise CaptureError("capture PNG has invalid image properties")
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            if length != 0 or crc_end != len(payload):
                raise CaptureError("capture PNG has an invalid terminal chunk")
            saw_iend = True
            break
        offset = crc_end
    if width is None or not compressed or not saw_iend:
        raise CaptureError("capture PNG is incomplete")
    if interlace != 0:
        raise CaptureError("interlaced production PNGs are unsupported")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None or bit_depth not in (1, 2, 4, 8, 16):
        raise CaptureError("capture PNG uses an unsupported pixel layout")
    row_bytes = (width * channels * bit_depth + 7) // 8
    try:
        decoded = zlib.decompress(bytes(compressed))
    except zlib.error as error:
        raise CaptureError("capture PNG pixel stream is corrupt") from error
    if len(decoded) != height * (row_bytes + 1):
        raise CaptureError("capture PNG decoded byte count is invalid")
    return width, height


def validate_capture(frame_id: str, metadata: dict[str, Any], image: bytes) -> str:
    """Validate the immutable board sidecar and lossless PNG payload."""
    if not _is_frame_id(frame_id):
        raise CaptureError("pending index contains a malformed frame ID")
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise CaptureError("production capture schema is incompatible")
    if metadata.get("frame_id") != frame_id:
        raise CaptureError("sidecar frame ID does not match the pending entry")
    image_info = metadata.get("image")
    if not isinstance(image_info, dict):
        raise CaptureError("sidecar has no image object")
    if image_info.get("file") != f"{frame_id}.png":
        raise CaptureError("sidecar image filename does not match its frame ID")
    if image_info.get("media_type") != "image/png" or not image.startswith(PNG_SIGNATURE):
        raise CaptureError("capture is not a lossless PNG")
    if image_info.get("bytes") != len(image):
        raise CaptureError("capture byte count does not match its sidecar")
    digest = _sha256(image)
    if image_info.get("sha256") != digest:
        raise CaptureError("capture SHA-256 does not match its sidecar")
    width, height = _verify_png(image)
    if image_info.get("width") != width or image_info.get("height") != height:
        raise CaptureError("decoded PNG dimensions do not match its sidecar")
    if image_info.get("pixel_format") != "BGR8":
        raise CaptureError("sidecar pixel format is incompatible")
    if metadata.get("training_label_status") != "unannotated":
        raise CaptureError("sidecar does not identify an unannotated frame")
    _captured_date(metadata.get("captured_utc"))
    return digest


class BoardClient:
    def __init__(self, base_url: str, timeout_seconds: float):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _request(self, path: str, method: str = "GET", body: bytes | None = None) -> tuple[bytes, str]:
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Accept": "application/json, image/png", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read(), response.headers.get_content_type().lower()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise CaptureError(f"board {method} {path} failed with HTTP {error.code}: {detail}") from error
        except Exception as error:
            raise CaptureError(f"board {method} {path} failed: {error}") from error

    def health(self) -> dict[str, Any]:
        payload, media_type = self._request("/training_capture/health")
        if media_type != "application/json":
            raise CaptureError("capture health response is not JSON")
        result = _json_object(payload, "capture health")
        if result.get("schema_version") != SCHEMA_VERSION:
            raise CaptureError("production capture schema is incompatible")
        return result

    def pending(self) -> list[dict[str, Any]]:
        payload, media_type = self._request("/training_frames")
        if media_type != "application/json":
            raise CaptureError("pending-frame response is not JSON")
        result = _json_object(payload, "pending-frame")
        if result.get("schema_version") != SCHEMA_VERSION or not isinstance(result.get("frames"), list):
            raise CaptureError("pending-frame response has an incompatible contract")
        return result["frames"]

    def metadata(self, frame_id: str) -> tuple[bytes, dict[str, Any]]:
        payload, media_type = self._request(f"/training_frames/{frame_id}")
        if media_type != "application/json":
            raise CaptureError("frame sidecar response is not JSON")
        return payload, _json_object(payload, "frame sidecar")

    def image(self, frame_id: str) -> bytes:
        payload, media_type = self._request(f"/training_frames/{frame_id}.png")
        if media_type != "image/png":
            raise CaptureError("frame response is not PNG")
        return payload

    def acknowledge(self, frame_id: str, digest: str) -> None:
        payload = json.dumps({"image_sha256": digest}, separators=(",", ":")).encode("utf-8")
        response, media_type = self._request(
            f"/training_frames/{frame_id}/ack", method="POST", body=payload
        )
        if media_type != "application/json" or _json_object(response, "acknowledgement").get("status") != "acknowledged":
            raise CaptureError("board did not confirm capture acknowledgement")


class SingleInstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self) -> "SingleInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                self.handle.write(b"0")
                self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            self.handle.close()
            self.handle = None
            raise CaptureError("another production collector is already running") from error
        return self

    def __exit__(self, *_args: Any) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


class Collector:
    def __init__(self, config: CollectorConfig, client: BoardClient | None = None):
        self.config = config
        self.client = client or BoardClient(config.base_url, config.timeout_seconds)

    def _ensure_space(self) -> None:
        available = shutil.disk_usage(_nearest_existing(self.config.output_root)).free
        if available < self.config.minimum_free_bytes:
            raise CaptureError(
                f"capture storage has {available} free bytes; minimum is {self.config.minimum_free_bytes}"
            )

    def _write_atomic_or_verify(self, path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if _sha256(path.read_bytes()) != _sha256(payload):
                raise CaptureError(f"refusing to overwrite colliding permanent file: {path}")
            return
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}{TEMP_SUFFIX}")
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                if _sha256(path.read_bytes()) != _sha256(payload):
                    raise CaptureError(f"refusing to overwrite colliding permanent file: {path}")
                return
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _event(self, event: str, **fields: Any) -> None:
        record = {"utc": _utc_text(), "event": event, **fields}
        path = self.config.output_root / "collector-events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    def _status(self, state: str, **fields: Any) -> None:
        document = {"updated_utc": _utc_text(), "state": state, **fields}
        path = self.config.output_root / "collector-status.json"
        payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
        temporary = path.with_name(path.name + TEMP_SUFFIX)
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def collect_frame(self, frame_id: str) -> Path:
        self._ensure_space()
        metadata_payload, metadata = self.client.metadata(frame_id)
        image = self.client.image(frame_id)
        digest = validate_capture(frame_id, metadata, image)
        date = _captured_date(metadata["captured_utc"])
        image_path = self.config.output_root / date / "images" / f"{frame_id}.png"
        metadata_path = self.config.output_root / date / "metadata" / f"{frame_id}.json"
        self._write_atomic_or_verify(image_path, image)
        self._write_atomic_or_verify(metadata_path, metadata_payload)
        if _sha256(image_path.read_bytes()) != digest:
            raise CaptureError("permanent image failed post-write SHA-256 verification")
        self.client.acknowledge(frame_id, digest)
        self._event(
            "capture_saved",
            frame_id=frame_id,
            image=str(image_path),
            image_sha256=digest,
            session_name=self.config.session_name,
        )
        return image_path

    def collect_once(self) -> int:
        self._ensure_space()
        self.client.health()
        frames = self.client.pending()
        ordered = sorted(frames, key=lambda item: (str(item.get("captured_utc", "")), str(item.get("frame_id", ""))))
        saved = 0
        for entry in ordered:
            frame_id = entry.get("frame_id") if isinstance(entry, dict) else None
            if not _is_frame_id(frame_id):
                raise CaptureError("pending index contains a malformed frame ID")
            self.collect_frame(frame_id)
            saved += 1
        self._status("healthy", pending_seen=len(ordered), saved=saved, board_url=self.config.base_url)
        return saved

    def run_forever(self) -> None:
        delay = self.config.idle_poll_seconds
        while True:
            try:
                saved = self.collect_once()
                delay = self.config.busy_poll_seconds if saved else self.config.idle_poll_seconds
            except CaptureError as error:
                self._status("error", error=str(error), board_url=self.config.base_url)
                self._event("collector_error", error=str(error))
                delay = min(max(delay * 2, self.config.idle_poll_seconds), self.config.retry_max_seconds)
            time.sleep(delay)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect exact raw frames from production inference")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true", help="drain current pending frames and exit")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = CollectorConfig.from_file(args.config)
        collector = Collector(config)
        with SingleInstanceLock(config.output_root / ".collector.lock"):
            if args.once:
                count = collector.collect_once()
                print(f"COLLECTED {count} frame(s)")
            else:
                print(f"COLLECTOR RUNNING board={config.base_url} output={config.output_root}")
                collector.run_forever()
    except KeyboardInterrupt:
        print("COLLECTOR STOPPED")
        return 0
    except CaptureError as error:
        print(f"COLLECTOR FAILED: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
