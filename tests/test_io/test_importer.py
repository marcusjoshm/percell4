"""Integration tests for the import pipeline."""

from __future__ import annotations

import warnings

import numpy as np
import pytest
import tifffile

from percell4.adapters.importer import import_dataset
from percell4.domain.io.models import TileConfig, TokenConfig
from percell4.store import DatasetStore


def _create_tiff_dir(tmp_path, n_channels=2, n_z=1):
    """Create a directory of synthetic TIFF files with token-based names."""
    src = tmp_path / "raw"
    src.mkdir()
    for ch in range(n_channels):
        for z in range(n_z):
            name = f"image_ch{ch:02d}_z{z:02d}.tif"
            data = np.full((64, 64), (ch + 1) * 100 + z, dtype=np.uint16)
            tifffile.imwrite(str(src / name), data)
    return src


def test_import_single_channel(tmp_path):
    """Import a single-channel dataset (no z)."""
    src = _create_tiff_dir(tmp_path, n_channels=1, n_z=1)
    h5_path = tmp_path / "output.h5"

    n_ch = import_dataset(src, h5_path)

    assert n_ch == 1
    store = DatasetStore(h5_path)
    intensity = store.read_array("intensity")
    assert intensity.ndim == 2
    assert intensity.shape == (64, 64)


def test_import_multichannel(tmp_path):
    """Import a multi-channel dataset → (C, H, W) array."""
    src = _create_tiff_dir(tmp_path, n_channels=3, n_z=1)
    h5_path = tmp_path / "output.h5"

    n_ch = import_dataset(src, h5_path)

    assert n_ch == 3
    store = DatasetStore(h5_path)
    intensity = store.read_array("intensity")
    assert intensity.shape == (3, 64, 64)
    assert intensity.dtype == np.float32


def test_import_with_z_projection(tmp_path):
    """Import with z-slices, MIP projects to 2D per channel."""
    src = _create_tiff_dir(tmp_path, n_channels=1, n_z=3)
    h5_path = tmp_path / "output.h5"

    n_ch = import_dataset(src, h5_path, z_project_method="mip")

    assert n_ch == 1
    store = DatasetStore(h5_path)
    intensity = store.read_array("intensity")
    # MIP of z=0 (100), z=1 (101), z=2 (102) = 102
    assert intensity.ndim == 2
    assert intensity[0, 0] == 102.0


def test_import_stores_metadata(tmp_path):
    """Import stores source_dir and channel_names in metadata."""
    src = _create_tiff_dir(tmp_path, n_channels=2)
    h5_path = tmp_path / "output.h5"

    import_dataset(src, h5_path, metadata={"experiment": "test"})

    store = DatasetStore(h5_path)
    meta = store.metadata
    assert "source_dir" in meta
    assert meta["n_channels"] == 2
    assert meta["experiment"] == "test"


def test_import_updates_project_csv(tmp_path):
    """Import adds a row to project.csv."""
    src = _create_tiff_dir(tmp_path, n_channels=1)
    h5_path = tmp_path / "output.h5"
    csv_path = tmp_path / "project.csv"

    import_dataset(src, h5_path, project_csv=csv_path)

    import pandas as pd
    df = pd.read_csv(csv_path)
    assert len(df) == 1
    assert df.iloc[0]["status"] == "complete"


def test_import_with_custom_tokens(tmp_path):
    """Import with custom token patterns."""
    src = tmp_path / "raw"
    src.mkdir()
    for ch in range(2):
        name = f"experiment_C{ch}_T001.tif"
        tifffile.imwrite(
            str(src / name),
            np.full((32, 32), ch * 50, dtype=np.uint16),
        )

    h5_path = tmp_path / "output.h5"
    config = TokenConfig(
        channel=r"_C(\d+)",
        timepoint=r"_T(\d+)",
        z_slice=None,
        tile=None,
    )

    n_ch = import_dataset(src, h5_path, token_config=config)
    assert n_ch == 2


def test_import_empty_dir_raises(tmp_path):
    """Import from empty directory raises ValueError."""
    src = tmp_path / "empty"
    src.mkdir()
    h5_path = tmp_path / "output.h5"

    import pytest
    with pytest.raises(ValueError, match="No image files"):
        import_dataset(src, h5_path)


