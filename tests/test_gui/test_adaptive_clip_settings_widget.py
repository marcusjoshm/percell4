"""Tests for AdaptiveClipSettingsWidget (U3)."""

from __future__ import annotations

import pytest

from percell4.gui._adaptive_clip_settings import (
    AdaptiveClipConfig,
    AdaptiveClipSettingsWidget,
)


def _widget(qtbot) -> AdaptiveClipSettingsWidget:
    w = AdaptiveClipSettingsWidget()
    qtbot.addWidget(w)
    return w


def test_default_config(qtbot):
    w = _widget(qtbot)
    assert w.current_config() == AdaptiveClipConfig(
        window_value=15.0,
        window_unit="px",
        k=2.25,
        gaussian_sigma=1.0,
        min_size_value=3.0,
        min_size_unit="px",
        auto_window=False,
        noise_estimator="mad",
    )


def test_noise_estimator_default_is_mad(qtbot):
    """The Noise (σ) estimate dropdown defaults to MAD (matches the reference)."""
    w = _widget(qtbot)
    assert w._noise.currentText() == "MAD (robust)"
    assert w.current_config().noise_estimator == "mad"


def test_noise_estimator_mapping(qtbot):
    """Each dropdown label maps to its BACKGROUND_ESTIMATORS registry name."""
    w = _widget(qtbot)
    for label, code in [
        ("MAD (robust)", "mad"),
        ("stddev", "stddev"),
        ("gaussian-peak", "gaussian-peak"),
    ]:
        w._noise.setCurrentText(label)
        assert w.current_config().noise_estimator == code


def test_window_method_default_is_granule_size(qtbot):
    """The Auto window method dropdown defaults to the granule-isolating finder."""
    w = _widget(qtbot)
    assert w._window_method.currentText() == "Granule size"
    assert w.current_config().window_method == "granule-size"


def test_window_method_mapping(qtbot):
    """Each dropdown label maps to its internal method code."""
    w = _widget(qtbot)
    for label, code in [
        ("Granule size", "granule-size"),
        ("Otsu mean (baseline)", "otsu-mean"),
        ("Otsu detect particle size", "otsu-smallest"),
        ("Multi-scale (particle range)", "multiscale"),
    ]:
        w._window_method.setCurrentText(label)
        assert w.current_config().window_method == code


def test_window_method_gated_by_auto(qtbot):
    """The method dropdown is active only when Auto is on (it is the picker)."""
    w = _widget(qtbot)
    assert not w._window_method.isEnabled()  # auto off by default
    w._auto.setChecked(True)
    assert w._window_method.isEnabled()
    w._auto.setChecked(False)
    assert not w._window_method.isEnabled()


def test_set_enabled_respects_window_method_gate(qtbot):
    w = _widget(qtbot)
    w._auto.setChecked(True)
    w.set_enabled(True)
    assert w._window_method.isEnabled()  # auto on -> method live after global enable
    w.set_enabled(False)
    assert not w._window_method.isEnabled()


def test_programmatic_changes_reflected(qtbot):
    w = _widget(qtbot)
    w._window.setValue(31)
    w._k.setValue(2.5)
    w._sigma.setValue(0.0)
    w._min_size.setValue(12.0)
    w._unit.setCurrentText("µm²")
    w._auto.setChecked(True)

    cfg = w.current_config()
    assert cfg.window_value == 31.0
    assert cfg.window_unit == "px"
    assert cfg.k == 2.5
    assert cfg.gaussian_sigma == 0.0
    assert cfg.min_size_value == 12.0
    assert cfg.min_size_unit == "um2"
    assert cfg.auto_window is True


def test_window_value_and_unit_in_config(qtbot):
    w = _widget(qtbot)
    w._window.setValue(20.0)
    assert w.current_config().window_value == 20.0
    assert w.current_config().window_unit == "px"  # default unit
    w._window_unit.setCurrentText("µm")
    assert w.current_config().window_unit == "um"  # the engine resolves µm -> odd px


