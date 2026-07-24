"""Tests for the launcher's Batch Tools tab wiring + reload hook (U2, U6).

The Batch Tools tab now opens a dedicated window (U2): the sidebar page is a
compact ``BatchToolsPanel`` (Open/Hide), and selecting the tab auto-opens the
``BatchToolsWindow`` registered in the launcher's ``_windows`` registry.
"""

from __future__ import annotations

from percell4.application.session import Session
from percell4.interfaces.gui.main_window import LauncherWindow
from percell4.interfaces.gui.peer_views.batch_tools_window import BatchToolsWindow
from percell4.interfaces.gui.task_panels.batch_tools_panel import BatchToolsPanel
from percell4.model import CellDataModel


def _make_launcher() -> LauncherWindow:
    return LauncherWindow(CellDataModel(session=Session()))


def test_sidebar_has_batch_tools_tab_after_workflows(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    names = [b.text() for b in launcher._sidebar_buttons]
    assert "Batch Tools" in names
    assert names.index("Batch Tools") == names.index("Workflows") + 1


def test_batch_tools_factory_returns_panel(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    panel = launcher._create_batch_tools_panel()
    qtbot.addWidget(panel)
    # The Batch Tools tab now hosts a compact Open/Hide page, not the console.
    assert isinstance(panel, BatchToolsPanel)


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


# ── Auto-open on tab select (U2) ────────────────────────────


def test_selecting_batch_tools_tab_auto_opens_window(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    calls: list[str] = []
    launcher._show_window = lambda key: calls.append(key)  # type: ignore[assignment]
    launcher._on_sidebar_click(launcher._batch_tools_index)
    assert "batch_tools" in calls


def test_other_tabs_do_not_auto_open_batch_tools(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    calls: list[str] = []
    launcher._show_window = lambda key: calls.append(key)  # type: ignore[assignment]
    launcher._on_sidebar_click(0)  # I/O tab — must not auto-open the window
    assert "batch_tools" not in calls


def test_batch_tools_window_factory_returns_and_caches_window(qtbot):
    launcher = _make_launcher()
    qtbot.addWidget(launcher)
    win = launcher._get_or_create_window("batch_tools")
    qtbot.addWidget(win)
    assert isinstance(win, BatchToolsWindow)
    assert launcher._windows.get("batch_tools") is win
    # Second call returns the same instance (lazy singleton).
    assert launcher._get_or_create_window("batch_tools") is win


# ── Reload hook (U6, unchanged) ─────────────────────────────


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
