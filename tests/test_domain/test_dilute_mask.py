"""Tests for the pure-domain dilute-mask morphology helper.

Covers the U1 scenarios from
``docs/plans/2026-06-28-002-feat-dilute-phase-mask-from-mask-plan.md``:

- Happy path: filled-square condensed mask + 2-cell labels + radius 2 →
  1 for in-cell pixels outside the dilated square, 0 on the dilated square,
  0 outside all cells; specific coordinates asserted.
- Edge: ``radius_px == 0`` → ``(labels > 0) & ~condensed`` exactly (pure
  invert-within-cells, no growth).
- Edge: empty condensed → result equals ``labels > 0``.
- Edge: condensed fills a cell → that cell contributes 0 pixels.
- Edge: condensed spills outside cells → no out-of-cell pixel appears in
  the output.
- Edge (D8): cross-cell halo — a condensed blob in cell A near the A/B
  border with a radius spanning the gap removes dilute pixels inside
  neighbor cell B (the deliberate global-dilation semantic).
- Error: ``condensed.shape != seg_labels.shape`` raises a clear
  ``ValueError`` (not a numpy broadcast error).
- Edge (dtype): a 0/255 ``uint8`` condensed mask is treated as boolean
  (``> 0``); output is ``bool``; shape == input shape.
- Stack: ``(T, H, W)`` inputs → ``(T, H, W)`` bool output, per-frame
  correct; an empty label plane → all-zero plane (exact-``T``).
"""

from __future__ import annotations

import numpy as np
import pytest
from skimage.morphology import dilation, disk

from percell4.domain.segmentation.dilute_mask import (
    dilate_mask,
    dilute_from_mask,
    dilute_from_mask_stack,
    invert_within_cells,
)

# ── happy path ──────────────────────────────────────────────────────────


def _two_cell_labels() -> np.ndarray:
    """A 20x20 label image: cell 1 (cols 2-9) and cell 2 (cols 11-18),
    rows 2-17, with a background border + a 1-col gap (col 10) between
    them."""
    labels = np.zeros((20, 20), dtype=np.int32)
    labels[2:18, 2:10] = 1   # cell 1: cols 2..9
    labels[2:18, 11:19] = 2  # cell 2: cols 11..18
    return labels


def test_happy_path_specific_coordinates():
    """Filled-square condensed in cell 1 + radius 2 → in-cell pixels
    outside the dilated square are 1, dilated-square pixels are 0, and
    out-of-cell pixels are 0."""
    labels = _two_cell_labels()
    condensed = np.zeros((20, 20), dtype=bool)
    condensed[7:10, 4:7] = True  # 3x3 square fully inside cell 1

    result = dilute_from_mask(condensed, labels, radius_px=2)

    # In-cell, outside the dilated square → dilute (1).
    assert result[15, 8]   # cell 1, well below the square+halo
    assert result[8, 9]    # cell 1 col 9: distance 3 from square's col 6 > 2
    assert result[10, 15]  # cell 2, untouched (halo doesn't cross the gap)

    # On the dilated square → 0.
    assert not result[8, 5]  # square center
    assert not result[8, 8]  # distance 2 from col 6 → covered by disk(2)

    # Outside all cells → 0.
    assert not result[0, 0]   # background border
    assert not result[5, 10]  # the gap column between the two cells

    # Output is a boolean array shaped like the inputs.
    assert result.dtype == np.bool_
    assert result.shape == labels.shape


def test_result_is_subset_of_in_cell():
    """Every output pixel lies inside a cell (output ⊆ labels > 0)."""
    labels = _two_cell_labels()
    condensed = np.zeros((20, 20), dtype=bool)
    condensed[7:10, 4:7] = True

    result = dilute_from_mask(condensed, labels, radius_px=2)
    assert not result[labels == 0].any()


# ── radius_px == 0 (pure invert-within-cells) ──────────────────────────


def test_radius_zero_is_pure_invert_within_cells():
    """radius_px == 0 → no dilation; result equals (labels>0) & ~condensed
    exactly."""
    labels = _two_cell_labels()
    rng = np.random.default_rng(seed=0)
    condensed = rng.random((20, 20)) > 0.7  # scattered foreground

    result = dilute_from_mask(condensed, labels, radius_px=0)
    expected = (labels > 0) & ~condensed
    np.testing.assert_array_equal(result, expected)
    # And it matches the standalone helper.
    np.testing.assert_array_equal(result, invert_within_cells(condensed, labels))


