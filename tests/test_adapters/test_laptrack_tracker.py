"""LaptrackTracker adapter: linking, death/appearance, division (real laptrack)."""

from __future__ import annotations

import numpy as np
import pytest

from percell4.adapters.laptrack_tracker import LaptrackTracker
from percell4.ports.tracker import SPLIT_COLUMNS, TRACK_COLUMNS


def _square(value, y0, x0, size=3, shape=(12, 12)):
    arr = np.zeros(shape, dtype=np.int32)
    arr[y0 : y0 + size, x0 : x0 + size] = value
    return arr


def test_single_cell_links_across_frames():
    # One cell overlapping itself across 3 frames -> one track spanning all.
    frames = np.stack(
        [_square(5, 1, 1), _square(2, 1, 1), _square(9, 2, 1)], axis=0
    )
    result = LaptrackTracker().track(frames)

    assert list(result.track_df.columns) == TRACK_COLUMNS
    assert result.track_df["timepoint"].tolist() == [0, 1, 2]
    # Same physical cell -> one stable track_id across all frames.
    assert result.track_df["track_id"].nunique() == 1
    assert result.split_df.empty


def test_cell_death_track_ends_early():
    # Cell present at t0,t1 then gone at t2 -> its track ends at t1, no error.
    frames = np.stack(
        [_square(1, 1, 1), _square(1, 1, 1), np.zeros((12, 12), np.int32)],
        axis=0,
    )
    result = LaptrackTracker().track(frames)
    # Track only spans frames 0 and 1.
    assert sorted(result.track_df["timepoint"].unique().tolist()) == [0, 1]


def test_cell_appears_midway():
    # A new cell only from t1 -> a track that begins at t1.
    frames = np.stack(
        [np.zeros((12, 12), np.int32), _square(1, 5, 5), _square(1, 5, 5)],
        axis=0,
    )
    result = LaptrackTracker().track(frames)
    tp = sorted(result.track_df["timepoint"].unique().tolist())
    assert tp == [1, 2]


def test_division_produces_split_records():
    # One parent at t0 splits into two overlapping daughters at t1.
    f0 = np.zeros((12, 12), np.int32)
    f0[3:9, 3:9] = 1
    f1 = np.zeros((12, 12), np.int32)
    f1[3:9, 3:5] = 2
    f1[3:9, 7:9] = 3
    result = LaptrackTracker().track(np.stack([f0, f1], axis=0))

    assert list(result.split_df.columns) == SPLIT_COLUMNS
    assert len(result.split_df) == 2  # parent -> two daughters
    # Exactly one parent, two distinct children.
    assert result.split_df["parent_track_id"].nunique() == 1
    assert result.split_df["child_track_id"].nunique() == 2
    # Parent + daughters share one tree.
    assert result.track_df["tree_id"].nunique() == 1


def test_empty_stack_returns_empty_frames():
    frames = np.zeros((2, 8, 8), dtype=np.int32)
    result = LaptrackTracker().track(frames)
    assert result.track_df.empty
    assert result.split_df.empty
    assert list(result.track_df.columns) == TRACK_COLUMNS


def test_non_3d_input_raises():
    with pytest.raises(ValueError, match=r"\(T, H, W\)"):
        LaptrackTracker().track(np.zeros((8, 8), dtype=np.int32))
