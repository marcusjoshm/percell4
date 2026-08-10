"""FLIM phasor calibration extracted from a Leica ``.lif`` header.

LAS X solves the phasor calibration and stores it; this module reads it back
at full precision rather than at the four decimal places the LAS X dialog
displays. Hand-transcribing that display into a calibration CSV is what this
replaces.

**Two records, one of them wrong.** A ``.lif`` header carries phasor
calibration twice under the same element:

- ``PhasorPhase`` / ``PhasorAmplitude``, under the acquisition-time detector
  block. Appears *first* in the document.
- ``AutomaticReferencePhase`` / ``AutomaticReferenceAmplitude``, under the
  phasor-analysis block. This is what the LAS X *Phasor Calibration* dialog
  shows in its Images table and applies to the data.

Only the second is correct. For the reference file they differ by 5.459°,
which is a time-origin shift of two decay bins rather than a competing
calibration. Extraction anchors on ``AutomaticReferencePhase`` and never falls
back to the acquisition record — a header carrying only the acquisition record
raises rather than silently returning the wrong number.

Conversion into the units :mod:`percell4.domain.io.calibration_csv` defines::

    frequency_mhz = 1 / Period / 1e6
    phase         = -radians(AutomaticReferencePhase)
    modulation    = 1 / AutomaticReferenceAmplitude

See ``docs/reference/lif-xml-header.md`` for the header layout.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from percell4.domain.errors import LifCalibrationError
from percell4.domain.io.calibration_csv import (
    BatchCalibration,
    ChannelCalibration,
    validate_frequency_consistency,
)
from percell4.domain.io.lif_header import read_lif_header

# Presence of this child is what marks a ``Channels`` element as the per-image
# calibration block. The reference file has 26 ``Channels`` elements and only
# one of them carries it.
CALIBRATION_MARKER = "AutomaticReferencePhase"


@dataclass(frozen=True)
class LifCalibrationRecord:
    """One channel's phasor calibration, in PerCell4 units.

    ``dataset_stem`` and ``detector_name`` are how this record proposes to
    bind onto a selected ``.h5``; both are best-effort, and the caller is
    expected to let the user correct the binding.
    """

    dataset_stem: str
    region_name: str
    element_path: str
    channel_index: int
    detector_name: str
    frequency_mhz: float
    phase: float
    modulation: float
    harmonic: int

    @property
    def label(self) -> str:
        """Human-readable identity for a picker."""
        return f"{self.region_name} · {self.detector_name}"


def read_lif_calibration(path: Path | str) -> tuple[LifCalibrationRecord, ...]:
    """Return every phasor calibration record in the ``.lif`` at ``path``.

    Raises :class:`LifCalibrationError` when the header parses but carries no
    per-image calibration, or when a record's numeric fields are unusable.
    Field errors accumulate across every record and raise once at the end, so
    a caller sees all of them rather than only the first — the same contract
    :func:`~percell4.domain.io.calibration_csv.parse_calibration_csv` offers.

    A malformed container raises
    :class:`~percell4.domain.errors.LifHeaderError` from the reader instead,
    which keeps "not a ``.lif``" distinct from "no calibration in this ``.lif``".
    """
    path = Path(path)
    root = read_lif_header(path)
    parents = {child: parent for parent in root.iter() for child in parent}

    blocks = [c for c in root.iter("Channels") if c.find(CALIBRATION_MARKER) is not None]
    if not blocks:
        raise LifCalibrationError(
            [
                f"{path.name}: no phasor calibration in this .lif — the header "
                f"has no <{CALIBRATION_MARKER}> record. Calibrate the image in "
                "the LAS X Phasor Calibration dialog and re-save."
            ]
        )

    errors: list[str] = []
    records: list[LifCalibrationRecord] = []
    for block in blocks:
        chain = _element_chain(block, parents)
        record = _build_record(block, chain, parents, errors)
        if record is not None:
            records.append(record)

    if errors:
        raise LifCalibrationError([f"{path.name}: {e}" for e in errors])
    return tuple(records)


def _element_chain(node: ET.Element, parents: dict) -> list[str]:
    """Names of the ``Element`` ancestors of ``node``, outermost first."""
    names: list[str] = []
    current = node
    while current is not None:
        if current.tag == "Element" and current.get("Name"):
            names.append(current.get("Name", ""))
        current = parents.get(current)
    return list(reversed(names))


def _owning_element(node: ET.Element, parents: dict) -> ET.Element | None:
    current = parents.get(node)
    while current is not None and current.tag != "Element":
        current = parents.get(current)
    return current


def _build_record(
    block: ET.Element,
    chain: list[str],
    parents: dict,
    errors: list[str],
) -> LifCalibrationRecord | None:
    region = chain[1] if len(chain) > 1 else (chain[0] if chain else "")
    where = region or "unnamed element"

    # Dataset stem is root name + region name, joined the way LAS X names its
    # exports. Best-effort: the caller lets the user rebind when it misses.
    stem = "_".join(chain[:2]) if len(chain) > 1 else (chain[0] if chain else "")

    channel_index = _int(block.findtext("Channel"), default=0)

    period = _float(block.findtext("Period"), f"{where}: 'Period'", errors)
    phase_deg = _float(
        block.findtext(CALIBRATION_MARKER), f"{where}: '{CALIBRATION_MARKER}'", errors
    )
    amplitude = _float(
        block.findtext("AutomaticReferenceAmplitude"),
        f"{where}: 'AutomaticReferenceAmplitude'",
        errors,
    )
    if period is None or phase_deg is None or amplitude is None:
        return None

    if period <= 0:
        errors.append(f"{where}: 'Period' is {period}; cannot derive a frequency")
        return None
    if amplitude == 0:
        errors.append(
            f"{where}: 'AutomaticReferenceAmplitude' is 0; cannot derive a modulation"
        )
        return None

    owner = _owning_element(block, parents)
    return LifCalibrationRecord(
        dataset_stem=stem,
        region_name=region,
        element_path="/".join(chain),
        channel_index=channel_index,
        detector_name=_detector_name(owner, channel_index),
        frequency_mhz=1.0 / period / 1e6,
        phase=-math.radians(phase_deg),
        modulation=1.0 / amplitude,
        harmonic=_harmonic(owner),
    )


def _detector_name(owner: ET.Element | None, channel_index: int) -> str:
    """Detector label from the acquisition block whose index matches.

    Display only — nothing binds on it, so a miss costs a nicer label and
    falls back to the channel index.
    """
    if owner is not None:
        for detectors in owner.iter("Detectors"):
            name = detectors.findtext("Name")
            index = detectors.findtext("Detector")
            if name and index is not None and _int(index, default=-1) == channel_index:
                return name
    return f"ch{channel_index}"


def _harmonic(owner: ET.Element | None) -> int:
    if owner is not None:
        value = next((e.text for e in owner.iter("Harmonic") if e.text), None)
        if value is not None:
            return _int(value, default=1)
    return 1


def _float(raw: str | None, label: str, errors: list[str]) -> float | None:
    if raw is None or not raw.strip():
        errors.append(f"{label} is missing")
        return None
    try:
        return float(raw)
    except ValueError:
        errors.append(f"{label} is not a number (got {raw!r})")
        return None


def _int(raw: str | None, *, default: int) -> int:
    try:
        return int((raw or "").strip())
    except ValueError:
        return default


# ── Binding records onto a dataset selection ──────────────────
#
# A ``.lif`` names things its own way — region ``Region_1``, channel index 0,
# detector ``HyD X 3``. ``BatchCalibration`` is keyed by ``.h5`` stem and
# ``.h5`` channel name (``G3BP1``). Nothing in the ``.lif`` can know that
# second name, so the two have to be bridged. The CSV path bridged it by
# having a human type both names; here, auto-matching proposes what it can
# prove and the caller lets the user correct the rest.

# (dataset stem, channel name) -> index into the records tuple
Bindings = dict[tuple[str, str], int]


def auto_match(
    records: Sequence[LifCalibrationRecord],
    selection: Mapping[str, Sequence[str]],
) -> Bindings:
    """Propose bindings that follow unambiguously from the inputs.

    ``selection`` maps a dataset stem to the channel names that need
    calibration. A dataset binds by exact stem match; within it, a channel
    binds only when there is exactly one candidate record and exactly one
    channel. Anything else is left out rather than guessed — a wrong silent
    binding writes a wrong calibration into the ``.h5``, whereas an unbound
    row is visible in the table and blocks validation.
    """
    bindings: Bindings = {}
    for stem, channels in selection.items():
        candidates = [i for i, r in enumerate(records) if r.dataset_stem == stem]
        if len(candidates) == 1 and len(channels) == 1:
            bindings[(stem, channels[0])] = candidates[0]
    return bindings


def resolve_lif_calibration(
    records: Sequence[LifCalibrationRecord],
    selection: Mapping[str, Sequence[str]],
    bindings: Bindings,
) -> tuple[BatchCalibration, tuple[str, ...]]:
    """Apply ``bindings`` and return the calibration plus what is unresolved.

    Returns messages rather than raising, so the dialog can render every
    problem at once alongside its other pre-flight errors. A record whose stem
    matches no selected dataset is silently ignored: a ``.lif`` may hold
    regions the user did not select, and that is not an error.
    """
    rows: dict[str, dict[str, ChannelCalibration]] = {}
    messages: list[str] = []

    for stem, channels in selection.items():
        for channel in channels:
            index = bindings.get((stem, channel))
            if index is None or not 0 <= index < len(records):
                messages.append(
                    f"{stem}: channel {channel!r} has no .lif calibration bound"
                )
                continue
            record = records[index]
            rows.setdefault(stem, {})[channel] = ChannelCalibration(
                frequency_mhz=record.frequency_mhz,
                phase=record.phase,
                modulation=record.modulation,
            )

    frozen: dict[str, Mapping[str, ChannelCalibration]] = {
        stem: MappingProxyType(dict(channels)) for stem, channels in rows.items()
    }
    calibration = BatchCalibration(rows=MappingProxyType(frozen))
    messages.extend(validate_frequency_consistency(calibration))
    return calibration, tuple(messages)
