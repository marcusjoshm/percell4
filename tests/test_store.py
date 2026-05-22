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


def test_metadata_channel_names_normalized_to_str_list(store):
    """channel_names round-trips as a plain list[str], not a numpy array.

    h5py returns a numpy string array for a multi-element sequence attr,
    whose truthiness is ambiguous downstream (``arr or []`` raises). The
    metadata reader normalizes it.
    """
    store.set_metadata({"channel_names": ["DAPI", "GFP", "RFP"]})
    cn = store.metadata["channel_names"]
    assert isinstance(cn, list)
    assert cn == ["DAPI", "GFP", "RFP"]
    assert all(isinstance(c, str) for c in cn)
    # Single-channel case stays a list too.
    store.set_metadata({"channel_names": ["mNG"]})
    assert store.metadata["channel_names"] == ["mNG"]


# ── Bin metadata: native_shape + creation_bin (U1) ────────────


def test_metadata_infers_native_shape_from_intensity(store):
    """native_shape and creation_bin are populated on read even when the
    file was written before the keys existed."""
    data = np.random.rand(50, 50).astype(np.float32)
    store.write_array("intensity", data)

    meta = store.metadata
    assert meta["native_shape"] == (50, 50)
    assert meta["creation_bin"] == 1


def test_metadata_infers_native_shape_from_3d_intensity(store):
    """Trailing two dims are the spatial dims."""
    data = np.random.rand(3, 64, 80).astype(np.float32)
    store.write_array("intensity", data, attrs={"dims": ["C", "H", "W"]})

    meta = store.metadata
    assert meta["native_shape"] == (64, 80)


def test_metadata_infers_native_shape_from_decay_only(store):
    """Decay-only files (bin-only TCSPC imports) infer from /decay."""
    # No /intensity — directly poke a /decay/<ch> array onto disk.
    with h5py.File(store.path, "a") as f:
        decay_grp = f.require_group("decay")
        decay_grp.create_dataset("ch0", data=np.zeros((128, 128, 8)))

    meta = store.metadata
    assert meta["native_shape"] == (128, 128)
    assert meta["creation_bin"] == 1


def test_metadata_native_shape_none_when_no_arrays(store):
    """Empty .h5 (no /intensity, no /decay) exposes native_shape=None."""
    meta = store.metadata
    assert meta["native_shape"] is None
    assert meta["creation_bin"] == 1


def test_metadata_stored_native_shape_overrides_inference(store):
    """Writer's intent wins when /metadata.native_shape is explicit."""
    data = np.random.rand(50, 50).astype(np.float32)
    store.write_array("intensity", data)
    # Set an explicit native_shape that disagrees with /intensity (legal
    # only if it was the actual native at compress and /intensity was
    # subsequently rewritten -- contrived but tests the precedence).
    with h5py.File(store.path, "a") as f:
        f["metadata"].attrs["native_shape"] = (200, 200)

    meta = store.metadata
    assert meta["native_shape"] == (200, 200)  # stored wins


def test_set_metadata_persists_inferred_native_shape(store):
    """First set_metadata call after a write_array persists the inferred
    bin metadata to /metadata.attrs."""
    data = np.random.rand(40, 40).astype(np.float32)
    store.write_array("intensity", data)

    # Confirm not on disk yet.
    with h5py.File(store.path, "r") as f:
        assert "native_shape" not in f["metadata"].attrs

    store.set_metadata({"unrelated": 1})

    with h5py.File(store.path, "r") as f:
        assert tuple(f["metadata"].attrs["native_shape"]) == (40, 40)
        assert int(f["metadata"].attrs["creation_bin"]) == 1
        assert f["metadata"].attrs["unrelated"] == 1


def test_set_metadata_raises_on_inconsistent_stored_shape(store):
    """Stored /metadata.native_shape disagreeing with /intensity shape
    raises MetadataConsistencyError -- never silently overwritten."""
    from percell4.store import MetadataConsistencyError

    data = np.random.rand(40, 40).astype(np.float32)
    store.write_array("intensity", data)
    # Plant a disagreeing native_shape (513x513 on a 40x40 array).
    with h5py.File(store.path, "a") as f:
        f["metadata"].attrs["native_shape"] = (513, 513)

    with pytest.raises(MetadataConsistencyError):
        store.set_metadata({"some_other_key": 1})


