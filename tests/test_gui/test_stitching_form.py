"""Tests for the canonical StitchingForm (U3).

Covers the three things the refactor actually promises: Grid size X/Y map to
the right TileConfig fields, the Order options track the selected Type per
Fiji, and no reachable UI state can produce an invalid TileConfig.
"""

from __future__ import annotations

import pytest

from percell4.domain.io.models import TileConfig
from percell4.gui._stitch_order import GRID_TYPES


def _form(qtbot, **kwargs):
    from percell4.gui._stitching_form import StitchingForm

    form = StitchingForm(**kwargs)
    qtbot.addWidget(form)
    return form


def _labels(combo) -> list[str]:
    return [combo.itemText(i) for i in range(combo.count())]


def _values(combo) -> list:
    return [combo.itemData(i) for i in range(combo.count())]


# ── Grid size X/Y ───────────────────────────────────────────────────


def test_default_tile_config(qtbot) -> None:
    form = _form(qtbot)
    tc = form.tile_config()
    assert tc.grid_rows == 1
    assert tc.grid_cols == 1
    assert tc.grid_type == "row_by_row"
    assert tc.order == "top_left"


def test_grid_x_is_cols_and_y_is_rows(qtbot) -> None:
    """Deliberately non-square: a transposition is invisible on square grids,
    and on the registered path it changes native_shape."""
    form = _form(qtbot)
    form.grid_x.setValue(3)
    form.grid_y.setValue(2)

    tc = form.tile_config()
    assert tc.grid_cols == 3, "Grid size X must be the column count"
    assert tc.grid_rows == 2, "Grid size Y must be the row count"


# ── Type / Order labels ─────────────────────────────────────────────


def test_type_labels_match_fiji(qtbot) -> None:
    form = _form(qtbot)
    assert _labels(form.grid_type) == [
        "Grid: row-by-row",
        "Grid: column-by-column",
        "Grid: snake-by-row",
        "Grid: snake-by-column",
    ]
    assert _values(form.grid_type) == list(GRID_TYPES)


@pytest.mark.parametrize("grid_type", ["row_by_row", "snake_by_row"])
def test_row_types_show_row_order_labels(qtbot, grid_type: str) -> None:
    form = _form(qtbot)
    form.grid_type.setCurrentIndex(form.grid_type.findData(grid_type))
    assert _labels(form.order) == [
        "Right & Down", "Left & Down", "Right & Up", "Left & Up",
    ]
    assert _values(form.order) == [
        "top_left", "top_right", "bottom_left", "bottom_right",
    ]


@pytest.mark.parametrize("grid_type", ["column_by_column", "snake_by_column"])
def test_column_types_show_column_order_labels(qtbot, grid_type: str) -> None:
    form = _form(qtbot)
    form.grid_type.setCurrentIndex(form.grid_type.findData(grid_type))
    assert _labels(form.order) == [
        "Down & Right", "Down & Left", "Up & Right", "Up & Left",
    ]
    assert _values(form.order) == [
        "top_left", "top_right", "bottom_left", "bottom_right",
    ]


def test_order_selection_survives_a_type_switch(qtbot) -> None:
    """Values are Type-independent corners, so switching Type rewords the
    user's pick rather than resetting it."""
    form = _form(qtbot)
    form.grid_type.setCurrentIndex(form.grid_type.findData("row_by_row"))
    form.order.setCurrentIndex(form.order.findData("bottom_right"))
    assert form.order.currentText() == "Left & Up"

    form.grid_type.setCurrentIndex(form.grid_type.findData("column_by_column"))

    assert form.order.currentData() == "bottom_right"
    assert form.order.currentText() == "Up & Left"


def test_every_ui_combination_yields_a_valid_tile_config(qtbot) -> None:
    """No pairing of Type and Order may raise in TileConfig.__post_init__."""
    form = _form(qtbot)
    for t in range(form.grid_type.count()):
        form.grid_type.setCurrentIndex(t)
        for o in range(form.order.count()):
            form.order.setCurrentIndex(o)
            tc = form.tile_config()
            assert isinstance(tc, TileConfig)


# ── changed signal ──────────────────────────────────────────────────


def test_type_change_emits_changed_exactly_once(qtbot) -> None:
    """Repopulating Order must not double-fire. Zero would leave a Run button
    enabled against a stale config; twice signals a double-wire."""
    form = _form(qtbot)
    seen = {"n": 0}
    form.changed.connect(lambda: seen.__setitem__("n", seen["n"] + 1))

    form.grid_type.setCurrentIndex(form.grid_type.findData("column_by_column"))
    assert seen["n"] == 1

    seen["n"] = 0
    form.grid_type.setCurrentIndex(form.grid_type.findData("snake_by_row"))
    assert seen["n"] == 1


def test_every_control_emits_changed_through_its_signal_path(qtbot) -> None:
    form = _form(qtbot, show_registration=True, show_fusion=True)
    seen = {"n": 0}
    form.changed.connect(lambda: seen.__setitem__("n", seen["n"] + 1))

    for action in (
        lambda: form.grid_x.setValue(4),
        lambda: form.grid_y.setValue(5),
        lambda: form.order.setCurrentIndex(2),
        lambda: form.overlap.setValue(12.5),
        lambda: form.register_check.setChecked(True),
        lambda: form.fusion.setCurrentIndex(1),
    ):
        seen["n"] = 0
        action()
        assert seen["n"] >= 1


