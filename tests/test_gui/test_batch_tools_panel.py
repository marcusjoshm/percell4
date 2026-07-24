"""Tests for the Batch Tools sidebar page (U2): Open / Hide the window."""

from __future__ import annotations

from pathlib import Path

from qtpy.QtWidgets import QPushButton

from percell4.interfaces.gui.task_panels.batch_tools_panel import BatchToolsPanel


class _FakeWindow:
    def __init__(self) -> None:
        self.hidden = 0

    def hide(self) -> None:
        self.hidden += 1


def _button(panel: BatchToolsPanel, text: str) -> QPushButton:
    return next(b for b in panel.findChildren(QPushButton) if b.text() == text)


def _make(*, shown=None, window=None) -> BatchToolsPanel:
    return BatchToolsPanel(
        show_window=(lambda key: shown.append(key))
        if shown is not None
        else (lambda key: None),
        get_batch_tools_window=lambda: window,
    )


def test_open_button_shows_batch_tools_window(qtbot) -> None:
    shown: list[str] = []
    panel = _make(shown=shown)
    qtbot.addWidget(panel)
    _button(panel, "Open Batch Tools").click()
    assert shown == ["batch_tools"]


def test_hide_button_hides_existing_window(qtbot) -> None:
    win = _FakeWindow()
    panel = _make(window=win)
    qtbot.addWidget(panel)
    _button(panel, "Hide Batch Tools").click()
    assert win.hidden == 1


def test_hide_button_noop_when_no_window(qtbot) -> None:
    panel = _make(window=None)
    qtbot.addWidget(panel)
    _button(panel, "Hide Batch Tools").click()  # must not raise


def test_panel_never_writes_session_fields() -> None:
    # Action-class guard: the page must not mutate the five session fields.
    src = Path(
        "src/percell4/interfaces/gui/task_panels/batch_tools_panel.py"
    ).read_text()
    for forbidden in (
        "set_active_channel",
        "set_active_segmentation",
        "set_active_mask",
        "set_filter",
        "set_selection",
        ".selection =",
    ):
        assert forbidden not in src, f"panel must not call {forbidden}"
