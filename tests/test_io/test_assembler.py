"""Tests for tile stitching and Z-projection."""

from __future__ import annotations

import numpy as np
import pytest

from percell4.io.assembler import assemble_channels, assemble_tiles, project_z


# ── Tile stitching ────────────────────────────────────────────


def test_stitch_2x2_row_by_row():
    """Stitch a 2x2 grid of 10x10 tiles in row-by-row order."""
    tiles = {
        0: np.full((10, 10), 1, dtype=np.uint16),
        1: np.full((10, 10), 2, dtype=np.uint16),
        2: np.full((10, 10), 3, dtype=np.uint16),
        3: np.full((10, 10), 4, dtype=np.uint16),
    }
    result = assemble_tiles(tiles, grid_rows=2, grid_cols=2)
    assert result.shape == (20, 20)
    # Row-by-row, right_down: tile 0 = top-left, tile 1 = top-right
    assert result[0, 0] == 1
    assert result[0, 10] == 2
    assert result[10, 0] == 3
    assert result[10, 10] == 4


def test_stitch_snake_by_row():
    """Snake by row reverses even/odd rows."""
    tiles = {
        0: np.full((5, 5), 0, dtype=np.uint16),
        1: np.full((5, 5), 1, dtype=np.uint16),
        2: np.full((5, 5), 2, dtype=np.uint16),
        3: np.full((5, 5), 3, dtype=np.uint16),
    }
    result = assemble_tiles(
        tiles, grid_rows=2, grid_cols=2, grid_type="snake_by_row"
    )
    # Row 0: 0,1 (left to right). Row 1: 3,2 (right to left).
    assert result[0, 0] == 0
    assert result[0, 5] == 1
    assert result[5, 0] == 3  # snake reversal
    assert result[5, 5] == 2


def test_stitch_empty_raises():
    """Stitching with no tiles raises ValueError."""
    with pytest.raises(ValueError, match="No tiles"):
        assemble_tiles({}, grid_rows=1, grid_cols=1)


def test_stitch_missing_tile():
    """Missing tiles are left as zeros."""
    tiles = {0: np.ones((10, 10), dtype=np.uint16)}
    result = assemble_tiles(tiles, grid_rows=1, grid_cols=2)
    assert result[0, 0] == 1
    assert result[0, 10] == 0  # missing tile 1


# ── Channel assembly ──────────────────────────────────────────


def test_assemble_channels():
    """Stack two channels into (C, H, W)."""
    ch1 = np.ones((10, 10), dtype=np.float32)
    ch2 = np.full((10, 10), 2.0, dtype=np.float32)
    result = assemble_channels([ch1, ch2])
    assert result.shape == (2, 10, 10)
    assert result[0, 0, 0] == 1.0
    assert result[1, 0, 0] == 2.0


def test_assemble_channels_shape_mismatch():
    """Mismatched spatial dimensions raise ValueError."""
    with pytest.raises(ValueError, match="different shapes"):
        assemble_channels([np.zeros((10, 10)), np.zeros((10, 20))])


# ── Z-projection ─────────────────────────────────────────────


def test_project_z_mip():
    """MIP takes the maximum across z-slices."""
    slices = [
        np.array([[1, 2], [3, 4]], dtype=np.uint16),
        np.array([[4, 3], [2, 1]], dtype=np.uint16),
    ]
    result = project_z(slices, method="mip")
    expected = np.array([[4, 3], [3, 4]], dtype=np.uint16)
    np.testing.assert_array_equal(result, expected)


def test_project_z_sum_uses_int64():
    """Sum projection uses int64 to prevent overflow."""
    # uint16 max is 65535; two slices of 40000 would overflow
    slices = [
        np.full((2, 2), 40000, dtype=np.uint16),
        np.full((2, 2), 40000, dtype=np.uint16),
    ]
    result = project_z(slices, method="sum")
    assert result.dtype == np.float32
    assert result[0, 0] == 80000.0


def test_project_z_mean():
    """Mean projection averages z-slices."""
    slices = [
        np.full((2, 2), 10.0, dtype=np.float32),
        np.full((2, 2), 20.0, dtype=np.float32),
    ]
    result = project_z(slices, method="mean")
    np.testing.assert_allclose(result, 15.0)


def test_project_z_unknown_method():
    """Unknown method raises ValueError."""
    with pytest.raises(ValueError, match="Unknown projection method"):
        project_z([np.zeros((2, 2))], method="nonexistent")


def test_project_z_single_slice():
    """Single z-slice returns a copy of that slice."""
    single = np.array([[5, 6], [7, 8]], dtype=np.uint16)
    result = project_z([single], method="mip")
    np.testing.assert_array_equal(result, single)


def test_project_z_streaming(tmp_path):
    """Streaming mode reads from disk one at a time."""
    import tifffile

    paths = []
    for i in range(3):
        p = tmp_path / f"z{i:02d}.tif"
        tifffile.imwrite(str(p), np.full((10, 10), i * 10, dtype=np.uint16))
        paths.append(str(p))

    def read_fn(path):
        return tifffile.imread(path)

    result = project_z(
        streaming_paths=paths, read_fn=read_fn, method="mip"
    )
    assert result.shape == (10, 10)
    assert result[0, 0] == 20  # max of 0, 10, 20