def test_auto_checkbox_gates_window_field(qtbot):
    w = _widget(qtbot)
    assert w._window.isEnabled()
    w._auto.setChecked(True)
    assert not w._window.isEnabled()
    w._auto.setChecked(False)
    assert w._window.isEnabled()


def test_unit_mapping(qtbot):
    w = _widget(qtbot)
    w._unit.setCurrentText("px²")
    assert w.current_config().min_size_unit == "px"
    w._unit.setCurrentText("µm²")
    assert w.current_config().min_size_unit == "um2"


def test_config_changed_fires_on_edits(qtbot):
    w = _widget(qtbot)
    fired = []
    w.config_changed.connect(lambda: fired.append(1))
    w._window.setValue(25)
    w._k.setValue(3.0)
    w._auto.setChecked(True)
    w._unit.setCurrentIndex(1)
    assert len(fired) >= 4


def test_set_window_value_displays_odd_px(qtbot):
    w = _widget(qtbot)
    w._window_unit.setCurrentText("µm")  # set_window_value forces the unit back to px
    w.set_window_value(50)
    assert w._window.value() == 51.0
    cfg = w.current_config()
    assert cfg.window_value == 51.0
    assert cfg.window_unit == "px"


def test_set_enabled_respects_auto_gate(qtbot):
    w = _widget(qtbot)
    w._auto.setChecked(True)
    w.set_enabled(True)
    # window stays disabled while auto is on, even after a global enable
    assert not w._window.isEnabled()
    assert w._k.isEnabled()
    w.set_enabled(False)
    assert not w._k.isEnabled()


# ── particle-size (one-knob) mode via the "Otsu detect smallest particle" method ──


def _enter_particle_mode(w) -> None:
    """Activate the per-cell d_min engine: Auto on + the Otsu-smallest method."""
    w._auto.setChecked(True)
    w._window_method.setCurrentText("Otsu detect particle size")


def test_particle_mode_defaults_and_snapshot(qtbot):
    w = _widget(qtbot)
    assert w.current_config().particle_mode is False
    assert w.current_config().d_min_um == 0.40
    _enter_particle_mode(w)
    w._d_min.setValue(0.14)
    cfg = w.current_config()
    assert cfg.particle_mode is True
    assert cfg.window_method == "otsu-smallest"
    assert cfg.d_min_um == 0.14


def test_particle_mode_gates_fields(qtbot):
    w = _widget(qtbot)
    assert not w._d_min.isEnabled()  # readout, never an input
    _enter_particle_mode(w)
    assert not w._d_min.isEnabled()  # still a readout (host fills it fresh at run)
    # size / unit / noise are derived or fixed -> disabled in particle mode.
    for widget in (w._min_size, w._unit, w._noise):
        assert not widget.isEnabled()
    # window is derived (auto on); the method dropdown stays live (it is the picker).
    assert not w._window.isEnabled()
    assert w._window_method.isEnabled()
    # k, Gaussian σ, and Auto stay live.
    assert w._k.isEnabled()
    assert w._sigma.isEnabled()
    assert w._auto.isEnabled()
    # Switching to a finder method leaves particle mode (size filter back on).
    w._window_method.setCurrentText("Granule size")
    assert w._min_size.isEnabled()
    assert w._k.isEnabled()


def test_particle_mode_adopts_default_k_one_but_stays_editable(qtbot):
    w = _widget(qtbot)
    w._k.setValue(2.25)
    _enter_particle_mode(w)
    assert w.current_config().k == 1.0  # validated one-knob default on entry
    w._k.setValue(3.0)  # raise to be conservative
    assert w.current_config().k == 3.0


