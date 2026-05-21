"""Tests for file readers."""

from __future__ import annotations

import numpy as np
import pytest
import tifffile

from percell4.adapters.readers import (
    read_flim_bin,
    read_tiff,
    read_tiff_metadata,
)


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


# ── pixel_size_um decoding ────────────────────────────────────


def test_read_tiff_pixel_size_um_centimeter_unit(tmp_path):
    """ResolutionUnit=3 (cm) → pixel_size_um = (den/num) × 10000."""
    data = np.zeros((16, 16), dtype=np.uint16)
    path = tmp_path / "px_cm.tif"
    # 5538 pixels/cm in both axes ⇒ ~1.8056 µm/px.
    tifffile.imwrite(
        str(path), data,
        resolution=(5538.0, 5538.0), resolutionunit="CENTIMETER",
    )

    meta = read_tiff_metadata(path)
    assert "pixel_size_um" in meta
    # 1 cm / 5538 px = 1.8056e-4 cm/px = 1.8056 µm/px
    assert meta["pixel_size_um"] == pytest.approx(1.8056, rel=1e-3)


def test_read_tiff_pixel_size_um_inch_unit(tmp_path):
    """ResolutionUnit=2 (inch) → pixel_size_um = (den/num) × 25400."""
    data = np.zeros((16, 16), dtype=np.uint16)
    path = tmp_path / "px_in.tif"
    # 300 dpi ⇒ 25400/300 = 84.667 µm/px.
    tifffile.imwrite(
        str(path), data,
        resolution=(300.0, 300.0), resolutionunit="INCH",
    )

    meta = read_tiff_metadata(path)
    assert meta["pixel_size_um"] == pytest.approx(25400.0 / 300.0, rel=1e-3)


def test_read_tiff_pixel_size_um_no_unit_returns_none(tmp_path):
    """ResolutionUnit=1 (no unit) → pixel_size_um is omitted (can't convert)."""
    data = np.zeros((16, 16), dtype=np.uint16)
    path = tmp_path / "px_none.tif"
    tifffile.imwrite(
        str(path), data,
        resolution=(100.0, 100.0), resolutionunit="NONE",
    )

    meta = read_tiff_metadata(path)
    assert "pixel_size_um" not in meta


def test_read_tiff_pixel_size_um_missing_resolution_returns_none(tmp_path):
    """No resolution tag → pixel_size_um is omitted."""
    data = np.zeros((16, 16), dtype=np.uint16)
    path = tmp_path / "px_missing.tif"
    tifffile.imwrite(str(path), data)  # no resolution kwarg

    meta = read_tiff_metadata(path)
    assert "pixel_size_um" not in meta


def test_read_tiff_pixel_size_um_imagej_style_rational(tmp_path):
    """ImageJ-style encoding (large numerator, small denominator with
    cm unit) decodes correctly. Mirrors the form the user's microscope
    writes: XResolution = (4294967295, 77546), ResolutionUnit = cm."""
    data = np.zeros((16, 16), dtype=np.uint16)
    path = tmp_path / "px_imagej.tif"
    tifffile.imwrite(
        str(path), data,
        resolution=(4294967295.0 / 77546.0, 4294967295.0 / 77546.0),
        resolutionunit="CENTIMETER",
    )

    meta = read_tiff_metadata(path)
    # 4294967295/77546 ≈ 55382 px/cm ⇒ 77546/4294967295 cm/px
    # × 10000 µm/cm ≈ 0.1806 µm/px
    assert meta["pixel_size_um"] == pytest.approx(0.1806, rel=1e-3)


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
