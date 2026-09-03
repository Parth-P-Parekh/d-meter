"""Stage only the self-contained DIY runtime to an isolated board directory."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "diy_runtime" / "diy_inference_service.py", ROOT / "diy_runtime" / "start_diy_inference_service.sh"]


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="sima@192.168.1.20")
    parser.add_argument("--remote-dir", default="/home/sima/diy-inference-demo")
    args = parser.parse_args()
    if not args.remote_dir.startswith("/home/sima/") or ".." in Path(args.remote_dir).parts:
        parser.error("remote-dir must be an absolute isolated directory below /home/sima/")
    try:
        run(["ssh", "-o", "BatchMode=yes", args.host, "mkdir", "-p", args.remote_dir])
        for path in FILES:
            run(["scp", "-p", str(path), f"{args.host}:{args.remote_dir}/{path.name}"])
        run(["ssh", "-o", "BatchMode=yes", args.host, "chmod", "0755", f"{args.remote_dir}/start_diy_inference_service.sh"])
        run(["ssh", "-o", "BatchMode=yes", args.host, "sha256sum", *[f"{args.remote_dir}/{path.name}" for path in FILES]])
    except subprocess.CalledProcessError as error:
        print(f"DIY BOARD STAGING FAILED: {error}", file=sys.stderr)
        return error.returncode or 1
    print("DIY BOARD STAGING COMPLETE: no existing SiMa package or production service was modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