def test_import_bin_only_splits_channels_by_token(tmp_path):
    """Regression test: importing only .bin files (no TIFFs) must still
    split bins into per-channel buckets via their ``_ch(\\d+)`` tokens.

    Pre-U1 behavior parsed each bin's channel token directly when no
    TIFFs were present. The U1 refactor routed everything through
    ``match_bin_to_intensity``, which returned no bindings when the
    intensity-channel list was empty — collapsing every bin into a
    single ``ch0`` decay layer and visually breaking the phasor.
    """
    src = tmp_path / "bin_only"
    src.mkdir()
    # 2 channels × 4 tiles = 8 bin files. Tiny dimensions to keep the
    # test fast: 4×4×8 = 128 uint32 values per bin.
    h, w, t = 4, 4, 8
    for ch in range(2):
        for tile in range(4):
            arr = np.full((h, w, t), ch * 1000 + tile, dtype=np.uint32)
            (src / f"sample_s{tile:02d}_ch{ch:02d}.bin").write_bytes(
                arr.tobytes()
            )

    h5_path = tmp_path / "bin_only.h5"
    flim_params = {
        "frequency_mhz": 80.0,
        "channel_calibrations": {},
        "bin_dimensions": {
            "x_dim": w, "y_dim": h, "t_dim": t,
            "dtype": "uint32", "dim_order": "YXT", "header_bytes": 0,
        },
    }
    from percell4.domain.io.models import TileConfig
    n_ch = import_dataset(
        src,
        h5_path,
        flim_params=flim_params,
        tile_config=TileConfig(grid_rows=2, grid_cols=2),
    )

    # The store should have /decay/ch00 AND /decay/ch01 — NOT a single
    # collapsed /decay/ch0 (the regression's symptom).
    store = DatasetStore(h5_path)
    decay_groups = store.list_groups("decay")
    assert "ch00" in decay_groups, f"ch00 missing — got {decay_groups}"
    assert "ch01" in decay_groups, f"ch01 missing — got {decay_groups}"


# ── Time-lapse import (U1) ──────────────────────────────────────────────


def _create_timelapse_dir(tmp_path, n_channels=1, n_timepoints=3):
    """Create synthetic TIFFs with ``_t<NN>_ch<NN>`` tokens.

    Pixel value encodes (timepoint, channel) as t*1000 + ch so tests can
    assert frames landed in the right order.
    """
    src = tmp_path / "raw"
    src.mkdir()
    for t in range(n_timepoints):
        for ch in range(n_channels):
            name = f"movie_t{t:02d}_ch{ch:02d}.tif"
            data = np.full((16, 16), t * 1000 + ch, dtype=np.uint16)
            tifffile.imwrite(str(src / name), data)
    return src


def test_import_single_channel_timelapse_stacks_T(tmp_path):
    """A _tN single-channel series imports as one (T, H, W) /intensity."""
    src = _create_timelapse_dir(tmp_path, n_channels=1, n_timepoints=3)
    h5_path = tmp_path / "movie.h5"

    import_dataset(src, h5_path)

    store = DatasetStore(h5_path)
    intensity = store.read_array("intensity")
    assert intensity.shape == (3, 16, 16)
    assert store.metadata["n_timepoints"] == 3
    # Frames in numeric timepoint order: t0 plane == 0, t2 plane == 2000.
    assert intensity[0, 0, 0] == 0.0
    assert intensity[2, 0, 0] == 2000.0


def test_import_multichannel_timelapse_stacks_TCHW(tmp_path):
    """Two channels x three timepoints -> (T, C, H, W)."""
    src = _create_timelapse_dir(tmp_path, n_channels=2, n_timepoints=3)
    h5_path = tmp_path / "movie.h5"

    n_ch = import_dataset(src, h5_path)

    assert n_ch == 2
    store = DatasetStore(h5_path)
    intensity = store.read_array("intensity")
    assert intensity.shape == (3, 2, 16, 16)
    # (t=1, ch=1) plane encodes 1*1000 + 1 = 1001.
    assert intensity[1, 1, 0, 0] == 1001.0


