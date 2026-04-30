"""Unit tests for phasor display-time filter composition.

Covers ``compute_valid_phasor_pixels`` — the pure helper that composes
validity + cell-selection + mask filters into a single boolean array.
The phasor plot uses this in ``_refresh_histogram`` and
``_compute_combined_mask``; testing it as a pure function avoids needing
a QApplication.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.domain.flim.phasor_display import (
    compute_valid_phasor_pixels,
    mask_shape_matches,
)


@pytest.fixture
def small_phasor() -> dict:
    """4x4 phasor scene: half NaN, half finite; labels 1..4 mark quadrants."""
    g = np.array(
        [
            [0.5, 0.5, 0.4, 0.4],
            [0.5, 0.5, 0.4, 0.4],
            [0.3, 0.3, 0.2, 0.2],
            [0.3, 0.3, 0.2, 0.2],
        ],
        dtype=np.float32,
    )
    s = np.full_like(g, 0.3)
    labels = np.array(
        [
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [3, 3, 4, 4],
            [3, 3, 4, 4],
        ],
        dtype=np.int32,
    )
    return {
        "g_flat": g.ravel(),
        "s_flat": s.ravel(),
        "labels_flat": labels.ravel(),
        "shape": g.shape,
    }


def test_validity_only_no_filters(small_phasor):
    """No filters → every finite, non-zero pixel valid."""
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=None, filter_ids=None, mask_flat=None,
    )
    assert valid.shape == (16,)
    assert valid.all()


def test_validity_excludes_nan_and_zero():
    """NaN g, NaN s, and g==0 are dropped from valid."""
    g = np.array([0.5, np.nan, 0.5, 0.0, 0.5], dtype=np.float32)
    s = np.array([0.3, 0.3, np.nan, 0.3, 0.3], dtype=np.float32)
    valid = compute_valid_phasor_pixels(
        g, s, labels_flat=None, filter_ids=None, mask_flat=None,
    )
    assert valid.tolist() == [True, False, False, False, True]


def test_cell_filter_only(small_phasor):
    """Cell filter restricts to selected labels."""
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=small_phasor["labels_flat"],
        filter_ids={1, 4},
        mask_flat=None,
    )
    # Labels 1 and 4 occupy 8 of 16 pixels (4 each)
    assert valid.sum() == 8
    expected_labels = small_phasor["labels_flat"][valid]
    assert set(expected_labels.tolist()) == {1, 4}


def test_cell_filter_ignored_without_labels(small_phasor):
    """filter_ids without labels falls back to validity-only."""
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=None,
        filter_ids={1, 2},
        mask_flat=None,
    )
    assert valid.all()


def test_mask_filter_only(small_phasor):
    """Mask filter restricts to truthy pixels in mask_flat."""
    mask = np.zeros(16, dtype=np.uint8)
    mask[[0, 1, 5, 10, 15]] = 1
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=None, filter_ids=None,
        mask_flat=mask,
    )
    assert valid.sum() == 5
    assert np.flatnonzero(valid).tolist() == [0, 1, 5, 10, 15]


def test_compose_with_AND(small_phasor):
    """Cell filter AND mask filter intersects both."""
    # Mask covers labels 1 and 2 region (top half: rows 0-1, indices 0..7)
    mask = np.zeros(16, dtype=np.uint8)
    mask[:8] = 1
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=small_phasor["labels_flat"],
        filter_ids={1, 4},  # labels 1 (top) + label 4 (bottom)
        mask_flat=mask,  # only top half
    )
    # Intersection: label 1 AND top half = 4 pixels (top-left quadrant)
    # Label 4 is in bottom half, mask excludes it
    assert valid.sum() == 4
    assert small_phasor["labels_flat"][valid].tolist() == [1, 1, 1, 1]


def test_mask_filter_shape_mismatch_silently_bypassed(small_phasor):
    """A wrong-size mask is treated as 'no mask' — no crash, no filter."""
    bad_mask = np.ones(8, dtype=np.uint8)  # wrong size: 8 != 16
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=None, filter_ids=None,
        mask_flat=bad_mask,
    )
    # Should fall through to validity-only
    assert valid.all()


def test_mask_with_zero_pixels_returns_empty():
    """All-zero mask = 0 valid pixels (legitimate result, not a bug)."""
    g = np.full(16, 0.5, dtype=np.float32)
    s = np.full(16, 0.3, dtype=np.float32)
    mask = np.zeros(16, dtype=np.uint8)
    valid = compute_valid_phasor_pixels(
        g, s, labels_flat=None, filter_ids=None, mask_flat=mask,
    )
    assert not valid.any()


def test_mask_truthy_handles_nonzero_integer_values():
    """Mask uint8 with values 0/255 (napari convention) treated as boolean."""
    g = np.full(4, 0.5, dtype=np.float32)
    s = np.full(4, 0.3, dtype=np.float32)
    mask = np.array([0, 255, 0, 255], dtype=np.uint8)
    valid = compute_valid_phasor_pixels(
        g, s, labels_flat=None, filter_ids=None, mask_flat=mask,
    )
    assert valid.tolist() == [False, True, False, True]


def test_empty_filter_ids_excludes_all_pixels(small_phasor):
    """An empty filter set excludes everything — different from 'no filter'."""
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=small_phasor["labels_flat"],
        filter_ids=set(),
        mask_flat=None,
    )
    assert not valid.any()


def test_mask_shape_matches_helper():
    """mask_shape_matches: True only when shapes equal."""
    a = np.zeros((4, 4), dtype=np.uint8)
    b = np.zeros((4, 4), dtype=np.float32)
    c = np.zeros((4, 5), dtype=np.uint8)

    assert mask_shape_matches(a, b)
    assert not mask_shape_matches(a, c)
    assert not mask_shape_matches(None, b)
    assert not mask_shape_matches(a, None)
    assert not mask_shape_matches(None, None)
