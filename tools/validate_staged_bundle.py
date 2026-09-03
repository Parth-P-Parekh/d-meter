from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(directory: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    for path in files:
        digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the immutable Wood parity asset bundle.")
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.bundle / "bundle-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != "1.0":
            raise ValueError("schema_version must be 1.0")
        required = {"post_surgery_onnx", "classes_file", "known_good_mpk", "production_configuration"}
        files = manifest["files"]
        if required - files.keys():
            raise ValueError(f"missing file roles: {sorted(required - files.keys())}")
        for role in sorted(required):
            item = files[role]
            path = args.bundle / item["path"]
            if not path.is_file():
                raise ValueError(f"{role} is missing: {path}")
            actual = sha256_file(path)
            if actual != item["sha256"]:
                raise ValueError(f"checksum mismatch for {role}: expected {item['sha256']}, got {actual}")
        calibration = manifest["calibration_directory"]
        directory = args.bundle / calibration["path"]
        if not directory.is_dir():
            raise ValueError(f"calibration directory is missing: {directory}")
        actual_tree, actual_count = sha256_tree(directory)
        if actual_count != calibration["file_count"] or actual_tree != calibration["sha256_tree"]:
            raise ValueError("calibration directory count or checksum does not match manifest")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"BUNDLE INVALID: {error}", file=sys.stderr)
        return 1
    print(f"BUNDLE VALID: {manifest.get('bundle_version', 'unversioned')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
