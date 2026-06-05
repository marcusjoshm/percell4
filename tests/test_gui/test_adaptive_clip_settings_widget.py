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
    )


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
