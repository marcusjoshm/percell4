"""Display/compute dispatch over the time axis in Hdf5DatasetRepository."""

from __future__ import annotations

import numpy as np

from percell4.adapters.hdf5_store import Hdf5DatasetRepository
from percell4.store import DatasetStore


def _make_dataset(path, intensity, dims, channel_names):
    store = DatasetStore(path)
    store.create(metadata={"channel_names": list(channel_names)})
    store.write_array("intensity", intensity, attrs={"dims": dims})
    return store


def test_build_view_timelapse_multichannel_keeps_time_axis(tmp_path):
    """(T,C,H,W) -> one layer per channel, each kept (T,H,W) for the slider."""
    h5 = tmp_path / "movie.h5"
    intensity = np.zeros((3, 2, 8, 8), dtype=np.float32)
    _make_dataset(h5, intensity, ["T", "C", "H", "W"], ["GFP", "DAPI"])

    repo = Hdf5DatasetRepository()
    handle = repo.open(h5)
    assert handle.metadata["n_timepoints"] == 3

    view = repo.build_view(handle)
    assert set(view.channel_images) == {"GFP", "DAPI"}
    # Each channel layer keeps its time axis (napari builds one slider).
    assert view.channel_images["GFP"].shape == (3, 8, 8)
    assert view.channel_images["DAPI"].shape == (3, 8, 8)


def test_build_view_timelapse_single_channel(tmp_path):
    """(T,H,W) -> ONE layer with the time axis, not T channel layers."""
    h5 = tmp_path / "movie.h5"
    intensity = np.zeros((4, 8, 8), dtype=np.float32)
    _make_dataset(h5, intensity, ["T", "H", "W"], ["GFP"])

    repo = Hdf5DatasetRepository()
    view = repo.build_view(repo.open(h5))
    assert list(view.channel_images) == ["GFP"]
    assert view.channel_images["GFP"].shape == (4, 8, 8)


def test_build_view_non_timelapse_splits_channels(tmp_path):
    """(C,H,W) on a single-timepoint dataset still splits into channels."""
    h5 = tmp_path / "still.h5"
    intensity = np.zeros((2, 8, 8), dtype=np.float32)
    _make_dataset(h5, intensity, ["C", "H", "W"], ["GFP", "DAPI"])

    repo = Hdf5DatasetRepository()
    view = repo.build_view(repo.open(h5))
    assert set(view.channel_images) == {"GFP", "DAPI"}
    assert view.channel_images["GFP"].shape == (8, 8)


def test_read_channel_images_timelapse_returns_2d_frame(tmp_path):
    """Compute path returns 2D per channel for a selected timepoint."""
    h5 = tmp_path / "movie.h5"
    # Encode timepoint in pixel value so we can assert which frame came back.
    intensity = np.stack(
        [np.full((2, 8, 8), t, dtype=np.float32) for t in range(3)], axis=0
    )  # (T=3, C=2, H, W)
    _make_dataset(h5, intensity, ["T", "C", "H", "W"], ["GFP", "DAPI"])

    repo = Hdf5DatasetRepository()
    handle = repo.open(h5)

    # Default frame 0.
    imgs0 = repo.read_channel_images(handle)
    assert set(imgs0) == {"GFP", "DAPI"}
    assert imgs0["GFP"].shape == (8, 8)
    assert imgs0["GFP"][0, 0] == 0.0

    # Explicit frame 2.
    imgs2 = repo.read_channel_images(handle, timepoint=2)
    assert imgs2["GFP"].shape == (8, 8)
    assert imgs2["GFP"][0, 0] == 2.0


def test_read_channel_images_non_timelapse_unchanged(tmp_path):
    """Single-timepoint compute path is unchanged: 2D per channel."""
    h5 = tmp_path / "still.h5"
    intensity = np.zeros((2, 8, 8), dtype=np.float32)
    _make_dataset(h5, intensity, ["C", "H", "W"], ["GFP", "DAPI"])

    repo = Hdf5DatasetRepository()
    imgs = repo.read_channel_images(repo.open(h5))
    assert set(imgs) == {"GFP", "DAPI"}
    assert imgs["GFP"].shape == (8, 8)


def test_repo_read_channel_timelapse_slices_frame_then_channel(tmp_path):
    """repo.read_channel reaches store.read_channel's timepoint param (U1)."""
    h5 = tmp_path / "movie.h5"
    intensity = np.zeros((3, 2, 8, 8), dtype=np.float32)
    for t in range(3):
        for c in range(2):
            intensity[t, c] = t * 10 + c
    _make_dataset(h5, intensity, ["T", "C", "H", "W"], ["GFP", "DAPI"])

    repo = Hdf5DatasetRepository()
    handle = repo.open(h5)
    out = repo.read_channel(handle, "intensity", 1, timepoint=2)
    assert out.shape == (8, 8)
    assert np.all(out == 21)  # t=2, c=1


def test_repo_read_channel_non_timelapse_unchanged(tmp_path):
    """On (C,H,W), repo.read_channel returns the channel plane as today."""
    h5 = tmp_path / "still.h5"
    intensity = np.zeros((2, 8, 8), dtype=np.float32)
    intensity[1] = 5.0
    _make_dataset(h5, intensity, ["C", "H", "W"], ["GFP", "DAPI"])

    repo = Hdf5DatasetRepository()
    out = repo.read_channel(repo.open(h5), "intensity", 1)
    assert out.shape == (8, 8)
    assert np.all(out == 5.0)
