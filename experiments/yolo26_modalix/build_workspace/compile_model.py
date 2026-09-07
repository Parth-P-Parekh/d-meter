"""Direct ModelSDK import/quantize/compile; does not import the converter project."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import onnx


def model_tensor(path: Path) -> np.ndarray:
    source = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if source is None:
        raise ValueError(f"unreadable calibration image: {path}")
    pipeline = cv2.resize(source, (1280, 720), interpolation=cv2.INTER_LINEAR)
    resized = cv2.resize(pipeline, (640, 360), interpolation=cv2.INTER_LINEAR)
    model = cv2.copyMakeBorder(resized, 140, 140, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    rgb = cv2.cvtColor(model, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32) / np.float32(255.0)


def graph_contract(path: Path) -> dict[str, object]:
    graph = onnx.load(str(path))
    onnx.checker.check_model(graph)
    inputs = [[dimension.dim_value for dimension in item.type.tensor_type.shape.dim] for item in graph.graph.input]
    outputs = [[dimension.dim_value for dimension in item.type.tensor_type.shape.dim] for item in graph.graph.output]
    if inputs != [[1, 3, 640, 640]] or outputs != [[1, 84, 8400]]:
        raise ValueError(f"expected static input/output [[1,3,640,640]]/[[1,84,8400]], got {inputs}/{outputs}")
    return {"inputs": inputs, "outputs": outputs, "operators": dict(Counter(node.op_type for node in graph.graph.node))}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile raw-output YOLO26 for Modalix/gen2")
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    forbidden = Path.home() / "Sima_Projects" / "tool-model-to-pipeline"
    if forbidden.exists() and (Path.cwd().resolve() == forbidden.resolve() or forbidden.resolve() in Path.cwd().resolve().parents):
        parser.error("refusing to run from the installed tool-model-to-pipeline tree")
    contract = graph_contract(args.onnx)
    calibration_paths = sorted(args.calibration.glob("*.png"))
    if len(calibration_paths) != 100:
        parser.error(f"exactly 100 hashed PNG calibration frames are required; found {len(calibration_paths)}")
    args.output.mkdir(parents=True, exist_ok=True)
    failure_path = args.output / "unsupported-operator-report.json"
    try:
        from afe.apis.defines import CalibrationMethod, QuantizationParams, gen2_target, quantization_scheme
        from afe.apis.loaded_net import load_model
        from afe.ir.defines import BiasCorrectionType
        from afe.ir.tensor_type import ScalarType
        from afe.load.importers.general_importer import onnx_source

        input_name = onnx.load(str(args.onnx)).graph.input[0].name
        source = onnx_source(str(args.onnx), {input_name: (1, 3, 640, 640)}, {input_name: ScalarType.float32})
        model = load_model(source, target=gen2_target)
        calibration_data = ({input_name: model_tensor(path)} for path in calibration_paths)
        quantization = QuantizationParams(
            calibration_method=CalibrationMethod.from_str("min_max"),
            activation_quantization_scheme=quantization_scheme(True, False, bits=8),
            weight_quantization_scheme=quantization_scheme(False, True, bits=8),
            requantization_mode="sima", node_names={""}, custom_quantization_configs=None,
            biascorr_type=BiasCorrectionType.NONE, channel_equalization=False, smooth_quant=False,
        )
        compiled = model.quantize(calibration_data=calibration_data, quantization_config=quantization,
                                  model_name="yolo26n", arm_only=False, any_shape_on_mla=True,
                                  automatic_layout_conversion=True)
        compiled.compile(output_path=args.output, batch_size=1, compress=False)
        produced = args.output / "yolo26n_mpk.tar.gz"
        target = args.output / "yolo26n_coco.tar.gz"
        if not produced.is_file():
            raise RuntimeError(f"ModelSDK did not produce expected artifact: {produced}")
        shutil.move(produced, target)
        failure_path.unlink(missing_ok=True)
        print(target)
        return 0
    except Exception as error:
        report = {"status": "unsupported-or-sdk-error", "message": str(error), "exception": type(error).__name__,
                  "onnx": str(args.onnx), "contract": contract, "traceback": traceback.format_exc(),
                  "policy": "No automatic graph surgery was attempted. Apply only compiler-reported changes, then validate equivalence."}
        failure_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"COMPILE STOPPED: {failure_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
