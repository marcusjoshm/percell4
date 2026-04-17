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
