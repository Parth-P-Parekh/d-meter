from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .postprocess import box_iou_xywh


@dataclass(frozen=True)
class ParityPolicy:
    minimum_iou: float
    maximum_confidence_delta: float


def _boxes(detections: list[dict[str, Any]]) -> np.ndarray:
    return np.array([[item["box_source"][key] for key in ("x", "y", "width", "height")] for item in detections], dtype=np.float32).reshape(-1, 4)


def compare_detections(reference: list[dict[str, Any]], candidate: list[dict[str, Any]], policy: ParityPolicy) -> dict[str, Any]:
    if not 0 <= policy.minimum_iou <= 1 or policy.maximum_confidence_delta < 0:
        raise ValueError("invalid parity policy")
    if len(reference) != len(candidate):
        return {"passed": False, "error": f"detection count differs: {len(reference)} != {len(candidate)}", "matches": []}
    if not reference:
        return {"passed": True, "matches": []}
    ious = box_iou_xywh(_boxes(reference), _boxes(candidate))
    edges: list[list[int]] = []
    for ref_index, ref in enumerate(reference):
        allowed = []
        for candidate_index, item in enumerate(candidate):
            confidence_delta = abs(float(ref["confidence"]) - float(item["confidence"]))
            if (int(ref["class_id"]) == int(item["class_id"]) and ious[ref_index, candidate_index] >= policy.minimum_iou
                    and confidence_delta <= policy.maximum_confidence_delta):
                allowed.append(candidate_index)
        allowed.sort(key=lambda index: float(ious[ref_index, index]), reverse=True)
        edges.append(allowed)
    assigned: dict[int, int] = {}

    def augment(ref_index: int, visited: set[int]) -> bool:
        for candidate_index in edges[ref_index]:
            if candidate_index in visited:
                continue
            visited.add(candidate_index)
            if candidate_index not in assigned or augment(assigned[candidate_index], visited):
                assigned[candidate_index] = ref_index
                return True
        return False

    if not all(augment(index, set()) for index in range(len(reference))):
        return {"passed": False, "error": "no one-to-one class/IoU/confidence matching satisfies policy", "matches": []}
    matches = []
    for candidate_index, ref_index in sorted(assigned.items(), key=lambda pair: pair[1]):
        matches.append({"reference_index": ref_index, "candidate_index": candidate_index,
                        "class_id": int(reference[ref_index]["class_id"]),
                        "iou": float(ious[ref_index, candidate_index]),
                        "confidence_delta": abs(float(reference[ref_index]["confidence"]) - float(candidate[candidate_index]["confidence"]))})
    return {"passed": True, "matches": matches}

