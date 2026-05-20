"""Unit tests for the pure phase helpers.

These exercise each helper against small synthetic fixtures built on
top of real :class:`DatasetStore` h5 files. The runner's end-to-end
test lives under ``tests/test_gui_workflows/`` because it pulls in Qt.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from percell4.domain.measure.grouper import GroupingResult
from percell4.store import DatasetStore
from percell4.workflows.failures import DatasetFailure
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)
from percell4.workflows.phases import (
    apply_threshold_headless,
    datasets_without_failures,
    export_run,
    measure_one,
    record_failure,
    segment_one,
    threshold_compute_one,
    write_staging_parquet,
)

# ── Synthetic fixtures ──────────────────────────────────────────────────


def _make_fixture_h5(
    path: Path,
    channel_names: list[str] = None,
    n_cells: int = 12,
    size: int = 100,
) -> DatasetStore:
    """Create a small h5 with a multi-channel intensity cube and diverse cells.

    Layout:
      - (C, size, size) intensity where each channel has distinct values
      - Cells are placed as small squares with known intensities so the
        per-cell metrics are predictable.

    The grouper has a min-cells threshold of 10 before it will produce
    more than one group, so fixtures default to 12 cells on a 100×100
    grid (4 rows × 3 cols).
    """
    channel_names = channel_names or ["GFP", "RFP"]
    store = DatasetStore(path)
    store.create(metadata={"channel_names": channel_names})

    n_ch = len(channel_names)
    intensity = np.zeros((n_ch, size, size), dtype=np.float32)

    # Place `n_cells` 6×6 squares on a grid, each with increasing
    # intensity on channel 0 and random on channel 1.
    for i in range(n_cells):
        row = 5 + (i // 3) * 22
        col = 5 + (i % 3) * 22
        # Channel 0: increasing intensity per cell (for grouping)
        intensity[0, row : row + 6, col : col + 6] = 50 + 30 * i
        if n_ch > 1:
            # Channel 1: positive everywhere, values mix "bright" and "dim"
            # within each cell so Otsu has something to find.
            base = 30 + 10 * (i % 2)
            # Add a few extra-bright pixels so Otsu actually separates them.
            intensity[1, row : row + 6, col : col + 6] = base
            intensity[1, row + 2 : row + 4, col + 2 : col + 4] = base + 80

    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    return store


def _write_synthetic_labels(store: DatasetStore, n_cells: int = 12) -> np.ndarray:
    """Write cellpose_qc labels matching the cell layout in _make_fixture_h5."""
    size = 100
    labels = np.zeros((size, size), dtype=np.int32)
    for i in range(n_cells):
        row = 5 + (i // 3) * 22
        col = 5 + (i % 3) * 22
        labels[row : row + 6, col : col + 6] = i + 1
    store.write_labels("cellpose_qc", labels)
    return labels


@pytest.fixture
def fixture_store(tmp_path: Path) -> DatasetStore:
    return _make_fixture_h5(tmp_path / "DS1.h5")


@pytest.fixture
def fixture_store_with_labels(tmp_path: Path) -> DatasetStore:
    store = _make_fixture_h5(tmp_path / "DS1.h5")
    _write_synthetic_labels(store)
    return store


# ── segment_one ─────────────────────────────────────────────────────────


@pytest.mark.slow
def test_segment_one_writes_cellpose_qc(fixture_store):
    """Real Cellpose run — marked slow so CI can skip it if needed."""
    from percell4.adapters.cellpose import build_cellpose_model

    cfg = CellposeSettings(diameter=8.0, gpu=True, min_size=5)
    model = build_cellpose_model(gpu=True)
    labels, failure, msg = segment_one(
        fixture_store, cfg, cellpose_model=model, channel_idx=0
    )
    # With tiny synthetic squares and tiny diameter, Cellpose may or may
    # not find them; we only check that the helper doesn't crash and
    # that the failure mode is either None or SEGMENTATION_EMPTY.
    assert failure in (None, DatasetFailure.SEGMENTATION_EMPTY)
    if failure is None:
        assert labels.max() > 0
        # Verify the labels were persisted
        assert "cellpose_qc" in fixture_store.list_labels()


def test_segment_one_handles_read_error(tmp_path):
    """An empty h5 (no /intensity) should return SEGMENTATION_ERROR."""
    store = DatasetStore(tmp_path / "empty.h5")
    store.create(metadata={})
    cfg = CellposeSettings()

    labels, failure, msg = segment_one(store, cfg)

    assert failure is DatasetFailure.SEGMENTATION_ERROR
    assert "read /intensity failed" in msg


# ── segment_one × edge_mode ─────────────────────────────────────────────


def _fake_cellpose_labels_with_edges() -> np.ndarray:
    """A 50×50 label array with 4 cells, 2 touching borders.

    Cells:
      - label 1: top-left corner (0:8, 0:8) — touches top + left edges
      - label 2: interior (20:30, 20:30) — area=100
      - label 3: interior (15:18, 15:18) — area=9
      - label 4: bottom-right corner (42:50, 42:50) — touches bottom + right
    """
    labels = np.zeros((50, 50), dtype=np.int32)
    labels[0:8, 0:8] = 1
    labels[20:30, 20:30] = 2
    labels[15:18, 15:18] = 3
    labels[42:50, 42:50] = 4
    return labels


@pytest.fixture
def fixture_store_50px(tmp_path: Path) -> DatasetStore:
    """A 50×50 h5 store sized for the edge-mode fixture labels."""
    return _make_fixture_h5(tmp_path / "edge_ds.h5", n_cells=4, size=50)


def test_segment_one_exclude_mode_removes_edge_cells(
    fixture_store_50px, monkeypatch
):
    """Default EXCLUDE mode preserves today's filter-edge invariant."""
    from percell4.workflows import phases
    from percell4.workflows.models import EdgeMode

    monkeypatch.setattr(
        phases, "run_cellpose", lambda *a, **kw: _fake_cellpose_labels_with_edges()
    )
    cfg = CellposeSettings(min_size=5)

    labels, failure, _msg = segment_one(
        fixture_store_50px, cfg, edge_mode=EdgeMode.EXCLUDE
    )

    assert failure is None
    # Cells 1 and 4 (edge-touching) were removed; cells 2 and 3 survive
    # and get sequential relabeling (1, 2).
    unique = set(np.unique(labels).tolist()) - {0}
    assert len(unique) == 2  # exactly 2 cells remain


