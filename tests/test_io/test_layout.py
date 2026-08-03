"""Tests for intensity → display-layer splitting (T-vs-C disambiguation)."""

from __future__ import annotations

import numpy as np

from percell4.domain.io.layout import (
    plan_channel_deletion,
    split_channels_2d,
    split_intensity_layers,
)

# ── split_channels_2d (single-timepoint plane) ────────────────


def test_split_channels_2d_single():
    plane = np.zeros((8, 8), dtype=np.float32)
    out = split_channels_2d(plane, ["GFP"])
    assert len(out) == 1
    assert out[0][0] == "GFP"
    assert out[0][1].shape == (8, 8)


def test_split_channels_2d_multi():
    plane = np.zeros((3, 8, 8), dtype=np.float32)
    out = split_channels_2d(plane, ["a", "b", "c"])
    assert [n for n, _ in out] == ["a", "b", "c"]
    assert all(arr.shape == (8, 8) for _, arr in out)


def test_split_channels_2d_falls_back_to_single_for_large_leading():
    # A leading dim > 20 is not treated as channels.
    plane = np.zeros((30, 4, 4), dtype=np.float32)
    out = split_channels_2d(plane, [])
    assert len(out) == 1
    assert out[0][0] == "Intensity"


# ── split_intensity_layers (T-vs-C disambiguation) ────────────


def test_timelapse_single_channel_keeps_T_axis():
    intensity = np.zeros((5, 8, 8), dtype=np.float32)  # (T,H,W)
    out = split_intensity_layers(intensity, ["GFP"], n_timepoints=5)
    assert len(out) == 1
    name, arr = out[0]
    assert name == "GFP"
    assert arr.shape == (5, 8, 8)  # T axis preserved -> napari slider


def test_timelapse_multichannel_splits_C_keeps_T():
    intensity = np.zeros((5, 2, 8, 8), dtype=np.float32)  # (T,C,H,W)
    out = split_intensity_layers(intensity, ["GFP", "DAPI"], n_timepoints=5)
    assert [n for n, _ in out] == ["GFP", "DAPI"]
    # Each channel layer keeps its time axis as (T,H,W).
    assert all(arr.shape == (5, 8, 8) for _, arr in out)


def test_channel_names_as_numpy_array_does_not_crash():
    # h5py returns a numpy string array for a multi-element channel_names
    # attr; `channel_names or []` would raise "ambiguous truth value".
    intensity = np.zeros((3, 8, 8), dtype=np.float32)
    names = np.array(["DAPI", "GFP", "RFP"])
    out = split_intensity_layers(intensity, names, n_timepoints=1)
    assert [str(n) for n, _ in out] == ["DAPI", "GFP", "RFP"]
    # split_channels_2d shares the same guard.
    out2 = split_channels_2d(intensity, names)
    assert [str(n) for n, _ in out2] == ["DAPI", "GFP", "RFP"]


def test_non_timelapse_3d_splits_into_channels():
    # n_timepoints == 1: (C,H,W) is channels, NOT a time stack.
    intensity = np.zeros((3, 8, 8), dtype=np.float32)
    out = split_intensity_layers(intensity, ["a", "b", "c"], n_timepoints=1)
    assert [n for n, _ in out] == ["a", "b", "c"]
    assert all(arr.shape == (8, 8) for _, arr in out)


def test_non_timelapse_2d_single_layer():
    intensity = np.zeros((8, 8), dtype=np.float32)
    out = split_intensity_layers(intensity, ["GFP"], n_timepoints=1)
    assert len(out) == 1
    assert out[0][1].shape == (8, 8)


def test_shape_ambiguity_resolved_by_n_timepoints():
    # The SAME (3,8,8) array is C-split when nt=1 and T-kept when nt=3.
    arr = np.zeros((3, 8, 8), dtype=np.float32)
    as_channels = split_intensity_layers(arr, [], n_timepoints=1)
    as_time = split_intensity_layers(arr, [], n_timepoints=3)
    assert len(as_channels) == 3
    assert len(as_time) == 1
    assert as_time[0][1].shape == (3, 8, 8)


# ── plan_channel_deletion (T-vs-C disambiguation on delete) (U7) ──


def test_plan_delete_timelapse_thw_empties_intensity():
    """A (T,H,W) single-channel time-lapse dataset is emptied, NOT sliced along
    the time axis (the corruption the old code caused)."""
    intensity = np.zeros((6, 8, 8), dtype=np.float32)
    action, arr, dims = plan_channel_deletion(intensity, slice_idx=0, n_timepoints=6)
    assert action == "delete"
    assert arr is None and dims is None


def test_plan_delete_timelapse_tchw_slices_channel_axis():
    """(T,C,H,W): deleting channel 1 slices axis=1 -> (T,C-1,H,W), keeping T."""
    intensity = np.zeros((3, 3, 8, 8), dtype=np.float32)
    for c in range(3):
        intensity[:, c] = c
    action, arr, dims = plan_channel_deletion(intensity, slice_idx=1, n_timepoints=3)
    assert action == "write"
    assert arr.shape == (3, 2, 8, 8)
    assert dims == ["T", "C", "H", "W"]
    # Channels 0 and 2 survive (1 removed); time axis intact.
    assert np.all(arr[:, 0] == 0)
    assert np.all(arr[:, 1] == 2)


def test_plan_delete_timelapse_tchw_collapses_to_thw():
    """Deleting one of two channels collapses (T,2,H,W) -> (T,H,W)."""
    intensity = np.zeros((3, 2, 8, 8), dtype=np.float32)
    intensity[:, 1] = 5
    action, arr, dims = plan_channel_deletion(intensity, slice_idx=0, n_timepoints=3)
    assert action == "write"
    assert arr.shape == (3, 8, 8)
    assert dims == ["T", "H", "W"]
    assert np.all(arr == 5)


def test_plan_delete_non_timelapse_chw_slices_axis0():
    """Non-time-lapse (C,H,W): unchanged behavior, slice axis 0 as channels."""
    intensity = np.zeros((3, 8, 8), dtype=np.float32)
    for c in range(3):
        intensity[c] = c
    action, arr, dims = plan_channel_deletion(intensity, slice_idx=1, n_timepoints=1)
    assert action == "write"
    assert arr.shape == (2, 8, 8)
    assert dims == ["C", "H", "W"]
    assert np.all(arr[0] == 0) and np.all(arr[1] == 2)


def test_plan_delete_non_timelapse_single_channel_chw_empties():
    intensity = np.zeros((1, 8, 8), dtype=np.float32)
    action, _, _ = plan_channel_deletion(intensity, slice_idx=0, n_timepoints=1)
    assert action == "delete"


def test_plan_delete_2d_empties():
    intensity = np.zeros((8, 8), dtype=np.float32)
    action, _, _ = plan_channel_deletion(intensity, slice_idx=0, n_timepoints=1)
    assert action == "delete"


def test_plan_delete_index_past_axis_is_noop():
    intensity = np.zeros((3, 2, 8, 8), dtype=np.float32)
    action, _, _ = plan_channel_deletion(intensity, slice_idx=9, n_timepoints=3)
    assert action == "noop"
