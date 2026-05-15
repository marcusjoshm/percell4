"""Tests for the batch TCSPC append orchestrator (U2 of batch TCSPC append).

The orchestrator is exercised against real HDF5 files (so the calibration
``/metadata.attrs`` writes are observable) but :func:`add_decay_to_dataset`
itself is monkeypatched — the goal is to verify the orchestration shape,
not re-test the decay write engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import h5py
import pytest

from percell4.application.use_cases import batch_add_decay as bad
from percell4.application.use_cases.add_decay_to_dataset import AppendReport
from percell4.application.use_cases.batch_add_decay import (
    BatchAppendItem,
    BatchAppendReport,
    BatchItemResult,
    batch_add_decay,
    validate_batch_inputs,
    validate_calibration_csv_against_selection,
)
from percell4.domain.io.calibration_csv import (
    BatchCalibration,
    ChannelCalibration,
    parse_calibration_csv,
)
from percell4.domain.io.cross_format import IntensityChannel
from percell4.domain.io.models import (
    BaseStemRule,
    FlimConfig,
    TileConfig,
    TokenConfig,
)


def _make_h5(path: Path, channel_names: list[str]) -> Path:
    """Create a minimal .h5 with /metadata.attrs.channel_names populated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channel_names
    return path


def _cal(freq: float, phase: float, mod: float) -> ChannelCalibration:
    return ChannelCalibration(frequency_mhz=freq, phase=phase, modulation=mod)


def _ok_report(written: tuple[str, ...]) -> AppendReport:
    return AppendReport(written=written)


def _empty_args() -> dict[str, Any]:
    return {
        "token_config": TokenConfig(),
        "tile_config": TileConfig(),
        "flim_config": FlimConfig(),
        "cross_format_rule": BaseStemRule(),
    }


# ── Happy path: distinct calibrations land in distinct /metadata ──────


def test_two_items_with_distinct_calibrations_write_distinct_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards against Bug-3 echo: each item carries its own scope; outputs differ."""
    h5_a = _make_h5(tmp_path / "a.h5", ["ch1", "ch2"])
    h5_b = _make_h5(tmp_path / "b.h5", ["ch1", "ch2"])

    item_a = BatchAppendItem(
        h5_path=h5_a,
        source_dir=tmp_path / "src_a",
        calibration={
            "ch1": _cal(80.0, 0.10, 0.95),
            "ch2": _cal(80.0, 0.20, 0.96),
        },
    )
    item_b = BatchAppendItem(
        h5_path=h5_b,
        source_dir=tmp_path / "src_b",
        calibration={
            "ch1": _cal(80.0, 0.30, 0.85),
            "ch2": _cal(80.0, 0.40, 0.86),
        },
    )

    monkeypatch.setattr(
        bad, "add_decay_to_dataset", lambda **_: _ok_report(("ch1", "ch2"))
    )

    report = batch_add_decay([item_a, item_b], **_empty_args())
    assert all(r.status == "succeeded" for r in report.items)

    with h5py.File(h5_a, "r") as f:
        assert f["metadata"].attrs["flim_cal_phase_ch1"] == pytest.approx(0.10)
        assert f["metadata"].attrs["flim_cal_phase_ch2"] == pytest.approx(0.20)
    with h5py.File(h5_b, "r") as f:
        assert f["metadata"].attrs["flim_cal_phase_ch1"] == pytest.approx(0.30)
        assert f["metadata"].attrs["flim_cal_phase_ch2"] == pytest.approx(0.40)


def test_calibration_is_written_before_decay_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Order is load-bearing: /metadata must land first so a mid-flow failure
    still leaves a consistent calibration record."""
    h5 = _make_h5(tmp_path / "x.h5", ["ch1"])

    call_order: list[str] = []

    def fake_add(**kwargs: Any) -> AppendReport:
        # By the time the decay write runs, the /metadata write must already
        # be on disk — check by reading the attrs back.
        with h5py.File(kwargs["h5_path"], "r") as f:
            assert "flim_cal_phase_ch1" in f["metadata"].attrs
        call_order.append("decay")
        return _ok_report(("ch1",))

    monkeypatch.setattr(bad, "add_decay_to_dataset", fake_add)

    item = BatchAppendItem(
        h5_path=h5,
        source_dir=tmp_path / "src",
        calibration={"ch1": _cal(80.0, 0.11, 0.99)},
    )
    batch_add_decay([item], **_empty_args())
    assert call_order == ["decay"]


