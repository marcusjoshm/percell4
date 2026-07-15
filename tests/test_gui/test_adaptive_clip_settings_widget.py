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
        smallest_particle_value=3.0,
        smallest_particle_unit="px",
        auto_extract_smallest_auto=True,
        largest_only=False,
    )


def test_widget_exposes_only_auto_extract_fields(qtbot):
    """Only the auto-extraction fields exist; the removed-mode widgets are gone."""
    w = _widget(qtbot)
    # Kept widgets: the auto-detect toggle, smallest Ø (+unit), σ, min size (+unit).
    for attr in (
        "_ae_smallest_auto",
        "_smallest",
        "_smallest_unit",
        "_sigma",
        "_min_size",
        "_unit",
    ):
        assert hasattr(w, attr), f"missing kept widget {attr}"
    # Removed widgets (the other four detection modes).
    for attr in (
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


def test_programmatic_changes_reflected(qtbot):
    w = _widget(qtbot)
    w._sigma.setValue(0.0)
    w._min_size.setValue(12.0)
    w._unit.setCurrentText("µm²")
    w._ae_smallest_auto.setChecked(False)
    w._smallest.setValue(5.0)

    cfg = w.current_config()
    assert cfg.gaussian_sigma == 0.0
    assert cfg.min_size_value == 12.0
    assert cfg.min_size_unit == "um2"
    assert cfg.auto_extract_smallest_auto is False
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
    w._ae_smallest_auto.setChecked(False)
    w._unit.setCurrentIndex(1)
    assert len(fired) >= 4


# ── auto-extraction (two-pass): the only mode ────────────────────────────────


def test_auto_extract_defaults(qtbot):
    w = _widget(qtbot)
    cfg = w.current_config()
    assert cfg.smallest_particle_value == 3.0
    assert cfg.smallest_particle_unit == "px"
    assert cfg.auto_extract_smallest_auto is True  # auto-detect by default


def test_auto_extract_gates_smallest_field(qtbot):
    w = _widget(qtbot)
    # The Auto-detect checkbox is always live; with it ON (default) the smallest-Ø
    # field is a disabled readout.
    assert w._ae_smallest_auto.isEnabled()
    assert w._ae_smallest_auto.isChecked()
    assert not w._smallest.isEnabled()
    assert not w._smallest_unit.isEnabled()
    # Gaussian σ and the min particle-size filter are always live.
    assert w._sigma.isEnabled()
    assert w._min_size.isEnabled()
    assert w._unit.isEnabled()


def test_auto_extract_uncheck_auto_enables_manual_smallest(qtbot):
    w = _widget(qtbot)
    assert not w._smallest.isEnabled()       # auto on -> readout
    w._ae_smallest_auto.setChecked(False)    # manual override
    assert w._smallest.isEnabled()
    assert w._smallest_unit.isEnabled()
    assert w.current_config().auto_extract_smallest_auto is False


def test_auto_extract_smallest_value_and_unit_reach_config(qtbot):
    w = _widget(qtbot)
    w._ae_smallest_auto.setChecked(False)  # manual override
    w._smallest.setValue(2.0)
    w._smallest_unit.setCurrentText("µm")
    cfg = w.current_config()
    assert cfg.smallest_particle_value == 2.0
    assert cfg.smallest_particle_unit == "um"


def test_auto_extract_set_smallest_value_readout(qtbot):
    w = _widget(qtbot)
    w.set_smallest_value(4.5)
    assert w.current_config().smallest_particle_value == 4.5
    assert w.current_config().smallest_particle_unit == "px"


def test_set_enabled_respects_smallest_gate(qtbot):
    w = _widget(qtbot)
    w._ae_smallest_auto.setChecked(False)  # manual -> smallest field live
    w.set_enabled(False)
    assert not w._smallest.isEnabled()
    w.set_enabled(True)
    assert w._smallest.isEnabled()  # gating re-applied on unlock (manual)


# ── largest-only single-pass mode (U2) ───────────────────────────────────────


def test_largest_only_default_off(qtbot):
    w = _widget(qtbot)
    assert hasattr(w, "_largest_only")
    assert w._largest_only.isChecked() is False
    assert w.current_config().largest_only is False


def test_largest_only_reaches_config(qtbot):
    w = _widget(qtbot)
    w._largest_only.setChecked(True)
    assert w.current_config().largest_only is True


def test_largest_only_disables_smallest_controls(qtbot):
    """Largest-only has no fine pass, so the whole smallest-particle group is off."""
    w = _widget(qtbot)
    w._largest_only.setChecked(True)
    assert not w._ae_smallest_auto.isEnabled()
    assert not w._smallest.isEnabled()
    assert not w._smallest_unit.isEnabled()
    # Gaussian σ and the min particle-size filter stay live.
    assert w._sigma.isEnabled()
    assert w._min_size.isEnabled()
    assert w._unit.isEnabled()


def test_largest_only_off_restores_auto_detect_gating(qtbot):
    w = _widget(qtbot)
    w._largest_only.setChecked(True)
    w._largest_only.setChecked(False)
    # Back to the auto-detect gating: toggle live, smallest field follows it.
    assert w._ae_smallest_auto.isEnabled()
    assert not w._smallest.isEnabled()       # auto-detect on -> readout
    w._ae_smallest_auto.setChecked(False)
    assert w._smallest.isEnabled()           # manual override live again


def test_largest_only_fires_config_changed(qtbot):
    w = _widget(qtbot)
    fired = []
    w.config_changed.connect(lambda: fired.append(1))
    w._largest_only.setChecked(True)
    assert len(fired) >= 1


def test_set_enabled_locks_largest_only(qtbot):
    w = _widget(qtbot)
    w._largest_only.setChecked(True)
    w.set_enabled(False)
    assert not w._largest_only.isEnabled()
    w.set_enabled(True)
    assert w._largest_only.isEnabled()
    # Gating re-applied on unlock: smallest controls stay disabled in largest-only.
    assert not w._smallest.isEnabled()
    assert not w._ae_smallest_auto.isEnabled()