def test_d_min_is_a_readout_filled_by_set_d_min_um(qtbot):
    """In particle mode the Ø field is a disabled readout, written by the host."""
    w = _widget(qtbot)
    _enter_particle_mode(w)
    assert not w._d_min.isEnabled()  # not user-editable
    w.set_d_min_um(0.73)  # the host's run-time readout write still lands
    assert w.current_config().d_min_um == pytest.approx(0.73)


def test_size_percentile_default_gated_and_in_config(qtbot):
    """The size-percentile knob defaults to 10 and is editable only in particle mode."""
    w = _widget(qtbot)
    assert w.current_config().particle_percentile == 10.0
    assert not w._percentile.isEnabled()  # off until the per-cell method is selected
    _enter_particle_mode(w)
    assert w._percentile.isEnabled()  # the editable per-cell knob
    w._percentile.setValue(25.0)
    assert w.current_config().particle_percentile == 25.0
    # Leaving particle mode disables it again.
    w._window_method.setCurrentText("Granule size")
    assert not w._percentile.isEnabled()


def test_set_enabled_respects_particle_gate(qtbot):
    w = _widget(qtbot)
    _enter_particle_mode(w)
    w.set_enabled(True)
    assert not w._d_min.isEnabled()  # readout, never editable
    assert not w._window.isEnabled()
    assert w._k.isEnabled()  # k is a live knob in particle mode
    w.set_enabled(False)
    assert not w._d_min.isEnabled()


def test_config_changed_fires_on_particle_edits(qtbot):
    w = _widget(qtbot)
    fired = []
    w.config_changed.connect(lambda: fired.append(1))
    _enter_particle_mode(w)
    w._k.setValue(0.5)
    assert len(fired) >= 2


def test_otsu_smallest_inert_without_auto(qtbot):
    """With Auto off the Otsu-smallest method is not active (not particle mode)."""
    w = _widget(qtbot)
    w._window_method.setCurrentText("Otsu detect particle size")
    assert w.current_config().particle_mode is False


def test_checking_auto_enters_particle_when_method_preselected(qtbot):
    """Auto-on while Otsu-smallest is the selected method enters particle mode."""
    w = _widget(qtbot)
    w._window_method.setCurrentText("Otsu detect particle size")  # inert (auto off)
    assert w.current_config().particle_mode is False
    w._auto.setChecked(True)
    assert w.current_config().particle_mode is True


def test_set_d_min_um_updates_field_and_config(qtbot):
    w = _widget(qtbot)
    w.set_d_min_um(0.235)
    assert w._d_min.value() == pytest.approx(0.235)
    assert w.current_config().d_min_um == pytest.approx(0.235)


# ── multi-scale mode (per-cell, doubling windows) ───────────────────────


def _enter_multiscale_mode(w) -> None:
    w._auto.setChecked(True)
    w._window_method.setCurrentText("Multi-scale (particle range)")


def test_multiscale_mode_config_and_defaults(qtbot):
    w = _widget(qtbot)
    cfg = w.current_config()
    assert cfg.multiscale_mode is False
    assert cfg.size_cutoff_px == 0.0
    assert cfg.ms_auto_start is True
    _enter_multiscale_mode(w)
    cfg = w.current_config()
    assert cfg.multiscale_mode is True
    assert cfg.window_method == "multiscale"
    assert cfg.particle_mode is False  # distinct from the otsu-smallest mode


def test_multiscale_mode_gates_fields(qtbot):
    w = _widget(qtbot)
    assert not w._cutoff.isEnabled()
    assert not w._ms_auto_start.isEnabled()
    _enter_multiscale_mode(w)
    assert w._cutoff.isEnabled()
    assert w._ms_auto_start.isEnabled()
    # Min particle size filters the multi-scale output, so it stays live; the noise
    # estimator (whole-frame only) and the percentile (otsu-smallest) are off.
    assert w._min_size.isEnabled()
    assert w._unit.isEnabled()
    for widget in (w._noise, w._percentile):
        assert not widget.isEnabled()
    # Auto start on -> the manual Window field is off.
    assert not w._window.isEnabled()
    # Turning auto start off makes the Window field the manual starting window.
    w._ms_auto_start.setChecked(False)
    assert w._window.isEnabled()
    assert w._window_unit.isEnabled()