def test_segment_one_include_normal_keeps_edge_cells(
    fixture_store_50px, monkeypatch
):
    """INCLUDE_AS_NORMAL mode keeps edge cells in labels."""
    from percell4.workflows import phases
    from percell4.workflows.models import EdgeMode

    monkeypatch.setattr(
        phases, "run_cellpose", lambda *a, **kw: _fake_cellpose_labels_with_edges()
    )
    cfg = CellposeSettings(min_size=5)

    labels, failure, _msg = segment_one(
        fixture_store_50px, cfg, edge_mode=EdgeMode.INCLUDE_AS_NORMAL
    )

    assert failure is None
    # All 4 cells survive (cell 3 area=9 > min_size=5; no edge filter).
    unique = set(np.unique(labels).tolist()) - {0}
    assert len(unique) == 4


def test_segment_one_size_normalized_cohort_keeps_edge_cells(
    fixture_store_50px, monkeypatch
):
    """INCLUDE_AS_SIZE_NORMALIZED_COHORT mode also keeps edge cells in Phase 1.

    The cohort treatment is a measure-time concern (U4), not a Phase 1
    concern — Phase 1 just preserves the edge cells in labels.
    """
    from percell4.workflows import phases
    from percell4.workflows.models import EdgeMode

    monkeypatch.setattr(
        phases, "run_cellpose", lambda *a, **kw: _fake_cellpose_labels_with_edges()
    )
    cfg = CellposeSettings(min_size=5)

    labels, failure, _msg = segment_one(
        fixture_store_50px,
        cfg,
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
    )

    assert failure is None
    unique = set(np.unique(labels).tolist()) - {0}
    assert len(unique) == 4


