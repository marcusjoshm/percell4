"""Parser for long-format FLIM calibration CSVs used by the batch TCSPC append flow.

Schema (required columns; extra columns silently ignored):

    dataset,channel,frequency_mhz,phase,modulation

One row per (dataset, channel). Returns a frozen ``BatchCalibration`` keyed
by ``dataset_stem -> channel_name -> ChannelCalibration``.

Per-row errors accumulate into a single ``CalibrationCSVError`` raised after
the entire file is read, so the caller sees every problem at once rather than
bailing on the first. Numeric fields are parsed strictly — no auto-coercion.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from percell4.domain.errors import CalibrationCSVError

REQUIRED_COLUMNS: tuple[str, ...] = (
    "dataset",
    "channel",
    "frequency_mhz",
    "phase",
    "modulation",
)


@dataclass(frozen=True)
class ChannelCalibration:
    """Per-channel FLIM calibration from one CSV row."""

    frequency_mhz: float
    phase: float
    modulation: float


@dataclass(frozen=True)
class BatchCalibration:
    """Long-format CSV calibration, indexed by dataset stem then channel name.

    ``rows`` is exposed as a read-only mapping view; mutate by building a new
    ``BatchCalibration`` rather than editing in place. ``get`` and the helper
    accessors are the common read paths.
    """

    rows: Mapping[str, Mapping[str, ChannelCalibration]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def datasets(self) -> tuple[str, ...]:
        return tuple(self.rows.keys())

    def channels(self, dataset: str) -> tuple[str, ...]:
        return tuple(self.rows.get(dataset, {}).keys())

    def get(self, dataset: str, channel: str) -> ChannelCalibration | None:
        return self.rows.get(dataset, {}).get(channel)


def parse_calibration_csv(path: Path | str) -> BatchCalibration:
    """Parse the calibration CSV at ``path``.

    Required columns are listed in ``REQUIRED_COLUMNS``. Extra columns are
    ignored. Every malformed row contributes one entry to the aggregated
    error list; the function raises ``CalibrationCSVError`` once at the end
    if any errors accumulated.
    """
    path = Path(path)
    errors: list[str] = []
    rows: dict[str, dict[str, ChannelCalibration]] = {}

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise CalibrationCSVError(["empty CSV — no header row"])
        missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise CalibrationCSVError(
                [f"missing required column(s): {', '.join(missing)}"]
            )

        # csv.DictReader yields rows starting at file line 2 (after the header).
        for line_no, row in enumerate(reader, start=2):
            dataset = (row.get("dataset") or "").strip()
            channel = (row.get("channel") or "").strip()
            if not dataset:
                errors.append(f"row {line_no}: 'dataset' is empty")
                continue
            if not channel:
                errors.append(f"row {line_no}: 'channel' is empty")
                continue

            parsed = _parse_numeric_fields(row, line_no, errors)
            if parsed is None:
                continue
            frequency_mhz, phase, modulation = parsed

            if dataset in rows and channel in rows[dataset]:
                errors.append(
                    f"row {line_no}: duplicate (dataset={dataset!r}, "
                    f"channel={channel!r})"
                )
                continue

            rows.setdefault(dataset, {})[channel] = ChannelCalibration(
                frequency_mhz=frequency_mhz,
                phase=phase,
                modulation=modulation,
            )

    if errors:
        raise CalibrationCSVError(errors)

    # Freeze the nested dicts behind MappingProxyType so callers can't
    # accidentally mutate parsed state.
    frozen: dict[str, Mapping[str, ChannelCalibration]] = {
        ds: MappingProxyType(dict(channels)) for ds, channels in rows.items()
    }
    return BatchCalibration(rows=MappingProxyType(frozen))


def validate_frequency_consistency(cal: BatchCalibration) -> list[str]:
    """Return per-dataset error messages for inconsistent ``frequency_mhz``.

    ``frequency_mhz`` is allowed to vary across datasets (different microscope
    sessions). Within a single dataset, it must be constant across channels —
    ``/metadata.attrs.flim_frequency_mhz`` is a single scalar per ``.h5``.
    """
    errors: list[str] = []
    for dataset, channels in cal.rows.items():
        freqs = {ch: c.frequency_mhz for ch, c in channels.items()}
        if len(set(freqs.values())) > 1:
            freq_str = ", ".join(f"{ch}={f}" for ch, f in sorted(freqs.items()))
            errors.append(
                f"dataset {dataset!r}: frequency_mhz differs across channels "
                f"({freq_str}); expected a single value per dataset"
            )
    return errors


def _parse_numeric_fields(
    row: dict[str, str | None], line_no: int, errors: list[str]
) -> tuple[float, float, float] | None:
    """Parse the three numeric columns; append to ``errors`` and return None on failure."""
    try:
        frequency_mhz = float(row["frequency_mhz"])
    except (ValueError, TypeError, KeyError):
        errors.append(
            f"row {line_no}: 'frequency_mhz' is not a number "
            f"(got {row.get('frequency_mhz')!r})"
        )
        return None
    try:
        phase = float(row["phase"])
    except (ValueError, TypeError, KeyError):
        errors.append(
            f"row {line_no}: 'phase' is not a number (got {row.get('phase')!r})"
        )
        return None
    try:
        modulation = float(row["modulation"])
    except (ValueError, TypeError, KeyError):
        errors.append(
            f"row {line_no}: 'modulation' is not a number "
            f"(got {row.get('modulation')!r})"
        )
        return None
    return frequency_mhz, phase, modulation
