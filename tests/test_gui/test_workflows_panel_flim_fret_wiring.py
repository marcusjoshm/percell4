"""Wiring test for U5: FLIM-FRET analysis button on the Workflows panel."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from qtpy.QtWidgets import QPushButton

from percell4.interfaces.gui.main_window import LauncherWindow
from percell4.model import CellDataModel


def _find_flim_fret_button(win: LauncherWindow) -> QPushButton:
    for btn in win.findChildren(QPushButton):
        if btn.text() == "FLIM-FRET analysis":
            return btn
    raise AssertionError("FLIM-FRET analysis button not found")


def test_workflows_panel_has_flim_fret_button(qtbot):
    """The Workflows sidebar exposes a button labeled 'FLIM-FRET analysis'."""
    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    btn = _find_flim_fret_button(win)
    assert btn is not None
    assert btn.toolTip()  # non-empty tooltip


def test_clicking_button_opens_flim_fret_dialog(qtbot):
    """Clicking the button instantiates FlimFretDialog and calls exec_().

    The dialog itself is mocked so the test doesn't open a modal window.
    """
    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    btn = _find_flim_fret_button(win)

    fake_dialog_cls = MagicMock()
    fake_dialog = MagicMock()
    fake_dialog.last_run_folder = None
    fake_dialog_cls.return_value = fake_dialog

    with patch(
        "percell4.gui.flim_fret_dialog.FlimFretDialog", fake_dialog_cls
    ):
        btn.click()

    fake_dialog_cls.assert_called_once()
    fake_dialog.exec_.assert_called_once()
    fake_dialog.deleteLater.assert_called_once()


def test_button_respects_workflow_lock(qtbot):
    """When is_workflow_locked, the handler returns without opening a dialog."""
    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    win.set_workflow_locked(True)
    btn = _find_flim_fret_button(win)

    fake_dialog_cls = MagicMock()
    with patch(
        "percell4.gui.flim_fret_dialog.FlimFretDialog", fake_dialog_cls
    ):
        btn.click()

    # The reentrance guard short-circuits before FlimFretDialog is constructed.
    fake_dialog_cls.assert_not_called()


def test_handler_updates_status_bar_on_successful_run(qtbot, tmp_path):
    """When the dialog reports a run_folder, the status bar shows the CSV path."""
    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    btn = _find_flim_fret_button(win)

    fake_dialog = MagicMock()
    fake_run_folder = tmp_path / "flim_fret_run_x"
    fake_dialog.last_run_folder = fake_run_folder

    with patch(
        "percell4.gui.flim_fret_dialog.FlimFretDialog",
        return_value=fake_dialog,
    ):
        btn.click()

    msg = win.statusBar().currentMessage()
    assert "FLIM-FRET run complete" in msg
    assert "flim_fret_results.csv" in msg