def test_semantic_channel_names_succeed_with_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5 fix: explicit IntensityChannel overrides let semantic names match.

    Without the override, the digit-suffix heuristic would yield empty
    tokens for ``mNG``/``mTQ2`` and the matcher would bind nothing. The
    orchestrator must thread the override into ``add_decay_to_dataset``.
    """
    h5 = _make_h5(tmp_path / "semantic.h5", ["mNG", "mTQ2"])

    captured_intensity: list[list[IntensityChannel]] = []

    def fake_add(**kwargs: Any) -> AppendReport:
        captured_intensity.append(list(kwargs["intensity_channels"]))
        return _ok_report(("mNG", "mTQ2"))

    monkeypatch.setattr(bad, "add_decay_to_dataset", fake_add)

    overrides = {
        h5: [
            IntensityChannel(name="mNG", token="1"),
            IntensityChannel(name="mTQ2", token="2"),
        ]
    }
    item = BatchAppendItem(
        h5_path=h5,
        source_dir=tmp_path / "src",
        calibration={
            "mNG": _cal(80.0, 0.10, 0.95),
            "mTQ2": _cal(80.0, 0.12, 0.96),
        },
    )
    report = batch_add_decay(
        [item], **_empty_args(), intensity_channels_overrides=overrides
    )
    assert report.items[0].status == "succeeded"
    # The override list reached add_decay_to_dataset verbatim.
    assert [c.token for c in captured_intensity[0]] == ["1", "2"]


def test_default_intensity_channels_use_digit_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no override is supplied, the orchestrator builds intensity
    channels from store metadata using the same digit-suffix path the
    use case takes internally."""
    h5 = _make_h5(tmp_path / "digits.h5", ["ch01", "ch02"])

    captured: list[list[IntensityChannel]] = []

    def fake_add(**kwargs: Any) -> AppendReport:
        captured.append(list(kwargs["intensity_channels"]))
        return _ok_report(("ch01", "ch02"))

    monkeypatch.setattr(bad, "add_decay_to_dataset", fake_add)

    item = BatchAppendItem(
        h5_path=h5,
        source_dir=tmp_path / "src",
        calibration={
            "ch01": _cal(80.0, 0.10, 0.95),
            "ch02": _cal(80.0, 0.12, 0.96),
        },
    )
    batch_add_decay([item], **_empty_args())
    tokens = [c.token for c in captured[0]]
    assert tokens == ["01", "02"]


# ── Edge cases ────────────────────────────────────────────────────────


def test_empty_items_returns_empty_report() -> None:
    report = batch_add_decay([], **_empty_args())
    assert isinstance(report, BatchAppendReport)
    assert report.items == ()


def test_cancel_after_first_item_marks_rest_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cancel_check fires before item 2; items 2 and 3 must be cancelled."""
    h5_1 = _make_h5(tmp_path / "1.h5", ["ch1"])
    h5_2 = _make_h5(tmp_path / "2.h5", ["ch1"])
    h5_3 = _make_h5(tmp_path / "3.h5", ["ch1"])

    monkeypatch.setattr(bad, "add_decay_to_dataset", lambda **_: _ok_report(("ch1",)))

    seen_items: list[Path] = []

    def cancel_after_first() -> bool:
        # Returns False on first call, True after.
        seen_items.append(Path("tick"))
        return len(seen_items) > 1

    items = [
        BatchAppendItem(p, tmp_path / "src", {"ch1": _cal(80.0, 0.1, 0.9)})
        for p in (h5_1, h5_2, h5_3)
    ]
    report = batch_add_decay(items, **_empty_args(), cancel_check=cancel_after_first)
    statuses = [r.status for r in report.items]
    assert statuses == ["succeeded", "cancelled", "cancelled"]


def test_cancel_before_any_item_marks_all_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cancel_check returning True immediately marks every item cancelled."""
    h5_1 = _make_h5(tmp_path / "1.h5", ["ch1"])
    items = [BatchAppendItem(h5_1, tmp_path / "src", {"ch1": _cal(80.0, 0.1, 0.9)})]
    report = batch_add_decay(items, **_empty_args(), cancel_check=lambda: True)
    assert [r.status for r in report.items] == ["cancelled"]


