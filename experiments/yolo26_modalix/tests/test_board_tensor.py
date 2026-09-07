from __future__ import annotations

import numpy as np
import pytest

from yolo26_modalix.raw_tensor import parse_raw_float32


def test_raw_board_tensor_shape_and_validation():
    raw = np.zeros((1, 84, 8400), dtype="<f4")
    assert parse_raw_float32(raw.tobytes()).shape == (1, 84, 8400)
    with pytest.raises(ValueError):
        parse_raw_float32(raw.tobytes()[:-4])
    raw.flat[0] = np.nan
    with pytest.raises(ValueError):
        parse_raw_float32(raw.tobytes())
