"""Tests for the grouped existing-mask reuse UI in WorkflowConfigDialog.

Datasets sharing an identical set of available ``/masks`` layers collapse
into one shared picker; the ``existing_mask_selections`` property fans a
group's selection out to one dict entry per member (plan U2). A subset of a
group can be split into its own sub-group (plan U4, covered separately).
"""

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


def _group_for(dialog, name):
    """Return the _MaskGroup whose members include ``name``."""
    for g in dialog._mask_groups:
        if any(pd.display_name == name for pd in g.members):
            return g
    raise KeyError(name)


def _lw_for(dialog, name):
    """Return the shared mask list widget for the group containing ``name``."""
    return _group_for(dialog, name).list_widget


def test_mask_picker_groups_masks_and_handles_empty(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP", "RFP"], ["pbody", "grouped"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP", "RFP"], [])  # no masks
    dialog._add_h5_paths([ds1, ds2])

    g1 = _group_for(dialog, "DS1")
    assert g1.has_masks is True
    items1 = {g1.list_widget.item(i).text() for i in range(g1.list_widget.count())}
    assert items1 == {"pbody", "grouped"}
    # Dataset with no masks lands in a separate, non-selectable group.
    g2 = _group_for(dialog, "DS2")
    assert g2.has_masks is False
    assert g2.list_widget is None
    assert g2 is not g1


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
    _select(_lw_for(dialog, "DS1"), {"pbody"})
    assert dialog.existing_mask_selections == {"DS1": ["pbody"]}


def test_build_config_existing_masks(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP", "RFP"], ["pbody"])
    dialog._add_h5_paths([ds1])
    dialog._output_edit.setText(str(tmp_path / "out"))
    dialog._mask_selection_group.setChecked(True)
    _select(_lw_for(dialog, "DS1"), {"pbody"})

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
    _select(_lw_for(dialog, "DS1"), {"pbody"})
    assert dialog._start_btn.isEnabled()
    assert dialog._rounds_table.rowCount() == 0
    # Toggling back to rounds mode (no rounds) disables Start again.
    dialog._mask_selection_group.setChecked(False)
    assert not dialog._start_btn.isEnabled()


def test_mask_can_be_unselected_with_a_click(dialog, tmp_path):
    """A previously selected mask can be unselected (MultiSelection mode)."""
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["pbody", "grouped"])
    dialog._add_h5_paths([ds1])
    dialog._mask_selection_group.setChecked(True)
    lw = _lw_for(dialog, "DS1")
    _select(lw, {"pbody"})
    assert dialog.existing_mask_selections == {"DS1": ["pbody"]}
    # Toggling the same item off clears the selection — no datasets remain,
    # which also disables Start.
    for i in range(lw.count()):
        if lw.item(i).text() == "pbody":
            lw.item(i).setSelected(False)
    assert dialog.existing_mask_selections == {}
    assert not dialog._start_btn.isEnabled()


def test_mask_selections_preserved_across_toggle(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["pbody", "grouped"])
    dialog._add_h5_paths([ds1])
    dialog._mask_selection_group.setChecked(True)
    _select(_lw_for(dialog, "DS1"), {"grouped"})
    # Toggling the group off and on does not rebuild the lists / drop picks.
    dialog._mask_selection_group.setChecked(False)
    dialog._mask_selection_group.setChecked(True)
    assert dialog.existing_mask_selections == {"DS1": ["grouped"]}


# ── Grouping-specific behavior (plan U2) ─────────────────────────────────


