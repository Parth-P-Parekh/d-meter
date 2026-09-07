"""Download the official PT on Windows, export raw static ONNX, and update provenance."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from yolo26_modalix.artifacts import sha256_file
from yolo26_modalix.constants import MODEL_VERSION, RAW_OUTPUT_SHAPE

WEIGHT_URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt"


def package_versions() -> dict[str, str]:
    names = ("ultralytics", "torch", "torchvision", "onnx", "onnxruntime", "opencv-python", "numpy")
    return {name: importlib.metadata.version(name) for name in names}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "model")
    args = parser.parse_args()
    from ultralytics import YOLO
    import onnx

    if importlib.metadata.version("ultralytics") != "8.4.142":
        parser.error("this export requires ultralytics==8.4.142")
    args.output.mkdir(parents=True, exist_ok=True)
    pt = args.output / "yolo26n.pt"
    if not pt.is_file():
        # YOLO resolves the official asset URL. Loading on this PC is the only
        # permitted automatic weight download in this experiment.
        loaded = YOLO(str(pt))
        del loaded
    model = YOLO(str(pt))
    exported = Path(model.export(format="onnx", imgsz=640, opset=13, dynamic=False,
                                 simplify=False, nms=None, batch=1, device="cpu"))
    onnx_path = args.output / "yolo26n.onnx"
    if exported.resolve() != onnx_path.resolve():
        shutil.move(str(exported), onnx_path)
    graph = onnx.load(str(onnx_path))
    onnx.checker.check_model(graph)
    input_shape = [dimension.dim_value for dimension in graph.graph.input[0].type.tensor_type.shape.dim]
    output_shape = [dimension.dim_value for dimension in graph.graph.output[0].type.tensor_type.shape.dim]
    if input_shape != [1, 3, 640, 640] or output_shape != list(RAW_OUTPUT_SHAPE) or len(graph.graph.output) != 1:
        raise RuntimeError(f"unexpected ONNX contract: input={input_shape}, output={output_shape}")
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze", "--all"], check=True, text=True, capture_output=True).stdout
    (args.output / "pip-freeze.txt").write_text(freeze, encoding="utf-8")
    manifest = {
        "schema_version": "1.0", "model_version": MODEL_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(), "packages": package_versions(),
        "sources": {
            "ultralytics_release": "https://github.com/ultralytics/ultralytics/releases/tag/v8.4.142",
            "onnx_export_documentation": "https://docs.ultralytics.com/integrations/onnx",
        },
        "export": {"imgsz": 640, "opset": 13, "dynamic": False, "simplify": False, "nms": None,
                   "batch": 1, "output_shape": list(RAW_OUTPUT_SHAPE)},
        "artifacts": {
            "pt": {"path": "model/yolo26n.pt", "source_url": WEIGHT_URL, "bytes": pt.stat().st_size,
                   "sha256": sha256_file(pt)},
            "onnx": {"path": "model/yolo26n.onnx", "source": "static export of pt above",
                     "bytes": onnx_path.stat().st_size, "sha256": sha256_file(onnx_path)},
            "mpk": {"path": "package/yolo26n_coco_simaaisrc/project.mpk", "bytes": None, "sha256": None},
        },
    }
    manifest_path = ROOT / "artifacts" / "artifact-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