def test_segment_one_default_edge_mode_is_exclude(
    fixture_store_50px, monkeypatch
):
    """Calling segment_one without edge_mode preserves the today's-behavior default."""
    from percell4.workflows import phases

    monkeypatch.setattr(
        phases, "run_cellpose", lambda *a, **kw: _fake_cellpose_labels_with_edges()
    )
    cfg = CellposeSettings(min_size=5)

    # No edge_mode kwarg — default EXCLUDE
    labels, failure, _msg = segment_one(fixture_store_50px, cfg)

    assert failure is None
    unique = set(np.unique(labels).tolist()) - {0}
    assert len(unique) == 2  # edge cells removed


def test_segment_one_include_normal_with_no_edge_cells_matches_exclude(
    fixture_store_50px, monkeypatch
):
    """When labels have no edge-touching cells, all three modes produce the same output."""
    from percell4.workflows import phases
    from percell4.workflows.models import EdgeMode

    # All cells are well inside the image
    interior_labels = np.zeros((50, 50), dtype=np.int32)
    interior_labels[10:20, 10:20] = 1
    interior_labels[30:40, 30:40] = 2

    monkeypatch.setattr(
        phases, "run_cellpose", lambda *a, **kw: interior_labels.copy()
    )
    cfg = CellposeSettings(min_size=5)

    out_exclude, _, _ = segment_one(
        fixture_store_50px, cfg, edge_mode=EdgeMode.EXCLUDE
    )
    out_normal, _, _ = segment_one(
        fixture_store_50px, cfg, edge_mode=EdgeMode.INCLUDE_AS_NORMAL
    )

    # Both produce the same sequential labels [1, 2]
    np.testing.assert_array_equal(out_exclude, out_normal)
    assert set(np.unique(out_exclude).tolist()) - {0} == {1, 2}


# ── threshold_compute_one ───────────────────────────────────────────────


def test_threshold_compute_kmeans_happy_path(fixture_store_with_labels):
    round_spec = ThresholdingRound(
        name="GFP_split",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
    )
    result, failure, msg = threshold_compute_one(
        fixture_store_with_labels, round_spec
    )
    assert failure is None
    assert isinstance(result, GroupingResult)
    # With 6 cells of varying intensity, k-means should produce 2 groups.
    assert result.n_groups == 2


def test_threshold_compute_unknown_channel(fixture_store_with_labels):
    round_spec = ThresholdingRound(
        name="bogus",
        channel="NotAChannel",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
    )
    result, failure, msg = threshold_compute_one(
        fixture_store_with_labels, round_spec
    )
    assert result is None
    assert failure is DatasetFailure.THRESHOLD_ERROR
    assert "NotAChannel" in msg


def test_threshold_compute_empty_labels(tmp_path):
    store = _make_fixture_h5(tmp_path / "empty_labels.h5")
    store.write_labels("cellpose_qc", np.zeros((60, 60), dtype=np.int32))
    round_spec = ThresholdingRound(
        name="r",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
    )
    result, failure, msg = threshold_compute_one(store, round_spec)
    assert result is None
    assert failure is DatasetFailure.THRESHOLD_EMPTY


# ── apply_threshold_headless ────────────────────────────────────────────


def test_apply_threshold_headless_writes_mask_and_groups(
    fixture_store_with_labels,
):
    # First compute the grouping; then apply it headlessly.
    round_spec = ThresholdingRound(
        name="GFP_split",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,  # no smoothing — makes the test deterministic
    )
    grouping, _, _ = threshold_compute_one(
        fixture_store_with_labels, round_spec
    )
    assert grouping is not None

    failure, msg = apply_threshold_headless(
        fixture_store_with_labels, round_spec, grouping
    )
    assert failure is None, msg

    # Verify the mask and groups DF were written.
    assert "GFP_split" in fixture_store_with_labels.list_masks()
    groups_df = fixture_store_with_labels.read_dataframe("/groups/GFP_split")
    assert "label" in groups_df.columns
    assert any(c.startswith("group_GFP_") for c in groups_df.columns)

    # The combined mask should have some positive pixels.
    combined = fixture_store_with_labels.read_mask("GFP_split")
    assert combined.sum() > 0


