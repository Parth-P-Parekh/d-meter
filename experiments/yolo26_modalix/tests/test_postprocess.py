from __future__ import annotations

import numpy as np
import pytest

from yolo26_modalix.constants import COCO_NAMES
from yolo26_modalix.geometry import LetterboxTransform
from yolo26_modalix.postprocess import box_iou_xywh, decode_yolo26


def raw_output() -> np.ndarray:
    return np.zeros((1, 84, 8400), dtype=np.float32)


def add(raw: np.ndarray, index: int, *, xywh=(320, 320, 100, 80), class_id=0, score=0.9):
    raw[0, :4, index] = xywh
    raw[0, 4 + class_id, index] = score


def test_decodes_class_and_both_coordinate_forms():
    raw = raw_output()
    add(raw, 0, class_id=2, score=0.75)
    detections = decode_yolo26(raw, LetterboxTransform.create(1280, 720))
    assert len(detections) == 1
    assert detections[0]["class_id"] == 2
    assert detections[0]["class_name"] == "car"
    assert detections[0]["confidence"] == pytest.approx(0.75)
    assert set(detections[0]["box_model"]) == {"x", "y", "width", "height"}
    assert set(detections[0]["box_source"]) == {"x", "y", "width", "height"}


def test_class_aware_nms_suppresses_same_class_only():
    raw = raw_output()
    add(raw, 0, class_id=0, score=0.9)
    add(raw, 1, xywh=(321, 320, 100, 80), class_id=0, score=0.8)
    add(raw, 2, xywh=(321, 320, 100, 80), class_id=1, score=0.7)
    detections = decode_yolo26(raw, LetterboxTransform.create(1280, 720))
    assert [item["class_id"] for item in detections] == [0, 1]
    assert [item["confidence"] for item in detections] == pytest.approx([0.9, 0.7])


def test_empty_and_below_threshold_outputs():
    raw = raw_output()
    add(raw, 0, score=0.0999)
    assert decode_yolo26(raw, LetterboxTransform.create(640, 640)) == []


def test_maximum_detection_limit():
    raw = raw_output()
    for index in range(150):
        add(raw, index, xywh=(5 + (index % 20) * 30, 5 + (index // 20) * 30, 10, 10),
            class_id=index % len(COCO_NAMES), score=0.5 + index / 1000)
    assert len(decode_yolo26(raw, LetterboxTransform.create(640, 640))) == 100


def test_rejects_six_tensor_or_wrong_shape_output():
    with pytest.raises(ValueError, match="expected raw output"):
        decode_yolo26(np.zeros((1, 6, 8400), dtype=np.float32), LetterboxTransform.create(640, 640))


def test_iou_known_values():
    boxes = np.array([[0, 0, 10, 10], [20, 20, 5, 5]], dtype=np.float32)
    result = box_iou_xywh(boxes, np.array([[5, 5, 10, 10]], dtype=np.float32))
    assert result[:, 0] == pytest.approx([25 / 175, 0])
