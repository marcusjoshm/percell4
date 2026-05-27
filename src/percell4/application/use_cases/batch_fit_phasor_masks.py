"""Use case: batch fit-ellipse + dual-threshold phasor-masks across ``.h5`` files.

Per-(dataset, channel) orchestrator backing the Automated Phasor-Masks
Workflow (``docs/plans/2026-05-27-001-feat-phasor-masks-workflow-plan.md``).

For each input ``.h5``, this module:

1. Opens the file via :class:`DatasetStore`.
2. For each requested channel, runs the collision check (refuses to
   create a mask whose name would shadow an existing channel) and the
   presence check (channel must be in ``metadata.channel_names`` AND
   have ``/decay/<ch>`` on disk).
3. Reads the **unfiltered** phasor maps from ``/phasor/<ch>/g`` +
   ``/phasor/<ch>/s``. This matches the manual phasor-mask recipe
   captured in the workflow's brainstorm: "Start with an unfiltered
   phasor … apply the ROI as Mask". When the maps are absent and
   ``ensure_phasor=True`` (the default), computes them on the fly via
   :class:`ComputePhasor`. When absent and ``ensure_phasor=False``,
   skips the channel with a structured reason. The wavelet-filtered
   pair (``g_filtered`` / ``s_filtered``) is **never** used here even
   when present on disk — the workflow's masks must reflect the raw
   phasor distribution so the dim/noisy regions stay noisy in the
   permissive mask instead of getting smoothed into filled blobs.
4. Derives ``intensity_map = decay.sum(axis=-1)`` (the FLIM
   cross-layer-alignment rule lives at the call site, as called out in
   ``docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md``).
5. Calls :func:`fit_phasor_ellipse_and_apply_masks` from the domain
   helper. Empty-fit-subset and degenerate-fit ``ValueError`` s are
   routed to ``errors[ch]`` with the actual exception message — no
   mask is written for that channel.
6. Writes ``store.write_mask(f"{channel}{suffix_a}", result.mask_a)``
   and ``store.write_mask(f"{channel}{suffix_b}", result.mask_b)``.
   A failure on the second write leaves the first on disk and routes
   the channel to ``errors`` (item ends ``partial`` for the channel).

Per-channel and per-dataset failures isolate; the loop continues. The
returned :class:`BatchPhasorReport` carries truthful partial state.
``BatchPhasorItemResult`` / ``BatchPhasorReport`` are reused from
:mod:`batch_compute_phasor` — the 4-state taxonomy
(``succeeded`` / ``partial`` / ``skipped_no_changes`` / ``failed``) is
shared between the two batch surfaces.

No imports from ``qtpy``, ``napari``, or any GUI module. Pure
application-layer orchestration.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

import h5py
import numpy as np

from percell4.adapters.hdf5_store import Hdf5DatasetRepository
from percell4.application.session import Session
from percell4.application.use_cases.batch_compute_phasor import (
    BatchPhasorItemResult,
    BatchPhasorReport,
    _classify_status,
)
from percell4.application.use_cases.compute_phasor import ComputePhasor
from percell4.domain.segmentation.phasor_masks import (
    fit_phasor_ellipse_and_apply_masks,
)
from percell4.store import DatasetStore

logger = logging.getLogger(__name__)


# ── Pre-flight helpers ──────────────────────────────────────────────────


def _has_path(h5_path: Path, hdf5_path: str) -> bool:
    """Return True if ``hdf5_path`` exists in the file at ``h5_path``."""
    try:
        with h5py.File(h5_path, "r") as f:
            return hdf5_path in f
    except OSError:
        return False


def _unfiltered_phasor_available(h5_path: Path, channel: str) -> bool:
    """``g`` AND ``s`` (unfiltered) both on disk for ``channel``."""
    with h5py.File(h5_path, "r") as f:
        return (
            f"phasor/{channel}/g" in f
            and f"phasor/{channel}/s" in f
        )


# ── Main entry point ────────────────────────────────────────────────────


def batch_fit_phasor_masks(
    h5_paths: Iterable[Path],
    *,
    channels: Sequence[str],
    t_fit: float,
    t_mask_a: float,
    t_mask_b: float,
    suffix_a: str,
    suffix_b: str,
    ensure_phasor: bool = True,
    progress_callback: Callable[[BatchPhasorItemResult], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> BatchPhasorReport:
    """Fit a phasor ellipse + write two dual-threshold masks per channel,
    across every ``.h5`` in ``h5_paths``.

    Per-dataset isolation mirrors :func:`batch_compute_phasor`: a missing
    file, a corrupted HDF5 header, or any unexpected exception is recorded
    as a per-dataset ``failed`` item and the batch continues. Per-channel
    failures (degenerate fit, write error, missing decay/phasor) are
    recorded on the dataset's item and never abort sibling channels.

    Args:
        h5_paths: Datasets to process.
        channels: Channel names to fit on each dataset. Missing channels
            within an individual dataset are skipped; an entirely-missing
            channel set yields ``skipped_no_changes`` for that dataset.
        t_fit: Intensity threshold defining the GMM fit subset.
        t_mask_a, t_mask_b: Intensity thresholds applied to the two
            output masks after the ellipse-membership step.
        suffix_a, suffix_b: Suffixes appended to each channel name to
            form the two mask names. Both must be non-empty and must
            differ from each other (validated up-front).
        ensure_phasor: When True (default), compute
            ``/phasor/<ch>/{g, s}`` and the wavelet-filtered pair on the
            fly when they are missing — using the same primitives
            ``batch_compute_phasor`` does. When False, channels lacking
            phasor maps are skipped with reason ``"phasor not computed"``.
        progress_callback: Invoked once per dataset, in input order,
            after the dataset's item is fully classified.
        cancel_check: Optional callable polled between datasets. When
            it returns True the loop breaks and the report contains only
            the items processed so far.

    Returns:
        A :class:`BatchPhasorReport` whose items list is ``<=
        len(h5_paths)`` (equal when not cancelled).

    Raises:
        ValueError: when ``channels`` is empty, ``suffix_a`` /
            ``suffix_b`` is empty, or ``suffix_a == suffix_b``. Raised
            before any I/O.
    """
    # ── Input validation (no I/O) ──────────────────────────────────────
    channels_tuple = tuple(channels)
    if not channels_tuple:
        raise ValueError("channels must be non-empty")
    if suffix_a == "" or suffix_b == "":
        raise ValueError("suffix must be non-empty")
    if suffix_a == suffix_b:
        raise ValueError("suffixes must differ")

    repo = Hdf5DatasetRepository()
    results: list[BatchPhasorItemResult] = []

    for h5_path in h5_paths:
        if cancel_check is not None and cancel_check():
            break
        item = _process_one_dataset(
            h5_path=Path(h5_path),
            repo=repo,
            channels=channels_tuple,
            t_fit=t_fit,
            t_mask_a=t_mask_a,
            t_mask_b=t_mask_b,
            suffix_a=suffix_a,
            suffix_b=suffix_b,
            ensure_phasor=ensure_phasor,
        )
        results.append(item)
        if progress_callback is not None:
            progress_callback(item)

    return BatchPhasorReport(items=tuple(results))


def _process_one_dataset(
    *,
    h5_path: Path,
    repo: Hdf5DatasetRepository,
    channels: tuple[str, ...],
    t_fit: float,
    t_mask_a: float,
    t_mask_b: float,
    suffix_a: str,
    suffix_b: str,
    ensure_phasor: bool,
) -> BatchPhasorItemResult:
    """Run the fit + mask write loop for one dataset.

    Catches dataset-level exceptions (missing file, bad HDF5) and
    per-channel exceptions independently. Returns a classified
    :class:`BatchPhasorItemResult`.
    """
    # Open the store. A missing or corrupted file fails the whole item.
    try:
        store = DatasetStore(h5_path)
        if not store.exists():
            raise FileNotFoundError(f"file not found: {h5_path}")
        handle = repo.open(h5_path)
    except Exception as exc:  # noqa: BLE001 — orchestrator never raises per-item
        return BatchPhasorItemResult(
            h5_path=h5_path,
            status="failed",
            error=f"open failed: {exc}",
        )

    session = Session()
    session.set_dataset(handle)

    try:
        channel_names = list(store.metadata.get("channel_names", []) or [])
    except Exception as exc:  # noqa: BLE001
        return BatchPhasorItemResult(
            h5_path=h5_path,
            status="failed",
            error=f"metadata read failed: {exc}",
        )

    # The ``channels`` argument is the request; the dataset's
    # ``channel_names`` is what's actually present. We process the
    # requested order, classifying per-channel.
    processed: list[str] = []
    skipped: dict[str, str] = {}
    errors: dict[str, str] = {}

    # Lazily build the on-the-fly compute use case — only matters when
    # ``ensure_phasor`` is True AND at least one channel lacks phasor.
    phasor_uc: ComputePhasor | None = None

    try:
        for channel in channels:
            # ── Collision check (defense-in-depth for callers like the CLI). ──
            mask_a_name = f"{channel}{suffix_a}"
            mask_b_name = f"{channel}{suffix_b}"
            collide_name: str | None = None
            if mask_a_name in channel_names:
                collide_name = mask_a_name
            elif mask_b_name in channel_names:
                collide_name = mask_b_name
            if collide_name is not None:
                errors[channel] = (
                    f"mask name collides with channel '{collide_name}'"
                )
                continue

            # ── Presence check: channel + /decay/<ch>. ──
            if channel not in channel_names or not _has_path(
                h5_path, f"decay/{channel}"
            ):
                skipped[channel] = "channel not present"
                continue

            # ── Resolve phasor source: unfiltered g/s only. ──
            # The manual recipe in the workflow's brainstorm is explicit
            # about using the unfiltered phasor for both the GMM fit and
            # the mask application. We never read the wavelet-filtered
            # pair here even when it's on disk.
            if not _unfiltered_phasor_available(h5_path, channel):
                if not ensure_phasor:
                    skipped[channel] = "phasor not computed"
                    continue
                # Compute on the fly. Build the use case the first time
                # we need it — keeps the common path (phasor already on
                # disk) free of needless construction. The wavelet step
                # is intentionally NOT invoked: the workflow reads
                # unfiltered g/s.
                if phasor_uc is None:
                    phasor_uc = ComputePhasor(repo, session)
                try:
                    phasor_uc.execute(channel=channel, harmonic=1, view_bin=1)
                except Exception as exc:  # noqa: BLE001 — per-channel isolation
                    errors[channel] = f"compute_phasor: {exc}"
                    continue

            # ── Read phasor + decay. ──
            try:
                g_map = store.read_array(f"phasor/{channel}/g")
                s_map = store.read_array(f"phasor/{channel}/s")
                decay = store.read_decay(channel)
            except Exception as exc:  # noqa: BLE001
                errors[channel] = f"read failed: {exc}"
                continue

            intensity_map = decay.sum(axis=-1)

            # ── Fit + apply masks. ValueError → channel-level error. ──
            try:
                result = fit_phasor_ellipse_and_apply_masks(
                    g_map, s_map, intensity_map,
                    t_fit=t_fit,
                    t_mask_a=t_mask_a,
                    t_mask_b=t_mask_b,
                )
            except ValueError as exc:
                # Routes both the empty-subset case and the
                # degenerate-fit case to errors with the actual message.
                errors[channel] = str(exc)
                continue

            # ── Write masks. Defensive binarize (U1 already does it). ──
            # First write: a failure here means no mask landed for this
            # channel. Second write: a failure leaves the first mask on
            # disk; the channel still does NOT join `processed`.
            mask_a = (result.mask_a > 0).astype(np.uint8)
            mask_b = (result.mask_b > 0).astype(np.uint8)
            try:
                store.write_mask(mask_a_name, mask_a)
            except Exception as exc:  # noqa: BLE001
                errors[channel] = f"write failed: {exc}"
                continue
            try:
                store.write_mask(mask_b_name, mask_b)
            except Exception as exc:  # noqa: BLE001
                errors[channel] = f"write failed: {exc}"
                continue

            processed.append(channel)
    except Exception as exc:  # noqa: BLE001
        # Unexpected dataset-level failure mid-loop. Preserve whatever
        # per-channel state we accumulated; classify the item as failed.
        logger.exception(
            "batch_fit_phasor_masks unexpected failure on %s", h5_path,
        )
        return BatchPhasorItemResult(
            h5_path=h5_path,
            status="failed",
            processed=tuple(processed),
            skipped=skipped,
            errors=errors,
            error=f"unexpected failure: {exc}",
        )

    status = _classify_status(
        channels=list(channels),
        processed=processed,
        errors=errors,
        skipped=skipped,
    )

    return BatchPhasorItemResult(
        h5_path=h5_path,
        status=status,
        processed=tuple(processed),
        skipped=skipped,
        errors=errors,
    )