def test_apply_threshold_headless_handles_unknown_channel(
    fixture_store_with_labels,
):
    round_spec = ThresholdingRound(
        name="bogus",
        channel="NotAChannel",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
    )
    # Fake a grouping result (we never get this far in practice if
    # threshold_compute_one already failed).
    grouping = GroupingResult(
        group_assignments=pd.Series(
            [1, 1, 2, 2, 2, 2], index=[1, 2, 3, 4, 5, 6], name="group"
        ),
        n_groups=2,
        group_means=[1.0, 2.0],
    )
    failure, msg = apply_threshold_headless(
        fixture_store_with_labels, round_spec, grouping
    )
    assert failure is DatasetFailure.THRESHOLD_ERROR


# ── measure_one ─────────────────────────────────────────────────────────


def test_measure_one_with_no_masks(fixture_store_with_labels):
    """Measuring without any round masks produces the base per-channel table."""
    df, failure, msg = measure_one(fixture_store_with_labels, round_specs=[])
    assert failure is None, msg
    assert len(df) == 12  # n_cells
    # Should have per-channel metric columns for GFP and RFP
    assert "GFP_mean_intensity" in df.columns
    assert "RFP_mean_intensity" in df.columns


def test_measure_one_with_round_masks(fixture_store_with_labels):
    """Full measure path: compute → apply → measure with the resulting mask."""
    round_spec = ThresholdingRound(
        name="GFP_split",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(
        fixture_store_with_labels, round_spec
    )
    apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)

    df, failure, msg = measure_one(
        fixture_store_with_labels, round_specs=[round_spec]
    )
    assert failure is None, msg
    assert len(df) == 12

    # Per-round inside/outside columns should exist
    assert "GFP_mean_intensity_in_GFP_split" in df.columns
    assert "GFP_mean_intensity_out_GFP_split" in df.columns
    assert "RFP_mean_intensity_in_GFP_split" in df.columns

    # The group_<round> column should be merged in
    assert "group_GFP_split" in df.columns
    # Every cell should have a non-null group assignment
    assert df["group_GFP_split"].notna().all()


def test_measure_one_missing_mask_still_succeeds(fixture_store_with_labels):
    """A round without a mask (threshold failed earlier) is skipped silently."""
    round_spec = ThresholdingRound(
        name="nonexistent",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
    )
    df, failure, msg = measure_one(
        fixture_store_with_labels, round_specs=[round_spec]
    )
    assert failure is None
    assert len(df) == 12
    # No _in_nonexistent columns because the mask was missing
    assert "GFP_mean_intensity_in_nonexistent" not in df.columns


# ── export_run ──────────────────────────────────────────────────────────


def _sample_run_metadata(run_folder: Path) -> RunMetadata:
    from datetime import UTC, datetime

    return RunMetadata(
        run_id="test",
        run_folder=run_folder,
        started_at=datetime.now(UTC),
        intersected_channels=["GFP", "RFP"],
    )


def _sample_workflow_config(
    selected_cols: list[str],
) -> WorkflowConfig:
    return WorkflowConfig(
        datasets=[
            WorkflowDatasetEntry(
                name="DS1",
                source=DatasetSource.H5_EXISTING,
                h5_path=Path("/tmp/DS1.h5"),
                channel_names=["GFP", "RFP"],
            ),
        ],
        cellpose=CellposeSettings(),
        thresholding_rounds=[
            ThresholdingRound(
                name="R",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.KMEANS,
                kmeans_n_clusters=2,
            ),
        ],
        selected_csv_columns=selected_cols,
        output_parent=Path("/tmp/runs"),
    )