def test_negative_radius_is_no_dilation():
    """A non-positive radius is treated as "no dilation" (controller
    semantic: radius <= 0)."""
    labels = _two_cell_labels()
    condensed = np.zeros((20, 20), dtype=bool)
    condensed[7:10, 4:7] = True

    result = dilute_from_mask(condensed, labels, radius_px=-3)
    expected = (labels > 0) & ~condensed
    np.testing.assert_array_equal(result, expected)


# ── empty condensed ─────────────────────────────────────────────────────


def test_empty_condensed_equals_in_cell():
    """An empty condensed mask → the whole cell interiors are dilute."""
    labels = _two_cell_labels()
    condensed = np.zeros((20, 20), dtype=bool)

    result = dilute_from_mask(condensed, labels, radius_px=5)
    np.testing.assert_array_equal(result, labels > 0)


# ── condensed fills a cell ─────────────────────────────────────────────


def test_condensed_fills_cell_contributes_zero():
    """When condensed fully covers a cell, that cell contributes zero
    dilute pixels (and dilation can only grow the coverage)."""
    labels = _two_cell_labels()
    condensed = np.zeros((20, 20), dtype=bool)
    condensed[labels == 1] = True  # fill cell 1 exactly

    result = dilute_from_mask(condensed, labels, radius_px=1)

    # Cell 1 is fully condensed → no dilute pixels there.
    assert not result[labels == 1].any()
    # Cell 2 is untouched → fully dilute (gap >> radius 1 keeps the halo out).
    assert result[labels == 2].all()


# ── condensed spills outside cells ─────────────────────────────────────


def test_condensed_spill_outside_cells_never_in_output():
    """A condensed blob partly outside the cells (and its dilation) never
    contributes out-of-cell pixels to the output."""
    labels = _two_cell_labels()
    condensed = np.zeros((20, 20), dtype=bool)
    # Blob straddling cell 1's left border into the background.
    condensed[8:11, 0:4] = True

    result = dilute_from_mask(condensed, labels, radius_px=3)

    # No output pixel lands in background, despite the spill + dilation.
    assert not result[labels == 0].any()


# ── cross-cell halo (D8) ───────────────────────────────────────────────


def test_cross_cell_halo_removes_dilute_inside_neighbor():
    """D8 — global dilation, then clipped within cells.

    Two ADJACENT cells A (cols 0-8) and B (cols 10-18) separated by a
    1-column gap (col 9). A single condensed pixel sits in A at the A/B
    border (col 8). With radius 3 the global dilation reaches col 11
    (distance 3) — crossing the gap into B — so dilute pixels just inside
    B near the border are REMOVED. This pins the intended global-dilation
    semantic (a per-cell-contained dilation would leave B untouched).
    """
    h, w = 10, 19
    labels = np.zeros((h, w), dtype=np.int32)
    labels[:, 0:9] = 1    # cell A: cols 0..8
    labels[:, 10:19] = 2  # cell B: cols 10..18
    # col 9 is the background gap between A and B.

    condensed = np.zeros((h, w), dtype=bool)
    condensed[5, 8] = True  # single pixel in A at the A/B border

    result = dilute_from_mask(condensed, labels, radius_px=3)

    # Sanity: without a halo these B pixels would be dilute (in B, not
    # condensed). The cross-border dilation removes them.
    assert not result[5, 10]  # B, distance 2 from the blob → covered → 0
    assert not result[5, 11]  # B, distance 3 from the blob → covered → 0

    # Far inside B (distance 10) is untouched → still dilute.
    assert result[5, 18]

    # The gap column itself is background → 0 regardless.
    assert not result[5, 9]

    # Cross-check against a per-cell-contained dilation: containing the
    # dilation to cell A would leave B's near-border pixels dilute. Our
    # global op must DIFFER from that, proving the halo is real.
    dilated_global = dilation(condensed, footprint=disk(3))
    contained = dilation(condensed & (labels == 1), footprint=disk(3)) & (labels == 1)
    per_cell_result = (labels > 0) & ~(contained | (condensed & (labels != 1)))
    assert per_cell_result[5, 10]  # contained variant keeps B dilute here
    assert not result[5, 10]       # global variant removes it
    # And the two results genuinely differ.
    assert not np.array_equal(result, per_cell_result)
    # The global dilation does reach into B (documents the mechanism).
    assert dilated_global[5, 11]


