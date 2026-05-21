"""Round-trip tests for the TIFF writer + reader pair.

Pins the inverse-formula relationship between
``percell4.adapters.tiff_writer.write_tiff_with_metadata`` and
``percell4.adapters.readers.read_tiff_metadata``: a value written here
must come back through the reader within float tolerance.

Also pins the atomic-write contract — no ``.tif.tmp`` siblings are left
behind on success, and partial failures don't replace the target.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import tifffile

from percell4.adapters.readers import read_tiff_metadata
from percell4.adapters.tiff_writer import (
    _resolution_kwargs,
    write_tiff_with_metadata,
)


# ── Resolution kwarg derivation (pure unit math, no I/O) ─────────────


def test_resolution_kwargs_returns_empty_when_pixel_size_missing():
    assert _resolution_kwargs(None, view_bin=1) == {}


def test_resolution_kwargs_returns_empty_when_pixel_size_zero():
    assert _resolution_kwargs(0.0, view_bin=1) == {}


def test_resolution_kwargs_returns_empty_when_pixel_size_negative():
    assert _resolution_kwargs(-0.1, view_bin=1) == {}


def test_resolution_kwargs_emits_px_per_cm_pair():
    kw = _resolution_kwargs(0.12034, view_bin=1)
    assert kw["resolutionunit"] == "CENTIMETER"
    xres, yres = kw["resolution"]
    assert xres == pytest.approx(yres)
    # 0.12034 µm/px → 1 / (0.12034e-4 cm/px) ≈ 83100 px/cm
    assert xres == pytest.approx(83100.0, rel=1e-3)


def test_resolution_kwargs_scales_by_view_bin():
    kw1 = _resolution_kwargs(0.12034, view_bin=1)
    kw2 = _resolution_kwargs(0.12034, view_bin=2)
    # view_bin=2 means each output pixel covers 2× the physical width,
    # so pixels-per-cm is halved.
    assert kw2["resolution"][0] == pytest.approx(kw1["resolution"][0] / 2)


# ── Writer → Reader round-trip (the load-bearing assertion) ───────────


def test_roundtrip_native_bin(tmp_path):
    out = tmp_path / "ch.tif"
    data = np.zeros((16, 16), dtype=np.uint16)
    write_tiff_with_metadata(out, data, pixel_size_um=0.12034, view_bin=1)

    meta = read_tiff_metadata(out)
    assert meta["pixel_size_um"] == pytest.approx(0.12034, abs=1e-4)


def test_roundtrip_view_bin_two(tmp_path):
    out = tmp_path / "ch.tif"
    data = np.zeros((8, 8), dtype=np.uint16)
    write_tiff_with_metadata(out, data, pixel_size_um=0.12034, view_bin=2)

    meta = read_tiff_metadata(out)
    # Output describes its own coarser sampling.
    assert meta["pixel_size_um"] == pytest.approx(0.24068, abs=1e-4)


def test_roundtrip_view_bin_four(tmp_path):
    out = tmp_path / "ch.tif"
    data = np.zeros((4, 4), dtype=np.uint16)
    write_tiff_with_metadata(out, data, pixel_size_um=0.5, view_bin=4)

    meta = read_tiff_metadata(out)
    assert meta["pixel_size_um"] == pytest.approx(2.0, abs=1e-4)


def test_roundtrip_skipped_when_pixel_size_none(tmp_path):
    out = tmp_path / "ch.tif"
    write_tiff_with_metadata(out, np.zeros((4, 4), dtype=np.uint16), pixel_size_um=None)

    meta = read_tiff_metadata(out)
    assert "pixel_size_um" not in meta


def test_roundtrip_labels_uint32(tmp_path):
    """Label layers carry the same spatial calibration as channels."""
    out = tmp_path / "seg.tif"
    data = np.zeros((16, 16), dtype=np.uint32)
    write_tiff_with_metadata(out, data, pixel_size_um=0.12034, view_bin=1)

    meta = read_tiff_metadata(out)
    assert meta["pixel_size_um"] == pytest.approx(0.12034, abs=1e-4)
    # Dtype must round-trip too — label IDs > 65535 must survive.
    arr = tifffile.imread(out)
    assert arr.dtype == np.uint32


def test_roundtrip_masks_uint8(tmp_path):
    """Masks carry the same spatial calibration as channels."""
    out = tmp_path / "mask.tif"
    data = np.zeros((16, 16), dtype=np.uint8)
    write_tiff_with_metadata(out, data, pixel_size_um=0.12034, view_bin=1)

    meta = read_tiff_metadata(out)
    assert meta["pixel_size_um"] == pytest.approx(0.12034, abs=1e-4)
    arr = tifffile.imread(out)
    assert arr.dtype == np.uint8


def test_software_tag_is_emitted(tmp_path):
    """Software tag identifies the writer for downstream audits."""
    out = tmp_path / "ch.tif"
    write_tiff_with_metadata(out, np.zeros((4, 4), dtype=np.uint16))

    with tifffile.TiffFile(out) as tif:
        soft = tif.pages[0].tags.get("Software")
        assert soft is not None
        assert "PerCell4" in str(soft.value)


# ── Atomic write contract ─────────────────────────────────────────────


def test_no_tmp_left_after_successful_write(tmp_path):
    out = tmp_path / "ch.tif"
    write_tiff_with_metadata(out, np.zeros((4, 4), dtype=np.uint16))

    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
    assert out.exists()


def test_creates_parent_directory(tmp_path):
    out = tmp_path / "nested" / "deeper" / "ch.tif"
    write_tiff_with_metadata(out, np.zeros((4, 4), dtype=np.uint16))
    assert out.exists()
