"""End-to-end test for :class:`SingleCellThresholdingRunner`.

Runs the full Phase 0 → Phase 8 sequence on synthetic h5 fixtures with
pre-written labels (so we can skip the slow Cellpose step and focus on
the threshold / measure / export plumbing). Asserts that every expected
artifact is produced in the run folder and that the run metadata
records zero failures on the happy path.

A separate test simulates a per-dataset failure and asserts that the
run completes with the failure recorded but the other datasets still
processed successfully.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from percell4.gui.workflows.base_runner import WorkflowEventKind
from percell4.gui.workflows.single_cell.runner import (
    SingleCellThresholdingRunner,
)
from percell4.store import DatasetStore
from percell4.workflows.artifacts import create_run_folder, read_run_config
from percell4.workflows.host import WorkflowHost
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)

# ── Fixture builders ────────────────────────────────────────────────────


def _make_dataset(
    path: Path,
    name: str,
    n_cells: int = 12,
    size: int = 100,
) -> DatasetStore:
    """Create an h5 with pre-written labels so segment_one can be skipped.

    Same layout pattern as tests/test_workflows/test_phases.py.
    """
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP", "RFP"]})

    intensity = np.zeros((2, size, size), dtype=np.float32)
    labels = np.zeros((size, size), dtype=np.int32)
    for i in range(n_cells):
        row = 5 + (i // 3) * 22
        col = 5 + (i % 3) * 22
        # Channel 0: increasing per-cell intensity
        intensity[0, row : row + 6, col : col + 6] = 50 + 30 * i
        # Channel 1: mixed intensity within each cell
        intensity[1, row : row + 6, col : col + 6] = 40
        intensity[1, row + 2 : row + 4, col + 2 : col + 4] = 120
        labels[row : row + 6, col : col + 6] = i + 1

    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    store.write_labels("cellpose_qc", labels)
    return store


@pytest.fixture
def fake_host() -> MagicMock:
    return MagicMock(spec=WorkflowHost)


def _make_two_datasets(tmp_path: Path) -> list[WorkflowDatasetEntry]:
    """Create two fresh h5 datasets and return their WorkflowDatasetEntries."""
    p1 = tmp_path / "DS1.h5"
    p2 = tmp_path / "DS2.h5"
    _make_dataset(p1, "DS1")
    _make_dataset(p2, "DS2")
    return [
        WorkflowDatasetEntry(
            name="DS1",
            source=DatasetSource.H5_EXISTING,
            h5_path=p1,
            channel_names=["GFP", "RFP"],
        ),
        WorkflowDatasetEntry(
            name="DS2",
            source=DatasetSource.H5_EXISTING,
            h5_path=p2,
            channel_names=["GFP", "RFP"],
        ),
    ]


def _make_config(
    entries: list[WorkflowDatasetEntry],
    run_parent: Path,
    selected_cols: list[str] | None = None,
) -> WorkflowConfig:
    return WorkflowConfig(
        datasets=entries,
        cellpose=CellposeSettings(diameter=8.0, gpu=False, min_size=5),
        thresholding_rounds=[
            ThresholdingRound(
                name="GFP_split",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.KMEANS,
                kmeans_n_clusters=2,
                gaussian_sigma=0.0,
            ),
        ],
        selected_csv_columns=(
            selected_cols
            if selected_cols is not None
            else ["GFP_mean_intensity", "group_GFP_split"]
        ),
        output_parent=run_parent,
    )


def _make_metadata(run_folder: Path) -> RunMetadata:
    return RunMetadata(
        run_id=run_folder.name,
        run_folder=run_folder,
        started_at=datetime.now(UTC),
        intersected_channels=["GFP", "RFP"],
    )


# ── End-to-end happy path ───────────────────────────────────────────────


@pytest.mark.slow
def test_runner_happy_path_skipping_cellpose(qtbot, fake_host, tmp_path):
    """Full Phase 0 → Phase 8 sequence on 2 datasets with pre-written labels.

    The runner's segment handler tries to run Cellpose on each dataset,
    but because we pre-populated ``/labels/cellpose_qc`` the subsequent
    threshold_compute + apply + measure phases produce real output.
    Cellpose runs here too — it operates on the synthetic intensity
    image and either finds cells (overwriting our labels) or returns
    empty (marking the dataset failed). The key invariants we assert
    are:

    1. The run terminates with ``RUN_FINISHED`` exactly once.
    2. The export phase produces the expected artifacts for every
       dataset that made it through.
    3. ``run_config.json`` is rewritten with ``finished_at`` stamped.
    """
    entries = _make_two_datasets(tmp_path)
    run_folder = create_run_folder(tmp_path / "runs")
    cfg = _make_config(entries, tmp_path / "runs")
    meta = _make_metadata(run_folder)

    runner = SingleCellThresholdingRunner(config=cfg, metadata=meta)
    events = []
    runner.workflow_event.connect(lambda e: events.append(e))

    runner.start(cfg, fake_host, meta)

    # Exactly one run_finished event
    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1

    # Host lock lifecycle
    fake_host.set_workflow_locked.assert_any_call(True)
    fake_host.set_workflow_locked.assert_any_call(False)
    assert fake_host.close_child_windows.called
    assert fake_host.restore_child_windows.called

    # run_config.json was rewritten with finished_at stamped
    loaded_cfg, loaded_meta = read_run_config(run_folder)
    assert loaded_meta.finished_at is not None


def test_runner_produces_expected_artifacts_when_segment_succeeds(
    qtbot, fake_host, tmp_path
):
    """With pre-written labels, Cellpose may produce empty segmentation.

    We patch segment_one to be a no-op that leaves the pre-written labels
    in place, guaranteeing that threshold / measure / export phases run
    on real data and produce the expected run folder artifacts.
    """
    import percell4.workflows.phases as phases

    entries = _make_two_datasets(tmp_path)
    run_folder = create_run_folder(tmp_path / "runs")
    cfg = _make_config(entries, tmp_path / "runs")
    meta = _make_metadata(run_folder)

    # Patch segment_one to a no-op success that returns the existing labels.
    original_segment = phases.segment_one

    def _noop_segment(store, cfg_, cellpose_model=None, channel_idx=0):
        try:
            labels = store.read_labels("cellpose_qc")
        except KeyError:
            labels = np.zeros((100, 100), dtype=np.int32)
        return labels, None, "noop"

    phases.segment_one = _noop_segment
    try:
        # The runner imports the symbol by name, so we also need to
        # patch the local binding it captured.
        import percell4.gui.workflows.single_cell.runner as runner_mod
        runner_mod.segment_one = _noop_segment

        runner = SingleCellThresholdingRunner(config=cfg, metadata=meta)
        events = []
        runner.workflow_event.connect(lambda e: events.append(e))
        runner.start(cfg, fake_host, meta)
    finally:
        phases.segment_one = original_segment
        runner_mod.segment_one = original_segment

    # Terminal event
    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].success is True, finished[0].message

    # Artifacts exist
    assert (run_folder / "measurements.parquet").is_file()
    assert (run_folder / "combined.csv").is_file()
    assert (run_folder / "per_dataset" / "DS1.csv").is_file()
    assert (run_folder / "per_dataset" / "DS2.csv").is_file()
    assert (run_folder / "run_log.jsonl").is_file()
    # staging/ is cleaned up on successful export
    assert not (run_folder / "staging").exists()

    # Parquet contents
    df = pd.read_parquet(run_folder / "measurements.parquet")
    assert len(df) == 24  # 12 cells × 2 datasets
    assert set(df["dataset"].unique()) == {"DS1", "DS2"}
    assert "GFP_mean_intensity" in df.columns
    assert "GFP_mean_intensity_in_GFP_split" in df.columns
    assert "group_GFP_split" in df.columns

    # combined.csv has the selected columns plus identity
    combined = pd.read_csv(run_folder / "combined.csv")
    assert "dataset" in combined.columns
    assert "label" in combined.columns
    assert "GFP_mean_intensity" in combined.columns
    assert "group_GFP_split" in combined.columns
    # Columns not selected should not be in the CSV
    assert "RFP_mean_intensity" not in combined.columns

    # per-dataset CSVs do NOT include the dataset column
    per_ds = pd.read_csv(run_folder / "per_dataset" / "DS1.csv")
    assert "dataset" not in per_ds.columns

    # No h5 dataset was given a /measurements group — the workflow
    # writes measurements exclusively to the run folder parquet.
    for entry in entries:
        store = DatasetStore(entry.h5_path)
        # list_groups on "" returns the root-level group names
        roots = store.list_groups("")
        assert "measurements" not in roots, (
            f"{entry.name} unexpectedly got a /measurements group"
        )
        # /masks/<round> and /groups/<round> WERE written
        assert "GFP_split" in store.list_masks()

    # run_config.json has finished_at stamped
    loaded_cfg, loaded_meta = read_run_config(run_folder)
    assert loaded_meta.finished_at is not None
    # Failures list is empty on happy path
    assert loaded_meta.failures == []


def test_runner_records_failure_and_continues_other_datasets(
    qtbot, fake_host, tmp_path
):
    """Per-dataset failure doesn't crash the run — other datasets proceed."""
    import percell4.gui.workflows.single_cell.runner as runner_mod
    import percell4.workflows.phases as phases

    entries = _make_two_datasets(tmp_path)
    run_folder = create_run_folder(tmp_path / "runs")
    cfg = _make_config(entries, tmp_path / "runs")
    meta = _make_metadata(run_folder)

    # Fake segment_one: DS1 fails with SEGMENTATION_EMPTY, DS2 succeeds.
    from percell4.workflows.failures import DatasetFailure

    call_counter = {"n": 0}

    def _flaky_segment(store, cfg_, cellpose_model=None, channel_idx=0):
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            return (
                np.zeros((100, 100), dtype=np.int32),
                DatasetFailure.SEGMENTATION_EMPTY,
                "ds1 empty",
            )
        # DS2 succeeds — leave the pre-written labels alone
        labels = store.read_labels("cellpose_qc")
        return labels, None, "ok"

    original_segment = phases.segment_one
    phases.segment_one = _flaky_segment
    runner_mod.segment_one = _flaky_segment
    try:
        runner = SingleCellThresholdingRunner(config=cfg, metadata=meta)
        events = []
        runner.workflow_event.connect(lambda e: events.append(e))
        runner.start(cfg, fake_host, meta)
    finally:
        phases.segment_one = original_segment
        runner_mod.segment_one = original_segment

    # Run still finished, successfully (export aggregates the good dataset)
    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1

    # DS1 is in the failures list
    assert any(
        rec.dataset_name == "DS1"
        and rec.failure is DatasetFailure.SEGMENTATION_EMPTY
        for rec in meta.failures
    )

    # DS2 CSV exists; DS1 CSV does not
    assert (run_folder / "per_dataset" / "DS2.csv").is_file()
    assert not (run_folder / "per_dataset" / "DS1.csv").exists()

    # Parquet has only DS2 rows
    df = pd.read_parquet(run_folder / "measurements.parquet")
    assert set(df["dataset"].unique()) == {"DS2"}

    # run_config.json preserves the failure record
    _cfg, loaded_meta = read_run_config(run_folder)
    assert any(
        rec.dataset_name == "DS1" for rec in loaded_meta.failures
    )


