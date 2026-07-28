"""AddLayerDialog's two tabs on the canonical StitchingForm (U5).

Closes the follow-up filed in PR #9 review (todos/037). Both tabs previously
built their own copies of the stitching widgets inside this one file.

The delicate part is the TCSPC tab's ``_tcspc_stitching_user_edited`` flag: it
gates whether re-clicking Scan & Match re-seeds stitching from the dataset's
compress config. The migration replaces four individual signal wires with one
``StitchingForm.changed`` wire, and the new Type->Order repopulation is an extra
emission source on it — so both directions are pinned here.
"""

from __future__ import annotations

import numpy as np

from percell4.store import DatasetStore


def _store(path):
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["ch00", "ch01"]})
    store.write_array(
        "intensity",
        np.zeros((2, 16, 16), dtype=np.float32),
        attrs={"dims": ["C", "H", "W"]},
    )
    return store


def _dialog(qtbot, tmp_path):
    from percell4.gui.add_layer_dialog import AddLayerDialog

    dlg = AddLayerDialog(
        parent=None, store=_store(tmp_path / "ds.h5"), data_model=None, viewer_win=None
    )
    qtbot.addWidget(dlg)
    return dlg


# ── Batch tab ───────────────────────────────────────────────────────


def test_batch_tab_builds_the_expected_tile_config(qtbot, tmp_path) -> None:
    dlg = _dialog(qtbot, tmp_path)
    dlg._batch_stitch_check.setChecked(True)
    dlg._batch_stitch_rows.setValue(2)
    dlg._batch_stitch_cols.setValue(5)
    dlg._batch_stitch_type.setCurrentIndex(
        dlg._batch_stitch_type.findData("snake_by_column")
    )
    dlg._batch_stitch_order.setCurrentIndex(
        dlg._batch_stitch_order.findData("bottom_left")
    )

    tc = dlg._batch_stitch_widget.tile_config()
    assert tc.grid_rows == 2, "Grid size Y is the row count"
    assert tc.grid_cols == 5, "Grid size X is the column count"
    assert tc.grid_type == "snake_by_column"
    assert tc.order == "bottom_left"


def test_batch_tab_has_no_registration_controls(qtbot, tmp_path) -> None:
    """Registration is a compress-time concern; append surfaces reuse the
    persisted geometry instead (origin R13)."""
    dlg = _dialog(qtbot, tmp_path)
    form = dlg._batch_stitch_widget
    assert form.register_check.isVisible() is False
    assert form.overlap.isVisible() is False
    assert form.fusion.isVisible() is False

    tc = form.tile_config()
    assert tc.register is False
    assert tc.overlap == 0.0
    assert tc.reference_channel is None
    assert tc.fusion_method == "none"


def test_batch_tab_reuse_affordance_exists(qtbot, tmp_path) -> None:
    dlg = _dialog(qtbot, tmp_path)
    assert dlg._batch_reuse_label is not None


# ── TCSPC tab ───────────────────────────────────────────────────────


def test_tcspc_unchecked_yields_a_1x1_config_not_none(qtbot, tmp_path) -> None:
    """This surface deliberately differs from Compress and Import, which pass
    None. Unifying them would be a silent behavior change on the decay path."""
    dlg = _dialog(qtbot, tmp_path)
    dlg._tcspc_stitch_check.setChecked(False)

    # Mirror the branch in _on_tcspc_accept rather than driving the whole
    # accept flow, which needs a scan result.
    from percell4.domain.io.models import TileConfig

    tile_config = (
        dlg._tcspc_stitch_widget.tile_config()
        if dlg._tcspc_stitch_check.isChecked()
        else TileConfig(grid_rows=1, grid_cols=1)
    )
    assert tile_config is not None
    assert (tile_config.grid_rows, tile_config.grid_cols) == (1, 1)


def test_user_driven_type_change_marks_the_form_edited(qtbot, tmp_path) -> None:
    """The flag must still fire through the single StitchingForm.changed wire
    that replaced four individual connections."""
    dlg = _dialog(qtbot, tmp_path)
    assert dlg._tcspc_stitching_user_edited is False

    dlg._tcspc_stitch_type.setCurrentIndex(
        dlg._tcspc_stitch_type.findData("column_by_column")
    )
    assert dlg._tcspc_stitching_user_edited is True


