"""TrackCells use case: relabel into a tracked segmentation + lineage table."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from percell4.adapters.laptrack_tracker import LaptrackTracker
from percell4.application.session import Session
from percell4.application.use_cases.track_cells import TrackCells
from percell4.domain.dataset import DatasetHandle


class FakeRepo:
    def __init__(self, raw_labels: dict[str, np.ndarray]) -> None:
        self.labels = dict(raw_labels)
        self.masks: dict[str, np.ndarray] = {}
        self.tracks: dict[str, pd.DataFrame] = {}

    def read_labels(self, handle, name, view_bin=1):
        return self.labels[name]

    def write_labels(self, handle, name, data, attrs=None):
        self.labels[name] = data

    def list_labels(self, handle):
        return list(self.labels.keys())

    def list_masks(self, handle):
        return list(self.masks.keys())

    def write_tracks(self, handle, name, df):
        self.tracks[name] = df


def _session(n_timepoints):
    s = Session()
    s.set_dataset(
        DatasetHandle(path=Path("/tmp/movie.h5"), metadata={"n_timepoints": n_timepoints})
    )
    return s


def _linking_stack():
    """One cell overlapping itself across 3 frames -> single track."""
    raw = np.zeros((3, 12, 12), dtype=np.int32)
    raw[0, 1:5, 1:5] = 5
    raw[1, 1:5, 1:5] = 2
    raw[2, 2:6, 1:5] = 9
    return raw


def _division_stack():
    raw = np.zeros((2, 12, 12), dtype=np.int32)
    raw[0, 3:9, 3:9] = 1
    raw[1, 3:9, 3:5] = 2
    raw[1, 3:9, 7:9] = 3
    return raw


def test_tracked_labels_are_stable_across_frames():
    raw = _linking_stack()
    repo = FakeRepo({"cellpose_raw": raw})
    uc = TrackCells(repo, _session(3), LaptrackTracker())

    result = uc.execute("cellpose_raw")

    tracked = repo.labels[result.seg_name]
    assert tracked.shape == (3, 12, 12)
    # The same physical cell carries one stable, non-zero id in every frame.
    ids = [int(tracked[t][tracked[t] > 0][0]) for t in range(3)]
    assert len(set(ids)) == 1
    assert ids[0] >= 1  # 1-based; 0 is background
    assert result.n_tracks == 1
    assert result.seg_name == "cellpose_raw_tracked"


def test_tracked_segmentation_auto_selected_and_listed():
    repo = FakeRepo({"cellpose_raw": _linking_stack()})
    session = _session(3)
    uc = TrackCells(repo, session, LaptrackTracker())

    result = uc.execute("cellpose_raw")

    assert result.seg_name in repo.labels
    assert session.active_segmentation == result.seg_name
    # Lineage table persisted alongside the tracked labels.
    assert result.seg_name in repo.tracks


def test_division_writes_lineage_linking_parent_to_daughters():
    repo = FakeRepo({"raw": _division_stack()})
    uc = TrackCells(repo, _session(2), LaptrackTracker())

    result = uc.execute("raw")

    assert result.n_divisions == 1  # one dividing parent
    lineage = repo.tracks[result.seg_name].set_index("track_id")
    # Two daughters point at the same parent track id.
    daughters = lineage[lineage["parent_track_id"] != -1]
    assert len(daughters) == 2
    assert daughters["parent_track_id"].nunique() == 1
    # Track ids are 1-based (no track uses 0, which is background).
    assert (lineage.index >= 1).all()


def test_tracking_single_timepoint_raises():
    repo = FakeRepo({"raw": np.zeros((1, 8, 8), dtype=np.int32)})
    uc = TrackCells(repo, _session(1), LaptrackTracker())
    with pytest.raises(ValueError, match="time-lapse"):
        uc.execute("raw")


def test_tracking_2d_raw_segmentation_raises():
    repo = FakeRepo({"raw2d": np.zeros((8, 8), dtype=np.int32)})
    uc = TrackCells(repo, _session(3), LaptrackTracker())
    with pytest.raises(ValueError, match=r"\(T, H, W\)"):
        uc.execute("raw2d")
