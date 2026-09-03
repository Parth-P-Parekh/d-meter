"""Annotated comparison overlays. Pillow is required only for this output."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_overlay(image_path: Path, output_path: Path, baseline: list[dict[str, Any]], demo: list[dict[str, Any]]) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError as error:
        raise RuntimeError("install requirements.txt to generate overlays") from error
    with Image.open(image_path).convert("RGB") as image:
        draw = ImageDraw.Draw(image)
        for label, detections, colour in (("baseline", baseline, "#00ff00"), ("demo", demo, "#ff9900")):
            for detection in detections:
                box = detection["box_display"]
                x1, y1 = float(box["x"]), float(box["y"])
                x2, y2 = x1 + float(box["width"]), y1 + float(box["height"])
                draw.rectangle((x1, y1, x2, y2), outline=colour, width=3)
                draw.text((x1, max(0, y1 - 13)), f"{label}:{detection['class_name']} {float(detection['confidence']):.3f}", fill=colour)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
