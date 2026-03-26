"""Tests for DatasetStore."""

from __future__ import annotations

import h5py
import numpy as np
import pandas as pd
import pytest

from percell4.store import DatasetStore


@pytest.fixture
def store(tmp_h5):
    """Create a DatasetStore with an initialized .h5 file."""
    s = DatasetStore(tmp_h5)
    s.create(metadata={"source": "test", "pixel_size_um": 0.325})
    return s


# ── Array roundtrip ───────────────────────────────────────────


def test_write_read_array_2d(store):
    """Write a 2D array, read it back, verify exact match."""
    data = np.random.rand(100, 100).astype(np.float32)
    count = store.write_array("intensity", data, attrs={"dims": ["H", "W"]})
    assert count == 10000

    result = store.read_array("intensity")
    np.testing.assert_array_equal(result, data)


def test_write_read_array_3d(store):
    """Write a 3D array (e.g., multi-channel), verify roundtrip."""
    data = np.random.rand(3, 64, 64).astype(np.float32)
    count = store.write_array(
        "intensity", data, attrs={"dims": ["C", "H", "W"]}
    )
    assert count == 3 * 64 * 64

    result = store.read_array("intensity")
    np.testing.assert_array_equal(result, data)


def test_write_array_decay_uses_lzf(store):
    """Decay data should use lzf compression, not gzip."""
    data = np.zeros((64, 64, 256), dtype=np.uint16)
    store.write_array("decay", data, is_decay=True)

    with h5py.File(store.path, "r") as f:
        assert f["decay"].compression == "lzf"


def test_write_array_spatial_uses_gzip_shuffle(store):
    """Spatial data should use gzip + shuffle."""
    data = np.zeros((100, 100), dtype=np.float32)
    store.write_array("intensity", data)

    with h5py.File(store.path, "r") as f:
        assert f["intensity"].compression == "gzip"
        assert f["intensity"].shuffle is True


def test_dims_attribute_stored(store):
    """Every array write with dims attr should persist it."""
    data = np.zeros((3, 100, 100), dtype=np.float32)
    store.write_array("intensity", data, attrs={"dims": ["C", "H", "W"]})

    with h5py.File(store.path, "r") as f:
        dims = list(f["intensity"].attrs["dims"])
        assert dims == ["C", "H", "W"]


def test_write_array_overwrites(store):
    """Writing to the same path replaces the dataset."""
    data1 = np.ones((10, 10), dtype=np.float32)
    data2 = np.full((20, 20), 2.0, dtype=np.float32)

    store.write_array("test", data1)
    store.write_array("test", data2)

    result = store.read_array("test")
    assert result.shape == (20, 20)
    np.testing.assert_array_equal(result, data2)


def test_read_nonexistent_raises(store):
    """Reading a path that doesn't exist should raise KeyError."""
    with pytest.raises(KeyError, match="Dataset not found"):
        store.read_array("nonexistent")


# ── Labels ────────────────────────────────────────────────────


def test_write_read_labels(store, sample_labels):
    """Labels roundtrip with int32 enforcement."""
    count = store.write_labels("cellpose", sample_labels)
    assert count == sample_labels.size

    result = store.read_labels("cellpose")
    assert result.dtype == np.int32
    np.testing.assert_array_equal(result, sample_labels)


def test_labels_enforces_2d(store):
    """3D array should be rejected for labels."""
    with pytest.raises(ValueError, match="2D"):
        store.write_labels("bad", np.zeros((10, 10, 10), dtype=np.int32))


def test_list_labels(store, sample_labels):
    """list_labels returns all label set names."""
    store.write_labels("cellpose", sample_labels)
    store.write_labels("manual", sample_labels)
    assert sorted(store.list_labels()) == ["cellpose", "manual"]


def test_list_labels_empty(store):
    """list_labels returns empty list when no labels exist."""
    assert store.list_labels() == []


# ── Masks ─────────────────────────────────────────────────────


