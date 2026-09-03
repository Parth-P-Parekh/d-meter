#!/usr/bin/env python3
"""Self-contained recorded-image inference demo; independent of SiMa app packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request

SERVICE_NAME = "diy-inference-demo"
PIPELINE_VERSION = "0.1.0"
MODEL_VERSION = "opencv-hough-screw-v1"
RUN_ROOT = Path("/tmp/diy-inference-demo")
MODEL_WIDTH = 640
MODEL_HEIGHT = 640
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
FRAME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ALLOWED_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".ppm"}
SCREW_MIN_RADIUS = 6
SCREW_MAX_RADIUS = 20
SCREW_MIN_DISTANCE = 22
# Normalized within the unpadded source content. Tune per fixture/recipe.
SCREW_ROI_X = (0.32, 0.66)
SCREW_ROI_Y = (0.50, 0.58)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def letterbox(image: np.ndarray) -> tuple[np.ndarray, dict[str, float | int]]:
    height, width = image.shape[:2]
    scale = min(MODEL_WIDTH / width, MODEL_HEIGHT / height)
    resized_width = round(width * scale)
    resized_height = round(height * scale)
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    pad_left = (MODEL_WIDTH - resized_width) // 2
    pad_top = (MODEL_HEIGHT - resized_height) // 2
    pad_right = MODEL_WIDTH - resized_width - pad_left
    pad_bottom = MODEL_HEIGHT - resized_height - pad_top
    model_image = cv2.copyMakeBorder(resized, pad_top, pad_bottom, pad_left, pad_right, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    return model_image, {
        "source_width": width,
        "source_height": height,
        "model_width": MODEL_WIDTH,
        "model_height": MODEL_HEIGHT,
        "resize_scale_x": scale,
        "resize_scale_y": scale,
        "pad_left": pad_left,
        "pad_top": pad_top,
        "pad_right": pad_right,
        "pad_bottom": pad_bottom,
    }


def map_to_source(box: tuple[int, int, int, int], transform: dict[str, float | int]) -> dict[str, float]:
    x, y, width, height = box
    scale = float(transform["resize_scale_x"])
    source_width = float(transform["source_width"])
    source_height = float(transform["source_height"])
    x1 = max(0.0, min(source_width, (x - float(transform["pad_left"])) / scale))
    y1 = max(0.0, min(source_height, (y - float(transform["pad_top"])) / scale))
    x2 = max(0.0, min(source_width, (x + width - float(transform["pad_left"])) / scale))
    y2 = max(0.0, min(source_height, (y + height - float(transform["pad_top"])) / scale))
    return {"x": x1, "y": y1, "width": max(0.0, x2 - x1), "height": max(0.0, y2 - y1)}


def map_to_model(box: tuple[float, float, float, float], transform: dict[str, float | int]) -> dict[str, float]:
    x, y, width, height = box
    scale = float(transform["resize_scale_x"])
    return {
        "x": x * scale + float(transform["pad_left"]),
        "y": y * scale + float(transform["pad_top"]),
        "width": width * scale,
        "height": height * scale,
    }


def detect_screws(model_rgb: np.ndarray, transform: dict[str, float | int]) -> list[dict[str, Any]]:
    """Mark likely circular screw heads in the upper fixture/PCB working region.

    This is deliberately a transparent classical-vision detector. Its geometric
    ROI prevents the large fixture holes and lower PCB vias from being called
    screws in the supplied DUT image. Replace this function with a CNN later.
    """
    gray = cv2.cvtColor(model_rgb, cv2.COLOR_RGB2GRAY)
    blurred = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(
        blurred, cv2.HOUGH_GRADIENT, dp=1.0, minDist=SCREW_MIN_DISTANCE,
        param1=100, param2=13, minRadius=SCREW_MIN_RADIUS, maxRadius=SCREW_MAX_RADIUS,
    )
    pad_left = float(transform["pad_left"])
    pad_top = float(transform["pad_top"])
    content_width = float(transform["source_width"]) * float(transform["resize_scale_x"])
    content_height = float(transform["source_height"]) * float(transform["resize_scale_y"])
    # Fixture-specific learning ROI: the upper row of screw heads in the DUT fixture.
    x_min, x_max = pad_left + content_width * SCREW_ROI_X[0], pad_left + content_width * SCREW_ROI_X[1]
    y_min, y_max = pad_top + content_height * SCREW_ROI_Y[0], pad_top + content_height * SCREW_ROI_Y[1]
    output: list[dict[str, Any]] = []
    if circles is None:
        return output
    for center_x, center_y, radius in sorted(np.round(circles[0]).astype(int).tolist()):
        if not (x_min <= center_x <= x_max and y_min <= center_y <= y_max):
            continue
        box_model = {
            "x": float(center_x - radius), "y": float(center_y - radius),
            "width": float(radius * 2), "height": float(radius * 2),
        }
        source_box = map_to_source((int(box_model["x"]), int(box_model["y"]), int(box_model["width"]), int(box_model["height"])), transform)
        # Hough does not provide a calibrated probability. This is a transparent quality score.
        circularity_score = round(min(1.0, radius / SCREW_MAX_RADIUS), 6)
        output.append({
            "class_name": "screw",
            "confidence": circularity_score,
            "detector": "hough-circle",
            "box_model": box_model,
            "box_source": source_box,
        })
    return output


def save_overlay(source_bgr: np.ndarray, detections: list[dict[str, Any]], output_path: Path) -> None:
    overlay = source_bgr.copy()
    for detection in detections:
        box = detection["box_source"]
        x1, y1 = int(box["x"]), int(box["y"])
        x2, y2 = int(box["x"] + box["width"]), int(box["y"] + box["height"])
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 220, 0), 2)
        cv2.putText(overlay, f"screw {detection['confidence']:.2f}", (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 0), 1, cv2.LINE_AA)
    if not cv2.imwrite(str(output_path), overlay):
        raise RuntimeError("could not write overlay")


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": SERVICE_NAME,
        "pipeline_version": PIPELINE_VERSION,
        "model_version": MODEL_VERSION,
        "port": 5003,
        "dependencies": {"opencv": cv2.__version__, "numpy": np.__version__},
        "safety": "recorded-image only; no SiMa package, camera, PLC, SQL, MES, or production service access",
    })


@app.get("/contract")
def contract():
    return jsonify({
        "input": {"field": "image", "formats": sorted(ALLOWED_SUFFIXES), "maximum_bytes": MAX_UPLOAD_BYTES},
        "preprocessing": {"colour_order": "BGR to RGB", "model_size": [MODEL_WIDTH, MODEL_HEIGHT], "resize": "bilinear", "padding": "centered black"},
        "detector": {"type": MODEL_VERSION, "minimum_radius": SCREW_MIN_RADIUS, "maximum_radius": SCREW_MAX_RADIUS, "working_roi": {"x": SCREW_ROI_X, "y": SCREW_ROI_Y}},
        "output": "screw candidates only; never pass/fail",
    })


@app.post("/infer")
def infer():
    upload = request.files.get("image")
    frame_id = request.form.get("frame_id", uuid.uuid4().hex)
    if upload is None or not upload.filename:
        return jsonify({"status": "error", "error": "multipart field 'image' is required"}), 400
    if not FRAME_ID_RE.fullmatch(frame_id):
        return jsonify({"status": "error", "error": "frame_id is invalid"}), 400
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return jsonify({"status": "error", "error": f"unsupported image type {suffix}"}), 415

    run_id = uuid.uuid4().hex
    run_directory = RUN_ROOT / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    input_path = run_directory / f"input{suffix}"
    try:
        upload.save(input_path)
        source_bgr = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
        if source_bgr is None:
            raise ValueError("uploaded file is not a readable image")
        start = time.monotonic()
        model_bgr, transform = letterbox(source_bgr)
        model_rgb = cv2.cvtColor(model_bgr, cv2.COLOR_BGR2RGB)
        detections = detect_screws(model_rgb, transform)
        elapsed_ms = (time.monotonic() - start) * 1000
        overlay_path = run_directory / "overlay.png"
        save_overlay(source_bgr, detections, overlay_path)
        result = {
            "schema_version": "1.0",
            "run_id": run_id,
            "frame_id": frame_id,
            "pipeline_version": PIPELINE_VERSION,
            "model_version": MODEL_VERSION,
            "image": {"name": upload.filename, "sha256": sha256_file(input_path), "width": int(source_bgr.shape[1]), "height": int(source_bgr.shape[0])},
            "transform": transform,
            "detections": detections,
            "timings_ms": {"total": round(elapsed_ms, 3)},
            "artifacts": {"result": f"/runs/{run_id}/result", "overlay": f"/runs/{run_id}/overlay"},
        }
        (run_directory / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return jsonify(result)
    except (OSError, RuntimeError, ValueError, cv2.error) as error:
        shutil.rmtree(run_directory, ignore_errors=True)
        return jsonify({"status": "error", "frame_id": frame_id, "error": str(error)}), 422


@app.get("/runs/<run_id>/result")
def run_result(run_id: str):
    path = RUN_ROOT / run_id / "result.json"
    if not path.is_file():
        return jsonify({"status": "error", "error": "unknown run"}), 404
    return Response(path.read_bytes(), mimetype="application/json")


@app.get("/runs/<run_id>/overlay")
def run_overlay(run_id: str):
    path = RUN_ROOT / run_id / "overlay.png"
    if not path.is_file():
        return jsonify({"status": "error", "error": "unknown run"}), 404
    return Response(path.read_bytes(), mimetype="image/png")


def main() -> None:
    parser = argparse.ArgumentParser(description="DIY recorded-image inference demo")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5003)
    args = parser.parse_args()
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
