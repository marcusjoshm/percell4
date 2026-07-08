"""Workflow tracking phase: track_one on a DatasetStore (U3)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from percell4.ports.tracker import TrackingResult
from percell4.store import DatasetStore
from percell4.workflows.failures import DatasetFailure
from percell4.workflows.phases import track_one


class FakeTracker:
    def __init__(self, track_df, split_df):
        self._t, self._s = track_df, split_df

    def track(self, stack):
        return TrackingResult(track_df=self._t, split_df=self._s)


def _empty_split():
    return pd.DataFrame(columns=["parent_track_id", "child_track_id"])


def _timelapse_store(path: Path, raw: np.ndarray) -> DatasetStore:
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP"]})
    t = raw.shape[0]
    store.write_array(
        "intensity", np.zeros((t, *raw.shape[1:]), dtype=np.float32),
        attrs={"dims": ["T", "H", "W"]},
    )
    store.write_labels("cellpose_qc", raw)
    return store


def test_track_one_writes_tracked_segmentation_and_lineage(tmp_path):
    raw = np.zeros((2, 8, 8), dtype=np.int32)
    raw[0, 1:4, 1:4] = 1
    raw[1, 1:4, 1:4] = 1  # same cell both frames
    store = _timelapse_store(tmp_path / "tl.h5", raw)
    # 0-based track_id 0 across both frames -> stored as 1 (1-based).
    track_df = pd.DataFrame(
        [(0, 1, 0, 0), (1, 1, 0, 0)],
        columns=["timepoint", "label", "track_id", "tree_id"],
    )
    fake = FakeTracker(track_df, _empty_split())

    name, failure, msg = track_one(store, "cellpose_qc", tracker=fake)

    assert failure is None
    assert name == "cellpose_qc_tracked"
    tracked = store.read_labels("cellpose_qc_tracked")
    assert tracked.shape == (2, 8, 8)
    assert tracked[0, 1, 1] == 1 and tracked[1, 1, 1] == 1  # stable track id
    assert "cellpose_qc_tracked" in store.list_tracks()
    # Raw segmentation preserved.
    assert "cellpose_qc" in store.list_labels()


def test_track_one_rejects_2d_segmentation(tmp_path):
    store = DatasetStore(tmp_path / "still.h5")
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array("intensity", np.zeros((8, 8), dtype=np.float32),
                      attrs={"dims": ["H", "W"]})
    store.write_labels("cellpose_qc", np.zeros((8, 8), dtype=np.int32))

    name, failure, msg = track_one(store, "cellpose_qc", tracker=FakeTracker(None, None))

    assert name is None
    assert failure is DatasetFailure.TRACKING_ERROR
    assert "not a (T, H, W)" in msg


def test_track_one_missing_segmentation_fails(tmp_path):
    store = DatasetStore(tmp_path / "tl.h5")
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array("intensity", np.zeros((2, 8, 8), dtype=np.float32),
                      attrs={"dims": ["T", "H", "W"]})

    name, failure, _msg = track_one(store, "nonexistent", tracker=FakeTracker(None, None))

    assert name is None
    assert failure is DatasetFailure.TRACKING_ERROR