def test_import_timepoints_numeric_order_not_lexical(tmp_path):
    """_t2 must precede _t10 — lexical order would place t10 first."""
    src = tmp_path / "raw"
    src.mkdir()
    for t in (1, 2, 10):
        data = np.full((8, 8), t, dtype=np.uint16)
        tifffile.imwrite(str(src / f"movie_t{t:02d}_ch00.tif"), data)

    h5_path = tmp_path / "movie.h5"
    import_dataset(src, h5_path)

    intensity = DatasetStore(h5_path).read_array("intensity")
    assert intensity.shape == (3, 8, 8)
    # Numeric order => planes are [t1, t2, t10] => values [1, 2, 10].
    assert [int(intensity[i, 0, 0]) for i in range(3)] == [1, 2, 10]


def test_import_single_timepoint_unchanged(tmp_path):
    """No _t token => no T axis; layout byte-identical to today, n_timepoints=1."""
    src = _create_tiff_dir(tmp_path, n_channels=1, n_z=1)
    h5_path = tmp_path / "output.h5"

    import_dataset(src, h5_path)

    store = DatasetStore(h5_path)
    intensity = store.read_array("intensity")
    assert intensity.ndim == 2
    assert intensity.shape == (64, 64)
    assert store.metadata["n_timepoints"] == 1


def test_import_timepoint_missing_channel_frame_raises(tmp_path):
    """A channel that doesn't cover every timepoint aborts cleanly."""
    import pytest

    from percell4.store import SourceShapeMismatchError

    src = tmp_path / "raw"
    src.mkdir()
    # ch00 has t0,t1,t2; ch01 only has t0,t1 -> mis-stack risk.
    for t in range(3):
        tifffile.imwrite(
            str(src / f"movie_t{t:02d}_ch00.tif"),
            np.full((8, 8), t, dtype=np.uint16),
        )
    for t in range(2):
        tifffile.imwrite(
            str(src / f"movie_t{t:02d}_ch01.tif"),
            np.full((8, 8), t, dtype=np.uint16),
        )

    h5_path = tmp_path / "movie.h5"
    with pytest.raises(SourceShapeMismatchError, match="timepoint"):
        import_dataset(src, h5_path)
    assert not h5_path.exists()


def test_import_timelapse_with_z_projection(tmp_path):
    """Each timepoint z-projects independently, then stacks on T."""
    src = tmp_path / "raw"
    src.mkdir()
    # 2 timepoints x 3 z-slices. MIP per timepoint picks the max z value.
    for t in range(2):
        for z in range(3):
            tifffile.imwrite(
                str(src / f"movie_t{t:02d}_ch00_z{z:02d}.tif"),
                np.full((8, 8), t * 100 + z, dtype=np.uint16),
            )

    h5_path = tmp_path / "movie.h5"
    import_dataset(src, h5_path, z_project_method="mip")

    intensity = DatasetStore(h5_path).read_array("intensity")
    assert intensity.shape == (2, 8, 8)
    # MIP over z: t0 -> max(0,1,2)=2; t1 -> max(100,101,102)=102.
    assert intensity[0, 0, 0] == 2.0
    assert intensity[1, 0, 0] == 102.0


def test_import_progress_callback(tmp_path):
    """Progress callback is called during import."""
    src = _create_tiff_dir(tmp_path, n_channels=1)
    h5_path = tmp_path / "output.h5"

    calls = []
    def on_progress(current, total, msg):
        calls.append((current, total, msg))

    import_dataset(src, h5_path, progress_callback=on_progress)

    assert len(calls) >= 2  # at least start and end
    assert calls[-1][0] == calls[-1][1]  # last call: current == total


# ── creation_bin (U7) ──────────────────────────────────────────────────


def test_import_creation_bin_default_one_writes_native_metadata(tmp_path):
    """Default creation_bin=1 still writes native_shape and creation_bin=1
    to /metadata. Array payloads byte-identical to pre-binning behavior."""
    src = _create_tiff_dir(tmp_path, n_channels=1, n_z=1)
    h5_path = tmp_path / "output.h5"

    import_dataset(src, h5_path)

    store = DatasetStore(h5_path)
    meta = store.metadata
    assert meta["native_shape"] == (64, 64)
    assert meta["creation_bin"] == 1
    # Array shape unchanged at k=1.
    assert store.read_array("intensity").shape == (64, 64)


