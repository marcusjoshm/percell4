"""SegmentationSelectDialog picker + default_segmentation_picks (U12)."""

from __future__ import annotations

from percell4.workflows.phases import default_segmentation_picks


def test_default_picks_prefer_tracked():
    picks = default_segmentation_picks({
        "DS1": ["cellpose", "cellpose_tracked"],
        "DS2": ["manual"],
        "DS3": [],  # no labels -> omitted
    })
    assert picks == {"DS1": "cellpose_tracked", "DS2": "manual"}


def test_dialog_defaults_to_tracked_and_reflects_changes(qtbot):
    from percell4.gui.workflows.single_cell.segmentation_select_dialog import (
        SegmentationSelectDialog,
    )

    dlg = SegmentationSelectDialog({
        "DS1": ["cellpose", "cellpose_tracked"],
        "DS2": ["manual"],
    })
    qtbot.addWidget(dlg)

    # Defaults: DS1 -> tracked, DS2 -> its only segmentation.
    assert dlg.picks == {"DS1": "cellpose_tracked", "DS2": "manual"}

    # Changing a combo is reflected in picks.
    dlg._combos["DS1"].setCurrentText("cellpose")
    assert dlg.picks["DS1"] == "cellpose"
