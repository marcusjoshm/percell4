"""Tests for the existing-mask reuse UI in WorkflowConfigDialog (plan U3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

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


def _select(list_widget, names):
    for i in range(list_widget.count()):
        if list_widget.item(i).text() in names:
            list_widget.item(i).setSelected(True)


def test_mask_picker_lists_masks_and_handles_empty(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP", "RFP"], ["pbody", "grouped"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP", "RFP"], [])  # no masks
    dialog._add_h5_paths([ds1, ds2])

    has_by_name = {pd.display_name: has for pd, lw, has in dialog._mask_lists}
    lw_by_name = {pd.display_name: lw for pd, lw, has in dialog._mask_lists}
    items1 = {lw_by_name["DS1"].item(i).text() for i in range(lw_by_name["DS1"].count())}
    assert items1 == {"pbody", "grouped"}
    assert has_by_name["DS1"] is True
    # Dataset with no masks shows a non-selectable "No masks found" row.
    assert lw_by_name["DS2"].item(0).text() == "No masks found"
    assert has_by_name["DS2"] is False


def test_toggle_hides_rounds_group(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["pbody"])
    dialog._add_h5_paths([ds1])
    assert dialog._rounds_group_box.isVisibleTo(dialog)  # visible by default
    dialog._mask_selection_group.setChecked(True)
    assert not dialog._rounds_group_box.isVisibleTo(dialog)
    dialog._mask_selection_group.setChecked(False)
    assert dialog._rounds_group_box.isVisibleTo(dialog)


def test_existing_mask_selections_property(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["pbody", "grouped"])
    dialog._add_h5_paths([ds1])
    dialog._mask_selection_group.setChecked(True)  # enable the picker
    lw = {pd.display_name: lw for pd, lw, _ in dialog._mask_lists}["DS1"]
    _select(lw, {"pbody"})
    assert dialog.existing_mask_selections == {"DS1": ["pbody"]}


def test_build_config_existing_masks(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP", "RFP"], ["pbody"])
    dialog._add_h5_paths([ds1])
    dialog._output_edit.setText(str(tmp_path / "out"))
    dialog._mask_selection_group.setChecked(True)
    lw = {pd.display_name: lw for pd, lw, _ in dialog._mask_lists}["DS1"]
    _select(lw, {"pbody"})

    cfg = dialog._try_build_config()
    assert cfg is not None
    assert cfg.use_existing_masks is True
    assert cfg.existing_mask_selections == {"DS1": ["pbody"]}
    assert cfg.thresholding_rounds == []
    # CSV columns carry the mask's particle columns.
    assert "pbody_particle_count" in cfg.selected_csv_columns


def test_build_config_mask_reuse_requires_a_selection(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["pbody"])
    dialog._add_h5_paths([ds1])
    dialog._output_edit.setText(str(tmp_path / "out"))
    dialog._mask_selection_group.setChecked(True)
    # No mask selected.
    with patch.object(dialog, "_warn") as warn:
        cfg = dialog._try_build_config()
    assert cfg is None
    warn.assert_called_once()


def test_rounds_mode_still_requires_rounds_when_toggle_off(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["pbody"])
    dialog._add_h5_paths([ds1])
    dialog._output_edit.setText(str(tmp_path / "out"))
    # Toggle off (default) + no rounds → original guard fires.
    with patch.object(dialog, "_warn") as warn:
        cfg = dialog._try_build_config()
    assert cfg is None
    warn.assert_called_once()
    assert "thresholding round" in warn.call_args[0][0]


def test_start_enabled_in_mask_reuse_mode_without_rounds(dialog, tmp_path):
    """Regression: Start must enable on a mask selection, not require a round."""
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["pbody"])
    dialog._add_h5_paths([ds1])
    # Rounds mode, no rounds → Start disabled (the original gate).
    assert not dialog._start_btn.isEnabled()
    # Mask-reuse on but nothing selected yet → still disabled.
    dialog._mask_selection_group.setChecked(True)
    assert not dialog._start_btn.isEnabled()
    # Selecting a mask flips Start on without any thresholding round.
    lw = {pd.display_name: lw for pd, lw, _ in dialog._mask_lists}["DS1"]
    _select(lw, {"pbody"})
    assert dialog._start_btn.isEnabled()
    assert dialog._rounds_table.rowCount() == 0
    # Toggling back to rounds mode (no rounds) disables Start again.
    dialog._mask_selection_group.setChecked(False)
    assert not dialog._start_btn.isEnabled()


def test_mask_selections_preserved_across_toggle(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["pbody", "grouped"])
    dialog._add_h5_paths([ds1])
    dialog._mask_selection_group.setChecked(True)
    lw = {pd.display_name: lw for pd, lw, _ in dialog._mask_lists}["DS1"]
    _select(lw, {"grouped"})
    # Toggling the group off and on does not rebuild the lists / drop picks.
    dialog._mask_selection_group.setChecked(False)
    dialog._mask_selection_group.setChecked(True)
    assert dialog.existing_mask_selections == {"DS1": ["grouped"]}
