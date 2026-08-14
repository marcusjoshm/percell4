"""Tests for the shared analysis-dialog widget factories (U7)."""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox, QWidget

from percell4.domain.analysis import (
    BoolParam,
    ChoiceParam,
    FloatParam,
    IntParam,
)
from percell4.gui.analysis_widgets import (
    LAYER_SENTINEL,
    build_dataset_picker,
    build_layer_combo,
    build_param_widget,
    build_preset_combo,
    populate_layer_combo,
)

# ── dataset picker ────────────────────────────────────────────


def test_build_dataset_picker_returns_three_widgets(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    listw, add_files, add_folder = build_dataset_picker(parent)
    assert listw is not None
    assert add_files.text().startswith("Add .h5 files")
    assert add_folder.text().startswith("Add folder")


# ── layer combo + populate ────────────────────────────────────


def test_build_layer_combo_starts_with_sentinel_only(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    _row, label, combo = build_layer_combo("cap", "Capture channel", parent)
    assert combo.count() == 1
    assert combo.itemText(0) == LAYER_SENTINEL
    assert "Capture channel" in label.text()


def test_populate_layer_combo_intersection_and_sort(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    _row, _label, combo = build_layer_combo("cap", "", parent)
    populated = populate_layer_combo(combo, ["pnorm", "Cap"])
    assert combo.count() == 3
    assert combo.itemText(0) == LAYER_SENTINEL
    assert combo.itemText(1) == "Cap"  # sorted
    assert combo.itemText(2) == "pnorm"
    # No prior selection → snapped to sentinel.
    assert populated is False
    assert combo.currentText() == LAYER_SENTINEL


def test_populate_preserves_prior_when_still_available(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    _row, _label, combo = build_layer_combo("cap", "", parent)
    populate_layer_combo(combo, ["Cap", "pnorm"])
    combo.setCurrentText("Cap")
    populated = populate_layer_combo(combo, ["Cap", "other"])
    assert populated is True
    assert combo.currentText() == "Cap"


def test_populate_snaps_to_sentinel_when_prior_gone(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    _row, _label, combo = build_layer_combo("cap", "", parent)
    populate_layer_combo(combo, ["Cap", "pnorm"])
    combo.setCurrentText("Cap")
    populated = populate_layer_combo(combo, ["other"])
    assert populated is False
    assert combo.currentText() == LAYER_SENTINEL


def test_populate_with_empty_inventory(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    _row, _label, combo = build_layer_combo("cap", "", parent)
    populate_layer_combo(combo, [])
    assert combo.count() == 1
    assert combo.currentText() == LAYER_SENTINEL


# ── param widget dispatch ─────────────────────────────────────


def test_build_int_param_widget(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    decl = IntParam(default=5, min=0, max=99, desc="buffer size")
    widget, getter, setter = build_param_widget(decl, parent)
    assert isinstance(widget, QSpinBox)
    assert widget.minimum() == 0
    assert widget.maximum() == 99
    assert getter() == 5
    setter(7)
    assert getter() == 7
    assert widget.toolTip() == "buffer size"


def test_build_float_param_widget(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    decl = FloatParam(default=2.5, min=0.0, max=10.0)
    widget, getter, setter = build_param_widget(decl, parent)
    assert isinstance(widget, QDoubleSpinBox)
    assert getter() == 2.5
    setter(3.25)
    assert getter() == 3.25


def test_build_bool_param_widget(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    decl = BoolParam(default=False, requires=("cp_mask",))
    widget, getter, setter = build_param_widget(decl, parent)
    assert isinstance(widget, QCheckBox)
    assert getter() is False
    setter(True)
    assert getter() is True


def test_build_choice_param_widget(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    decl = ChoiceParam(choices=("a", "b", "c"), default="b")
    widget, getter, setter = build_param_widget(decl, parent)
    assert isinstance(widget, QComboBox)
    assert getter() == "b"
    setter("c")
    assert getter() == "c"


def test_build_param_widget_unknown_type_raises(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)

    class FakeDecl:
        pass

    try:
        build_param_widget(FakeDecl(), parent)
    except ValueError as exc:
        assert "unsupported" in str(exc).lower()
    else:
        raise AssertionError("Expected ValueError for unknown declaration")


def test_param_setter_does_not_fire_user_signal(qtbot):
    """Programmatic setter goes through ``blockSignals`` — pinned by the
    qt-wire-user-edit-signals learning."""
    parent = QWidget()
    qtbot.addWidget(parent)
    decl = IntParam(default=1)
    widget, _getter, setter = build_param_widget(decl, parent)
    fired = []
    widget.valueChanged.connect(lambda v: fired.append(v))
    setter(42)
    assert fired == []  # setter blocks signals
    # But a USER edit fires.
    qtbot.keyClicks(widget, "")  # focus
    widget.stepUp()
    assert fired == [43]


# ── preset combo ──────────────────────────────────────────────


def test_build_preset_combo_includes_no_preset_sentinel(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    combo = build_preset_combo(
        {"foo": {"x": 1}, "bar": {"y": 2}}, parent, no_preset_label="No preset"
    )
    assert combo.count() == 3
    assert combo.itemText(0) == "No preset"
    # Presets are alphabetized.
    assert combo.itemText(1) == "bar"
    assert combo.itemText(2) == "foo"


def test_preset_combo_tooltip_shows_values(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    combo = build_preset_combo({"p1": {"buffer": 5, "donut": 5}}, parent)
    tooltip = combo.itemData(1, Qt.ToolTipRole)
    assert "buffer: 5" in tooltip
    assert "donut: 5" in tooltip
