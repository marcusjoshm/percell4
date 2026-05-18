"""Tests for the CompressDialog creation_bin spinner (U8).

Asserts the SpinBox default, range, and that its value flows into the
materialized CompressConfig.
"""

from __future__ import annotations

from pathlib import Path


def test_creation_bin_default_is_one(qtbot) -> None:
    """The spinner starts at k=1 (no binning) by default."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)
    assert dlg._creation_bin_spin.value() == 1


def test_creation_bin_range_is_one_to_sixteen(qtbot) -> None:
    """Range matches the Session active_bin and the (removed) TCSPC spinner."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)
    assert dlg._creation_bin_spin.minimum() == 1
    assert dlg._creation_bin_spin.maximum() == 16


def test_creation_bin_default_flows_into_config(qtbot) -> None:
    """compress_config.creation_bin == 1 when the spinner is untouched."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)
    cfg = dlg.compress_config
    assert cfg.creation_bin == 1


def test_creation_bin_value_flows_into_config(qtbot) -> None:
    """Changing the spinner to k=3 surfaces in CompressConfig.creation_bin."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)
    dlg._creation_bin_spin.setValue(3)
    cfg = dlg.compress_config
    assert cfg.creation_bin == 3


def test_creation_bin_value_changed_signal_wired(qtbot) -> None:
    """valueChanged is connected at construction so test/UI drivers can
    rely on the user-edit signal path (per the qt-wire-user-edit-signals
    convention)."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)

    # Receivers count is >= 1 (the controller's _on_creation_bin_changed).
    receivers = dlg._creation_bin_spin.receivers(
        dlg._creation_bin_spin.valueChanged
    )
    assert receivers >= 1
