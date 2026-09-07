from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np


def save_overlay(source_bgr: np.ndarray, detections: list[dict[str, Any]], path: Path) -> None:
    canvas = source_bgr.copy()
    for detection in detections:
        box = detection["box_source"]
        x1, y1 = round(box["x"]), round(box["y"])
        x2, y2 = round(box["x"] + box["width"]), round(box["y"] + box["height"])
        class_id = int(detection["class_id"])
        colour = ((37 * class_id + 31) % 256, (17 * class_id + 127) % 256, (29 * class_id + 211) % 256)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), colour, 2)
        label = f"{detection['class_name']} {detection['confidence']:.3f}"
        cv2.putText(canvas, label, (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)
    if not cv2.imwrite(str(path), canvas):
        raise RuntimeError(f"could not write overlay: {path}")

