"""Dataset eligibility / pre-screening for the FLIM-FRET analysis workflow.

This module is Qt-free and importable without a running ``QApplication``. It
provides two surfaces:

- :func:`discover_flim_fret_candidates` — scan a folder for ``.h5`` files and
  classify each as eligible or excluded, with human-readable reasons.
- :func:`validate_pair_layers` — re-validate a configured pair against the
  live ``.h5`` files at run time (called from the orchestrator at the top of
  per-pair compute).

The qualification rule mirrors the user-chosen contract: a dataset is eligible
only if it has at least one ``/masks/<name>`` ending in ``_mask``, one ending
in ``_phasor``, and one channel name ending in ``_lifetime`` under
``/intensity``. Single-cell mode additionally requires at least one
``/labels/<name>``. Time-lapse ``/intensity`` (4D ``(T, C, H, W)``) is
explicitly out of scope and rejected at discovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import h5py

from percell4.io.paths import scan_files
from percell4.store import DatasetStore
from percell4.workflows.models import FlimFretPair


@dataclass(frozen=True)
class DatasetCandidate:
    """One ``.h5`` file's eligibility evaluation.

    ``qualifies`` is True iff ``reasons`` is empty. ``reasons`` carries
    human-readable strings suitable for surfacing in a dialog warning
    summary or a tooltip.
    """

    path: Path
    qualifies: bool
    reasons: list[str] = field(default_factory=list)


def discover_flim_fret_candidates(
    source_folder: Path, *, single_cell: bool
) -> list[DatasetCandidate]:
    """Scan ``source_folder`` non-recursively for ``.h5`` / ``.hdf5`` files.

    Each discovered file is opened (read-only) and evaluated against the
    FLIM-FRET eligibility rule. The function never raises — file-open
    failures are captured as a candidate with ``qualifies=False`` and a
    ``reasons=["open failed: ..."]`` entry.

    Results are sorted by path so dialog dropdowns are deterministic.
    """
    paths = scan_files(source_folder, "*.h5", "*.hdf5")
    return [_evaluate_candidate(p, single_cell=single_cell) for p in paths]


def _evaluate_candidate(path: Path, *, single_cell: bool) -> DatasetCandidate:
    reasons: list[str] = []
    try:
        store = DatasetStore(path)
        mask_names = store.list_masks()
        if not any(n.endswith("_mask") for n in mask_names):
            reasons.append("no /masks/<*>_mask")
        if not any(n.endswith("_phasor") for n in mask_names):
            reasons.append("no /masks/<*>_phasor")

        channel_names = store.metadata.get("channel_names", []) or []
        if not any(c.endswith("_lifetime") for c in channel_names):
            reasons.append("no /intensity/*_lifetime channel")

        if _intensity_is_time_lapse(path):
            reasons.append("time-lapse /intensity unsupported")

        if single_cell and not store.list_labels():
            reasons.append("no /labels/* (required for single-cell)")
    except Exception as exc:  # noqa: BLE001 — discovery must not raise
        reasons.append(f"open failed: {exc}")

    return DatasetCandidate(path=path, qualifies=not reasons, reasons=reasons)


def _intensity_is_time_lapse(path: Path) -> bool:
    """Inspect ``/intensity`` without loading the array.

    The authoritative time-axis discriminator is the ``dims`` attribute (a
    leading ``'T'``), per the time-handling contract — a single-channel
    ``(T, H, W)`` movie is only 3D, so a bare ``ndim >= 4`` check would miss it.
    Falls back to ``ndim >= 4`` for legacy files written without a ``dims`` attr.

    Opens the file directly with ``h5py`` to avoid pulling the full
    array through :meth:`DatasetStore.read_array`.
    """
    with h5py.File(path, "r") as f:
        ds = f.get("intensity")
        if ds is None:
            return False
        dims = ds.attrs.get("dims")
        if dims is not None and len(dims) > 0:
            return str(dims[0]) == "T"
        return ds.ndim >= 4


def list_lifetime_channel_names(store: DatasetStore) -> list[str]:
    """Return channel names ending in ``_lifetime`` from ``/intensity``.

    Used to populate the lifetime dropdown in the Configure sub-dialog.
    Unlike the mask and phasor dropdowns (which are unfiltered per R4 of
    the plan), the lifetime dropdown filters — only the derived lifetime
    channels qualify, and the convention ``_lifetime`` is enforced at the
    writer side by ``application.use_cases.compute_lifetime``.
    """
    channel_names = store.metadata.get("channel_names", []) or []
    return [c for c in channel_names if c.endswith("_lifetime")]


def validate_pair_layers(pair: FlimFretPair, *, single_cell: bool) -> list[str]:
    """Re-validate every named layer in ``pair`` against the live ``.h5``.

    Called from the orchestrator before per-pair compute. Returns a list of
    human-readable missing-layer reasons (empty when everything checks
    out). The orchestrator turns a non-empty return into a
    ``status="missing_layer"`` result and continues with the next pair.

    Also re-checks the time-lapse rejection from discovery, so a dataset
    re-imported as time-lapse between dialog Accept and run Start is
    captured as an error rather than crashing in the read path.
    """
    reasons: list[str] = []
    sides = (
        (
            "donor",
            pair.donor_h5,
            pair.donor_mask,
            pair.donor_phasor,
            pair.donor_lifetime,
            pair.donor_segmentation,
        ),
        (
            "DA",
            pair.da_h5,
            pair.da_mask,
            pair.da_phasor,
            pair.da_lifetime,
            pair.da_segmentation,
        ),
    )
    for side, h5_path, mask, phasor, lifetime, seg in sides:
        try:
            store = DatasetStore(h5_path)
            masks = store.list_masks()
            if mask not in masks:
                reasons.append(f"missing {side} mask {mask!r}")
            if phasor not in masks:
                reasons.append(f"missing {side} phasor mask {phasor!r}")
            channel_names = store.metadata.get("channel_names", []) or []
            if lifetime not in channel_names:
                reasons.append(
                    f"missing {side} lifetime channel {lifetime!r}"
                )
            if _intensity_is_time_lapse(h5_path):
                reasons.append(
                    f"{side} /intensity is time-lapse (unsupported)"
                )
            if single_cell and seg is not None:
                if seg not in store.list_labels():
                    reasons.append(
                        f"missing {side} segmentation {seg!r}"
                    )
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"{side} dataset open failed: {exc}")
    return reasons