def test_creation_bin_default_is_one(store):
    """creation_bin defaults to 1 when not explicitly set."""
    data = np.random.rand(50, 50).astype(np.float32)
    store.write_array("intensity", data)

    meta = store.metadata
    assert meta["creation_bin"] == 1


def test_creation_bin_stored_value_preserved(store):
    """An explicit creation_bin attr is preserved by set_metadata."""
    data = np.random.rand(50, 50).astype(np.float32)
    store.write_array("intensity", data)
    store.set_metadata({"creation_bin": 3})

    meta = store.metadata
    assert meta["creation_bin"] == 3


# ── view_bin dispatch (U3) ────────────────────────────────────


def test_read_array_view_bin_1_byte_identical(store):
    """At view_bin=1 every read is byte-identical to the array written
    (R5 round-trip)."""
    data = np.random.rand(40, 40).astype(np.float32)
    store.write_array("intensity", data)
    np.testing.assert_array_equal(
        store.read_array("intensity", view_bin=1), data
    )


def test_read_array_intensity_sum_bin_at_k2(store):
    """/intensity at view_bin=2 sum-bins on (H, W)."""
    data = np.ones((6, 6), dtype=np.float32)
    store.write_array("intensity", data)
    out = store.read_array("intensity", view_bin=2)
    assert out.shape == (3, 3)
    np.testing.assert_array_equal(out, np.full((3, 3), 4.0))


def test_read_array_intensity_3d_view_bin(store):
    """3D /intensity (C, H, W) bins each channel's trailing two axes."""
    data = np.ones((2, 6, 6), dtype=np.float32)
    store.write_array("intensity", data, attrs={"dims": ["C", "H", "W"]})
    out = store.read_array("intensity", view_bin=2)
    assert out.shape == (2, 3, 3)
    assert np.all(out == 4)


def test_read_array_decay_uses_sum_bin_decay(store):
    """/decay/<ch> at view_bin=2 sum-bins (H, W); T axis untouched."""
    decay = np.ones((6, 6, 4), dtype=np.float32)
    store.write_array("decay/ch0", decay, is_decay=True)
    out = store.read_array("decay/ch0", view_bin=2)
    assert out.shape == (3, 3, 4)
    assert np.all(out == 4)


def test_read_array_labels_uses_mode(store):
    """/labels/<name> at view_bin=2 mode-downsamples (ties -> 0)."""
    # 4x4 block: top-left 2x2 has three 1s, one 2 -> mode 1
    #             top-right 2x2 has two 1s, two 2s -> tie -> 0
    arr = np.array(
        [
            [1, 1, 1, 2],
            [1, 2, 1, 2],
            [3, 3, 4, 4],
            [3, 3, 4, 4],
        ],
        dtype=np.int32,
    )
    store.write_labels("seg", arr)
    out = store.read_array("labels/seg", view_bin=2)
    assert out.shape == (2, 2)
    assert out[0, 0] == 1
    assert out[0, 1] == 0  # tie
    assert out[1, 0] == 3
    assert out[1, 1] == 4


def test_read_array_masks_uses_majority(store):
    """/masks/<name> at view_bin=2 majority-votes."""
    # k=2 -> need >=2 of 4 set
    arr = np.array(
        [
            [1, 1, 1, 0],
            [1, 0, 0, 0],
            [0, 0, 1, 1],
            [0, 0, 1, 1],
        ],
        dtype=np.uint8,
    )
    store.write_mask("mask1", arr)
    out = store.read_array("masks/mask1", view_bin=2)
    assert out.shape == (2, 2)
    np.testing.assert_array_equal(out, [[1, 0], [0, 1]])


def test_read_array_phasor_uses_mean_bin(store):
    """/phasor/<ch>/g at view_bin=2 mean-bins (intensive, not sum)."""
    g = np.full((4, 4), 0.5, dtype=np.float32)
    store.write_array("phasor/ch0/g", g)
    out = store.read_array("phasor/ch0/g", view_bin=2)
    assert out.shape == (2, 2)
    # Mean of 0.5s is still 0.5, not 2.0.
    np.testing.assert_allclose(out, np.full((2, 2), 0.5))


