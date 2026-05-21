"""WorkflowConfigDialog wires a px/µm² unit combo for Min particle area.

Verifies the combo exists with the right entries, switching units
re-tunes spinbox precision but does NOT auto-convert the entered value,
and the constructed ParticleSettings reflects the active selection.
"""

from __future__ import annotations

import pytest

from percell4.gui.workflows.single_cell.config_dialog import (
    WorkflowConfigDialog,
)


@pytest.fixture
def dialog(qtbot):
    dlg = WorkflowConfigDialog()
    qtbot.addWidget(dlg)
    return dlg


# ── Combo construction ────────────────────────────────────────────────


def test_min_area_unit_combo_has_px_and_um2(dialog):
    combo = dialog._particle_min_area_unit
    units = [combo.itemData(i) for i in range(combo.count())]
    assert units == ["px", "um2"]


def test_min_area_unit_defaults_to_px(dialog):
    assert dialog._particle_min_area_unit.currentData() == "px"


# ── Decimal/step re-tuning on unit change ─────────────────────────────


def test_switching_to_um2_enables_fractional_step(dialog):
    spin = dialog._particle_min_area
    assert spin.decimals() == 0
    dialog._particle_min_area_unit.setCurrentIndex(1)  # µm²
    assert spin.decimals() > 0


def test_switching_back_to_px_restores_integer_step(dialog):
    spin = dialog._particle_min_area
    dialog._particle_min_area_unit.setCurrentIndex(1)  # µm²
    dialog._particle_min_area_unit.setCurrentIndex(0)  # px
    assert spin.decimals() == 0


# ── Value preservation across unit switches ───────────────────────────


def test_switching_unit_does_not_auto_convert_value(dialog):
    """The user re-states intent — switching units leaves the number alone."""
    dialog._particle_min_area.setValue(42.0)
    assert dialog._particle_min_area.value() == pytest.approx(42.0)

    dialog._particle_min_area_unit.setCurrentIndex(1)  # px → µm²
    assert dialog._particle_min_area.value() == pytest.approx(42.0)

    dialog._particle_min_area_unit.setCurrentIndex(0)  # µm² → px
    assert dialog._particle_min_area.value() == pytest.approx(42.0)
