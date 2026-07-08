"""Per-dataset effective segmentation name resolution (U1)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from percell4.gui.workflows.single_cell.runner import SingleCellThresholdingRunner
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)


def _config(tmp_path: Path, seg_name: str = "cellpose_qc") -> WorkflowConfig:
    return WorkflowConfig(
        datasets=[
            WorkflowDatasetEntry(
                name="DS1", source=DatasetSource.H5_EXISTING,
                h5_path=tmp_path / "DS1.h5", channel_names=["GFP"],
            ),
            WorkflowDatasetEntry(
                name="DS2", source=DatasetSource.H5_EXISTING,
                h5_path=tmp_path / "DS2.h5", channel_names=["GFP"],
            ),
        ],
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
        cellpose_segmentation_name=seg_name,
    )


def _runner(cfg) -> SingleCellThresholdingRunner:
    meta = RunMetadata(
        run_id="r", run_folder=Path("/tmp/r"), started_at=datetime.now(UTC),
        intersected_channels=["GFP"],
    )
    return SingleCellThresholdingRunner(config=cfg, metadata=meta, interactive_qc=False)


def _entry(runner, name):
    return next(e for e in runner._working_entries if e.name == name)


def test_default_resolves_to_config_seg_name(qtbot, tmp_path):
    cfg = _config(tmp_path, seg_name="cellpose_qc")
    runner = _runner(cfg)
    # No overrides -> both datasets resolve to the config default (parity).
    assert runner._seg_name_for(_entry(runner, "DS1")) == "cellpose_qc"
    assert runner._seg_name_for(_entry(runner, "DS2")) == "cellpose_qc"


def test_override_affects_only_that_dataset(qtbot, tmp_path):
    cfg = _config(tmp_path, seg_name="cellpose_qc")
    runner = _runner(cfg)
    runner._effective_seg["DS1"] = "cellpose_qc_tracked"
    assert runner._seg_name_for(_entry(runner, "DS1")) == "cellpose_qc_tracked"
    # DS2 untouched -> still the config default.
    assert runner._seg_name_for(_entry(runner, "DS2")) == "cellpose_qc"


def test_effective_seg_map_starts_empty(qtbot, tmp_path):
    runner = _runner(_config(tmp_path))
    assert runner._effective_seg == {}
