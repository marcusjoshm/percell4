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


# ── Composite re-emission ────────────────────────────────────────────


def test_extracted_widget_edits_reach_composite_changed_exactly_once(qtbot) -> None:
    """Editing a control that now lives in an extracted widget must still emit
    ``StitchingFlimForm.changed`` — once per edit, never zero, never twice.

    Zero would leave BatchTCSPCDialog's Run button enabled against a stale
    config; twice would be harmless but signals a double-wire.
    """
    from percell4.gui._stitching_flim_form import StitchingFlimForm

    form = StitchingFlimForm()
    qtbot.addWidget(form)

    counter = {"n": 0}
    form.changed.connect(lambda: counter.__setitem__("n", counter["n"] + 1))

    # Rotate/flip (RotateFlipForm)
    counter["n"] = 0
    form.rotation_combo.setCurrentIndex(2)
    assert counter["n"] == 1

    counter["n"] = 0
    form.flip_combo.setCurrentIndex(1)
    assert counter["n"] == 1

    # Raw .bin geometry (FlimBinParamsForm)
    counter["n"] = 0
    form.flim_group.setChecked(True)
    assert counter["n"] == 1

    counter["n"] = 0
    form.bin_t.setValue(64)
    assert counter["n"] == 1

    counter["n"] = 0
    form.bin_dtype.setCurrentIndex(1)
    assert counter["n"] == 1

    # A stitching control still owned by the composite itself.
    counter["n"] = 0
    form.stitch_rows.setValue(3)
    assert counter["n"] == 1


def test_composite_accessors_delegate_to_extracted_widgets(qtbot) -> None:
    """The public surface is unchanged by the split: every accessor callers
    and tests use must still resolve through the composite."""
    from percell4.gui._stitching_flim_form import StitchingFlimForm

    form = StitchingFlimForm()
    qtbot.addWidget(form)

    form.rotation_combo.setCurrentIndex(3)
    form.flip_combo.setCurrentIndex(2)
    assert form.rotation_k() == 3
    assert form.flip_axis() == 1

    form.flim_group.setChecked(True)
    form.bin_x.setValue(128)
    cfg = form.flim_config(frequency_mhz=80.0)
    assert cfg.bin_x == 128

    # Widget identity: the composite's attribute IS the extracted widget's,
    # so external code mutating one is seen by the other.
    assert form.rotation_combo is form._rotate_flip.rotation_combo
    assert form.bin_dtype is form._flim_bin.bin_dtype
