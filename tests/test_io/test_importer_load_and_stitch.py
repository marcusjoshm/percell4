"""Regression tests for ``_load_and_stitch`` silent data loss.

The pre-fix branch returned ``files[0]`` and discarded the rest when
multiple files arrived without a ``tile_config``. That silently turned
multi-tile datasets into single-tile datasets in the .h5 — a six-tile
scan would land as one tile, with no warning or metadata.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile

from percell4.adapters.importer import _load_and_stitch
from percell4.domain.io.models import DiscoveredFile


def _make_tile(path: Path, value: int, size: int = 32) -> DiscoveredFile:
    """Write a constant-valued TIFF and return its DiscoveredFile wrapper."""
    arr = np.full((size, size), value, dtype=np.uint16)
    tifffile.imwrite(str(path), arr)
    return DiscoveredFile(path=path, tokens={"channel": "00", "tile": str(value)})


def test_load_and_stitch_raises_on_multi_file_no_tile_config(tmp_path: Path) -> None:
    """Multiple files + no tile_config must raise, not silently drop tiles.

    The legacy behaviour returned ``files[0]`` and discarded files[1:] —
    a six-tile dataset became a single tile in the .h5 with no
    indication anything was lost.
    """
    files = [
        _make_tile(tmp_path / f"img_s0{i}_ch00.tif", value=i)
        for i in range(6)
    ]

    with pytest.raises(ValueError) as exc:
        _load_and_stitch(files, tile_config=None)

    # The error must name the file count so the user can spot the
    # ambiguity quickly.
    msg = str(exc.value)
    assert "6" in msg
    # Mention at least one of the offending basenames so the user can
    # locate the source in the run.
    assert any(f.path.name in msg for f in files)


def test_load_and_stitch_single_file_no_tile_config_returns_array(
    tmp_path: Path,
) -> None:
    """Single file is unambiguous — read and return as before."""
    f = _make_tile(tmp_path / "img_s00_ch00.tif", value=42)

    arr = _load_and_stitch([f], tile_config=None)

    assert arr.shape == (32, 32)
    assert int(arr[0, 0]) == 42
