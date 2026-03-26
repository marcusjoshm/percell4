"""Tests for ROI import functions."""

from __future__ import annotations

import numpy as np
import pytest

from percell4.segment.roi_import import import_cellpose_seg


def test_import_cellpose_seg(tmp_path):
    """Load a synthetic _seg.npy file."""
    masks = np.zeros((64, 64), dtype=np.int32)
    masks[10:30, 10:30] = 1
    masks[40:60, 40:60] = 2

    seg_path = tmp_path / "test_seg.npy"
    np.save(str(seg_path), {"masks": masks})

    result = import_cellpose_seg(seg_path)
    assert result.dtype == np.int32
    assert result.shape == (64, 64)
    np.testing.assert_array_equal(result, masks)


def test_import_cellpose_seg_missing_key(tmp_path):
    """_seg.npy without 'masks' key raises KeyError."""
    seg_path = tmp_path / "bad_seg.npy"
    np.save(str(seg_path), {"something_else": np.zeros(10)})

    with pytest.raises(KeyError, match="masks"):
        import_cellpose_seg(seg_path)


def test_import_cellpose_seg_wrong_type(tmp_path):
    """_seg.npy that's not a dict raises ValueError."""
    seg_path = tmp_path / "notdict_seg.npy"
    np.save(str(seg_path), np.zeros(10))

    with pytest.raises(ValueError, match="Expected dict"):
        import_cellpose_seg(seg_path)
