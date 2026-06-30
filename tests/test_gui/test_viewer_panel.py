"""Tests for the Viewer task panel (U1): Open / Hide Viewer buttons."""

from __future__ import annotations

from qtpy.QtWidgets import QPushButton

from percell4.interfaces.gui.task_panels.viewer_panel import ViewerPanel


class _FakeViewer:
    def __init__(self) -> None:
        self.hidden = 0

    def hide(self) -> None:
        self.hidden += 1


def _button(panel: ViewerPanel, text: str) -> QPushButton:
    return next(b for b in panel.findChildren(QPushButton) if b.text() == text)


def test_open_viewer_button_shows_viewer(qtbot) -> None:
    """Open Viewer delegates to show_window('viewer')."""
    shown: list[str] = []
    panel = ViewerPanel(
        object(),
        show_window=lambda key: shown.append(key),
        get_viewer_window=lambda: None,
    )
    qtbot.addWidget(panel)

    _button(panel, "Open Viewer").click()
    assert shown == ["viewer"]


def test_hide_viewer_button_hides_existing_viewer(qtbot) -> None:
    """Hide Viewer calls hide() on the live viewer window (not destroy)."""
    viewer = _FakeViewer()
    panel = ViewerPanel(
        object(),
        show_window=lambda key: None,
        get_viewer_window=lambda: viewer,
    )
    qtbot.addWidget(panel)

    _button(panel, "Hide Viewer").click()
    assert viewer.hidden == 1


def test_hide_viewer_no_op_when_no_viewer(qtbot) -> None:
    """Hide Viewer with no viewer yet is a silent no-op."""
    panel = ViewerPanel(
        object(),
        show_window=lambda key: None,
        get_viewer_window=lambda: None,
    )
    qtbot.addWidget(panel)

    _button(panel, "Hide Viewer").click()  # must not raise
