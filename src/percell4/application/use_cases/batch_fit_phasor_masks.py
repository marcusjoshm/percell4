"""Use case: batch fit-ellipse + dual-threshold phasor-masks across ``.h5`` files.

Per-(dataset, channel) orchestrator backing the Automated Phasor-Masks
Workflow (``docs/plans/2026-05-27-001-feat-phasor-masks-workflow-plan.md``)
and the shared-ROI extension
(``docs/plans/2026-05-27-002-feat-phasor-masks-shared-roi-plan.md``).

Execution shape — **interleaved per-source-group**:

1. The caller declares per-dataset ROI assignments via ``roi_sources``
   (``Mapping[Path, Path | None]``). ``None`` (or absent key) means
   "self-fitting"; a non-``None`` value names the resolved path of the
   source dataset whose ellipse this target should reuse.
2. Iterate ``paths`` in the user's input order. For each self-fitting
   path, process it (fit + cache + apply own masks), then **immediately**
   process every later target in the input order that points at it.
   When that group is done, advance to the next self-fitting path.
3. Sources are always fitted strictly before their dependents
   (requirement R6) while cancellation produces complete per-group
   results — never a half-cohort where a source has masks but its
   dependents don't.

Per-dataset processing (source OR target):

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
5. **Self-fitting:** calls :func:`fit_phasor_ellipse` (caching the
   :class:`PhasorEllipseFit` at ``roi_cache[(path, channel)]``), then
   :func:`apply_ellipse_masks`. Empty-fit-subset and degenerate-fit
   ``ValueError`` s are routed to ``errors[ch]`` with the actual
   exception message — no mask is written for that channel, and no
   cache entry is populated.
   **Target:** looks up ``roi_cache[(source_path, channel)]``. On a
   cache miss (the source's fit for ``channel`` failed) the channel
   lands in ``errors[ch]`` with a "ROI source <source.name> fit failed"
   message, no mask is written. On a cache hit, calls
   :func:`apply_ellipse_masks` with the cached fit against the target's
   own phasor map.
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
from collections.abc import Callable, Iterable, Mapping, Sequence
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
    PhasorEllipseFit,
    apply_ellipse_masks,
    fit_phasor_ellipse,
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
    roi_sources: Mapping[Path, Path | None] | None = None,
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

    Shared-ROI semantics: when ``roi_sources[target] is None`` (or
    ``target not in roi_sources``), the dataset self-fits. When
    ``roi_sources[target] is source`` and ``source != target``, the
    target reuses the source's per-channel fitted ellipse (cached
    in-memory for the duration of this call). Self-references
    (``roi_sources[p] == p``) are normalized to ``None``.

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
        roi_sources: Per-dataset ROI assignment. ``None`` (the default)
            means "every dataset self-fits" — identical behavior to the
            pre-shared-ROI single-pass design. A mapping keyed by target
            path (after ``.resolve()``) with values either ``None``
            (self-fitting) or another path in ``h5_paths`` (use that
            source's ellipse).
        progress_callback: Invoked once per dataset, in *processing*
            order — sources first, then their dependents in input
            order, then the next source-group.
        cancel_check: Optional callable polled between datasets and
            between targets within a source-group. When it returns True
            the loop breaks and the report contains only the items
            processed so far.

    Returns:
        A :class:`BatchPhasorReport` whose items list is ``<=
        len(h5_paths)`` (equal when not cancelled).

    Raises:
        ValueError: when ``channels`` is empty, ``suffix_a`` /
            ``suffix_b`` is empty, ``suffix_a == suffix_b``, or any
            ``roi_sources`` key / non-``None`` value is missing from
            ``h5_paths``, or chain detected (a path is both a target
            and a source). All raised before any I/O.
    """
    # ── Input validation (no I/O) ──────────────────────────────────────
    channels_tuple = tuple(channels)
    if not channels_tuple:
        raise ValueError("channels must be non-empty")
    if suffix_a == "" or suffix_b == "":
        raise ValueError("suffix must be non-empty")
    if suffix_a == suffix_b:
        raise ValueError("suffixes must differ")

    # ── Input normalization (no I/O) ───────────────────────────────────
    # Resolve all paths so that relative-vs-absolute path forms collapse
    # to the same canonical key for the roi_sources mapping. This mirrors
    # the dialog's add-time resolve() and protects script callers.
    resolved_paths: list[Path] = [Path(p).resolve() for p in h5_paths]
    paths_set: set[Path] = set(resolved_paths)

    # Normalize roi_sources: resolve keys/values, then collapse
    # self-references (roi_sources[p] == p) to None.
    raw_roi_sources: Mapping[Path, Path | None] = roi_sources or {}
    resolved_roi_sources: dict[Path, Path | None] = {}
    for k, v in raw_roi_sources.items():
        rk = Path(k).resolve()
        rv: Path | None = Path(v).resolve() if v is not None else None
        if rv == rk:
            rv = None
        resolved_roi_sources[rk] = rv

    # ── Validate roi_sources ───────────────────────────────────────────
    # Every key must be a known path.
    for k in resolved_roi_sources:
        if k not in paths_set:
            raise ValueError(
                f"roi_sources key {k} is not in h5_paths"
            )
    # Every non-None value must be a known path.
    for k, v in resolved_roi_sources.items():
        if v is not None and v not in paths_set:
            raise ValueError(
                f"roi_sources value {v} (for target {k}) is not in h5_paths"
            )
    # No path may appear as both a target (key with non-None value) AND
    # a source (non-None value somewhere else). Sources must be
    # self-fitting; chains of depth > 1 are not supported.
    targets_with_source: set[Path] = {
        k for k, v in resolved_roi_sources.items() if v is not None
    }
    sources_in_use: set[Path] = {
        v for v in resolved_roi_sources.values() if v is not None
    }
    chain_overlap = targets_with_source & sources_in_use
    if chain_overlap:
        offender = next(iter(chain_overlap))
        raise ValueError(
            f"chain detected: {offender} is both a target and a "
            f"source — sources must be self-fitting"
        )

    repo = Hdf5DatasetRepository()
    results: list[BatchPhasorItemResult] = []

    # In-memory ROI cache keyed by (source_path_resolved, channel_name).
    # Populated when a self-fitting dataset's fit succeeds; consulted by
    # targets that point at the source; garbage-collected when this
    # function returns.
    roi_cache: dict[tuple[Path, str], PhasorEllipseFit] = {}

    processed_paths: set[Path] = set()

    # ── Interleaved per-source-group loop ──────────────────────────────
    for path in resolved_paths:
        if path in processed_paths:
            # Already handled when its source-group ran (it was a target
            # of an earlier source).
            continue
        if cancel_check is not None and cancel_check():
            break

        path_source = resolved_roi_sources.get(path)
        if path_source is None:
            # Self-fitting: fit, cache geometry, apply own masks.
            item = _process_one_dataset(
                h5_path=path,
                repo=repo,
                channels=channels_tuple,
                t_fit=t_fit,
                t_mask_a=t_mask_a,
                t_mask_b=t_mask_b,
                suffix_a=suffix_a,
                suffix_b=suffix_b,
                ensure_phasor=ensure_phasor,
                roi_cache=roi_cache,
                roi_source=None,
            )
            results.append(item)
            processed_paths.add(path)
            if progress_callback is not None:
                progress_callback(item)

            # Immediately process every later-in-list-order target that
            # points at this path. Cancel check between each target.
            for target in resolved_paths:
                if target in processed_paths:
                    continue
                if resolved_roi_sources.get(target) != path:
                    continue
                if cancel_check is not None and cancel_check():
                    break
                t_item = _process_one_dataset(
                    h5_path=target,
                    repo=repo,
                    channels=channels_tuple,
                    t_fit=t_fit,
                    t_mask_a=t_mask_a,
                    t_mask_b=t_mask_b,
                    suffix_a=suffix_a,
                    suffix_b=suffix_b,
                    ensure_phasor=ensure_phasor,
                    roi_cache=roi_cache,
                    roi_source=path,
                )
                results.append(t_item)
                processed_paths.add(target)
                if progress_callback is not None:
                    progress_callback(t_item)
        else:
            # path is a target whose source has NOT been reached yet.
            # Validation rejects chains, so the only way to land here is
            # if the source appears later in the list. But the
            # interleaved loop processes a source's dependents in the
            # same pass when the source is encountered — so by the time
            # we reach the target's own slot it would already be in
            # processed_paths. If we're here, something is structurally
            # off; log and skip rather than crash.
            logger.warning(
                "batch_fit_phasor_masks: target %s with source %s reached"
                " before its source was processed; skipping",
                path, path_source,
            )

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
    roi_cache: dict[tuple[Path, str], PhasorEllipseFit],
    roi_source: Path | None,
) -> BatchPhasorItemResult:
    """Run the fit + mask write loop for one dataset.

    When ``roi_source is None`` the dataset self-fits: for each channel
    the use case runs :func:`fit_phasor_ellipse`, caches the resulting
    :class:`PhasorEllipseFit` at ``roi_cache[(h5_path, channel)]`` on
    success, then applies the ellipse against the dataset's own phasor
    via :func:`apply_ellipse_masks`.

    When ``roi_source is not None`` the dataset is a target: for each
    channel the use case looks up ``roi_cache[(roi_source, channel)]``.
    On a cache miss (the source's fit for ``channel`` raised), routes
    the channel to ``errors`` with a "ROI source ... fit failed"
    message. On a hit, applies the cached fit against the *target's*
    own phasor map.

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

            # ── Fit (self) or look up cached fit (target). ──
            if roi_source is None:
                # Self-fitting: try the fit, cache geometry on success.
                try:
                    # Module-level lookup so monkeypatches on
                    # ``batch_fit_phasor_masks.fit_phasor_ellipse`` take
                    # effect (per the U2 test surface).
                    fit = fit_phasor_ellipse(
                        g_map, s_map, intensity_map, t_fit=t_fit,
                    )
                except ValueError as exc:
                    # Routes both empty-subset and degenerate-fit cases
                    # to errors with the actual message. Cache entry for
                    # (h5_path, channel) is NOT populated → dependents
                    # will hit the cache-miss branch.
                    errors[channel] = str(exc)
                    continue
                roi_cache[(h5_path, channel)] = fit
            else:
                # Target: consult cache. Miss → source's fit for this
                # channel failed; route to errors with a source-failed
                # message naming the source path.
                fit = roi_cache.get((roi_source, channel))
                if fit is None:
                    errors[channel] = (
                        f"ROI source {roi_source.name} fit failed: "
                        f"see source's item for details"
                    )
                    continue

            # ── Apply the (fitted or cached) ellipse to this dataset's
            # own phasor maps + intensity. ValueError → channel-level
            # error (shape mismatch only — apply does not re-validate
            # degeneracy). ──
            try:
                applied = apply_ellipse_masks(
                    g_map, s_map, intensity_map, fit,
                    t_mask_a=t_mask_a,
                    t_mask_b=t_mask_b,
                )
            except ValueError as exc:
                errors[channel] = str(exc)
                continue

            # ── Write masks. Defensive binarize (U1 already does it). ──
            # First write: a failure here means no mask landed for this
            # channel. Second write: a failure leaves the first mask on
            # disk; the channel still does NOT join `processed`.
            mask_a = (applied.mask_a > 0).astype(np.uint8)
            mask_b = (applied.mask_b > 0).astype(np.uint8)
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
