from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yolo26_modalix.comparison import ParityPolicy, compare_detections
from yolo26_modalix.runner import OnnxRunner
from yolo26_modalix.torch_runner import TorchRunner


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "corpus" / "manifest.json")
    parser.add_argument("--pt", type=Path, default=ROOT / "artifacts" / "model" / "yolo26n.pt")
    parser.add_argument("--onnx", type=Path, default=ROOT / "artifacts" / "model" / "yolo26n.onnx")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "pt-onnx")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    frames = [item for item in manifest["frames"] if item["role"] != "calibration"]
    if len(frames) < 25:
        parser.error("at least 20 pipeline and five recognizable-COCO held-out frames are required")
    pt_runner, onnx_runner = TorchRunner(args.pt), OnnxRunner(args.onnx)
    policy = ParityPolicy(0.99, 0.001)
    report = {"schema_version": "1.0", "policy": policy.__dict__, "frames": [], "passed": True}
    for frame in frames:
        path = ROOT / frame["path"]
        source = cv2.imread(str(path), cv2.IMREAD_COLOR)
        pt_detections, pt_times, transform = pt_runner.infer_bgr(source)
        onnx_detections, onnx_times, _ = onnx_runner.infer_bgr(source)
        comparison = compare_detections(pt_detections, onnx_detections, policy)
        frame_id = frame["source_sha256"][:16]
        args.output.mkdir(parents=True, exist_ok=True)
        for name, detections, timings in (("pt", pt_detections, pt_times), ("onnx", onnx_detections, onnx_times)):
            directory = args.output / name
            directory.mkdir(exist_ok=True)
            (directory / f"{frame_id}.json").write_text(json.dumps({"frame_id": frame_id, "detections": detections,
                                                                     "timings_ms": timings, "transform": transform}, indent=2) + "\n")
        item = {"frame_id": frame_id, "role": frame["role"], **comparison}
        report["frames"].append(item)
        report["passed"] = report["passed"] and item["passed"]
    (args.output / "parity-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"PT/ONNX PARITY {'PASSED' if report['passed'] else 'FAILED'}: {args.output / 'parity-report.json'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
