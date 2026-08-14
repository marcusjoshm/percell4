"""Wiring tests for the Add Data ▾ menu on IoPanel (U3).

Add Layer and Batch TCSPC are now actions inside the "Add Data" menu
button rather than standalone buttons.
"""

from __future__ import annotations

from qtpy.QtWidgets import QPushButton

from percell4.interfaces.gui.task_panels.io_panel import IoPanel


def _noop() -> None:
    pass


def _menu_actions(panel: IoPanel, button_text: str) -> dict:
    btn = next(b for b in panel.findChildren(QPushButton) if b.text() == button_text)
    menu = btn.menu()
    assert menu is not None, f"{button_text!r} has no menu"
    return {a.text(): a for a in menu.actions()}


def _base_panel(qtbot, **overrides) -> IoPanel:
    callbacks = dict(
        on_import=_noop,
        on_load=_noop,
        on_add_layer=_noop,
        on_close=_noop,
        on_export_csv=_noop,
        on_export_images=_noop,
        on_export_phasor_npz=_noop,
    )
    callbacks.update(overrides)
    panel = IoPanel(**callbacks)
    qtbot.addWidget(panel)
    return panel


def test_add_data_menu_has_layer_and_batch_tcspc(qtbot) -> None:
    actions = _menu_actions(_base_panel(qtbot), "Add Data")
    assert "Layer..." in actions
    assert "Batch TCSPC..." in actions


def test_batch_tcspc_action_invokes_callback(qtbot) -> None:
    calls: list[str] = []
    panel = _base_panel(qtbot, on_batch_tcspc=lambda: calls.append("batch"))
    _menu_actions(panel, "Add Data")["Batch TCSPC..."].trigger()
    assert calls == ["batch"]


def test_layer_action_invokes_callback(qtbot) -> None:
    calls: list[str] = []
    panel = _base_panel(qtbot, on_add_layer=lambda: calls.append("layer"))
    _menu_actions(panel, "Add Data")["Layer..."].trigger()
    assert calls == ["layer"]


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
    _menu_actions(panel, "Add Data")["Batch TCSPC..."].trigger()  # silent
