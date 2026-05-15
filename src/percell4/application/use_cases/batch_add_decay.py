"""Use case: batch append of TCSPC ``.bin`` files to many existing ``.h5`` datasets.

Thin orchestration around :func:`add_decay_to_dataset`. Per item, the
orchestrator:

1. Writes the per-channel FLIM calibration (``flim_frequency_mhz``,
   ``flim_cal_phase_<ch>``, ``flim_cal_mod_<ch>``) to ``/metadata`` via
   :meth:`DatasetStore.set_metadata` — *before* the decay write, so any later
   ``compute_phasor`` reads correct values even if the decay step fails.
2. Calls :func:`add_decay_to_dataset` with the per-batch tile/orientation
   settings and the per-item paths.
3. Translates the returned :class:`AppendReport` into a :class:`BatchItemResult`
   with a clean ``succeeded | failed | skipped_no_changes | cancelled | not_run``
   status.

The orchestrator is **pure-Python and Session-free** — no Qt, no
``CellDataModel``. The dialog post-processes each result to update an active
``Session.dataset.metadata`` in place; that responsibility never leaks into
this module.

The batch is *not* a transaction. Per-item failures isolate and the loop
continues. The returned :class:`BatchAppendReport` carries truthful partial
state — succeeded items committed, failed items annotated, cancelled items
flagged — for the caller to render.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from percell4.application.use_cases.add_decay_to_dataset import (
    AppendReport,
    add_decay_to_dataset,
)
from percell4.domain.io.calibration_csv import (
    BatchCalibration,
    ChannelCalibration,
    validate_frequency_consistency,
)
from percell4.domain.io.cross_format import IntensityChannel
from percell4.domain.io.models import (
    CrossFormatRule,
    FlimConfig,
    TileConfig,
    TokenConfig,
)
from percell4.store import DatasetStore

# ── Result + report dataclasses ─────────────────────────────────────────


@dataclass(frozen=True)
class BatchAppendItem:
    """One ``(.h5, source_dir, calibration)`` triple in a batch.

    ``source_dir`` is **the per-dataset group folder** — the immediate
    subfolder of the user-picked source root that contains this dataset's
    ``.bin`` files. It is **not** the source root itself; the dialog
    extracts one group folder per selected dataset and constructs one
    ``BatchAppendItem`` from each pairing row.

    ``calibration`` maps channel name → :class:`ChannelCalibration` for
    every channel that should be written to ``/metadata``. Callers will
    typically slice :class:`BatchCalibration` by dataset stem to populate
    this field.
    """

    h5_path: Path
    source_dir: Path
    calibration: dict[str, ChannelCalibration]


# Status string set — kept narrow so callers can switch over it exhaustively.
ITEM_STATUSES: tuple[str, ...] = (
    "succeeded",
    "failed",
    "skipped_no_changes",
    "cancelled",
    "not_run",
)


@dataclass(frozen=True)
class BatchItemResult:
    """Outcome of one item in a batch run."""

    item: BatchAppendItem
    status: str  # one of ITEM_STATUSES
    append_report: AppendReport | None = None
    error: str | None = None


@dataclass(frozen=True)
class BatchAppendReport:
    """Aggregated result of a batch append run."""

    items: tuple[BatchItemResult, ...] = ()

    def by_status(self, status: str) -> tuple[BatchItemResult, ...]:
        return tuple(r for r in self.items if r.status == status)


# ── Validation ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BatchValidationReport:
    """Pure-data result of pre-flight validation."""

    pairing_errors: tuple[str, ...] = ()
    csv_coverage_errors: tuple[str, ...] = ()
    frequency_consistency_errors: tuple[str, ...] = ()
    source_dir_uniqueness_errors: tuple[str, ...] = ()
    decay_collision_warnings: tuple[str, ...] = ()

    @property
    def is_passing(self) -> bool:
        """True iff no blocking errors. Warnings do not gate Run."""
        return not (
            self.pairing_errors
            or self.csv_coverage_errors
            or self.frequency_consistency_errors
            or self.source_dir_uniqueness_errors
        )


def validate_batch_inputs(
    items: list[BatchAppendItem],
    *,
    channel_names_per_item: dict[Path, list[str]],
    force: bool,
    existing_decay_per_item: dict[Path, set[str]],
) -> BatchValidationReport:
    """Pre-flight validation — pure function, no I/O.

    The dialog is responsible for reading ``channel_names_per_item`` and
    ``existing_decay_per_item`` from each store's metadata before calling
    this. Keeping the function I/O-free lets tests exercise it without
    HDF5 fixtures.
    """
    pairing_errors: list[str] = []
    coverage_errors: list[str] = []
    freq_errors: list[str] = []
    uniqueness_errors: list[str] = []
    collision_warnings: list[str] = []

    if not items:
        pairing_errors.append("no datasets selected for the batch")

    # ── Pairing: items must have h5_path + source_dir + calibration set ──
    for item in items:
        if not item.h5_path:
            pairing_errors.append("an item is missing its .h5 path")
        if not item.source_dir:
            pairing_errors.append(
                f"{item.h5_path.name}: source folder not paired"
            )

    # ── source_dir uniqueness across items ──
    seen_dirs: dict[Path, list[Path]] = {}
    for item in items:
        seen_dirs.setdefault(item.source_dir, []).append(item.h5_path)
    for src, owners in seen_dirs.items():
        if len(owners) > 1:
            names = ", ".join(p.name for p in owners)
            uniqueness_errors.append(
                f"group folder {src} is paired with multiple datasets: {names}"
            )

    # ── CSV coverage: every (item, channel) needs a calibration row ──
    for item in items:
        channel_names = channel_names_per_item.get(item.h5_path, [])
        if not channel_names:
            coverage_errors.append(
                f"{item.h5_path.name}: no /intensity channels in dataset "
                "(channel_names empty)"
            )
            continue
        for ch in channel_names:
            if ch not in item.calibration:
                coverage_errors.append(
                    f"{item.h5_path.name}: missing calibration for channel {ch!r}"
                )

    # ── frequency_mhz consistency within each dataset ──
    for item in items:
        freqs = {ch: cal.frequency_mhz for ch, cal in item.calibration.items()}
        if len(set(freqs.values())) > 1:
            freq_str = ", ".join(f"{ch}={f}" for ch, f in sorted(freqs.items()))
            freq_errors.append(
                f"{item.h5_path.name}: frequency_mhz differs across channels "
                f"({freq_str})"
            )

    # ── /decay collision warnings (only when force=False) ──
    if not force:
        for item in items:
            existing = existing_decay_per_item.get(item.h5_path, set())
            collisions = sorted(existing & set(item.calibration.keys()))
            if collisions:
                collision_warnings.append(
                    f"{item.h5_path.name}: {len(collisions)} channel(s) already "
                    f"have /decay layers and will be skipped: {', '.join(collisions)}"
                )

    return BatchValidationReport(
        pairing_errors=tuple(pairing_errors),
        csv_coverage_errors=tuple(coverage_errors),
        frequency_consistency_errors=tuple(freq_errors),
        source_dir_uniqueness_errors=tuple(uniqueness_errors),
        decay_collision_warnings=tuple(collision_warnings),
    )


def validate_calibration_csv_against_selection(
    cal: BatchCalibration,
    selected_dataset_stems: list[str],
) -> list[str]:
    """Cross-check a parsed CSV against the user's selected dataset stems.

    Returns one error per selected dataset that has no row in the CSV.
    Extra rows in the CSV (datasets not in the selection) are allowed
    silently — one master CSV may cover multiple batches.
    """
    csv_stems = set(cal.datasets())
    errors: list[str] = []
    for stem in selected_dataset_stems:
        if stem not in csv_stems:
            errors.append(
                f"no calibration rows for dataset {stem!r} in CSV"
            )
    # Also surface frequency-consistency issues here so callers see one
    # combined error list before constructing BatchAppendItems.
    errors.extend(validate_frequency_consistency(cal))
    return errors


# ── Main entry point ────────────────────────────────────────────────────


def batch_add_decay(
    items: list[BatchAppendItem],
    *,
    token_config: TokenConfig,
    tile_config: TileConfig,
    flim_config: FlimConfig,
    cross_format_rule: CrossFormatRule,
    rotate_k: int = 0,
    flip_axis: int | None = None,
    spatial_bin: int = 1,
    force: bool = False,
    progress_callback: Callable[[BatchAppendItem, BatchItemResult], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    intensity_channels_overrides: dict[Path, list[IntensityChannel]] | None = None,
) -> BatchAppendReport:
    """Run a batch TCSPC append over ``items``.

    See module docstring for the per-item flow. Returns a
    :class:`BatchAppendReport`; never raises on per-item errors.

    ``intensity_channels_overrides`` lets callers supply explicit
    :class:`IntensityChannel` records keyed by ``h5_path`` for datasets
    whose channel names don't parse via the digit-suffix heuristic (e.g.
    semantic names like ``mNG``, ``mTQ2``). When omitted for an item,
    the orchestrator builds default ``IntensityChannel`` records from
    each store's ``channel_names`` and ``channel_base_stems`` metadata —
    the same path :func:`add_decay_to_dataset` takes internally when its
    own ``intensity_channels`` parameter is ``None``. Constructing them
    here keeps the per-item scope explicit (see batch-compress lessons).
    """
    results: list[BatchItemResult] = []
    cancelled_from: int | None = None

    for index, item in enumerate(items):
        # ── Cancellation between items ──
        if cancel_check is not None and cancel_check():
            cancelled_from = index
            break

        try:
            store = DatasetStore(item.h5_path)

            # Step 1 — write calibration to /metadata BEFORE the decay
            # write, so any later compute_phasor reads correct values.
            cal_attrs = _calibration_attrs(item.calibration)
            store.set_metadata(cal_attrs)

            # Step 2 — build per-item IntensityChannel records (or use the
            # caller's override). Per-item because channels are owned by
            # each .h5; never reuse a list across the batch.
            metadata = store.metadata
            channel_names = list(metadata.get("channel_names", []))
            if not channel_names:
                result = BatchItemResult(
                    item=item,
                    status="failed",
                    error=(
                        "no /intensity channels in dataset "
                        "(channel_names empty)"
                    ),
                )
                results.append(result)
                if progress_callback:
                    progress_callback(item, result)
                continue

            override = (
                intensity_channels_overrides.get(item.h5_path)
                if intensity_channels_overrides
                else None
            )
            intensity_channels = override or _build_intensity_channels(
                channel_names,
                base_stems=list(metadata.get("channel_base_stems", [])),
            )

            # Step 3 — call the existing decay write engine. Pass
            # progress_callback=None deliberately; per-channel chatter
            # would not improve the per-item progress UX.
            report = add_decay_to_dataset(
                h5_path=item.h5_path,
                source_dir=item.source_dir,
                token_config=token_config,
                tile_config=tile_config,
                flim_config=flim_config,
                cross_format_rule=cross_format_rule,
                rotate_k=rotate_k,
                flip_axis=flip_axis,
                spatial_bin=spatial_bin,
                force=force,
                progress_callback=None,
                intensity_channels=intensity_channels,
            )
            result = _classify_append_report(item, report)
        except Exception as exc:  # noqa: BLE001 — orchestrator must never raise on a per-item failure
            result = BatchItemResult(item=item, status="failed", error=str(exc))

        results.append(result)
        if progress_callback:
            progress_callback(item, result)

    # Mark the remaining items as cancelled in the order they would have run.
    if cancelled_from is not None:
        for item in items[cancelled_from:]:
            results.append(BatchItemResult(item=item, status="cancelled"))

    return BatchAppendReport(items=tuple(results))


# ── Helpers ─────────────────────────────────────────────────────────────


def _calibration_attrs(
    calibration: dict[str, ChannelCalibration],
) -> dict[str, float]:
    """Flatten a per-channel calibration dict into the on-disk attr shape.

    Mirrors :func:`add_layer_dialog._tcspc_persist_flim_metadata` — the
    same flat keys the single-dataset flow writes today, so
    :func:`compute_phasor` (which reads them back) needs no change. The
    frequency value is a single scalar at the top level; per-channel
    rows must agree (callers should run
    :func:`validate_frequency_consistency` before reaching this point).
    """
    if not calibration:
        return {}
    attrs: dict[str, float] = {}
    # Take frequency from any channel — agreement is a pre-flight invariant.
    any_cal = next(iter(calibration.values()))
    attrs["flim_frequency_mhz"] = float(any_cal.frequency_mhz)
    for channel, cal in calibration.items():
        attrs[f"flim_cal_phase_{channel}"] = float(cal.phase)
        attrs[f"flim_cal_mod_{channel}"] = float(cal.modulation)
    return attrs


def _build_intensity_channels(
    channel_names: list[str],
    *,
    base_stems: list[str],
) -> list[IntensityChannel]:
    """Construct ``IntensityChannel`` records via the digit-suffix heuristic.

    Mirrors :func:`add_decay_to_dataset._extract_channel_token` — names
    like ``ch00`` produce token ``"00"``. Semantic names like ``mNG``
    or ``mTQ2`` produce an empty token; the matcher will then yield
    zero bindings for those channels unless the caller supplied an
    explicit override via ``intensity_channels_overrides``.
    """
    out: list[IntensityChannel] = []
    for i, name in enumerate(channel_names):
        m = re.search(r"(\d+)$", name)
        token = m.group(1) if m else ""
        base_stem = base_stems[i] if i < len(base_stems) else None
        out.append(IntensityChannel(name=name, token=token, base_stem=base_stem))
    return out


def _classify_append_report(
    item: BatchAppendItem, report: AppendReport
) -> BatchItemResult:
    """Translate an :class:`AppendReport` into a :class:`BatchItemResult` status."""
    if report.written:
        return BatchItemResult(item=item, status="succeeded", append_report=report)
    # report.written is empty — distinguish skip-from-collision vs hard failure.
    error_messages = list(report.errors.values())
    if error_messages and all(
        "already exists" in msg.lower() for msg in error_messages
    ):
        return BatchItemResult(
            item=item, status="skipped_no_changes", append_report=report
        )
    # Surface the first error message so the dialog has something useful to
    # show. If ``add_decay_to_dataset`` produced neither ``written`` channels
    # nor ``errors`` entries, the matcher most likely returned zero bindings —
    # that case lands in ``report.unmatched`` / ``report.ambiguous``, which we
    # explicitly surface here. Without this, semantic channel names (mNG,
    # mTQ2, CA-SiR) produce the cryptic "wrote no channels and reported no
    # errors" message.
    first_error: str | None = next(iter(error_messages), None)
    if first_error is None:
        unmatched = getattr(report, "unmatched", ()) or ()
        ambiguous = getattr(report, "ambiguous", ()) or ()
        if unmatched or ambiguous:
            parts = []
            if unmatched:
                parts.append(f"{len(unmatched)} .bin unmatched")
            if ambiguous:
                parts.append(f"{len(ambiguous)} .bin ambiguous")
            first_error = (
                "no .bin files matched any channel ("
                + ", ".join(parts)
                + "). Channel names may not parse via the digit-suffix "
                "heuristic (e.g. mNG, mTQ2, CA-SiR) — see "
                "intensity_channels_overrides on the batch orchestrator."
            )
        else:
            first_error = "add_decay_to_dataset wrote no channels and reported no errors"
    return BatchItemResult(
        item=item, status="failed", append_report=report, error=first_error
    )
