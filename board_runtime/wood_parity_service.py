#!/usr/bin/env python3
"""Offline recorded-image parity service for the installed Wood SiMa package.

This program never starts a camera, contacts the PLC/MES/SQL services, or changes
the installed package. Each POST uses one uploaded image and one temporary run
directory under /tmp/wood-parity-demo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path("/data/simaai/applications/Wood_simaaisrc")
RUN_ROOT = Path("/tmp/wood-parity-demo")
INPUT_WIDTH, INPUT_HEIGHT = 1280, 720
MODEL_WIDTH, MODEL_HEIGHT = 640, 640
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
FRAME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ALLOWED_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png"}

# These must be set before importing the SiMa GStreamer plugins.
os.environ.setdefault("LD_LIBRARY_PATH", str(PACKAGE_ROOT / "lib"))
os.environ.setdefault("GST_PLUGIN_PATH", str(PACKAGE_ROOT / "lib"))

import cv2  # noqa: E402
from flask import Flask, jsonify, request  # noqa: E402
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
RUN_LOCK = threading.Lock()
MODEL_SHA256: str | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_labels() -> list[str]:
    path = PACKAGE_ROOT / "share" / "overlay" / "labels"
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def model_version() -> str:
    global MODEL_SHA256
    if MODEL_SHA256 is None:
        MODEL_SHA256 = sha256_file(PACKAGE_ROOT / "share" / "processmla" / "yolov8_stage1_mla.elf")
    return MODEL_SHA256


def letterbox_transform() -> dict[str, float | int]:
    scale = min(MODEL_WIDTH / INPUT_WIDTH, MODEL_HEIGHT / INPUT_HEIGHT)
    resized_width, resized_height = INPUT_WIDTH * scale, INPUT_HEIGHT * scale
    return {
        "source_width": INPUT_WIDTH,
        "source_height": INPUT_HEIGHT,
        "model_width": MODEL_WIDTH,
        "model_height": MODEL_HEIGHT,
        "resize_scale_x": scale,
        "resize_scale_y": scale,
        "pad_left": (MODEL_WIDTH - resized_width) / 2,
        "pad_top": (MODEL_HEIGHT - resized_height) / 2,
        "pad_right": (MODEL_WIDTH - resized_width) / 2,
        "pad_bottom": (MODEL_HEIGHT - resized_height) / 2,
    }


def run_worker(input_path: Path, timeout_seconds: float) -> list[dict[str, Any]]:
    worker = Path(__file__).with_name("wood_parity_worker.py")
    command = ["/usr/bin/python3", str(worker), "--image", str(input_path), "--timeout", str(timeout_seconds)]
    try:
        completed = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(f"inference did not finish within {timeout_seconds:.1f} seconds") from error
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        detail = completed.stderr.strip().replace("\n", " ")[-1200:]
        raise RuntimeError(f"worker returned invalid JSON (exit {completed.returncode}): {detail}") from error
    if completed.returncode != 0:
        raise RuntimeError(f"worker failed: {payload.get('error', 'no error detail')}")
    if not isinstance(payload.get("detections"), list):
        raise RuntimeError("worker response is missing detections")
    return payload["detections"]


def parse_bbox_file(path: Path, labels: list[str]) -> list[dict[str, Any]]:
    data = path.read_bytes()
    if len(data) < 4:
        raise ValueError("bbox output is too short")
    count = struct.unpack_from("<i", data, 0)[0]
    if count < 0 or count > 100:
        raise ValueError(f"invalid bbox detection count: {count}")
    expected = 4 + count * 24
    if len(data) < expected:
        raise ValueError(f"bbox output is truncated: expected {expected} bytes, received {len(data)}")
    detections: list[dict[str, Any]] = []
    for index in range(count):
        offset = 4 + index * 24
        center_x, center_y, width, height = struct.unpack_from("<4I", data, offset)
        confidence = struct.unpack_from("<f", data, offset + 16)[0]
        class_id = struct.unpack_from("<I", data, offset + 20)[0]
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"detection {index} has invalid confidence {confidence}")
        if class_id >= len(labels):
            raise ValueError(f"detection {index} has unknown class ID {class_id}")
        x = float(center_x - width / 2)
        y = float(center_y - height / 2)
        detections.append({
            "class_name": labels[class_id],
            "confidence": float(confidence),
            "box_input": {"x": x, "y": y, "width": float(width), "height": float(height)},
        })
    return detections


def add_coordinate_forms(detections: list[dict[str, Any]], source_width: int, source_height: int) -> None:
    input_to_model = letterbox_transform()
    source_scale_x, source_scale_y = source_width / INPUT_WIDTH, source_height / INPUT_HEIGHT
    for detection in detections:
        box = detection["box_input"]
        detection["box_model"] = {
            "x": box["x"] * input_to_model["resize_scale_x"] + input_to_model["pad_left"],
            "y": box["y"] * input_to_model["resize_scale_y"] + input_to_model["pad_top"],
            "width": box["width"] * input_to_model["resize_scale_x"],
            "height": box["height"] * input_to_model["resize_scale_y"],
        }
        detection["box_display"] = {
            "x": max(0.0, min(float(source_width), box["x"] * source_scale_x)),
            "y": max(0.0, min(float(source_height), box["y"] * source_scale_y)),
            "width": max(0.0, min(float(source_width), (box["x"] + box["width"]) * source_scale_x) - max(0.0, box["x"] * source_scale_x)),
            "height": max(0.0, min(float(source_height), (box["y"] + box["height"]) * source_scale_y) - max(0.0, box["y"] * source_scale_y)),
        }


def check_installation() -> tuple[list[str], str | None]:
    required = [
        PACKAGE_ROOT / "etc" / "0_preproc.json",
        PACKAGE_ROOT / "etc" / "0_process_mla.json",
        PACKAGE_ROOT / "etc" / "boxdecoder.json",
        PACKAGE_ROOT / "share" / "overlay" / "labels",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        return [], "missing installed Wood package files: " + ", ".join(missing)
    labels = load_labels()
    if labels != ["ChipOff", "Major", "Minor"]:
        return labels, f"unexpected installed label order: {labels}"
    return labels, None


@app.get("/health")
def health():
    labels, error = check_installation()
    return jsonify({
        "status": "ok" if error is None else "error",
        "service": "wood-parity-demo",
        "pipeline_version": "wood-parity-demo-0.1.0",
        "port": 5002,
        "labels": labels,
        "package_root": str(PACKAGE_ROOT),
        "error": error,
        "safety": "recorded-image only; no camera, PLC, SQL, MES, or production service access",
    }), 200 if error is None else 503


@app.get("/contract")
def contract():
    return jsonify({
        "schema_version": "1.1",
        "input": {"upload_field": "image", "formats": sorted(ALLOWED_SUFFIXES), "maximum_bytes": MAX_UPLOAD_BYTES},
        "pipeline_input": {"width": INPUT_WIDTH, "height": INPUT_HEIGHT, "format": "NV12"},
        "model_input": {"width": MODEL_WIDTH, "height": MODEL_HEIGHT, "format": "RGB", "letterbox": True},
        "result": "versioned detection JSON; no pass/fail decision",
    })


@app.post("/infer")
def infer():
    labels, installation_error = check_installation()
    if installation_error:
        return jsonify({"status": "error", "error": installation_error}), 503
    upload = request.files.get("image")
    frame_id = request.form.get("frame_id", "")
    if upload is None or not upload.filename:
        return jsonify({"status": "error", "error": "multipart field 'image' is required"}), 400
    if not FRAME_ID_RE.fullmatch(frame_id):
        return jsonify({"status": "error", "error": "frame_id must be 1-128 characters: letters, digits, dot, underscore, or hyphen"}), 400
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return jsonify({"status": "error", "error": f"unsupported image type {suffix}"}), 415

    run_directory = RUN_ROOT / uuid.uuid4().hex
    run_directory.mkdir(parents=True, exist_ok=False)
    input_path = run_directory / f"input{suffix}"
    try:
        upload.save(input_path)
        image = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("uploaded file is not a readable image")
        source_height, source_width = image.shape[:2]
        start = time.monotonic()
        with RUN_LOCK:
            detections = run_worker(input_path, timeout_seconds=30.0)
        total_ms = (time.monotonic() - start) * 1000
        add_coordinate_forms(detections, source_width, source_height)
        source_to_input = {
            "source_width": source_width,
            "source_height": source_height,
            "input_width": INPUT_WIDTH,
            "input_height": INPUT_HEIGHT,
            "resize_scale_x": INPUT_WIDTH / source_width,
            "resize_scale_y": INPUT_HEIGHT / source_height,
            "pad_left": 0.0,
            "pad_top": 0.0,
            "pad_right": 0.0,
            "pad_bottom": 0.0,
        }
        return jsonify({
            "schema_version": "1.1",
            "frame_id": frame_id,
            "pipeline_version": "wood-parity-demo-0.1.0",
            "model_version": model_version(),
            "image": {"path": upload.filename, "sha256": sha256_file(input_path), "width": source_width, "height": source_height},
            "transform": {"source_to_input": source_to_input, "input_to_model": letterbox_transform()},
            "detections": detections,
            "timings_ms": {"total": total_ms},
        })
    except (OSError, RuntimeError, TimeoutError, ValueError) as error:
        return jsonify({"status": "error", "frame_id": frame_id, "error": str(error)}), 422
    finally:
        shutil.rmtree(run_directory, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Recorded-image SiMa Wood parity service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5002)
    args = parser.parse_args()
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
