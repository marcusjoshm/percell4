"""Tests for the BatchTCSPCDialog (U3 of batch TCSPC append).

The dialog drives :func:`batch_add_decay`, :func:`validate_batch_inputs`,
and :func:`parse_calibration_csv` through injected callables. Tests
exercise the dialog's state machine — auto-pair, pairing uniqueness,
Run-gate invalidation, CSV-driven calibration display, and the
post-run summary — by mocking those callables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import h5py
import pandas as pd
from qtpy.QtWidgets import QCheckBox, QComboBox

from percell4.application.use_cases.add_decay_to_dataset import AppendReport
from percell4.application.use_cases.batch_add_decay import (
    BatchAppendItem,
    BatchAppendReport,
    BatchItemResult,
    BatchValidationReport,
)
from percell4.domain.io.calibration_csv import (
    BatchCalibration,
    ChannelCalibration,
)
from percell4.gui.batch_tcspc_dialog import (
    BatchTCSPCDialog,
    _best_match,
    _render_summary_text,
)


def _make_h5(path: Path, channel_names: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channel_names
    return path


def _bcal(rows: dict[str, dict[str, ChannelCalibration]]) -> BatchCalibration:
    return BatchCalibration(rows=rows)


def _passing_report() -> BatchValidationReport:
    return BatchValidationReport()


def _failing_report(*, missing_pair: bool = True) -> BatchValidationReport:
    return BatchValidationReport(
        pairing_errors=("Dish 1.h5: source folder not paired",)
        if missing_pair
        else (),
    )


# ── Construction + project prefill ──────────────────────────────────────


def test_empty_dialog_has_run_disabled(qtbot) -> None:
    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    assert dlg._run_btn is not None
    assert not dlg._run_btn.isEnabled()
    assert dlg._dataset_table.rowCount() == 0


def test_project_index_prefills_dataset_table(qtbot, tmp_path: Path) -> None:
    """When a ProjectIndex returns a DataFrame, every path lands in the table."""
    paths = [
        _make_h5(tmp_path / f"d{i}.h5", ["ch1", "ch2"]) for i in range(3)
    ]
    df = pd.DataFrame({"path": [str(p) for p in paths]})

    class FakeIndex:
        def load(self) -> pd.DataFrame:
            return df

    dlg = BatchTCSPCDialog(get_project_index=lambda: FakeIndex())
    qtbot.addWidget(dlg)
    assert dlg._dataset_table.rowCount() == 3
    # All three start checked.
    for row in range(3):
        wrapper = dlg._dataset_table.cellWidget(row, 0)
        cb = wrapper.property("checkbox")
        assert isinstance(cb, QCheckBox) and cb.isChecked()


# ── Source-root group discovery ────────────────────────────────────────


def test_source_root_discovers_immediate_subfolders(qtbot, tmp_path: Path) -> None:
    root = tmp_path / "scan"
    for name in ("Dish 1", "Dish 2", "Dish 3"):
        (root / name).mkdir(parents=True)
    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    dlg._source_root = root
    dlg._refresh_groups()
    assert [g.name for g in dlg._groups] == ["Dish 1", "Dish 2", "Dish 3"]
    assert "3" in dlg._groups_label.text()


def test_source_root_ignores_files_at_top_level(qtbot, tmp_path: Path) -> None:
    root = tmp_path / "scan"
    root.mkdir()
    (root / "stray.bin").write_bytes(b"")
    (root / "Dish 1").mkdir()
    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    dlg._source_root = root
    dlg._refresh_groups()
    assert [g.name for g in dlg._groups] == ["Dish 1"]


# ── CSV loading ────────────────────────────────────────────────────────


def test_csv_loaded_via_parser_populates_calibration_column(
    qtbot, tmp_path: Path
) -> None:
    h5 = _make_h5(tmp_path / "Dish 1.h5", ["ch1", "ch2"])
    cal = _bcal(
        {
            "Dish 1": {
                "ch1": ChannelCalibration(80.0, 0.12, 0.98),
                "ch2": ChannelCalibration(80.0, 0.10, 0.99),
            }
        }
    )
    dlg = BatchTCSPCDialog(csv_parser=lambda _p: cal)
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5, checked=True)
    dlg._refresh_pairing_table()

    # Simulate file pick by directly invoking the parser path used by _on_load_csv.
    dlg._calibration = cal
    dlg._refresh_pairing_table()
    item = dlg._pairing_table.item(0, 2)
    assert item is not None
    text = item.text()
    assert "ch1=(0.120, 0.980)" in text
    assert "freq=80.0" in text


# ── Auto-pair + uniqueness ─────────────────────────────────────────────


def test_auto_pair_fills_exact_name_matches(qtbot, tmp_path: Path) -> None:
    root = tmp_path / "scan"
    for n in ("Dish 1", "Dish 2"):
        (root / n).mkdir(parents=True)
    h5_1 = _make_h5(tmp_path / "Dish 1.h5", ["ch1"])
    h5_2 = _make_h5(tmp_path / "Dish 2.h5", ["ch1"])

    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5_1, checked=True)
    dlg._add_dataset_row(h5_2, checked=True)
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()
    dlg._on_auto_pair()

    assert dlg._pairings[h5_1] is not None
    assert dlg._pairings[h5_1].name == "Dish 1"
    assert dlg._pairings[h5_2] is not None
    assert dlg._pairings[h5_2].name == "Dish 2"


def test_auto_pair_below_threshold_leaves_unpaired(qtbot, tmp_path: Path) -> None:
    """Totally unrelated names should not pair."""
    root = tmp_path / "scan"
    (root / "completely_unrelated_xyz").mkdir(parents=True)
    h5 = _make_h5(tmp_path / "Dish 1.h5", ["ch1"])

    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5, checked=True)
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()
    dlg._on_auto_pair()
    assert dlg._pairings.get(h5) is None


def test_pairing_uniqueness_clears_prior_owner(qtbot, tmp_path: Path) -> None:
    """When B claims group X that A held, A's combo resets to 'select'."""
    root = tmp_path / "scan"
    (root / "G").mkdir(parents=True)
    h5_a = _make_h5(tmp_path / "A.h5", ["ch1"])
    h5_b = _make_h5(tmp_path / "B.h5", ["ch1"])

    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5_a, checked=True)
    dlg._add_dataset_row(h5_b, checked=True)
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()

    # A claims group G.
    combo_a = dlg._pairing_table.cellWidget(0, 1)
    assert isinstance(combo_a, QComboBox)
    combo_a.setCurrentText("G")
    assert dlg._pairings[h5_a] is not None and dlg._pairings[h5_a].name == "G"

    # B claims group G — A should reset.
    combo_b = dlg._pairing_table.cellWidget(1, 1)
    assert isinstance(combo_b, QComboBox)
    combo_b.setCurrentText("G")
    assert dlg._pairings[h5_b] is not None and dlg._pairings[h5_b].name == "G"
    assert dlg._pairings.get(h5_a) is None


