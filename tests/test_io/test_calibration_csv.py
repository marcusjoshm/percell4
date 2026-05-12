"""Tests for the long-format FLIM calibration CSV parser (U1 of batch TCSPC append)."""

from __future__ import annotations

from pathlib import Path

import pytest

from percell4.domain.errors import CalibrationCSVError
from percell4.domain.io.calibration_csv import (
    BatchCalibration,
    ChannelCalibration,
    parse_calibration_csv,
    validate_frequency_consistency,
)


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "cal.csv"
    p.write_text(content)
    return p


def test_happy_path_three_datasets_three_channels(tmp_path: Path) -> None:
    """The 9-row example from the requirements doc parses into the expected mapping."""
    csv_text = (
        "dataset,channel,frequency_mhz,phase,modulation\n"
        "Dish 1 - WT 60min As + Noco,ch1,80.0,0.12,0.98\n"
        "Dish 1 - WT 60min As + Noco,ch2,80.0,0.10,0.99\n"
        "Dish 1 - WT 60min As + Noco,ch3,80.0,0.11,0.97\n"
        "Dish 2 - TAOK2 KO 60min As + Noco,ch1,80.0,0.13,0.97\n"
        "Dish 2 - TAOK2 KO 60min As + Noco,ch2,80.0,0.11,0.98\n"
        "Dish 2 - TAOK2 KO 60min As + Noco,ch3,80.0,0.10,0.96\n"
        "Dish 3 - RTN4 KO 60min As + Noco,ch1,80.0,0.14,0.96\n"
        "Dish 3 - RTN4 KO 60min As + Noco,ch2,80.0,0.12,0.97\n"
        "Dish 3 - RTN4 KO 60min As + Noco,ch3,80.0,0.13,0.95\n"
    )
    cal = parse_calibration_csv(_write(tmp_path, csv_text))

    assert isinstance(cal, BatchCalibration)
    assert set(cal.datasets()) == {
        "Dish 1 - WT 60min As + Noco",
        "Dish 2 - TAOK2 KO 60min As + Noco",
        "Dish 3 - RTN4 KO 60min As + Noco",
    }
    assert cal.channels("Dish 1 - WT 60min As + Noco") == ("ch1", "ch2", "ch3")

    entry = cal.get("Dish 2 - TAOK2 KO 60min As + Noco", "ch1")
    assert entry == ChannelCalibration(frequency_mhz=80.0, phase=0.13, modulation=0.97)


def test_extra_columns_are_ignored(tmp_path: Path) -> None:
    """Columns outside REQUIRED_COLUMNS are silently dropped."""
    csv_text = (
        "dataset,channel,frequency_mhz,phase,modulation,notes,experimenter\n"
        "Dish A,ch1,80.0,0.10,0.95,calibrated against rhodamine,Josh\n"
    )
    cal = parse_calibration_csv(_write(tmp_path, csv_text))
    assert cal.get("Dish A", "ch1") == ChannelCalibration(80.0, 0.10, 0.95)


def test_empty_after_header_returns_empty_batch(tmp_path: Path) -> None:
    """A header-only file is valid; downstream validation owns the 'missing rows' check."""
    csv_text = "dataset,channel,frequency_mhz,phase,modulation\n"
    cal = parse_calibration_csv(_write(tmp_path, csv_text))
    assert cal.datasets() == ()


def test_duplicate_dataset_channel_pair_errors(tmp_path: Path) -> None:
    """Two rows for the same (dataset, channel) — even with identical values — fail."""
    csv_text = (
        "dataset,channel,frequency_mhz,phase,modulation\n"
        "Dish A,ch1,80.0,0.10,0.95\n"
        "Dish A,ch1,80.0,0.10,0.95\n"
    )
    with pytest.raises(CalibrationCSVError) as exc:
        parse_calibration_csv(_write(tmp_path, csv_text))
    assert any("duplicate" in e and "ch1" in e for e in exc.value.errors)
    assert any("row 3" in e for e in exc.value.errors)


