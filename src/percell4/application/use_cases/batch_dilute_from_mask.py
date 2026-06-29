"""Use case: batch dilute-from-mask across ``.h5`` files.

Per-dataset orchestrator backing the batch "Dilute phase mask from mask"
tool (``docs/plans/2026-06-28-002-feat-dilute-phase-mask-from-mask-plan.md``).
For every ``.h5`` it grows an existing condensed-phase ``/masks/<mask_name>``
by a disk radius and inverts it within an existing
``/labels/<segmentation_name>``, writing the dilute phase to
``/masks/<output_name>``:

    dilute = (seg_labels > 0) AND NOT dilation(condensed, disk(radius_px))

The morphology is the single source of truth in
:mod:`percell4.domain.segmentation.dilute_mask` (R7); this layer only
orchestrates per-dataset I/O, isolation, and the per-frame time-lapse loop.

Per-dataset isolation mirrors :func:`batch_fit_phasor_masks`: a missing
file, a corrupt HDF5 header, a mask/segmentation shape mismatch, or any
unexpected exception is recorded as a per-dataset item and the batch
continues — the loop **never raises** out of a single dataset's failure.

Time-lapse (D4): the canonical discriminator is ``n_timepoints`` from
``/metadata`` (never ``array.ndim``). When ``n_timepoints <= 1`` — or both
the mask and the segmentation are stored 2D (time-invariant) — one 2D op is
run and a 2D mask written (byte-identical to a pure 2D dataset). When a
multi-timepoint dataset has a time-stacked ``(T, H, W)`` mask or
segmentation, the op runs per frame (a 2D input broadcasts) and an
exact-``T`` ``(T, H, W)`` mask is written — empty frames yield all-zero
planes, never dropped.

No imports from ``qtpy``, ``napari``, or any GUI module. Pure
application-layer orchestration; the on-disk write reuses
:func:`write_dilute_mask`, which requires a **bool** array and casts to
``uint8`` itself (never pre-binarize).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from percell4.adapters.hdf5_store import Hdf5DatasetRepository
from percell4.application.use_cases.accept_dilute_mask import write_dilute_mask
from percell4.domain.segmentation.dilute_mask import dilute_from_mask
from percell4.store import DatasetStore

logger = logging.getLogger(__name__)

ItemStatus = str  # one of "processed", "skipped", "failed"


# ── Result types (D6) ───────────────────────────────────────────────────


@dataclass(frozen=True)
class DiluteItemResult:
    """Outcome of one dataset in a batch dilute-from-mask run.

    ``status`` is one of ``"processed"`` / ``"skipped"`` / ``"failed"``.
    ``message`` carries the reason (skip / failure) or an annotation — most
    notably the empty-output flag on an otherwise ``processed`` item when the
    dilute mask covers zero in-cell pixels (radius too large, the condensed
    mask already fills its cells, or a zero-cell segmentation).
    """

    h5_path: Path
    status: ItemStatus
    message: str = ""


@dataclass(frozen=True)
class DiluteReport:
    """Aggregated result of a batch dilute-from-mask run."""

    items: tuple[DiluteItemResult, ...] = ()

    @property
    def total_processed(self) -> int:
        return sum(1 for r in self.items if r.status == "processed")

    @property
    def total_skipped(self) -> int:
        return sum(1 for r in self.items if r.status == "skipped")

    @property
    def total_failed(self) -> int:
        return sum(1 for r in self.items if r.status == "failed")


_EMPTY_MESSAGE = (
    "output empty: 0 in-cell dilute pixels "
    "(radius too large or condensed fills cells)"
)


# ── Main entry point ────────────────────────────────────────────────────


def batch_dilute_from_mask(
    h5_paths: Iterable[Path],
    *,
    mask_name: str,
    segmentation_name: str,
    radius_px: int,
    output_name: str,
    progress_callback: Callable[[DiluteItemResult], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> DiluteReport:
    """Dilate + invert-within-cells an existing mask across ``h5_paths``.

    For every dataset the use case reads ``/masks/<mask_name>`` and
    ``/labels/<segmentation_name>``, computes
    ``(seg_labels > 0) & ~dilation(condensed, disk(radius_px))``, and writes
    the result to ``/masks/<output_name>``. A missing mask/segmentation, an
    output-name collision, or a bad file classifies *that dataset* without
    aborting the batch.

    Args:
        h5_paths: Datasets to process. Resolved via ``.resolve()`` and
            de-duplicated (preserving input order).
        mask_name: Name of the existing condensed ``/masks/<name>`` to grow.
        segmentation_name: Name of the existing ``/labels/<name>`` that
            bounds the inversion.
        radius_px: Disk **radius** in pixels (``0`` → no dilation, D2).
        output_name: Name to write under ``/masks/<output_name>``. Must not
            already exist as a channel, a mask, or a label in a dataset
            (collision → that dataset is skipped, never overwritten).
        progress_callback: Invoked once per dataset, in processing order,
            with the dataset's :class:`DiluteItemResult`.
        cancel_check: Optional callable polled between datasets. When it
            returns True the loop breaks; the report contains only the items
            processed so far (already-written masks persist).

    Returns:
        A :class:`DiluteReport` whose items list is ``<= len(h5_paths)``
        after de-dup (equal when not cancelled).

    Raises:
        ValueError: when ``output_name``, ``mask_name``, or
            ``segmentation_name`` is empty, ``radius_px < 0``, or
            ``h5_paths`` is empty. All raised before any I/O.
    """
    # ── Input validation (no I/O) ──────────────────────────────────────
    if not output_name:
        raise ValueError("output_name must be non-empty")
    if radius_px < 0:
        raise ValueError("radius_px must be >= 0")
    if not mask_name:
        raise ValueError("mask_name must be non-empty")
    if not segmentation_name:
        raise ValueError("segmentation_name must be non-empty")

    # ── Input normalization (no I/O): resolve + dedup, preserve order. ──
    resolved_paths: list[Path] = []
    seen: set[Path] = set()
    for p in h5_paths:
        rp = Path(p).resolve()
        if rp not in seen:
            seen.add(rp)
            resolved_paths.append(rp)
    if not resolved_paths:
        raise ValueError("h5_paths must be non-empty")

    repo = Hdf5DatasetRepository()
    results: list[DiluteItemResult] = []

    for path in resolved_paths:
        if cancel_check is not None and cancel_check():
            break
        item = _process_one_dataset(
            h5_path=path,
            repo=repo,
            mask_name=mask_name,
            segmentation_name=segmentation_name,
            radius_px=radius_px,
            output_name=output_name,
        )
        results.append(item)
        if progress_callback is not None:
            progress_callback(item)

    return DiluteReport(items=tuple(results))


def _process_one_dataset(
    *,
    h5_path: Path,
    repo: Hdf5DatasetRepository,
    mask_name: str,
    segmentation_name: str,
    radius_px: int,
    output_name: str,
) -> DiluteItemResult:
    """Run the dilute-from-mask op for one dataset. Never raises.

    Order of checks: open → metadata/listings → presence (mask + seg) →
    collision (channel/mask/label) → compute (2D or per-frame) → empty-output
    annotation → write. A failure at any store boundary classifies the
    dataset (``failed`` / ``skipped``) without touching siblings.
    """
    # Open the store. A missing or corrupt file fails the whole item.
    try:
        store = DatasetStore(h5_path)
        if not store.exists():
            raise FileNotFoundError(f"file not found: {h5_path}")
        handle = repo.open(h5_path)
    except Exception as exc:  # noqa: BLE001 — orchestrator never raises per-item
        return DiluteItemResult(h5_path, "failed", f"open failed: {exc}")

    try:
        meta = store.metadata
        # Canonical time-handling discriminator — never array.ndim.
        n_timepoints = int(meta.get("n_timepoints", 1) or 1)
        channel_names = list(meta.get("channel_names", []) or [])
        masks_present = store.list_groups("masks")
        labels_present = store.list_groups("labels")
    except Exception as exc:  # noqa: BLE001
        return DiluteItemResult(h5_path, "failed", f"metadata read failed: {exc}")

    # ── Presence (D7): the chosen mask + segmentation must be on disk. ──
    if mask_name not in masks_present:
        return DiluteItemResult(
            h5_path, "skipped", f"mask not present: '{mask_name}'"
        )
    if segmentation_name not in labels_present:
        return DiluteItemResult(
            h5_path,
            "skipped",
            f"segmentation not present: '{segmentation_name}'",
        )

    # ── Collision: never overwrite an existing channel/mask/label. ──
    if output_name in channel_names:
        return DiluteItemResult(
            h5_path, "skipped", f"name collides with channel '{output_name}'"
        )
    if output_name in masks_present:
        return DiluteItemResult(
            h5_path, "skipped", f"name collides with mask '{output_name}'"
        )
    if output_name in labels_present:
        return DiluteItemResult(
            h5_path, "skipped", f"name collides with label '{output_name}'"
        )

    # ── Compute (D4): 2D op or exact-T per-frame loop. ──
    try:
        out = _compute_dilute(
            store, mask_name, segmentation_name, radius_px, n_timepoints
        )
    except ValueError as exc:
        # Mask/seg shape mismatch (domain op) or a mis-stacked time axis —
        # surface the clear reason, not a numpy traceback.
        return DiluteItemResult(h5_path, "failed", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("batch_dilute_from_mask compute failed on %s", h5_path)
        return DiluteItemResult(h5_path, "failed", f"compute failed: {exc}")

    # ── Empty-output annotation (D6): still processed, but flagged. ──
    message = _EMPTY_MESSAGE if int(out.sum()) == 0 else ""

    # ── Write via the reusable bool→uint8 store-first writer. ──
    try:
        write_dilute_mask(repo, handle, output_name, out)
    except Exception as exc:  # noqa: BLE001
        return DiluteItemResult(h5_path, "failed", f"write failed: {exc}")

    return DiluteItemResult(h5_path, "processed", message)


def _compute_dilute(
    store: DatasetStore,
    mask_name: str,
    segmentation_name: str,
    radius_px: int,
    n_timepoints: int,
) -> NDArray[np.bool_]:
    """Compute the dilute mask: 2D, or exact-``T`` per-frame for time-lapse.

    The 2D (time-invariant) branch is taken when ``n_timepoints <= 1`` or
    both inputs are stored 2D — a single 2D op, a 2D output. Otherwise (a
    multi-timepoint dataset with a time-stacked mask or segmentation) the op
    runs per frame; ``read_mask`` / ``read_labels`` broadcast a 2D input, so
    a 2D mask paired with a ``(T, H, W)`` segmentation (or vice versa) still
    yields a ``(T, H, W)`` output. Returns a **bool** array (preserved
    through ``np.stack``) for :func:`write_dilute_mask`.
    """
    mask_stacked = store.is_time_stacked(f"masks/{mask_name}")
    seg_stacked = store.is_time_stacked(f"labels/{segmentation_name}")

    if n_timepoints <= 1 or (not mask_stacked and not seg_stacked):
        condensed = store.read_mask(mask_name)
        seg = store.read_labels(segmentation_name)
        return dilute_from_mask(condensed, seg, radius_px)

    # Defensive pre-check: a time-stacked resource's leading axis must equal
    # n_timepoints (write_mask enforces this for production datasets, so this
    # is normally unreachable). Fail with a clear reason rather than a
    # mid-loop IndexError on a corrupted file.
    if mask_stacked:
        leading = store.masks_shape(mask_name)[0]
        if leading != n_timepoints:
            raise ValueError(
                f"mask '{mask_name}' has {leading} frame(s) but the dataset "
                f"has {n_timepoints} timepoints; the time axis would mis-stack"
            )
    if seg_stacked:
        leading = store.labels_shape(segmentation_name)[0]
        if leading != n_timepoints:
            raise ValueError(
                f"segmentation '{segmentation_name}' has {leading} frame(s) "
                f"but the dataset has {n_timepoints} timepoints; the time axis "
                "would mis-stack"
            )

    planes = [
        dilute_from_mask(
            store.read_mask(mask_name, timepoint=t),
            store.read_labels(segmentation_name, timepoint=t),
            radius_px,
        )
        for t in range(n_timepoints)
    ]
    return np.stack(planes, axis=0)
