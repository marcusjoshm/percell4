"""Wiring test for the new Batch TCSPC Append button on IoPanel (U4)."""

from __future__ import annotations

from qtpy.QtWidgets import QPushButton

from percell4.interfaces.gui.task_panels.io_panel import IoPanel


def _noop() -> None:
    pass


def test_batch_tcspc_button_invokes_injected_callback(qtbot) -> None:
    """Clicking the new button fires the injected on_batch_tcspc callable."""
    calls: list[str] = []
    panel = IoPanel(
        on_import=_noop,
        on_load=_noop,
        on_add_layer=_noop,
        on_close=_noop,
        on_export_csv=_noop,
        on_export_images=_noop,
        on_export_phasor_npz=_noop,
        on_batch_tcspc=lambda: calls.append("clicked"),
    )
    qtbot.addWidget(panel)

    btn = next(
        b for b in panel.findChildren(QPushButton)
        if b.text() == "Batch TCSPC Append..."
    )
    btn.click()
    assert calls == ["clicked"]


def test_on_batch_tcspc_defaults_to_noop(qtbot) -> None:
    """Constructing without on_batch_tcspc must not crash — default is a no-op."""
    panel = IoPanel(
        on_import=_noop,
        on_load=_noop,
        on_add_layer=_noop,
        on_close=_noop,
        on_export_csv=_noop,
        on_export_images=_noop,
        on_export_phasor_npz=_noop,
    )
    qtbot.addWidget(panel)
    btn = next(
        b for b in panel.findChildren(QPushButton)
        if b.text() == "Batch TCSPC Append..."
    )
    btn.click()  # should be silent
