"""Centered black letterboxing and box transformations shared by every runner."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float

    @property
    def x2(self) -> float:
        return self.x + self.width

    @property
    def y2(self) -> float:
        return self.y + self.height

    def as_dict(self) -> dict[str, float]:
        return {"x": float(self.x), "y": float(self.y), "width": float(self.width), "height": float(self.height)}


@dataclass(frozen=True)
class LetterboxTransform:
    source_width: int
    source_height: int
    model_width: int
    model_height: int
    resized_width: int
    resized_height: int
    pad_left: int
    pad_top: int
    pad_right: int
    pad_bottom: int

    @property
    def scale_x(self) -> float:
        return self.resized_width / self.source_width

    @property
    def scale_y(self) -> float:
        return self.resized_height / self.source_height

    @classmethod
    def create(cls, source_width: int, source_height: int, model_width: int = 640, model_height: int = 640) -> "LetterboxTransform":
        if min(source_width, source_height, model_width, model_height) <= 0:
            raise ValueError("all image dimensions must be positive")
        scale = min(model_width / source_width, model_height / source_height)
        # OpenCV receives integer dimensions. Recording those exact dimensions avoids
        # fractional-transform drift during inverse mapping.
        resized_width = min(model_width, max(1, round(source_width * scale)))
        resized_height = min(model_height, max(1, round(source_height * scale)))
        pad_left = (model_width - resized_width) // 2
        pad_top = (model_height - resized_height) // 2
        return cls(
            source_width, source_height, model_width, model_height, resized_width, resized_height,
            pad_left, pad_top, model_width - resized_width - pad_left, model_height - resized_height - pad_top,
        )

    def to_model(self, box: Box) -> Box:
        return Box(box.x * self.scale_x + self.pad_left, box.y * self.scale_y + self.pad_top,
                   box.width * self.scale_x, box.height * self.scale_y)

    def to_source(self, box: Box, clip: bool = True) -> Box:
        mapped = Box((box.x - self.pad_left) / self.scale_x, (box.y - self.pad_top) / self.scale_y,
                     box.width / self.scale_x, box.height / self.scale_y)
        return self.clip(mapped) if clip else mapped

    def clip(self, box: Box) -> Box:
        x1 = min(max(box.x, 0.0), float(self.source_width))
        y1 = min(max(box.y, 0.0), float(self.source_height))
        x2 = min(max(box.x2, 0.0), float(self.source_width))
        y2 = min(max(box.y2, 0.0), float(self.source_height))
        return Box(x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1))

    def as_dict(self) -> dict[str, float | int]:
        return {
            "source_width": self.source_width, "source_height": self.source_height,
            "model_width": self.model_width, "model_height": self.model_height,
            "resized_width": self.resized_width, "resized_height": self.resized_height,
            "resize_scale_x": self.scale_x, "resize_scale_y": self.scale_y,
            "pad_left": self.pad_left, "pad_top": self.pad_top,
            "pad_right": self.pad_right, "pad_bottom": self.pad_bottom,
        }

