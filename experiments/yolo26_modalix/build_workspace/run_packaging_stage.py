"""Run only pipeline/MPK stages from a pinned, isolated converter checkout."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED_COMMIT = "c784d26a96898031c70e9c25e67aab7ac3673c6f"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("pipelinecreate", "mpkcreate"))
    parser.add_argument("--converter", type=Path, default=ROOT / "build_workspace" / "vendor" / "tool-model-to-pipeline")
    parser.add_argument("--executable", default="sima-model-to-pipeline")
    args = parser.parse_args()
    converter = args.converter.resolve()
    forbidden = (Path.home() / "Sima_Projects" / "tool-model-to-pipeline").resolve()
    if converter == forbidden:
        parser.error("the installed converter tree is forbidden; clone the pinned source into build_workspace/vendor")
    if not (converter / ".git").is_dir():
        parser.error(f"isolated converter checkout not found: {converter}")
    commit = subprocess.run(["git", "-C", str(converter), "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
    if commit != PINNED_COMMIT:
        parser.error(f"converter must be pinned to {PINNED_COMMIT}, found {commit}")
    if args.stage == "pipelinecreate":
        compiled = ROOT / "build_workspace" / "result" / "modalix" / "yolo26n_coco.tar.gz"
        if not compiled.is_file():
            parser.error(f"compiled Modalix model not found: {compiled}")
    config = ROOT / "build_workspace" / "yolo26_modalix.yaml"
    command = [args.executable, "model-to-pipeline", "--config-yaml", str(config), "--step", args.stage]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
