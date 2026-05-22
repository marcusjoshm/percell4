"""Time-lapse threshold-apply routes to the headless per-frame path (U9).

The interactive ThresholdQCController is single-frame and can't consume the
per-timepoint grouping dict, so time-lapse datasets use the headless per-frame
apply (U4) even in interactive mode. The interactive dilute controller is
skipped for time-lapse datasets (no per-frame equivalent).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from percell4.gui.workflows.single_cell.runner import SingleCellThresholdingRunner
from percell4.store import DatasetStore
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    DiluteSettings,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)


def _store(path, n_timepoints, labels):
    s = DatasetStore(path)
    s.create(metadata={"channel_names": ["GFP"]})
    if n_timepoints > 1:
        s.write_array("intensity", np.zeros((n_timepoints, 12, 12), dtype=np.float32),
                      attrs={"dims": ["T", "H", "W"]})
        cell = np.zeros((n_timepoints, 12, 12), dtype=np.int32)
        cell[:, 2:5, 2:5] = 1
    else:
        s.write_array("intensity", np.zeros((12, 12), dtype=np.float32),
                      attrs={"dims": ["H", "W"]})
        cell = np.zeros((12, 12), dtype=np.int32)
        cell[2:5, 2:5] = 1
    for name in labels:
        s.write_labels(name, cell)


def _runner(entries, tmp_path, *, dilute=False):
    cfg = WorkflowConfig(
        datasets=entries,
        cellpose=CellposeSettings(diameter=8.0, gpu=False, min_size=5),
        thresholding_rounds=[ThresholdingRound(
            name="GFP_split", channel="GFP", metric="mean_intensity",
            algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2,
            gaussian_sigma=0.0)],
        selected_csv_columns=["GFP_mean_intensity"],
        output_parent=tmp_path / "runs",
        dilute_settings=(
            DiluteSettings(mask_name="dilute", dilation_radius_px=2,
                           channel="GFP", metric="mean_intensity",
                           algorithm=ThresholdAlgorithm.KMEANS)
            if dilute else None
        ),
    )
    meta = RunMetadata(run_id="r", run_folder=Path("/tmp/r"),
                       started_at=datetime.now(UTC), intersected_channels=["GFP"])
    # interactive_qc=True so the interactive vs headless branch is exercised.
    return SingleCellThresholdingRunner(config=cfg, metadata=meta, interactive_qc=True)


def _phases(runner):
    # Pre-seed the grouping cache so the threshold-apply branch is reached
    # (handlers don't run during pure generator iteration).
    for entry in runner._working_entries:
        runner._grouping_cache[(entry.name, "GFP_split")] = object()
    by_ds: dict[str, list[str]] = {}
    for req in runner._phase_generator():
        by_ds.setdefault(req.dataset_name, []).append(req.phase_name)
    return by_ds


def test_timelapse_uses_headless_apply_in_interactive_mode(tmp_path):
    p = tmp_path / "TL.h5"
    _store(p, n_timepoints=3, labels=["cellpose", "cellpose_tracked"])
    runner = _runner(
        [WorkflowDatasetEntry(name="TL", source=DatasetSource.H5_EXISTING,
                              h5_path=p, channel_names=["GFP"])],
        tmp_path,
    )
    by_ds = _phases(runner)
    assert "threshold_apply:GFP_split" in by_ds["TL"]      # headless per-frame
    assert "threshold_qc:GFP_split" not in by_ds["TL"]      # not interactive


def test_single_timepoint_keeps_interactive_threshold_qc(tmp_path):
    p = tmp_path / "ST.h5"
    _store(p, n_timepoints=1, labels=["cellpose"])
    runner = _runner(
        [WorkflowDatasetEntry(name="ST", source=DatasetSource.H5_EXISTING,
                              h5_path=p, channel_names=["GFP"])],
        tmp_path,
    )
    by_ds = _phases(runner)
    assert "threshold_qc:GFP_split" in by_ds["ST"]          # interactive
    assert "threshold_apply:GFP_split" not in by_ds["ST"]


def test_dilute_skipped_for_timelapse(tmp_path):
    p = tmp_path / "TL.h5"
    _store(p, n_timepoints=3, labels=["cellpose", "cellpose_tracked"])
    runner = _runner(
        [WorkflowDatasetEntry(name="TL", source=DatasetSource.H5_EXISTING,
                              h5_path=p, channel_names=["GFP"])],
        tmp_path, dilute=True,
    )
    by_ds = _phases(runner)
    assert "dilute" not in by_ds.get("TL", [])
