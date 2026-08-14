"""Tests for the Export ▾ menu on IoPanel (U3).

The three export targets are now actions inside the "Export" menu button
rather than standalone buttons. The IoPanel is a thin Tier-1 panel — its
only job is to wire actions to injected callbacks.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from qtpy.QtWidgets import QPushButton

from percell4.interfaces.gui.task_panels.io_panel import IoPanel


@pytest.fixture
def panel(qtbot):
    callbacks = {
        name: MagicMock()
        for name in (
            "on_import",
            "on_load",
            "on_add_layer",
            "on_close",
            "on_export_csv",
            "on_export_images",
            "on_export_phasor_npz",
        )
    }
    p = IoPanel(**callbacks)
    qtbot.addWidget(p)
    p._test_callbacks = callbacks
    return p


def _export_actions(panel) -> dict:
    btn = next(b for b in panel.findChildren(QPushButton) if b.text() == "Export")
    menu = btn.menu()
    assert menu is not None
    return {a.text(): a for a in menu.actions()}


def test_export_menu_has_all_three_targets(panel):
    actions = _export_actions(panel)
    assert "Measurements (CSV)..." in actions
    assert "Images (TIFF)..." in actions
    assert "Phasor (.npz)..." in actions


def test_export_phasor_action_invokes_callback(panel):
    _export_actions(panel)["Phasor (.npz)..."].trigger()
    panel._test_callbacks["on_export_phasor_npz"].assert_called_once()


def test_export_csv_and_images_actions_invoke_callbacks(panel):
    actions = _export_actions(panel)
    actions["Measurements (CSV)..."].trigger()
    actions["Images (TIFF)..."].trigger()
    panel._test_callbacks["on_export_csv"].assert_called_once()
    panel._test_callbacks["on_export_images"].assert_called_once()


def test_export_phasor_action_has_tooltip(panel):
    act = _export_actions(panel)["Phasor (.npz)..."]
    assert act.toolTip() != ""
    assert "phasor" in act.toolTip().lower()
