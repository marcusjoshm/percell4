"""Generator auto-skips Cellpose for datasets with existing segmentation (U13)."""

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


def _make_h5(path: Path, labels: dict[str, np.ndarray] | None = None):
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array("intensity", np.zeros((20, 20), dtype=np.float32),
                      attrs={"dims": ["H", "W"]})
    for name, arr in (labels or {}).items():
        store.write_labels(name, arr)


def _config(entries, tmp_path):
    return WorkflowConfig(
        datasets=entries,
        cellpose=CellposeSettings(diameter=8.0, gpu=False, min_size=5),
        thresholding_rounds=[
            ThresholdingRound(
                name="GFP_split", channel="GFP", metric="mean_intensity",
                algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2,
                gaussian_sigma=0.0,
            ),
        ],
        selected_csv_columns=["GFP_mean_intensity"],
        output_parent=tmp_path / "runs",
    )


def _runner(cfg):
    meta = RunMetadata(run_id="r", run_folder=Path("/tmp/r"),
                       started_at=datetime.now(UTC), intersected_channels=["GFP"])
    return SingleCellThresholdingRunner(config=cfg, metadata=meta, interactive_qc=False)


def _phases_by_dataset(runner):
    by_ds: dict[str, list[str]] = {}
    for req in runner._phase_generator():
        by_ds.setdefault(req.dataset_name, []).append(req.phase_name)
    return by_ds


def test_presegmented_dataset_skips_segment(qtbot, tmp_path):
    p = tmp_path / "DS1.h5"
    cell = np.zeros((20, 20), dtype=np.int32)
    cell[5:9, 5:9] = 1
    _make_h5(p, labels={"cellpose": cell})
    cfg = _config(
        [WorkflowDatasetEntry(name="DS1", source=DatasetSource.H5_EXISTING,
                              h5_path=p, channel_names=["GFP"])],
        tmp_path,
    )
    runner = _runner(cfg)

    by_ds = _phases_by_dataset(runner)

    assert "segment" not in by_ds.get("DS1", [])
    assert "seg_qc" not in by_ds.get("DS1", [])
    assert runner._effective_seg["DS1"] == "cellpose"


def test_unsegmented_dataset_still_segments(qtbot, tmp_path):
    p = tmp_path / "DS2.h5"
    _make_h5(p, labels=None)  # no /labels
    cfg = _config(
        [WorkflowDatasetEntry(name="DS2", source=DatasetSource.H5_EXISTING,
                              h5_path=p, channel_names=["GFP"])],
        tmp_path,
    )
    runner = _runner(cfg)

    by_ds = _phases_by_dataset(runner)

    assert "segment" in by_ds.get("DS2", [])
    assert "DS2" not in runner._effective_seg


def test_tracked_layer_preferred_when_present(qtbot, tmp_path):
    p = tmp_path / "DS3.h5"
    cell = np.zeros((20, 20), dtype=np.int32)
    cell[5:9, 5:9] = 1
    _make_h5(p, labels={"cellpose": cell, "cellpose_tracked": cell})
    cfg = _config(
        [WorkflowDatasetEntry(name="DS3", source=DatasetSource.H5_EXISTING,
                              h5_path=p, channel_names=["GFP"])],
        tmp_path,
    )
    runner = _runner(cfg)

    by_ds = _phases_by_dataset(runner)

    assert "segment" not in by_ds.get("DS3", [])
    assert runner._effective_seg["DS3"] == "cellpose_tracked"
