"""Tests for the widgets extracted out of ``StitchingFlimForm`` (U1).

``RotateFlipForm`` and ``FlimBinParamsForm`` carry the decay-orientation and
raw-``.bin`` geometry controls. The item lists and ``itemData`` carriers pinned
here are the canonical ones every TCSPC surface must show — the same values
``tests/test_gui/test_batch_tcspc_dialog.py`` pins through the composite.
"""

from __future__ import annotations

from percell4.domain.io.models import FlimConfig

# ── FlimBinParamsForm ────────────────────────────────────────────────


def test_flim_config_defaults_when_group_unchecked(qtbot) -> None:
    """Unchecked → bare FlimConfig, so add_decay_to_dataset falls back to its
    built-in geometry (matches the single-dataset flow)."""
    from percell4.gui._flim_bin_form import FlimBinParamsForm

    form = FlimBinParamsForm()
    qtbot.addWidget(form)
    assert form.flim_group.isChecked() is False

    cfg = form.flim_config(frequency_mhz=80.0)
    assert cfg == FlimConfig(frequency_mhz=80.0)
    # Explicitly: the widget's own 512/512/132 display values must NOT leak
    # through when the group is unchecked.
    assert cfg.bin_x == 0
    assert cfg.bin_y == 0
    assert cfg.bin_t == 0


def test_flim_config_round_trips_all_six_fields_when_checked(qtbot) -> None:
    from percell4.gui._flim_bin_form import FlimBinParamsForm

    form = FlimBinParamsForm()
    qtbot.addWidget(form)

    form.flim_group.setChecked(True)
    form.bin_x.setValue(256)
    form.bin_y.setValue(256)
    form.bin_t.setValue(64)
    form.bin_dtype.setCurrentIndex(form.bin_dtype.findText("float32"))
    form.bin_dim_order.setCurrentIndex(form.bin_dim_order.findText("XYT"))
    form.bin_header.setValue(128)

    cfg = form.flim_config(frequency_mhz=40.0)
    assert cfg.frequency_mhz == 40.0
    assert cfg.bin_x == 256
    assert cfg.bin_y == 256
    assert cfg.bin_t == 64
    assert cfg.bin_dtype == "float32"
    assert cfg.bin_dim_order == "XYT"
    assert cfg.bin_header_bytes == 128


def test_bin_dtype_lists_uint32_first(qtbot) -> None:
    """uint32 must be item 0 — defaulting to uint16 is a documented PR #9
    drift bug that silently misreads uint32 .bin exports."""
    from percell4.gui._flim_bin_form import FlimBinParamsForm

    form = FlimBinParamsForm()
    qtbot.addWidget(form)
    items = [form.bin_dtype.itemText(i) for i in range(form.bin_dtype.count())]
    assert items == ["uint32", "uint16", "float32", "uint8"]
    assert form.bin_dtype.currentText() == "uint32"


def test_bin_header_zero_shows_auto_detect(qtbot) -> None:
    from percell4.gui._flim_bin_form import FlimBinParamsForm

    form = FlimBinParamsForm()
    qtbot.addWidget(form)
    assert form.bin_header.value() == 0
    assert form.bin_header.specialValueText() == "Auto-detect"


# ── RotateFlipForm ───────────────────────────────────────────────────


def test_rotation_k_reads_item_data_not_index(qtbot) -> None:
    from percell4.gui._flim_bin_form import RotateFlipForm

    form = RotateFlipForm()
    qtbot.addWidget(form)
    items = [
        (form.rotation_combo.itemText(i), form.rotation_combo.itemData(i))
        for i in range(form.rotation_combo.count())
    ]
    assert items == [("None", 0), ("90° CCW", 1), ("180°", 2), ("90° CW", 3)]

    for idx, expected in enumerate((0, 1, 2, 3)):
        form.rotation_combo.setCurrentIndex(idx)
        assert form.rotation_k() == expected


def test_flip_axis_maps_user_data_including_none_sentinel(qtbot) -> None:
    """``-1`` itemData is the no-flip sentinel and must surface as ``None``,
    not as the integer -1 (which numpy would read as the last axis)."""
    from percell4.gui._flim_bin_form import RotateFlipForm

    form = RotateFlipForm()
    qtbot.addWidget(form)
    items = [
        (form.flip_combo.itemText(i), form.flip_combo.itemData(i))
        for i in range(form.flip_combo.count())
    ]
    assert items == [
        ("None", -1),
        ("Vertical (top ↔ bottom)", 0),
        ("Horizontal (left ↔ right)", 1),
    ]

    form.flip_combo.setCurrentIndex(0)
    assert form.flip_axis() is None
    form.flip_combo.setCurrentIndex(1)
    assert form.flip_axis() == 0
    form.flip_combo.setCurrentIndex(2)
    assert form.flip_axis() == 1


# ── changed signal ───────────────────────────────────────────────────


def test_each_widget_emits_changed_exactly_once_per_edit(qtbot) -> None:
    """Zero emissions would leave BatchTCSPCDialog's Run button enabled
    against a stale config; two would signal a double-wire."""
    from percell4.gui._flim_bin_form import FlimBinParamsForm, RotateFlipForm

    rotate = RotateFlipForm()
    flim = FlimBinParamsForm()
    qtbot.addWidget(rotate)
    qtbot.addWidget(flim)

    counter = {"n": 0}
    bump = lambda: counter.__setitem__("n", counter["n"] + 1)  # noqa: E731
    rotate.changed.connect(bump)
    flim.changed.connect(bump)

    for action in (
        lambda: rotate.rotation_combo.setCurrentIndex(2),
        lambda: rotate.flip_combo.setCurrentIndex(1),
        lambda: flim.flim_group.setChecked(True),
        lambda: flim.bin_t.setValue(64),
        lambda: flim.bin_dtype.setCurrentIndex(1),
    ):
        counter["n"] = 0
        action()
        assert counter["n"] == 1
