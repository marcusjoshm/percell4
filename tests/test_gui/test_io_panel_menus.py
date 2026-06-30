"""Top-level control tests for the consolidated I/O panel (U3).

The I/O tab is five controls: New Dataset, Open Dataset, Add Data ▾,
Close Dataset, Export ▾. The two menu buttons are covered in detail by
test_io_panel_batch_tcspc_wiring.py and test_io_panel_export_phasor.py.
"""

from __future__ import annotations

from qtpy.QtWidgets import QPushButton

from percell4.interfaces.gui.task_panels.io_panel import IoPanel


def _panel(qtbot, **overrides) -> IoPanel:
    calls = dict(
        on_import=lambda: None,
        on_load=lambda: None,
        on_add_layer=lambda: None,
        on_close=lambda: None,
        on_export_csv=lambda: None,
        on_export_images=lambda: None,
        on_export_phasor_npz=lambda: None,
    )
    calls.update(overrides)
    p = IoPanel(**calls)
    qtbot.addWidget(p)
    return p


def test_five_top_level_controls_present(qtbot) -> None:
    labels = [b.text() for b in _panel(qtbot).findChildren(QPushButton)]
    assert labels == [
        "New Dataset...",
        "Open Dataset...",
        "Add Data",
        "Close Dataset",
        "Export",
    ]


def test_new_open_close_invoke_callbacks(qtbot) -> None:
    calls: list[str] = []
    panel = _panel(
        qtbot,
        on_import=lambda: calls.append("new"),
        on_load=lambda: calls.append("open"),
        on_close=lambda: calls.append("close"),
    )
    by_text = {b.text(): b for b in panel.findChildren(QPushButton)}
    by_text["New Dataset..."].click()
    by_text["Open Dataset..."].click()
    by_text["Close Dataset"].click()
    assert calls == ["new", "open", "close"]


def test_add_data_and_export_are_menu_buttons(qtbot) -> None:
    by_text = {b.text(): b for b in _panel(qtbot).findChildren(QPushButton)}
    assert by_text["Add Data"].menu() is not None
    assert by_text["Export"].menu() is not None
