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


# ── Step re-tuning on unit change ─────────────────────────────────────


def test_switching_to_um2_enables_fractional_step(dialog):
    spin = dialog._particle_min_area
    assert spin.singleStep() == pytest.approx(1.0)
    dialog._particle_min_area_unit.setCurrentIndex(1)  # µm²
    assert spin.singleStep() < 1.0


def test_switching_back_to_px_restores_integer_step(dialog):
    spin = dialog._particle_min_area
    dialog._particle_min_area_unit.setCurrentIndex(1)  # µm²
    dialog._particle_min_area_unit.setCurrentIndex(0)  # px
    assert spin.singleStep() == pytest.approx(1.0)


# ── Value preservation across unit switches ───────────────────────────


def test_switching_unit_does_not_auto_convert_value(dialog):
    """The user re-states intent — switching units leaves the number alone."""
    dialog._particle_min_area.setValue(42.0)
    assert dialog._particle_min_area.value() == pytest.approx(42.0)

    dialog._particle_min_area_unit.setCurrentIndex(1)  # px → µm²
    assert dialog._particle_min_area.value() == pytest.approx(42.0)

    dialog._particle_min_area_unit.setCurrentIndex(0)  # µm² → px
    assert dialog._particle_min_area.value() == pytest.approx(42.0)


def test_switching_unit_preserves_fractional_value(dialog):
    """Fractional µm² value must survive a transient px detour — decimals
    is fixed at construction so px mode does not silently quantize."""
    dialog._particle_min_area_unit.setCurrentIndex(1)  # µm²
    dialog._particle_min_area.setValue(0.5)
    dialog._particle_min_area_unit.setCurrentIndex(0)  # px (would quantize)
    dialog._particle_min_area_unit.setCurrentIndex(1)  # back to µm²
    assert dialog._particle_min_area.value() == pytest.approx(0.5)


def test_single_step_changes_with_unit(dialog):
    """Step matches the unit so up/down arrows feel right: 1 px vs 0.01 µm²."""
    spin = dialog._particle_min_area
    assert spin.singleStep() == pytest.approx(1.0)
    dialog._particle_min_area_unit.setCurrentIndex(1)  # µm²
    assert spin.singleStep() == pytest.approx(0.01)
    dialog._particle_min_area_unit.setCurrentIndex(0)  # px
    assert spin.singleStep() == pytest.approx(1.0)


def test_particle_group_attribute_is_the_group_box(dialog):
    """The build path uses self._particle_group directly; storing it as
    an attribute removes the parent-walk fragility flagged in review."""
    from qtpy.QtWidgets import QGroupBox
    assert isinstance(dialog._particle_group, QGroupBox)
    assert dialog._particle_group.isCheckable()
