"""Regression tests for ``_load_and_stitch`` silent data loss.

The pre-fix branch returned ``files[0]`` and discarded the rest when
multiple files arrived without a ``tile_config``. That silently turned
multi-tile datasets into single-tile datasets in the .h5 — a six-tile
scan would land as one tile, with no warning or metadata.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest
import tifffile

from percell4.adapters.importer import _load_and_stitch, write_decay_streaming
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


# ─────────────────────────────────────────────────────────────────────────
# write_decay_streaming — offset-aware (registered) placement (U5)
#
# The patterned reader embeds the tile index into every photon of the
# decay tile, so we can assert which tile "won" each overlap pixel by
# reading the stored value. read_flim_bin is imported lazily inside
# write_decay_streaming via ``from percell4.adapters.readers import
# read_flim_bin`` — patch that source module so the streamer picks up the
# fake.
# ─────────────────────────────────────────────────────────────────────────

_TILE_H = 8
_TILE_W = 8
_N_BINS = 4


@pytest.fixture
def patched_flim_reader(monkeypatch):
    """Reader that stamps the ``_s<idx>`` tile index into the whole tile.

    Every photon of tile ``i`` holds the value ``i + 1`` (so tile 0 is
    distinguishable from the HDF5 fill of 0), letting overlap-winner
    assertions read the value back directly.
    """
    import re as _re

    def _fake_read(path, **kwargs):
        m = _re.search(r"_s(\d+)", str(path))
        idx = int(m.group(1)) if m else 0
        arr = np.full((_TILE_H, _TILE_W, _N_BINS), idx + 1, dtype=np.uint16)
        return {
            "array": arr,
            "intensity": arr[..., 0].astype(np.float32),
            "metadata": {"shape": (_TILE_H, _TILE_W, _N_BINS)},
        }

    monkeypatch.setattr("percell4.adapters.readers.read_flim_bin", _fake_read)
    return _fake_read


def _make_bins(source: Path, n: int) -> dict[int, Path]:
    """Create ``n`` placeholder .bin files named ``tile_s<i>.bin``."""
    source.mkdir(parents=True, exist_ok=True)
    bins: dict[int, Path] = {}
    for i in range(n):
        p = source / f"tile_s{i}.bin"
        p.write_bytes(b"\0")
        bins[i] = p
    return bins


def test_decay_offset_overlap_higher_index_wins(tmp_path, patched_flim_reader):
    """AE1: two overlapping decay tiles placed via pixel_offsets — overlap
    pixels hold the higher-index tile's photons (ascending-index priority).
    """
    h5 = tmp_path / "decay.h5"
    bins = _make_bins(tmp_path / "bin", 2)
    # Tile 0 at (0,0); tile 1 at (0,4) — they overlap in cols [4..8).
    pixel_offsets = {0: (0, 0), 1: (0, 4)}

    write_decay_streaming(
        h5_path=h5,
        channel_name="ch00",
        tile_bins=bins,
        bin_dims={"x_dim": _TILE_W, "y_dim": _TILE_H, "t_dim": _N_BINS,
                  "dtype": "uint16", "dim_order": "YXT"},
        tile_h=_TILE_H,
        tile_w=_TILE_W,
        n_bins=_N_BINS,
        out_h=0,  # ignored on the offset path (canvas_from_offsets drives it)
        out_w=0,
        positions={},
        use_tiling=True,
        pixel_offsets=pixel_offsets,
    )

    with h5py.File(h5, "r") as f:
        decay = f["decay/ch00"][...]

    # canvas = max(y0+h)=8 by max(x0+w)=12
    assert decay.shape == (8, 12, _N_BINS)
    # Tile 0 (value 1) occupies cols [0,4) exclusively.
    assert np.all(decay[:, 0:4, 0] == 1)
    # Overlap cols [4,8): tile 1 (value 2) wins because it has the higher index.
    assert np.all(decay[:, 4:8, 0] == 2)
    # Tile 1's exclusive cols [8,12).
    assert np.all(decay[:, 8:12, 0] == 2)


def test_decay_offset_disconnected_demoted(tmp_path, patched_flim_reader):
    """A disconnected decay tile does not win the overlap even with a higher
    index — disconnected tiles are placed first (lowest priority).
    """
    h5 = tmp_path / "decay.h5"
    bins = _make_bins(tmp_path / "bin", 2)
    # Same overlap as above, but tile 1 (higher index) is disconnected, so the
    # registered tile 0 must win the overlap region.
    pixel_offsets = {0: (0, 0), 1: (0, 4)}

    write_decay_streaming(
        h5_path=h5,
        channel_name="ch00",
        tile_bins=bins,
        bin_dims={"x_dim": _TILE_W, "y_dim": _TILE_H, "t_dim": _N_BINS,
                  "dtype": "uint16", "dim_order": "YXT"},
        tile_h=_TILE_H,
        tile_w=_TILE_W,
        n_bins=_N_BINS,
        out_h=0,
        out_w=0,
        positions={},
        use_tiling=True,
        pixel_offsets=pixel_offsets,
        disconnected=(1,),
    )

    with h5py.File(h5, "r") as f:
        decay = f["decay/ch00"][...]

    # Overlap cols [4,8): registered tile 0 (value 1) wins; disconnected
    # tile 1 was placed first and overwritten there.
    assert np.all(decay[:, 4:8, 0] == 1)
    # Tile 1's exclusive cols [8,12) still come from tile 1 (value 2).
    assert np.all(decay[:, 8:12, 0] == 2)


def test_decay_offset_path_invalidates_phasor(tmp_path, patched_flim_reader):
    """A /phasor/<ch> present before an offset-path write is gone after."""
    h5 = tmp_path / "decay.h5"
    bins = _make_bins(tmp_path / "bin", 2)
    pixel_offsets = {0: (0, 0), 1: (0, 4)}

    # Pre-seed a stale /phasor/ch00.
    with h5py.File(h5, "a") as f:
        f.create_dataset("phasor/ch00", data=np.zeros((4, 4), dtype=np.float32))

    write_decay_streaming(
        h5_path=h5,
        channel_name="ch00",
        tile_bins=bins,
        bin_dims={"x_dim": _TILE_W, "y_dim": _TILE_H, "t_dim": _N_BINS,
                  "dtype": "uint16", "dim_order": "YXT"},
        tile_h=_TILE_H,
        tile_w=_TILE_W,
        n_bins=_N_BINS,
        out_h=0,
        out_w=0,
        positions={},
        use_tiling=True,
        pixel_offsets=pixel_offsets,
    )

    with h5py.File(h5, "r") as f:
        assert "phasor/ch00" not in f, "offset path must invalidate stale phasor"
        assert "decay/ch00" in f


def test_decay_grid_path_byte_identical_to_offset_none(tmp_path, patched_flim_reader):
    """AE3 golden: pixel_offsets=None produces output byte-identical to the
    current grid path. We capture the grid-path output (the load-bearing
    arithmetic ``y0=row*tile_h``) and assert it is unchanged.
    """
    bins = _make_bins(tmp_path / "bin", 4)
    # 2x2 grid, raster (top_left + row_by_row): positions index→(row,col).
    positions = {0: (0, 0), 1: (0, 1), 2: (1, 0), 3: (1, 1)}
    out_h, out_w = 2 * _TILE_H, 2 * _TILE_W

    common = dict(
        channel_name="ch00",
        tile_bins=bins,
        bin_dims={"x_dim": _TILE_W, "y_dim": _TILE_H, "t_dim": _N_BINS,
                  "dtype": "uint16", "dim_order": "YXT"},
        tile_h=_TILE_H,
        tile_w=_TILE_W,
        n_bins=_N_BINS,
        out_h=out_h,
        out_w=out_w,
        positions=positions,
        use_tiling=True,
    )

    h5_grid = tmp_path / "grid.h5"
    write_decay_streaming(h5_path=h5_grid, **common)
    with h5py.File(h5_grid, "r") as f:
        grid_decay = f["decay/ch00"][...]

    # The golden values: tile i (value i+1) sits at its grid block.
    assert grid_decay.shape == (out_h, out_w, _N_BINS)
    assert np.all(grid_decay[0:8, 0:8, 0] == 1)    # tile 0 → (0,0)
    assert np.all(grid_decay[0:8, 8:16, 0] == 2)   # tile 1 → (0,1)
    assert np.all(grid_decay[8:16, 0:8, 0] == 3)   # tile 2 → (1,0)
    assert np.all(grid_decay[8:16, 8:16, 0] == 4)  # tile 3 → (1,1)

    # Equivalent absolute offsets must reproduce the grid path byte-for-byte.
    h5_off = tmp_path / "off.h5"
    offsets = {0: (0, 0), 1: (0, 8), 2: (8, 0), 3: (8, 8)}
    write_decay_streaming(
        h5_path=h5_off,
        channel_name="ch00",
        tile_bins=bins,
        bin_dims=common["bin_dims"],
        tile_h=_TILE_H,
        tile_w=_TILE_W,
        n_bins=_N_BINS,
        out_h=0,
        out_w=0,
        positions={},
        use_tiling=True,
        pixel_offsets=offsets,
    )
    with h5py.File(h5_off, "r") as f:
        off_decay = f["decay/ch00"][...]

    assert np.array_equal(grid_decay, off_decay)


def test_decay_single_tile_fast_path_unchanged(tmp_path, patched_flim_reader):
    """use_tiling=False (single-tile fast path) is untouched by the new
    parameters — the whole canvas is the one tile, written via ``[:, :, :]``.
    """
    h5 = tmp_path / "single.h5"
    bins = _make_bins(tmp_path / "bin", 1)  # one tile, _s0 → value 1

    write_decay_streaming(
        h5_path=h5,
        channel_name="ch00",
        tile_bins=bins,
        bin_dims={"x_dim": _TILE_W, "y_dim": _TILE_H, "t_dim": _N_BINS,
                  "dtype": "uint16", "dim_order": "YXT"},
        tile_h=_TILE_H,
        tile_w=_TILE_W,
        n_bins=_N_BINS,
        out_h=_TILE_H,
        out_w=_TILE_W,
        positions={0: (0, 0)},
        use_tiling=False,
    )

    with h5py.File(h5, "r") as f:
        decay = f["decay/ch00"][...]

    assert decay.shape == (_TILE_H, _TILE_W, _N_BINS)
    assert np.all(decay[..., 0] == 1)