def test_write_read_mask(store):
    """Mask roundtrip with uint8 enforcement."""
    mask = np.ones((100, 100), dtype=np.bool_)
    count = store.write_mask("otsu_ch1", mask)
    assert count == 10000

    result = store.read_mask("otsu_ch1")
    assert result.dtype == np.uint8
    assert result.sum() == 10000


def test_list_masks(store):
    """list_masks returns all mask names."""
    mask = np.zeros((10, 10), dtype=np.uint8)
    store.write_mask("otsu", mask)
    store.write_mask("triangle", mask)
    assert sorted(store.list_masks()) == ["otsu", "triangle"]


# ── DataFrame ─────────────────────────────────────────────────


def test_write_read_dataframe(store):
    """DataFrame roundtrip via CSV string."""
    df = pd.DataFrame(
        {"label": [1, 2, 3], "area": [100.0, 200.0, 300.0], "mean": [1.5, 2.5, 3.5]}
    )
    count = store.write_dataframe("measurements", df)
    assert count == 3

    result = store.read_dataframe("measurements")
    assert len(result) == 3
    assert list(result.columns) == ["label", "area", "mean"]
    pd.testing.assert_frame_equal(result, df)


def test_write_dataframe_overwrites(store):
    """Overwriting a DataFrame replaces it."""
    df1 = pd.DataFrame({"x": [1]})
    df2 = pd.DataFrame({"y": [2, 3]})

    store.write_dataframe("measurements", df1)
    store.write_dataframe("measurements", df2)

    result = store.read_dataframe("measurements")
    assert len(result) == 2
    assert "y" in result.columns


# ── Metadata ──────────────────────────────────────────────────


def test_metadata_from_create(store):
    """Metadata set at creation time is readable."""
    meta = store.metadata
    assert meta["source"] == "test"
    assert meta["pixel_size_um"] == 0.325


def test_set_metadata(store):
    """set_metadata adds/updates attributes."""
    count = store.set_metadata({"laser_freq": 80.0, "channels": 3})
    assert count == 2

    meta = store.metadata
    assert meta["laser_freq"] == 80.0


# ── Session reads ─────────────────────────────────────────────


def test_session_read(store, sample_labels, sample_image):
    """Session mode allows multiple reads without re-opening."""
    store.write_array("intensity", sample_image, attrs={"dims": ["H", "W"]})
    store.write_labels("cellpose", sample_labels)

    with store.open_read() as s:
        img = s.read_array("intensity")
        lab = s.read_labels("cellpose")
        meta = s.metadata

    assert img.shape == (100, 100)
    assert lab.shape == (100, 100)
    assert "source" in meta


# ── Atomic create ─────────────────────────────────────────────


def test_create_atomic(tmp_path):
    """Atomic creation writes to temp then renames."""
    h5_path = tmp_path / "atomic_test.h5"

    def build(f):
        f.create_dataset("test", data=np.array([1, 2, 3]))

    DatasetStore.create_atomic(h5_path, build)

    store = DatasetStore(h5_path)
    result = store.read_array("test")
    np.testing.assert_array_equal(result, [1, 2, 3])


def test_create_atomic_cleans_up_on_error(tmp_path):
    """If build_fn raises, temp file is cleaned up."""
    h5_path = tmp_path / "should_not_exist.h5"

    def build(f):
        raise RuntimeError("build failed")

    with pytest.raises(RuntimeError, match="build failed"):
        DatasetStore.create_atomic(h5_path, build)

    assert not h5_path.exists()


# ── Chunking ──────────────────────────────────────────────────


def test_decay_chunking_64(store):
    """Decay data should get (64, 64, N_bins) chunks, not (256, 256, N_bins)."""
    data = np.zeros((128, 128, 256), dtype=np.uint16)
    store.write_array("decay", data, is_decay=True)

    with h5py.File(store.path, "r") as f:
        chunks = f["decay"].chunks
        assert chunks[0] == 64
        assert chunks[1] == 64
        assert chunks[2] == 256  # full TCSPC axis
