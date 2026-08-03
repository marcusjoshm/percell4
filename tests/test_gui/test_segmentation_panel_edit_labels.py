"""Structural tests for the merged Edit Labels section (U4).

Manual Editing + Label Cleanup are now one "Edit Labels" group, and the
redundant "Save Labels to HDF5" button is gone (auto-save covers it).
"""

from __future__ import annotations

from qtpy.QtWidgets import QGroupBox, QLabel, QPushButton

from percell4.gui.segmentation_panel import SegmentationPanel
from percell4.model import CellDataModel


def _panel(qtbot) -> SegmentationPanel:
    panel = SegmentationPanel(CellDataModel(), launcher=None)
    qtbot.addWidget(panel)
    return panel


def test_no_save_labels_button(qtbot) -> None:
    panel = _panel(qtbot)
    labels = [b.text() for b in panel.findChildren(QPushButton)]
    assert "Save Labels to HDF5" not in labels


def test_no_standalone_manual_editing_or_label_cleanup_groups(qtbot) -> None:
    panel = _panel(qtbot)
    titles = {g.title() for g in panel.findChildren(QGroupBox)}
    assert "Manual Editing" not in titles
    assert "Label Cleanup" not in titles
    assert "Save" not in titles
    assert "Edit Labels" in titles


def test_edit_labels_group_holds_all_controls(qtbot) -> None:
    panel = _panel(qtbot)
    edit_group = next(
        g for g in panel.findChildren(QGroupBox) if g.title() == "Edit Labels"
    )
    btns = {b.text() for b in edit_group.findChildren(QPushButton)}
    assert {
        "Create Empty Labels Layer",
        "Add New Label (next ID)",
        "Delete Selected Label",
        "Clean Up Labels (relabel sequential)",
        "Preview Removal",
        "Apply Removal",
    } <= btns


def test_auto_saved_reassurance_label_present(qtbot) -> None:
    panel = _panel(qtbot)
    texts = [lbl.text() for lbl in panel.findChildren(QLabel)]
    assert any("auto-saved" in t.lower() for t in texts)
