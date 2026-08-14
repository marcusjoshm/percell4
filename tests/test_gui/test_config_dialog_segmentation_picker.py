"""Two-pane segmentation group builder in the workflow config dialog.

Each group is a Datasets checklist (left) driving a single-pick Segmentation
list (right = intersection of the checked datasets' /labels). A dataset's
segmentation in ``segmentation_overrides`` is the pick of the LAST group it is
checked in; datasets left unpicked are omitted so the runner auto-detects their
preferred (tracked) layer. Datasets with no /labels are excluded (Cellpose runs).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from qtpy.QtCore import Qt

from percell4.gui.workflows.single_cell.config_dialog import WorkflowConfigDialog
from percell4.store import DatasetStore


def _h5(tmp_path: Path, name: str, labels: list[str]) -> Path:
    p = tmp_path / f"{name}.h5"
    s = DatasetStore(p)
    s.create(metadata={"channel_names": ["GFP"]})
    s.write_array("intensity", np.ones((1, 8, 8), np.float32), attrs={"dims": ["C", "H", "W"]})
    cell = np.zeros((8, 8), np.int32)
    cell[2:5, 2:5] = 1
    for lab in labels:
        s.write_labels(lab, cell)
    return p


@pytest.fixture
def dialog(qtbot):
    dlg = WorkflowConfigDialog()
    qtbot.addWidget(dlg)
    return dlg


def _check(list_widget, names, checked=True):
    state = Qt.Checked if checked else Qt.Unchecked
    for i in range(list_widget.count()):
        it = list_widget.item(i)
        if it.text() in names:
            it.setCheckState(state)


def _checked(list_widget):
    return {
        list_widget.item(i).text()
        for i in range(list_widget.count())
        if list_widget.item(i).checkState() == Qt.Checked
    }


def _texts(list_widget):
    return {list_widget.item(i).text() for i in range(list_widget.count())}


def test_panel_lists_datasets_and_common_segmentations(dialog, tmp_path):
    p1 = _h5(tmp_path, "DS1", ["cellpose", "cellpose_tracked", "cellpose_tracked_tracked"])
    p2 = _h5(tmp_path, "DS2", ["cellpose", "cellpose_tracked"])
    p3 = _h5(tmp_path, "DS3", [])  # no labels -> excluded (Cellpose)
    dialog._add_h5_paths([p1, p2, p3])

    assert len(dialog._seg_group_panels) == 1
    panel = dialog._seg_group_panels[0]
    assert _texts(panel.ds_list) == {"DS1", "DS2"}
    assert _checked(panel.ds_list) == {"DS1", "DS2"}  # first group checks all
    # Segmentations common to the checked datasets (tracked_tracked is DS1-only).
    assert _texts(panel.seg_list) == {"cellpose", "cellpose_tracked"}
    assert _checked(panel.seg_list) == set()  # nothing picked by default
    assert "1 dataset" in dialog._seg_excluded_note.text()


def test_overrides_empty_until_pick_then_fans_out(dialog, tmp_path):
    p1 = _h5(tmp_path, "DS1", ["cellpose", "cellpose_tracked"])
    p2 = _h5(tmp_path, "DS2", ["cellpose", "cellpose_tracked"])
    dialog._add_h5_paths([p1, p2])
    # Nothing picked -> empty overrides -> runner auto-detects preferred layer.
    assert dialog.segmentation_overrides == {}
    _check(dialog._seg_group_panels[0].seg_list, {"cellpose_tracked"})
    assert dialog.segmentation_overrides == {
        "DS1": "cellpose_tracked",
        "DS2": "cellpose_tracked",
    }


def test_single_pick_is_exclusive(dialog, tmp_path):
    p1 = _h5(tmp_path, "DS1", ["cellpose", "cellpose_tracked"])
    dialog._add_h5_paths([p1])
    panel = dialog._seg_group_panels[0]
    _check(panel.seg_list, {"cellpose"})
    assert _checked(panel.seg_list) == {"cellpose"}
    # Checking a second segmentation unchecks the first (pick-one).
    _check(panel.seg_list, {"cellpose_tracked"})
    assert _checked(panel.seg_list) == {"cellpose_tracked"}
    assert dialog.segmentation_overrides == {"DS1": "cellpose_tracked"}


def test_segmentations_are_intersection_of_checked_datasets(dialog, tmp_path):
    p1 = _h5(tmp_path, "DS1", ["cellpose", "cellpose_tracked", "cellpose_tracked_tracked"])
    p2 = _h5(tmp_path, "DS2", ["cellpose", "cellpose_tracked"])
    dialog._add_h5_paths([p1, p2])
    panel = dialog._seg_group_panels[0]
    assert _texts(panel.seg_list) == {"cellpose", "cellpose_tracked"}
    _check(panel.ds_list, {"DS2"}, checked=False)  # DS1 alone
    assert _texts(panel.seg_list) == {
        "cellpose",
        "cellpose_tracked",
        "cellpose_tracked_tracked",
    }


def test_add_group_overrides_subset_last_wins(dialog, tmp_path):
    p1 = _h5(tmp_path, "DS1", ["cellpose", "cellpose_tracked"])
    p2 = _h5(tmp_path, "DS2", ["cellpose", "cellpose_tracked"])
    dialog._add_h5_paths([p1, p2])
    g1 = dialog._seg_group_panels[0]
    _check(g1.seg_list, {"cellpose_tracked"})  # baseline: both -> tracked

    dialog._on_add_seg_group()
    g2 = dialog._seg_group_panels[1]
    assert _checked(g2.ds_list) == set()  # added group starts empty
    _check(g2.ds_list, {"DS2"})
    _check(g2.seg_list, {"cellpose"})  # DS2 overridden to cellpose
    assert dialog.segmentation_overrides == {
        "DS1": "cellpose_tracked",
        "DS2": "cellpose",
    }


def test_remove_group(dialog, tmp_path):
    p1 = _h5(tmp_path, "DS1", ["cellpose"])
    dialog._add_h5_paths([p1])
    assert dialog._seg_group_panels[0].remove_btn is None
    dialog._on_add_seg_group()
    assert len(dialog._seg_group_panels) == 2
    g2 = dialog._seg_group_panels[1]
    assert g2.remove_btn is not None
    g2.remove_btn.click()
    assert len(dialog._seg_group_panels) == 1


def test_deselect_all_datasets_empties_seg_pane(dialog, tmp_path):
    p1 = _h5(tmp_path, "DS1", ["cellpose", "cellpose_tracked"])
    dialog._add_h5_paths([p1])
    panel = dialog._seg_group_panels[0]
    assert _texts(panel.seg_list) == {"cellpose", "cellpose_tracked"}
    dialog._select_all_seg_datasets(panel, False)
    assert _texts(panel.seg_list) == set()


def test_seg_check_signal_updates_overrides(dialog, tmp_path):
    p1 = _h5(tmp_path, "DS1", ["cellpose", "cellpose_tracked"])
    p2 = _h5(tmp_path, "DS2", ["cellpose", "cellpose_tracked"])
    dialog._add_h5_paths([p1, p2])
    panel = dialog._seg_group_panels[0]
    # Drive the item's checkState directly (fires itemChanged) — proves the wire.
    for i in range(panel.seg_list.count()):
        if panel.seg_list.item(i).text() == "cellpose":
            panel.seg_list.item(i).setCheckState(Qt.Checked)
    assert dialog.segmentation_overrides == {"DS1": "cellpose", "DS2": "cellpose"}
