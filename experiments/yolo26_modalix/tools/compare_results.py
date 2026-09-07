from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yolo26_modalix.comparison import ParityPolicy, compare_detections


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True, help="directory of frame-id.json FP32 ONNX results")
    parser.add_argument("--candidate", type=Path, required=True, help="directory of matching PT or Modalix results")
    parser.add_argument("--mode", choices=("pt", "int8"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = ParityPolicy(0.99, 0.001) if args.mode == "pt" else ParityPolicy(0.90, 0.05)
    report = {"schema_version": "1.0", "mode": args.mode, "policy": policy.__dict__, "frames": [], "passed": True}
    for reference_path in sorted(args.reference.glob("*.json")):
        candidate_path = args.candidate / reference_path.name
        frame = {"frame_id": reference_path.stem, "passed": False}
        try:
            reference = json.loads(reference_path.read_text(encoding="utf-8"))
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            comparison = compare_detections(reference["detections"], candidate["detections"], policy)
            frame.update(comparison)
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
            frame["error"] = str(error)
        report["frames"].append(frame)
        report["passed"] = report["passed"] and frame["passed"]
    if not report["frames"]:
        report["passed"] = False
        report["error"] = "no reference JSON files"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"PARITY {'PASSED' if report['passed'] else 'FAILED'}: {args.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