def test_read_array_unknown_path_passes_through(store):
    """Paths without a known prefix are returned at native shape even
    when view_bin > 1 — we don't guess a rule."""
    data = np.arange(16).reshape(4, 4).astype(np.float32)
    store.write_array("custom/whatever", data)
    out = store.read_array("custom/whatever", view_bin=2)
    np.testing.assert_array_equal(out, data)


def test_read_array_rejects_view_bin_zero(store):
    data = np.ones((4, 4), dtype=np.float32)
    store.write_array("intensity", data)
    with pytest.raises(ValueError, match="view_bin must be >= 1"):
        store.read_array("intensity", view_bin=0)


def test_read_channel_view_bin(store):
    """read_channel respects view_bin (always /intensity, always sum-bin)."""
    data = np.ones((2, 6, 6), dtype=np.float32)
    store.write_array("intensity", data, attrs={"dims": ["C", "H", "W"]})
    out = store.read_channel("intensity", 1, view_bin=2)
    assert out.shape == (3, 3)
    assert np.all(out == 4)


def test_read_labels_view_bin_wrapper(store):
    """read_labels(name, view_bin) reaches mode_labels."""
    arr = np.zeros((4, 4), dtype=np.int32)
    arr[0:2, 0:2] = 5
    store.write_labels("seg", arr)
    out = store.read_labels("seg", view_bin=2)
    assert out.shape == (2, 2)
    assert out[0, 0] == 5
    assert out[1, 1] == 0


def test_read_mask_view_bin_wrapper(store):
    """read_mask(name, view_bin) reaches majority_vote_mask."""
    arr = np.ones((4, 4), dtype=np.uint8)
    store.write_mask("mask", arr)
    out = store.read_mask("mask", view_bin=2)
    assert out.shape == (2, 2)
    assert np.all(out == 1)


def test_read_decay_helper(store):
    """read_decay(channel) is sugar over read_array('decay/<ch>')."""
    decay = np.ones((6, 6, 4), dtype=np.float32) * 3
    store.write_array("decay/ch0", decay, is_decay=True)
    out_k1 = store.read_decay("ch0")
    np.testing.assert_array_equal(out_k1, decay)
    out_k2 = store.read_decay("ch0", view_bin=2)
    assert out_k2.shape == (3, 3, 4)
    assert np.all(out_k2 == 12)  # sum of 4 threes


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


# ── Time-lapse: leading T axis (U2) ───────────────────────────


def _make_timelapse_store(store, t=3, c=0, h=8, w=8):
    """Initialize ``store`` with a time-lapse /intensity so n_timepoints>1.

    c=0 -> (T,H,W) single channel; c>0 -> (T,C,H,W).
    """
    if c == 0:
        intensity = np.zeros((t, h, w), dtype=np.float32)
        dims = ["T", "H", "W"]
    else:
        intensity = np.zeros((t, c, h, w), dtype=np.float32)
        dims = ["T", "C", "H", "W"]
    store.write_array("intensity", intensity, attrs={"dims": dims})
    return store


def test_metadata_reports_n_timepoints_from_dims(store):
    """A leading-T /intensity surfaces n_timepoints; native_shape is (H,W)."""
    _make_timelapse_store(store, t=4, c=2, h=8, w=10)
    meta = store.metadata
    assert meta["n_timepoints"] == 4
    assert meta["native_shape"] == (8, 10)


def test_metadata_n_timepoints_defaults_to_one(store):
    """A 2D /intensity (no T axis) reports n_timepoints == 1."""
    store.write_array("intensity", np.zeros((8, 8), dtype=np.float32))
    assert store.metadata["n_timepoints"] == 1


def test_write_read_time_stacked_labels(store):
    """(T,H,W) labels round-trip whole-stack and per-frame."""
    _make_timelapse_store(store, t=3, h=8, w=8)
    labels = np.zeros((3, 8, 8), dtype=np.int32)
    labels[0, 0, 0] = 1
    labels[2, 1, 1] = 5
    store.write_labels("tracked", labels)

    full = store.read_labels("tracked")
    assert full.shape == (3, 8, 8)
    np.testing.assert_array_equal(full, labels)
    # Per-frame read returns just that (H,W) slice.
    frame2 = store.read_labels("tracked", timepoint=2)
    assert frame2.shape == (8, 8)
    assert frame2[1, 1] == 5


