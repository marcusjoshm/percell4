"""Tests for StitchingFlimForm + ImportDialog registration controls (U8).

The canonical shared widget (StitchingFlimForm) and the ImportDialog both
BUILD a TileConfig (hop 1). Both must carry overlap (as a fraction),
register, and reference_channel.
"""

from __future__ import annotations


def test_form_tile_config_threads_registration_fields(qtbot) -> None:
    from percell4.gui._stitching_flim_form import StitchingFlimForm

    form = StitchingFlimForm()
    qtbot.addWidget(form)

    form.stitch_rows.setValue(2)
    form.stitch_cols.setValue(2)
    form.overlap_spin.setValue(20.0)  # percent
    form.register_check.setChecked(True)
    form.set_reference_channels(["ch00", "ch01"])
    form.reference_combo.setCurrentText("ch01")

    tc = form.tile_config()
    assert tc.register is True
    assert tc.overlap == 0.2  # fraction
    assert tc.reference_channel == "ch01"


def test_form_defaults_keep_gate_closed(qtbot) -> None:
    from percell4.gui._stitching_flim_form import StitchingFlimForm

    form = StitchingFlimForm()
    qtbot.addWidget(form)

    tc = form.tile_config()
    assert tc.register is False
    assert tc.overlap == 0.0
    assert tc.reference_channel is None


def test_form_reference_channels_carry_name_as_itemdata(qtbot) -> None:
    """set_reference_channels stores the name as itemData (not an index)."""
    from percell4.gui._stitching_flim_form import StitchingFlimForm

    form = StitchingFlimForm()
    qtbot.addWidget(form)
    form.set_reference_channels(["ch00", "ch01", "ch02"])
    assert form.reference_combo.itemData(0) == "ch00"
    assert form.reference_combo.itemData(2) == "ch02"


def test_form_change_signal_fires_on_register_toggle(qtbot) -> None:
    from percell4.gui._stitching_flim_form import StitchingFlimForm

    form = StitchingFlimForm()
    qtbot.addWidget(form)
    with qtbot.waitSignal(form.changed, timeout=1000):
        form.register_check.setChecked(True)


def test_import_dialog_threads_registration_fields(qtbot) -> None:
    from percell4.gui.import_dialog import ImportDialog

    dlg = ImportDialog()
    qtbot.addWidget(dlg)

    dlg._tile_enabled.setChecked(True)
    dlg._tile_rows.setValue(2)
    dlg._tile_cols.setValue(2)
    dlg._tile_overlap.setValue(10.0)  # percent
    dlg._tile_register.setChecked(True)
    dlg._tile_reference.setCurrentText("ch00")

    tc = dlg.tile_config
    assert tc is not None
    assert tc.register is True
    assert tc.overlap == 0.1  # fraction
    assert tc.reference_channel == "ch00"


def test_import_dialog_defaults_keep_gate_closed(qtbot) -> None:
    from percell4.gui.import_dialog import ImportDialog

    dlg = ImportDialog()
    qtbot.addWidget(dlg)
    dlg._tile_enabled.setChecked(True)

    tc = dlg.tile_config
    assert tc is not None
    assert tc.register is False
    assert tc.overlap == 0.0
    assert tc.reference_channel is None
