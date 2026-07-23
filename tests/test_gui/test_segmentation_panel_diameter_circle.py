"""Tests for the Cellpose diameter reference circle (U2/U3).

Two halves:

* ``diameter_circle_bbox`` — the pure geometry helper, tested without Qt or
  napari. The clamping rules are the part most likely to be wrong.
* ``SegmentationPanel`` toggle lifecycle — the checkbox, the live resize, and
  (most importantly) that no ``_diameter_reference`` layer survives an untick
  or a dataset switch. Layer residue is the documented failure mode here; see
  docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md,
  where a regression test passed while the bug shipped because it asserted on
  internal state instead of the viewer.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.config import viewer_presets as vp
from percell4.gui.segmentation_panel import diameter_circle_bbox


# ══════════════════════════════════════════════════════════════
# U2 — pure geometry
# ══════════════════════════════════════════════════════════════


def _extents(bbox):
    """(y_extent, x_extent) of a bounding box."""
    ys, xs = bbox[:, 0], bbox[:, 1]
    return ys.max() - ys.min(), xs.max() - xs.min()


def test_bbox_sized_and_placed_bottom_left() -> None:
    """A 100 px circle on a 512² image is 100 px across, lower-left."""
    bbox = diameter_circle_bbox((512, 512), 100.0, 10.0)

    y_extent, x_extent = _extents(bbox)
    assert y_extent == pytest.approx(100.0)
    assert x_extent == pytest.approx(100.0)

    center_y = bbox[:, 0].mean()
    center_x = bbox[:, 1].mean()
    assert center_y > 256, "napari y grows downward — bottom is large y"
    assert center_x < 256


def test_bbox_scales_with_diameter_and_stays_in_frame() -> None:
    """Doubling the diameter doubles both extents; both fit the image."""
    small = diameter_circle_bbox((512, 512), 100.0, 10.0)
    large = diameter_circle_bbox((512, 512), 200.0, 10.0)

    assert _extents(large)[0] == pytest.approx(2 * _extents(small)[0])
    assert _extents(large)[1] == pytest.approx(2 * _extents(small)[1])

    for bbox in (small, large):
        assert bbox.min() >= 0.0
        assert bbox[:, 0].max() <= 512.0
        assert bbox[:, 1].max() <= 512.0


def test_zero_diameter_returns_none() -> None:
    """R6: 0 is the auto-detect sentinel — nothing to draw."""
    assert diameter_circle_bbox((512, 512), 0.0, 10.0) is None


def test_negative_diameter_returns_none() -> None:
    """Unreachable via the spinbox, but the helper is public."""
    assert diameter_circle_bbox((512, 512), -5.0, 10.0) is None


def test_diameter_equal_to_height_clamps_flush() -> None:
    """A full-height circle sits flush with no negative coordinates."""
    bbox = diameter_circle_bbox((512, 512), 512.0, 10.0)

    assert bbox[:, 0].min() == pytest.approx(0.0)
    assert bbox[:, 0].max() == pytest.approx(512.0)
    assert bbox.min() >= 0.0


def test_oversize_diameter_pins_flush_and_overflows() -> None:
    """1000 px on a 512² image overflows, but never off the top/left.

    The overflow is the point: a disc spilling past the image edge tells the
    user their diameter is far too large.
    """
    bbox = diameter_circle_bbox((512, 512), 1000.0, 10.0)

    assert bbox[:, 0].min() == pytest.approx(0.0)
    assert bbox[:, 1].min() == pytest.approx(0.0)
    assert _extents(bbox)[0] == pytest.approx(1000.0)


def test_non_square_image_stays_round() -> None:
    """The circle is round, not stretched to the image aspect ratio."""
    bbox = diameter_circle_bbox((256, 1024), 100.0, 10.0)

    y_extent, x_extent = _extents(bbox)
    assert y_extent == pytest.approx(x_extent)
    assert y_extent == pytest.approx(100.0)


def test_margin_larger_than_image_clamps() -> None:
    """An absurd margin clamps instead of inverting or leaving the image."""
    bbox = diameter_circle_bbox((100, 100), 20.0, 500.0)

    assert bbox.min() >= 0.0
    assert _extents(bbox)[0] == pytest.approx(20.0)
    assert _extents(bbox)[1] == pytest.approx(20.0)


def test_presets_are_opaque_magenta() -> None:
    """R4: the disc is opaque magenta, per the Cellpose GUI."""
    r, g, b, a = vp.DIAMETER_CIRCLE_FACE_COLOR
    assert (r, g, b) == (1.0, 0.0, 1.0)
    assert a == 1.0, "opaque — see the preset comment before changing this"


def test_helper_is_numpy_pure() -> None:
    """Returns a float array of four (y, x) vertices."""
    bbox = diameter_circle_bbox((512, 512), 100.0, 10.0)
    assert isinstance(bbox, np.ndarray)
    assert bbox.shape == (4, 2)
    assert bbox.dtype == float
