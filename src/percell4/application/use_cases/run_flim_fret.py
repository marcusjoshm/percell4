"""Use case: orchestrate a FLIM-FRET analysis run across donor / DA pairs.

This module is Qt-free and importable without a running ``QApplication``.

Per pair:

1. ``cancel_check`` is honored before any compute — if True, the pair and
   every remaining pair is marked ``cancelled``.
2. ``flim_fret_discovery.validate_pair_layers`` re-validates the layer names
   against the live ``.h5`` so a layer deleted between dialog Accept and Start
   surfaces as ``status="missing_layer"`` rather than crashing the run.
3. Donor and DA stores are opened (lightweight; reads use ``view_bin=1`` and
   never look at ``Session``). Lifetime channel names are resolved to a
   channel index via ``metadata["channel_names"].index(name)``.
4. The effective mask is ``(mask > 0) & (phasor > 0)`` on each side.
5. In whole-field mode the orchestrator emits one row per pair using
   per-side arithmetic means over the effective masks.
6. In single-cell mode the donor reference is the arithmetic mean over per-cell
   donor means (cells with zero valid pixels excluded). The orchestrator then
   emits one row per DA cell (label 0 excluded as background), with that
   single donor reference repeated across all rows of that pair.
7. ``fret_efficiency = 1 - (da_mean / donor_mean)`` with one division guard:
   ``NaN`` when ``donor_mean == 0`` or ``isnan(donor_mean)``. Negative donor
   values and ``> 1`` or negative FRET values are reported as-is.

Per-pair exceptions never abort the batch — they are captured as
``status="error"`` and the run continues.

The orchestrator returns ``FlimFretReport`` with per-pair results. It does
NOT create the run folder or write the CSV; that is the dialog's
responsibility (so the run folder is shared with the ``RunLog`` and the
dialog can surface the result path in a summary message box).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np

from percell4.application.use_cases.flim_fret_discovery import (
    validate_pair_layers,
)
from percell4.store import DatasetStore
from percell4.workflows.models import (
    FlimFretConfig,
    FlimFretPair,
    FlimFretPairResult,
    FlimFretReport,
    FlimFretStatus,
)
from percell4.workflows.run_log import RunLog

logger = logging.getLogger(__name__)


ProgressCallback = Callable[[FlimFretPair, FlimFretPairResult], None]
CancelCheck = Callable[[], bool]


def run_flim_fret(
    config: FlimFretConfig,
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
    run_log: RunLog | None = None,
) -> FlimFretReport:
    """Process every pair in ``config`` and return per-pair results.

    The orchestrator never raises for per-pair problems — they are
    captured as result statuses. Unexpected exceptions in this function
    itself (e.g., bad config) propagate.
    """
    results: list[FlimFretPairResult] = []
    if run_log is not None:
        run_log.log(
            phase="flim_fret",
            event="run_started",
            n_pairs=len(config.pairs),
            single_cell=config.single_cell,
        )

    cancelled = False
    for pair in config.pairs:
        if cancelled or (cancel_check is not None and cancel_check()):
            cancelled = True
            result = _empty_result(pair, FlimFretStatus.CANCELLED, reason=None)
            if run_log is not None:
                run_log.log(
                    phase="flim_fret",
                    dataset=pair.name,
                    event="pair_cancelled",
                )
            _emit(result, progress_callback)
            results.append(result)
            continue

        if run_log is not None:
            run_log.log(
                phase="flim_fret",
                dataset=pair.name,
                event="pair_started",
            )

        try:
            result = _compute_pair(pair, single_cell=config.single_cell)
        except Exception as exc:  # noqa: BLE001 — never abort the batch
            logger.exception("FLIM-FRET pair %r failed", pair.name)
            result = _empty_result(
                pair, FlimFretStatus.ERROR, reason=str(exc)
            )

        if run_log is not None:
            run_log.log(
                phase="flim_fret",
                dataset=pair.name,
                event="pair_done",
                status=result.status.value,
                reason=result.reason,
                n_rows=len(result.rows),
                n_pixels_donor=result.n_pixels_donor,
                n_cells_donor_reference=result.n_cells_donor_reference,
                n_da_cells_skipped=result.n_da_cells_skipped,
            )

        _emit(result, progress_callback)
        results.append(result)

    if run_log is not None:
        run_log.log(
            phase="flim_fret",
            event="run_finished",
            n_succeeded=sum(
                1 for r in results if r.status is FlimFretStatus.SUCCEEDED
            ),
            n_failed=sum(
                1
                for r in results
                if r.status
                in (
                    FlimFretStatus.ERROR,
                    FlimFretStatus.MISSING_LAYER,
                    FlimFretStatus.DATASET_OPEN_FAILED,
                    FlimFretStatus.DONOR_REFERENCE_EMPTY,
                )
            ),
            n_cancelled=sum(
                1 for r in results if r.status is FlimFretStatus.CANCELLED
            ),
        )

    return FlimFretReport(results=results)


# ── Per-pair compute ────────────────────────────────────────


def _compute_pair(
    pair: FlimFretPair, *, single_cell: bool
) -> FlimFretPairResult:
    """Do the math for one pair. Returns a ``FlimFretPairResult``."""
    missing = validate_pair_layers(pair, single_cell=single_cell)
    if missing:
        return _empty_result(
            pair, FlimFretStatus.MISSING_LAYER, reason="; ".join(missing)
        )

    donor_store = DatasetStore(pair.donor_h5)
    da_store = DatasetStore(pair.da_h5)

    # Load arrays at view_bin=1. native_shape lock guarantees per-dataset
    # consistency; cross-dataset shapes may differ and that's fine.
    donor_mask = donor_store.read_mask(pair.donor_mask, view_bin=1)
    donor_phasor = donor_store.read_mask(pair.donor_phasor, view_bin=1)
    donor_lifetime = _read_lifetime_channel(donor_store, pair.donor_lifetime)
    donor_eff = (donor_mask > 0) & (donor_phasor > 0)
    _assert_shape("donor mask/phasor/lifetime", donor_mask, donor_phasor, donor_lifetime)

    da_mask = da_store.read_mask(pair.da_mask, view_bin=1)
    da_phasor = da_store.read_mask(pair.da_phasor, view_bin=1)
    da_lifetime = _read_lifetime_channel(da_store, pair.da_lifetime)
    da_eff = (da_mask > 0) & (da_phasor > 0)
    _assert_shape("DA mask/phasor/lifetime", da_mask, da_phasor, da_lifetime)

    if not single_cell:
        return _compute_whole_field(
            pair, donor_eff, donor_lifetime, da_eff, da_lifetime
        )

    # Single-cell branch.
    assert pair.donor_segmentation is not None
    assert pair.da_segmentation is not None
    donor_labels = donor_store.read_labels(pair.donor_segmentation, view_bin=1)
    da_labels = da_store.read_labels(pair.da_segmentation, view_bin=1)
    _assert_shape("donor labels vs mask", donor_labels, donor_mask)
    _assert_shape("DA labels vs mask", da_labels, da_mask)

    return _compute_single_cell(
        pair,
        donor_eff=donor_eff,
        donor_lifetime=donor_lifetime,
        donor_labels=donor_labels,
        da_eff=da_eff,
        da_lifetime=da_lifetime,
        da_labels=da_labels,
    )


def _compute_whole_field(
    pair: FlimFretPair,
    donor_eff: np.ndarray,
    donor_lifetime: np.ndarray,
    da_eff: np.ndarray,
    da_lifetime: np.ndarray,
) -> FlimFretPairResult:
    n_pixels_donor = int(donor_eff.sum())
    n_pixels_da = int(da_eff.sum())
    donor_mean = (
        float(np.mean(donor_lifetime[donor_eff])) if n_pixels_donor else float("nan")
    )
    da_mean = (
        float(np.mean(da_lifetime[da_eff])) if n_pixels_da else float("nan")
    )
    fret = _fret(donor_mean, da_mean)

    row = _row(
        pair,
        cell_id="",
        donor_mean=donor_mean,
        da_mean=da_mean,
        fret_efficiency=fret,
        n_pixels_donor=n_pixels_donor,
        n_pixels_da=n_pixels_da,
        n_cells_donor_reference="",
        n_da_cells_skipped="",
    )
    return FlimFretPairResult(
        pair=pair,
        status=FlimFretStatus.SUCCEEDED,
        reason=None,
        rows=[row],
        n_pixels_donor=n_pixels_donor,
        n_cells_donor_reference=0,
        n_da_cells_skipped=0,
    )


def _compute_single_cell(
    pair: FlimFretPair,
    *,
    donor_eff: np.ndarray,
    donor_lifetime: np.ndarray,
    donor_labels: np.ndarray,
    da_eff: np.ndarray,
    da_lifetime: np.ndarray,
    da_labels: np.ndarray,
) -> FlimFretPairResult:
    # Pool donor cell means into one reference.
    donor_cell_ids = _cell_ids(donor_labels)
    donor_cell_means: list[float] = []
    total_donor_pixels = 0
    for cid in donor_cell_ids:
        cell_mask = donor_eff & (donor_labels == cid)
        n = int(cell_mask.sum())
        if n == 0:
            continue
        donor_cell_means.append(float(np.mean(donor_lifetime[cell_mask])))
        total_donor_pixels += n

    n_cells_donor_reference = len(donor_cell_means)
    donor_ref = (
        float(np.mean(donor_cell_means))
        if donor_cell_means
        else float("nan")
    )

    # Iterate DA cells. Ascending integer label order is the canonical
    # sort and the plan's stated rule.
    da_cell_ids = _cell_ids(da_labels)
    rows: list[dict[str, Any]] = []
    n_skipped = 0
    for cid in da_cell_ids:
        cell_mask = da_eff & (da_labels == cid)
        n = int(cell_mask.sum())
        if n == 0:
            da_mean = float("nan")
            n_skipped += 1
        else:
            da_mean = float(np.mean(da_lifetime[cell_mask]))
        fret = _fret(donor_ref, da_mean)
        rows.append(
            _row(
                pair,
                cell_id=int(cid),
                donor_mean=donor_ref,
                da_mean=da_mean,
                fret_efficiency=fret,
                n_pixels_donor=total_donor_pixels,
                n_pixels_da=n,
                n_cells_donor_reference=n_cells_donor_reference,
                # Filled with the final count below.
                n_da_cells_skipped=None,
            )
        )

    # Stamp the per-pair skipped count into every row.
    for row in rows:
        row["n_da_cells_skipped"] = n_skipped

    status = (
        FlimFretStatus.DONOR_REFERENCE_EMPTY
        if n_cells_donor_reference == 0
        else FlimFretStatus.SUCCEEDED
    )
    reason = (
        "donor reference pool was empty (no donor cells with valid pixels)"
        if status is FlimFretStatus.DONOR_REFERENCE_EMPTY
        else None
    )
    return FlimFretPairResult(
        pair=pair,
        status=status,
        reason=reason,
        rows=rows,
        n_pixels_donor=total_donor_pixels,
        n_cells_donor_reference=n_cells_donor_reference,
        n_da_cells_skipped=n_skipped,
    )


# ── Small helpers ──────────────────────────────────────────


def _read_lifetime_channel(store: DatasetStore, name: str) -> np.ndarray:
    """Resolve a lifetime channel name to its /intensity slice.

    Channel index is ``metadata["channel_names"].index(name)``; a missing
    name raises ``ValueError`` which the orchestrator turns into a
    ``status="error"`` result (with `validate_pair_layers` having already
    caught the same problem earlier — this is the belt-and-braces path).
    """
    channel_names = store.metadata.get("channel_names", []) or []
    channel_idx = channel_names.index(name)
    return store.read_channel("intensity", channel_idx, view_bin=1)


def _cell_ids(labels: np.ndarray) -> list[int]:
    """Return unique label values, sorted ascending, excluding 0 (background)."""
    unique = np.unique(labels)
    return sorted(int(v) for v in unique if int(v) != 0)


def _assert_shape(label: str, *arrays: np.ndarray) -> None:
    shapes = {a.shape for a in arrays}
    if len(shapes) != 1:
        raise AssertionError(
            f"{label} shape mismatch: {[a.shape for a in arrays]}"
        )


def _fret(donor_mean: float, da_mean: float) -> float:
    """Compute ``1 - (da / donor)`` with the single division guard.

    The guard fires only on ``donor == 0`` or ``isnan(donor)``. Negative
    donor values and ``> 1`` or negative FRET values are reported as-is
    per the workflow's scope boundary "no clamping".
    """
    if donor_mean == 0 or _isnan(donor_mean):
        return float("nan")
    if _isnan(da_mean):
        return float("nan")
    return 1.0 - (da_mean / donor_mean)


def _isnan(x: float) -> bool:
    # numpy.float32 doesn't have `.is_integer`; cast to plain float for math.
    return x != x


def _row(
    pair: FlimFretPair,
    *,
    cell_id: int | str,
    donor_mean: float,
    da_mean: float,
    fret_efficiency: float,
    n_pixels_donor: int,
    n_pixels_da: int,
    n_cells_donor_reference: int | str,
    n_da_cells_skipped: int | None | str,
) -> dict[str, Any]:
    return {
        "pair_name": pair.name,
        "donor_dataset": pair.donor_h5.name,
        "da_dataset": pair.da_h5.name,
        "cell_id": cell_id,
        "donor_mean_lifetime": donor_mean,
        "da_mean_lifetime": da_mean,
        "fret_efficiency": fret_efficiency,
        "n_pixels_donor": n_pixels_donor,
        "n_pixels_da": n_pixels_da,
        "n_cells_donor_reference": n_cells_donor_reference,
        "n_da_cells_skipped": n_da_cells_skipped,
    }


def _empty_result(
    pair: FlimFretPair, status: FlimFretStatus, *, reason: str | None
) -> FlimFretPairResult:
    return FlimFretPairResult(
        pair=pair,
        status=status,
        reason=reason,
        rows=[],
        n_pixels_donor=0,
        n_cells_donor_reference=0,
        n_da_cells_skipped=0,
    )


def _emit(
    result: FlimFretPairResult, cb: ProgressCallback | None
) -> None:
    if cb is None:
        return
    try:
        cb(result.pair, result)
    except Exception:  # noqa: BLE001
        logger.exception("progress_callback raised; continuing")
