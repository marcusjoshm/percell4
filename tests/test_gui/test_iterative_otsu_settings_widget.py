"""Tests for IterativeOtsuSettingsWidget (U5)."""

from __future__ import annotations

from percell4.gui._iterative_otsu_settings import (
    IterativeOtsuConfig,
    IterativeOtsuSettingsWidget,
)


def _widget(qtbot) -> IterativeOtsuSettingsWidget:
    w = IterativeOtsuSettingsWidget()
    qtbot.addWidget(w)
    return w


def test_default_config(qtbot):
    w = _widget(qtbot)
    cfg = w.current_config()
    assert cfg == IterativeOtsuConfig(
        scope="per-cell",
        dilation_radius_px=5,
        max_rounds=10,
        gaussian_sigma=1.0,
        stop_criteria=("bg-floor", "positive-fraction-high"),
        stop_params=(("bg-floor.k", 2.0), ("positive-fraction-high.max_frac", 0.5)),
        stop_combine="any",
    )


def test_default_config_constructs_real_settings(qtbot):
    # The widget snapshot must be directly accepted by IterativeOtsuSettings.
    from percell4.workflows.models import IterativeOtsuSettings

    cfg = _widget(qtbot).current_config()
    s = IterativeOtsuSettings(
        scope=cfg.scope,
        dilation_radius_px=cfg.dilation_radius_px,
        max_rounds=cfg.max_rounds,
        stop_criteria=cfg.stop_criteria,
        stop_params=cfg.stop_params,
        stop_combine=cfg.stop_combine,
    )
    assert s.scope == "per-cell"


def test_toggling_a_criterion_adds_dotted_param(qtbot):
    w = _widget(qtbot)
    cb, spin, _ = w._rows["peak-prominence"]
    cb.setChecked(True)
    spin.setValue(3.5)
    cfg = w.current_config()
    assert "peak-prominence" in cfg.stop_criteria
    assert ("peak-prominence.k", 3.5) in cfg.stop_params


def test_unchecking_all_yields_empty_criteria(qtbot):
    w = _widget(qtbot)
    for cb, _, _ in w._rows.values():
        cb.setChecked(False)
    cfg = w.current_config()
    assert cfg.stop_criteria == ()
    assert cfg.stop_params == ()


def test_scope_and_globals_captured(qtbot):
    w = _widget(qtbot)
    w._scope.setCurrentIndex(1)  # Whole field
    w._dilation.setValue(8)
    w._max_rounds.setValue(20)
    w._sigma.setValue(0.0)
    w._combine.setCurrentText("all")
    cfg = w.current_config()
    assert cfg.scope == "whole-field"
    assert cfg.dilation_radius_px == 8
    assert cfg.max_rounds == 20
    assert cfg.gaussian_sigma == 0.0
    assert cfg.stop_combine == "all"


def test_param_spin_gated_by_checkbox(qtbot):
    w = _widget(qtbot)
    cb, spin, _ = w._rows["separability"]
    assert not cb.isChecked()
    assert not spin.isEnabled()
    cb.setChecked(True)
    assert spin.isEnabled()


def test_fixed_mode_defaults_off(qtbot):
    w = _widget(qtbot)
    assert not w._fixed_mode.isChecked()
    assert w.current_config().fixed_iterations is None
    assert w._mr_label.text() == "Max iterations:"
    assert w._crit_group.isEnabled()


def test_fixed_mode_blocks_criteria_group_and_sets_count(qtbot):
    w = _widget(qtbot)
    w._max_rounds.setValue(4)
    w._fixed_mode.setChecked(True)
    # Criteria group greys out; the iterations label switches.
    assert not w._crit_group.isEnabled()
    assert w._mr_label.text() == "Iterations:"
    cfg = w.current_config()
    assert cfg.fixed_iterations == 4
    # Unchecking returns to criteria-driven mode.
    w._fixed_mode.setChecked(False)
    assert w._crit_group.isEnabled()
    assert w.current_config().fixed_iterations is None


def test_fixed_mode_config_constructs_real_settings_with_empty_criteria(qtbot):
    from percell4.workflows.models import IterativeOtsuSettings

    w = _widget(qtbot)
    for cb, _, _ in w._rows.values():
        cb.setChecked(False)  # all criteria off
    w._fixed_mode.setChecked(True)
    cfg = w.current_config()
    assert cfg.stop_criteria == ()
    s = IterativeOtsuSettings(
        scope=cfg.scope,
        dilation_radius_px=cfg.dilation_radius_px,
        max_rounds=cfg.max_rounds,
        stop_criteria=cfg.stop_criteria,
        stop_params=cfg.stop_params,
        stop_combine=cfg.stop_combine,
        fixed_iterations=cfg.fixed_iterations,
    )
    assert s.fixed_iterations == cfg.fixed_iterations


def test_config_changed_fires_on_edits(qtbot):
    w = _widget(qtbot)
    fired = []
    w.config_changed.connect(lambda: fired.append(1))
    w._dilation.setValue(7)
    w._max_rounds.setValue(12)
    w._rows["min-positive"][0].setChecked(True)
    assert len(fired) >= 3