def test_skipped_no_changes_when_all_channels_already_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force=False + every channel hits 'already exists' → skipped_no_changes."""
    h5 = _make_h5(tmp_path / "exists.h5", ["ch1"])

    monkeypatch.setattr(
        bad,
        "add_decay_to_dataset",
        lambda **_: AppendReport(errors={"ch1": "decay layer already exists for ch1"}),
    )

    item = BatchAppendItem(h5, tmp_path / "src", {"ch1": _cal(80.0, 0.1, 0.9)})
    report = batch_add_decay([item], **_empty_args(), force=False)
    assert report.items[0].status == "skipped_no_changes"
    # Calibration was still written even though no decay landed.
    with h5py.File(h5, "r") as f:
        assert "flim_cal_phase_ch1" in f["metadata"].attrs


def test_force_true_still_classifies_as_succeeded_when_channels_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force=True returns a report.written list — orchestrator surfaces 'succeeded'."""
    h5 = _make_h5(tmp_path / "force.h5", ["ch1"])
    monkeypatch.setattr(bad, "add_decay_to_dataset", lambda **_: _ok_report(("ch1",)))
    item = BatchAppendItem(h5, tmp_path / "src", {"ch1": _cal(80.0, 0.1, 0.9)})
    report = batch_add_decay([item], **_empty_args(), force=True)
    assert report.items[0].status == "succeeded"


# ── Error paths: per-item failure isolation ───────────────────────────


def test_missing_h5_fails_isolated_batch_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 2's .h5 doesn't exist; items 1 and 3 still complete."""
    h5_1 = _make_h5(tmp_path / "1.h5", ["ch1"])
    h5_3 = _make_h5(tmp_path / "3.h5", ["ch1"])
    missing = tmp_path / "does_not_exist.h5"

    monkeypatch.setattr(bad, "add_decay_to_dataset", lambda **_: _ok_report(("ch1",)))

    items = [
        BatchAppendItem(h5_1, tmp_path / "src1", {"ch1": _cal(80.0, 0.1, 0.9)}),
        BatchAppendItem(missing, tmp_path / "src2", {"ch1": _cal(80.0, 0.2, 0.8)}),
        BatchAppendItem(h5_3, tmp_path / "src3", {"ch1": _cal(80.0, 0.3, 0.7)}),
    ]
    report = batch_add_decay(items, **_empty_args())
    statuses = [r.status for r in report.items]
    assert statuses == ["succeeded", "failed", "succeeded"]
    assert report.items[1].error  # populated with the underlying error string


def test_empty_channel_names_in_metadata_classifies_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dataset with no /intensity channels can't be matched — failed, never raises."""
    path = tmp_path / "empty.h5"
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        f.create_group("metadata")  # no channel_names attr

    monkeypatch.setattr(bad, "add_decay_to_dataset", lambda **_: _ok_report(()))
    item = BatchAppendItem(path, tmp_path / "src", {})
    report = batch_add_decay([item], **_empty_args())
    assert report.items[0].status == "failed"
    assert "channel_names" in (report.items[0].error or "")


