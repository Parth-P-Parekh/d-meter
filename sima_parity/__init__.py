"""Offline comparison tools for the SiMa Wood pipeline parity demo."""

from .comparison import ComparisonPolicy, compare_detections
from .geometry import LetterboxTransform

__all__ = ["ComparisonPolicy", "LetterboxTransform", "compare_detections"]
