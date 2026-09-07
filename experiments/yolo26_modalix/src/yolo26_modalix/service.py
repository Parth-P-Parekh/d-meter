"""Flask contract shared by the ONNX reference and Modalix raw-output runner."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Protocol

import cv2
from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from .artifacts import artifact_checksums, load_manifest, sha256_file
from .constants import (
    COCO_NAMES, CONFIDENCE_THRESHOLD, MAX_DETECTIONS, MODEL_HEIGHT, MODEL_VERSION, MODEL_WIDTH,
    NMS_IOU_THRESHOLD, PIPELINE_HEIGHT, PIPELINE_NAME, PIPELINE_VERSION, PIPELINE_WIDTH, PORT,
)
from .overlay import save_overlay

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
FRAME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
ALLOWED_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".ppm", ".webp"}


class Runner(Protocol):
    def infer_bgr(self, source_bgr: Any) -> tuple[list[dict[str, object]], dict[str, float], dict[str, object]]: ...


def _metadata(manifest_path: Path) -> tuple[dict[str, str | None], str | None]:
    try:
        checksums = artifact_checksums(load_manifest(manifest_path))
        missing = [name for name, value in checksums.items() if value is None]
        return checksums, f"missing finalized checksum(s): {', '.join(missing)}" if missing else None
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {"pt": None, "onnx": None, "mpk": None}, str(error)


def create_app(runner: Runner, run_root: Path, manifest_path: Path, *, service_name: str, require_mpk: bool) -> Flask:
    app = Flask(service_name)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    run_root.mkdir(parents=True, exist_ok=True)

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify({"status": "error", "error": f"upload exceeds {MAX_UPLOAD_BYTES} bytes"}), 413

    @app.get("/health")
    def health():
        checksums, manifest_error = _metadata(manifest_path)
        runner_check = getattr(runner, "health_error", None)
        runner_error = runner_check() if callable(runner_check) else None
        error = (manifest_error or runner_error) if require_mpk else runner_error
        return jsonify({
            "status": "ok" if error is None else "error", "service": service_name,
            "pipeline_name": PIPELINE_NAME, "pipeline_version": PIPELINE_VERSION,
            "model_version": MODEL_VERSION, "port": PORT, "checksums": checksums,
            "labels": list(COCO_NAMES), "error": error,
            "safety": "uploaded-image only; no camera, PLC, MES, SQL, port-5001, or port-5003 access",
        }), 200 if error is None else 503

    @app.get("/contract")
    def contract():
        checksums, manifest_error = _metadata(manifest_path)
        return jsonify({
            "schema_version": "1.0", "pipeline_name": PIPELINE_NAME, "pipeline_version": PIPELINE_VERSION,
            "model_version": MODEL_VERSION, "checksums": checksums, "manifest_error": manifest_error,
            "input": {"upload_field": "image", "optional_field": "frame_id", "formats": sorted(ALLOWED_SUFFIXES),
                      "maximum_bytes": MAX_UPLOAD_BYTES, "uploaded_decode": "OpenCV BGR",
                      "forced_pipeline_size": [PIPELINE_WIDTH, PIPELINE_HEIGHT]},
            "model_input": {"shape": [1, 3, MODEL_HEIGHT, MODEL_WIDTH], "colour_order": "RGB",
                            "normalization": "/255 float32", "letterbox": "centered black"},
            "postprocessing": {"head": "one-to-many raw (1,84,8400)", "confidence": CONFIDENCE_THRESHOLD,
                               "nms_iou": NMS_IOU_THRESHOLD, "max_detections": MAX_DETECTIONS,
                               "class_aware_nms": True},
            "result": "COCO integration detections only; never ChipOff/Major/Minor or pass/fail",
        })

    @app.post("/infer")
    def infer():
        upload = request.files.get("image")
        frame_id = request.form.get("frame_id") or uuid.uuid4().hex
        if upload is None or not upload.filename:
            return jsonify({"status": "error", "error": "multipart field 'image' is required"}), 400
        if not FRAME_ID_RE.fullmatch(frame_id):
            return jsonify({"status": "error", "error": "frame_id must be 1-128 safe characters"}), 400
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            return jsonify({"status": "error", "error": f"unsupported image type {suffix or '<none>'}"}), 415
        run_id = uuid.uuid4().hex
        run_directory = run_root / run_id
        run_directory.mkdir(parents=False, exist_ok=False)
        input_path = run_directory / f"input{suffix}"
        try:
            upload.save(input_path)
            source_bgr = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
            if source_bgr is None:
                raise ValueError("uploaded file is not a readable image")
            detections, timings, transform = runner.infer_bgr(source_bgr)
            overlay_path = run_directory / "overlay.png"
            save_overlay(source_bgr, detections, overlay_path)
            checksums, _ = _metadata(manifest_path)
            result = {
                "schema_version": "1.0", "run_id": run_id, "frame_id": frame_id,
                "pipeline_name": PIPELINE_NAME, "pipeline_version": PIPELINE_VERSION,
                "model_version": MODEL_VERSION, "checksums": checksums,
                "image": {"name": upload.filename, "sha256": sha256_file(input_path),
                          "width": int(source_bgr.shape[1]), "height": int(source_bgr.shape[0])},
                "transform": transform, "detections": detections, "timings_ms": timings,
                "artifacts": {"result": f"/runs/{run_id}/result", "overlay": f"/runs/{run_id}/overlay"},
            }
            (run_directory / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return jsonify(result)
        except (OSError, RuntimeError, TimeoutError, ValueError, cv2.error) as error:
            shutil.rmtree(run_directory, ignore_errors=True)
            return jsonify({"status": "error", "frame_id": frame_id, "error": str(error)}), 422

    @app.get("/runs/<run_id>/result")
    def result(run_id: str):
        if not RUN_ID_RE.fullmatch(run_id):
            return jsonify({"status": "error", "error": "unknown run"}), 404
        path = run_root / run_id / "result.json"
        return Response(path.read_bytes(), mimetype="application/json") if path.is_file() else (jsonify({"status": "error", "error": "unknown run"}), 404)

    @app.get("/runs/<run_id>/overlay")
    def overlay(run_id: str):
        if not RUN_ID_RE.fullmatch(run_id):
            return jsonify({"status": "error", "error": "unknown run"}), 404
        path = run_root / run_id / "overlay.png"
        return Response(path.read_bytes(), mimetype="image/png") if path.is_file() else (jsonify({"status": "error", "error": "unknown run"}), 404)

    return app
