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
    parser.add_argument("--mpk", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "artifacts" / "artifact-manifest.json")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not args.mpk.is_file() or not all(manifest["artifacts"][name].get("sha256") for name in ("pt", "onnx")):
        parser.error("PT/ONNX manifest must be finalized and MPK must exist")
    try:
        relative_mpk = args.mpk.resolve().relative_to((ROOT / "artifacts").resolve())
    except ValueError:
        parser.error("MPK must be staged below this experiment's artifacts directory")
    manifest["artifacts"]["mpk"].update({"path": relative_mpk.as_posix(),
                                         "bytes": args.mpk.stat().st_size, "sha256": sha256_file(args.mpk)})
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