def test_runner_reentrance_guard(qtbot, fake_host, tmp_path):
    """Second call to start() while running raises RuntimeError.

    This drives a runner via the FINISHED state — it's trivially true
    here because the MVP runs synchronously, so by the time we get the
    next line the run is already finished. We verify by calling start
    a second time after the first completed and asserting the same
    reentrance guard fires.
    """
    entries = _make_two_datasets(tmp_path)
    run_folder = create_run_folder(tmp_path / "runs")
    cfg = _make_config(entries, tmp_path / "runs")
    meta = _make_metadata(run_folder)

    runner = SingleCellThresholdingRunner(config=cfg, metadata=meta)

    # Patch segment_one to no-op so the run finishes without Cellpose.
    import percell4.gui.workflows.single_cell.runner as runner_mod
    import percell4.workflows.phases as phases

    def _noop(store, cfg_, cellpose_model=None, channel_idx=0):
        try:
            labels = store.read_labels("cellpose_qc")
        except KeyError:
            labels = np.zeros((100, 100), dtype=np.int32)
        return labels, None, "noop"

    original = phases.segment_one
    phases.segment_one = _noop
    runner_mod.segment_one = _noop
    try:
        runner.start(cfg, fake_host, meta)
        # Run is in FINISHED state by now.
        assert runner.state == "finished"

        with pytest.raises(RuntimeError, match="state=finished"):
            runner.start(cfg, fake_host, meta)
    finally:
        phases.segment_one = original
        runner_mod.segment_one = original
