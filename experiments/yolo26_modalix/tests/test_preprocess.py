from __future__ import annotations

import numpy as np

from yolo26_modalix.preprocess import letterbox_bgr, to_nchw_rgb_float


def test_letterbox_is_centered_black():
    source = np.full((720, 1280, 3), 255, dtype=np.uint8)
    model, transform = letterbox_bgr(source)
    assert model.shape == (640, 640, 3)
    assert np.all(model[:140] == 0)
    assert np.all(model[140:500] == 255)
    assert np.all(model[500:] == 0)
    assert transform.pad_top == 140


def test_bgr_becomes_normalized_nchw_rgb():
    source = np.zeros((640, 640, 3), dtype=np.uint8)
    source[:, :, :] = (0, 127, 255)
    tensor, _ = to_nchw_rgb_float(source)
    assert tensor.shape == (1, 3, 640, 640)
    assert tensor.dtype == np.float32
    assert tensor[0, :, 0, 0].tolist() == [1.0, np.float32(127 / 255), 0.0]

