"""Tests for the launcher's Batch Tools tab wiring + reload hook (U6)."""

from __future__ import annotations

from percell4.application.session import Session
from percell4.interfaces.gui.main_window import LauncherWindow
from percell4.interfaces.gui.task_panels.batch_console_panel import (
    BatchConsolePanel,
)
from percell4.model import CellDataModel


def _make_launcher() -> LauncherWindow:
    return LauncherWindow(CellDataModel(session=Session()))


def test_sidebar_has_batch_tools_tab_after_workflows(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    names = [b.text() for b in launcher._sidebar_buttons]
    assert "Batch Tools" in names
    assert names.index("Batch Tools") == names.index("Workflows") + 1


def test_batch_console_factory_returns_panel(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    panel = launcher._create_batch_console_panel()
    qtbot.addWidget(panel)
    assert isinstance(panel, BatchConsolePanel)
    # It manages its own scroll, so the launcher must not wrap it.
    assert panel.manages_own_scroll is True


def test_existing_tab_order_preserved(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    names = [b.text() for b in launcher._sidebar_buttons]
    # Tabs before the new one keep their positions.
    assert names[:6] == [
        "I/O",
        "Viewer",
        "Segmentation",
        "Analysis",
        "FLIM",
        "Workflows",
    ]


def test_reload_current_dataset_noop_when_nothing_open(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    called: list[str] = []
    launcher._load_h5_into_viewer = lambda p: called.append(p)  # type: ignore[assignment]
    # No _current_h5_path attribute set → no-op.
    launcher._reload_current_dataset()
    assert called == []


def test_reload_current_dataset_reopens_when_open(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    called: list[str] = []
    launcher._load_h5_into_viewer = lambda p: called.append(p)  # type: ignore[assignment]
    launcher._current_h5_path = "/data/open.h5"
    launcher._reload_current_dataset()
    assert called == ["/data/open.h5"]
