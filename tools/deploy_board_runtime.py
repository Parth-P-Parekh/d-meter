"""Copy the two board-runtime files to an isolated directory; never start a service."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD_FILES = [
    ROOT / "board_runtime" / "wood_parity_service.py",
    ROOT / "board_runtime" / "wood_parity_worker.py",
    ROOT / "board_runtime" / "start_wood_parity_service.sh",
]


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage only the Wood parity board runtime over SCP.")
    parser.add_argument("--host", default="sima@192.168.1.20")
    parser.add_argument("--remote-dir", default="/home/sima/wood-parity-demo")
    args = parser.parse_args()
    if not args.remote_dir.startswith("/home/sima/") or ".." in Path(args.remote_dir).parts:
        parser.error("remote-dir must be an absolute isolated directory below /home/sima/")
    missing = [str(path) for path in BOARD_FILES if not path.is_file()]
    if missing:
        parser.error("missing board-runtime file(s): " + ", ".join(missing))
    try:
        run(["ssh", "-o", "BatchMode=yes", args.host, "mkdir", "-p", args.remote_dir])
        for path in BOARD_FILES:
            run(["scp", "-p", str(path), f"{args.host}:{args.remote_dir}/{path.name}"])
        run(["ssh", "-o", "BatchMode=yes", args.host, "chmod", "0755", f"{args.remote_dir}/start_wood_parity_service.sh"])
        run(["ssh", "-o", "BatchMode=yes", args.host, "sha256sum", *[f"{args.remote_dir}/{path.name}" for path in BOARD_FILES]])
    except subprocess.CalledProcessError as error:
        print(f"BOARD STAGING FAILED: {error}", file=sys.stderr)
        return error.returncode or 1
    print("BOARD STAGING COMPLETE: no service was started and no production package was modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
