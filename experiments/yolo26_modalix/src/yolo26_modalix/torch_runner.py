from __future__ import annotations

from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from .constants import PIPELINE_HEIGHT, PIPELINE_WIDTH
from .postprocess import decode_yolo26
from .preprocess import to_nchw_rgb_float


class TorchRunner:
    """Raw one-to-many PT runner using the same pixels and host postprocessing as ONNX."""

    def __init__(self, model_path: Path):
        self.model = YOLO(str(model_path)).model.eval().fuse()
        head = self.model.model[-1]
        if getattr(head, "end2end", True):
            raise ValueError("PT model is not configured for the one-to-many head")

    def infer_bgr(self, source_bgr: np.ndarray):
        start = perf_counter()
        source_height, source_width = source_bgr.shape[:2]
        pipeline_bgr = cv2.resize(source_bgr, (PIPELINE_WIDTH, PIPELINE_HEIGHT), interpolation=cv2.INTER_LINEAR)
        tensor, transform = to_nchw_rgb_float(pipeline_bgr)
        preprocessing_ms = (perf_counter() - start) * 1000
        infer_start = perf_counter()
        with torch.inference_mode():
            output = self.model(torch.from_numpy(tensor))
        raw = output[0] if isinstance(output, (tuple, list)) else output
        raw_numpy = raw.detach().cpu().numpy()
        inference_ms = (perf_counter() - infer_start) * 1000
        post_start = perf_counter()
        detections = decode_yolo26(raw_numpy, transform)
        scale_x, scale_y = source_width / PIPELINE_WIDTH, source_height / PIPELINE_HEIGHT
        for detection in detections:
            box = detection["box_source"]
            detection["box_source"] = {"x": box["x"] * scale_x, "y": box["y"] * scale_y,
                                       "width": box["width"] * scale_x, "height": box["height"] * scale_y}
        postprocessing_ms = (perf_counter() - post_start) * 1000
        return detections, {
            "preprocessing": round(preprocessing_ms, 3), "inference": round(inference_ms, 3),
            "postprocessing": round(postprocessing_ms, 3),
            "total": round(preprocessing_ms + inference_ms + postprocessing_ms, 3),
        }, {
            "source_to_pipeline": {"source_width": source_width, "source_height": source_height,
                                   "pipeline_width": PIPELINE_WIDTH, "pipeline_height": PIPELINE_HEIGHT,
                                   "resize_scale_x": PIPELINE_WIDTH / source_width,
                                   "resize_scale_y": PIPELINE_HEIGHT / source_height, "padding": "none"},
            "pipeline_to_model": transform.as_dict(),
        }

