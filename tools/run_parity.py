from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sima_parity.comparison import ComparisonPolicy, compare_detections
from sima_parity.overlay import render_overlay
from sima_parity.results import load_json, validate_result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare demo SiMa detections with immutable production baselines.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--demo-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-iou", type=float, default=0.90)
    parser.add_argument("--maximum-score-delta", type=float, default=0.05)
    args = parser.parse_args()
    try:
        manifest = load_json(args.manifest)
        if manifest.get("schema_version") != "1.1" or not isinstance(manifest.get("frames"), list):
            raise ValueError("manifest must have schema_version 1.1 and a frames list")
        if not 0 <= args.minimum_iou <= 1 or args.maximum_score_delta < 0:
            raise ValueError("comparison thresholds are invalid")
    except ValueError as error:
        print(f"MANIFEST INVALID: {error}", file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    policy = ComparisonPolicy(args.minimum_iou, args.maximum_score_delta)
    report = {"schema_version": "1.1", "corpus_version": manifest.get("corpus_version"), "policy": policy.__dict__, "frames": []}
    all_passed = True
    for frame in manifest["frames"]:
        frame_id = frame.get("frame_id")
        frame_report: dict[str, object] = {"frame_id": frame_id, "passed": False}
        try:
            image_path = args.manifest.parent / frame["image_path"]
            baseline_path = args.manifest.parent / frame["baseline_path"]
            demo_path = args.demo_results / f"{frame_id}.json"
            if not image_path.is_file():
                raise ValueError(f"image not found: {image_path}")
            if sha256_file(image_path) != frame["image_sha256"]:
                raise ValueError("image checksum does not match corpus manifest")
            baseline = load_json(baseline_path)
            demo = load_json(demo_path)
            validate_result(baseline, frame_id)
            validate_result(demo, frame_id)
            comparison = compare_detections(baseline["detections"], demo["detections"], policy)
            overlay_path = args.output / "overlays" / f"{frame_id}.png"
            render_overlay(image_path, overlay_path, baseline["detections"], demo["detections"])
            frame_report.update(comparison)
            frame_report["overlay_path"] = str(overlay_path.relative_to(args.output))
        except (OSError, KeyError, ValueError, RuntimeError) as error:
            frame_report["error"] = str(error)
        if not frame_report["passed"]:
            all_passed = False
        report["frames"].append(frame_report)
    report["passed"] = all_passed
    (args.output / "parity-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"PARITY {'PASSED' if all_passed else 'FAILED'}: {args.output / 'parity-report.json'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