def test_identical_masks_share_one_group_and_fan_out(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["m1", "m2"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["m1", "m2"])
    dialog._add_h5_paths([ds1, ds2])
    # Both datasets collapse into one shared, selectable group.
    selectable = [g for g in dialog._mask_groups if g.has_masks]
    assert len(selectable) == 1
    g = selectable[0]
    assert {pd.display_name for pd in g.members} == {"DS1", "DS2"}
    dialog._mask_selection_group.setChecked(True)
    _select(g.list_widget, {"m1", "m2"})
    # Fan-out: one entry per member, identical lists, keyed by display_name.
    assert dialog.existing_mask_selections == {
        "DS1": ["m1", "m2"],
        "DS2": ["m1", "m2"],
    }


def test_different_masks_are_separate_groups(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["m1", "m2"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["m1"])
    dialog._add_h5_paths([ds1, ds2])
    dialog._mask_selection_group.setChecked(True)
    g1 = _group_for(dialog, "DS1")
    g2 = _group_for(dialog, "DS2")
    assert g1 is not g2
    _select(g1.list_widget, {"m2"})
    # Selecting in one group leaves the other untouched.
    assert dialog.existing_mask_selections == {"DS1": ["m2"]}


def test_selection_preserved_across_refresh(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["m1", "m2"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["m1", "m2"])
    dialog._add_h5_paths([ds1])
    dialog._mask_selection_group.setChecked(True)
    _select(_lw_for(dialog, "DS1"), {"m1"})
    # Adding a same-signature dataset rebuilds the picker.
    dialog._add_h5_paths([ds2])
    g = _group_for(dialog, "DS1")
    assert {pd.display_name for pd in g.members} == {"DS1", "DS2"}
    # Selection survives the rebuild (keyed by signature) and fans to both.
    assert dialog.existing_mask_selections == {"DS1": ["m1"], "DS2": ["m1"]}


def test_no_mask_group_renders_last(dialog, tmp_path):
    # No-mask dataset added FIRST; its group must still sort last.
    dsn = _make_h5(tmp_path, "DSN", ["GFP"], [])
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["m1"])
    dialog._add_h5_paths([dsn, ds1])
    assert dialog._mask_groups[0].has_masks is True
    assert dialog._mask_groups[-1].has_masks is False


def test_singleton_group_has_no_collapse_toggle(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["m1"])
    dialog._add_h5_paths([ds1])
    g = _group_for(dialog, "DS1")
    # A one-member group shows its name inline — no collapse toggle.
    assert g.toggle_btn is None


def test_multi_member_group_collapse_reveals_members(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["m1"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["m1"])
    dialog._add_h5_paths([ds1, ds2])
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    assert g.toggle_btn is not None
    assert g.members_list is not None
    # Collapsed by default; the toggle reveals the member names.
    assert not g.members_list.isVisibleTo(dialog)
    g.toggle_btn.setChecked(True)
    assert g.members_list.isVisibleTo(dialog)
    names = {g.members_list.item(i).text() for i in range(g.members_list.count())}
    assert names == {"DS1", "DS2"}


def test_all_no_mask_has_nothing_selectable_and_gates_start(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], [])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], [])
    dialog._add_h5_paths([ds1, ds2])
    dialog._mask_selection_group.setChecked(True)
    # One no-mask group, nothing to pick, Start stays gated.
    assert len(dialog._mask_groups) == 1
    assert dialog._mask_groups[0].has_masks is False
    assert dialog.existing_mask_selections == {}
    assert not dialog._start_btn.isEnabled()


def test_selection_signal_fans_to_all_members_and_enables_start(dialog, tmp_path):
    ds1 = _make_h5(tmp_path, "DS1", ["GFP"], ["m1"])
    ds2 = _make_h5(tmp_path, "DS2", ["GFP"], ["m1"])
    dialog._add_h5_paths([ds1, ds2])
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    assert not dialog._start_btn.isEnabled()
    # Drive the item selection signal (not a direct property write) — this
    # proves the itemSelectionChanged wire fans out and re-gates Start.
    g.list_widget.item(0).setSelected(True)
    assert dialog.existing_mask_selections == {"DS1": ["m1"], "DS2": ["m1"]}
    assert dialog._start_btn.isEnabled()


# ── Breakout / per-group override (plan U4) ──────────────────────────────


