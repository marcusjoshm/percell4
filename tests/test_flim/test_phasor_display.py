"""Unit tests for phasor display-time filter composition.

Covers ``compute_valid_phasor_pixels`` — the pure helper that composes
validity + cell-selection + mask filters into a single boolean array.
The phasor plot uses this in ``_refresh_histogram`` and
``_compute_filtered_binary``; testing it as a pure function avoids needing
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


# ── Intensity threshold + reference circle (U2) ───────────────────────


def test_intensity_threshold_only(small_phasor):
    """Pixels above the intensity floor are kept; below are dropped."""
    intensity = np.array([
        100, 200, 50, 50,
        100, 100, 50, 50,
        100, 100, 50, 50,
        100, 100, 50, 50,
    ], dtype=np.float32)
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=None, filter_ids=None, mask_flat=None,
        intensity_flat=intensity, intensity_threshold=100.0,
    )
    # 8 pixels have intensity >= 100 (rows 0–3 each contribute 2)
    assert valid.sum() == 8
    assert intensity[valid].min() >= 100


def test_intensity_threshold_zero_is_noop(small_phasor):
    """intensity_threshold=0 is a no-op even when intensity_flat is provided."""
    intensity = np.full(16, 5.0, dtype=np.float32)
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=None, filter_ids=None, mask_flat=None,
        intensity_flat=intensity, intensity_threshold=0.0,
    )
    assert valid.all()


def test_intensity_none_disables_threshold(small_phasor):
    """intensity_flat=None disables the filter regardless of threshold."""
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=None, filter_ids=None, mask_flat=None,
        intensity_flat=None, intensity_threshold=999_999.0,
    )
    assert valid.all()


def test_intensity_shape_mismatch_silently_bypassed(small_phasor):
    """Wrong-size intensity is treated as 'no filter' — same as mask convention."""
    bad_intensity = np.full(8, 999_999.0, dtype=np.float32)  # 8 != 16
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=None, filter_ids=None, mask_flat=None,
        intensity_flat=bad_intensity, intensity_threshold=100.0,
    )
    assert valid.all()


def test_reference_circle_only(small_phasor):
    """Restrict to pixels inside a circle around (G_c, S_c)."""
    # small_phasor has g in {0.2, 0.3, 0.4, 0.5} and s == 0.3 everywhere.
    # A circle at (0.4, 0.3) with radius 0.05 captures only the g=0.4
    # column (4 pixels) — the 0.5 column is at distance 0.1.
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=None, filter_ids=None, mask_flat=None,
        ref_circle_center=(0.4, 0.3), ref_circle_radius=0.05,
    )
    assert valid.sum() == 4


def test_reference_circle_zero_radius_excludes_all():
    """A degenerate radius=0 circle excludes everything except an exact-center match.

    When no pixel sits exactly on the center, valid is all-False.
    """
    g = np.array([0.5, 0.5, 0.5], dtype=np.float32)
    s = np.array([0.3, 0.4, 0.5], dtype=np.float32)
    valid = compute_valid_phasor_pixels(
        g, s, labels_flat=None, filter_ids=None, mask_flat=None,
        ref_circle_center=(0.3, 0.3), ref_circle_radius=0.0,
    )
    assert not valid.any()


def test_reference_circle_disabled_by_none():
    """ref_circle_radius=None disables the filter."""
    g = np.array([0.5, 0.5, 0.5], dtype=np.float32)
    s = np.array([0.3, 0.4, 0.5], dtype=np.float32)
    valid = compute_valid_phasor_pixels(
        g, s, labels_flat=None, filter_ids=None, mask_flat=None,
        ref_circle_center=(0.5, 0.4), ref_circle_radius=None,
    )
    assert valid.all()


def test_all_five_filters_compose_via_AND(small_phasor):
    """Cell + mask + intensity + reference circle + finiteness all AND."""
    intensity = np.array([
        100, 200, 50, 50,
        100, 100, 50, 50,
        100, 100, 50, 50,
        100, 100, 50, 50,
    ], dtype=np.float32)
    # Mask: top half only (indices 0..7)
    mask = np.zeros(16, dtype=np.uint8)
    mask[:8] = 1
    valid = compute_valid_phasor_pixels(
        small_phasor["g_flat"], small_phasor["s_flat"],
        labels_flat=small_phasor["labels_flat"],
        filter_ids={1, 4},  # label 1 (top-left) + label 4 (bottom-right)
        mask_flat=mask,  # top half only
        intensity_flat=intensity, intensity_threshold=100.0,
        # Reference circle around (0.5, 0.3) with r=0.01 — only g=0.5 column matches
        ref_circle_center=(0.5, 0.3), ref_circle_radius=0.01,
    )
    # Intersection: label 1 (top-left, g=0.5) AND mask top half AND intensity≥100
    # AND ref-circle (g=0.5). Result = top-left quadrant pixels with intensity≥100.
    # Indices 0,1 have intensity 100,200 ≥ 100; indices 4,5 have intensity 100,100.
    # All four are label 1 + top half + g=0.5 + ref circle. → 4 pixels.
    assert valid.sum() == 4
