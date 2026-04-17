"""Tests for thresholding methods."""

from __future__ import annotations

import numpy as np
import pytest

from percell4.domain.measure.thresholding import (
    THRESHOLD_METHODS,
    apply_gaussian_smoothing,
    threshold_adaptive,
    threshold_li,
    threshold_manual,
    threshold_otsu,
    threshold_triangle,
)


def _bimodal_image():
    """Create a 100x100 image with two intensity peaks (dark bg, bright spots)."""
    image = np.full((100, 100), 20.0, dtype=np.float32)
    image[20:40, 20:40] = 200.0  # bright square
    image[60:80, 60:80] = 200.0  # bright square
    return image


# ── Otsu ──────────────────────────────────────────────────────


def test_otsu_bimodal():
    """Otsu finds a threshold between the two modes."""
    image = _bimodal_image()
    mask, value = threshold_otsu(image)
    assert mask.dtype == np.uint8
    assert mask.shape == (100, 100)
    assert value > 0  # some threshold found
    assert mask[30, 30] == 1  # bright area above threshold
    assert mask[0, 0] == 0  # dark area below threshold


# ── Triangle ──────────────────────────────────────────────────


def test_triangle():
    """Triangle method produces a valid mask."""
    image = _bimodal_image()
    mask, value = threshold_triangle(image)
    assert mask.dtype == np.uint8
    assert value > 0
    assert mask[30, 30] == 1


# ── Li ────────────────────────────────────────────────────────


def test_li():
    """Li method produces a valid mask."""
    image = _bimodal_image()
    mask, value = threshold_li(image)
    assert mask.dtype == np.uint8
    assert value > 0
    assert mask[30, 30] == 1


# ── Adaptive ──────────────────────────────────────────────────


def test_adaptive_auto_block():
    """Adaptive with auto-calculated block size."""
    image = _bimodal_image()
    mask, value = threshold_adaptive(image)
    assert mask.dtype == np.uint8
    assert mask.shape == (100, 100)


def test_adaptive_custom_block():
    """Adaptive with explicit block size."""
    image = _bimodal_image()
    mask, value = threshold_adaptive(image, block_size=21)
    assert mask.dtype == np.uint8


def test_adaptive_even_block_size_corrected():
    """Even block size is automatically made odd."""
    image = _bimodal_image()
    # Should not raise — even block_size is corrected to odd
    mask, value = threshold_adaptive(image, block_size=20)
    assert mask.dtype == np.uint8


# ── Manual ────────────────────────────────────────────────────


def test_manual():
    """Manual threshold at exact value."""
    image = _bimodal_image()
    mask, value = threshold_manual(image, 100.0)
    assert value == 100.0
    assert mask[30, 30] == 1  # 200 > 100
    assert mask[0, 0] == 0  # 20 < 100


def test_manual_zero_threshold():
    """Manual threshold at 0 — everything above 0 is positive."""
    image = _bimodal_image()
    mask, value = threshold_manual(image, 0.0)
    assert mask.sum() == 100 * 100  # everything is > 0


# ── Gaussian smoothing ────────────────────────────────────────


def test_smoothing_with_sigma():
    """Smoothing with sigma > 0 produces float32 output."""
    image = np.zeros((50, 50), dtype=np.uint16)
    image[25, 25] = 1000
    result = apply_gaussian_smoothing(image, sigma=2.0)
    assert result.dtype == np.float32
    assert result[25, 25] < 1000  # peak is reduced
    assert result[25, 25] > 0  # but still positive


def test_smoothing_none_sigma():
    """Smoothing with None sigma returns original."""
    image = np.ones((10, 10), dtype=np.float32)
    result = apply_gaussian_smoothing(image, sigma=None)
    assert result is image  # same object, not a copy


def test_smoothing_zero_sigma():
    """Smoothing with sigma=0 returns original."""
    image = np.ones((10, 10), dtype=np.float32)
    result = apply_gaussian_smoothing(image, sigma=0.0)
    assert result is image


# ── Registry ──────────────────────────────────────────────────


def test_method_registry():
    """THRESHOLD_METHODS contains all 5 methods."""
    assert len(THRESHOLD_METHODS) == 5
    expected = {"otsu", "triangle", "li", "adaptive", "manual"}
    assert set(THRESHOLD_METHODS.keys()) == expected


# ── Mask dtype consistency ────────────────────────────────────


def test_all_methods_return_uint8():
    """All threshold methods return uint8 masks with 0/1 values."""
    image = _bimodal_image()
    for name, fn in THRESHOLD_METHODS.items():
        if name == "manual":
            mask, _ = fn(image, 100.0)
        else:
            mask, _ = fn(image)
        assert mask.dtype == np.uint8, f"{name} returned {mask.dtype}"
        assert set(np.unique(mask)) <= {0, 1}, f"{name} has values != 0/1"