def test_export_run_writes_parquet_and_csvs(tmp_path, fixture_store_with_labels):
    run_folder = tmp_path / "run_01"
    (run_folder / "per_dataset").mkdir(parents=True)
    (run_folder / "staging").mkdir(parents=True)

    # Build one staging parquet from a real measure_one call
    round_spec = ThresholdingRound(
        name="R",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(
        fixture_store_with_labels, round_spec
    )
    apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)
    df, failure, _ = measure_one(
        fixture_store_with_labels, round_specs=[round_spec]
    )
    assert failure is None

    write_staging_parquet(run_folder, "DS1", df)
    assert (run_folder / "staging" / "DS1.parquet").exists()

    cfg = _sample_workflow_config(
        selected_cols=["GFP_mean_intensity", "group_R"]
    )
    meta = _sample_run_metadata(run_folder)

    failure, msg = export_run(run_folder, cfg, meta)
    assert failure is None, msg

    # Final artifacts
    parquet_path = run_folder / "measurements.parquet"
    combined_csv = run_folder / "combined.csv"
    per_ds_csv = run_folder / "per_dataset" / "DS1.csv"

    assert parquet_path.exists()
    assert combined_csv.exists()
    assert per_ds_csv.exists()

    # Parquet round-trips with the expected columns
    loaded = pd.read_parquet(parquet_path)
    assert "dataset" in loaded.columns
    assert "GFP_mean_intensity" in loaded.columns
    assert len(loaded) == 12

    # combined.csv has identity + selected columns only
    combined = pd.read_csv(combined_csv)
    assert "dataset" in combined.columns
    assert "label" in combined.columns
    assert "GFP_mean_intensity" in combined.columns
    # Unselected column should NOT be in the CSV
    assert "RFP_mean_intensity" not in combined.columns

    # per-dataset CSV has no dataset column
    per_ds = pd.read_csv(per_ds_csv)
    assert "dataset" not in per_ds.columns
    assert "label" in per_ds.columns

    # staging/ was cleaned up
    assert not (run_folder / "staging").exists()


def test_export_run_fails_if_staging_missing(tmp_path):
    run_folder = tmp_path / "run_02"
    run_folder.mkdir()
    cfg = _sample_workflow_config(selected_cols=[])
    meta = _sample_run_metadata(run_folder)

    failure, msg = export_run(run_folder, cfg, meta)
    assert failure is DatasetFailure.MEASUREMENT_ERROR
    assert "staging" in msg


def test_export_run_fails_if_no_staging_parquets(tmp_path):
    run_folder = tmp_path / "run_03"
    (run_folder / "staging").mkdir(parents=True)
    cfg = _sample_workflow_config(selected_cols=[])
    meta = _sample_run_metadata(run_folder)

    failure, msg = export_run(run_folder, cfg, meta)
    assert failure is DatasetFailure.MEASUREMENT_ERROR


# ── Failure tracking helpers ────────────────────────────────────────────


def test_record_failure_appends_to_metadata(tmp_path):
    meta = _sample_run_metadata(tmp_path)
    assert meta.failures == []

    record_failure(
        meta,
        dataset_name="DS_bad",
        phase_name="segment",
        failure=DatasetFailure.SEGMENTATION_EMPTY,
        message="no cells",
    )
    assert len(meta.failures) == 1
    assert meta.failures[0].dataset_name == "DS_bad"
    assert meta.failures[0].failure is DatasetFailure.SEGMENTATION_EMPTY


def test_datasets_without_failures_excludes_failed(tmp_path):
    meta = _sample_run_metadata(tmp_path)
    entries = [
        WorkflowDatasetEntry(
            name=f"DS{i}",
            source=DatasetSource.H5_EXISTING,
            h5_path=tmp_path / f"DS{i}.h5",
        )
        for i in range(3)
    ]

    # No failures yet — all datasets pass through.
    assert len(datasets_without_failures(entries, meta)) == 3

    record_failure(
        meta,
        dataset_name="DS1",
        phase_name="segment",
        failure=DatasetFailure.SEGMENTATION_ERROR,
        message="synthetic",
    )

    remaining = datasets_without_failures(entries, meta)
    assert [e.name for e in remaining] == ["DS0", "DS2"]