def test_import_creation_bin_2_halves_shape(tmp_path):
    """creation_bin=2 sum-bins the 64x64 TIFF to a 32x32 stored intensity."""
    src = _create_tiff_dir(tmp_path, n_channels=1, n_z=1)
    h5_path = tmp_path / "output.h5"

    import_dataset(src, h5_path, creation_bin=2)

    store = DatasetStore(h5_path)
    meta = store.metadata
    assert meta["native_shape"] == (32, 32)
    assert meta["creation_bin"] == 2
    intensity = store.read_array("intensity")
    assert intensity.shape == (32, 32)
    # Each binned pixel is the sum of 4 source pixels (all == 100).
    np.testing.assert_allclose(intensity, np.full((32, 32), 400.0))


def test_import_creation_bin_multichannel(tmp_path):
    """creation_bin works on multichannel 3D /intensity (sum-bin per channel)."""
    src = _create_tiff_dir(tmp_path, n_channels=2, n_z=1)
    h5_path = tmp_path / "output.h5"

    import_dataset(src, h5_path, creation_bin=2)

    store = DatasetStore(h5_path)
    intensity = store.read_array("intensity")
    assert intensity.shape == (2, 32, 32)
    assert store.metadata["native_shape"] == (32, 32)


def test_import_creation_bin_zero_raises(tmp_path):
    """creation_bin < 1 is rejected before any work happens."""
    import pytest
    src = _create_tiff_dir(tmp_path, n_channels=1, n_z=1)
    h5_path = tmp_path / "output.h5"

    with pytest.raises(ValueError, match="creation_bin must be >= 1"):
        import_dataset(src, h5_path, creation_bin=0)

    # No file should have been written.
    assert not h5_path.exists()


def test_import_creation_bin_truncates_residual(tmp_path):
    """A source that isn't divisible by creation_bin truncates residual rows/cols."""
    # 7x7 TIFF, creation_bin=3 -> 2x2 stored intensity (last row+col dropped).
    src = tmp_path / "raw"
    src.mkdir()
    data = np.ones((7, 7), dtype=np.uint16)
    tifffile.imwrite(str(src / "image_ch00_z00.tif"), data)

    h5_path = tmp_path / "output.h5"
    import_dataset(src, h5_path, creation_bin=3)

    store = DatasetStore(h5_path)
    assert store.metadata["native_shape"] == (2, 2)
    assert store.read_array("intensity").shape == (2, 2)
    # Each binned pixel = 3*3 = 9 source ones summed.
    np.testing.assert_allclose(store.read_array("intensity"), np.full((2, 2), 9.0))


def test_import_source_shape_mismatch_raises(tmp_path):
    """When two TIFFs at the same level disagree on (H, W), abort cleanly."""
    import pytest

    from percell4.store import SourceShapeMismatchError

    src = tmp_path / "raw"
    src.mkdir()
    tifffile.imwrite(str(src / "image_ch00_z00.tif"), np.ones((32, 32), dtype=np.uint16))
    tifffile.imwrite(str(src / "image_ch01_z00.tif"), np.ones((40, 40), dtype=np.uint16))

    h5_path = tmp_path / "output.h5"
    with pytest.raises(SourceShapeMismatchError):
        import_dataset(src, h5_path)

    # No partial file written.
    assert not h5_path.exists()


# ── Overlap-aware (phase-correlation) registered stitching (U6) ─────────────
#
# These build small *textured* overlapping tiles (random-but-seeded + a smooth
# gradient) so phase correlation is well-conditioned and recovers an exact
# integer offset. The non-registered byte-identical path is guarded by every
# test above; these only exercise the registered branch.

_REG_TH = 60
_REG_TW = 60
_REG_OVERLAP = 0.25
_REG_STEP = int(_REG_TW * (1 - _REG_OVERLAP))  # 45
# 2x2 grid of 60px tiles stepping 45px → 105x105 canvas.
_REG_CANVAS = (_REG_TH + _REG_STEP, _REG_TW + _REG_STEP)  # (105, 105)
_REG_CORNERS = {
    0: (0, 0),
    1: (0, _REG_STEP),
    2: (_REG_STEP, 0),
    3: (_REG_STEP, _REG_STEP),
}


