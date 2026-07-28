"""Tests for StitchingForm + ImportDialog registration controls.

The canonical shared widget and the ImportDialog both BUILD a TileConfig
(hop 1). Both must carry overlap (as a fraction), register, and
reference_channel.

Retargeted from StitchingFlimForm when that transitional composite was retired.
"""

from __future__ import annotations


def test_form_tile_config_threads_registration_fields(qtbot) -> None:
    from percell4.gui._stitching_form import StitchingForm

    form = StitchingForm()
    qtbot.addWidget(form)

    form.grid_y.setValue(2)
    form.grid_x.setValue(2)
    form.overlap.setValue(20.0)  # percent
    form.register_check.setChecked(True)
    form.set_reference_channels(["ch00", "ch01"])
    form.reference.setCurrentText("ch01")

    tc = form.tile_config()
    assert tc.register is True
    assert tc.overlap == 0.2  # fraction
    assert tc.reference_channel == "ch01"


def test_form_defaults_keep_gate_closed(qtbot) -> None:
    from percell4.gui._stitching_form import StitchingForm

    form = StitchingForm()
    qtbot.addWidget(form)

    tc = form.tile_config()
    assert tc.register is False
    assert tc.overlap == 0.0
    assert tc.reference_channel is None


def test_form_reference_channels_carry_name_as_itemdata(qtbot) -> None:
    """set_reference_channels stores the name as itemData (not an index)."""
    from percell4.gui._stitching_form import StitchingForm

    form = StitchingForm()
    qtbot.addWidget(form)
    form.set_reference_channels(["ch00", "ch01", "ch02"])
    assert form.reference.itemData(0) == "ch00"
    assert form.reference.itemData(2) == "ch02"


def test_form_change_signal_fires_on_register_toggle(qtbot) -> None:
    from percell4.gui._stitching_form import StitchingForm

    form = StitchingForm()
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


def test_import_dialog_uses_the_canonical_form(qtbot) -> None:
    """The last surface to migrate. It is dead production code — nothing in
    src/ constructs it — but leaving it hand-rolled kept a fifth divergent copy
    of the stitching controls alive.
    """
    from percell4.gui._stitching_form import StitchingForm
    from percell4.gui.import_dialog import ImportDialog

    dlg = ImportDialog()
    qtbot.addWidget(dlg)
    assert isinstance(dlg._tile_widget, StitchingForm)


def test_import_dialog_reference_combo_survives_rediscovery(qtbot) -> None:
    """Scanning again must not drop the user's reference pick.

    This surface rebuilds the list from scanned channels, so the NAME is the
    stable identity — unlike CompressDialog, where a Manual-mode rename means
    the POSITION is what has to survive. Hence preserve="text" here.
    """
    from percell4.gui.import_dialog import ImportDialog

    dlg = ImportDialog()
    qtbot.addWidget(dlg)

    dlg._tile_widget.set_reference_channels(["ch00", "ch01"], preserve="text")
    dlg._tile_reference.setCurrentText("ch01")
    dlg._tile_widget.set_reference_channels(["ch00", "ch01", "ch02"], preserve="text")

    assert dlg._tile_reference.currentText() == "ch01"
