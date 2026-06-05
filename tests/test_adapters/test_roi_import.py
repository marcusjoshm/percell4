"""Cellpose _seg.npy import rank policy (U7)."""

from __future__ import annotations

import numpy as np
import pytest

from percell4.adapters.roi_import import import_cellpose_seg


def _write_seg(path, masks):
    """Write a Cellpose-style _seg.npy (a dict wrapped in a 0-d object array)."""
    np.save(str(path), {"masks": masks}, allow_pickle=True)


def test_import_cellpose_2d_returns_2d(tmp_path):
    p = tmp_path / "a_seg.npy"
    masks = np.zeros((8, 8), dtype=np.int32)
    masks[2, 2] = 1
    _write_seg(p, masks)
    out = import_cellpose_seg(p)
    assert out.shape == (8, 8)
    assert out.dtype == np.int32
    assert out[2, 2] == 1


def test_import_cellpose_3d_stack_preserves_rank(tmp_path):
    """A per-frame (T,H,W) Cellpose stack is preserved (the caller validates the
    leading dim against n_timepoints)."""
    p = tmp_path / "b_seg.npy"
    masks = np.zeros((3, 8, 8), dtype=np.int32)
    masks[1, 4, 4] = 7
    _write_seg(p, masks)
    out = import_cellpose_seg(p)
    assert out.shape == (3, 8, 8)
    assert out[1, 4, 4] == 7


def test_import_cellpose_rejects_4d(tmp_path):
    p = tmp_path / "c_seg.npy"
    _write_seg(p, np.zeros((2, 3, 8, 8), dtype=np.int32))
    with pytest.raises(ValueError, match="2D .H,W. or 3D"):
        import_cellpose_seg(p)
