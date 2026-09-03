"""Guarded gateway for the vendor SiMa build command; it never deploys anything."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from validate_staged_bundle import main as validate_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate staged inputs then run an explicit local SiMa build command.")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="local output directory for generated MPK artifacts")
    parser.add_argument("--build-command", required=True, help="vendor command template; supports {bundle}, {output}, {contract}")
    args = parser.parse_args()
    original = sys.argv
    try:
        sys.argv = ["validate_staged_bundle.py", "--bundle", str(args.bundle)]
        if validate_bundle() != 0:
            return 1
    finally:
        sys.argv = original
    args.output.mkdir(parents=True, exist_ok=True)
    contract = Path(__file__).resolve().parents[1] / "native_pipeline_contract.json"
    command = args.build_command.format(bundle=str(args.bundle.resolve()), output=str(args.output.resolve()), contract=str(contract))
    print("Running local vendor build command. This tool does not copy or deploy artifacts to a board.")
    return subprocess.run(command, shell=True, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