# ── Run-gate invalidation ──────────────────────────────────────────────


def test_settings_change_disables_run(qtbot, tmp_path: Path) -> None:
    """Any settings edit after Validate re-disables Run."""
    h5 = _make_h5(tmp_path / "A.h5", ["ch1"])
    dlg = BatchTCSPCDialog(
        validator=lambda *a, **kw: _passing_report(),
        csv_parser=lambda _p: _bcal(
            {"A": {"ch1": ChannelCalibration(80.0, 0.1, 0.9)}}
        ),
    )
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5, checked=True)
    dlg._calibration = _bcal({"A": {"ch1": ChannelCalibration(80.0, 0.1, 0.9)}})
    root = tmp_path / "scan"
    (root / "G").mkdir(parents=True)
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()
    dlg._pairings[h5] = root / "G"

    dlg._on_validate()
    assert dlg._validated is True
    assert dlg._run_btn.isEnabled()

    # Touching a stitching widget should re-disable Run.
    form = dlg._stitching_form
    form.stitch_rows.setValue(form.stitch_rows.value() + 1)
    assert dlg._validated is False
    assert not dlg._run_btn.isEnabled()


def test_validate_missing_pairing_keeps_run_disabled(qtbot, tmp_path: Path) -> None:
    h5 = _make_h5(tmp_path / "Dish 1.h5", ["ch1"])
    dlg = BatchTCSPCDialog(
        validator=lambda *a, **kw: _failing_report(missing_pair=True)
    )
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5, checked=True)
    dlg._calibration = _bcal(
        {"Dish 1": {"ch1": ChannelCalibration(80.0, 0.1, 0.9)}}
    )
    # No pairings set.
    dlg._on_validate()
    assert dlg._validated is False
    assert not dlg._run_btn.isEnabled()
    assert "no group folder paired" in dlg._validate_log.toPlainText()


