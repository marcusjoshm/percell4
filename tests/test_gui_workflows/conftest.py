"""Shared fixtures for gui/workflows tests.

All tests in this directory need a ``qtbot`` (from ``pytest-qt``) so that
``BaseWorkflowRunner`` — which is a ``QObject`` and emits a Qt signal —
can be instantiated. They also need a throw-away ``WorkflowHost`` and a
real run folder on disk, since the runner writes ``run_config.json`` and
``run_log.jsonl`` into it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from percell4.workflows.artifacts import create_run_folder
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


@pytest.fixture
def fake_host() -> MagicMock:
    """MagicMock posing as a :class:`WorkflowHost`.

    ``MagicMock`` conforms structurally to any ``Protocol`` because it
    answers every attribute access with another MagicMock, so
    ``isinstance(fake_host, WorkflowHost)`` holds when the Protocol is
    ``@runtime_checkable``. Each of the six host methods records its
    call count, so tests can assert e.g. ``fake_host.set_workflow_locked``
    was called with ``False`` on the way out.
    """
    host = MagicMock(spec=WorkflowHost)
    # The spec= argument restricts the mock to the six host methods, which
    # keeps tests honest (a typo like ``host.set_workflw_locked`` raises
    # AttributeError instead of silently creating a new MagicMock child).
    return host


@pytest.fixture
def run_folder(tmp_path: Path) -> Path:
    """A real run folder on disk, with per_dataset/ and staging/ subdirs."""
    return create_run_folder(tmp_path / "runs")


@pytest.fixture
def sample_config() -> WorkflowConfig:
    """Minimal valid WorkflowConfig for runner tests."""
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
                name="GFP_bright",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.GMM,
            ),
        ],
        selected_csv_columns=[],
        output_parent=Path("/tmp/runs"),
    )


@pytest.fixture
def sample_metadata(run_folder: Path) -> RunMetadata:
    """RunMetadata pointing at an existing run folder."""
    return RunMetadata(
        run_id="test_run_0001",
        run_folder=run_folder,
        started_at=datetime(2026, 4, 10, 14, 0, 0, tzinfo=UTC),
        intersected_channels=["GFP", "RFP"],
    )


@pytest.fixture
def collect_events():
    """Factory for a list-appending slot for the ``workflow_event`` signal.

    Usage::

        events = []
        def on_event(e):
            events.append(e)
        runner.workflow_event.connect(on_event)
        runner.start(...)
        assert events[-1].kind == WorkflowEventKind.RUN_FINISHED

    This helper returns the ``(list, slot)`` pair so the caller can hold
    a reference to the slot function (Qt won't keep one for us).
    """
    def _factory():
        events: list = []
        def _slot(event):
            events.append(event)
        return events, _slot
    return _factory


# ── Phasor-plot fixtures (shared across phasor test files) ──────────

import numpy as np  # noqa: E402

from percell4.application.session import Session  # noqa: E402
from percell4.domain.dataset import DatasetHandle  # noqa: E402
from percell4.interfaces.gui.peer_views.phasor_plot import (  # noqa: E402
    PhasorPlotWindow,
    PhasorROI,
)


@pytest.fixture
def session_with_dataset(tmp_path) -> Session:
    sess = Session()
    sess._dataset = DatasetHandle(path=tmp_path / "fake.h5", metadata={})
    return sess


def _wide_phasor_maps(shape=(16, 16)) -> tuple[np.ndarray, np.ndarray]:
    """Build deterministic g/s maps that span a wide phasor region."""
    h, w = shape
    gs = np.linspace(0.05, 0.95, h * w, dtype=np.float32).reshape(shape)
    ss = np.linspace(0.05, 0.45, h * w, dtype=np.float32).reshape(shape)
    return gs, ss


def _add_wide_roi(window: PhasorPlotWindow, name: str = "ROI_test") -> None:
    """Place a single ROI big enough to enclose the entire phasor cloud."""
    roi = PhasorROI(
        name=name,
        center=(0.5, 0.25),
        radii=(0.6, 0.4),
        angle_deg=0,
        label=1,
        color="#ff00ff",
    )
    window._create_roi_widget(roi)


@pytest.fixture
def phasor_window(qtbot, session_with_dataset) -> PhasorPlotWindow:
    """PhasorPlotWindow pre-loaded with deterministic g/s maps.

    Patches ``_g_map`` / ``_s_map`` directly (bypassing
    ``set_phasor_data``) and pushes the apply-button gate by hand, so
    callers can drive filter behavior without going through dataset
    machinery.
    """
    repo = MagicMock()
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: repo)
    qtbot.addWidget(win)
    g, s = _wide_phasor_maps()
    win._g_map = g
    win._s_map = s
    win._total_valid_pixels = int((np.isfinite(g) & (g != 0)).sum())
    # set_phasor_data normally runs the gate; we patched _g_map directly
    # so push it by hand. Idempotent for tests that don't care.
    win._refresh_apply_buttons_enabled()
    return win
