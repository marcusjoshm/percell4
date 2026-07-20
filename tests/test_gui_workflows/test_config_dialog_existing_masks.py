"""Tests for the two-pane existing-mask group builder in WorkflowConfigDialog.

Each group is a Datasets checklist (left) driving a Masks checklist (right,
the intersection of the checked datasets' available masks). A dataset's final
selection in ``existing_mask_selections`` is the UNION of the checked masks
across every group it is checked in — the per-dataset dict contract consumed by
SingleCellThresholdingRunner / WorkflowConfig is unchanged.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from qtpy.QtCore import Qt

from percell4.gui.workflows.single_cell.config_dialog import WorkflowConfigDialog
from percell4.store import DatasetStore


def _make_h5(tmp_path: Path, name: str, channels: list[str], masks: list[str]) -> Path:
    path = tmp_path / f"{name}.h5"
    store = DatasetStore(path)
    store.create(metadata={"channel_names": channels})
    store.write_array(
        "intensity",
        np.ones((len(channels), 16, 16), dtype=np.float32),
        attrs={"dims": ["C", "H", "W"]},
    )
    store.write_labels("cellpose", np.zeros((16, 16), dtype=np.int32))
    for m in masks:
        store.write_mask(m, np.zeros((16, 16), dtype=np.uint8))
    return path


@pytest.fixture
def dialog(qtbot):
    dlg = WorkflowConfigDialog()
    qtbot.addWidget(dlg)
    return dlg


def _check(list_widget, names, checked=True):
    """Check/uncheck items whose text is in ``names`` (fires itemChanged)."""
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


def _panel(dialog, index=0):
    return dialog._mask_group_panels[index]


# ── Basic listing / intersection ─────────────────────────────────────────


def test_panel_lists_mask_datasets_and_common_masks(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a", "b"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["a", "b"])
    ds3 = _make_h5(tmp_path, "DS3", ["GFP"], [])  # no masks -> excluded
    dialog._add_h5_paths([ds1, ds2, ds3])
    dialog._mask_selection_group.setChecked(True)

    assert len(dialog._mask_group_panels) == 1
    p = _panel(dialog)
    # Only mask-bearing datasets appear, and the first panel checks them all.
    assert _texts(p.ds_list) == {"DS1", "DS2"}
    assert _checked(p.ds_list) == {"DS1", "DS2"}
    # Masks = intersection of the checked datasets' masks (unchecked initially).
    assert _texts(p.mask_list) == {"a", "b"}
    assert _checked(p.mask_list) == set()
    # The no-mask dataset is reported as hidden.
    assert "1 dataset" in dialog._mask_excluded_note.text()


def test_masks_are_intersection_of_checked_datasets(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a", "b"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["a", "c"])
    dialog._add_h5_paths([ds1, ds2])
    dialog._mask_selection_group.setChecked(True)
    p = _panel(dialog)
    # All checked -> common masks = {a}.
    assert _texts(p.mask_list) == {"a"}
    # Uncheck DS2 -> intersection becomes DS1's full set {a, b}.
    _check(p.ds_list, {"DS2"}, checked=False)
    assert _texts(p.mask_list) == {"a", "b"}


def test_toggle_hides_rounds_group(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a"])
    dialog._add_h5_paths([ds1])
    assert dialog._rounds_group_box.isVisibleTo(dialog)
    dialog._mask_selection_group.setChecked(True)
    assert not dialog._rounds_group_box.isVisibleTo(dialog)
    dialog._mask_selection_group.setChecked(False)
    assert dialog._rounds_group_box.isVisibleTo(dialog)


# ── Fan-out / build config ───────────────────────────────────────────────


def test_existing_mask_selections_fans_to_checked_datasets(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a", "b"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["a", "b"])
    dialog._add_h5_paths([ds1, ds2])
    dialog._mask_selection_group.setChecked(True)
    p = _panel(dialog)
    _check(p.mask_list, {"a"})
    assert dialog.existing_mask_selections == {"DS1": ["a"], "DS2": ["a"]}


def test_build_config_existing_masks(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP", "RFP"], ["pbody"])
    dialog._add_h5_paths([ds1])
    dialog._output_edit.setText(str(tmp_path / "out"))
    dialog._mask_selection_group.setChecked(True)
    _check(_panel(dialog).mask_list, {"pbody"})

    cfg = dialog._try_build_config()
    assert cfg is not None
    assert cfg.use_existing_masks is True
    assert cfg.existing_mask_selections == {"DS1": ["pbody"]}
    assert cfg.thresholding_rounds == []
    assert "pbody_particle_count" in cfg.selected_csv_columns


def test_build_config_requires_a_mask(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a"])
    dialog._add_h5_paths([ds1])
    dialog._output_edit.setText(str(tmp_path / "out"))
    dialog._mask_selection_group.setChecked(True)
    # Datasets checked (default) but no mask checked.
    with patch.object(dialog, "_warn") as warn:
        cfg = dialog._try_build_config()
    assert cfg is None
    warn.assert_called_once()


def test_rounds_mode_still_requires_rounds_when_toggle_off(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a"])
    dialog._add_h5_paths([ds1])
    dialog._output_edit.setText(str(tmp_path / "out"))
    with patch.object(dialog, "_warn") as warn:
        cfg = dialog._try_build_config()
    assert cfg is None
    warn.assert_called_once()
    assert "thresholding round" in warn.call_args[0][0]


def test_start_enabled_tracks_mask_checks(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a"])
    dialog._add_h5_paths([ds1])
    assert not dialog._start_btn.isEnabled()  # rounds mode, no rounds
    dialog._mask_selection_group.setChecked(True)
    assert not dialog._start_btn.isEnabled()  # no mask checked yet
    _check(_panel(dialog).mask_list, {"a"})  # fires itemChanged
    assert dialog._start_btn.isEnabled()
    _check(_panel(dialog).mask_list, {"a"}, checked=False)
    assert not dialog._start_btn.isEnabled()


# ── Select All / Deselect All ────────────────────────────────────────────


def test_select_all_deselect_all_masks(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a", "b"])
    dialog._add_h5_paths([ds1])
    dialog._mask_selection_group.setChecked(True)
    p = _panel(dialog)
    dialog._select_all_masks(p, True)
    assert dialog.existing_mask_selections == {"DS1": ["a", "b"]}
    dialog._select_all_masks(p, False)
    assert dialog.existing_mask_selections == {}


def test_deselect_all_datasets_empties_mask_pane(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a", "b"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["a", "b"])
    dialog._add_h5_paths([ds1, ds2])
    dialog._mask_selection_group.setChecked(True)
    p = _panel(dialog)
    assert _texts(p.mask_list) == {"a", "b"}
    dialog._select_all_datasets(p, False)
    assert _texts(p.mask_list) == set()
    dialog._select_all_datasets(p, True)
    assert _texts(p.mask_list) == {"a", "b"}


# ── Add / remove groups + union ──────────────────────────────────────────


def test_add_group_union_for_unique_masks(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a", "b"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["a", "b"])
    ds3 = _make_h5(tmp_path, "DS3", ["GFP"], ["a", "b", "c"])
    dialog._add_h5_paths([ds1, ds2, ds3])
    dialog._mask_selection_group.setChecked(True)

    # Group 1: all datasets, common masks {a, b}.
    g1 = _panel(dialog, 0)
    assert _texts(g1.mask_list) == {"a", "b"}  # c not common
    _check(g1.mask_list, {"a", "b"})

    # Group 2: only DS3, its unique mask c.
    dialog._on_add_mask_group()
    assert len(dialog._mask_group_panels) == 2
    g2 = _panel(dialog, 1)
    assert _checked(g2.ds_list) == set()  # added group starts empty
    _check(g2.ds_list, {"DS3"})
    assert _texts(g2.mask_list) == {"a", "b", "c"}  # DS3's full set
    _check(g2.mask_list, {"c"})

    # Union: DS1/DS2 -> {a,b}; DS3 -> {a,b} + {c}.
    assert dialog.existing_mask_selections == {
        "DS1": ["a", "b"],
        "DS2": ["a", "b"],
        "DS3": ["a", "b", "c"],
    }


def test_remove_group(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a"])
    dialog._add_h5_paths([ds1])
    dialog._mask_selection_group.setChecked(True)
    assert _panel(dialog, 0).remove_btn is None  # first panel not removable
    dialog._on_add_mask_group()
    assert len(dialog._mask_group_panels) == 2
    g2 = _panel(dialog, 1)
    assert g2.remove_btn is not None
    g2.remove_btn.click()  # drive the wired button
    assert len(dialog._mask_group_panels) == 1


# ── Signal-path wiring + refresh preservation ────────────────────────────


def test_mask_check_signal_updates_selection_and_start(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["a"])
    dialog._add_h5_paths([ds1, ds2])
    dialog._mask_selection_group.setChecked(True)
    p = _panel(dialog)
    assert not dialog._start_btn.isEnabled()
    # Drive the item's checkState directly (fires itemChanged) — proves the wire.
    p.mask_list.item(0).setCheckState(Qt.Checked)
    assert dialog.existing_mask_selections == {"DS1": ["a"], "DS2": ["a"]}
    assert dialog._start_btn.isEnabled()


def test_dataset_check_signal_recomputes_masks(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a", "b"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["a"])
    dialog._add_h5_paths([ds1, ds2])
    dialog._mask_selection_group.setChecked(True)
    p = _panel(dialog)
    assert _texts(p.mask_list) == {"a"}  # all checked -> common {a}
    # Uncheck DS2 via the item signal -> intersection recomputed to DS1's {a,b}.
    for i in range(p.ds_list.count()):
        if p.ds_list.item(i).text() == "DS2":
            p.ds_list.item(i).setCheckState(Qt.Unchecked)
    assert _texts(p.mask_list) == {"a", "b"}


def test_mask_checks_preserved_across_toggle(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a", "b"])
    dialog._add_h5_paths([ds1])
    dialog._mask_selection_group.setChecked(True)
    _check(_panel(dialog).mask_list, {"b"})
    dialog._mask_selection_group.setChecked(False)
    dialog._mask_selection_group.setChecked(True)
    assert dialog.existing_mask_selections == {"DS1": ["b"]}


def test_new_dataset_auto_joins_first_group(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["a", "b"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["a", "b"])
    dialog._add_h5_paths([ds1])
    dialog._mask_selection_group.setChecked(True)
    _check(_panel(dialog).mask_list, {"a"})
    assert dialog.existing_mask_selections == {"DS1": ["a"]}
    # Adding a dataset auto-includes it in group 1 (checked) and inherits the
    # group's mask picks via the fan-out.
    dialog._add_h5_paths([ds2])
    p = _panel(dialog)
    assert _checked(p.ds_list) == {"DS1", "DS2"}
    assert dialog.existing_mask_selections == {"DS1": ["a"], "DS2": ["a"]}
