"""Require checker-valid and numerically equivalent graph surgery before compile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import onnx
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yolo26_modalix.preprocess import to_nchw_rgb_float


def session(path: Path):
    graph = onnx.load(str(path))
    onnx.checker.check_model(graph)
    value = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    if len(value.get_inputs()) != 1 or len(value.get_outputs()) != 1:
        raise ValueError(f"{path} is not a single-input/single-output graph")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--revised", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--atol", type=float, default=1e-5)
    parser.add_argument("--rtol", type=float, default=1e-5)
    args = parser.parse_args()
    first, second = session(args.original), session(args.revised)
    image_paths = sorted(args.images.glob("*.png"))[:5]
    if len(image_paths) < 5:
        parser.error("five held-out PNG images are required")
    frames, passed = [], True
    for path in image_paths:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        tensor, _ = to_nchw_rgb_float(cv2.resize(image, (1280, 720), interpolation=cv2.INTER_LINEAR))
        left = first.run(None, {first.get_inputs()[0].name: tensor})[0]
        right = second.run(None, {second.get_inputs()[0].name: tensor})[0]
        shape_ok = left.shape == right.shape == (1, 84, 8400)
        close = shape_ok and bool(np.allclose(left, right, rtol=args.rtol, atol=args.atol))
        maximum = float(np.max(np.abs(left - right))) if shape_ok else None
        frames.append({"image": path.name, "passed": close, "max_abs_difference": maximum})
        passed = passed and close
    report = {"schema_version": "1.0", "passed": passed, "atol": args.atol, "rtol": args.rtol,
              "original": str(args.original), "revised": str(args.revised), "frames": frames}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"GRAPH EQUIVALENCE {'PASSED' if passed else 'FAILED'}: {args.report}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

