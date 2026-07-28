"""CompressDialog on the canonical StitchingForm (U4).

The migration must be invisible downstream: for any given user selection the
emitted TileConfig has to match what the old inline widget set produced. These
tests pin that, plus the two host-owned behaviours that reach into the form's
widgets from outside it (discovery auto-enable, Manual-mode rename).
"""

from __future__ import annotations

from qtpy.QtWidgets import QScrollArea

from percell4.gui.compress_dialog import CompressDialog


def _dialog(qtbot):
    dlg = CompressDialog()
    qtbot.addWidget(dlg)
    return dlg


def _tile_config(dlg):
    return dlg.compress_config.tile_config


# ── TileConfig parity ───────────────────────────────────────────────


def test_full_selection_round_trips_to_tile_config(qtbot) -> None:
    dlg = _dialog(qtbot)
    dlg._stitch_check.setChecked(True)
    dlg._stitch_rows.setValue(2)
    dlg._stitch_cols.setValue(3)
    dlg._stitch_type.setCurrentIndex(dlg._stitch_type.findData("snake_by_column"))
    dlg._stitch_order.setCurrentIndex(dlg._stitch_order.findData("bottom_right"))
    dlg._stitch_overlap.setValue(15.0)
    dlg._stitch_register.setChecked(True)
    dlg._stitch_reference.setCurrentText("ch00")
    dlg._stitch_fusion.setCurrentIndex(dlg._stitch_fusion.findData("linear_blending"))

    tc = _tile_config(dlg)
    assert tc is not None
    assert tc.grid_rows == 2
    assert tc.grid_cols == 3
    assert tc.grid_type == "snake_by_column"
    assert tc.order == "bottom_right"
    assert tc.overlap == 0.15  # UI shows percent, TileConfig stores a fraction
    assert tc.register is True
    assert tc.reference_channel == "ch00"
    assert tc.fusion_method == "linear_blending"


def test_grid_x_is_cols_and_y_is_rows_through_the_dialog(qtbot) -> None:
    """The aliases map _stitch_rows -> Grid size Y and _stitch_cols -> Grid
    size X. A swap here transposes every mosaic this dialog produces."""
    dlg = _dialog(qtbot)
    dlg._stitch_check.setChecked(True)
    dlg._stitch_rows.setValue(2)
    dlg._stitch_cols.setValue(5)

    assert dlg._stitch_widget.grid_y.value() == 2
    assert dlg._stitch_widget.grid_x.value() == 5

    tc = _tile_config(dlg)
    assert tc.grid_rows == 2
    assert tc.grid_cols == 5


def test_unchecked_yields_no_tile_config(qtbot) -> None:
    """Compress passes None when stitching is off — distinct from the TCSPC
    tab, which passes a 1x1 config. That divergence is deliberate."""
    dlg = _dialog(qtbot)
    dlg._stitch_check.setChecked(False)
    assert _tile_config(dlg) is None


def test_register_unchecked_keeps_the_byte_identical_gate_closed(qtbot) -> None:
    """register ∧ overlap>0 ∧ grid>1x1 is what opens the phase-correlation
    path; with Register off the config must not satisfy it."""
    dlg = _dialog(qtbot)
    dlg._stitch_check.setChecked(True)
    dlg._stitch_rows.setValue(2)
    dlg._stitch_cols.setValue(2)

    tc = _tile_config(dlg)
    assert tc.register is False
    assert tc.overlap == 0.0


# ── Host-owned behaviours reaching into the form ────────────────────


def test_discovery_auto_enables_stitching(qtbot) -> None:
    """_populate_lists ticks the checkbox when tiles are detected; that path
    lives outside the block this unit replaced, so it needs a guard."""
    dlg = _dialog(qtbot)
    assert dlg._stitch_check.isChecked() is False

    dlg._all_tiles = ["s00", "s01", "s02", "s03"]
    dlg._populate_lists()

    assert dlg._stitch_check.isChecked() is True
    assert dlg._stitch_widget.isVisible() is False or dlg.isVisible() is False


def test_manual_rename_keeps_the_reference_on_the_same_channel(qtbot) -> None:
    """Renaming ch00 to ER must leave the reference pointing at that channel,
    not silently fall back to the first entry — the importer keys registration
    tiles by the renamed layer name."""
    dlg = _dialog(qtbot)
    dlg._all_channels = ["ch00", "ch01"]
    dlg._manual_radio.setChecked(True)
    dlg._build_manual_channel_panel()
    dlg._refresh_reference_combo()

    dlg._stitch_reference.setCurrentIndex(1)
    assert dlg._stitch_reference.currentText() == "ch01"

    dlg._channel_configs["ch01"].name_edit.setText("ER")

    assert dlg._stitch_reference.currentIndex() == 1
    assert dlg._stitch_reference.currentText() == "ER"


# ── Width (R1) ──────────────────────────────────────────────────────


def test_no_horizontal_scrollbar_at_minimum_width(qtbot) -> None:
    """The defect that started this refactor: the stitching row overflowed and
    the dialog grew a horizontal scrollbar, so Register / Reference / Fusion
    could only be reached by scrolling sideways."""
    dlg = _dialog(qtbot)
    dlg._stitch_check.setChecked(True)
    dlg.resize(dlg.minimumWidth(), 700)
    dlg.show()
    qtbot.waitExposed(dlg)

    for area in dlg.findChildren(QScrollArea):
        inner = area.widget()
        if inner is None:
            continue
        assert inner.sizeHint().width() <= area.viewport().width(), (
            f"content wants {inner.sizeHint().width()}px in a "
            f"{area.viewport().width()}px viewport — horizontal scrolling is back"
        )


def test_minimum_width_fits_a_standard_window(qtbot) -> None:
    dlg = _dialog(qtbot)
    assert dlg.minimumWidth() <= 700