def test_split_creates_independent_subgroup_and_fans_out(dialog, tmp_path):
    paths = [_make_h5(tmp_path, n, ["GFP"], ["m1", "m2"]) for n in ("DS1", "DS2", "DS3")]
    dialog._add_h5_paths(paths)
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    _select(g.list_widget, {"m1"})  # group selection = m1
    # Split DS3 into its own sub-group.
    _select(g.members_list, {"DS3"})
    dialog._split_selected(g.signature, g.members_list)

    gr = _group_for(dialog, "DS1")
    gs = _group_for(dialog, "DS3")
    assert gr is not gs
    assert {pd.display_name for pd in gr.members} == {"DS1", "DS2"}
    assert {pd.display_name for pd in gs.members} == {"DS3"}
    assert gs.split_key == frozenset({"DS3"})
    # Split seeded from the remainder selection (m1); one entry per dataset.
    assert dialog.existing_mask_selections == {
        "DS1": ["m1"],
        "DS2": ["m1"],
        "DS3": ["m1"],
    }
    # Give the split an independent selection (m2 only).
    for i in range(gs.list_widget.count()):
        it = gs.list_widget.item(i)
        it.setSelected(it.text() == "m2")
    assert dialog.existing_mask_selections == {
        "DS1": ["m1"],
        "DS2": ["m1"],
        "DS3": ["m2"],
    }


def test_merge_back_returns_members_to_remainder(dialog, tmp_path):
    paths = [_make_h5(tmp_path, n, ["GFP"], ["m1", "m2"]) for n in ("DS1", "DS2")]
    dialog._add_h5_paths(paths)
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    _select(g.members_list, {"DS2"})
    dialog._split_selected(g.signature, g.members_list)
    gs = _group_for(dialog, "DS2")
    assert gs.split_key is not None
    # Merge the split back.
    dialog._merge_split(gs.signature, gs.split_key)
    selectable = [x for x in dialog._mask_groups if x.has_masks]
    assert len(selectable) == 1
    assert {pd.display_name for pd in selectable[0].members} == {"DS1", "DS2"}
    assert selectable[0].split_key is None


def test_split_preserved_when_same_signature_dataset_added(dialog, tmp_path):
    p1 = _make_h5(tmp_path, "DS1", ["GFP"], ["m1", "m2"])
    p2 = _make_h5(tmp_path, "DS2", ["GFP"], ["m1", "m2"])
    p3 = _make_h5(tmp_path, "DS3", ["GFP"], ["m1", "m2"])
    dialog._add_h5_paths([p1, p2])
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    _select(g.list_widget, {"m1"})  # remainder selection
    _select(g.members_list, {"DS2"})
    dialog._split_selected(g.signature, g.members_list)
    gs = _group_for(dialog, "DS2")
    for i in range(gs.list_widget.count()):
        it = gs.list_widget.item(i)
        it.setSelected(it.text() == "m2")  # split selection

    # Adding a same-signature dataset rebuilds; it joins the remainder and
    # both sub-groups keep their selections.
    dialog._add_h5_paths([p3])
    assert {pd.display_name for pd in _group_for(dialog, "DS1").members} == {"DS1", "DS3"}
    assert {pd.display_name for pd in _group_for(dialog, "DS2").members} == {"DS2"}
    assert dialog.existing_mask_selections == {
        "DS1": ["m1"],
        "DS3": ["m1"],
        "DS2": ["m2"],
    }


def test_split_button_click_splits_group(dialog, tmp_path):
    paths = [_make_h5(tmp_path, n, ["GFP"], ["m1"]) for n in ("DS1", "DS2")]
    dialog._add_h5_paths(paths)
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    assert g.split_btn is not None
    # Expand to reveal the member list + split button, select DS2, click.
    g.toggle_btn.setChecked(True)
    _select(g.members_list, {"DS2"})
    g.split_btn.click()  # drive the wired button, not the handler directly
    assert {pd.display_name for pd in _group_for(dialog, "DS1").members} == {"DS1"}
    assert {pd.display_name for pd in _group_for(dialog, "DS2").members} == {"DS2"}