def _textured_scene(seed: int, h: int = 110, w: int = 110) -> np.ndarray:
    """Seeded random texture + smooth gradient → phase-correlation friendly."""
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 5000, size=(h, w))
    grad = np.add.outer(np.arange(h) * 7, np.arange(w) * 5)
    return (noise + grad).astype(np.uint16)


def _write_overlapping_tiles(
    src, scene: np.ndarray, *, channel: str = "00", make_bin: bool = False,
    t_bins: int = 4,
):
    """Write 2x2 overlapping TIFF tiles (and optionally stamped .bin decay).

    The decay .bin for tile ``i`` stamps the value ``i + 1`` into every
    photon so overlap-winner assertions can read which tile won a pixel.
    """
    src.mkdir(parents=True, exist_ok=True)
    for i, (y, x) in _REG_CORNERS.items():
        tile = scene[y : y + _REG_TH, x : x + _REG_TW]
        tifffile.imwrite(str(src / f"img_s{i:02d}_ch{channel}.tif"), tile)
        if make_bin:
            decay = np.full((_REG_TH, _REG_TW, t_bins), i + 1, dtype=np.uint32)
            (src / f"img_s{i:02d}_ch{channel}.bin").write_bytes(decay.tobytes())


def _reg_tile_config(**overrides) -> TileConfig:
    base = dict(
        grid_rows=2, grid_cols=2, grid_type="row_by_row", order="right_down",
        overlap=_REG_OVERLAP, register=True, reference_channel="ch00",
    )
    base.update(overrides)
    return TileConfig(**base)


def test_register_2x2_persists_offsets_and_canvas(tmp_path):
    """Happy path: a 2x2 overlapping import registers, persists offsets, and
    stores /intensity on canvas_from_offsets; native_shape == canvas (R1-R4).
    """
    src = tmp_path / "raw"
    _write_overlapping_tiles(src, _textured_scene(7))

    h5 = tmp_path / "out.h5"
    n = import_dataset(src, h5, tile_config=_reg_tile_config())

    assert n == 1
    store = DatasetStore(h5)
    geo = store.read_stitch_geometry()
    assert geo.registered is True
    # Exact integer offsets recovered (no overlap ambiguity at this texture).
    np.testing.assert_array_equal(
        geo.offsets,
        np.array([[0, 0], [0, 45], [45, 0], [45, 45]], dtype=np.int32),
    )
    assert geo.reference_channel == "ch00"
    assert geo.overlap == pytest.approx(_REG_OVERLAP)

    intensity = store.read_array("intensity")
    assert intensity.shape == _REG_CANVAS
    assert tuple(store.metadata["native_shape"]) == _REG_CANVAS


def test_register_provenance_recorded(tmp_path):
    """The /provenance/stitch record carries the library identity + quality."""
    import json

    import h5py

    src = tmp_path / "raw"
    _write_overlapping_tiles(src, _textured_scene(7))
    h5 = tmp_path / "out.h5"
    import_dataset(src, h5, tile_config=_reg_tile_config())

    with h5py.File(h5, "r") as f:
        attrs = dict(f["provenance/stitch"].attrs)
    assert "grid_stitching" in attrs["library"]
    assert attrs["reference_channel"] == "ch00"
    quality = json.loads(attrs["quality_json"])
    assert "coverage_fraction" in quality
    assert "regression_threshold" in quality
    assert "n_peaks" in quality


