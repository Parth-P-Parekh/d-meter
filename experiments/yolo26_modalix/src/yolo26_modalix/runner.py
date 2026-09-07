from __future__ import annotations

from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
import onnxruntime as ort

from .constants import PIPELINE_HEIGHT, PIPELINE_WIDTH
from .postprocess import decode_yolo26
from .preprocess import to_nchw_rgb_float


class OnnxRunner:
    def __init__(self, model_path: Path):
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if len(inputs) != 1 or inputs[0].shape != [1, 3, 640, 640]:
            raise ValueError(f"ONNX input must be static [1,3,640,640], received {[item.shape for item in inputs]}")
        if len(outputs) != 1 or outputs[0].shape != [1, 84, 8400]:
            raise ValueError(f"ONNX output must be [1,84,8400], received {[item.shape for item in outputs]}")
        self.input_name = inputs[0].name
        self.output_name = outputs[0].name

    def infer_bgr(self, source_bgr: np.ndarray) -> tuple[list[dict[str, object]], dict[str, float], dict[str, object]]:
        start = perf_counter()
        source_height, source_width = source_bgr.shape[:2]
        pipeline_bgr = cv2.resize(source_bgr, (PIPELINE_WIDTH, PIPELINE_HEIGHT), interpolation=cv2.INTER_LINEAR)
        tensor, transform = to_nchw_rgb_float(pipeline_bgr)
        preprocess_ms = (perf_counter() - start) * 1000
        infer_start = perf_counter()
        raw = self.session.run([self.output_name], {self.input_name: tensor})[0]
        inference_ms = (perf_counter() - infer_start) * 1000
        post_start = perf_counter()
        detections = decode_yolo26(raw, transform)
        source_scale_x = source_width / PIPELINE_WIDTH
        source_scale_y = source_height / PIPELINE_HEIGHT
        for detection in detections:
            box = detection["box_source"]
            detection["box_source"] = {
                "x": box["x"] * source_scale_x, "y": box["y"] * source_scale_y,
                "width": box["width"] * source_scale_x, "height": box["height"] * source_scale_y,
            }
        postprocess_ms = (perf_counter() - post_start) * 1000
        return detections, {
            "preprocessing": round(preprocess_ms, 3), "inference": round(inference_ms, 3),
            "postprocessing": round(postprocess_ms, 3),
            "total": round(preprocess_ms + inference_ms + postprocess_ms, 3),
        }, {
            "source_to_pipeline": {
                "source_width": source_width, "source_height": source_height,
                "pipeline_width": PIPELINE_WIDTH, "pipeline_height": PIPELINE_HEIGHT,
                "resize_scale_x": PIPELINE_WIDTH / source_width, "resize_scale_y": PIPELINE_HEIGHT / source_height,
                "padding": "none",
            },
            "pipeline_to_model": transform.as_dict(),
        }

    def infer_path(self, image_path: Path):
        source = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if source is None:
            raise ValueError(f"not a readable image: {image_path}")
        return self.infer_bgr(source)