def _remove_dataset(dialog, name):
    """Remove the dataset named ``name`` via the real Remove-button path."""
    tree = dialog._dataset_tree
    for i in range(tree.topLevelItemCount()):
        if tree.topLevelItem(i).text(0) == name:
            tree.setCurrentItem(tree.topLevelItem(i))
            break
    dialog._on_remove_dataset()


def test_merge_button_click_merges_group(dialog, tmp_path):
    paths = [_make_h5(tmp_path, n, ["GFP"], ["m1"]) for n in ("DS1", "DS2")]
    dialog._add_h5_paths(paths)
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    _select(g.members_list, {"DS2"})
    dialog._split_selected(g.signature, g.members_list)
    gs = _group_for(dialog, "DS2")
    assert gs.merge_btn is not None
    gs.merge_btn.click()  # drive the wired merge button, not the handler directly
    selectable = [x for x in dialog._mask_groups if x.has_masks]
    assert len(selectable) == 1
    assert {pd.display_name for pd in selectable[0].members} == {"DS1", "DS2"}


def test_split_noop_when_nothing_selected(dialog, tmp_path):
    paths = [_make_h5(tmp_path, n, ["GFP"], ["m1"]) for n in ("DS1", "DS2")]
    dialog._add_h5_paths(paths)
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    dialog._split_selected(g.signature, g.members_list)  # nothing checked
    assert dialog._mask_breakouts == {}
    assert len([x for x in dialog._mask_groups if x.has_masks]) == 1


def test_split_rejected_when_all_members_selected(dialog, tmp_path):
    paths = [_make_h5(tmp_path, n, ["GFP"], ["m1"]) for n in ("DS1", "DS2")]
    dialog._add_h5_paths(paths)
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    _select(g.members_list, {"DS1", "DS2"})  # all -> would empty remainder
    dialog._split_selected(g.signature, g.members_list)
    assert dialog._mask_breakouts == {}
    assert len([x for x in dialog._mask_groups if x.has_masks]) == 1


def test_split_subgroup_cannot_be_split_further(dialog, tmp_path):
    paths = [_make_h5(tmp_path, n, ["GFP"], ["m1"]) for n in ("DS1", "DS2", "DS3", "DS4")]
    dialog._add_h5_paths(paths)
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    _select(g.members_list, {"DS3", "DS4"})
    dialog._split_selected(g.signature, g.members_list)  # split {DS3, DS4}
    gs = _group_for(dialog, "DS3")
    assert gs.split_key == frozenset({"DS3", "DS4"})
    assert len(gs.members) == 2
    # A breakout sub-group offers no further split.
    assert gs.split_btn is None


def test_two_simultaneous_splits_from_one_signature(dialog, tmp_path):
    paths = [_make_h5(tmp_path, n, ["GFP"], ["m1", "m2"]) for n in ("DS1", "DS2", "DS3", "DS4")]
    dialog._add_h5_paths(paths)
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    _select(g.members_list, {"DS4"})
    dialog._split_selected(g.signature, g.members_list)  # split {DS4}
    g2 = _group_for(dialog, "DS1")  # remainder {DS1, DS2, DS3}
    _select(g2.members_list, {"DS3"})
    dialog._split_selected(g2.signature, g2.members_list)  # split {DS3}

    selectable = [x for x in dialog._mask_groups if x.has_masks]
    assert len(selectable) == 3  # remainder {DS1,DS2} + split {DS3} + split {DS4}
    # Independent selections per sub-group all fan out per dataset.
    _select(_group_for(dialog, "DS1").list_widget, {"m1"})  # remainder
    _set_only(_group_for(dialog, "DS3").list_widget, "m2")
    _set_only(_group_for(dialog, "DS4").list_widget, "m1")
    assert dialog.existing_mask_selections == {
        "DS1": ["m1"],
        "DS2": ["m1"],
        "DS3": ["m2"],
        "DS4": ["m1"],
    }


