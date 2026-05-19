"""Use case: batch export of cached phasors as PNG across many .h5 datasets.

Thin orchestration around
:func:`percell4.application.phasor_render.render_phasor_png`. Per
dataset, for every channel under ``/phasor/<ch>``, writes a raw phasor
PNG (from ``g``/``s``) and — when both wavelet-filtered maps exist — a
separate filtered PNG (from ``g_filtered``/``s_filtered``) into
``output_dir`` using the flat ``<stem>_<ch>_phasor[_filtered].png``
layout.

Sequential, single-process. Per-dataset *and* per-channel failures
isolate: one bad channel never aborts the dataset, one bad dataset
never aborts the batch.

Alignment precondition (load-bearing — also restated here because this
is the canonical batch read site): :class:`LoadCachedPhasor` *trusts
but does not enforce* that a non-empty ``/phasor/<ch>`` is spatially
aligned with the current ``/decay/<ch>`` — that invariant is normally
maintained by the live GUI/Session recompute + TCSPC-reimport-clears-
phasor chain. A batch CLI reads arbitrary on-disk ``.h5`` files (older
writers, interrupted writes, hand-edited datasets) where the invariant
can be violated. This use case therefore *enforces* it: when
``decay.sum(-1)`` and ``g`` have mismatched pixel counts, that is a
stale-cache signal recorded as a per-channel **error** ("phasor likely
stale, run batch_phasor"), never a silent unweighted render.

Phasor compute is out of scope: channels with no ``/phasor/<ch>/g``
have nothing to export and are reported as skipped (run
``batch_phasor`` first), mirroring ``batch_export``'s
"compute is a separate CLI" stance.

Pattern source: ``src/percell4/application/use_cases/batch_export_images.py``.
Canonical read source: ``src/percell4/application/use_cases/load_cached_phasor.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from percell4.application.phasor_render import (
    RenderOutcome,
    render_phasor_png,
)
from percell4.store import DatasetStore

logger = logging.getLogger(__name__)


# ── Result + report dataclasses ─────────────────────────────────────────


ITEM_STATUSES: tuple[str, ...] = (
    "succeeded",
    "skipped_no_changes",
    "failed",
)


@dataclass(frozen=True)
class BatchPhasorExportItemResult:
    """Outcome of one dataset in a batch phasor-export run.

    ``skipped`` / ``errors`` are split (mirroring ``batch_phasor``'s
    ``BatchPhasorItemResult``) so the CLI can print the two categories
    distinctly:

    * ``skipped[<key>]`` — an output deliberately not produced
      (channel has no phasor; asymmetric filtered cache → no filtered
      PNG).
    * ``errors[<key>]`` — an output that *should* have been produced
      but failed (read raised, ``decay``/``g`` shape mismatch, render
      raised).
    * ``rendered_empty`` — output filenames whose phasor existed but
      had zero valid pixels (PNG written, but the empty signal is
      recorded so the user is not silently handed blank plots).

    ``error`` is the dataset-level failure message when the dataset
    could not even be opened/enumerated.
    """

    h5_path: Path
    status: str  # one of ITEM_STATUSES
    files_written: int = 0
    channels_exported: tuple[str, ...] = ()
    skipped: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    rendered_empty: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class BatchPhasorExportReport:
    """Aggregated result of a batch phasor-export run."""

    items: tuple[BatchPhasorExportItemResult, ...] = ()

    @property
    def total_succeeded(self) -> int:
        return sum(1 for r in self.items if r.status == "succeeded")

    @property
    def total_failed(self) -> int:
        return sum(1 for r in self.items if r.status == "failed")

    @property
    def total_skipped(self) -> int:
        return sum(
            1 for r in self.items if r.status == "skipped_no_changes"
        )

    @property
    def total_files_written(self) -> int:
        return sum(r.files_written for r in self.items)

    @property
    def total_rendered_empty(self) -> int:
        return sum(len(r.rendered_empty) for r in self.items)


# ── Main entry point ────────────────────────────────────────────────────


def batch_export_phasor(
    h5_paths: list[Path],
    *,
    output_dir: Path,
    progress_callback: (
        Callable[[BatchPhasorExportItemResult], None] | None
    ) = None,
) -> BatchPhasorExportReport:
    """Export every cached phasor from each ``.h5`` to PNG.

    Per dataset, writes ``<output_dir>/<h5_stem>_<ch>_phasor.png`` for
    every channel under ``/phasor/<ch>`` and
    ``<h5_stem>_<ch>_phasor_filtered.png`` for every channel that also
    has both ``g_filtered`` and ``s_filtered``. Flat layout; existing
    files at those paths are overwritten silently.

    Args:
        h5_paths: Datasets to export. Missing/unreadable paths are
            reported as failed items; the loop continues.
        output_dir: Target directory for the PNG outputs. Created by
            the renderer if missing.
        progress_callback: Invoked once per dataset after its
            :class:`BatchPhasorExportItemResult` is classified.

    Returns:
        A :class:`BatchPhasorExportReport` with one item per input path.
    """
    results: list[BatchPhasorExportItemResult] = []
    for h5_path in h5_paths:
        result = _process_one_dataset(
            h5_path=h5_path, output_dir=output_dir
        )
        results.append(result)
        if progress_callback is not None:
            progress_callback(result)
    return BatchPhasorExportReport(items=tuple(results))


def _read_optional(store: DatasetStore, path: str) -> np.ndarray | None:
    """Read an array, returning None when the path is absent."""
    try:
        return store.read_array(path)
    except KeyError:
        return None


def _process_one_dataset(
    *, h5_path: Path, output_dir: Path
) -> BatchPhasorExportItemResult:
    """Export every cached phasor of one dataset. Isolates failures."""
    store = DatasetStore(h5_path)
    stem = h5_path.stem
    exported: list[str] = []
    skipped: dict[str, str] = {}
    errors: dict[str, str] = {}
    rendered_empty: list[str] = []

    try:
        # One shared h5py handle (+ chunk cache) for the whole dataset
        # instead of an open/close per array read across every channel.
        with store.open_read():
            channels = store.list_groups("phasor")

            if not channels:
                return BatchPhasorExportItemResult(
                    h5_path=h5_path,
                    status="skipped_no_changes",
                    skipped={
                        "_dataset": "no /phasor groups (run batch_phasor)"
                    },
                )

            for ch in sorted(channels):
                try:
                    _export_channel(
                        store=store,
                        ch=ch,
                        stem=stem,
                        output_dir=output_dir,
                        exported=exported,
                        skipped=skipped,
                        errors=errors,
                        rendered_empty=rendered_empty,
                    )
                except Exception as exc:  # noqa: BLE001 — per-channel isolation
                    errors[ch] = f"unexpected: {exc}"
                    continue
    except Exception as exc:  # noqa: BLE001 — orchestrator never raises per-item
        return BatchPhasorExportItemResult(
            h5_path=h5_path,
            status="failed",
            error=f"open/enumerate failed: {exc}",
        )

    files_written = len(exported)
    if files_written > 0:
        status = "succeeded"
    elif errors:
        # Channels existed and at least one produced a genuine error
        # with no output anywhere — that is a failure, not a no-op.
        status = "failed"
    else:
        # Channels existed but were all skipped (no usable phasor).
        status = "skipped_no_changes"

    return BatchPhasorExportItemResult(
        h5_path=h5_path,
        status=status,
        files_written=files_written,
        channels_exported=tuple(exported),
        skipped=skipped,
        errors=errors,
        rendered_empty=tuple(rendered_empty),
    )


def _export_channel(
    *,
    store: DatasetStore,
    ch: str,
    stem: str,
    output_dir: Path,
    exported: list[str],
    skipped: dict[str, str],
    errors: dict[str, str],
    rendered_empty: list[str],
) -> None:
    """Export raw + (optional) filtered PNG for a single channel.

    Mutates ``skipped`` / ``errors`` / ``rendered_empty`` and appends
    to ``exported`` once per PNG actually written.
    """
    # Required raw maps. Missing g, or asymmetric g-without-s, => the
    # channel has no usable phasor: skip (per load_cached_phasor.py).
    g = _read_optional(store, f"phasor/{ch}/g")
    s = _read_optional(store, f"phasor/{ch}/s")
    if g is None:
        skipped[ch] = "no phasor/<ch>/g (run batch_phasor)"
        return
    if s is None:
        skipped[ch] = "asymmetric phasor cache: g present, s missing"
        return

    # Optional filtered maps — both required together. Asymmetric
    # filtered cache => treat as no filtered cache AND record a
    # structured skip for the filtered output (not log-only).
    g_filt = _read_optional(store, f"phasor/{ch}/g_filtered")
    s_filt = _read_optional(store, f"phasor/{ch}/s_filtered")
    if (g_filt is None) != (s_filt is None):
        skipped[f"{ch}_filtered"] = (
            "asymmetric wavelet cache: only one of "
            "g_filtered/s_filtered present"
        )
        g_filt = s_filt = None

    # Decay-derived intensity. Per the cross-layer-alignment learning,
    # intensity MUST come from decay.sum(-1), never /intensity[ch_idx].
    # Missing decay is a legitimate unweighted render.
    decay = _read_optional(store, f"decay/{ch}")
    intensity: np.ndarray | None
    if decay is None:
        intensity = None
    else:
        intensity = decay.sum(axis=-1).astype(np.float32)
        # Alignment enforcement: a decay/g pixel-count mismatch is a
        # stale-cache signal, not an unweighted-render opportunity.
        if intensity.size != g.size:
            errors[ch] = (
                "decay/phasor shape mismatch "
                f"(decay-derived intensity size {intensity.size} != "
                f"g size {g.size}); phasor likely stale, run "
                "batch_phasor"
            )
            return

    # Render raw and filtered as independent outputs: a failure of one
    # must not skip the other (the channel's two PNGs are unrelated
    # render calls). Per-output errors are keyed by the output name so
    # they are distinguishable from a whole-channel error.
    _render_output(
        f"{stem}_{ch}_phasor.png", g, s, intensity,
        output_dir, exported, errors, rendered_empty,
    )
    if g_filt is not None and s_filt is not None:
        _render_output(
            f"{stem}_{ch}_phasor_filtered.png", g_filt, s_filt, intensity,
            output_dir, exported, errors, rendered_empty,
        )


def _render_output(
    name: str,
    g: np.ndarray,
    s: np.ndarray,
    intensity: np.ndarray | None,
    output_dir: Path,
    exported: list[str],
    errors: dict[str, str],
    rendered_empty: list[str],
) -> None:
    """Render one PNG, isolating its failure from the channel's others."""
    try:
        outcome = render_phasor_png(
            g, s, out_path=output_dir / name, intensity=intensity
        )
    except Exception as exc:  # noqa: BLE001 — per-output isolation
        errors[name] = f"render failed: {exc}"
        return
    exported.append(name)
    if outcome is RenderOutcome.RENDERED_EMPTY:
        rendered_empty.append(name)
