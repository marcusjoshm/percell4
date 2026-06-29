"""Wiring test for U4: 'Dilute phase mask from mask' button on the Workflows panel."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from qtpy.QtWidgets import QPushButton

from percell4.interfaces.gui.main_window import LauncherWindow
from percell4.model import CellDataModel

_DIALOG_PATH = "percell4.gui.dilute_from_mask_dialog.DiluteFromMaskDialog"


def _find_button(win: LauncherWindow, text: str) -> QPushButton:
    for btn in win.findChildren(QPushButton):
        if btn.text() == text:
            return btn
    raise AssertionError(f"{text!r} button not found")


def test_workflows_panel_has_dilute_from_mask_button(qtbot):
    """The Workflows sidebar exposes a 'Dilute phase mask from mask' button."""
    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    btn = _find_button(win, "Dilute phase mask from mask")
    assert btn is not None
    assert btn.toolTip()  # non-empty tooltip


def test_button_is_directly_below_dilute_generation(qtbot):
    """R1: the new button sits immediately below 'Dilute phase mask generation'."""
    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    gen = _find_button(win, "Dilute phase mask generation")
    new = _find_button(win, "Dilute phase mask from mask")
    layout = gen.parentWidget().layout()  # the shared Workflows QVBoxLayout
    assert layout.indexOf(new) == layout.indexOf(gen) + 1


def test_clicking_button_opens_dialog(qtbot):
    """Clicking instantiates DiluteFromMaskDialog and calls exec_() then deleteLater."""
    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    btn = _find_button(win, "Dilute phase mask from mask")

    fake_cls = MagicMock()
    fake_dialog = MagicMock()
    fake_dialog.last_report = None
    fake_cls.return_value = fake_dialog

    with patch(_DIALOG_PATH, fake_cls):
        btn.click()

    fake_cls.assert_called_once()
    fake_dialog.exec_.assert_called_once()
    fake_dialog.deleteLater.assert_called_once()


def test_button_respects_workflow_lock(qtbot):
    """When is_workflow_locked, the handler returns without opening a dialog."""
    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    win.set_workflow_locked(True)
    btn = _find_button(win, "Dilute phase mask from mask")

    fake_cls = MagicMock()
    with patch(_DIALOG_PATH, fake_cls):
        btn.click()

    # The reentrance guard short-circuits before the dialog is constructed.
    fake_cls.assert_not_called()


def test_handler_updates_status_bar_on_run(qtbot):
    """A completed run reports the dataset count in the status bar."""
    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    btn = _find_button(win, "Dilute phase mask from mask")

    fake_dialog = MagicMock()
    fake_report = MagicMock()
    fake_report.items = (MagicMock(), MagicMock())  # 2 datasets
    fake_dialog.last_report = fake_report

    with patch(_DIALOG_PATH, return_value=fake_dialog):
        btn.click()

    msg = win.statusBar().currentMessage()
    assert "Dilute-from-mask workflow complete" in msg
    assert "2 dataset(s)" in msg
