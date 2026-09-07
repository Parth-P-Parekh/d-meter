"""Stage only the isolated service files; never start/stop/install a board app."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/home/sima/yolo26-inference-demo"


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="sima@192.168.1.20")
    parser.add_argument("--remote-dir", default=REMOTE)
    args = parser.parse_args()
    if args.remote_dir != REMOTE:
        parser.error(f"remote-dir is fixed to {REMOTE}")
    manifest = ROOT / "artifacts" / "artifact-manifest.json"
    if not manifest.is_file():
        parser.error("artifact manifest is missing")
    with tempfile.TemporaryDirectory(prefix="yolo26-board-stage-") as directory:
        stage = Path(directory)
        for file in ("board_service.py", "board_worker.py", "start_yolo26_service.sh", "verify_guardrails.sh"):
            shutil.copy2(ROOT / "board" / file, stage / file)
        shutil.copy2(manifest, stage / "artifact-manifest.json")
        shutil.copytree(ROOT / "src", stage / "src")
        try:
            # Read-only guards precede staging. A listener on 5004 may only be this experiment.
            run(["ssh", "-o", "BatchMode=yes", args.host,
                 "test ! -e /home/sima/yolo26-inference-demo || test -d /home/sima/yolo26-inference-demo; "
                 "! ss -ltn '( sport = :5004 )' | tail -n +2 | grep -q . || "
                 "ps -eo args | grep '/home/sima/yolo26-inference-demo/board_service.py' | grep -v grep >/dev/null"])
            run(["ssh", "-o", "BatchMode=yes", args.host, "mkdir", "-p", args.remote_dir])
            run(["scp", "-pr", *[str(path) for path in stage.iterdir()], f"{args.host}:{args.remote_dir}/"])
            run(["ssh", "-o", "BatchMode=yes", args.host, "chmod", "0755",
                 f"{args.remote_dir}/start_yolo26_service.sh", f"{args.remote_dir}/verify_guardrails.sh"])
            run(["ssh", "-o", "BatchMode=yes", args.host, "find", args.remote_dir, "-maxdepth", "4", "-type", "f", "-exec", "sha256sum", "{}", "\\;"])
        except subprocess.CalledProcessError as error:
            print(f"BOARD STAGING FAILED: {error}", file=sys.stderr)
            return error.returncode or 1
    print("BOARD RUNTIME STAGED; no process or MPK was started, stopped, or installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
