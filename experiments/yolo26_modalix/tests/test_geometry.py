from __future__ import annotations

import pytest

from yolo26_modalix.geometry import Box, LetterboxTransform


def test_1280_by_720_letterbox_and_round_trip():
    transform = LetterboxTransform.create(1280, 720)
    assert transform.resized_width == 640
    assert transform.resized_height == 360
    assert (transform.pad_left, transform.pad_top, transform.pad_right, transform.pad_bottom) == (0, 140, 0, 140)
    source = Box(99.5, 50.25, 300, 200)
    restored = transform.to_source(transform.to_model(source), clip=False)
    assert restored == pytest.approx(source)


def test_portrait_letterbox_has_asymmetric_rounding_but_exact_shape():
    transform = LetterboxTransform.create(333, 1000)
    assert transform.resized_width + transform.pad_left + transform.pad_right == 640
    assert transform.resized_height + transform.pad_top + transform.pad_bottom == 640
    assert abs(transform.pad_left - transform.pad_right) <= 1


def test_inverse_mapping_clips_to_source():
    transform = LetterboxTransform.create(1280, 720)
    clipped = transform.to_source(Box(-50, 100, 100, 500))
    assert clipped.x == 0
    assert clipped.y == 0
    assert clipped.x2 <= 1280
    assert clipped.y2 <= 720


def test_invalid_dimensions_rejected():
    with pytest.raises(ValueError):
        LetterboxTransform.create(0, 720)

