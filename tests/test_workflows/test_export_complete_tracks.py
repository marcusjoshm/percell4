"""export_run writes complete_tracks.csv for tracked runs (U7)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from percell4.workflows.artifacts import create_run_folder
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)
from percell4.workflows.phases import export_run, write_staging_parquet


def _config(output_parent: Path) -> WorkflowConfig:
    return WorkflowConfig(
        datasets=[WorkflowDatasetEntry(name="DS1", source=DatasetSource.H5_EXISTING,
                                       h5_path=output_parent / "DS1.h5",
                                       channel_names=["GFP"])],
        cellpose=CellposeSettings(diameter=8.0, gpu=False, min_size=5),
        thresholding_rounds=[ThresholdingRound(
            name="GFP_split", channel="GFP", metric="mean_intensity",
            algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2,
            gaussian_sigma=0.0)],
        selected_csv_columns=["GFP_mean_intensity"],
        output_parent=output_parent,
    )


def _staging_df():
    # 3 timepoints. track 1 complete; track 4 divides into 5; track 7 has a gap.
    rows = [
        (1, 0, 1, -1, 100.0), (1, 1, 1, -1, 110.0), (1, 2, 1, -1, 120.0),
        (4, 0, 4, -1, 50.0),
        (5, 1, 5, 4, 30.0), (5, 2, 5, 4, 31.0),
        (7, 0, 7, -1, 70.0), (7, 2, 7, -1, 72.0),
    ]
    return pd.DataFrame(
        rows,
        columns=["cell_id", "timepoint", "track_id", "parent_track_id",
                 "GFP_mean_intensity"],
    ).assign(label=lambda d: d["track_id"])


def test_export_writes_complete_tracks_csv(tmp_path):
    run_folder = create_run_folder(tmp_path / "runs")
    write_staging_parquet(run_folder, "DS1", _staging_df())
    cfg = _config(tmp_path)
    meta = RunMetadata(run_id=run_folder.name, run_folder=run_folder,
                       started_at=datetime.now(UTC), intersected_channels=["GFP"])

    failure, _msg = export_run(run_folder, cfg, meta)

    assert failure is None
    out = run_folder / "complete_tracks.csv"
    assert out.is_file()
    df = pd.read_csv(out)
    # Only track 1 is complete (every timepoint, no division, no gap).
    assert set(df["track_id"].unique()) == {1}
    assert sorted(df["timepoint"]) == [0, 1, 2]


def test_complete_tracks_respects_selected_columns(tmp_path):
    """complete_tracks.csv carries only the selected columns + identity/track
    columns — not every measured column (regression: it dumped all 50+)."""
    run_folder = create_run_folder(tmp_path / "runs")
    # Add an unselected measurement column that must be filtered out, plus a
    # tree_id identity column that must be kept.
    df = _staging_df()
    df["GFP_max_intensity"] = 999.0          # measured but NOT selected
    df["round_1_particle_count"] = 3          # measured but NOT selected
    df["tree_id"] = df["track_id"]            # tracking identity -> kept
    write_staging_parquet(run_folder, "DS1", df)
    cfg = _config(tmp_path)  # selected_csv_columns = ["GFP_mean_intensity"]
    meta = RunMetadata(run_id=run_folder.name, run_folder=run_folder,
                       started_at=datetime.now(UTC), intersected_channels=["GFP"])

    failure, _msg = export_run(run_folder, cfg, meta)
    assert failure is None

    out = pd.read_csv(run_folder / "complete_tracks.csv")
    # Selected measurement column present; unselected ones absent.
    assert "GFP_mean_intensity" in out.columns
    assert "GFP_max_intensity" not in out.columns
    assert "round_1_particle_count" not in out.columns
    # Identity + per-timepoint tracking columns are always kept.
    for col in ("dataset", "cell_id", "label", "timepoint", "track_id",
                "tree_id", "parent_track_id"):
        assert col in out.columns, col
    # Still per-timepoint: one row per timepoint for the one complete track.
    assert sorted(out["timepoint"]) == [0, 1, 2]


def test_export_no_complete_tracks_file_when_untracked(tmp_path):
    run_folder = create_run_folder(tmp_path / "runs")
    # A non-time-lapse staging df: no track_id column.
    df = pd.DataFrame({
        "cell_id": [1, 2], "label": [1, 2], "GFP_mean_intensity": [100.0, 200.0],
    })
    write_staging_parquet(run_folder, "DS1", df)
    cfg = _config(tmp_path)
    meta = RunMetadata(run_id=run_folder.name, run_folder=run_folder,
                       started_at=datetime.now(UTC), intersected_channels=["GFP"])

    failure, _msg = export_run(run_folder, cfg, meta)

    assert failure is None
    # No track_id column -> no complete_tracks.csv emitted.
    assert not (run_folder / "complete_tracks.csv").exists()
