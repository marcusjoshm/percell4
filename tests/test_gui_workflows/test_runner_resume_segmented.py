"""Resume-from-segmented: per-dataset segmentation overrides (U12)."""

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


def _h5(path, labels):
    s = DatasetStore(path)
    s.create(metadata={"channel_names": ["GFP"]})
    s.write_array("intensity", np.zeros((20, 20), dtype=np.float32),
                  attrs={"dims": ["H", "W"]})
    cell = np.zeros((20, 20), dtype=np.int32)
    cell[5:9, 5:9] = 1
    for name in labels:
        s.write_labels(name, cell)


def _runner(entries, tmp_path, overrides=None):
    cfg = WorkflowConfig(
        datasets=entries,
        cellpose=CellposeSettings(diameter=8.0, gpu=False, min_size=5),
        thresholding_rounds=[ThresholdingRound(
            name="GFP_split", channel="GFP", metric="mean_intensity",
            algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2,
            gaussian_sigma=0.0)],
        selected_csv_columns=["GFP_mean_intensity"],
        output_parent=tmp_path / "runs",
    )
    meta = RunMetadata(run_id="r", run_folder=Path("/tmp/r"),
                       started_at=datetime.now(UTC), intersected_channels=["GFP"])
    return SingleCellThresholdingRunner(
        config=cfg, metadata=meta, interactive_qc=False,
        segmentation_overrides=overrides,
    )


def _phases(runner):
    by_ds: dict[str, list[str]] = {}
    for req in runner._phase_generator():
        by_ds.setdefault(req.dataset_name, []).append(req.phase_name)
    return by_ds


def test_override_skips_segment_and_uses_chosen_segmentation(tmp_path):
    p = tmp_path / "DS1.h5"
    _h5(p, ["cellpose", "manual"])  # two untracked segmentations
    entry = WorkflowDatasetEntry(name="DS1", source=DatasetSource.H5_EXISTING,
                                 h5_path=p, channel_names=["GFP"])
    # User explicitly picks "manual" (not the auto-default "cellpose").
    runner = _runner([entry], tmp_path, overrides={"DS1": "manual"})

    by_ds = _phases(runner)

    assert "segment" not in by_ds.get("DS1", [])
    assert runner._effective_seg["DS1"] == "manual"


def test_no_override_falls_back_to_autodetect(tmp_path):
    p = tmp_path / "DS1.h5"
    _h5(p, ["cellpose"])
    entry = WorkflowDatasetEntry(name="DS1", source=DatasetSource.H5_EXISTING,
                                 h5_path=p, channel_names=["GFP"])
    runner = _runner([entry], tmp_path)  # no overrides

    by_ds = _phases(runner)

    assert "segment" not in by_ds.get("DS1", [])
    assert runner._effective_seg["DS1"] == "cellpose"  # auto-detected
