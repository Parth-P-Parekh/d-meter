#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
import os
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

from yolo26_modalix.constants import PIPELINE_HEIGHT, PIPELINE_WIDTH
from yolo26_modalix.geometry import LetterboxTransform
from yolo26_modalix.postprocess import decode_yolo26
from yolo26_modalix.service import create_app

RUN_ROOT = Path("/tmp/yolo26-inference-demo")
MANIFEST = HERE / "artifact-manifest.json"
LOCK = threading.Lock()


class ModalixRunner:
    def health_error(self):
        package = Path(os.environ.get("YOLO26_PACKAGE_ROOT", "/data/simaai/applications/yolo26n_coco_simaaisrc"))
        missing = [str(package / "etc" / name) for name in ("0_preproc.json", "0_process_mla.json", "0_postproc.json")
                   if not (package / "etc" / name).is_file()]
        return "missing isolated package configuration: " + ", ".join(missing) if missing else None

    def infer_bgr(self, source_bgr):
        total_start = time.perf_counter()
        source_height, source_width = source_bgr.shape[:2]
        pipeline_bgr = cv2.resize(source_bgr, (PIPELINE_WIDTH, PIPELINE_HEIGHT), interpolation=cv2.INTER_LINEAR)
        with tempfile.TemporaryDirectory(prefix="worker-", dir=RUN_ROOT) as directory:
            directory_path = Path(directory)
            input_path = directory_path / "pipeline-input.png"
            raw_path = directory_path / "raw-output.f32"
            if not cv2.imwrite(str(input_path), pipeline_bgr):
                raise RuntimeError("could not stage the worker input")
            preprocessing_ms = (time.perf_counter() - total_start) * 1000
            command = [sys.executable, str(HERE / "board_worker.py"), "--image", str(input_path), "--raw-output", str(raw_path)]
            inference_start = time.perf_counter()
            with LOCK:
                completed = subprocess.run(command, text=True, capture_output=True, timeout=40, check=False)
            inference_ms = (time.perf_counter() - inference_start) * 1000
            try:
                worker_result = json.loads(completed.stdout)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"board worker returned invalid JSON: {completed.stderr[-1000:]}") from error
            if completed.returncode != 0:
                raise RuntimeError(worker_result.get("error", "board worker failed"))
            raw = np.fromfile(raw_path, dtype="<f4")
        if raw.size != 1 * 84 * 8400:
            raise RuntimeError(f"board worker wrote {raw.size} values instead of 705600")
        post_start = time.perf_counter()
        transform = LetterboxTransform.create(PIPELINE_WIDTH, PIPELINE_HEIGHT)
        detections = decode_yolo26(raw.reshape(1, 84, 8400), transform)
        scale_x, scale_y = source_width / PIPELINE_WIDTH, source_height / PIPELINE_HEIGHT
        for detection in detections:
            box = detection["box_source"]
            detection["box_source"] = {"x": box["x"] * scale_x, "y": box["y"] * scale_y,
                                       "width": box["width"] * scale_x, "height": box["height"] * scale_y}
        postprocessing_ms = (time.perf_counter() - post_start) * 1000
        timings = {"preprocessing": round(preprocessing_ms, 3), "inference": round(inference_ms, 3),
                   "postprocessing": round(postprocessing_ms, 3),
                   "accelerator_pipeline": round(float(worker_result["inference_ms"]), 3),
                   "total": round((time.perf_counter() - total_start) * 1000, 3)}
        transforms = {
            "source_to_pipeline": {"source_width": source_width, "source_height": source_height,
                                   "pipeline_width": PIPELINE_WIDTH, "pipeline_height": PIPELINE_HEIGHT,
                                   "resize_scale_x": PIPELINE_WIDTH / source_width,
                                   "resize_scale_y": PIPELINE_HEIGHT / source_height, "padding": "none"},
            "pipeline_to_model": transform.as_dict(),
        }
        return detections, timings, transforms


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated YOLO26 Modalix uploaded-image service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5004)
    args = parser.parse_args()
    if args.port in (5001, 5003):
        parser.error("ports 5001 and 5003 are protected")
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    app = create_app(ModalixRunner(), RUN_ROOT, MANIFEST, service_name="yolo26-modalix-demo", require_mpk=True)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