# ── shape mismatch ─────────────────────────────────────────────────────


def test_shape_mismatch_raises_value_error():
    """A mask/seg shape mismatch raises a clear ValueError, not a numpy
    broadcast error."""
    condensed = np.zeros((10, 10), dtype=bool)
    labels = np.zeros((10, 12), dtype=np.int32)

    with pytest.raises(ValueError, match="share shape"):
        dilute_from_mask(condensed, labels, radius_px=2)


# ── dtype handling ─────────────────────────────────────────────────────


def test_uint8_0_255_mask_treated_as_boolean():
    """A 0/255 uint8 condensed mask is treated as boolean (> 0); the
    output is bool with shape == input shape."""
    labels = _two_cell_labels()
    condensed = np.zeros((20, 20), dtype=np.uint8)
    condensed[7:10, 4:7] = 255  # foreground encoded as 255

    result = dilute_from_mask(condensed, labels, radius_px=2)

    # Same as the boolean-encoded equivalent.
    bool_equiv = dilute_from_mask(condensed > 0, labels, radius_px=2)
    np.testing.assert_array_equal(result, bool_equiv)
    assert result.dtype == np.bool_
    assert result.shape == condensed.shape


def test_dilate_mask_returns_bool_and_no_growth_at_zero():
    """dilate_mask returns bool; radius 0 leaves the mask unchanged;
    radius > 0 grows it."""
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[5, 5] = 1

    unchanged = dilate_mask(mask, 0)
    assert unchanged.dtype == np.bool_
    np.testing.assert_array_equal(unchanged, mask.astype(bool))

    grown = dilate_mask(mask, 2)
    assert grown.dtype == np.bool_
    assert grown.sum() > mask.sum()  # the disk grew the single pixel
    np.testing.assert_array_equal(grown, dilation(mask.astype(bool), footprint=disk(2)))


# ── stack convenience ──────────────────────────────────────────────────


def test_dilute_from_mask_stack_per_frame_and_empty_plane():
    """(T, H, W) inputs → (T, H, W) bool output, per-frame correct; a
    frame whose label plane is empty yields an all-zero plane (exact-T)."""
    labels2d = _two_cell_labels()
    h, w = labels2d.shape
    t = 3

    seg = np.stack([labels2d, labels2d, np.zeros_like(labels2d)], axis=0)
    condensed = np.zeros((t, h, w), dtype=bool)
    condensed[0, 7:10, 4:7] = True  # blob only in frame 0

    out = dilute_from_mask_stack(condensed, seg, radius_px=2)

    assert out.shape == (t, h, w)
    assert out.dtype == np.bool_

    # Frame 0 matches the 2D op on frame 0's inputs.
    np.testing.assert_array_equal(
        out[0], dilute_from_mask(condensed[0], seg[0], radius_px=2)
    )
    # Frame 1 (no condensed) → whole cell interiors are dilute.
    np.testing.assert_array_equal(out[1], labels2d > 0)
    # Frame 2 (empty label plane) → all-zero plane, never dropped.
    assert not out[2].any()


def test_dilute_from_mask_stack_frame_count_mismatch_raises():
    """A mismatched frame count between the two stacks raises ValueError."""
    condensed = np.zeros((3, 8, 8), dtype=bool)
    seg = np.zeros((2, 8, 8), dtype=np.int32)
    with pytest.raises(ValueError, match="number of frames"):
        dilute_from_mask_stack(condensed, seg, radius_px=1)


# ── domain isolation ───────────────────────────────────────────────────


def test_pure_domain_no_forbidden_imports():
    """Domain isolation: dilute_mask must not import qtpy, PyQt5, napari,
    h5py, or percell4.application."""
    import percell4.domain.segmentation.dilute_mask as mod

    src = mod.__file__
    assert src is not None
    with open(src, encoding="utf-8") as f:
        text = f.read()
    for token in (
        "import qtpy", "from qtpy",
        "import PyQt5", "from PyQt5",
        "import napari", "from napari",
        "import h5py", "from h5py",
        "from percell4.application", "import percell4.application",
    ):
        assert token not in text, f"forbidden import found: {token}"
