"""Tests for parallel HDF5 decode into shared memory."""

from __future__ import annotations

import numpy as np

from percell4.adapters.parallel_decode import (
    array_meta,
    decode_array_parallel,
    default_worker_count,
)
from percell4.store import DatasetStore


def _make_timelapse(path, n_t=6, n_c=2, h=24, w=20, *, with_nan=False):
    rng = np.random.default_rng(0)
    intensity = (rng.random((n_t, n_c, h, w)) * 300).astype(np.float32)
    if with_nan:
        intensity[2, 0, 0, 0] = np.nan
    store = DatasetStore(path)
    store.create(metadata={})
    store.write_array("intensity", intensity, attrs={"dims": ["T", "C", "H", "W"]})
    store.set_metadata({"channel_names": ["A", "B"], "n_timepoints": n_t})
    labels = np.zeros((n_t, h, w), dtype=np.int32)
    for t in range(n_t):
        labels[t, : t + 1, : t + 1] = t + 1
    store.write_labels("seg", labels)
    return intensity, labels


def test_array_meta(tmp_h5):
    _make_timelapse(tmp_h5)
    shape, dtype, is_ts = array_meta(str(tmp_h5), "intensity")
    assert shape == (6, 2, 24, 20)
    assert dtype == np.float32
    assert is_ts is True


def test_default_worker_count_caps_at_frames():
    assert default_worker_count(1) == 1
    assert default_worker_count(2) <= 2


def test_parallel_intensity_matches_serial(tmp_h5):
    intensity, _ = _make_timelapse(tmp_h5)
    par = decode_array_parallel(str(tmp_h5), "intensity")
    serial = DatasetStore(tmp_h5).read_array("intensity")
    assert par.shape == intensity.shape
    assert par.dtype == np.float32
    np.testing.assert_array_equal(par, serial)


def test_parallel_labels_match_serial(tmp_h5):
    _, labels = _make_timelapse(tmp_h5)
    par = decode_array_parallel(str(tmp_h5), "labels/seg")
    serial = DatasetStore(tmp_h5).read_labels("seg")
    np.testing.assert_array_equal(par, serial)


def test_parallel_preserves_nan(tmp_h5):
    intensity, _ = _make_timelapse(tmp_h5, with_nan=True)
    par = decode_array_parallel(str(tmp_h5), "intensity")
    np.testing.assert_array_equal(np.isnan(par), np.isnan(intensity))


def test_progress_callback_counts_all_frames(tmp_h5):
    _make_timelapse(tmp_h5, n_t=6)
    seen = []
    decode_array_parallel(str(tmp_h5), "intensity", progress_cb=seen.append)
    # One callback per frame; final value equals frame count.
    assert max(seen) == 6


def test_non_timelapse_serial_path(tmp_h5):
    """A non-time-stacked array reads via the serial branch, still correct."""
    store = DatasetStore(tmp_h5)
    store.create(metadata={"channel_names": ["GFP", "DAPI"]})
    img = (np.random.default_rng(1).random((30, 40)) * 10).astype(np.float32)
    store.write_array("intensity", np.stack([img, img * 0.5]))  # (C,H,W), no T
    par = decode_array_parallel(str(tmp_h5), "intensity")
    np.testing.assert_array_equal(par, DatasetStore(tmp_h5).read_array("intensity"))
