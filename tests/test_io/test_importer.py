"""Integration tests for the import pipeline."""

from __future__ import annotations

import numpy as np
import tifffile

from percell4.adapters.importer import import_dataset
from percell4.domain.io.models import TokenConfig
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