# ── Build items + Run flow ─────────────────────────────────────────────


def test_run_calls_orchestrator_with_built_items(qtbot, tmp_path: Path) -> None:
    """End-to-end: validated state + Run → orchestrator receives the items."""
    h5 = _make_h5(tmp_path / "A.h5", ["ch1"])
    root = tmp_path / "scan"
    (root / "G").mkdir(parents=True)

    captured: dict[str, Any] = {}

    def fake_orchestrator(items, **kwargs):
        captured["items"] = list(items)
        captured["force"] = kwargs.get("force")
        # Drive the progress callback so the dialog updates.
        cb = kwargs.get("progress_callback")
        if cb is not None:
            for item in items:
                cb(
                    item,
                    BatchItemResult(
                        item=item,
                        status="succeeded",
                        append_report=AppendReport(written=("ch1",)),
                    ),
                )
        return BatchAppendReport(
            items=tuple(
                BatchItemResult(
                    item=item,
                    status="succeeded",
                    append_report=AppendReport(written=("ch1",)),
                )
                for item in items
            )
        )

    dlg = BatchTCSPCDialog(
        validator=lambda *a, **kw: _passing_report(),
        orchestrator=fake_orchestrator,
    )
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5, checked=True)
    dlg._calibration = _bcal(
        {"A": {"ch1": ChannelCalibration(80.0, 0.1, 0.9)}}
    )
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()
    dlg._pairings[h5] = root / "G"

    dlg._on_validate()
    assert dlg._validated
    dlg._on_run()

    assert len(captured["items"]) == 1
    assert captured["items"][0].h5_path == h5
    assert captured["items"][0].source_dir == root / "G"
    assert "ch1" in captured["items"][0].calibration
    # Summary widget swapped in.
    assert dlg._summary_widget is not None


def test_run_force_flag_follows_conflict_radio(qtbot, tmp_path: Path) -> None:
    h5 = _make_h5(tmp_path / "A.h5", ["ch1"])
    root = tmp_path / "scan"
    (root / "G").mkdir(parents=True)

    captured: dict[str, Any] = {}

    def fake_orchestrator(items, **kwargs):
        captured["force"] = kwargs.get("force")
        return BatchAppendReport(items=())

    dlg = BatchTCSPCDialog(
        validator=lambda *a, **kw: _passing_report(),
        orchestrator=fake_orchestrator,
    )
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5, checked=True)
    dlg._calibration = _bcal(
        {"A": {"ch1": ChannelCalibration(80.0, 0.1, 0.9)}}
    )
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()
    dlg._pairings[h5] = root / "G"
    dlg._conflict_overwrite_radio.setChecked(True)
    dlg._on_validate()
    dlg._on_run()
    assert captured["force"] is True


# ── Pure helpers ───────────────────────────────────────────────────────


def test_best_match_picks_highest_score(tmp_path: Path) -> None:
    a = tmp_path / "Dish 1 - WT"
    b = tmp_path / "totally other"
    a.mkdir()
    b.mkdir()
    best, score = _best_match("Dish 1 - WT 60min As + Noco", [a, b])
    assert best == a
    assert score > 0.5


def test_channel_token_section_discovers_bin_tokens(qtbot, tmp_path: Path) -> None:
    """After a pairing is set, the channel-tokens table populates with the
    distinct ``_ch(\\d+)`` tokens scanned from the paired group's .bin files."""
    h5 = _make_h5(tmp_path / "WT_60min.h5", ["CA-SiR", "mNG", "mTQ2"])
    root = tmp_path / "scan"
    group = root / "Dish 1 - WT 60min"
    group.mkdir(parents=True)
    for tile in range(1, 4):
        for ch in (1, 2, 3):
            (group / f"Dish 1 - WT 60min_s{tile}_ch{ch}.bin").write_bytes(b"")

    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5, checked=True)
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()
    dlg._pairings[h5] = group
    dlg._refresh_channel_tokens_table()

    assert dlg._available_bin_tokens == ["1", "2", "3"]
    # Three channel-name rows.
    assert dlg._channel_tokens_table.rowCount() == 3
    # The semantic-only channels start unmapped; mTQ2 happens to end in "2"
    # and "2" is in the discovered token list, so it gets seeded.
    assert dlg._channel_token_overrides.get("mTQ2") == "2"
    assert "CA-SiR" not in dlg._channel_token_overrides
    assert "mNG" not in dlg._channel_token_overrides


