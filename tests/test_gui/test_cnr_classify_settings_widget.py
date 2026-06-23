"""Tests for CnrClassifySettingsWidget (U5)."""

from __future__ import annotations

from percell4.gui._cnr_classify_settings import (
    CnrClassifyConfig,
    CnrClassifySettingsWidget,
)


def _widget(qtbot) -> CnrClassifySettingsWidget:
    w = CnrClassifySettingsWidget()
    qtbot.addWidget(w)
    return w


def test_default_config(qtbot):
    w = _widget(qtbot)
    cfg = w.current_config()
    assert cfg == CnrClassifyConfig(source_mask="", mode="discover", threshold=8.0)


def test_threshold_disabled_unless_guided(qtbot):
    w = _widget(qtbot)
    # discover (default) -> threshold off
    assert not w._threshold.isEnabled()
    # guided -> threshold on
    w._mode.setCurrentText("Guided (CNR threshold)")
    assert w._threshold.isEnabled()
    assert w.current_config().mode == "guided"
    # forced -> threshold off again
    w._mode.setCurrentText("Forced (always 2)")
    assert not w._threshold.isEnabled()
    assert w.current_config().mode == "forced"


def test_set_mask_choices_populates_and_selects(qtbot):
    w = _widget(qtbot)
    w.set_mask_choices(["adaptive", "multiscale"])
    assert w._source.count() == 2
    assert w.current_config().source_mask in ("adaptive", "multiscale")
    # empty list clears without raising
    w.set_mask_choices([])
    assert w._source.count() == 0
    assert w.current_config().source_mask == ""


def test_set_mask_choices_preserves_current_pick(qtbot):
    w = _widget(qtbot)
    w.set_mask_choices(["a", "b", "c"])
    w._source.setCurrentText("b")
    w.set_mask_choices(["a", "b", "c", "d"])  # refresh keeps the pick
    assert w.current_config().source_mask == "b"


def test_threshold_value_reaches_config(qtbot):
    w = _widget(qtbot)
    w._mode.setCurrentText("Guided (CNR threshold)")
    w._threshold.setValue(12.5)
    assert w.current_config().threshold == 12.5


def test_config_changed_emits_on_edit(qtbot):
    w = _widget(qtbot)
    fired = []
    w.config_changed.connect(lambda: fired.append(1))
    w._mode.setCurrentText("Guided (CNR threshold)")
    assert fired  # at least one emit on the mode edit
    fired.clear()
    w._threshold.setValue(5.0)
    assert fired


def test_set_enabled_locks_and_restores_gating(qtbot):
    w = _widget(qtbot)
    w._mode.setCurrentText("Guided (CNR threshold)")
    w.set_enabled(False)
    assert not w._source.isEnabled()
    assert not w._mode.isEnabled()
    assert not w._threshold.isEnabled()
    w.set_enabled(True)
    assert w._source.isEnabled()
    assert w._threshold.isEnabled()  # guided -> threshold re-enabled