def test_write_time_stacked_mask(store):
    """(T,H,W) masks round-trip; per-frame read works."""
    _make_timelapse_store(store, t=2, h=8, w=8)
    mask = np.zeros((2, 8, 8), dtype=np.uint8)
    mask[1, 2, 2] = 1
    store.write_mask("roi", mask)
    assert store.read_mask("roi").shape == (2, 8, 8)
    assert store.read_mask("roi", timepoint=1)[2, 2] == 1


def test_time_stacked_labels_dims_attr(store):
    """Time-stacked labels are tagged dims=[T,H,W]."""
    _make_timelapse_store(store, t=3, h=8, w=8)
    store.write_labels("tracked", np.zeros((3, 8, 8), dtype=np.int32))
    with h5py.File(store.path, "r") as f:
        dims = [str(d) for d in f["labels/tracked"].attrs["dims"]]
    assert dims == ["T", "H", "W"]


def test_labels_3d_rejected_on_non_timelapse_dataset(store):
    """A 3D label on a single-timepoint dataset still raises (2D contract)."""
    store.write_array("intensity", np.zeros((8, 8), dtype=np.float32))
    with pytest.raises(ValueError, match="2D"):
        store.write_labels("bad", np.zeros((3, 8, 8), dtype=np.int32))


def test_time_stacked_labels_wrong_frame_count_raises(store):
    """(T,H,W) labels whose T != n_timepoints is rejected."""
    from percell4.store import LayerSizeMismatchError

    _make_timelapse_store(store, t=3, h=8, w=8)
    with pytest.raises(LayerSizeMismatchError, match="timepoints"):
        store.write_labels("tracked", np.zeros((2, 8, 8), dtype=np.int32))


def test_time_stacked_labels_wrong_hw_raises(store):
    """(T,H,W) labels whose trailing dims != native_shape is rejected."""
    from percell4.store import LayerSizeMismatchError

    _make_timelapse_store(store, t=3, h=8, w=8)
    with pytest.raises(LayerSizeMismatchError, match="native_shape"):
        store.write_labels("tracked", np.zeros((3, 8, 10), dtype=np.int32))


def test_time_stacked_labels_view_bin_per_frame(store):
    """view_bin>1 on (T,H,W) labels modes each frame and keeps T."""
    _make_timelapse_store(store, t=2, h=4, w=4)
    labels = np.ones((2, 4, 4), dtype=np.int32)
    labels[1] = 3
    store.write_labels("tracked", labels)
    binned = store.read_labels("tracked", view_bin=2)
    assert binned.shape == (2, 2, 2)
    assert binned[0, 0, 0] == 1
    assert binned[1, 0, 0] == 3


def test_time_stacked_intensity_view_bin_per_frame(store):
    """view_bin>1 on (T,C,H,W) intensity sum-bins each frame, keeps T and C."""
    intensity = np.ones((2, 2, 4, 4), dtype=np.float32)
    store.write_array("intensity", intensity, attrs={"dims": ["T", "C", "H", "W"]})
    binned = store.read_array("intensity", view_bin=2)
    assert binned.shape == (2, 2, 2, 2)
    # Each binned pixel sums 2x2 source ones.
    assert binned[0, 0, 0, 0] == 4.0


# ── Tracks (lineage tables) (U7) ──────────────────────────────


def test_write_read_list_tracks(store):
    """A lineage table round-trips through /tracks/<name>."""
    df = pd.DataFrame(
        {
            "track_id": [1, 2, 3],
            "tree_id": [0, 0, 0],
            "begin_t": [0, 1, 1],
            "end_t": [2, 2, 2],
            "parent_track_id": [-1, 1, 1],
        }
    )
    store.write_tracks("cellpose_tracked", df)

    assert store.list_tracks() == ["cellpose_tracked"]
    back = store.read_tracks("cellpose_tracked")
    pd.testing.assert_frame_equal(back, df)
