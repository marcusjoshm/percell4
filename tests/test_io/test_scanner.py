"""Tests for FileScanner and token parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from percell4.io.models import TokenConfig
from percell4.io.scanner import FileScanner


@pytest.fixture
def tiff_dir(tmp_path):
    """Create a directory with synthetic TIFF files."""
    files = [
        "image_ch00_s00_z00.tif",
        "image_ch00_s00_z01.tif",
        "image_ch00_s01_z00.tif",
        "image_ch00_s01_z01.tif",
        "image_ch01_s00_z00.tif",
        "image_ch01_s00_z01.tif",
        "image_ch01_s01_z00.tif",
        "image_ch01_s01_z01.tif",
    ]
    for f in files:
        (tmp_path / f).write_bytes(b"fake tiff")
    return tmp_path


def test_scan_finds_tiffs(tiff_dir):
    """Scanner discovers all TIFF files."""
    scanner = FileScanner()
    result = scanner.scan(path=tiff_dir)
    assert len(result.files) == 8


def test_scan_parses_channels(tiff_dir):
    """Scanner extracts unique channel tokens."""
    scanner = FileScanner()
    result = scanner.scan(path=tiff_dir)
    assert result.channels == {"00", "01"}


def test_scan_parses_z_slices(tiff_dir):
    """Scanner extracts unique z-slice tokens."""
    scanner = FileScanner()
    result = scanner.scan(path=tiff_dir)
    assert result.z_slices == {"00", "01"}


def test_scan_parses_tiles(tiff_dir):
    """Scanner extracts unique tile tokens."""
    scanner = FileScanner()
    result = scanner.scan(path=tiff_dir)
    assert result.tiles == {"00", "01"}


def test_scan_with_explicit_files(tiff_dir):
    """Scanner works with an explicit file list."""
    files = list(tiff_dir.glob("*ch00*.tif"))
    scanner = FileScanner()
    result = scanner.scan(files=files)
    assert len(result.files) == 4
    assert result.channels == {"00"}


def test_scan_real_style_filename(tmp_path):
    """Scanner handles realistic microscope filenames."""
    name = "30min_Recovery_+_VCPi_Merged_ch00_z00.tif"
    (tmp_path / name).write_bytes(b"fake")
    scanner = FileScanner()
    result = scanner.scan(path=tmp_path)
    assert len(result.files) == 1
    tokens = result.files[0].tokens
    assert tokens["channel"] == "00"
    assert tokens["z_slice"] == "00"


def test_scan_custom_token_config(tmp_path):
    """Scanner uses custom regex patterns."""
    (tmp_path / "data_C1_T003.tiff").write_bytes(b"fake")
    config = TokenConfig(
        channel=r"_C(\d+)",
        timepoint=r"_T(\d+)",
        z_slice=None,
        tile=None,
    )
    scanner = FileScanner(config)
    result = scanner.scan(path=tmp_path)
    assert len(result.files) == 1
    tokens = result.files[0].tokens
    assert tokens["channel"] == "1"
    assert tokens["timepoint"] == "003"
    assert "z_slice" not in tokens


def test_scan_ignores_non_tiff(tmp_path):
    """Scanner skips non-TIFF files."""
    (tmp_path / "image_ch00.tif").write_bytes(b"fake")
    (tmp_path / "notes.txt").write_text("not an image")
    (tmp_path / "data.csv").write_text("a,b,c")
    scanner = FileScanner()
    result = scanner.scan(path=tmp_path)
    assert len(result.files) == 1


def test_scan_no_tokens(tmp_path):
    """Files without matching tokens get empty token dict."""
    (tmp_path / "plain_image.tif").write_bytes(b"fake")
    scanner = FileScanner()
    result = scanner.scan(path=tmp_path)
    assert result.files[0].tokens == {}


def test_scan_requires_path_or_files():
    """Scanner raises if neither path nor files provided."""
    scanner = FileScanner()
    with pytest.raises(ValueError, match="Must provide"):
        scanner.scan()


def test_token_config_validates_regex():
    """Invalid regex in TokenConfig raises ValueError."""
    with pytest.raises(ValueError, match="invalid regex"):
        TokenConfig(channel=r"_ch[bad")


def test_token_config_requires_capture_group():
    """Pattern without capture group raises ValueError."""
    with pytest.raises(ValueError, match="capture group"):
        TokenConfig(channel=r"_ch\d+")
