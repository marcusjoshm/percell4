"""FLIM phasor calibration extracted from a Leica ``.lif`` header.

LAS X solves the phasor calibration and stores it; this module reads it back
at full precision rather than at the four decimal places the LAS X dialog
displays. Hand-transcribing that display into a calibration CSV is what this
replaces. The source may be the ``.lif`` itself or a ``.xml`` header export
from ``tools/extract_lif_metadata.py`` — useful when the ``.lif`` is a
multi-GB file stranded on the acquisition PC.

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
from percell4.domain.io.lif_header import read_lif_metadata

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
    #: Position of this calibration block within its ``PhasorData``. This is
    #: the channel identity — the block's own ``<Channel>`` element reads 0 in
    #: every block, so it names nothing.
    channel_index: int
    detector_name: str
    frequency_mhz: float
    phase: float
    modulation: float
    harmonic: int

    @property
    def label(self) -> str:
        """Identity for a picker: region, channel ordinal, and detector."""
        return f"{self.region_name} · ch{self.channel_index} {self.detector_name}"


def read_lif_calibration(path: Path | str) -> tuple[LifCalibrationRecord, ...]:
    """Return every phasor calibration record in the LIF metadata at ``path``.

    ``path`` may be a ``.lif`` or a ``.xml`` header export made by
    ``tools/extract_lif_metadata.py`` — the two carry the same document, so
    every rule below applies to both identically.

    Raises :class:`LifCalibrationError` when the header parses but carries no
    per-image calibration, or when a record's numeric fields are unusable.
    Field errors accumulate across every record and raise once at the end, so
    a caller sees all of them rather than only the first — the same contract
    :func:`~percell4.domain.io.calibration_csv.parse_calibration_csv` offers.

    A malformed container raises
    :class:`~percell4.domain.errors.LifHeaderError` from the reader instead,
    which keeps "not LIF metadata" distinct from "no calibration in it".
    """
    path = Path(path)
    root = read_lif_metadata(path)
    parents = {child: parent for parent in root.iter() for child in parent}

    # Enumerate per container: a region's calibration blocks are ordered, and
    # that order is the only thing tying a block to a channel.
    blocks: list[tuple[ET.Element, int]] = []
    for container in root.iter():
        siblings = [
            c
            for c in container
            if c.tag == "Channels" and c.find(CALIBRATION_MARKER) is not None
        ]
        blocks.extend((block, ordinal) for ordinal, block in enumerate(siblings))

    if not blocks:
        raise LifCalibrationError(
            [
                f"{path.name}: no phasor calibration in this file — the "
                f"header has no <{CALIBRATION_MARKER}> record. Calibrate the "
                "image in the LAS X Phasor Calibration dialog and re-save "
                "the .lif (then re-extract, if using a .xml export)."
            ]
        )

    errors: list[str] = []
    records: list[LifCalibrationRecord] = []
    for block, ordinal in blocks:
        chain = _element_chain(block, parents)
        record = _build_record(block, ordinal, chain, parents, errors)
        if record is not None:
            records.append(record)

    if errors:
        raise LifCalibrationError([f"{path.name}: {e}" for e in errors])
    return tuple(records)


def _strip_lif_suffix(name: str) -> str:
    return name[:-4] if name.lower().endswith(".lif") else name


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
    ordinal: int,
    chain: list[str],
    parents: dict,
    errors: list[str],
) -> LifCalibrationRecord | None:
    region = chain[1] if len(chain) > 1 else (chain[0] if chain else "")
    where = region or "unnamed element"

    # Dataset stem is root name + region name, joined the way LAS X names its
    # exports. The root Element is often named for the file *including* its
    # ``.lif`` suffix, which no exported .h5 stem carries — leaving it in makes
    # every stem comparison fail, so auto-match silently binds nothing. Mirrors
    # the ``.h5`` trimming in ``calibration_csv.parse_calibration_csv``.
    # Best-effort even so: the caller lets the user rebind when it misses.
    parts = [_strip_lif_suffix(part) for part in chain[:2]]
    stem = "_".join(parts) if len(parts) > 1 else (parts[0] if parts else "")

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
        channel_index=ordinal,
        detector_name=_detector_name(owner, ordinal),
        frequency_mhz=1.0 / period / 1e6,
        phase=-math.radians(phase_deg),
        modulation=1.0 / amplitude,
        harmonic=_harmonic(owner),
    )


def _detector_name(owner: ET.Element | None, ordinal: int) -> str:
    """Name of the ``ordinal``-th detector record in this element.

    Positional, deliberately. Every acquisition detector record reports
    ``<Detector>0</Detector>`` regardless of which detector it describes, so a
    lookup keyed on that value returns the first detector for every block — the
    reason a two-channel region used to show the same detector name twice. A
    region carries its calibration blocks and its detector records in the same
    order, and that correspondence is what names a block.
    """
    if owner is not None:
        names = [
            name
            for detectors in owner.iter("Detectors")
            if (name := detectors.findtext("Name"))
        ]
        if ordinal < len(names):
            return names[ordinal]
    return f"detector {ordinal}"


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
    calibration. A dataset binds by exact stem match. Within it, channels bind
    by position when the counts agree and the records occupy consecutive
    positions from zero — the ``.lif`` orders its calibration blocks the way
    the acquisition ordered its detectors, and the ``.h5`` orders its channels
    the same way, so the *n*-th channel takes the *n*-th record. A single
    record against a single channel is that rule's simplest case.

    Any other shape is left out rather than guessed: a wrong silent binding
    writes a wrong calibration into the ``.h5``, whereas an unbound row is
    visible in the table and blocks validation.

    Position is an inference, not a proof — the table shows every binding so
    it can be corrected before the run.
    """
    bindings: Bindings = {}
    for stem, channels in selection.items():
        candidates = [i for i, r in enumerate(records) if r.dataset_stem == stem]
        if len(candidates) != len(channels):
            continue
        by_position = sorted(candidates, key=lambda i: records[i].channel_index)
        if [records[i].channel_index for i in by_position] != list(range(len(channels))):
            continue
        for channel, index in zip(channels, by_position, strict=True):
            bindings[(stem, channel)] = index
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