def test_register_intensity_decay_same_canvas_and_winner(tmp_path):
    """AE1: an overlap pixel resolves to the SAME tile in /intensity and in
    decay.sum(axis=-1); both share one registered canvas (R5/R7).
    """
    src = tmp_path / "raw"
    _write_overlapping_tiles(src, _textured_scene(11), make_bin=True)

    h5 = tmp_path / "out.h5"
    flim = {
        "frequency_mhz": 80.0,
        "channel_calibrations": {},
        "bin_dimensions": {
            "x_dim": _REG_TW, "y_dim": _REG_TH, "t_dim": 4,
            "dtype": "uint32", "dim_order": "YXT", "header_bytes": 0,
        },
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import_dataset(src, h5, tile_config=_reg_tile_config(), flim_params=flim)

    store = DatasetStore(h5)
    intensity = store.read_array("intensity")  # ch00 TIFF wins; .bin decay-only
    decay = store.read_array("decay/ch00")
    assert intensity.shape == _REG_CANVAS
    assert decay.shape[:2] == _REG_CANVAS
    winner = decay[..., 0]  # each photon stamped with tile_index + 1
    # Ascending-index priority: highest index wins each overlap region.
    assert winner[10, 50] == 2  # tile0 vs tile1 overlap → tile1
    assert winner[50, 10] == 3  # tile0 vs tile2 overlap → tile2
    assert winner[50, 50] == 4  # 4-way center overlap → tile3
    assert winner[10, 10] == 1  # tile0 exclusive region


def test_register_byte_identical_to_grid_when_register_false(tmp_path):
    """AE3 (critical): the SAME source with register=False yields /intensity
    bytes identical to the pre-feature grid path. Golden np.array_equal.
    """
    scene = _textured_scene(7)

    # Grid path (register=False, overlap=0): the legacy edge-to-edge stitch.
    src_grid = tmp_path / "grid"
    _write_overlapping_tiles(src_grid, scene)
    h5_grid = tmp_path / "grid.h5"
    import_dataset(
        src_grid, h5_grid,
        tile_config=TileConfig(grid_rows=2, grid_cols=2),  # no register
    )
    grid_intensity = DatasetStore(h5_grid).read_array("intensity")

    # Registered path on the SAME tiles.
    src_reg = tmp_path / "reg"
    _write_overlapping_tiles(src_reg, scene)
    h5_reg = tmp_path / "reg.h5"
    import_dataset(src_reg, h5_reg, tile_config=_reg_tile_config())
    reg_intensity = DatasetStore(h5_reg).read_array("intensity")

    # Grid is edge-to-edge (120x120, no overlap fusion); registered is the
    # overlap-fused 105x105 canvas. They MUST differ in shape — the point of
    # this test is that the *grid* path is unchanged from pre-feature.
    assert grid_intensity.shape == (120, 120)
    assert reg_intensity.shape == _REG_CANVAS

    # The grid output is byte-identical to a direct assemble_tiles call (the
    # pre-feature behaviour) — proving register=False does not touch it.
    from percell4.adapters.readers import read_tiff
    from percell4.domain.io.assembler import assemble_tiles
    tiles = {
        i: read_tiff(str(src_grid / f"img_s{i:02d}_ch00.tif"))["array"]
        for i in range(4)
    }
    golden = assemble_tiles(tiles, grid_rows=2, grid_cols=2).astype(np.float32)
    assert np.array_equal(grid_intensity, golden)
    # And register=False persisted NO stitch geometry.
    assert DatasetStore(h5_grid).read_stitch_geometry().registered is False


def test_register_gate_overlap_zero_raises(tmp_path):
    """register=True with overlap=0 → ValueError at the importer gate."""
    src = tmp_path / "raw"
    _write_overlapping_tiles(src, _textured_scene(7))
    h5 = tmp_path / "out.h5"
    with pytest.raises(ValueError, match="overlap"):
        import_dataset(
            src, h5,
            tile_config=TileConfig(
                grid_rows=2, grid_cols=2, register=True, overlap=0.0,
                reference_channel="ch00",
            ),
        )
    assert not h5.exists()


def test_register_gate_no_reference_channel_raises(tmp_path):
    """register=True without a reference_channel → ValueError at the gate."""
    src = tmp_path / "raw"
    _write_overlapping_tiles(src, _textured_scene(7))
    h5 = tmp_path / "out.h5"
    with pytest.raises(ValueError, match="reference_channel"):
        import_dataset(
            src, h5,
            tile_config=TileConfig(
                grid_rows=2, grid_cols=2, register=True, overlap=0.25,
                reference_channel=None,
            ),
        )
    assert not h5.exists()


def test_register_degenerate_uniform_tiles_raises(tmp_path):
    """A low-texture (uniform) reference → degenerate solve → RegistrationError
    propagates; stitch_registered is never written.
    """
    from percell4.domain.io.assembler import RegistrationError

    src = tmp_path / "raw"
    src.mkdir()
    for i in range(4):
        # Flat tiles — no texture for phase correlation to lock onto.
        tile = np.full((_REG_TH, _REG_TW), 1234, dtype=np.uint16)
        tifffile.imwrite(str(src / f"img_s{i:02d}_ch00.tif"), tile)

    h5 = tmp_path / "out.h5"
    with pytest.raises(RegistrationError):
        import_dataset(src, h5, tile_config=_reg_tile_config())
    # The file may not even be created; if it is, it must not be registered.
    if h5.exists():
        assert DatasetStore(h5).read_stitch_geometry().registered is False


def test_register_reimport_guard_refuses(tmp_path):
    """Re-importing over a registered dataset that has appended decay is
    refused (re-solving would rewrite offsets the decay was placed against).
    """
    src = tmp_path / "raw"
    _write_overlapping_tiles(src, _textured_scene(11), make_bin=True)
    h5 = tmp_path / "out.h5"
    flim = {
        "frequency_mhz": 80.0,
        "channel_calibrations": {},
        "bin_dimensions": {
            "x_dim": _REG_TW, "y_dim": _REG_TH, "t_dim": 4,
            "dtype": "uint32", "dim_order": "YXT", "header_bytes": 0,
        },
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import_dataset(src, h5, tile_config=_reg_tile_config(), flim_params=flim)

    # Re-import over the SAME registered output → refused.
    with pytest.raises(ValueError, match="already carries registered"):
        import_dataset(src, h5, tile_config=_reg_tile_config(), flim_params=flim)


def test_register_zstack_mosaic_deferred_error(tmp_path):
    """register=True combined with a z-stack mosaic → clear deferred error."""
    src = tmp_path / "raw"
    src.mkdir()
    scene = _textured_scene(7)
    # Each tile has 2 z-slices → _assemble_plane z-projects → registered path
    # cannot capture per-tile arrays at the post-projection plane in v1.
    for i, (y, x) in _REG_CORNERS.items():
        tile = scene[y : y + _REG_TH, x : x + _REG_TW]
        for z in range(2):
            tifffile.imwrite(
                str(src / f"img_s{i:02d}_z{z:02d}_ch00.tif"),
                (tile + z).astype(np.uint16),
            )

    h5 = tmp_path / "out.h5"
    with pytest.raises(ValueError, match="z-stack-mosaic overlap"):
        import_dataset(src, h5, tile_config=_reg_tile_config())


def test_register_with_creation_bin_canvas_post_bin(tmp_path):
    """A binned (creation_bin>1) registered import: bbox(offsets)==native_shape
    in post-bin units; no MetadataConsistencyError.
    """
    src = tmp_path / "raw"
    _write_overlapping_tiles(src, _textured_scene(7))
    h5 = tmp_path / "out.h5"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import_dataset(src, h5, tile_config=_reg_tile_config(), creation_bin=2)

    store = DatasetStore(h5)
    geo = store.read_stitch_geometry()
    assert geo.registered is True
    intensity = store.read_array("intensity")
    # Post-bin canvas: 60px tiles → 30px tiles, step 45 → 22 (sum_bin floor div).
    # native_shape must equal the assembled intensity shape (the lock).
    assert tuple(store.metadata["native_shape"]) == intensity.shape[-2:]
    # Offsets per-axis min is 0 (read-back invariant).
    assert int(geo.offsets[:, 0].min()) == 0
    assert int(geo.offsets[:, 1].min()) == 0


def test_register_full_coverage_solve_warning_clean(tmp_path):
    """A full-coverage, fully-connected 2x2 solve surfaces NO coverage or
    disconnected warning (R11 — warnings only fire on actual gaps).
    """
    src = tmp_path / "raw"
    _write_overlapping_tiles(src, _textured_scene(7))
    h5 = tmp_path / "out.h5"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import_dataset(src, h5, tile_config=_reg_tile_config())
    msgs = [str(w.message) for w in caught]
    assert not any("covers only" in m.lower() for m in msgs)
    assert not any("disconnected" in m.lower() for m in msgs)


def test_register_coverage_below_one_warns(tmp_path):
    """When the registered canvas has uncovered pixels (coverage < 1.0) a
    warning is surfaced (R11). The binned 2x2 (post-bin step > tile extent
    overlap) leaves a corner band uncovered, which deterministically warns.
    """
    src = tmp_path / "raw"
    _write_overlapping_tiles(src, _textured_scene(7))
    h5 = tmp_path / "out.h5"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import_dataset(src, h5, tile_config=_reg_tile_config(), creation_bin=2)
    msgs = [str(w.message) for w in caught]
    assert any("covers only" in m.lower() for m in msgs), msgs
    # Despite the gap, the import succeeds and is registered.
    assert DatasetStore(h5).read_stitch_geometry().registered is True


def test_register_timelapse_registers_once(tmp_path):
    """A 2-timepoint registered import solves once (first timepoint) and reuses
    offsets for all frames; the output is (T, H, W) on the registered canvas.
    """
    src = tmp_path / "raw"
    src.mkdir()
    scene = _textured_scene(7)
    for t in range(2):
        for i, (y, x) in _REG_CORNERS.items():
            tile = scene[y : y + _REG_TH, x : x + _REG_TW]
            tifffile.imwrite(
                str(src / f"img_t{t:02d}_s{i:02d}_ch00.tif"),
                (tile + t).astype(np.uint16),
            )

    h5 = tmp_path / "out.h5"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import_dataset(src, h5, tile_config=_reg_tile_config())

    store = DatasetStore(h5)
    intensity = store.read_array("intensity")
    assert intensity.shape == (2, *_REG_CANVAS)
    assert store.read_stitch_geometry().registered is True
    assert store.metadata["n_timepoints"] == 2


def test_register_timelapse_drift_warns(tmp_path):
    """A time-lapse whose LAST frame drifts beyond the overlap budget surfaces
    a drift warning; offsets still come from the first timepoint (R14).
    """
    src = tmp_path / "raw"
    src.mkdir()
    scene = _textured_scene(7, h=140, w=140)
    # First timepoint carves tiles at the nominal 45px step. The last
    # timepoint carves at a much SMALLER step (28px) so the registered relative
    # offsets drift |45-28| = 17px, beyond the overlap budget (0.25*60 = 15px),
    # while keeping heavy overlap so the last-frame re-check still solves — a
    # real inter-frame stage-drift simulation.
    step_by_t = {0: _REG_STEP, 1: 28}
    for t in range(2):
        step = step_by_t[t]
        corners = {
            0: (0, 0), 1: (0, step), 2: (step, 0), 3: (step, step),
        }
        for i, (y, x) in corners.items():
            tile = scene[y : y + _REG_TH, x : x + _REG_TW]
            tifffile.imwrite(
                str(src / f"img_t{t:02d}_s{i:02d}_ch00.tif"), tile
            )

    h5 = tmp_path / "out.h5"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import_dataset(src, h5, tile_config=_reg_tile_config())

    store = DatasetStore(h5)
    assert store.read_stitch_geometry().registered is True
    # Offsets come from the first timepoint (the clean scene0).
    np.testing.assert_array_equal(
        store.read_stitch_geometry().offsets,
        np.array([[0, 0], [0, 45], [45, 0], [45, 45]], dtype=np.int32),
    )
    # A drift OR degenerate-recheck warning is surfaced for the last frame.
    msgs = [str(w.message).lower() for w in caught]
    assert any("drift" in m for m in msgs), msgs


def test_register_commit_flag_written_last_crash_safe(tmp_path, monkeypatch):
    """Atomicity: a failure after offsets are written but before the
    stitch_registered flag leaves a NON-registered (recoverable) dataset, never
    the AE4 brick (offsets present + flag missing → 'not committed').
    """
    src = tmp_path / "raw"
    _write_overlapping_tiles(src, _textured_scene(7))
    h5 = tmp_path / "out.h5"

    from percell4.store import DatasetStore as _Store

    real_set_metadata = _Store.set_metadata

    def _failing_set_metadata(self, meta):
        # Let the reference_channel/overlap scalars through, but blow up on the
        # commit marker (stitch_registered) — simulating a crash mid-commit.
        if "stitch_registered" in meta:
            raise RuntimeError("simulated crash before commit flag")
        return real_set_metadata(self, meta)

    monkeypatch.setattr(_Store, "set_metadata", _failing_set_metadata)

    with pytest.raises(RuntimeError, match="simulated crash"):
        import_dataset(src, h5, tile_config=_reg_tile_config())

    # The offsets dataset may be present, but without the commit flag the
    # dataset reads back as NOT registered → safe to re-import.
    monkeypatch.undo()
    geo = DatasetStore(h5).read_stitch_geometry()
    assert geo.registered is False
