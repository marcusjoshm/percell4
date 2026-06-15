"""Tests for AdaptiveClipSettingsWidget (U3)."""

from __future__ import annotations

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
    """Each dropdown label maps to its WINDOW_FINDERS registry name."""
    w = _widget(qtbot)
    for label, code in [
        ("Granule size", "granule-size"),
        ("Otsu mean (baseline)", "otsu-mean"),
    ]:
        w._window_method.setCurrentText(label)
        assert w.current_config().window_method == code


def test_window_method_gated_by_auto_and_particle(qtbot):
    """The method dropdown is active only when Auto is on and particle mode is off."""
    w = _widget(qtbot)
    assert not w._window_method.isEnabled()  # auto off by default
    w._auto.setChecked(True)
    assert w._window_method.isEnabled()  # auto on, not particle
    w._particle.setChecked(True)
    assert not w._window_method.isEnabled()  # particle mode derives the window
    w._particle.setChecked(False)
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


# ── particle-size (one-knob) mode ───────────────────────────────────────

def test_particle_mode_defaults_and_snapshot(qtbot):
    w = _widget(qtbot)
    assert w.current_config().particle_mode is False
    assert w.current_config().d_min_um == 0.40
    w._particle.setChecked(True)
    w._d_min.setValue(0.14)
    cfg = w.current_config()
    assert cfg.particle_mode is True
    assert cfg.d_min_um == 0.14


def test_particle_mode_gates_fields(qtbot):
    w = _widget(qtbot)
    assert not w._d_min.isEnabled()  # off until particle mode is checked
    w._particle.setChecked(True)
    assert w._d_min.isEnabled()
    # window / size / unit / noise / auto are derived or fixed -> disabled.
    for widget in (w._window, w._min_size, w._unit, w._noise, w._auto):
        assert not widget.isEnabled()
    # k and Gaussian σ stay live (sensitivity + noise-suppression knobs).
    assert w._k.isEnabled()
    assert w._sigma.isEnabled()
    # Unchecking restores manual gating (window live, k live).
    w._particle.setChecked(False)
    assert not w._d_min.isEnabled()
    assert w._k.isEnabled()
    assert w._window.isEnabled()


def test_particle_mode_adopts_default_k_one_but_stays_editable(qtbot):
    w = _widget(qtbot)
    w._k.setValue(2.25)
    w._particle.setChecked(True)
    assert w.current_config().k == 1.0  # validated one-knob default on entry
    w._k.setValue(3.0)  # raise to be conservative
    assert w.current_config().k == 3.0


def test_set_enabled_respects_particle_gate(qtbot):
    w = _widget(qtbot)
    w._particle.setChecked(True)
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
    w._particle.setChecked(True)
    w._d_min.setValue(0.2)
    assert len(fired) >= 2
