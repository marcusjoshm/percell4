"""Time-aware measurement: timepoint column + lineage join + backward compat."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from percell4.application.session import Session
from percell4.application.use_cases.measure_cells import MeasureCells
from percell4.domain.dataset import DatasetHandle


class FakeTimelapseRepo:
    """Repo backed by a (T,H,W) tracked segmentation + per-frame channel."""

    def __init__(self, channel_stack, label_stack, tracks=None):
        self._channel = channel_stack  # (T,H,W)
        self._labels = label_stack      # (T,H,W)
        self._tracks = tracks           # DataFrame or None
        self.written = None

    def read_channel_images(self, handle, view_bin=1, timepoint=None):
        t = 0 if timepoint is None else timepoint
        return {"GFP": self._channel[t].astype(np.float32)}

    def read_labels(self, handle, name, view_bin=1, timepoint=None):
        if timepoint is None:
            return self._labels
        return self._labels[timepoint]

    def read_mask(self, handle, name, view_bin=1):
        raise KeyError(name)

    def read_tracks(self, handle, name):
        if self._tracks is None:
            raise KeyError(name)
        return self._tracks

    def read_group_columns(self, handle):
        return None

    def write_measurements(self, handle, df):
        self.written = df


def _session(n_timepoints, seg="tracked"):
    s = Session()
    s.set_dataset(
        DatasetHandle(path=Path("/tmp/movie.h5"), metadata={"n_timepoints": n_timepoints})
    )
    s.set_active_segmentation(seg)
    return s


def _two_cell_frame(a=1, b=2):
    """20x20 frame with two interior cells labelled a and b."""
    f = np.zeros((20, 20), dtype=np.int32)
    f[4:8, 4:8] = a
    f[12:16, 12:16] = b
    return f


def test_measurement_has_timepoint_column_per_frame():
    labels = np.stack([_two_cell_frame(1, 2), _two_cell_frame(1, 2)], axis=0)
    channel = np.ones((2, 20, 20), dtype=np.float32)
    repo = FakeTimelapseRepo(channel, labels)
    uc = MeasureCells(repo, _session(2))

    df = uc.execute(metrics=["area"])

    assert "timepoint" in df.columns
    assert sorted(df["timepoint"].unique().tolist()) == [0, 1]
    # Two cells per frame x 2 frames = 4 rows.
    assert len(df) == 4


def test_lineage_columns_joined_for_tracked_segmentation():
    # Labels are track ids (1,2). Lineage: track 2 is a daughter of track 1.
    labels = np.stack([_two_cell_frame(1, 2), _two_cell_frame(1, 2)], axis=0)
    channel = np.ones((2, 20, 20), dtype=np.float32)
    tracks = pd.DataFrame(
        {
            "track_id": [1, 2],
            "tree_id": [0, 0],
            "begin_t": [0, 0],
            "end_t": [1, 1],
            "parent_track_id": [-1, 1],
        }
    )
    repo = FakeTimelapseRepo(channel, labels, tracks=tracks)
    uc = MeasureCells(repo, _session(2))

    df = uc.execute(metrics=["area"])

    assert "track_id" in df.columns
    assert "tree_id" in df.columns
    assert "parent_track_id" in df.columns
    # For the tracked seg, track_id == label.
    assert (df["track_id"] == df["label"]).all()
    # Track 2's rows carry parent 1; track 1's rows are roots (-1).
    assert set(df.loc[df["track_id"] == 2, "parent_track_id"]) == {1}
    assert set(df.loc[df["track_id"] == 1, "parent_track_id"]) == {-1}


def test_raw_segmentation_gets_timepoint_only_no_track_columns():
    labels = np.stack([_two_cell_frame(1, 2), _two_cell_frame(1, 2)], axis=0)
    channel = np.ones((2, 20, 20), dtype=np.float32)
    repo = FakeTimelapseRepo(channel, labels, tracks=None)  # untracked
    uc = MeasureCells(repo, _session(2))

    df = uc.execute(metrics=["area"])

    assert "timepoint" in df.columns
    assert "track_id" not in df.columns
    assert "parent_track_id" not in df.columns


def test_frame_with_no_cells_contributes_no_rows():
    # Frame 1 is empty (all cells died / left FOV).
    labels = np.stack(
        [_two_cell_frame(1, 2), np.zeros((20, 20), dtype=np.int32)], axis=0
    )
    channel = np.ones((2, 20, 20), dtype=np.float32)
    repo = FakeTimelapseRepo(channel, labels)
    uc = MeasureCells(repo, _session(2))

    df = uc.execute(metrics=["area"])

    # Only frame 0 contributes -> per-timepoint counts legitimately differ.
    assert sorted(df["timepoint"].unique().tolist()) == [0]
    assert len(df) == 2


def test_single_timepoint_has_no_timepoint_column():
    """Backward compat: single-timepoint measurement output is unchanged."""
    label_2d = _two_cell_frame(1, 2)

    class Single:
        def read_channel_images(self, handle, view_bin=1, timepoint=None):
            return {"GFP": np.ones((20, 20), dtype=np.float32)}

        def read_labels(self, handle, name, view_bin=1, timepoint=None):
            return label_2d

        def read_mask(self, handle, name, view_bin=1):
            raise KeyError(name)

        def read_group_columns(self, handle):
            return None

        def write_measurements(self, handle, df):
            pass

    df = MeasureCells(Single(), _session(1)).execute(metrics=["area"])
    assert "timepoint" not in df.columns
    assert "track_id" not in df.columns
    assert len(df) == 2
