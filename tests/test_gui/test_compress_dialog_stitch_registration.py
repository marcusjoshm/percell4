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
