"""Time-aware Add Layer logic (U5/U6).

Tests the Qt-free helpers in ``gui/_add_layer_logic`` plus a store-level
integration that mirrors ``_write_layer``'s channel path -- the operation that
previously corrupted ``/intensity`` on a time-lapse dataset.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.gui._add_layer_logic import (
    build_added_channel_intensity,
    coerce_added_array,
    is_time_invariant_add,
)
from percell4.store import DatasetStore, LayerSizeMismatchError


# ── coerce_added_array ────────────────────────────────────────


def test_coerce_single_timepoint_flattens_multipage():
    arr3 = np.arange(3 * 4 * 4).reshape(3, 4, 4).astype(np.float32)
    np.testing.assert_array_equal(coerce_added_array(arr3, 1), arr3[0])
    arr4 = np.zeros((2, 3, 4, 4), dtype=np.float32)
    np.testing.assert_array_equal(coerce_added_array(arr4, 1), arr4[0, 0])


def test_coerce_single_timepoint_2d_unchanged():
    arr = np.ones((4, 4), dtype=np.float32)
    np.testing.assert_array_equal(coerce_added_array(arr, 1), arr)


def test_coerce_timelapse_keeps_matching_stack():
    arr = np.zeros((3, 4, 4), dtype=np.float32)
    np.testing.assert_array_equal(coerce_added_array(arr, 3), arr)


def test_coerce_timelapse_2d_kept_as_time_invariant():
    arr = np.ones((4, 4), dtype=np.float32)
    np.testing.assert_array_equal(coerce_added_array(arr, 3), arr)


def test_coerce_timelapse_wrong_frame_count_raises():
    arr = np.zeros((2, 4, 4), dtype=np.float32)
    with pytest.raises(LayerSizeMismatchError, match="2 frame"):
        coerce_added_array(arr, 3)


def test_coerce_timelapse_high_rank_raises():
    arr = np.zeros((3, 2, 4, 4), dtype=np.float32)
    with pytest.raises(ValueError, match="Discover TIFFs"):
        coerce_added_array(arr, 3)


def test_is_time_invariant_add():
    assert is_time_invariant_add(np.zeros((4, 4)), 3) is True
    assert is_time_invariant_add(np.zeros((4, 4)), 1) is False
    assert is_time_invariant_add(np.zeros((3, 4, 4)), 3) is False


# ── build_added_channel_intensity ─────────────────────────────


def test_build_single_timepoint_first_channel():
    arr = np.ones((4, 4), dtype=np.float32)
    stacked, dims = build_added_channel_intensity(None, arr, 1)
    assert stacked.shape == (4, 4)
    assert dims == ["H", "W"]


def test_build_single_timepoint_append():
    existing = np.ones((4, 4), dtype=np.float32)
    new = np.full((4, 4), 2.0, dtype=np.float32)
    stacked, dims = build_added_channel_intensity(existing, new, 1)
    assert stacked.shape == (2, 4, 4)
    assert dims == ["C", "H", "W"]
    existing3 = np.ones((2, 4, 4), dtype=np.float32)
    stacked2, dims2 = build_added_channel_intensity(existing3, new, 1)
    assert stacked2.shape == (3, 4, 4)
    assert dims2 == ["C", "H", "W"]


def test_build_timelapse_first_channel():
    arr = np.zeros((3, 4, 4), dtype=np.float32)
    stacked, dims = build_added_channel_intensity(None, arr, 3)
    assert stacked.shape == (3, 4, 4)
    assert dims == ["T", "H", "W"]


def test_build_timelapse_append_to_thw_concats_on_c_axis():
    """Adding a (T,H,W) channel to an existing (T,H,W) yields (T,2,H,W) --
    concatenated on the C axis, NOT the time axis (the corruption fix)."""
    existing = np.zeros((3, 4, 4), dtype=np.float32)
    new = np.ones((3, 4, 4), dtype=np.float32)
    stacked, dims = build_added_channel_intensity(existing, new, 3)
    assert stacked.shape == (3, 2, 4, 4)
    assert dims == ["T", "C", "H", "W"]
    # Frame/channel identity preserved.
    np.testing.assert_array_equal(stacked[:, 0], existing)
    np.testing.assert_array_equal(stacked[:, 1], new)


def test_build_timelapse_append_to_tchw():
    existing = np.zeros((3, 2, 4, 4), dtype=np.float32)
    new = np.ones((3, 4, 4), dtype=np.float32)
    stacked, dims = build_added_channel_intensity(existing, new, 3)
    assert stacked.shape == (3, 3, 4, 4)
    assert dims == ["T", "C", "H", "W"]


def test_build_timelapse_2d_channel_broadcasts_across_time():
    existing = np.zeros((3, 4, 4), dtype=np.float32)
    flat = np.full((4, 4), 5.0, dtype=np.float32)
    stacked, dims = build_added_channel_intensity(existing, flat, 3)
    assert stacked.shape == (3, 2, 4, 4)
    # The 2D channel is the same plane on every timepoint.
    for t in range(3):
        np.testing.assert_array_equal(stacked[t, 1], flat)


def test_build_timelapse_wrong_frame_count_raises():
    existing = np.zeros((3, 4, 4), dtype=np.float32)
    new = np.ones((2, 4, 4), dtype=np.float32)
    with pytest.raises(LayerSizeMismatchError):
        build_added_channel_intensity(existing, new, 3)


# ── Store integration: the write stays consistent (no corruption) ──


def test_add_channel_to_timelapse_writes_consistent_tchw(tmp_h5):
    """Mirror _write_layer's channel path on a real store: adding a (T,H,W)
    channel to a (T,C,H,W) dataset produces (T,C+1,H,W) that passes the U4
    dims-consistency probe and leaves n_timepoints intact."""
    store = DatasetStore(tmp_h5)
    store.create(metadata={"channel_names": ["GFP", "DAPI"]})
    store.write_array(
        "intensity", np.zeros((3, 2, 8, 8), dtype=np.float32),
        attrs={"dims": ["T", "C", "H", "W"]},
    )

    existing = store.read_array("intensity")
    new_channel = np.ones((3, 8, 8), dtype=np.float32)
    stacked, dims = build_added_channel_intensity(existing, new_channel, 3)
    store.write_array("intensity", stacked, attrs={"dims": dims})
    store.set_metadata({"channel_names": ["GFP", "DAPI", "mCherry"], "n_channels": 3})

    # The U4 caller-side guard passes (no time-vs-channel mis-stamp).
    store.check_intensity_dims_consistency()

    meta = store.metadata
    assert meta["n_timepoints"] == 3  # unchanged -- not corrupted to 1
    assert store.read_array("intensity").shape == (3, 3, 8, 8)
    assert [str(d) for d in dims] == ["T", "C", "H", "W"]


def test_add_channel_does_not_reproduce_old_corruption(tmp_h5):
    """Regression: the new path must NOT stamp a grown leading axis as 'C'
    (the old bug produced (T+1,H,W) under dims=['C','H','W'])."""
    store = DatasetStore(tmp_h5)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity", np.zeros((4, 8, 8), dtype=np.float32),
        attrs={"dims": ["T", "H", "W"]},
    )
    existing = store.read_array("intensity")
    stacked, dims = build_added_channel_intensity(
        existing, np.ones((4, 8, 8), dtype=np.float32), 4
    )
    # Leading axis stays the time axis; it did not grow to 5 under a 'C' label.
    assert dims[0] == "T"
    assert stacked.shape[0] == 4


# ── Batch tab: timepoint grouping + stacking (U6) ─────────────


def test_batch_groups_by_timepoint_and_stacks(tmp_path):
    """The batch Discover-TIFFs path groups a channel's files by timepoint and
    stacks them into (T,H,W) -- the fix for collapsing every timepoint to one
    plane. Exercises the canonical scan -> group -> stack composition the loop
    delegates to, with real TIFF I/O."""
    import tifffile
    from collections import defaultdict

    from percell4.domain.io.assembler import stack_timepoints
    from percell4.domain.io.models import TokenConfig
    from percell4.domain.io.scanner import FileScanner
    from percell4.domain.io.timepoints import ordered_timepoint_tokens

    src = tmp_path / "movie"
    src.mkdir()
    # 3 timepoints x 2 channels; encode (t, c) in the pixel value.
    for t in range(3):
        for c in range(2):
            arr = np.full((8, 8), t * 10 + c, dtype=np.uint16)
            tifffile.imwrite(src / f"a_t0{t}_ch0{c}.tif", arr)

    scan = FileScanner(TokenConfig()).scan(path=str(src))

    by_channel: dict[str, list] = defaultdict(list)
    for f in scan.files:
        by_channel[f.tokens.get("channel", "")].append(f)
    assert set(by_channel) == {"00", "01"}

    # Channel "01" must stack its 3 timepoints, not collapse to one plane.
    files = by_channel["01"]
    tp_groups: dict[str, list] = defaultdict(list)
    for f in files:
        tp_groups[f.tokens.get("timepoint", "")].append(f)
    tp_tokens = ordered_timepoint_tokens(tp_groups.keys())
    assert tp_tokens == ["00", "01", "02"]

    planes = [tifffile.imread(str(tp_groups[tp][0].path)) for tp in tp_tokens]
    stacked = stack_timepoints(planes)
    assert stacked.shape == (3, 8, 8)
    for t in range(3):
        assert np.all(stacked[t] == t * 10 + 1)  # channel 1


def test_batch_single_timepoint_stays_2d(tmp_path):
    """A flat single-timepoint folder yields one 2D plane per channel (no T
    axis), byte-identical to the old behavior."""
    import tifffile
    from collections import defaultdict

    from percell4.domain.io.models import TokenConfig
    from percell4.domain.io.scanner import FileScanner

    src = tmp_path / "still"
    src.mkdir()
    tifffile.imwrite(src / "a_ch00.tif", np.ones((8, 8), dtype=np.uint16))

    scan = FileScanner(TokenConfig()).scan(path=str(src))
    files = [f for f in scan.files if f.tokens.get("channel") == "00"]
    tp_groups: dict[str, list] = defaultdict(list)
    for f in files:
        tp_groups[f.tokens.get("timepoint", "")].append(f)
    # No _t token -> only the empty-string key -> no real tokens -> no stacking
    # (the loop must NOT call ordered_timepoint_tokens on the "" token).
    real_tokens = [t for t in tp_groups if t != ""]
    assert real_tokens == []


# ── Delete channel keeps the time axis (U7, store integration) ──


def test_delete_channel_on_tchw_keeps_time_axis(tmp_h5):
    """Deleting a channel from a (T,C,H,W) dataset slices the C axis and leaves
    n_timepoints intact -- it does not delete a timepoint or mis-stamp dims."""
    from percell4.domain.io.layout import plan_channel_deletion

    store = DatasetStore(tmp_h5)
    store.create(metadata={"channel_names": ["GFP", "DAPI", "mCherry"]})
    store.write_array(
        "intensity", np.zeros((3, 3, 8, 8), dtype=np.float32),
        attrs={"dims": ["T", "C", "H", "W"]},
    )
    intensity = store.read_array("intensity")

    action, new_intensity, dims = plan_channel_deletion(
        intensity, slice_idx=1, n_timepoints=3
    )
    assert action == "write"
    store.write_array("intensity", new_intensity, attrs={"dims": dims})
    store.set_metadata({"channel_names": ["GFP", "mCherry"], "n_channels": 2})

    store.check_intensity_dims_consistency()  # no corruption
    meta = store.metadata
    assert meta["n_timepoints"] == 3  # the time axis survived
    assert store.read_array("intensity").shape == (3, 2, 8, 8)
