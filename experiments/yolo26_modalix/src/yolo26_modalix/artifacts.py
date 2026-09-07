from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "1.0" or not isinstance(value.get("artifacts"), dict):
        raise ValueError("artifact manifest must use schema_version 1.0 and contain artifacts")
    return value


def artifact_checksums(manifest: dict[str, Any]) -> dict[str, str | None]:
    output: dict[str, str | None] = {}
    for name in ("pt", "onnx", "mpk"):
        item = manifest.get("artifacts", {}).get(name, {})
        value = item.get("sha256") if isinstance(item, dict) else None
        output[name] = value if isinstance(value, str) and len(value) == 64 else None
    return output