def _set_only(list_widget, keep_text):
    """Select exactly the item whose text == keep_text; deselect the rest."""
    for i in range(list_widget.count()):
        it = list_widget.item(i)
        it.setSelected(it.text() == keep_text)


def test_breakout_dropped_when_split_member_removed(dialog, tmp_path):
    # A single-member split whose member is removed empties and is pruned.
    paths = [_make_h5(tmp_path, n, ["GFP"], ["m1"]) for n in ("DS1", "DS2", "DS3")]
    dialog._add_h5_paths(paths)
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    _select(g.members_list, {"DS3"})
    dialog._split_selected(g.signature, g.members_list)
    assert _group_for(dialog, "DS3").split_key == frozenset({"DS3"})
    _remove_dataset(dialog, "DS3")
    selectable = [x for x in dialog._mask_groups if x.has_masks]
    assert len(selectable) == 1
    assert selectable[0].split_key is None
    assert {pd.display_name for pd in selectable[0].members} == {"DS1", "DS2"}
    assert dialog._mask_breakouts == {}


def test_split_shrink_preserves_surviving_member_selection(dialog, tmp_path):
    # Removing ONE member of a multi-member split must not drop the survivors'
    # selection (split_key shrinks {DS2,DS3} -> {DS2}). Regression for the
    # split-key-shrink snapshot-miss bug.
    paths = [_make_h5(tmp_path, n, ["GFP"], ["m1", "m2"]) for n in ("DS1", "DS2", "DS3")]
    dialog._add_h5_paths(paths)
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    _select(g.list_widget, {"m1"})  # remainder selection
    _select(g.members_list, {"DS2", "DS3"})
    dialog._split_selected(g.signature, g.members_list)  # split {DS2, DS3}
    gs = _group_for(dialog, "DS2")
    assert gs.split_key == frozenset({"DS2", "DS3"})
    _set_only(gs.list_widget, "m2")  # split selection = m2
    assert dialog.existing_mask_selections == {
        "DS1": ["m1"],
        "DS2": ["m2"],
        "DS3": ["m2"],
    }
    # Remove DS3 -> split shrinks to {DS2}; DS2 must KEEP its m2 pick.
    _remove_dataset(dialog, "DS3")
    assert _group_for(dialog, "DS2").split_key == frozenset({"DS2"})
    assert dialog.existing_mask_selections == {"DS1": ["m1"], "DS2": ["m2"]}


def test_stale_breakout_not_resurrected_after_remove_all(dialog, tmp_path):
    # Remove ALL datasets of a split signature, then re-add the same files: the
    # stale split must not resurrect. Regression for the un-pruned _mask_breakouts.
    p1 = _make_h5(tmp_path, "DS1", ["GFP"], ["m1", "m2"])
    p2 = _make_h5(tmp_path, "DS2", ["GFP"], ["m1", "m2"])
    dialog._add_h5_paths([p1, p2])
    dialog._mask_selection_group.setChecked(True)
    g = _group_for(dialog, "DS1")
    _select(g.members_list, {"DS2"})
    dialog._split_selected(g.signature, g.members_list)
    assert _group_for(dialog, "DS2").split_key is not None
    _remove_dataset(dialog, "DS1")
    _remove_dataset(dialog, "DS2")
    assert dialog._mask_breakouts == {}  # pruned, not lingering
    # Re-add the same files -> one group, no resurrected split.
    dialog._add_h5_paths([p1, p2])
    selectable = [x for x in dialog._mask_groups if x.has_masks]
    assert len(selectable) == 1
    assert selectable[0].split_key is None
    assert {pd.display_name for pd in selectable[0].members} == {"DS1", "DS2"}