def test_multiscale_mode_adopts_k_one(qtbot):
    w = _widget(qtbot)
    w._k.setValue(2.25)
    _enter_multiscale_mode(w)
    assert w.current_config().k == 1.0  # validated default on entering a per-cell mode


def test_multiscale_cutoff_reaches_config(qtbot):
    w = _widget(qtbot)
    _enter_multiscale_mode(w)
    w._cutoff.setValue(8.0)
    assert w.current_config().size_cutoff_px == 8.0
    w._ms_auto_start.setChecked(False)
    assert w.current_config().ms_auto_start is False


def test_multiscale_iterations_gated_and_in_config(qtbot):
    w = _widget(qtbot)
    assert w.current_config().ms_iterations == 0  # auto by default
    assert not w._iterations.isEnabled()
    _enter_multiscale_mode(w)
    assert w._iterations.isEnabled()
    w._iterations.setValue(5)
    assert w.current_config().ms_iterations == 5


# ── AE-U3: auto-extraction (two-pass) mode ───────────────────────────────────


def _enter_auto_extract_mode(w) -> None:
    w._auto.setChecked(True)
    w._window_method.setCurrentText("Auto extraction (two-pass)")


def test_auto_extract_mode_config_and_defaults(qtbot):
    w = _widget(qtbot)
    cfg = w.current_config()
    assert cfg.auto_extract_mode is False
    assert cfg.smallest_particle_value == 3.0
    assert cfg.smallest_particle_unit == "px"
    _enter_auto_extract_mode(w)
    cfg = w.current_config()
    assert cfg.auto_extract_mode is True
    assert cfg.window_method == "auto-extract"
    # distinct from the other per-cell engine switches
    assert cfg.particle_mode is False
    assert cfg.multiscale_mode is False


def test_auto_extract_mode_gates_fields(qtbot):
    w = _widget(qtbot)
    assert not w._smallest.isEnabled()
    assert not w._smallest_unit.isEnabled()
    _enter_auto_extract_mode(w)
    assert w._smallest.isEnabled()
    assert w._smallest_unit.isEnabled()
    # Min particle size filters the union, so it stays live.
    assert w._min_size.isEnabled()
    assert w._unit.isEnabled()
    # k is ignored in auto-extract (fine k=1, coarse auto) -> disabled.
    assert not w._k.isEnabled()
    # noise estimator (whole-frame), percentile (otsu-smallest), multi-scale
    # controls are all off here.
    for widget in (w._noise, w._percentile, w._cutoff, w._ms_auto_start, w._iterations):
        assert not widget.isEnabled()
    # the manual window field is off in this mode
    assert not w._window.isEnabled()


def test_auto_extract_smallest_value_and_unit_reach_config(qtbot):
    w = _widget(qtbot)
    _enter_auto_extract_mode(w)
    w._smallest.setValue(2.0)
    w._smallest_unit.setCurrentText("µm")
    cfg = w.current_config()
    assert cfg.smallest_particle_value == 2.0
    assert cfg.smallest_particle_unit == "um"


def test_auto_extract_inert_without_auto(qtbot):
    w = _widget(qtbot)
    w._window_method.setCurrentText("Auto extraction (two-pass)")  # but Auto off
    assert w.current_config().auto_extract_mode is False


def test_set_enabled_respects_auto_extract_gate(qtbot):
    w = _widget(qtbot)
    _enter_auto_extract_mode(w)
    w.set_enabled(False)
    assert not w._smallest.isEnabled()
    w.set_enabled(True)
    assert w._smallest.isEnabled()  # gating re-applied on unlock
    assert not w._k.isEnabled()
