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
import pytest
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
    dlg._grid_rows_spin.setValue(dlg._grid_rows_spin.value() + 1)
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