def test_zero_bindings_surfaces_unmatched_count_not_catchall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the matcher returns zero bindings (semantic channel-name case),
    the orchestrator must surface the unmatched/ambiguous diagnostics
    rather than the cryptic 'wrote no channels and reported no errors'."""
    h5 = _make_h5(tmp_path / "semantic.h5", ["mNG", "mTQ2"])
    unmatched_paths = (
        tmp_path / "src" / "stub_s1_ch1.bin",
        tmp_path / "src" / "stub_s1_ch2.bin",
    )
    monkeypatch.setattr(
        bad,
        "add_decay_to_dataset",
        lambda **_: AppendReport(unmatched=unmatched_paths),
    )
    item = BatchAppendItem(h5, tmp_path / "src", {"mNG": _cal(80.0, 0.1, 0.9)})
    report = batch_add_decay([item], **_empty_args())
    assert report.items[0].status == "failed"
    err = report.items[0].error or ""
    assert "no .bin files matched" in err
    assert "2 .bin unmatched" in err
    assert "wrote no channels and reported no errors" not in err


def test_scan_error_from_use_case_propagates_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When add_decay_to_dataset reports a scan error, we surface 'failed' with the message."""
    h5 = _make_h5(tmp_path / "noscan.h5", ["ch1"])
    monkeypatch.setattr(
        bad,
        "add_decay_to_dataset",
        lambda **_: AppendReport(errors={"scan": "no .bin files found under /missing"}),
    )
    item = BatchAppendItem(h5, tmp_path / "empty_src", {"ch1": _cal(80.0, 0.1, 0.9)})
    report = batch_add_decay([item], **_empty_args())
    assert report.items[0].status == "failed"
    assert "no .bin files" in (report.items[0].error or "")


def test_spatial_bin_kwarg_threads_through_to_use_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batch orchestrator forwards ``spatial_bin`` verbatim into every
    per-item ``add_decay_to_dataset`` call."""
    h5_1 = _make_h5(tmp_path / "1.h5", ["ch1"])
    h5_2 = _make_h5(tmp_path / "2.h5", ["ch1"])

    captured: list[int] = []

    def fake_add(**kwargs: Any) -> AppendReport:
        captured.append(kwargs["spatial_bin"])
        return _ok_report(("ch1",))

    monkeypatch.setattr(bad, "add_decay_to_dataset", fake_add)

    items = [
        BatchAppendItem(p, tmp_path / "src", {"ch1": _cal(80.0, 0.1, 0.9)})
        for p in (h5_1, h5_2)
    ]
    batch_add_decay(items, **_empty_args(), spatial_bin=3)
    assert captured == [3, 3]


def test_spatial_bin_default_is_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting ``spatial_bin`` forwards the no-bin default (1) to the use case."""
    h5 = _make_h5(tmp_path / "x.h5", ["ch1"])

    captured: list[int] = []

    def fake_add(**kwargs: Any) -> AppendReport:
        captured.append(kwargs["spatial_bin"])
        return _ok_report(("ch1",))

    monkeypatch.setattr(bad, "add_decay_to_dataset", fake_add)

    item = BatchAppendItem(h5, tmp_path / "src", {"ch1": _cal(80.0, 0.1, 0.9)})
    batch_add_decay([item], **_empty_args())
    assert captured == [1]


def test_progress_callback_fires_after_every_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller's progress_callback gets one call per item, in order."""
    h5_1 = _make_h5(tmp_path / "1.h5", ["ch1"])
    h5_2 = _make_h5(tmp_path / "2.h5", ["ch1"])
    monkeypatch.setattr(bad, "add_decay_to_dataset", lambda **_: _ok_report(("ch1",)))

    calls: list[tuple[Path, str]] = []
    items = [
        BatchAppendItem(h5_1, tmp_path / "s1", {"ch1": _cal(80.0, 0.1, 0.9)}),
        BatchAppendItem(h5_2, tmp_path / "s2", {"ch1": _cal(80.0, 0.2, 0.8)}),
    ]
    batch_add_decay(
        items,
        **_empty_args(),
        progress_callback=lambda item, result: calls.append((item.h5_path, result.status)),
    )
    assert calls == [(h5_1, "succeeded"), (h5_2, "succeeded")]


# ── validate_batch_inputs ──────────────────────────────────────────────


def test_validate_flags_missing_csv_coverage() -> None:
    item = BatchAppendItem(
        h5_path=Path("/tmp/a.h5"),
        source_dir=Path("/tmp/src_a"),
        calibration={"ch1": _cal(80.0, 0.1, 0.9)},  # ch2 missing
    )
    report = validate_batch_inputs(
        [item],
        channel_names_per_item={Path("/tmp/a.h5"): ["ch1", "ch2"]},
        force=False,
        existing_decay_per_item={},
    )
    assert any("ch2" in e for e in report.csv_coverage_errors)
    assert not report.is_passing


