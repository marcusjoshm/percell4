"""U1 — 4-D ``(T_acq, H, W, T_bins)`` /decay storage in the store.

Covers the schema foundation for multi-timepoint TCSPC FLIM: per-frame
write/read, chunking, the ``dims`` vocabulary (leading ``"Tacq"`` distinct from
the generic ``"T"`` collision), native-shape inference from a 4-D decay, the
view-bin rank guard, and legacy 3-D back-compat.
"""

from __future__ import annotations

import h5py
import numpy as np
import pytest

from percell4.domain.io.view_bin import sum_bin_decay
from percell4.store import DatasetStore, LayerSizeMismatchError, _choose_chunks

_H, _W, _TB, _NT = 6, 8, 4, 3


def _timelapse_store(tmp_path) -> DatasetStore:
    store = DatasetStore(tmp_path / "tl.h5")
    store.create(metadata={
        "channel_names": ["ch00"],
        "native_shape": (_H, _W),
        "n_timepoints": _NT,
    })
    return store


def _frame(t: int) -> np.ndarray:
    """A distinct, spatially-varying 3-D decay frame per timepoint."""
    y = np.arange(_H)[:, None, None]
    x = np.arange(_W)[None, :, None]
    k = np.arange(_TB)[None, None, :]
    return ((t + 1) * 1000 + y * 100 + x * 10 + k).astype(np.float32)


def test_write_read_decay_frames_roundtrip(tmp_path):
    store = _timelapse_store(tmp_path)
    for t in range(_NT):
        store.write_decay_frame("ch00", _frame(t), timepoint=t)

    # Per-frame reads return exactly each written 3-D frame.
    for t in range(_NT):
        got = store.read_decay("ch00", timepoint=t)
        assert got.shape == (_H, _W, _TB)
        np.testing.assert_array_equal(got, _frame(t))

    # Whole read is 4-D with the Tacq-led dims vocabulary and per-frame chunks.
    whole = store.read_decay("ch00")
    assert whole.shape == (_NT, _H, _W, _TB)
    with h5py.File(store.path, "r") as f:
        ds = f["decay/ch00"]
        assert list(ds.attrs["dims"]) == ["Tacq", "H", "W", "T"]
        assert ds.chunks == (1, min(64, _H), min(64, _W), _TB)


def test_in_place_frame_rewrite_keeps_other_frames(tmp_path):
    store = _timelapse_store(tmp_path)
    for t in range(_NT):
        store.write_decay_frame("ch00", _frame(t), timepoint=t)
    # Rewrite only frame 1; frames 0 and 2 must be untouched.
    new1 = _frame(1) + 7.0
    store.write_decay_frame("ch00", new1, timepoint=1)
    np.testing.assert_array_equal(store.read_decay("ch00", timepoint=0), _frame(0))
    np.testing.assert_array_equal(store.read_decay("ch00", timepoint=1), new1)
    np.testing.assert_array_equal(store.read_decay("ch00", timepoint=2), _frame(2))


def test_decay_write_invalidates_phasor(tmp_path):
    store = _timelapse_store(tmp_path)
    store.write_decay_frame("ch00", _frame(0), timepoint=0)
    # Plant a stale phasor group, then rewrite the decay frame.
    with h5py.File(store.path, "a") as f:
        f.create_dataset("phasor/ch00/g", data=np.zeros((_NT, _H, _W), np.float32))
    store.write_decay_frame("ch00", _frame(1), timepoint=1)
    with h5py.File(store.path, "r") as f:
        assert "phasor/ch00" not in f


def test_legacy_3d_decay_backcompat(tmp_path):
    """A single-timepoint dataset stores plain 3-D decay; timepoint slicing
    accepts only 0."""
    store = DatasetStore(tmp_path / "single.h5")
    store.create(metadata={"channel_names": ["ch00"],
                           "native_shape": (_H, _W), "n_timepoints": 1})
    frame = _frame(0)
    store.write_decay_frame("ch00", frame, timepoint=0)
    with h5py.File(store.path, "r") as f:
        ds = f["decay/ch00"]
        assert ds.shape == (_H, _W, _TB)
        assert list(ds.attrs["dims"]) == ["H", "W", "T"]
    np.testing.assert_array_equal(store.read_decay("ch00"), frame)
    np.testing.assert_array_equal(store.read_decay("ch00", timepoint=0), frame)
    with pytest.raises(IndexError):
        store.read_decay("ch00", timepoint=1)


def test_view_bin_slices_spatial_only_on_4d(tmp_path):
    store = _timelapse_store(tmp_path)
    for t in range(_NT):
        store.write_decay_frame("ch00", _frame(t), timepoint=t)
    # view-bin a single frame: spatial halved, T_bins + frame identity intact.
    binned = store.read_decay("ch00", view_bin=2, timepoint=1)
    assert binned.shape == (_H // 2, _W // 2, _TB)
    np.testing.assert_array_equal(binned, sum_bin_decay(_frame(1), 2))
    # view-binning the whole 4-D decay is rejected (would fold T_acq).
    with pytest.raises(ValueError, match="timepoint"):
        store.read_decay("ch00", view_bin=2)
    # sum_bin_decay itself guards rank.
    with pytest.raises(ValueError, match="3-D"):
        sum_bin_decay(np.zeros((_NT, _H, _W, _TB), np.float32), 2)


def test_choose_chunks_4d_decay():
    assert _choose_chunks((_NT, _H, _W, _TB), is_decay=True) == (
        1, min(64, _H), min(64, _W), _TB
    )
    # 3-D decay chunking unchanged.
    assert _choose_chunks((_H, _W, _TB), is_decay=True) == (
        min(64, _H), min(64, _W), _TB
    )


def test_native_shape_inferred_from_4d_decay(tmp_path):
    """A decay-only 4-D file infers native_shape from dims [1:3] (not [0:2])."""
    store = _timelapse_store(tmp_path)
    for t in range(_NT):
        store.write_decay_frame("ch00", _frame(t), timepoint=t)
    # Strip /intensity + /metadata native_shape so inference must use /decay.
    with h5py.File(store.path, "a") as f:
        if "metadata" in f:
            f["metadata"].attrs.pop("native_shape", None)
    from percell4.store import _infer_bin_metadata
    with h5py.File(store.path, "r") as f:
        inferred = _infer_bin_metadata(f)
    assert inferred["native_shape"] == (_H, _W)
    assert inferred["n_timepoints"] == _NT


def test_validate_decay_shape_rejects_mismatch(tmp_path):
    store = _timelapse_store(tmp_path)
    # 3-D always valid.
    assert store._validate_decay_shape(np.zeros((_H, _W, _TB), np.float32)) == \
        ["H", "W", "T"]
    # Correct 4-D valid.
    assert store._validate_decay_shape(
        np.zeros((_NT, _H, _W, _TB), np.float32)
    ) == ["Tacq", "H", "W", "T"]
    # Wrong leading axis.
    with pytest.raises(LayerSizeMismatchError):
        store._validate_decay_shape(np.zeros((_NT + 1, _H, _W, _TB), np.float32))
    # Wrong spatial dims.
    with pytest.raises(LayerSizeMismatchError):
        store._validate_decay_shape(np.zeros((_NT, _H + 1, _W, _TB), np.float32))
