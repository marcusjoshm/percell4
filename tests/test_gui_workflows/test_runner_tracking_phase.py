"""Generator emits a track phase for time-lapse datasets after seg-QC (U3)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from percell4.gui.workflows.single_cell.runner import SingleCellThresholdingRunner
from percell4.store import DatasetStore
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)


def _store(path, n_timepoints, labels=None):
    s = DatasetStore(path)
    s.create(metadata={"channel_names": ["GFP"]})
    if n_timepoints > 1:
        s.write_array("intensity", np.zeros((n_timepoints, 12, 12), dtype=np.float32),
                      attrs={"dims": ["T", "H", "W"]})
    else:
        s.write_array("intensity", np.zeros((12, 12), dtype=np.float32),
                      attrs={"dims": ["H", "W"]})
    for name, arr in (labels or {}).items():
        s.write_labels(name, arr)


def _entry(name, path):
    return WorkflowDatasetEntry(name=name, source=DatasetSource.H5_EXISTING,
                                h5_path=path, channel_names=["GFP"])


def _runner(entries, tmp_path):
    cfg = WorkflowConfig(
        datasets=entries,
        cellpose=CellposeSettings(diameter=8.0, gpu=False, min_size=5),
        thresholding_rounds=[
            ThresholdingRound(name="GFP_split", channel="GFP",
                              metric="mean_intensity",
                              algorithm=ThresholdAlgorithm.KMEANS,
                              kmeans_n_clusters=2, gaussian_sigma=0.0),
        ],
        selected_csv_columns=["GFP_mean_intensity"],
        output_parent=tmp_path / "runs",
    )
    meta = RunMetadata(run_id="r", run_folder=Path("/tmp/r"),
                       started_at=datetime.now(UTC), intersected_channels=["GFP"])
    return SingleCellThresholdingRunner(config=cfg, metadata=meta, interactive_qc=False)


def _phases_by_dataset(runner):
    by_ds: dict[str, list[str]] = {}
    for req in runner._phase_generator():
        by_ds.setdefault(req.dataset_name, []).append(req.phase_name)
    return by_ds


def test_timelapse_dataset_gets_segment_then_track(qtbot, tmp_path):
    p = tmp_path / "TL.h5"
    _store(p, n_timepoints=3)  # no labels -> segments, then tracks
    runner = _runner([_entry("TL", p)], tmp_path)

    by_ds = _phases_by_dataset(runner)

    assert "segment" in by_ds["TL"]
    assert "track" in by_ds["TL"]
    # track comes after segment.
    assert by_ds["TL"].index("track") > by_ds["TL"].index("segment")


def test_single_timepoint_dataset_not_tracked(qtbot, tmp_path):
    p = tmp_path / "ST.h5"
    _store(p, n_timepoints=1)
    runner = _runner([_entry("ST", p)], tmp_path)

    by_ds = _phases_by_dataset(runner)

    assert "segment" in by_ds["ST"]
    assert "track" not in by_ds["ST"]


def test_timelapse_whole_field_2d_seg_skips_track(qtbot, tmp_path):
    """A 2D whole-field gate on a time-lapse dataset is time-invariant.

    It is auto-selected (Cellpose skipped) and must NOT be tracked: a 2D
    label has no per-frame evolution to track, and ``track_one`` would
    reject it ("is 2D, not a (T, H, W) stack"). Downstream per-frame phases
    broadcast the 2D label instead.
    """
    p = tmp_path / "WF.h5"
    whole = np.ones((12, 12), dtype=np.int32)
    _store(p, n_timepoints=3, labels={"whole_field": whole})
    runner = _runner([_entry("WF", p)], tmp_path)

    by_ds = _phases_by_dataset(runner)

    assert "segment" not in by_ds.get("WF", [])
    assert "track" not in by_ds.get("WF", [])
    assert runner._effective_seg["WF"] == "whole_field"


def test_already_tracked_dataset_skips_segment_and_track(qtbot, tmp_path):
    p = tmp_path / "TR.h5"
    cell = np.zeros((3, 12, 12), dtype=np.int32)
    cell[:, 2:5, 2:5] = 1
    _store(p, n_timepoints=3, labels={"cellpose": cell, "cellpose_tracked": cell})
    runner = _runner([_entry("TR", p)], tmp_path)

    by_ds = _phases_by_dataset(runner)

    assert "segment" not in by_ds.get("TR", [])
    assert "track" not in by_ds.get("TR", [])
    assert runner._effective_seg["TR"] == "cellpose_tracked"