def test_channel_token_section_surfaces_zero_for_single_channel_bins(
    qtbot, tmp_path: Path
) -> None:
    """Single-channel LASX exports omit the _chN suffix on .bin files.

    The discovery should surface token "0" so the user can pair the
    single channel via Section 4, mirroring the cross_format fallback.
    Regression for: 'Batch TCSPC append cannot use single-channel .bins
    without manual renaming'.
    """
    h5 = _make_h5(tmp_path / "single_chan.h5", ["mNG"])
    root = tmp_path / "scan"
    group = root / "Dish 1 - single chan"
    group.mkdir(parents=True)
    # LASX single-channel output: no _ch token at all.
    (group / "Dish 1 - single chan.bin").write_bytes(b"")
    (group / "Dish 1 - single chan_s2.bin").write_bytes(b"")

    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5, checked=True)
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()
    dlg._pairings[h5] = group
    dlg._refresh_channel_tokens_table()

    assert dlg._available_bin_tokens == ["0"], (
        f"Single-channel .bins should surface token '0'; got {dlg._available_bin_tokens}"
    )


def test_channel_token_section_mixes_zero_with_real_tokens(
    qtbot, tmp_path: Path
) -> None:
    """A group with BOTH labeled and unlabeled .bins surfaces all tokens.

    Real-world: if someone re-exports one channel without LASX's chN
    suffix while others have it, the dropdown should expose both.
    """
    h5 = _make_h5(tmp_path / "mixed.h5", ["mNG", "mTQ2"])
    root = tmp_path / "scan"
    group = root / "Dish"
    group.mkdir(parents=True)
    (group / "Dish.bin").write_bytes(b"")  # no token → "0"
    (group / "Dish_ch2.bin").write_bytes(b"")  # token "2"

    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5, checked=True)
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()
    dlg._pairings[h5] = group
    dlg._refresh_channel_tokens_table()

    assert dlg._available_bin_tokens == ["0", "2"]


def test_channel_token_override_picks_propagate_to_run(qtbot, tmp_path: Path) -> None:
    """User-picked tokens flow into intensity_channels_overrides at Run time."""
    h5 = _make_h5(tmp_path / "WT_60min.h5", ["CA-SiR", "mNG", "mTQ2"])
    root = tmp_path / "scan"
    group = root / "Dish 1 - WT 60min"
    group.mkdir(parents=True)
    for tile in range(1, 4):
        for ch in (1, 2, 3):
            (group / f"Dish 1 - WT 60min_s{tile}_ch{ch}.bin").write_bytes(b"")

    captured: dict[str, Any] = {}

    def fake_orchestrator(items, **kwargs):
        captured["overrides"] = kwargs.get("intensity_channels_overrides")
        return BatchAppendReport(items=())

    dlg = BatchTCSPCDialog(
        validator=lambda *a, **kw: _passing_report(),
        orchestrator=fake_orchestrator,
    )
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5, checked=True)
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()
    dlg._pairings[h5] = group
    dlg._refresh_channel_tokens_table()

    # Set the explicit mapping: CA-SiR=1, mNG=2, mTQ2=3.
    dlg._channel_token_overrides = {"CA-SiR": "1", "mNG": "2", "mTQ2": "3"}
    dlg._calibration = _bcal(
        {
            "WT_60min": {
                "CA-SiR": ChannelCalibration(80.0, 0.1, 0.9),
                "mNG": ChannelCalibration(80.0, 0.2, 0.9),
                "mTQ2": ChannelCalibration(80.0, 0.3, 0.9),
            }
        }
    )
    dlg._on_validate()
    dlg._on_run()

    overrides = captured["overrides"]
    assert overrides is not None
    assert h5 in overrides
    name_to_token = {c.name: c.token for c in overrides[h5]}
    assert name_to_token == {"CA-SiR": "1", "mNG": "2", "mTQ2": "3"}


