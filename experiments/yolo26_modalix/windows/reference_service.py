from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from yolo26_modalix.runner import OnnxRunner
from yolo26_modalix.service import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated YOLO26 FP32 ONNX upload service")
    parser.add_argument("--model", type=Path, default=ROOT / "artifacts" / "model" / "yolo26n.onnx")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5004)
    args = parser.parse_args()
    app = create_app(OnnxRunner(args.model), ROOT / "results" / "windows-runs",
                     ROOT / "artifacts" / "artifact-manifest.json", service_name="yolo26-onnx-reference", require_mpk=False)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

