"""Deterministic production-baseline to demo-detection comparison."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .geometry import Box


@dataclass(frozen=True)
class ComparisonPolicy:
    minimum_iou: float = 0.90
    maximum_score_delta: float = 0.05


def _box(detection: dict[str, Any]) -> Box:
    value = detection.get("box_input")
    if not isinstance(value, dict):
        raise ValueError("detection must contain a box_input object")
    try:
        return Box(*(float(value[key]) for key in ("x", "y", "width", "height")))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("box_input must contain numeric x, y, width, and height") from error


def intersection_over_union(first: Box, second: Box) -> float:
    x1 = max(first.x, second.x)
    y1 = max(first.y, second.y)
    x2 = min(first.x + first.width, second.x + second.width)
    y2 = min(first.y + first.height, second.y + second.height)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = first.width * first.height + second.width * second.height - intersection
    return intersection / union if union > 0 else 0.0


def compare_detections(
    baseline: Iterable[dict[str, Any]], demo: Iterable[dict[str, Any]], policy: ComparisonPolicy = ComparisonPolicy()
) -> dict[str, Any]:
    baseline_list = list(baseline)
    demo_list = list(demo)
    candidates: list[tuple[float, float, int, int]] = []
    for baseline_index, expected in enumerate(baseline_list):
        for demo_index, actual in enumerate(demo_list):
            if expected.get("class_name") != actual.get("class_name"):
                continue
            iou = intersection_over_union(_box(expected), _box(actual))
            score_delta = abs(float(expected["confidence"]) - float(actual["confidence"]))
            if iou >= policy.minimum_iou and score_delta <= policy.maximum_score_delta:
                candidates.append((iou, score_delta, baseline_index, demo_index))

    candidates.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
    used_baseline: set[int] = set()
    used_demo: set[int] = set()
    matches: list[dict[str, Any]] = []
    for iou, score_delta, baseline_index, demo_index in candidates:
        if baseline_index in used_baseline or demo_index in used_demo:
            continue
        used_baseline.add(baseline_index)
        used_demo.add(demo_index)
        matches.append({
            "baseline_index": baseline_index,
            "demo_index": demo_index,
            "class_name": baseline_list[baseline_index]["class_name"],
            "iou": iou,
            "score_delta": score_delta,
        })

    missing = [index for index in range(len(baseline_list)) if index not in used_baseline]
    extra = [index for index in range(len(demo_list)) if index not in used_demo]
    return {
        "passed": len(baseline_list) == len(demo_list) and not missing and not extra,
        "policy": {"minimum_iou": policy.minimum_iou, "maximum_score_delta": policy.maximum_score_delta},
        "baseline_count": len(baseline_list),
        "demo_count": len(demo_list),
        "matches": matches,
        "missing_baseline_indexes": missing,
        "extra_demo_indexes": extra,
    }
