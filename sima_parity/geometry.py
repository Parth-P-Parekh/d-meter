"""Exact aspect-preserving letterbox geometry and inverse box mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class LetterboxTransform:
    source_width: int
    source_height: int
    model_width: int
    model_height: int
    scale: float
    resized_width: float
    resized_height: float
    pad_left: float
    pad_top: float
    pad_right: float
    pad_bottom: float

    @classmethod
    def create(
        cls, source_width: int, source_height: int, model_width: int = 640, model_height: int = 640
    ) -> "LetterboxTransform":
        if min(source_width, source_height, model_width, model_height) <= 0:
            raise ValueError("all image dimensions must be positive")
        scale = min(model_width / source_width, model_height / source_height)
        resized_width = source_width * scale
        resized_height = source_height * scale
        pad_left = (model_width - resized_width) / 2
        pad_top = (model_height - resized_height) / 2
        return cls(
            source_width, source_height, model_width, model_height, scale,
            resized_width, resized_height, pad_left, pad_top,
            model_width - resized_width - pad_left,
            model_height - resized_height - pad_top,
        )

    def to_model(self, box: Box) -> Box:
        return Box(
            box.x * self.scale + self.pad_left,
            box.y * self.scale + self.pad_top,
            box.width * self.scale,
            box.height * self.scale,
        )

    def to_source(self, box: Box, clip: bool = True) -> Box:
        mapped = Box(
            (box.x - self.pad_left) / self.scale,
            (box.y - self.pad_top) / self.scale,
            box.width / self.scale,
            box.height / self.scale,
        )
        return self.clip_to_source(mapped) if clip else mapped

    def clip_to_source(self, box: Box) -> Box:
        x1 = min(max(box.x, 0.0), float(self.source_width))
        y1 = min(max(box.y, 0.0), float(self.source_height))
        x2 = min(max(box.x + box.width, 0.0), float(self.source_width))
        y2 = min(max(box.y + box.height, 0.0), float(self.source_height))
        return Box(x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1))

    def as_dict(self) -> dict[str, float | int]:
        return {
            "source_width": self.source_width,
            "source_height": self.source_height,
            "model_width": self.model_width,
            "model_height": self.model_height,
            "resize_scale_x": self.scale,
            "resize_scale_y": self.scale,
            "pad_left": self.pad_left,
            "pad_top": self.pad_top,
            "pad_right": self.pad_right,
            "pad_bottom": self.pad_bottom,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, float | int]) -> "LetterboxTransform":
        return cls.create(
            int(value["source_width"]), int(value["source_height"]),
            int(value["model_width"]), int(value["model_height"]),
        )
