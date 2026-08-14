"""U8 regression tests: Workflows sidebar entry + launcher integration.

The dilute phase mask generation workflow surfaces as a second
QPushButton on the Workflows sidebar tab. Clicking it:
  1. respects ``LauncherWindow.is_workflow_locked`` as a re-entry guard
  2. instantiates DilutePhaseMaskPanel
  3. acquires the workflow lock
  4. on workflow_done / workflow_cancelled: releases the lock and
     closes the panel
  5. on workflow_error: surfaces in the status bar (does NOT auto-
     teardown; the user still drives Cancel / Run another round)

These tests stub the panel class so the test doesn't need a viable
dataset on disk — the sidebar wiring is the surface under test, not
the panel's own behavior (covered separately by
test_dilute_phase_panel.py).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from qtpy.QtCore import QObject, Signal


class _FakePanel(QObject):
    """Minimal stand-in for DilutePhaseMaskPanel exposing only the
    three signals the launcher cares about + the window-setup and
    lifecycle methods the launcher calls. Each is a no-op recorder so
    the test can audit what the launcher invoked without dragging in
    a real QWidget (which would need a qtbot fixture and a parent).
    """

    workflow_done = Signal()
    workflow_cancelled = Signal()
    workflow_error = Signal(str)

    def __init__(self, **kwargs) -> None:
        super().__init__()
        self.show_called = False
        self.close_called = False
        self.window_flag_set = False
        self.title_set = None
        self.resize_called_with = None
        self.raise_called = False
        self.activate_called = False

    def show(self) -> None:
        self.show_called = True

    def close(self) -> None:
        self.close_called = True

    def setWindowFlag(self, flag, on=True) -> None:  # noqa: N802 - Qt API
        # Record only — the real panel forwards to QWidget.setWindowFlag
        # which makes the panel a top-level window.
        self.window_flag_set = bool(on)

    def setWindowTitle(self, title: str) -> None:  # noqa: N802 - Qt API
        self.title_set = title

    def resize(self, w: int, h: int) -> None:
        self.resize_called_with = (w, h)

    def raise_(self) -> None:
        self.raise_called = True

    def activateWindow(self) -> None:  # noqa: N802 - Qt API
        self.activate_called = True


@pytest.fixture
def launcher(qtbot, tmp_path):
    """Launcher window + a minimal open dataset, just enough for the
    handler's preflight checks (`get_session().dataset`, `_current_store`,
    `get_viewer_window()`) to pass."""
    import numpy as np

    from percell4.domain.dataset import DatasetHandle
    from percell4.interfaces.gui.main_window import LauncherWindow
    from percell4.store import DatasetStore

    path = tmp_path / "exp.h5"
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["ch0"]})
    store.write_array("intensity", np.zeros((8, 8), dtype=np.float32))

    from percell4.application.session import Session
    from percell4.model import CellDataModel

    win = LauncherWindow(CellDataModel(Session()))
    qtbot.addWidget(win)

    # Plant the open-dataset state the handler reads.
    win._current_store = store
    win.get_session().set_dataset(
        DatasetHandle(path=path, metadata={"channel_names": ["ch0"]})
    )
    # Force-create the viewer window so get_viewer_window() is non-None.
    win._get_or_create_window("viewer")

    return win


def test_sidebar_has_dilute_phase_button(launcher):
    """A second QPushButton labelled appropriately lives in the
    Workflows sidebar panel."""
    assert hasattr(launcher, "_btn_dilute_phase_workflow"), (
        "Workflows sidebar must expose the dilute phase entry as "
        "_btn_dilute_phase_workflow alongside the single-cell one"
    )
    btn = launcher._btn_dilute_phase_workflow
    assert "Dilute" in btn.text()


def test_click_opens_panel_and_locks(launcher):
    """Click → panel constructed, lock acquired, reference held to
    prevent GC."""
    with patch(
        "percell4.gui.workflows.dilute_phase.panel.DilutePhaseMaskPanel",
        new=_FakePanel,
    ):
        launcher._on_open_dilute_phase_workflow()

    assert launcher._active_dilute_workflow_panel is not None
    assert isinstance(launcher._active_dilute_workflow_panel, _FakePanel)
    assert launcher._active_dilute_workflow_panel.show_called
    assert launcher.is_workflow_locked


def test_click_promotes_panel_to_top_level_window(launcher):
    """Regression for the May-18 bug where panel.show() was called on a
    parented QWidget without Qt.Window flag, so the panel rendered
    invisibly inside the launcher's coordinate space (status bar said
    'Workflow running...' but no window appeared).

    The launcher must explicitly promote the panel to a top-level
    window before show(): set Qt.Window flag, set a window title,
    resize to a sensible default, then raise + activate so it surfaces
    above any napari sub-windows."""
    with patch(
        "percell4.gui.workflows.dilute_phase.panel.DilutePhaseMaskPanel",
        new=_FakePanel,
    ):
        launcher._on_open_dilute_phase_workflow()

    panel = launcher._active_dilute_workflow_panel
    assert panel.window_flag_set, (
        "Launcher must call panel.setWindowFlag(Qt.Window, True) so "
        "the parented QWidget renders as its own top-level window"
    )
    assert panel.title_set, "Launcher must set a window title"
    assert panel.resize_called_with is not None, (
        "Launcher must resize the panel to a sensible default; a "
        "0x0 QWidget would still render invisibly"
    )
    assert panel.raise_called, "Launcher must raise() the panel"
    assert panel.activate_called, "Launcher must activateWindow()"


def test_workflow_done_unlocks_and_clears_panel(launcher):
    """workflow_done → lock released, panel reference cleared, close()
    invoked."""
    with patch(
        "percell4.gui.workflows.dilute_phase.panel.DilutePhaseMaskPanel",
        new=_FakePanel,
    ):
        launcher._on_open_dilute_phase_workflow()
        panel = launcher._active_dilute_workflow_panel
        panel.workflow_done.emit()

    assert launcher._active_dilute_workflow_panel is None
    assert not launcher.is_workflow_locked
    assert panel.close_called


def test_workflow_cancelled_unlocks_and_clears_panel(launcher):
    """Cancel routes through the same finishing path."""
    with patch(
        "percell4.gui.workflows.dilute_phase.panel.DilutePhaseMaskPanel",
        new=_FakePanel,
    ):
        launcher._on_open_dilute_phase_workflow()
        panel = launcher._active_dilute_workflow_panel
        panel.workflow_cancelled.emit()

    assert launcher._active_dilute_workflow_panel is None
    assert not launcher.is_workflow_locked
    assert panel.close_called


def test_workflow_error_surfaces_in_status_bar_without_teardown(launcher):
    """Soft errors (`error.emit(msg)`) are shown in the status bar but
    do NOT release the lock or close the panel — the user keeps
    driving the workflow from where they left off."""
    with patch(
        "percell4.gui.workflows.dilute_phase.panel.DilutePhaseMaskPanel",
        new=_FakePanel,
    ):
        launcher._on_open_dilute_phase_workflow()
        panel = launcher._active_dilute_workflow_panel
        panel.workflow_error.emit("Round 3 has no finite cells")

    assert launcher._active_dilute_workflow_panel is panel
    assert launcher.is_workflow_locked
    assert not panel.close_called
    # Status bar carries the message.
    assert "Round 3" in launcher.statusBar().currentMessage()


def test_reentry_blocked_when_already_locked(launcher):
    """Click while locked → status message; no second panel."""
    with patch(
        "percell4.gui.workflows.dilute_phase.panel.DilutePhaseMaskPanel",
        new=_FakePanel,
    ):
        launcher._on_open_dilute_phase_workflow()
        first = launcher._active_dilute_workflow_panel

        # Second click while still locked.
        launcher._on_open_dilute_phase_workflow()

    assert launcher._active_dilute_workflow_panel is first
    assert "already running" in launcher.statusBar().currentMessage()


def test_click_without_open_dataset_surfaces_status_message(
    qtbot, tmp_path,
):
    """No open dataset (session.dataset is None) → status message,
    no panel construction."""
    from percell4.application.session import Session
    from percell4.interfaces.gui.main_window import LauncherWindow
    from percell4.model import CellDataModel

    win = LauncherWindow(CellDataModel(Session()))
    qtbot.addWidget(win)
    # Deliberately do NOT call set_dataset(); session.dataset is None.

    panel_factory = MagicMock(return_value=_FakePanel())
    with patch(
        "percell4.gui.workflows.dilute_phase.panel.DilutePhaseMaskPanel",
        new=panel_factory,
    ):
        win._on_open_dilute_phase_workflow()

    assert panel_factory.call_count == 0  # Panel never constructed.
    assert not win.is_workflow_locked
    assert "Open a dataset" in win.statusBar().currentMessage()
