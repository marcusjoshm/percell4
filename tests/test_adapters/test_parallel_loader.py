"""Tests for the lazy resident-buffer loader (U1)."""

from __future__ import annotations

import numpy as np
import pytest

from percell4.adapters.parallel_loader import (
    LazyResidentBuffer,
    decode_frame_into,
    plan_resources,
)
from percell4.store import DatasetStore


def _make_timelapse(
    path, n_t=4, n_c=2, h=16, w=16, *, with_nan=False, with_labels=True
) -> np.ndarray:
    """Build a time-lapse .h5 and return the written intensity array."""
    rng = np.random.default_rng(0)
    intensity = (rng.random((n_t, n_c, h, w)) * 100).astype(np.float32)
    if with_nan:
        intensity[1, 0, 0, 0] = np.nan
    store = DatasetStore(path)
    store.create(metadata={})
    store.write_array("intensity", intensity, attrs={"dims": ["T", "C", "H", "W"]})
    store.set_metadata({"channel_names": ["A", "B"], "n_timepoints": n_t})
    if with_labels:
        labels = np.zeros((n_t, h, w), dtype=np.int32)
        for t in range(n_t):
            labels[t, : t + 2, : t + 2] = t + 1
        store.write_labels("cellpose", labels)
    return intensity


def test_plan_resources_timelapse_multichannel(tmp_h5):
    _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    n_t, lazy_specs, eager = plan_resources(store)

    assert n_t == 4
    names = {s.layer_name for s in lazy_specs}
    assert names == {"A", "B", "cellpose"}
    assert eager == {}  # everything is time-stacked
    a = next(s for s in lazy_specs if s.layer_name == "A")
    assert a.kind == "intensity" and a.channel_idx == 0
    b = next(s for s in lazy_specs if s.layer_name == "B")
    assert b.channel_idx == 1
    seg = next(s for s in lazy_specs if s.layer_name == "cellpose")
    assert seg.kind == "labels" and seg.channel_idx is None
    assert seg.dtype == np.int32


def test_fill_frame_matches_eager(tmp_h5):
    intensity = _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    n_t, specs, _ = plan_resources(store)
    buf = LazyResidentBuffer(tmp_h5, n_t, specs)

    # Nothing ready initially.
    assert buf.pending_frames() == [0, 1, 2, 3]
    assert not buf.is_ready(0)

    for t in range(n_t):
        buf.fill_frame(t)

    assert buf.is_ready(2)
    assert buf.pending_frames() == []
    # Channel arrays equal the eager per-channel stacks.
    np.testing.assert_array_equal(buf.arrays["A"], intensity[:, 0])
    np.testing.assert_array_equal(buf.arrays["B"], intensity[:, 1])


def test_fill_frame_with_open_store_reads_intensity_once(tmp_h5):
    intensity = _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    n_t, specs, _ = plan_resources(store)
    buf = LazyResidentBuffer(tmp_h5, n_t, specs)
    with store.open_read() as s:
        for t in range(n_t):
            buf.fill_frame(t, store=s)
    np.testing.assert_array_equal(buf.arrays["A"], intensity[:, 0])
    np.testing.assert_array_equal(buf.arrays["B"], intensity[:, 1])


def test_fill_frame_idempotent(tmp_h5):
    _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    n_t, specs, _ = plan_resources(store)
    buf = LazyResidentBuffer(tmp_h5, n_t, specs)
    buf.fill_frame(1)
    snapshot = buf.arrays["A"][1].copy()
    buf.fill_frame(1)  # second call is a no-op
    np.testing.assert_array_equal(buf.arrays["A"][1], snapshot)
    assert buf.is_ready(1)


def test_nan_preserved(tmp_h5):
    intensity = _make_timelapse(tmp_h5, with_nan=True)
    store = DatasetStore(tmp_h5)
    n_t, specs, _ = plan_resources(store)
    buf = LazyResidentBuffer(tmp_h5, n_t, specs)
    buf.fill_frame(1)
    assert np.isnan(buf.arrays["A"][1, 0, 0])
    np.testing.assert_array_equal(
        np.isnan(buf.arrays["A"]), np.isnan(intensity[:, 0])
    )


def test_single_channel_timelapse(tmp_h5):
    n_frames, h, w = 3, 8, 8
    rng = np.random.default_rng(1)
    intensity = (rng.random((n_frames, h, w)) * 50).astype(np.float32)
    store = DatasetStore(tmp_h5)
    store.create(metadata={})
    store.write_array("intensity", intensity, attrs={"dims": ["T", "H", "W"]})
    store.set_metadata({"channel_names": ["solo"], "n_timepoints": n_frames})

    n_t, specs, eager = plan_resources(store)
    assert n_t == n_frames
    assert len(specs) == 1
    spec = specs[0]
    assert spec.layer_name == "solo" and spec.channel_idx is None
    buf = LazyResidentBuffer(tmp_h5, n_t, specs)
    for t in range(n_frames):
        buf.fill_frame(t)
    np.testing.assert_array_equal(buf.arrays["solo"], intensity)


def test_two_d_time_invariant_mask_is_eager(tmp_h5):
    _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    # A 2D (time-invariant) mask on the time-lapse dataset.
    mask = np.zeros((16, 16), dtype=np.uint8)
    mask[2:5, 2:5] = 1
    store.write_mask("gate", mask)

    n_t, specs, eager = plan_resources(store)
    # The 2D mask is eager, not a lazy spec.
    assert "gate" in eager
    assert eager["gate"].shape == (16, 16)
    assert "gate" not in {s.layer_name for s in specs}


def test_non_timelapse_all_eager(tmp_h5):
    store = DatasetStore(tmp_h5)
    store.create(metadata={"channel_names": ["GFP", "DAPI"]})
    img = (np.random.default_rng(2).random((100, 100)) * 10).astype(np.float32)
    store.write_array("intensity", np.stack([img, img * 0.5]))
    store.write_labels("cellpose", (img > 5).astype(np.int32))

    n_t, specs, eager = plan_resources(store)
    assert n_t == 1
    assert specs == []
    assert "GFP" in eager and "DAPI" in eager and "cellpose" in eager


def test_fill_frame_out_of_range(tmp_h5):
    _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    n_t, specs, _ = plan_resources(store)
    buf = LazyResidentBuffer(tmp_h5, n_t, specs)
    with pytest.raises(IndexError):
        buf.fill_frame(99)


def test_close_releases_and_resets(tmp_h5):
    _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    n_t, specs, _ = plan_resources(store)
    buf = LazyResidentBuffer(tmp_h5, n_t, specs)
    buf.fill_frame(0)
    buf.close()
    assert buf.arrays == {}
    assert not buf.is_ready(0)


def test_decode_frame_into_primitive(tmp_h5):
    intensity = _make_timelapse(tmp_h5)
    out = np.zeros((4, 16, 16), dtype=np.float32)
    decode_frame_into(
        out, 2, path=str(tmp_h5), hdf5_path="intensity", channel_idx=1, view_bin=1
    )
    np.testing.assert_array_equal(out[2], intensity[2, 1])
    # Other slots untouched.
    assert not out[0].any()
