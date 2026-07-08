"""Tests for U2: LauncherWindow opens the SessionWindow on construction.

The SessionWindow is registered under ``_windows["session"]`` and is
visible from the moment the app launches.
"""

from __future__ import annotations

from percell4.interfaces.gui.main_window import LauncherWindow
from percell4.interfaces.gui.peer_views.session_window import SessionWindow
from percell4.model import CellDataModel


def test_launcher_registers_session_window(qtbot):
    """LauncherWindow construction creates and registers the SessionWindow."""
    model = CellDataModel()
    win = LauncherWindow(model)
    qtbot.addWidget(win)

    assert "session" in win._windows
    assert isinstance(win._windows["session"], SessionWindow)


def test_session_window_is_visible_after_launcher_construction(qtbot):
    """SessionWindow is shown automatically on launcher init."""
    model = CellDataModel()
    win = LauncherWindow(model)
    qtbot.addWidget(win)

    session_win = win._windows["session"]
    assert session_win.isVisible()


def test_session_window_shares_data_model_with_launcher(qtbot):
    """SessionWindow uses the same CellDataModel as the Launcher."""
    model = CellDataModel()
    win = LauncherWindow(model)
    qtbot.addWidget(win)

    session_win = win._windows["session"]
    assert session_win.data_model is model
