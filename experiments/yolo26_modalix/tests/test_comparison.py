from __future__ import annotations

from yolo26_modalix.comparison import ParityPolicy, compare_detections


def detection(class_id=0, confidence=0.9, x=0):
    return {"class_id": class_id, "confidence": confidence,
            "box_source": {"x": x, "y": 0, "width": 10, "height": 10}}


def test_parity_accepts_permuted_one_to_one_matches():
    reference = [detection(x=0), detection(class_id=1, x=20)]
    candidate = [detection(class_id=1, confidence=0.91, x=20), detection(confidence=0.89, x=0)]
    result = compare_detections(reference, candidate, ParityPolicy(0.9, 0.05))
    assert result["passed"]


def test_parity_rejects_count_class_iou_and_confidence_differences():
    policy = ParityPolicy(0.9, 0.05)
    assert not compare_detections([detection()], [], policy)["passed"]
    assert not compare_detections([detection()], [detection(class_id=1)], policy)["passed"]
    assert not compare_detections([detection()], [detection(x=2)], policy)["passed"]
    assert not compare_detections([detection()], [detection(confidence=0.8)], policy)["passed"]


def test_empty_results_match():
    assert compare_detections([], [], ParityPolicy(0.99, 0.001))["passed"]

