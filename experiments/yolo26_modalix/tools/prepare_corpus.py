"""Hash, split, and normalize user-supplied calibration/evaluation images."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yolo26_modalix.artifacts import sha256_file

SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".ppm", ".webp"}


def images(directory: Path) -> list[Path]:
    paths = [path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in SUFFIXES]
    output = []
    for path in paths:
        if cv2.imread(str(path), cv2.IMREAD_COLOR) is None:
            raise ValueError(f"corrupt image: {path}")
        output.append(path)
    return output


def select(paths: list[Path], count: int, label: str) -> list[tuple[str, Path]]:
    unique: dict[str, Path] = {}
    for path in paths:
        unique.setdefault(sha256_file(path), path)
    if len(unique) < count:
        raise ValueError(f"{label} requires {count} unique readable images; found {len(unique)}")
    return sorted(unique.items())[:count]


def stage(entries: list[tuple[str, Path]], destination: Path, role: str) -> list[dict[str, object]]:
    destination.mkdir(parents=True, exist_ok=True)
    output = []
    for digest, source in entries:
        target = destination / f"{digest}.png"
        image = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if not cv2.imwrite(str(target), image):
            raise RuntimeError(f"could not stage {source}")
        # Manifest hashes the staged pixels/file, while source_sha256 proves disjointness.
        output.append({"role": role, "path": target.relative_to(ROOT).as_posix(), "sha256": sha256_file(target),
                       "source_sha256": digest, "width": image.shape[1], "height": image.shape[0]})
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration-dir", type=Path, required=True)
    parser.add_argument("--pipeline-evaluation-dir", type=Path, required=True)
    parser.add_argument("--coco-evaluation-dir", type=Path, required=True,
                        help="five or more recorded images asserted by the user to contain recognizable COCO objects")
    args = parser.parse_args()
    calibration = select(images(args.calibration_dir), 100, "calibration")
    pipeline_eval = select(images(args.pipeline_evaluation_dir), 20, "pipeline evaluation")
    coco_eval = select(images(args.coco_evaluation_dir), 5, "COCO-object evaluation")
    calibration_hashes = {digest for digest, _ in calibration}
    evaluation = pipeline_eval + coco_eval
    duplicate_eval = len({digest for digest, _ in evaluation}) != len(evaluation)
    overlap = calibration_hashes & {digest for digest, _ in evaluation}
    if overlap or duplicate_eval:
        raise ValueError("calibration and evaluation must contain distinct image content with no overlap")
    calibration_root = ROOT / "corpus" / "calibration" / "images"
    evaluation_root = ROOT / "corpus" / "evaluation" / "images"
    shutil.rmtree(calibration_root, ignore_errors=True)
    shutil.rmtree(evaluation_root, ignore_errors=True)
    frames = stage(calibration, calibration_root, "calibration")
    frames += stage(pipeline_eval, evaluation_root, "pipeline-held-out")
    frames += stage(coco_eval, evaluation_root, "recognizable-coco-held-out")
    manifest = {"schema_version": "1.0", "created_utc": datetime.now(timezone.utc).isoformat(),
                "selection": "lexicographically smallest source SHA-256", "calibration_count": 100,
                "evaluation_count": 25, "overlap_count": 0, "frames": frames}
    path = ROOT / "corpus" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

