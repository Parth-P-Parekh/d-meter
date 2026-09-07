from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yolo26_modalix.artifacts import sha256_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "corpus" / "manifest.json")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != "1.0" or not isinstance(manifest.get("frames"), list):
            raise ValueError("invalid corpus manifest")
        roles: dict[str, int] = {}
        source_hashes: dict[str, set[str]] = {"calibration": set(), "evaluation": set()}
        for frame in manifest["frames"]:
            path = ROOT / frame["path"]
            if not path.is_file() or sha256_file(path) != frame["sha256"]:
                raise ValueError(f"missing or changed corpus frame: {path}")
            role = frame["role"]
            roles[role] = roles.get(role, 0) + 1
            group = "calibration" if role == "calibration" else "evaluation"
            if frame["source_sha256"] in source_hashes[group]:
                raise ValueError(f"duplicate source content in {group}")
            source_hashes[group].add(frame["source_sha256"])
        if roles != {"calibration": 100, "pipeline-held-out": 20, "recognizable-coco-held-out": 5}:
            raise ValueError(f"unexpected corpus roles: {roles}")
        if source_hashes["calibration"] & source_hashes["evaluation"]:
            raise ValueError("calibration/evaluation overlap")
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"CORPUS INVALID: {error}", file=sys.stderr)
        return 1
    print("CORPUS VALID: 100 calibration + 25 held-out frames, no overlap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