def test_quoted_dataset_with_commas_roundtrips(tmp_path: Path) -> None:
    """Standard CSV quoting handles dataset names containing commas."""
    csv_text = (
        "dataset,channel,frequency_mhz,phase,modulation\n"
        '"Dish A, replicate 1",ch1,80.0,0.10,0.95\n'
    )
    cal = parse_calibration_csv(_write(tmp_path, csv_text))
    assert cal.get("Dish A, replicate 1", "ch1") is not None


def test_missing_required_column_raises_with_name(tmp_path: Path) -> None:
    """Missing 'channel' fails fast — global, not per-row."""
    csv_text = (
        "dataset,frequency_mhz,phase,modulation\n"
        "Dish A,80.0,0.10,0.95\n"
    )
    with pytest.raises(CalibrationCSVError) as exc:
        parse_calibration_csv(_write(tmp_path, csv_text))
    assert any("missing required column" in e and "channel" in e for e in exc.value.errors)


def test_non_numeric_phase_errors_with_row_and_value(tmp_path: Path) -> None:
    """A non-numeric phase reports row number, column name, and the offending value."""
    csv_text = (
        "dataset,channel,frequency_mhz,phase,modulation\n"
        "Dish A,ch1,80.0,not-a-number,0.95\n"
    )
    with pytest.raises(CalibrationCSVError) as exc:
        parse_calibration_csv(_write(tmp_path, csv_text))
    msg = next(e for e in exc.value.errors if "phase" in e)
    assert "row 2" in msg
    assert "not-a-number" in msg


def test_aggregates_multiple_row_errors_into_one_exception(tmp_path: Path) -> None:
    """Parser doesn't bail on the first bad row — all errors aggregate."""
    csv_text = (
        "dataset,channel,frequency_mhz,phase,modulation\n"
        "Dish A,ch1,nope,0.10,0.95\n"
        "Dish B,ch1,80.0,also-bad,0.95\n"
        "Dish C,ch1,80.0,0.10,still-bad\n"
    )
    with pytest.raises(CalibrationCSVError) as exc:
        parse_calibration_csv(_write(tmp_path, csv_text))
    assert len(exc.value.errors) == 3


def test_empty_dataset_or_channel_cell_errors(tmp_path: Path) -> None:
    """Blank dataset or channel cells are caught early with a row-numbered message."""
    csv_text = (
        "dataset,channel,frequency_mhz,phase,modulation\n"
        ",ch1,80.0,0.10,0.95\n"
        "Dish A,,80.0,0.10,0.95\n"
    )
    with pytest.raises(CalibrationCSVError) as exc:
        parse_calibration_csv(_write(tmp_path, csv_text))
    errors = exc.value.errors
    assert any("row 2" in e and "dataset" in e for e in errors)
    assert any("row 3" in e and "channel" in e for e in errors)


# ── validate_frequency_consistency ───────────────────────────────────────


def test_frequency_consistency_passes_when_uniform_within_dataset(tmp_path: Path) -> None:
    csv_text = (
        "dataset,channel,frequency_mhz,phase,modulation\n"
        "Dish A,ch1,80.0,0.10,0.95\n"
        "Dish A,ch2,80.0,0.12,0.96\n"
        "Dish B,ch1,40.0,0.11,0.97\n"  # different dataset, allowed to differ
    )
    cal = parse_calibration_csv(_write(tmp_path, csv_text))
    assert validate_frequency_consistency(cal) == []


def test_frequency_consistency_flags_mixed_frequency_in_one_dataset(tmp_path: Path) -> None:
    csv_text = (
        "dataset,channel,frequency_mhz,phase,modulation\n"
        "Dish A,ch1,80.0,0.10,0.95\n"
        "Dish A,ch2,40.0,0.12,0.96\n"
    )
    cal = parse_calibration_csv(_write(tmp_path, csv_text))
    errors = validate_frequency_consistency(cal)
    assert len(errors) == 1
    assert "Dish A" in errors[0]
    assert "80.0" in errors[0] and "40.0" in errors[0]


def test_batch_calibration_is_immutable() -> None:
    """The nested mapping view rejects in-place mutation."""
    cal = BatchCalibration()
    with pytest.raises(TypeError):
        cal.rows["foo"] = {}  # type: ignore[index]
