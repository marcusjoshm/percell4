"""Tests for the Export Phasor (.npz) button on IoPanel.

The IoPanel is a thin Tier-1 panel — its only job is to wire button
clicks to injected callbacks. The launcher's _on_export_phasor_npz
handler does the real work; that's tested via U4's use-case tests
plus the dialog flow here.
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


def test_export_phasor_button_present_with_correct_label(panel):
    buttons = panel.findChildren(QPushButton)
    labels = [b.text() for b in buttons]
    assert "Export Phasor (.npz)..." in labels


def test_export_phasor_button_invokes_callback(panel, qtbot):
    buttons = panel.findChildren(QPushButton)
    btn = next(b for b in buttons if b.text() == "Export Phasor (.npz)...")
    btn.click()
    panel._test_callbacks["on_export_phasor_npz"].assert_called_once()


def test_export_phasor_button_has_tooltip(panel):
    buttons = panel.findChildren(QPushButton)
    btn = next(b for b in buttons if b.text() == "Export Phasor (.npz)...")
    assert btn.toolTip() != ""
    assert "phasor" in btn.toolTip().lower()


def test_existing_export_buttons_still_present(panel):
    """Regression guard: don't accidentally remove the existing export buttons."""
    buttons = panel.findChildren(QPushButton)
    labels = [b.text() for b in buttons]
    assert "Export Measurements to CSV..." in labels
    assert "Export Images..." in labels
