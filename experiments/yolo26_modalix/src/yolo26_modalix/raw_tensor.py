from __future__ import annotations

import numpy as np

EXPECTED_FLOATS = 1 * 84 * 8400


def parse_raw_float32(data: bytes) -> np.ndarray:
    if len(data) != EXPECTED_FLOATS * 4:
        raise ValueError(f"detess/dequant output must contain {EXPECTED_FLOATS} float32 values; received {len(data)} bytes")
    output = np.frombuffer(data, dtype="<f4").reshape(1, 84, 8400)
    if not np.isfinite(output).all():
        raise ValueError("detess/dequant output contains non-finite values")
    return output

