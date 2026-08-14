"""Tests for AdaptiveClipSettingsWidget (auto-extraction-only form)."""

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
        gaussian_sigma=1.0,
        min_size_value=3.0,
        min_size_unit="px",
        smallest_particle_value=2.0,
        smallest_particle_unit="px",
    )


def test_widget_exposes_only_auto_extract_fields(qtbot):
    """Only the surviving fields exist; the removed-mode widgets are gone."""
    w = _widget(qtbot)
    # Kept widgets: smallest Ø (+unit), σ, min area (+unit).
    for attr in (
        "_smallest",
        "_smallest_unit",
        "_sigma",
        "_min_size",
        "_unit",
    ):
        assert hasattr(w, attr), f"missing kept widget {attr}"
    # Removed widgets: the dev-only detection knobs and the other four modes.
    for attr in (
        "_largest_only",
        "_ae_smallest_auto",
        "_fill_factor",
        "_fdr",
        "_auto",
        "_window_method",
        "_percentile",
        "_d_min",
        "_cutoff",
        "_ms_auto_start",
        "_iterations",
        "_window",
        "_window_unit",
        "_k",
        "_noise",
    ):
        assert not hasattr(w, attr), f"removed widget {attr} still present"
    # Removed readout setters.
    assert not hasattr(w, "set_d_min_um")
    assert not hasattr(w, "set_window_value")
    assert not hasattr(w, "set_smallest_value")


def test_config_has_no_removed_fields(qtbot):
    """The frozen config drops the four dev-only knobs entirely."""
    cfg = _widget(qtbot).current_config()
    for field in (
        "largest_only",
        "auto_extract_smallest_auto",
        "fill_factor",
        "fdr",
    ):
        assert not hasattr(cfg, field), f"removed config field {field} still present"


def test_programmatic_changes_reflected(qtbot):
    w = _widget(qtbot)
    w._sigma.setValue(0.0)
    w._min_size.setValue(12.0)
    w._unit.setCurrentText("µm²")
    w._smallest.setValue(5.0)

    cfg = w.current_config()
    assert cfg.gaussian_sigma == 0.0
    assert cfg.min_size_value == 12.0
    assert cfg.min_size_unit == "um2"
    assert cfg.smallest_particle_value == 5.0


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
    w._sigma.setValue(2.0)
    w._min_size.setValue(25.0)
    w._smallest.setValue(4.0)
    w._unit.setCurrentIndex(1)
    assert len(fired) >= 4


# ── smallest particle diameter: always live, no gating ───────────────────────


def test_smallest_defaults_to_two_px(qtbot):
    """R4: the default diameter is 2, in px."""
    cfg = _widget(qtbot).current_config()
    assert cfg.smallest_particle_value == 2.0
    assert cfg.smallest_particle_unit == "px"


def test_all_controls_live_on_construction(qtbot):
    """With auto-detect gone the form has no modes — everything is enabled."""
    w = _widget(qtbot)
    assert w._smallest.isEnabled()
    assert w._smallest_unit.isEnabled()
    assert w._sigma.isEnabled()
    assert w._min_size.isEnabled()
    assert w._unit.isEnabled()


def test_smallest_value_and_unit_reach_config(qtbot):
    w = _widget(qtbot)
    w._smallest.setValue(5.0)
    w._smallest_unit.setCurrentText("µm")
    cfg = w.current_config()
    assert cfg.smallest_particle_value == 5.0
    assert cfg.smallest_particle_unit == "um"


def test_set_enabled_round_trip_restores_every_widget(qtbot):
    """No gating survives the unlock — all five controls come back live."""
    w = _widget(qtbot)
    w.set_enabled(False)
    for widget in (w._smallest, w._smallest_unit, w._sigma, w._min_size, w._unit):
        assert not widget.isEnabled()
    w.set_enabled(True)
    for widget in (w._smallest, w._smallest_unit, w._sigma, w._min_size, w._unit):
        assert widget.isEnabled()
