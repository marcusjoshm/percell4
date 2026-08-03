"""Tests for the shared CellposeSettingsForm widget (U1).

Asserts the form renders the eight CellposeSettings controls, seeds them
from an ``initial`` value, and round-trips a ``CellposeSettings`` through
``settings()``.
"""

from __future__ import annotations

from qtpy.QtWidgets import QDoubleSpinBox

from percell4.gui._cellpose_settings_form import (
    CELLPOSE_MODELS,
    CellposeSettingsForm,
)
from percell4.workflows.models import CellposeSettings


def test_defaults_round_trip(qtbot) -> None:
    """Default construction returns the CellposeSettings defaults."""
    form = CellposeSettingsForm()
    qtbot.addWidget(form)
    s = form.settings()
    assert s == CellposeSettings()
    assert s.model == "cpsam_v2"
    assert s.flow_threshold == 0.4
    assert s.cellprob_threshold == 0.0
    assert s.min_size == 15
    assert s.saturation_pct == 1.0
    assert s.blur_sigma == 0.0


def test_seeds_from_initial_and_round_trips(qtbot) -> None:
    """Widgets reflect ``initial``; ``settings()`` reads them back."""
    initial = CellposeSettings(
        diameter=300.0, flow_threshold=0.7, min_size=40, blur_sigma=1.5
    )
    form = CellposeSettingsForm(initial=initial)
    qtbot.addWidget(form)
    assert form._diameter.value() == 300.0
    assert form._flow.value() == 0.7
    assert form._min_size.value() == 40
    assert form._blur_sigma.value() == 1.5
    assert form.settings() == initial


def test_model_combo_items_match_constant(qtbot) -> None:
    """Model combo lists CELLPOSE_MODELS in order; default selects initial."""
    form = CellposeSettingsForm(initial=CellposeSettings(model="cpdino"))
    qtbot.addWidget(form)
    items = [form._model.itemText(i) for i in range(form._model.count())]
    assert tuple(items) == CELLPOSE_MODELS
    assert form._model.currentText() == "cpdino"


def test_diameter_is_double_spinbox(qtbot) -> None:
    """Diameter standardizes on QDoubleSpinBox (0 = auto)."""
    form = CellposeSettingsForm()
    qtbot.addWidget(form)
    assert isinstance(form._diameter, QDoubleSpinBox)
    assert form._diameter.minimum() == 0.0
    assert form._diameter.maximum() == 1000.0


def test_saturation_and_sigma_suffixes_and_precision(qtbot) -> None:
    """Saturation shows ' %'; Sigma has no unit (it's a standard deviation,
    not a pixel radius). Both 1-decimal."""
    form = CellposeSettingsForm()
    qtbot.addWidget(form)
    assert form._saturation.suffix() == " %"
    assert form._saturation.decimals() == 1
    assert form._blur_sigma.suffix() == ""
    assert form._blur_sigma.decimals() == 1
    assert form._blur_sigma.minimum() == 0.0
    assert form._blur_sigma.maximum() == 20.0


def test_diameter_changed_emits_on_edit(qtbot) -> None:
    """U1/R3: every Diameter (px) edit reaches a subscriber."""
    form = CellposeSettingsForm()
    qtbot.addWidget(form)
    seen: list[float] = []
    form.diameter_changed.connect(seen.append)

    form._diameter.setValue(120.0)

    assert seen == [120.0]


def test_diameter_changed_deduplicates_repeat_values(qtbot) -> None:
    """Setting the same value twice emits once — Qt suppresses no-op sets.

    The overlay consumer relies on this to avoid rebuilding its napari layer
    on every keystroke that lands on the value already shown.
    """
    form = CellposeSettingsForm()
    qtbot.addWidget(form)
    seen: list[float] = []
    form.diameter_changed.connect(seen.append)

    form._diameter.setValue(120.0)
    form._diameter.setValue(120.0)

    assert seen == [120.0]


def test_diameter_changed_emits_zero(qtbot) -> None:
    """0.0 (auto-detect) is emitted like any other value.

    The form does not interpret it; deciding that 0 means 'draw nothing' is
    the consumer's call.
    """
    form = CellposeSettingsForm(initial=CellposeSettings(diameter=300.0))
    qtbot.addWidget(form)
    seen: list[float] = []
    form.diameter_changed.connect(seen.append)

    form._diameter.setValue(0.0)

    assert seen == [0.0]


def test_diameter_changed_does_not_replace_pull_style_read(qtbot) -> None:
    """The signal is additive: settings() still reflects the spinbox."""
    form = CellposeSettingsForm()
    qtbot.addWidget(form)
    emitted: list[float] = []
    form.diameter_changed.connect(emitted.append)

    form._diameter.setValue(75.0)

    assert emitted == [75.0]
    assert form.settings().diameter == 75.0


def test_zero_boundaries_accepted(qtbot) -> None:
    """saturation_pct == 0 and blur_sigma == 0 are accepted boundaries."""
    form = CellposeSettingsForm(
        initial=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0)
    )
    qtbot.addWidget(form)
    s = form.settings()
    assert s.saturation_pct == 0.0
    assert s.blur_sigma == 0.0
