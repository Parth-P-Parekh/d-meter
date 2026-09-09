#!/usr/bin/env python3
"""Trigger production inference and permanently save its exact source frame."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence

from .capture_stream import CaptureError, SavedFrame, save_frame, validate_part_id


FRAME_ID_HEADER = "X-Frame-Id"


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CaptureError(f"production returned invalid {label} JSON") from error
    if not isinstance(value, dict):
        raise CaptureError(f"production returned non-object {label} JSON")
    return value


def capture_inference_frame(
    base_url: str,
    output_root: Path,
    part_id: str,
    timeout_seconds: float,
) -> tuple[SavedFrame, dict[str, Any]]:
    """POST one inference, then fetch the source frame identified by its header."""
    validate_part_id(part_id)
    base_url = base_url.rstrip("/")
    inference_request = urllib.request.Request(
        f"{base_url}/run_inference",
        data=b"",
        method="POST",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(inference_request, timeout=timeout_seconds) as response:
            inference_payload = response.read()
            frame_id = response.headers.get(FRAME_ID_HEADER, "").strip()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise CaptureError(f"production inference failed with HTTP {error.code}: {detail}") from error
    except Exception as error:
        raise CaptureError(f"could not call production inference: {error}") from error

    inference = _json_object(inference_payload, "inference")
    if not frame_id:
        raise CaptureError(
            f"production response has no {FRAME_ID_HEADER}; the documented compatibility diff is not installed"
        )
    if len(frame_id) != 32 or any(character not in "0123456789abcdef" for character in frame_id):
        raise CaptureError(f"production returned malformed {FRAME_ID_HEADER}")

    frame_url = f"{base_url}/source_frame/{frame_id}"
    frame_request = urllib.request.Request(frame_url, headers={"Accept": "image/jpeg, image/png"})
    try:
        with urllib.request.urlopen(frame_request, timeout=timeout_seconds) as response:
            media_type = response.headers.get_content_type().lower()
            returned_id = response.headers.get(FRAME_ID_HEADER, "").strip()
            payload = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise CaptureError(f"exact source-frame fetch failed with HTTP {error.code}: {detail}") from error
    except Exception as error:
        raise CaptureError(f"could not fetch exact inference frame: {error}") from error
    if returned_id != frame_id:
        raise CaptureError("source-frame response ID does not match the inference response")

    saved = save_frame(
        output_root,
        part_id,
        payload,
        media_type,
        frame_url,
        {"production_frame_id": frame_id, "inference_result": inference},
    )
    return saved, inference


def capture_inference_frames(
    base_url: str,
    output_root: Path,
    part_id: str,
    count: int,
    interval_ms: int,
    timeout_seconds: float,
) -> list[SavedFrame]:
    if count < 1:
        raise CaptureError("count must be at least one")
    if interval_ms < 0:
        raise CaptureError("interval must not be negative")
    saved: list[SavedFrame] = []
    for index in range(count):
        frame, _ = capture_inference_frame(base_url, output_root, part_id, timeout_seconds)
        saved.append(frame)
        if index + 1 < count and interval_ms:
            time.sleep(interval_ms / 1000.0)
    return saved


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Save the exact clean frame used by production inference")
    parser.add_argument("--part-id", required=True)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval-ms", type=int, default=500)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--base-url", default="http://192.168.1.20:5001")
    parser.add_argument("--output-root", type=Path, default=Path("C:/Users/Admin/TrainingImages/PowerBoard"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        frames = capture_inference_frames(
            args.base_url,
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