def test_user_driven_grid_edit_marks_the_form_edited(qtbot, tmp_path) -> None:
    dlg = _dialog(qtbot, tmp_path)
    dlg._tcspc_stitch_rows.setValue(3)
    assert dlg._tcspc_stitching_user_edited is True


def test_seeding_a_different_type_does_not_mark_the_form_edited(
    qtbot, tmp_path
) -> None:
    """The regression this unit most risks.

    Seeding sets the Type programmatically, which now triggers the Order combo
    to repopulate and emit ``changed`` — an emission source that did not exist
    when the flag was wired to four separate signals. If it leaked through, the
    very first Scan & Match would mark the form user-edited and every later
    re-Scan would silently refuse to re-seed.
    """
    from percell4.gui.add_layer_dialog import AddLayerDialog

    s = DatasetStore(tmp_path / "seed.h5")
    s.create(
        metadata={
            "channel_names": ["ch00"],
            "stitch_grid_rows": 3,
            "stitch_grid_cols": 4,
            # Deliberately NOT the default type, so seeding really does change
            # it and really does trigger the repopulation.
            "stitch_grid_type": "snake_by_column",
            "stitch_order": "bottom_right",
        }
    )
    s.write_array(
        "intensity", np.zeros((1, 8, 8), dtype=np.float32), attrs={"dims": ["C", "H", "W"]}
    )
    dlg = AddLayerDialog(parent=None, store=s, data_model=None, viewer_win=None)
    qtbot.addWidget(dlg)

    dlg._tcspc_seed_stitching_from_metadata()

    assert dlg._tcspc_stitch_type.currentData() == "snake_by_column"
    assert dlg._tcspc_stitch_order.currentData() == "bottom_right"
    assert dlg._tcspc_stitching_user_edited is False, (
        "programmatic seeding leaked through the Type->Order repopulation and "
        "marked the form user-edited"
    )


def test_reseeding_is_suppressed_after_a_user_edit(qtbot, tmp_path) -> None:
    from percell4.gui.add_layer_dialog import AddLayerDialog

    s = DatasetStore(tmp_path / "sup.h5")
    s.create(
        metadata={
            "channel_names": ["ch00"],
            "stitch_grid_rows": 3,
            "stitch_grid_cols": 4,
            "stitch_grid_type": "row_by_row",
            "stitch_order": "top_left",
        }
    )
    s.write_array(
        "intensity", np.zeros((1, 8, 8), dtype=np.float32), attrs={"dims": ["C", "H", "W"]}
    )
    dlg = AddLayerDialog(parent=None, store=s, data_model=None, viewer_win=None)
    qtbot.addWidget(dlg)

    # A real user edit through the signal path.
    dlg._tcspc_stitch_cols.setValue(9)
    assert dlg._tcspc_stitching_user_edited is True

    dlg._tcspc_seed_stitching_from_metadata()

    assert dlg._tcspc_stitch_cols.value() == 9


def test_tcspc_tab_has_no_registration_controls(qtbot, tmp_path) -> None:
    dlg = _dialog(qtbot, tmp_path)
    form = dlg._tcspc_stitch_widget
    assert form.register_check.isVisible() is False
    assert form.overlap.isVisible() is False
    assert dlg._tcspc_reuse_label is not None


# ── Consolidation ───────────────────────────────────────────────────


def test_both_tabs_use_the_canonical_form(qtbot, tmp_path) -> None:
    """The point of the unit: no tab builds its own stitching widgets."""
    from percell4.gui._stitching_form import StitchingForm

    dlg = _dialog(qtbot, tmp_path)
    assert isinstance(dlg._batch_stitch_widget, StitchingForm)
    assert isinstance(dlg._tcspc_stitch_widget, StitchingForm)


def test_both_tabs_show_fiji_labels(qtbot, tmp_path) -> None:
    dlg = _dialog(qtbot, tmp_path)
    for form in (dlg._batch_stitch_widget, dlg._tcspc_stitch_widget):
        assert form.grid_type.itemText(0) == "Grid: row-by-row"
        assert form.order.itemText(0) == "Right & Down"
        form.grid_type.setCurrentIndex(form.grid_type.findData("column_by_column"))
        assert form.order.itemText(0) == "Down & Right"
