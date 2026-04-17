"""Tests for NaN-safe metric functions."""

from __future__ import annotations

import numpy as np

from percell4.domain.measure.metrics import (
    BUILTIN_METRICS,
    area,
    integrated_intensity,
    max_intensity,
    mean_intensity,
    median_intensity,
    min_intensity,
    std_intensity,
)


def _image_and_mask():
    """3x3 image with a 2x2 cell mask (values 10, 20, 30, 40)."""
    image = np.array(
        [[10, 20, 0], [30, 40, 0], [0, 0, 0]], dtype=np.float32
    )
    mask = np.array(
        [[True, True, False], [True, True, False], [False, False, False]]
    )
    return image, mask


def test_mean_intensity():
    image, mask = _image_and_mask()
    assert mean_intensity(image, mask) == 25.0  # (10+20+30+40)/4


def test_max_intensity():
    image, mask = _image_and_mask()
    assert max_intensity(image, mask) == 40.0


def test_min_intensity():
    image, mask = _image_and_mask()
    assert min_intensity(image, mask) == 10.0


def test_integrated_intensity():
    image, mask = _image_and_mask()
    assert integrated_intensity(image, mask) == 100.0  # 10+20+30+40


def test_std_intensity():
    image, mask = _image_and_mask()
    expected = np.std([10, 20, 30, 40])
    assert abs(std_intensity(image, mask) - expected) < 0.01


def test_median_intensity():
    image, mask = _image_and_mask()
    assert median_intensity(image, mask) == 25.0  # median of [10,20,30,40]


def test_area():
    image, mask = _image_and_mask()
    assert area(image, mask) == 4.0


def test_nan_handling():
    """Metrics ignore NaN values."""
    image = np.array([[np.nan, 20], [30, np.nan]], dtype=np.float32)
    mask = np.ones((2, 2), dtype=bool)

    assert mean_intensity(image, mask) == 25.0  # nanmean of [nan, 20, 30, nan]
    assert max_intensity(image, mask) == 30.0
    assert min_intensity(image, mask) == 20.0
    assert integrated_intensity(image, mask) == 50.0


def test_empty_mask_returns_zero():
    """All metrics return 0.0 for empty masks."""
    image = np.ones((3, 3), dtype=np.float32)
    mask = np.zeros((3, 3), dtype=bool)

    for name, fn in BUILTIN_METRICS.items():
        assert fn(image, mask) == 0.0, f"{name} should return 0.0 for empty mask"


def test_builtin_metrics_has_all_seven():
    """BUILTIN_METRICS dict contains exactly 7 metrics."""
    assert len(BUILTIN_METRICS) == 7
    expected = {
        "mean_intensity", "max_intensity", "min_intensity",
        "integrated_intensity", "std_intensity", "median_intensity", "area",
    }
    assert set(BUILTIN_METRICS.keys()) == expected