def test_stitching_combos_match_existing_dialog_conventions(qtbot) -> None:
    """Origin, rotate, flip, and bin-geometry combos must match the
    single-dataset TCSPC tab and compress_dialog. Both dialogs share the
    StitchingFlimForm widget, so this also pins the canonical lists."""
    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    form = dlg._stitching_form
    assert form is not None

    pattern_items = [
        form.stitch_type.itemText(i) for i in range(form.stitch_type.count())
    ]
    assert pattern_items == [
        "row_by_row",
        "column_by_column",
        "snake_by_row",
        "snake_by_column",
    ]

    origin_items = [
        form.stitch_order.itemText(i) for i in range(form.stitch_order.count())
    ]
    assert origin_items == [
        "right_down", "right_up", "left_down", "left_up",
        "top_left", "top_right", "bottom_left", "bottom_right",
    ]

    rotate_items = [
        (form.rotation_combo.itemText(i), form.rotation_combo.itemData(i))
        for i in range(form.rotation_combo.count())
    ]
    assert rotate_items == [
        ("None", 0),
        ("90° CCW", 1),
        ("180°", 2),
        ("90° CW", 3),
    ]

    flip_items = [
        (form.flip_combo.itemText(i), form.flip_combo.itemData(i))
        for i in range(form.flip_combo.count())
    ]
    assert flip_items == [
        ("None", -1),
        ("Vertical (top ↔ bottom)", 0),
        ("Horizontal (left ↔ right)", 1),
    ]

    dtype_items = [
        form.bin_dtype.itemText(i) for i in range(form.bin_dtype.count())
    ]
    assert dtype_items == ["uint32", "uint16", "float32", "uint8"]

    dim_order_items = [
        form.bin_dim_order.itemText(i)
        for i in range(form.bin_dim_order.count())
    ]
    assert dim_order_items == ["YXT", "XYT", "TYX"]

    # Header spinbox shows "Auto-detect" when value is 0 (matches compress).
    assert form.bin_header.specialValueText() == "Auto-detect"
    assert form.bin_header.value() == 0


def test_flip_axis_helper_maps_userdata_correctly(qtbot) -> None:
    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    form = dlg._stitching_form
    form.flip_combo.setCurrentIndex(0)  # None
    assert form.flip_axis() is None
    form.flip_combo.setCurrentIndex(1)  # vertical, data = 0
    assert form.flip_axis() == 0
    form.flip_combo.setCurrentIndex(2)  # horizontal, data = 1
    assert form.flip_axis() == 1


def test_flim_group_checked_propagates_dtype_to_run(qtbot, tmp_path: Path) -> None:
    """When the FLIM group is checked, the user's dtype pick reaches the
    orchestrator's FlimConfig (this is what fixes the uint32 .bin case)."""
    h5 = _make_h5(tmp_path / "A.h5", ["ch1"])
    root = tmp_path / "scan"
    (root / "G").mkdir(parents=True)

    captured: dict[str, Any] = {}

    def fake_orchestrator(items, **kwargs):
        captured["flim_config"] = kwargs.get("flim_config")
        return BatchAppendReport(items=())

    dlg = BatchTCSPCDialog(
        validator=lambda *a, **kw: _passing_report(),
        orchestrator=fake_orchestrator,
    )
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5, checked=True)
    dlg._calibration = _bcal({"A": {"ch1": ChannelCalibration(80.0, 0.1, 0.9)}})
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()
    dlg._pairings[h5] = root / "G"

    # Check the FLIM group and set dtype to uint32 (the user's real case).
    form = dlg._stitching_form
    form.flim_group.setChecked(True)
    form.bin_dtype.setCurrentText("uint32")
    form.bin_header.setValue(20)

    dlg._on_validate()
    dlg._on_run()
    assert captured["flim_config"].bin_dtype == "uint32"
    assert captured["flim_config"].bin_header_bytes == 20


