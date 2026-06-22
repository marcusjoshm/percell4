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
        window_px=15,
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
        ("Otsu detect smallest particle size", "otsu-smallest"),
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
    assert cfg.window_px == 31
    assert cfg.k == 2.5
    assert cfg.gaussian_sigma == 0.0
    assert cfg.min_size_value == 12.0
    assert cfg.min_size_unit == "um2"
    assert cfg.auto_window is True


def test_window_reported_odd(qtbot):
    w = _widget(qtbot)
    w._window.setValue(20)  # even
    assert w.current_config().window_px == 21  # forced odd


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


def test_set_window_value_displays_odd(qtbot):
    w = _widget(qtbot)
    w.set_window_value(50)
    assert w._window.value() == 51
    assert w.current_config().window_px == 51


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
    w._window_method.setCurrentText("Otsu detect smallest particle size")


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
    assert not w._d_min.isEnabled()  # off until the per-cell method is selected
    _enter_particle_mode(w)
    assert w._d_min.isEnabled()
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
    # Switching to a finder method leaves particle mode (d_min off, size filter on).
    w._window_method.setCurrentText("Granule size")
    assert not w._d_min.isEnabled()
    assert w._min_size.isEnabled()
    assert w._k.isEnabled()


def test_particle_mode_adopts_default_k_one_but_stays_editable(qtbot):
    w = _widget(qtbot)
    w._k.setValue(2.25)
    _enter_particle_mode(w)
    assert w.current_config().k == 1.0  # validated one-knob default on entry
    w._k.setValue(3.0)  # raise to be conservative
    assert w.current_config().k == 3.0


def test_set_enabled_respects_particle_gate(qtbot):
    w = _widget(qtbot)
    _enter_particle_mode(w)
    w.set_enabled(True)
    assert w._d_min.isEnabled()
    assert not w._window.isEnabled()
    assert w._k.isEnabled()  # k is a live knob in particle mode
    w.set_enabled(False)
    assert not w._d_min.isEnabled()


def test_config_changed_fires_on_particle_edits(qtbot):
    w = _widget(qtbot)
    fired = []
    w.config_changed.connect(lambda: fired.append(1))
    _enter_particle_mode(w)
    w._d_min.setValue(0.2)
    assert len(fired) >= 2


def test_otsu_smallest_emits_detect_request(qtbot):
    """Selecting the per-cell method asks the host to auto-fill d_min from Otsu."""
    w = _widget(qtbot)
    requested = []
    w.otsu_detect_requested.connect(lambda: requested.append(1))
    _enter_particle_mode(w)
    assert requested == [1]


def test_otsu_smallest_inert_without_auto(qtbot):
    """With Auto off the method is not active: no request, not particle mode."""
    w = _widget(qtbot)
    requested = []
    w.otsu_detect_requested.connect(lambda: requested.append(1))
    w._window_method.setCurrentText("Otsu detect smallest particle size")
    assert requested == []
    assert w.current_config().particle_mode is False


def test_checking_auto_enters_particle_when_method_preselected(qtbot):
    """Auto-on while Otsu-smallest is the selected method enters particle mode."""
    w = _widget(qtbot)
    requested = []
    w.otsu_detect_requested.connect(lambda: requested.append(1))
    w._window_method.setCurrentText("Otsu detect smallest particle size")  # inert (auto off)
    assert requested == []
    w._auto.setChecked(True)
    assert requested == [1]
    assert w.current_config().particle_mode is True


def test_set_d_min_um_updates_field_and_config(qtbot):
    w = _widget(qtbot)
    w.set_d_min_um(0.235)
    assert w._d_min.value() == pytest.approx(0.235)
    assert w.current_config().d_min_um == pytest.approx(0.235)
