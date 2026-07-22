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
    # "CNR threshold" (guided) is index 0 now that Discover is gone.
    assert cfg == CnrClassifyConfig(source_mask="", mode="guided", threshold=8.0)


def test_threshold_live_only_in_cnr_threshold_mode(qtbot):
    w = _widget(qtbot)
    # "CNR threshold" is the default -> threshold live from the start
    assert w._threshold.isEnabled()
    assert w.current_config().mode == "guided"
    # "Auto Two Groups" -> threshold off
    w._mode.setCurrentText("Auto Two Groups")
    assert not w._threshold.isEnabled()
    assert w.current_config().mode == "forced"
    # "Interactive" -> threshold off too (the histogram supplies the split)
    w._mode.setCurrentText("Interactive")
    assert not w._threshold.isEnabled()
    assert w.current_config().mode == "interactive"
    # back to the default -> live again
    w._mode.setCurrentText("CNR threshold")
    assert w._threshold.isEnabled()


def test_discover_is_gone(qtbot):
    """R7: Discover is removed from both the dropdown and the code map."""
    from percell4.gui._cnr_classify_settings import _MODE_CODES, _MODE_LABELS

    w = _widget(qtbot)
    items = [w._mode.itemText(i) for i in range(w._mode.count())]
    assert items == ["CNR threshold", "Auto Two Groups", "Interactive"]
    assert "discover" not in _MODE_CODES.values()
    assert not any("Discover" in label for label in _MODE_LABELS)


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
    w._threshold.setValue(12.5)
    assert w.current_config().threshold == 12.5


def test_config_changed_emits_on_edit(qtbot):
    w = _widget(qtbot)
    fired = []
    w.config_changed.connect(lambda: fired.append(1))
    w._mode.setCurrentText("Auto Two Groups")  # off the default index
    assert fired  # at least one emit on the mode edit
    fired.clear()
    w._threshold.setValue(5.0)
    assert fired


def test_set_enabled_locks_and_restores_gating(qtbot):
    w = _widget(qtbot)
    w._mode.setCurrentText("CNR threshold")
    w.set_enabled(False)
    assert not w._source.isEnabled()
    assert not w._mode.isEnabled()
    assert not w._threshold.isEnabled()
    w.set_enabled(True)
    assert w._source.isEnabled()
    assert w._threshold.isEnabled()  # CNR threshold -> re-enabled