# ── Capability flags ────────────────────────────────────────────────


def test_registration_hidden_keeps_the_gate_closed(qtbot) -> None:
    """With registration hidden the form must never emit a config that could
    open the register ∧ overlap>0 ∧ grid>1x1 gate."""
    form = _form(qtbot, show_registration=False)
    assert form.register_check.isVisible() is False

    form.register_check.setChecked(True)
    form.overlap.setValue(20.0)
    form.reference.setCurrentText("ch00")

    tc = form.tile_config()
    assert tc.register is False
    assert tc.overlap == 0.0
    assert tc.reference_channel is None


def test_fusion_hidden_yields_none(qtbot) -> None:
    form = _form(qtbot, show_fusion=False)
    assert form.fusion.isVisible() is False
    form.fusion.setCurrentIndex(1)
    assert form.tile_config().fusion_method == "none"


def test_fusion_shown_round_trips(qtbot) -> None:
    form = _form(qtbot, show_fusion=True)
    form.fusion.setCurrentIndex(form.fusion.findData("linear_blending"))
    assert form.tile_config().fusion_method == "linear_blending"


# ── Seeding from a TileConfig ───────────────────────────────────────


@pytest.mark.parametrize(
    ("stored_order", "expected_label"),
    [
        ("right_down", "Right & Down"),
        ("left_down", "Left & Down"),
        ("right_up", "Right & Up"),
        ("left_up", "Left & Up"),
        ("top_left", "Right & Down"),
        ("top_right", "Left & Down"),
        ("bottom_left", "Right & Up"),
        ("bottom_right", "Left & Up"),
    ],
)
def test_set_tile_config_accepts_both_order_vocabularies(
    qtbot, stored_order: str, expected_label: str
) -> None:
    """Persisted state carries either vocabulary; both must land on the right
    item rather than silently leaving the default."""
    form = _form(qtbot)
    form.set_tile_config(
        TileConfig(grid_rows=2, grid_cols=3, grid_type="row_by_row", order=stored_order)
    )
    assert form.order.currentText() == expected_label
    tc = form.tile_config()
    assert tc.grid_rows == 2
    assert tc.grid_cols == 3


def test_set_tile_config_round_trips_registration_fields(qtbot) -> None:
    form = _form(qtbot, show_registration=True, show_fusion=True)
    original = TileConfig(
        grid_rows=2,
        grid_cols=2,
        grid_type="snake_by_column",
        order="bottom_left",
        overlap=0.15,
        register=True,
        reference_channel="ch01",
        fusion_method="linear_blending",
    )
    form.set_tile_config(original)
    result = form.tile_config()

    assert result.grid_type == "snake_by_column"
    assert result.order == "bottom_left"
    assert result.overlap == pytest.approx(0.15)
    assert result.register is True
    assert result.reference_channel == "ch01"
    assert result.fusion_method == "linear_blending"


def test_set_tile_config_tolerates_junk_order(qtbot) -> None:
    form = _form(qtbot)
    before = form.order.currentData()
    # Bypass TileConfig validation to simulate a hand-edited plan file or a
    # future value this build does not know about.
    cfg = TileConfig(grid_rows=2, grid_cols=2)
    object.__setattr__(cfg, "order", "sideways")
    form.set_tile_config(cfg)
    assert form.order.currentData() == before


# ── Reference channel preservation ──────────────────────────────────


def test_reference_preserve_text_follows_the_name(qtbot) -> None:
    form = _form(qtbot)
    form.set_reference_channels(["ch00", "ch01"])
    form.reference.setCurrentText("ch01")
    form.set_reference_channels(["ch00", "ch01", "ch02"])
    assert form.reference.currentText() == "ch01"


def test_reference_preserve_index_survives_a_rename(qtbot) -> None:
    """Renaming ch00 to ER must leave the reference on that same channel, not
    fall back to the first entry."""
    form = _form(qtbot)
    form.set_reference_channels(["ch00", "ch01"])
    form.reference.setCurrentIndex(1)
    form.set_reference_channels(["ch00", "ER"], preserve="index")
    assert form.reference.currentIndex() == 1
    assert form.reference.currentText() == "ER"


def test_reference_rejects_unknown_preserve_mode(qtbot) -> None:
    form = _form(qtbot)
    with pytest.raises(ValueError, match="preserve must be"):
        form.set_reference_channels(["ch00"], preserve="whatever")


# ── Width ───────────────────────────────────────────────────────────


def test_form_fits_a_standard_window_width(qtbot) -> None:
    """The whole point of the layout change. The old single-row layout blew
    past its host dialog and forced a horizontal scrollbar."""
    form = _form(qtbot, show_registration=True, show_fusion=True)
    assert form.sizeHint().width() <= 620, (
        f"stitching form wants {form.sizeHint().width()}px — "
        "it has regressed toward the old wide-row layout"
    )
