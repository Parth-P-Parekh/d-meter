"""Stage the camera-only capture utility on SiMa without starting it."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / "training_capture" / "capture_training_images.py",
    ROOT / "training_capture" / "capture-config.json",
]


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="sima@192.168.1.20")
    parser.add_argument("--remote-dir", default="/home/sima/training-capture")
    args = parser.parse_args()
    remote = PurePosixPath(args.remote_dir)
    if not remote.is_absolute() or remote.parts[:3] != ("/", "home", "sima") or ".." in remote.parts:
        parser.error("remote-dir must be an absolute isolated directory below /home/sima")
    missing = [str(path) for path in FILES if not path.is_file()]
    if missing:
        parser.error("missing capture file(s): " + ", ".join(missing))
    try:
        run(["ssh", "-o", "BatchMode=yes", args.host, "mkdir", "-p", args.remote_dir])
        for path in FILES:
            run(["scp", "-p", str(path), f"{args.host}:{args.remote_dir}/{path.name}"])
        run(["ssh", "-o", "BatchMode=yes", args.host, "chmod", "0755", f"{args.remote_dir}/capture_training_images.py"])
        run(["ssh", "-o", "BatchMode=yes", args.host, "mkdir", "-p", f"{args.remote_dir}/outbox", f"{args.remote_dir}/logs"])
        run(["ssh", "-o", "BatchMode=yes", args.host, "sha256sum", *[f"{args.remote_dir}/{path.name}" for path in FILES]])
    except subprocess.CalledProcessError as error:
        print(f"CAPTURE STAGING FAILED: {error}", file=sys.stderr)
        return error.returncode or 1
    print("CAPTURE STAGING COMPLETE: nothing was started and production was not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
