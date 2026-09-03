"""Validation for versioned baseline and demo inference-result JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .geometry import LetterboxTransform

REQUIRED_RESULT_FIELDS = {"schema_version", "frame_id", "pipeline_version", "model_version", "image", "transform", "detections", "timings_ms"}
REQUIRED_IMAGE_FIELDS = {"path", "sha256", "width", "height"}
REQUIRED_LETTERBOX_FIELDS = {
    "source_width", "source_height", "model_width", "model_height",
    "resize_scale_x", "resize_scale_y", "pad_left", "pad_top", "pad_right", "pad_bottom",
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read valid JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def validate_result(value: dict[str, Any], expected_frame_id: str | None = None) -> None:
    missing = REQUIRED_RESULT_FIELDS - value.keys()
    if missing:
        raise ValueError(f"result missing fields: {', '.join(sorted(missing))}")
    if value["schema_version"] != "1.1":
        raise ValueError("result schema_version must be 1.1")
    if expected_frame_id and value["frame_id"] != expected_frame_id:
        raise ValueError(f"result frame_id must be {expected_frame_id}")
    if not isinstance(value["detections"], list):
        raise ValueError("detections must be a list")
    if not isinstance(value["image"], dict) or not isinstance(value["timings_ms"], dict):
        raise ValueError("image and timings_ms must be objects")
    if REQUIRED_IMAGE_FIELDS - value["image"].keys():
        raise ValueError("image is missing path, sha256, width, or height")
    if int(value["image"]["width"]) <= 0 or int(value["image"]["height"]) <= 0:
        raise ValueError("image dimensions must be positive")
    if not isinstance(value["transform"], dict) or {"source_to_input", "input_to_model"} - value["transform"].keys():
        raise ValueError("transform must contain source_to_input and input_to_model")
    source_to_input = value["transform"]["source_to_input"]
    input_to_model = value["transform"]["input_to_model"]
    if not isinstance(source_to_input, dict) or not isinstance(input_to_model, dict):
        raise ValueError("transform stages must be objects")
    required_source_to_input = {"source_width", "source_height", "input_width", "input_height", "resize_scale_x", "resize_scale_y", "pad_left", "pad_top", "pad_right", "pad_bottom"}
    if required_source_to_input - source_to_input.keys() or REQUIRED_LETTERBOX_FIELDS - input_to_model.keys():
        raise ValueError("transform is missing required geometry fields")
    transform = LetterboxTransform.from_dict(input_to_model)
    expected = transform.as_dict()
    for key in ("resize_scale_x", "resize_scale_y", "pad_left", "pad_top", "pad_right", "pad_bottom"):
        if abs(float(input_to_model[key]) - float(expected[key])) > 1e-6:
            raise ValueError(f"transform {key} is inconsistent with letterbox geometry")
    for key, duration in value["timings_ms"].items():
        if float(duration) < 0:
            raise ValueError(f"timings_ms.{key} must not be negative")
    for index, detection in enumerate(value["detections"]):
        if not isinstance(detection, dict):
            raise ValueError(f"detection {index} must be an object")
        for key in ("class_name", "confidence", "box_model", "box_input", "box_display"):
            if key not in detection:
                raise ValueError(f"detection {index} missing {key}")
        if not isinstance(detection["class_name"], str):
            raise ValueError(f"detection {index} class_name must be a string")
        confidence = float(detection["confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError(f"detection {index} confidence must be in [0, 1]")
        for box_name in ("box_model", "box_input", "box_display"):
            box = detection[box_name]
            if not isinstance(box, dict) or set(("x", "y", "width", "height")) - box.keys():
                raise ValueError(f"detection {index} {box_name} is invalid")
            if float(box["width"]) < 0 or float(box["height"]) < 0:
                raise ValueError(f"detection {index} {box_name} has negative dimensions")
    # Force construction so an invalid transform is never accepted silently.
    _ = transform