def test_validate_flags_duplicate_source_dirs() -> None:
    item_a = BatchAppendItem(
        h5_path=Path("/tmp/a.h5"),
        source_dir=Path("/tmp/shared"),
        calibration={"ch1": _cal(80.0, 0.1, 0.9)},
    )
    item_b = BatchAppendItem(
        h5_path=Path("/tmp/b.h5"),
        source_dir=Path("/tmp/shared"),  # same!
        calibration={"ch1": _cal(80.0, 0.2, 0.8)},
    )
    report = validate_batch_inputs(
        [item_a, item_b],
        channel_names_per_item={
            Path("/tmp/a.h5"): ["ch1"],
            Path("/tmp/b.h5"): ["ch1"],
        },
        force=False,
        existing_decay_per_item={},
    )
    assert report.source_dir_uniqueness_errors
    assert not report.is_passing


def test_validate_flags_frequency_inconsistency_within_dataset() -> None:
    item = BatchAppendItem(
        h5_path=Path("/tmp/a.h5"),
        source_dir=Path("/tmp/src"),
        calibration={
            "ch1": _cal(80.0, 0.1, 0.9),
            "ch2": _cal(40.0, 0.1, 0.9),
        },
    )
    report = validate_batch_inputs(
        [item],
        channel_names_per_item={Path("/tmp/a.h5"): ["ch1", "ch2"]},
        force=False,
        existing_decay_per_item={},
    )
    assert report.frequency_consistency_errors
    assert not report.is_passing


def test_validate_decay_collision_is_warning_not_error() -> None:
    """Pre-existing /decay under force=False is a warning — Run still allowed."""
    item = BatchAppendItem(
        h5_path=Path("/tmp/a.h5"),
        source_dir=Path("/tmp/src"),
        calibration={"ch1": _cal(80.0, 0.1, 0.9)},
    )
    report = validate_batch_inputs(
        [item],
        channel_names_per_item={Path("/tmp/a.h5"): ["ch1"]},
        force=False,
        existing_decay_per_item={Path("/tmp/a.h5"): {"ch1"}},
    )
    assert report.decay_collision_warnings
    assert report.is_passing  # warnings don't block Run


def test_validate_force_true_silences_decay_collision_warning() -> None:
    item = BatchAppendItem(
        h5_path=Path("/tmp/a.h5"),
        source_dir=Path("/tmp/src"),
        calibration={"ch1": _cal(80.0, 0.1, 0.9)},
    )
    report = validate_batch_inputs(
        [item],
        channel_names_per_item={Path("/tmp/a.h5"): ["ch1"]},
        force=True,
        existing_decay_per_item={Path("/tmp/a.h5"): {"ch1"}},
    )
    assert report.decay_collision_warnings == ()


def test_validate_empty_items_fails() -> None:
    report = validate_batch_inputs(
        [], channel_names_per_item={}, force=False, existing_decay_per_item={}
    )
    assert report.pairing_errors
    assert not report.is_passing


# ── validate_calibration_csv_against_selection ────────────────────────


def test_csv_selection_check_flags_missing_dataset(tmp_path: Path) -> None:
    csv = (tmp_path / "c.csv")
    csv.write_text(
        "dataset,channel,frequency_mhz,phase,modulation\n"
        "Dish A,ch1,80.0,0.1,0.9\n"
    )
    cal = parse_calibration_csv(csv)
    errors = validate_calibration_csv_against_selection(cal, ["Dish A", "Dish B"])
    assert any("Dish B" in e for e in errors)


def test_csv_selection_check_silent_on_extra_csv_rows(tmp_path: Path) -> None:
    """Extra datasets in the CSV (not selected) are silently allowed."""
    csv = (tmp_path / "c.csv")
    csv.write_text(
        "dataset,channel,frequency_mhz,phase,modulation\n"
        "Dish A,ch1,80.0,0.1,0.9\n"
        "Dish UNUSED,ch1,80.0,0.1,0.9\n"
    )
    cal = parse_calibration_csv(csv)
    errors = validate_calibration_csv_against_selection(cal, ["Dish A"])
    assert errors == []