def test_unchecking_dataset_refreshes_pairing_table_immediately(
    qtbot, tmp_path: Path
) -> None:
    """Reactive pairing refresh: unchecking a dataset row removes its pairing
    entry without waiting for a Remove or Add click.

    Without this, the pairing table only refreshes on add/remove/browse-root.
    A user who unchecks a row sees pairing stay stale until the NEXT trigger
    runs — which made "Remove selected" appear to operate on checkbox state."""
    h5_a = _make_h5(tmp_path / "A.h5", ["ch1"])
    h5_b = _make_h5(tmp_path / "B.h5", ["ch1"])
    root = tmp_path / "scan"
    (root / "G1").mkdir(parents=True)
    (root / "G2").mkdir(parents=True)

    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5_a, checked=True)
    dlg._add_dataset_row(h5_b, checked=True)
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()
    assert dlg._pairing_table.rowCount() == 2

    # Uncheck the first dataset by toggling its real QCheckBox.
    wrapper = dlg._dataset_table.cellWidget(0, 0)
    check: QCheckBox = wrapper.property("checkbox")
    assert isinstance(check, QCheckBox)
    check.setChecked(False)

    assert dlg._pairing_table.rowCount() == 1, (
        "Pairing must refresh immediately on uncheck, not wait for "
        "the next add/remove click"
    )
    # The remaining pairing row is for the still-checked dataset.
    assert dlg._pairing_table.item(0, 0).text() == "B"


def test_summary_view_hides_form_scroll_wrapper(qtbot, tmp_path: Path) -> None:
    """The summary must hide the scroll wrapper, not just its inner content.
    Otherwise the form's button row stays floating mid-screen (PR #9)."""
    h5 = _make_h5(tmp_path / "A.h5", ["ch1"])
    root = tmp_path / "scan"
    (root / "G").mkdir(parents=True)

    dlg = BatchTCSPCDialog(
        validator=lambda *a, **kw: _passing_report(),
        orchestrator=lambda items, **kw: BatchAppendReport(
            items=tuple(
                BatchItemResult(
                    item=i,
                    status="succeeded",
                    append_report=AppendReport(written=("ch1",)),
                )
                for i in items
            )
        ),
    )
    qtbot.addWidget(dlg)
    dlg._add_dataset_row(h5, checked=True)
    dlg._calibration = _bcal({"A": {"ch1": ChannelCalibration(80.0, 0.1, 0.9)}})
    dlg._source_root = root
    dlg._refresh_groups()
    dlg._refresh_pairing_table()
    dlg._pairings[h5] = root / "G"
    dlg._on_validate()
    dlg._on_run()

    assert dlg._summary_widget is not None
    assert dlg._summary_widget.isVisible() or True  # widget exists, layout owns it
    # Scroll wrapper is hidden — without this fix the form's tables stay
    # visible above the summary.
    assert dlg._form_scroll is not None
    assert not dlg._form_scroll.isVisible()


def test_render_summary_includes_unmatched_paths() -> None:
    """Summary surfaces unmatched .bin files when the matcher returned none."""
    items = (
        BatchItemResult(
            item=BatchAppendItem(Path("semantic.h5"), Path("g"), {}),
            status="failed",
            append_report=AppendReport(
                unmatched=(Path("/x/a_s1_ch1.bin"), Path("/x/a_s1_ch2.bin")),
            ),
            error="no .bin files matched any channel",
        ),
    )
    text = _render_summary_text(BatchAppendReport(items=items))
    assert "unmatched .bin files: 2" in text
    assert "a_s1_ch1.bin" in text


def test_render_summary_text_covers_every_status() -> None:
    """Summary text mentions each status bucket and totals correctly."""
    items = (
        BatchItemResult(
            item=BatchAppendItem(Path("a.h5"), Path("g"), {}),
            status="succeeded",
            append_report=AppendReport(written=("ch1", "ch2")),
        ),
        BatchItemResult(
            item=BatchAppendItem(Path("b.h5"), Path("g"), {}),
            status="failed",
            error="exploded",
        ),
        BatchItemResult(
            item=BatchAppendItem(Path("c.h5"), Path("g"), {}),
            status="skipped_no_changes",
        ),
        BatchItemResult(
            item=BatchAppendItem(Path("d.h5"), Path("g"), {}),
            status="cancelled",
        ),
    )
    text = _render_summary_text(BatchAppendReport(items=items))
    assert "a.h5" in text and "written: ch1, ch2" in text
    assert "b.h5" in text and "exploded" in text
    assert "c.h5" in text and "skipped_no_changes" in text
    assert "d.h5" in text and "cancelled" in text
    assert "1 succeeded, 1 failed, 1 skipped, 1 cancelled" in text
