"""Tests for the interactive-QC path of SingleCellThresholdingRunner.

The SegmentationQCController and ThresholdQCQueueEntry are hard to
drive in a headless test environment — they pop real QMainWindow
instances and wait for user input. Instead we mock the controller
classes at the module-import site inside the runner, so every
interactive PhaseRequest hits a fake controller that immediately fires
its ``on_complete`` callback with a scripted :class:`PhaseResult`.

This verifies the runner's *wiring* for interactive phases (the
generator yields them, the handler plumbs the callback, the cache
clears on success) without depending on the real QC UIs. The real UIs
are exercised manually through the launcher button.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.gui.workflows.base_runner import PhaseResult, WorkflowEventKind
from percell4.store import DatasetStore
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

# ── Fixture helpers ─────────────────────────────────────────────────────


def _make_dataset(path: Path, size: int = 100, n_cells: int = 12) -> None:
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP", "RFP"]})
    intensity = np.zeros((2, size, size), dtype=np.float32)
    labels = np.zeros((size, size), dtype=np.int32)
    for i in range(n_cells):
        row = 5 + (i // 3) * 22
        col = 5 + (i % 3) * 22
        intensity[0, row : row + 6, col : col + 6] = 50 + 30 * i
        intensity[1, row : row + 6, col : col + 6] = 40
        intensity[1, row + 2 : row + 4, col + 2 : col + 4] = 120
        labels[row : row + 6, col : col + 6] = i + 1
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    store.write_labels("cellpose_qc", labels)


@pytest.fixture
def fake_host() -> MagicMock:
    host = MagicMock(spec=WorkflowHost)
    host.get_viewer_window.return_value = MagicMock()
    host.get_data_model.return_value = MagicMock()
    return host


@pytest.fixture
def config_and_meta(tmp_path):
    p1 = tmp_path / "DS1.h5"
    p2 = tmp_path / "DS2.h5"
    _make_dataset(p1)
    _make_dataset(p2)

    entries = [
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
    cfg = WorkflowConfig(
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
        selected_csv_columns=["GFP_mean_intensity"],
        output_parent=tmp_path / "runs",
    )
    from percell4.workflows.artifacts import create_run_folder

    run_folder = create_run_folder(tmp_path / "runs")
    meta = RunMetadata(
        run_id=run_folder.name,
        run_folder=run_folder,
        started_at=datetime.now(UTC),
        intersected_channels=["GFP", "RFP"],
    )
    return cfg, meta, run_folder


# ── Auto-complete fakes for the QC controllers ────────────────────────


class _FakeSegQCController:
    """Auto-accept seg QC controller — records every call for assertions."""

    instances: list[_FakeSegQCController] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._on_complete = kwargs["on_complete"]
        _FakeSegQCController.instances.append(self)

    def start(self) -> None:
        # Immediately auto-accept without showing any window.
        self._on_complete(
            PhaseResult(success=True, message="fake seg QC accepted")
        )


class _FakeThresholdQCQueueEntry:
    """Auto-accept threshold QC controller — records every call."""

    instances: list[_FakeThresholdQCQueueEntry] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._on_complete = kwargs["on_complete"]
        _FakeThresholdQCQueueEntry.instances.append(self)

    def start(self) -> None:
        # Write the mask + groups DF that the real controller's
        # _finalize would have written, so measure_one finds them.
        from percell4.store import DatasetStore as _Store
        from percell4.workflows.phases import apply_threshold_headless

        entry = self.kwargs["entry"]
        round_spec = self.kwargs["round_spec"]
        grouping = self.kwargs["grouping_result"]
        store = _Store(entry.h5_path)
        apply_threshold_headless(store, round_spec, grouping)
        self._on_complete(
            PhaseResult(success=True, message="fake threshold QC accepted")
        )


@pytest.fixture
def patched_runner(monkeypatch):
    """Patch the real QC controllers inside the runner module."""
    _FakeSegQCController.instances.clear()
    _FakeThresholdQCQueueEntry.instances.clear()

    # Patch at the import sites inside the handlers. The handlers use
    # lazy imports, so we need to patch the sub-module, not the runner.
    import percell4.gui.workflows.single_cell.seg_qc as seg_qc_mod
    import percell4.gui.workflows.single_cell.threshold_qc_queue as thresh_mod

    monkeypatch.setattr(
        seg_qc_mod, "SegmentationQCController", _FakeSegQCController
    )
    monkeypatch.setattr(
        thresh_mod, "ThresholdQCQueueEntry", _FakeThresholdQCQueueEntry
    )
    yield


# ── Tests ───────────────────────────────────────────────────────────────


def test_interactive_runner_yields_seg_qc_and_threshold_qc_requests(
    qtbot, fake_host, config_and_meta, patched_runner, monkeypatch
):
    """End-to-end interactive run with auto-accepting fake controllers.

    Verifies that:
      - The runner yields one seg QC request per (non-failed) dataset
      - The runner yields one threshold QC request per (dataset, round)
      - Both fake controllers get ``on_complete`` fired
      - The run terminates successfully
      - Final artifacts are produced
    """
    import percell4.gui.workflows.single_cell.runner as runner_mod
    import percell4.workflows.phases as phases
    from percell4.gui.workflows.single_cell.runner import (
        SingleCellThresholdingRunner,
    )

    cfg, meta, run_folder = config_and_meta

    # Patch segment_one to a no-op so Cellpose doesn't run.
    def _noop_segment(store, cfg_, cellpose_model=None, channel_idx=0):
        try:
            labels = store.read_labels("cellpose_qc")
        except KeyError:
            labels = np.zeros((100, 100), dtype=np.int32)
        return labels, None, "noop"

    monkeypatch.setattr(phases, "segment_one", _noop_segment)
    monkeypatch.setattr(runner_mod, "segment_one", _noop_segment)

    runner = SingleCellThresholdingRunner(
        config=cfg, metadata=meta, interactive_qc=True
    )
    events = []
    runner.workflow_event.connect(lambda e: events.append(e))

    runner.start(cfg, fake_host, meta)

    # Interactive segment runs in a QThread worker; pump the event loop
    # until the run terminates.
    qtbot.waitUntil(
        lambda: any(
            e.kind is WorkflowEventKind.RUN_FINISHED for e in events
        ),
        timeout=30_000,
    )

    # Run completed
    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].success is True

    # Seg QC was opened for each dataset (2 datasets × 1 seg QC each)
    assert len(_FakeSegQCController.instances) == 2
    assert [c.kwargs["entry"].name for c in _FakeSegQCController.instances] == [
        "DS1",
        "DS2",
    ]

    # Threshold QC was opened for each (dataset, round) pair (2 × 1 = 2)
    assert len(_FakeThresholdQCQueueEntry.instances) == 2
    assert [
        c.kwargs["entry"].name for c in _FakeThresholdQCQueueEntry.instances
    ] == ["DS1", "DS2"]
    assert all(
        c.kwargs["round_spec"].name == "GFP_split"
        for c in _FakeThresholdQCQueueEntry.instances
    )

    # Final artifacts exist
    assert (run_folder / "measurements.parquet").is_file()
    assert (run_folder / "combined.csv").is_file()


def test_interactive_runner_cancel_from_seg_qc(
    qtbot, fake_host, config_and_meta, monkeypatch
):
    """Cancelling from seg QC unwinds the runner cleanly."""
    import percell4.gui.workflows.single_cell.runner as runner_mod
    import percell4.gui.workflows.single_cell.seg_qc as seg_qc_mod
    import percell4.workflows.phases as phases

    cfg, meta, run_folder = config_and_meta

    def _noop_segment(store, cfg_, cellpose_model=None, channel_idx=0):
        labels = store.read_labels("cellpose_qc")
        return labels, None, "noop"

    monkeypatch.setattr(phases, "segment_one", _noop_segment)
    monkeypatch.setattr(runner_mod, "segment_one", _noop_segment)

    # Seg QC controller that cancels instead of accepting.
    class _CancelSegQC:
        def __init__(self, **kwargs):
            self._on_complete = kwargs["on_complete"]

        def start(self):
            self._on_complete(
                PhaseResult(success=False, message="user cancelled during seg QC")
            )

    monkeypatch.setattr(seg_qc_mod, "SegmentationQCController", _CancelSegQC)

    from percell4.gui.workflows.single_cell.runner import (
        SingleCellThresholdingRunner,
    )

    runner = SingleCellThresholdingRunner(
        config=cfg, metadata=meta, interactive_qc=True
    )
    events = []
    runner.workflow_event.connect(lambda e: events.append(e))

    runner.start(cfg, fake_host, meta)

    qtbot.waitUntil(
        lambda: any(
            e.kind is WorkflowEventKind.RUN_FINISHED for e in events
        ),
        timeout=30_000,
    )

    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].success is False
    assert finished[0].message == "cancelled"
    assert runner.cancel_requested is True


def test_interactive_runner_threshold_qc_failure_is_recorded(
    qtbot, fake_host, config_and_meta, patched_runner, monkeypatch
):
    """A threshold QC failure is recorded and the run continues."""
    import percell4.gui.workflows.single_cell.runner as runner_mod
    import percell4.gui.workflows.single_cell.threshold_qc_queue as thresh_mod
    import percell4.workflows.phases as phases

    cfg, meta, run_folder = config_and_meta

    def _noop_segment(store, cfg_, cellpose_model=None, channel_idx=0):
        labels = store.read_labels("cellpose_qc")
        return labels, None, "noop"

    monkeypatch.setattr(phases, "segment_one", _noop_segment)
    monkeypatch.setattr(runner_mod, "segment_one", _noop_segment)

    # Threshold QC that fails for DS1 but succeeds for DS2.
    call_log = []

    class _FlakyThresholdQC:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._on_complete = kwargs["on_complete"]

        def start(self):
            entry_name = self.kwargs["entry"].name
            call_log.append(entry_name)
            if entry_name == "DS1":
                self._on_complete(
                    PhaseResult(
                        success=False, message="synthetic threshold QC failure"
                    )
                )
            else:
                # Write real mask + groups so measure_one can consume them.
                from percell4.store import DatasetStore as _Store
                from percell4.workflows.phases import apply_threshold_headless

                store = _Store(self.kwargs["entry"].h5_path)
                apply_threshold_headless(
                    store, self.kwargs["round_spec"], self.kwargs["grouping_result"]
                )
                self._on_complete(
                    PhaseResult(success=True, message="ok")
                )

    monkeypatch.setattr(thresh_mod, "ThresholdQCQueueEntry", _FlakyThresholdQC)

    from percell4.gui.workflows.single_cell.runner import (
        SingleCellThresholdingRunner,
    )

    runner = SingleCellThresholdingRunner(
        config=cfg, metadata=meta, interactive_qc=True
    )
    events = []
    runner.workflow_event.connect(lambda e: events.append(e))

    runner.start(cfg, fake_host, meta)

    # Interactive segment runs in a QThread worker; pump the event loop
    # until the run terminates.
    qtbot.waitUntil(
        lambda: any(
            e.kind is WorkflowEventKind.RUN_FINISHED for e in events
        ),
        timeout=30_000,
    )

    # Run completed
    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1

    # Both datasets were attempted
    assert call_log == ["DS1", "DS2"]

    # DS1 has a threshold QC failure record
    assert any(
        rec.dataset_name == "DS1" and "threshold_qc" in rec.phase_name
        for rec in meta.failures
    )
