"""Tests for file readers."""

from __future__ import annotations

import numpy as np
import pytest
import tifffile

from percell4.adapters.readers import read_flim_bin, read_tiff


def test_read_tiff(tmp_path):
    """Read a basic TIFF file."""
    data = np.random.rand(64, 64).astype(np.float32)
    path = tmp_path / "test.tif"
    tifffile.imwrite(str(path), data)

    result = read_tiff(path)
    np.testing.assert_array_almost_equal(result["array"], data)
    assert result["metadata"]["shape"] == (64, 64)


def test_read_tiff_uint16(tmp_path):
    """Read a uint16 TIFF preserving dtype."""
    data = np.full((32, 32), 1000, dtype=np.uint16)
    path = tmp_path / "test.tif"
    tifffile.imwrite(str(path), data)

    result = read_tiff(path)
    assert result["array"].dtype == np.uint16


# ── .bin reader ───────────────────────────────────────────────


def test_read_flim_bin_basic(tmp_path):
    """Read a synthetic .bin file with known dimensions."""
    data = np.arange(8 * 8 * 16, dtype=np.uint16).reshape(8, 8, 16)
    path = tmp_path / "test.bin"

    # Write in YXT order (default)
    data.tofile(str(path))

    result = read_flim_bin(
        path, x_dim=8, y_dim=8, t_dim=16, dim_order="YXT"
    )

    assert result["array"].shape == (8, 8, 16)
    assert result["intensity"].shape == (8, 8)
    assert result["metadata"]["n_time_bins"] == 16


def test_read_flim_bin_transposed(tmp_path):
    """Read a .bin file with TYX dimension order."""
    # Create data in TYX order
    data_tyx = np.arange(16 * 4 * 4, dtype=np.uint16).reshape(16, 4, 4)
    path = tmp_path / "test.bin"
    data_tyx.tofile(str(path))

    result = read_flim_bin(
        path, x_dim=4, y_dim=4, t_dim=16, dim_order="TYX"
    )

    # Output should be (H, W, T) = (4, 4, 16)
    assert result["array"].shape == (4, 4, 16)


def test_read_flim_bin_with_header(tmp_path):
    """Read a .bin file with a small header to skip."""
    header = b"\x00" * 100
    data = np.ones(4 * 4 * 8, dtype=np.uint16)
    path = tmp_path / "test.bin"

    with open(path, "wb") as f:
        f.write(header)
        f.write(data.tobytes())

    result = read_flim_bin(
        path, x_dim=4, y_dim=4, t_dim=8, header_bytes=100
    )
    assert result["array"].shape == (4, 4, 8)


def test_read_flim_bin_auto_header(tmp_path):
    """Auto-detect small header (<1000 bytes)."""
    header = b"\xff" * 50
    data = np.ones(4 * 4 * 8, dtype=np.uint16)
    path = tmp_path / "test.bin"

    with open(path, "wb") as f:
        f.write(header)
        f.write(data.tobytes())

    # header_bytes=0 triggers auto-detection
    result = read_flim_bin(path, x_dim=4, y_dim=4, t_dim=8)
    assert result["array"].shape == (4, 4, 8)
    assert result["metadata"]["header_bytes"] == 50


def test_read_flim_bin_wrong_size(tmp_path):
    """Mismatched dimensions raise ValueError."""
    data = np.ones(100, dtype=np.uint16)
    path = tmp_path / "test.bin"
    data.tofile(str(path))

    with pytest.raises(ValueError, match="Expected"):
        read_flim_bin(path, x_dim=8, y_dim=8, t_dim=16)


def test_read_flim_bin_intensity_sum(tmp_path):
    """Intensity is the sum over time bins."""
    # Each pixel has time bins [1, 1, 1, 1] → intensity = 4
    data = np.ones((4, 4, 4), dtype=np.uint16)
    path = tmp_path / "test.bin"
    data.tofile(str(path))

    result = read_flim_bin(path, x_dim=4, y_dim=4, t_dim=4, dim_order="YXT")
    np.testing.assert_array_equal(result["intensity"], np.full((4, 4), 4.0))
