"""Tests for the CompressDialog overlap-aware registration controls (U8).

Covers hop 1 (BUILD): the Overlap%/Register/Reference-channel controls in
the _stitch_widget must thread into the materialized CompressConfig's
TileConfig with overlap as a FRACTION, register, and reference_channel.
Also asserts the gate stays closed when Register is unchecked.
"""

from __future__ import annotations


def test_register_controls_thread_into_tile_config(qtbot) -> None:
    """Setting overlap/register/reference produces a TileConfig carrying them."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)

    dlg._stitch_check.setChecked(True)
    dlg._stitch_rows.setValue(2)
    dlg._stitch_cols.setValue(2)
    dlg._stitch_overlap.setValue(15.0)  # percent in the UI
    dlg._stitch_register.setChecked(True)
    dlg._stitch_reference.setCurrentText("ch00")

    cfg = dlg.compress_config
    tc = cfg.tile_config
    assert tc is not None
    assert tc.register is True
    # UI shows a percentage; TileConfig stores a fraction.
    assert tc.overlap == 0.15
    assert tc.reference_channel == "ch00"


def test_register_unchecked_keeps_gate_closed(qtbot) -> None:
    """Register unchecked → register=False, overlap=0.0 (grid path)."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)

    dlg._stitch_check.setChecked(True)
    dlg._stitch_rows.setValue(2)
    dlg._stitch_cols.setValue(2)
    # Register left unchecked; overlap left at default 0.

    cfg = dlg.compress_config
    tc = cfg.tile_config
    assert tc is not None
    assert tc.register is False
    assert tc.overlap == 0.0
    assert tc.reference_channel is None


def test_overlap_default_is_zero_fraction(qtbot) -> None:
    """The overlap spinbox defaults to 0% → 0.0 fraction."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)
    assert dlg._stitch_overlap.value() == 0.0


def test_reference_combo_is_editable(qtbot) -> None:
    """The reference-channel combo accepts free text (editable)."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)
    assert dlg._stitch_reference.isEditable()


def test_rename_channel_updates_reference_combo(qtbot) -> None:
    """Renaming a channel in Manual mode makes the new name selectable as the
    registration reference and drops the stale chXX id.

    Regression for the import failure ``reference_channel 'ch00' not found
    among the imported channels ['ER', 'G3BP1']; cannot register.`` — the
    importer keys registration tiles by the (renamed) layer name, so the
    reference combo must offer the renamed name, not the original chXX id.
    """
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)

    # Simulate discovery of two channels, then build the lists/panels.
    dlg._all_channels = ["00", "01"]
    dlg._populate_lists()

    # Rename ch00 -> "ER" the way the user does in the Manual-mode panel.
    dlg._channel_configs["00"].name_edit.setText("ER")

    items = [
        dlg._stitch_reference.itemText(i)
        for i in range(dlg._stitch_reference.count())
    ]
    assert "ER" in items  # renamed name is now offered
    assert "ch00" not in items  # stale id no longer offered
    assert "ch01" in items  # untouched channel keeps its default name


def test_renamed_reference_round_trips_into_tile_config(qtbot) -> None:
    """A renamed channel chosen as the reference round-trips into the
    TileConfig as the renamed name, matching an imported layer name so the
    importer's reference lookup succeeds."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)

    dlg._all_channels = ["00", "01"]
    dlg._populate_lists()
    dlg._channel_configs["00"].name_edit.setText("ER")

    # Manual mode → layer_assignments carry the renamed names.
    dlg._manual_radio.setChecked(True)
    dlg._stitch_check.setChecked(True)
    dlg._stitch_register.setChecked(True)
    dlg._stitch_reference.setCurrentText("ER")

    cfg = dlg.compress_config
    tc = cfg.tile_config
    assert tc is not None
    assert tc.reference_channel == "ER"
    # The reference resolves to an actual imported channel layer name, which is
    # exactly the key the importer uses for its registration tiles.
    assert cfg.layer_assignments["00"].name == "ER"
    assert tc.reference_channel in {
        a.name for a in cfg.layer_assignments.values()
    }
