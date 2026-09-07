"""Decode YOLO26 one-to-many raw output and perform class-aware NumPy NMS."""

from __future__ import annotations

from typing import Sequence

import numpy as np

from .constants import COCO_NAMES, CONFIDENCE_THRESHOLD, MAX_DETECTIONS, NMS_IOU_THRESHOLD, RAW_OUTPUT_SHAPE
from .geometry import Box, LetterboxTransform


def _canonical_output(raw: np.ndarray) -> np.ndarray:
    value = np.asarray(raw, dtype=np.float32)
    if value.shape == RAW_OUTPUT_SHAPE:
        return value[0].T
    if value.shape == (1, RAW_OUTPUT_SHAPE[2], RAW_OUTPUT_SHAPE[1]):
        return value[0]
    if value.shape == RAW_OUTPUT_SHAPE[1:]:
        return value.T
    if value.shape == (RAW_OUTPUT_SHAPE[2], RAW_OUTPUT_SHAPE[1]):
        return value
    raise ValueError(f"expected raw output (1,84,8400), received {value.shape}")


def box_iou_xywh(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Pairwise IoU for top-left xywh arrays shaped (N,4) and (M,4)."""
    first = np.asarray(first, dtype=np.float32)
    second = np.asarray(second, dtype=np.float32)
    if first.ndim != 2 or second.ndim != 2 or first.shape[1:] != (4,) or second.shape[1:] != (4,):
        raise ValueError("IoU inputs must have shape (N,4) and (M,4)")
    a1, a2 = first[:, None, :2], first[:, None, :2] + first[:, None, 2:]
    b1, b2 = second[None, :, :2], second[None, :, :2] + second[None, :, 2:]
    intersection = np.maximum(0.0, np.minimum(a2, b2) - np.maximum(a1, b1)).prod(axis=2)
    area_a = np.maximum(0.0, first[:, 2:]).prod(axis=1)[:, None]
    area_b = np.maximum(0.0, second[:, 2:]).prod(axis=1)[None, :]
    union = area_a + area_b - intersection
    return np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)


def class_aware_nms(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray, iou_threshold: float, limit: int) -> list[int]:
    if not 0.0 <= iou_threshold <= 1.0 or limit < 1:
        raise ValueError("invalid NMS parameters")
    order = np.argsort(-scores, kind="stable")
    kept: list[int] = []
    while order.size and len(kept) < limit:
        current = int(order[0])
        kept.append(current)
        remaining = order[1:]
        if not remaining.size:
            break
        same_class = classes[remaining] == classes[current]
        suppress = np.zeros(remaining.size, dtype=bool)
        if same_class.any():
            ious = box_iou_xywh(boxes[current:current + 1], boxes[remaining[same_class]])[0]
            suppress[np.flatnonzero(same_class)] = ious > iou_threshold
        order = remaining[~suppress]
    return kept


def decode_yolo26(
    raw: np.ndarray,
    transform: LetterboxTransform,
    class_names: Sequence[str] = COCO_NAMES,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
    nms_iou_threshold: float = NMS_IOU_THRESHOLD,
    max_detections: int = MAX_DETECTIONS,
) -> list[dict[str, object]]:
    predictions = _canonical_output(raw)
    if predictions.shape[1] != 4 + len(class_names):
        raise ValueError(f"output has {predictions.shape[1] - 4} classes but {len(class_names)} names were supplied")
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("confidence threshold must be between zero and one")
    class_ids = predictions[:, 4:].argmax(axis=1)
    scores = predictions[np.arange(predictions.shape[0]), class_ids + 4]
    selected = np.flatnonzero(scores >= confidence_threshold)
    if selected.size == 0:
        return []
    xywh_center = predictions[selected, :4]
    boxes = np.column_stack((xywh_center[:, 0] - xywh_center[:, 2] / 2,
                             xywh_center[:, 1] - xywh_center[:, 3] / 2,
                             xywh_center[:, 2], xywh_center[:, 3])).astype(np.float32)
    selected_scores = scores[selected]
    selected_classes = class_ids[selected]
    kept = class_aware_nms(boxes, selected_scores, selected_classes, nms_iou_threshold, max_detections)
    detections: list[dict[str, object]] = []
    for index in kept:
        class_id = int(selected_classes[index])
        model_box = Box(*(float(item) for item in boxes[index]))
        source_box = transform.to_source(model_box)
        if source_box.width <= 0 or source_box.height <= 0:
            continue
        detections.append({
            "class_id": class_id,
            "class_name": class_names[class_id],
            "confidence": float(selected_scores[index]),
            "box_model": model_box.as_dict(),
            "box_source": source_box.as_dict(),
        })
    return detections

