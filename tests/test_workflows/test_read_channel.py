"""Tests for DatasetStore.read_channel."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.store import DatasetStore


def _store_with_2d(tmp_h5: Path) -> DatasetStore:
    store = DatasetStore(tmp_h5)
    arr = np.arange(100 * 100, dtype=np.float32).reshape(100, 100)
    store.write_array("intensity", arr, attrs={"dims": ["H", "W"]})
    return store


def _store_with_3d(tmp_h5: Path) -> DatasetStore:
    store = DatasetStore(tmp_h5)
    # (C=3, H=32, W=32) with channel i filled with value (i + 1)
    arr = np.stack(
        [np.full((32, 32), i + 1, dtype=np.float32) for i in range(3)],
        axis=0,
    )
    store.write_array("intensity", arr, attrs={"dims": ["C", "H", "W"]})
    return store


def test_read_channel_2d_returns_full_array(tmp_h5: Path) -> None:
    store = _store_with_2d(tmp_h5)
    out = store.read_channel("intensity", 0)
    assert out.shape == (100, 100)
    assert np.array_equal(out, store.read_array("intensity"))


def test_read_channel_2d_rejects_nonzero_index(tmp_h5: Path) -> None:
    store = _store_with_2d(tmp_h5)
    with pytest.raises(IndexError):
        store.read_channel("intensity", 1)


def test_read_channel_3d_returns_one_plane(tmp_h5: Path) -> None:
    store = _store_with_3d(tmp_h5)
    for i in range(3):
        out = store.read_channel("intensity", i)
        assert out.shape == (32, 32)
        assert np.all(out == i + 1)


def test_read_channel_3d_rejects_out_of_range(tmp_h5: Path) -> None:
    store = _store_with_3d(tmp_h5)
    with pytest.raises(IndexError):
        store.read_channel("intensity", 3)
    with pytest.raises(IndexError):
        store.read_channel("intensity", -1)


def test_read_channel_missing_dataset_raises(tmp_h5: Path) -> None:
    store = DatasetStore(tmp_h5)
    # File doesn't exist yet — create empty one so open_read doesn't fail
    store.create(metadata={})
    with pytest.raises(KeyError):
        store.read_channel("intensity", 0)


def test_read_channel_works_in_session_mode(tmp_h5: Path) -> None:
    store = _store_with_3d(tmp_h5)
    with store.open_read() as s:
        a = s.read_channel("intensity", 0)
        b = s.read_channel("intensity", 2)
    assert np.all(a == 1)
    assert np.all(b == 3)
