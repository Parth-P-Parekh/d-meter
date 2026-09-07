from __future__ import annotations

import cv2
import numpy as np

from .geometry import LetterboxTransform


def letterbox_bgr(source_bgr: np.ndarray, width: int = 640, height: int = 640) -> tuple[np.ndarray, LetterboxTransform]:
    if source_bgr is None or source_bgr.ndim != 3 or source_bgr.shape[2] != 3:
        raise ValueError("source image must be an HxWx3 BGR array")
    transform = LetterboxTransform.create(source_bgr.shape[1], source_bgr.shape[0], width, height)
    resized = cv2.resize(source_bgr, (transform.resized_width, transform.resized_height), interpolation=cv2.INTER_LINEAR)
    model_bgr = cv2.copyMakeBorder(
        resized, transform.pad_top, transform.pad_bottom, transform.pad_left, transform.pad_right,
        cv2.BORDER_CONSTANT, value=(0, 0, 0),
    )
    return model_bgr, transform


def to_nchw_rgb_float(source_bgr: np.ndarray) -> tuple[np.ndarray, LetterboxTransform]:
    model_bgr, transform = letterbox_bgr(source_bgr)
    model_rgb = cv2.cvtColor(model_bgr, cv2.COLOR_BGR2RGB)
    tensor = np.ascontiguousarray(model_rgb.transpose(2, 0, 1)[None], dtype=np.float32) / np.float32(255.0)
    return tensor, transform

