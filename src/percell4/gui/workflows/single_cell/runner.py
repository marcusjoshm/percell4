"""Concrete runner for the single-cell thresholding workflow.

Subclasses :class:`BaseWorkflowRunner` and implements
``_phase_generator`` as a sequence of UNATTENDED :class:`PhaseRequest`
objects that drive the pure helpers in
:mod:`percell4.workflows.phases`:

    Phase 0 — compress(each ``tiff_pending`` entry)
    Phase 1 — segment(every dataset)
    For each configured thresholding round:
        Phase 3 — threshold_compute(every dataset)
        Phase 4 — apply_threshold_headless(every dataset)
    Phase 7 — measure(every dataset) → write staging parquet
    Phase 8 — export_run(aggregate → measurements.parquet + CSVs)

Phase 4 of the implementation plan uses the headless thresholding path
for every round. When Phase 5 lands, Phase 1 will yield an INTERACTIVE
request for the segmentation QC dialog in addition to the unattended
segment handler; when Phase 6 lands, the per-round "apply" phase will
be replaced by an INTERACTIVE threshold-QC queue. The runner itself
does not need to change shape for those phases — only the
``_phase_generator`` body does.

All failures are routed through :func:`phases.record_failure`, which
appends a :class:`FailureRecord` to the run metadata. Subsequent phases
skip datasets that have been marked failed; the export step rolls the
failures into ``run_config.json``. A misbehaving dataset never crashes
the run.
"""

from __future__ import annotations

import logging
from collections.abc import Generator

from percell4.adapters.cellpose import build_cellpose_model
from percell4.gui.workflows.base_runner import (
    BaseWorkflowRunner,
    PhaseKind,
    PhaseRequest,
    PhaseResult,
)
from percell4.store import DatasetStore
from percell4.workflows.failures import DatasetFailure
from percell4.workflows.models import (
    DatasetSource,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
)
from percell4.workflows.phases import (
    apply_threshold_headless,
    compress_one,
    config_needs_pixel_size,
    datasets_without_failures,
    export_run,
    measure_one,
    pick_existing_segmentation,
    record_failure,
    segment_one,
    threshold_compute_one,
    track_one,
    validate_compressed_dataset,
    write_staging_parquet,
)

logger = logging.getLogger(__name__)


class SingleCellThresholdingRunner(BaseWorkflowRunner):
    """Batch runner for the single-cell thresholding workflow.

    Phase 4 MVP: every phase is UNATTENDED (synchronous on the main
    thread). Cellpose runs inline — the UI freezes during segmentation
    of each dataset, but progress between datasets is visible via
    ``workflow_event`` signal emissions. Phase 8 (or a follow-up) will
    upgrade Cellpose to a ``Worker`` thread with cancel propagation.
    """

    def __init__(
        self,
        config: WorkflowConfig,
        metadata: RunMetadata,
        *,
        interactive_qc: bool = True,
        segmentation_overrides: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._metadata = metadata
        # Runtime caches populated during the run. Held here rather than
        # inside the generator so tests can inspect them.
        self._cellpose_model = None
        # Entries are replaced (not mutated) during Phase 0 to flip
        # tiff_pending → h5_existing with the real output path.
        self._working_entries = list(config.datasets)
        # When True (default), the runner yields INTERACTIVE PhaseRequest
        # objects for segmentation QC and threshold QC. When False,
        # those phases are replaced by the headless apply_threshold_headless
        # path — useful for unattended runs and for tests that don't
        # want to pump a Qt event loop through an interactive controller.
        self._interactive_qc = interactive_qc
        # Cross-phase state: Phase 3/5 compute stashes GroupingResult
        # per (dataset_name, round_name) for Phase 4/6 QC to pick up.
        self._grouping_cache: dict[tuple[str, str], object] = {}
        # Per-dataset effective segmentation name. Empty by default, so
        # every phase resolves to ``config.cellpose_segmentation_name``
        # (unchanged behavior). Populated when a dataset is tracked (the
        # tracking phase sets it to ``<seg>_tracked``), when an existing
        # segmentation is auto-detected (U13), or chosen via the
        # segmentation-select dialog (U12). Kept off the frozen
        # WorkflowConfig and reset per run. Seeded from
        # ``segmentation_overrides`` (the per-dataset picks), which take
        # precedence over auto-detection.
        self._effective_seg: dict[str, str] = dict(segmentation_overrides or {})
        # Currently-running interactive QC controller (if any). Held
        # here to prevent Qt GC. Cleared by the terminal callback.
        self._active_qc_controller = None
        # Currently-running segment Worker (if any). Held here to
        # prevent Qt GC and so request_cancel can propagate to it.
        self._active_worker = None

    # ── Cancel override ───────────────────────────────────────

    def request_cancel(self) -> None:
        """Extends the base cancel to propagate to an in-flight segment worker.

        The worker's ``request_abort`` is advisory — Cellpose inference
        is a C++ call that doesn't check our flag, so the in-flight
        dataset still runs to completion. Subsequent datasets will be
        skipped when the base runner's cancel check fires at the next
        dataset boundary.
        """
        super().request_cancel()
        worker = self._active_worker
        if worker is not None:
            try:
                worker.request_abort()
            except Exception:
                logger.exception("worker.request_abort raised")

    # ── Effective segmentation name ───────────────────────────

    def _seg_name_for(self, entry) -> str:
        """The segmentation name a dataset's phases should read/write.

        Defaults to ``config.cellpose_segmentation_name``; overridden per
        dataset via ``self._effective_seg`` (set by the tracking phase,
        auto-skip detection, or the segmentation-select dialog).
        """
        return self._effective_seg.get(
            entry.name, self._config.cellpose_segmentation_name
        )

    def _measure_round_specs_for(self, entry) -> list[ThresholdingRound]:
        """Round specs the measure phase should use for one dataset.

        Normal runs return ``config.thresholding_rounds`` (the same list
        for every dataset). In existing-mask mode the rounds are
        synthesized per dataset from ``existing_mask_selections[entry.name]``
        — one measure-only :class:`ThresholdingRound` whose ``name`` equals
        the chosen ``/masks/<name>`` layer. ``measure_one`` reads only
        ``round.name``, but ``ThresholdingRound.__post_init__`` still
        validates the placeholder ``channel``/``metric``/``algorithm``, so
        they must be valid; a mask name that fails the round-name regex
        records a per-dataset failure rather than aborting the run.

        Per-dataset (not the union of all selections): a dataset measures
        only the masks the user picked for it, never another dataset's
        selection that happens to also exist on disk here.
        """
        if not self._config.use_existing_masks:
            return list(self._config.thresholding_rounds)
        channel = entry.channel_names[0] if entry.channel_names else "channel"
        specs: list[ThresholdingRound] = []
        for mask_name in self._config.existing_mask_selections.get(entry.name, []):
            try:
                specs.append(
                    ThresholdingRound(
                        name=mask_name,
                        channel=channel,
                        metric="mean_intensity",
                        algorithm=ThresholdAlgorithm.KMEANS,
                    )
                )
            except ValueError as e:
                logger.error(
                    "cannot measure mask %r on %s: %s", mask_name, entry.name, e
                )
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name="measure",
                    failure=DatasetFailure.MEASUREMENT_ERROR,
                    message=f"invalid mask name {mask_name!r}: {e}",
                )
        return specs

    def _detect_existing_segmentation(self, entry) -> str | None:
        """Pick a pre-existing segmentation for a dataset, or None to segment it.

        Reads the dataset's label inventory and applies
        ``pick_existing_segmentation`` (prefer ``*_tracked``). Returns None
        when there are no labels (segment normally) or the store can't be
        read. Logs a warning when auto-selecting among multiple untracked
        segmentations so the user knows to use the segmentation-select dialog
        to override.
        """
        try:
            names = DatasetStore(entry.h5_path).list_labels()
        except Exception:
            logger.exception("could not list labels for %s", entry.name)
            return None
        seg = pick_existing_segmentation(names)
        if (
            seg is not None
            and not any(n.endswith("_tracked") for n in names)
            and len(names) > 1
        ):
            logger.warning(
                "dataset %s has multiple segmentations %r and no tracked "
                "layer; auto-selected %r (use the segmentation-select dialog "
                "to override)",
                entry.name,
                names,
                seg,
            )
        return seg

    def _is_timelapse(self, entry) -> bool:
        """True when the dataset has more than one acquisition timepoint."""
        try:
            return int(
                DatasetStore(entry.h5_path).metadata.get("n_timepoints", 1) or 1
            ) > 1
        except Exception:
            logger.exception("could not read n_timepoints for %s", entry.name)
            return False

    def _should_track(self, entry) -> bool:
        """True when a dataset needs tracking: time-lapse and not yet tracked.

        Skips single-timepoint datasets and datasets whose effective
        segmentation is already a tracked layer (auto-detected by U13).
        Also skips a 2D (time-invariant) segmentation — e.g. a whole-field
        gate from ``percell4-batch-whole-field``: a single 2D label has no
        per-frame evolution to track, and ``track_one`` would reject it as
        "not a (T, H, W) stack". Per-frame phases broadcast it instead.
        """
        seg_name = self._seg_name_for(entry)
        if seg_name.endswith("_tracked"):
            return False
        try:
            store = DatasetStore(entry.h5_path)
            n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)
        except Exception:
            logger.exception("could not read n_timepoints for %s", entry.name)
            return False
        if n_timepoints <= 1:
            return False
        try:
            if len(store.labels_shape(seg_name)) == 2:
                return False
        except Exception:
            logger.exception(
                "could not read labels_shape for %s/%s", entry.name, seg_name
            )
            # Fall through: let the track phase surface the real error.
        return True

    # ── Phase generator ───────────────────────────────────────

    def _phase_generator(
        self,
    ) -> Generator[PhaseRequest, PhaseResult | None, None]:
        cfg = self._config
        meta = self._metadata

        # ── Phase 0: compress tiff_pending datasets ─────────
        pending = [
            e for e in self._working_entries if e.source is DatasetSource.TIFF_PENDING
        ]
        total_pending = len(pending)
        for idx, entry in enumerate(pending):
            request = PhaseRequest(
                kind=PhaseKind.UNATTENDED,
                phase_name="compress",
                dataset_index=idx,
                dataset_total=total_pending,
                dataset_name=entry.name,
                handler=self._make_compress_handler(entry),
            )
            yield request  # result is handled inside the handler via the runner

        # ── Phase 1: Cellpose segmentation ─────────────────
        # Hoist the Cellpose model once per phase to avoid the per-dataset
        # construction cost (seconds to minutes on CPU). In interactive
        # mode (production), segmentation runs in a Worker(QThread) so
        # the UI stays responsive during inference — the PhaseRequest is
        # INTERACTIVE so the runner yields control back to Qt until the
        # worker finishes. In headless mode (tests), segmentation runs
        # synchronously on the main thread, which is simpler for the
        # monkey-patched ``segment_one`` fixtures the tests use.
        active = datasets_without_failures(self._working_entries, meta)
        for idx, entry in enumerate(active):
            # Auto-skip (U13/R10): if this dataset already has a segmentation
            # on disk (or an explicit segmentation override), skip Cellpose +
            # seg-QC and use it. An override (U12) wins over auto-detection.
            # TIFF-pending datasets were just compressed and have no labels
            # yet, so they segment normally.
            existing = self._effective_seg.get(entry.name)
            if existing is None:
                existing = self._detect_existing_segmentation(entry)
            if existing is not None:
                self._effective_seg[entry.name] = existing
                # Optionally QC a pre-existing segmentation (produced by
                # percell4-batch or picked via segmentation_overrides)
                # before thresholding, instead of skipping straight to it.
                # Gated to:
                #   - interactive runs only (headless never yields QC);
                #   - cfg.run_seg_qc_on_existing (the config-dialog
                #     checkbox, default True);
                #   - a layer that exists AND is 2D — a single
                #     labels_shape() call establishes both. The editor
                #     (SegmentationQCController) rejects non-2D labels, so a
                #     (T, H, W) stack is skipped; but a 2D whole-field gate
                #     on a time-lapse dataset is still QC-able and runs.
                #     A stale segmentation_overrides entry naming a missing
                #     layer raises here → skipped (rather than erroring
                #     inside the controller).
                #   - NOT a ``*_tracked`` layer — its label VALUES are track
                #     ids tied to the ``/tracks/<seg>`` lineage table. The
                #     raw-label QC tools renumber labels, which would
                #     desync the lineage; ``_should_track`` guards the same
                #     way. Skip QC for tracked layers.
                # When any guard fails we fall through to today's behavior
                # (skip seg-QC, go to thresholding). Cellpose-segmented-
                # this-run datasets take the fresh path below, unaffected
                # by this flag.
                if (
                    self._interactive_qc
                    and self._config.run_seg_qc_on_existing
                    and not existing.endswith("_tracked")
                ):
                    try:
                        is_2d = len(
                            DatasetStore(entry.h5_path).labels_shape(existing)
                        ) == 2
                    except Exception:
                        logger.exception(
                            "could not read labels_shape for %s/%s",
                            entry.name,
                            existing,
                        )
                        is_2d = False
                    if is_2d:
                        yield PhaseRequest(
                            kind=PhaseKind.INTERACTIVE,
                            phase_name="seg_qc",
                            dataset_index=idx,
                            dataset_total=len(active),
                            dataset_name=entry.name,
                            handler=self._make_seg_qc_handler(
                                entry, idx, len(active)
                            ),
                        )
                continue

            if self._interactive_qc:
                yield PhaseRequest(
                    kind=PhaseKind.INTERACTIVE,
                    phase_name="segment",
                    dataset_index=idx,
                    dataset_total=len(active),
                    dataset_name=entry.name,
                    handler=self._make_segment_worker_handler(entry),
                )
            else:
                yield PhaseRequest(
                    kind=PhaseKind.UNATTENDED,
                    phase_name="segment",
                    dataset_index=idx,
                    dataset_total=len(active),
                    dataset_name=entry.name,
                    handler=self._make_segment_handler(entry),
                )

            # ── Phase 2: Interactive segmentation QC ───
            # Interleaved with segment so the user sees each dataset's
            # Cellpose result immediately, edits it, and accepts before
            # the next segment runs.
            #
            # Gated by cfg.run_seg_qc_on_new_segmentations (the config-dialog
            # checkbox, default True) so a batch with settled Cellpose
            # parameters can run unattended. When the gate is off we emit an
            # explicit status + run-log line rather than silently advancing —
            # an unreviewed segmentation must never be handed downstream
            # without the user being told, the same convention the headless
            # threshold-apply handler follows.
            if self._interactive_qc:
                # Skip datasets that segment marked as failed.
                failed_names = {
                    rec.dataset_name
                    for rec in meta.failures
                    if rec.phase_name == "segment"
                }
                if entry.name not in failed_names:
                    if cfg.run_seg_qc_on_new_segmentations:
                        yield PhaseRequest(
                            kind=PhaseKind.INTERACTIVE,
                            phase_name="seg_qc",
                            dataset_index=idx,
                            dataset_total=len(active),
                            dataset_name=entry.name,
                            handler=self._make_seg_qc_handler(
                                entry, idx, len(active)
                            ),
                        )
                    else:
                        msg = (
                            f"{entry.name}: segmentation accepted without QC "
                            "(seg-QC turned off for workflow-created "
                            "segmentations)"
                        )
                        print(f"  [seg_qc] {msg}", flush=True)
                        self._log(
                            phase="seg_qc", dataset=entry.name,
                            event="skipped_no_qc", message=msg,
                        )

        # ── Tracking (time-lapse): link cells across timepoints ──
        # Runs after seg-QC for datasets with n_timepoints > 1 that aren't
        # already tracked. On success the dataset's effective segmentation
        # switches to the tracked layer, so every downstream phase uses it.
        active = datasets_without_failures(self._working_entries, meta)
        for idx, entry in enumerate(active):
            if not self._should_track(entry):
                continue
            yield PhaseRequest(
                kind=PhaseKind.UNATTENDED,
                phase_name="track",
                dataset_index=idx,
                dataset_total=len(active),
                dataset_name=entry.name,
                handler=self._make_track_handler(entry),
            )

        # ── Per-round: threshold compute + apply ────────────
        for round_idx, round_spec in enumerate(cfg.thresholding_rounds):
            active = datasets_without_failures(self._working_entries, meta)

            # Phase 3/5: compute grouping (UNATTENDED).
            for idx, entry in enumerate(active):
                yield PhaseRequest(
                    kind=PhaseKind.UNATTENDED,
                    phase_name=f"threshold_compute:{round_spec.name}",
                    dataset_index=idx,
                    dataset_total=len(active),
                    dataset_name=entry.name,
                    sub_progress=f"round {round_idx + 1}/{len(cfg.thresholding_rounds)}",
                    handler=self._make_threshold_compute_handler(entry, round_spec),
                )

            # Phase 4/6: apply thresholds.
            # Interactive: yield one ThresholdQCQueueEntry per dataset.
            # Headless: run apply_threshold_headless in an UNATTENDED
            # handler.
            active = datasets_without_failures(self._working_entries, meta)
            for idx, entry in enumerate(active):
                if (entry.name, round_spec.name) not in self._grouping_cache:
                    # Compute failed for this (dataset, round) pair —
                    # no GroupingResult to QC. Skip.
                    continue

                # Interactive threshold-QC uses the single-frame
                # ThresholdQCController. Time-lapse datasets run it one
                # timepoint at a time (TimelapseThresholdQCQueueEntry) so the
                # user QCs every frame's groups interactively, just like the
                # standard single-timepoint workflow; the per-frame masks are
                # stacked into the (T,H,W) /masks resource at the end. The
                # handler picks the right wrapper from _is_timelapse(entry).
                #
                # Adaptive-clip and auto-extraction rounds are per-cell (no
                # intensity grouping) and cannot be previewed by the per-group
                # ThresholdQCController, so they ALWAYS apply headlessly — even in
                # an interactive run. The headless handler emits a status line so
                # the user knows the round applied without a QC pause.
                if (
                    self._interactive_qc
                    and round_spec.adaptive_clip is None
                    and round_spec.auto_extract is None
                ):
                    yield PhaseRequest(
                        kind=PhaseKind.INTERACTIVE,
                        phase_name=f"threshold_qc:{round_spec.name}",
                        dataset_index=idx,
                        dataset_total=len(active),
                        dataset_name=entry.name,
                        sub_progress=f"round {round_idx + 1}/{len(cfg.thresholding_rounds)}",
                        handler=self._make_threshold_qc_handler(
                            entry, round_spec, idx, len(active)
                        ),
                    )
                else:
                    yield PhaseRequest(
                        kind=PhaseKind.UNATTENDED,
                        phase_name=f"threshold_apply:{round_spec.name}",
                        dataset_index=idx,
                        dataset_total=len(active),
                        dataset_name=entry.name,
                        sub_progress=f"round {round_idx + 1}/{len(cfg.thresholding_rounds)}",
                        handler=self._make_threshold_apply_headless_handler(
                            entry, round_spec
                        ),
                    )

        # ── Phase 5: Dilute-phase mask (optional, INTERACTIVE) ──
        # Runs only when the user enabled dilute generation in the
        # config dialog AND we're in interactive mode. In headless
        # tests we skip Phase 5 entirely — the dilute UI is inherently
        # user-driven (adaptive per-dataset round count) and has no
        # meaningful auto-mode equivalent.
        if cfg.dilute_settings is not None and self._interactive_qc:
            active = datasets_without_failures(self._working_entries, meta)
            for idx, entry in enumerate(active):
                # The dilute controller is single-frame and has no headless
                # per-frame equivalent; skip it for time-lapse datasets (the
                # per-frame dilute mask is deferred — see plan Scope).
                if self._is_timelapse(entry):
                    logger.info(
                        "skipping interactive dilute for time-lapse dataset %s",
                        entry.name,
                    )
                    continue
                yield PhaseRequest(
                    kind=PhaseKind.INTERACTIVE,
                    phase_name="dilute",
                    dataset_index=idx,
                    dataset_total=len(active),
                    dataset_name=entry.name,
                    handler=self._make_dilute_handler(entry, idx, len(active)),
                )

        # ── Phase 7: measurement ──────────────────────────
        active = datasets_without_failures(self._working_entries, meta)
        for idx, entry in enumerate(active):
            request = PhaseRequest(
                kind=PhaseKind.UNATTENDED,
                phase_name="measure",
                dataset_index=idx,
                dataset_total=len(active),
                dataset_name=entry.name,
                handler=self._make_measure_handler(entry),
            )
            yield request

        # ── Phase 8: export aggregate ─────────────────────
        yield PhaseRequest(
            kind=PhaseKind.UNATTENDED,
            phase_name="export",
            dataset_index=0,
            dataset_total=1,
            dataset_name="",
            handler=self._make_export_handler(),
        )

    # ── Per-phase handler factories ───────────────────────────
    #
    # Each factory returns a zero-arg callable that runs the pure
    # phase-helper on one dataset (or the aggregate, for export) and
    # returns a PhaseResult. Failures are recorded on the metadata and
    # surfaced via PhaseResult.success=False; the runner never raises
    # out of a handler (the base class would catch it and terminate the
    # run, which is undesirable for a per-dataset failure).

    def _make_compress_handler(self, entry):
        def handler() -> PhaseResult:
            print(f"  [compress] {entry.name}...", flush=True)
            updated, failure, msg = compress_one(entry)
            if failure is not None:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name="compress",
                    failure=failure,
                    message=msg,
                )
                self._log(phase="compress", dataset=entry.name,
                          event="failed", failure=failure.value, message=msg)
                return PhaseResult(success=False, message=msg)

            # Gate on what the run actually needs before any later phase
            # touches this dataset. import_dataset does not raise when no
            # source file matches the channel token pattern — it writes an
            # .h5 with no /intensity and empty channel_names and reports
            # success — so without this the run continues against an empty
            # dataset and fails minutes later somewhere unrelated.
            problem = validate_compressed_dataset(
                DatasetStore(updated.h5_path),
                seg_channel_name=self._config.seg_channel_name,
                round_channels=[
                    r.channel for r in self._config.thresholding_rounds
                ],
                needs_pixel_size=config_needs_pixel_size(
                    self._config.thresholding_rounds
                ),
            )
            if problem is not None:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name="compress",
                    failure=DatasetFailure.COMPRESS_FAILED,
                    message=problem,
                )
                self._log(
                    phase="compress", dataset=entry.name, event="failed",
                    failure=DatasetFailure.COMPRESS_FAILED.value,
                    message=problem,
                )
                return PhaseResult(success=False, message=problem)

            # Swap the updated entry in place so later phases see the
            # real h5_path.
            for i, e in enumerate(self._working_entries):
                if e.name == entry.name:
                    self._working_entries[i] = updated
                    break
            self._log(phase="compress", dataset=entry.name, event="done")
            return PhaseResult(success=True, message=msg)

        return handler

    def _make_track_handler(self, entry):
        def handler() -> PhaseResult:
            print(f"  [track] {entry.name}...", flush=True)
            store = DatasetStore(entry.h5_path)
            raw_seg = self._seg_name_for(entry)
            tracked_name, failure, msg = track_one(store, raw_seg)
            if failure is not None:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name="track",
                    failure=failure,
                    message=msg,
                )
                self._log(phase="track", dataset=entry.name,
                          event="failed", failure=failure.value, message=msg)
                return PhaseResult(success=False, message=msg)
            # Downstream phases now read the tracked segmentation.
            self._effective_seg[entry.name] = tracked_name
            self._log(phase="track", dataset=entry.name, event="done", message=msg)
            return PhaseResult(success=True, message=msg)

        return handler

    def _make_segment_handler(self, entry):
        def handler() -> PhaseResult:
            # Lazily build the Cellpose model on the first segment call.
            # Doing it here (not in __init__) defers the heavy import
            # until we're actually about to segment.
            if self._cellpose_model is None:
                try:
                    self._cellpose_model = build_cellpose_model(
                        gpu=self._config.cellpose.gpu
                    )
                except Exception as e:
                    logger.exception("build_cellpose_model failed")
                    record_failure(
                        self._metadata,
                        dataset_name=entry.name,
                        phase_name="segment",
                        failure=DatasetFailure.SEGMENTATION_ERROR,
                        message=f"build model failed: {e}",
                    )
                    return PhaseResult(success=False, message=str(e))

            try:
                store = DatasetStore(entry.h5_path)
            except Exception as e:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name="segment",
                    failure=DatasetFailure.SEGMENTATION_ERROR,
                    message=f"open store failed: {e}",
                )
                return PhaseResult(success=False, message=str(e))

            _labels, failure, msg = segment_one(
                store,
                self._config.cellpose,
                cellpose_model=self._cellpose_model,
                channel_idx=self._seg_channel_idx(store),
                edge_mode=self._config.edge_mode,
                edge_margin_px=self._config.edge_margin_px,
                seg_name=self._seg_name_for(entry),
            )
            if failure is not None:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name="segment",
                    failure=failure,
                    message=msg,
                )
                self._log(phase="segment", dataset=entry.name,
                          event="failed", failure=failure.value, message=msg)
                return PhaseResult(success=False, message=msg)

            self._log(phase="segment", dataset=entry.name, event="done",
                      message=msg)
            return PhaseResult(success=True, message=msg)

        return handler

    def _make_segment_worker_handler(self, entry):
        """Factory for an INTERACTIVE segment handler that runs in a Worker.

        The heavy work (``run_cellpose`` + postprocess + ``write_labels``)
        happens inside a :class:`percell4.gui.workers.Worker` QThread so
        the UI stays responsive during Cellpose inference. The runner
        yields ``PhaseKind.INTERACTIVE`` so the base runner's loop
        breaks out, registers the worker's ``finished``/``error`` slots,
        and returns control to Qt. When the worker emits ``finished``,
        the slot calls ``on_complete(PhaseResult)`` which re-enters the
        runner loop via :meth:`BaseWorkflowRunner._on_interactive_complete`.

        Cooperative cancel: ``runner.request_cancel()`` calls
        ``worker.request_abort()``; the next ``_advance`` call detects
        the cancel flag at a boundary and unwinds.
        """
        def handler(on_complete):
            from percell4.gui.workers import Worker

            if self._cellpose_model is None:
                try:
                    self._cellpose_model = build_cellpose_model(
                        gpu=self._config.cellpose.gpu
                    )
                except Exception as e:
                    logger.exception("build_cellpose_model failed")
                    record_failure(
                        self._metadata,
                        dataset_name=entry.name,
                        phase_name="segment",
                        failure=DatasetFailure.SEGMENTATION_ERROR,
                        message=f"build model failed: {e}",
                    )
                    on_complete(PhaseResult(success=False, message=str(e)))
                    return

            try:
                store = DatasetStore(entry.h5_path)
            except Exception as e:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name="segment",
                    failure=DatasetFailure.SEGMENTATION_ERROR,
                    message=f"open store failed: {e}",
                )
                on_complete(PhaseResult(success=False, message=str(e)))
                return

            seg_ch_idx = self._seg_channel_idx(store)

            edge_mode = self._config.edge_mode
            edge_margin_px = self._config.edge_margin_px
            seg_name = self._seg_name_for(entry)

            def _do_segment() -> tuple:
                """Runs in the Worker thread. Pure numpy + h5py, no Qt."""
                return segment_one(
                    store,
                    self._config.cellpose,
                    cellpose_model=self._cellpose_model,
                    channel_idx=seg_ch_idx,
                    edge_mode=edge_mode,
                    edge_margin_px=edge_margin_px,
                    seg_name=seg_name,
                )

            worker = Worker(_do_segment)

            # Show progress in the status bar + terminal so the user
            # knows Cellpose is running while the viewer is blank.
            if self._host is not None:
                self._host.show_workflow_status(
                    "Segmenting",
                    f"{entry.name} — running Cellpose...",
                )
            print(f"  [segment] {entry.name} — running Cellpose...", flush=True)

            def _on_worker_finished(result):
                self._active_worker = None
                _labels, failure, msg = result
                if failure is not None:
                    record_failure(
                        self._metadata,
                        dataset_name=entry.name,
                        phase_name="segment",
                        failure=failure,
                        message=msg,
                    )
                    self._log(phase="segment", dataset=entry.name,
                              event="failed", failure=failure.value,
                              message=msg)
                    on_complete(PhaseResult(success=False, message=msg))
                    return
                self._log(phase="segment", dataset=entry.name,
                          event="done", message=msg)
                on_complete(PhaseResult(success=True, message=msg))

            def _on_worker_error(err):
                self._active_worker = None
                message = f"{err.exc_type}: {err.message}"
                logger.error("segment worker error: %s", message)
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name="segment",
                    failure=DatasetFailure.SEGMENTATION_ERROR,
                    message=message,
                )
                on_complete(PhaseResult(success=False, message=message))

            worker.finished.connect(_on_worker_finished)
            worker.error.connect(_on_worker_error)
            # Hold a reference so Qt doesn't GC the thread.
            self._active_worker = worker
            self._log(phase="segment", dataset=entry.name, event="worker_started")
            worker.start()

        return handler

    def _make_seg_qc_handler(self, entry, queue_index: int, queue_total: int):
        """Factory for an INTERACTIVE seg QC phase handler.

        The handler takes an ``on_complete`` callback from the runner's
        ``_dispatch_request`` and forwards it to a fresh
        :class:`SegmentationQCController`. Holds the controller on
        ``self`` so Qt doesn't GC it while the user is interacting.
        """
        def handler(on_complete):
            from percell4.gui.workflows.single_cell.seg_qc import (
                SegmentationQCController,
            )

            if self._host is None:
                on_complete(
                    PhaseResult(success=False, message="no host for seg QC")
                )
                return

            viewer_win = self._host.get_viewer_window()

            def _wrapped_complete(result):
                # Record the user's cancel as a runner-level cancel so
                # _finish fires with the right message; otherwise the
                # base runner would treat it as a generator exception.
                if not result.success and "cancel" in result.message.lower():
                    self.request_cancel()
                on_complete(result)

            # Resolve the seg channel index for this dataset so the QC
            # controller loads the right intensity channel.
            try:
                _store = DatasetStore(entry.h5_path)
                seg_ch = self._seg_channel_idx(_store)
            except Exception:
                seg_ch = 0

            controller = SegmentationQCController(
                viewer_win=viewer_win,
                entry=entry,
                queue_index=queue_index,
                queue_total=queue_total,
                on_complete=_wrapped_complete,
                channel_idx=seg_ch,
                seg_name=self._seg_name_for(entry),
                cellpose_settings=self._config.cellpose,
                edge_mode=self._config.edge_mode,
                edge_margin_px=self._config.edge_margin_px,
            )
            self._active_qc_controller = controller
            self._log(phase="seg_qc", dataset=entry.name, event="opened")
            controller.start()

        return handler

    def _make_dilute_handler(self, entry, queue_index: int, queue_total: int):
        """Factory for the Phase 5 INTERACTIVE dilute-phase handler.

        Per the U5 plan, the handler instantiates a
        :class:`DilutePhaseQueueEntry` that wraps the existing
        :class:`DilutePhaseMaskController` in session-free mode.
        Per-dataset round counts are persisted into
        ``RunMetadata.per_dataset_dilute_round_counts`` so the
        ``summary_datasets.csv`` builder can populate ``n_rounds_dilute``.
        """
        def handler(on_complete):
            from percell4.gui.workflows.single_cell.dilute_queue import (
                DilutePhaseQueueEntry,
            )

            if self._host is None:
                on_complete(
                    PhaseResult(success=False, message="no host for dilute queue")
                )
                return
            if self._config.dilute_settings is None:
                on_complete(
                    PhaseResult(
                        success=True,
                        message="dilute disabled (no dilute_settings)",
                    )
                )
                return

            viewer_win = self._host.get_viewer_window()
            data_model = self._host.get_data_model()
            session = self._host.get_session()

            def _wrapped_complete(result: PhaseResult) -> None:
                # Explicit cancelled flag (new in U5) — the substring
                # sniff stays as a backward-compat fallback for handlers
                # that haven't migrated yet.
                if result.cancelled or (
                    not result.success and "cancel" in result.message.lower()
                ):
                    self.request_cancel()
                if not result.success and not result.cancelled:
                    record_failure(
                        self._metadata,
                        dataset_name=entry.name,
                        phase_name="dilute",
                        failure=DatasetFailure.MEASUREMENT_ERROR,
                        message=result.message,
                    )
                self._log(
                    phase="dilute",
                    dataset=entry.name,
                    event="done" if result.success else "failed",
                    message=result.message,
                )
                on_complete(result)

            def _record_round_count(name: str, n: int) -> None:
                """Callback from the queue entry at workflow_done."""
                self._metadata.per_dataset_dilute_round_counts[name] = n

            try:
                qentry = DilutePhaseQueueEntry(
                    entry=entry,
                    dilute_settings=self._config.dilute_settings,
                    viewer_win=viewer_win,
                    data_model=data_model,
                    session=session,
                    queue_index=queue_index,
                    queue_total=queue_total,
                    on_complete=_wrapped_complete,
                    on_round_complete=_record_round_count,
                    seg_name=self._seg_name_for(entry),
                )
            except Exception as e:
                logger.exception("dilute queue entry init failed")
                _wrapped_complete(
                    PhaseResult(
                        success=False,
                        message=f"dilute queue init failed: {e}",
                    )
                )
                return

            # Strong-ref slot to defeat Qt GC mid-flight.
            self._active_qc_controller = qentry
            self._log(phase="dilute", dataset=entry.name, event="opened")
            try:
                qentry.start()
            except Exception as e:
                logger.exception("dilute queue entry start raised")
                _wrapped_complete(
                    PhaseResult(
                        success=False,
                        message=f"dilute queue start failed: {e}",
                    )
                )

        return handler

    def _make_threshold_compute_handler(self, entry, round_spec):
        """UNATTENDED handler that computes the GroupingResult and stashes it."""
        def handler() -> PhaseResult:
            print(
                f"  [threshold compute] {entry.name} — round: {round_spec.name}...",
                flush=True,
            )
            try:
                store = DatasetStore(entry.h5_path)
            except Exception as e:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name=f"threshold_compute:{round_spec.name}",
                    failure=DatasetFailure.THRESHOLD_ERROR,
                    message=f"open store failed: {e}",
                )
                return PhaseResult(success=False, message=str(e))

            grouping, failure, msg = threshold_compute_one(
                store,
                round_spec,
                seg_name=self._seg_name_for(entry),
            )
            if failure is not None:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name=f"threshold_compute:{round_spec.name}",
                    failure=failure,
                    message=msg,
                )
                self._log(phase=f"threshold_compute:{round_spec.name}",
                          dataset=entry.name, event="failed",
                          failure=failure.value, message=msg)
                return PhaseResult(success=False, message=msg)

            # Stash the GroupingResult for the matching QC phase to
            # pick up.
            self._grouping_cache[(entry.name, round_spec.name)] = grouping
            self._log(phase=f"threshold_compute:{round_spec.name}",
                      dataset=entry.name, event="done", message=msg)
            return PhaseResult(success=True, message=msg)

        return handler

    def _make_threshold_apply_headless_handler(self, entry, round_spec):
        """UNATTENDED handler: apply Otsu per-group thresholds headlessly.

        Only used when ``interactive_qc=False``. The interactive path
        (``_make_threshold_qc_handler``) handles the persistence itself
        via :class:`ThresholdQCController`.
        """
        def handler() -> PhaseResult:
            grouping = self._grouping_cache.get((entry.name, round_spec.name))
            if grouping is None:
                # Compute phase failed for this pair — skip silently.
                return PhaseResult(
                    success=True,
                    message="no grouping (compute failed earlier, skipping)",
                )

            try:
                store = DatasetStore(entry.h5_path)
            except Exception as e:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name=f"threshold_apply:{round_spec.name}",
                    failure=DatasetFailure.THRESHOLD_ERROR,
                    message=f"open store failed: {e}",
                )
                return PhaseResult(success=False, message=str(e))

            failure, msg = apply_threshold_headless(
                store,
                round_spec,
                grouping,
                seg_name=self._seg_name_for(entry),
            )
            if failure is not None:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name=f"threshold_apply:{round_spec.name}",
                    failure=failure,
                    message=msg,
                )
                self._log(phase=f"threshold_apply:{round_spec.name}",
                          dataset=entry.name, event="failed",
                          failure=failure.value, message=msg)
                return PhaseResult(success=False, message=msg)

            # Per-cell rounds (adaptive-clip / auto-extraction) skip the
            # interactive QC pause every other round gets; make that explicit so
            # the user is not silently handed unreviewed masks.
            is_per_cell = (
                round_spec.adaptive_clip is not None or round_spec.auto_extract is not None
            )
            if is_per_cell and self._interactive_qc:
                method = (
                    "adaptive sigma clipping"
                    if round_spec.adaptive_clip is not None
                    else "auto-extraction (two-pass)"
                )
                msg = f"{method} — applied headlessly (no QC step): {msg}"
                event = "done_no_qc"
            else:
                event = "done"
            self._log(phase=f"threshold_apply:{round_spec.name}",
                      dataset=entry.name, event=event, message=msg)
            return PhaseResult(success=True, message=msg)

        return handler

    def _make_threshold_qc_handler(
        self, entry, round_spec, queue_index: int, queue_total: int
    ):
        """Factory for an INTERACTIVE threshold QC phase handler.

        Wraps :class:`ThresholdQCController` in a ``ThresholdQCQueueEntry``
        that bridges the controller's ``on_complete(success, msg)``
        into a :class:`PhaseResult` for the runner.
        """
        def handler(on_complete):
            from percell4.gui.workflows.single_cell.threshold_qc_queue import (
                ThresholdQCQueueEntry,
                TimelapseThresholdQCQueueEntry,
            )

            if self._host is None:
                on_complete(
                    PhaseResult(success=False, message="no host for threshold QC")
                )
                return

            grouping = self._grouping_cache.get((entry.name, round_spec.name))
            if grouping is None:
                on_complete(
                    PhaseResult(
                        success=True,
                        message="no grouping (compute failed earlier, skipping)",
                    )
                )
                return

            def _wrapped_complete(result):
                if not result.success and "cancel" in result.message.lower():
                    self.request_cancel()
                else:
                    # On success, drop the cached grouping to free memory.
                    self._grouping_cache.pop((entry.name, round_spec.name), None)
                # Record a failure record for non-cancel failures so
                # measure_one skips this dataset's mask for this round.
                if not result.success and "cancel" not in result.message.lower():
                    record_failure(
                        self._metadata,
                        dataset_name=entry.name,
                        phase_name=f"threshold_qc:{round_spec.name}",
                        failure=DatasetFailure.THRESHOLD_ERROR,
                        message=result.message,
                    )
                on_complete(result)

            viewer_win = self._host.get_viewer_window()
            data_model = self._host.get_data_model()
            if self._is_timelapse(entry):
                # Time-lapse: grouping is a dict[int, GroupingResult]; QC one
                # timepoint at a time, stacking masks into a (T,H,W) resource.
                queue_entry = TimelapseThresholdQCQueueEntry(
                    viewer_win=viewer_win,
                    data_model=data_model,
                    entry=entry,
                    round_spec=round_spec,
                    grouping_by_timepoint=grouping,
                    queue_index=queue_index,
                    queue_total=queue_total,
                    on_complete=_wrapped_complete,
                    seg_name=self._seg_name_for(entry),
                )
            else:
                queue_entry = ThresholdQCQueueEntry(
                    viewer_win=viewer_win,
                    data_model=data_model,
                    entry=entry,
                    round_spec=round_spec,
                    grouping_result=grouping,
                    queue_index=queue_index,
                    queue_total=queue_total,
                    on_complete=_wrapped_complete,
                    seg_name=self._seg_name_for(entry),
                )
            # Hold a reference to prevent GC.
            self._active_qc_controller = queue_entry
            self._log(
                phase=f"threshold_qc:{round_spec.name}",
                dataset=entry.name,
                event="opened",
            )
            queue_entry.start()

        return handler

    def _make_measure_handler(self, entry):
        def handler() -> PhaseResult:
            print(f"  [measure] {entry.name}...", flush=True)
            try:
                store = DatasetStore(entry.h5_path)
            except Exception as e:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name="measure",
                    failure=DatasetFailure.MEASUREMENT_ERROR,
                    message=f"open store failed: {e}",
                )
                return PhaseResult(success=False, message=str(e))

            round_specs = self._measure_round_specs_for(entry)
            df, failure, msg = measure_one(
                store,
                round_specs=round_specs,
                edge_mode=self._config.edge_mode,
                edge_margin_px=self._config.edge_margin_px,
                seg_name=self._seg_name_for(entry),
                particle_settings=self._config.particle_settings,
                run_log=self._run_log,
                dataset_name=entry.name,
            )
            # Soft failures from _append_synthetic_row (e.g. AE2: zero
            # whole cells in edge-cohort mode) leave df populated so
            # the dataset's per-cell rows still reach staging — only
            # the synthetic row is missing. Hard failures (read error,
            # empty labels, measure crash) return an empty df and we
            # skip staging entirely.
            if failure is not None:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name="measure",
                    failure=failure,
                    message=msg,
                )
                self._log(phase="measure", dataset=entry.name, event="failed",
                          failure=failure.value, message=msg)
                if df.empty:
                    return PhaseResult(success=False, message=msg)
                # Fall through: stage the soft-failure df so its
                # per-cell rows reach the parquet.

            try:
                write_staging_parquet(
                    self._metadata.run_folder, entry.name, df
                )
            except Exception as e:
                logger.exception("write_staging_parquet failed")
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name="measure",
                    failure=DatasetFailure.MEASUREMENT_ERROR,
                    message=f"staging write failed: {e}",
                )
                return PhaseResult(success=False, message=str(e))

            # Particle analysis: per-particle detail. Per-cell columns
            # were already merged into df inside measure_one. Errors
            # here are recorded but non-fatal — per-cell measurements
            # have already landed.
            if self._config.particle_settings is not None:
                try:
                    from percell4.workflows.phases import (
                        measure_particles_one,
                        write_staging_particles_parquet,
                    )

                    particles_df, pfail, pmsg = measure_particles_one(
                        store,
                        round_specs=round_specs,
                        particle_settings=self._config.particle_settings,
                        seg_name=self._seg_name_for(entry),
                        run_log=self._run_log,
                        dataset_name=entry.name,
                    )
                    if pfail is not None:
                        record_failure(
                            self._metadata,
                            dataset_name=entry.name,
                            phase_name="particles",
                            failure=pfail,
                            message=pmsg,
                        )
                    elif not particles_df.empty:
                        write_staging_particles_parquet(
                            self._metadata.run_folder, entry.name, particles_df
                        )
                except Exception as e:
                    logger.exception("particle staging failed for %s", entry.name)
                    record_failure(
                        self._metadata,
                        dataset_name=entry.name,
                        phase_name="particles",
                        failure=DatasetFailure.MEASUREMENT_ERROR,
                        message=f"particle staging failed: {e}",
                    )

            self._log(phase="measure", dataset=entry.name, event="done",
                      message=msg)
            return PhaseResult(success=True, message=msg)

        return handler

    def _make_export_handler(self):
        def handler() -> PhaseResult:
            print("  [export] aggregating measurements...", flush=True)
            # In existing-mask mode config.thresholding_rounds is empty, so
            # pass the union of measured mask names as the effective round
            # names — otherwise summary_groups.csv and n_rounds_thresholding
            # come out blank despite masks having been measured.
            round_names = None
            if self._config.use_existing_masks:
                names: list[str] = []
                seen: set[str] = set()
                for sel in self._config.existing_mask_selections.values():
                    for m in sel:
                        if m not in seen:
                            seen.add(m)
                            names.append(m)
                round_names = names
            failure, msg = export_run(
                self._metadata.run_folder, self._config, self._metadata, round_names
            )
            if failure is not None:
                # Export failure is a run-level failure; record it under
                # a sentinel dataset_name so the FailureRecord is visible
                # in run_config.json.
                record_failure(
                    self._metadata,
                    dataset_name="<export>",
                    phase_name="export",
                    failure=failure,
                    message=msg,
                )
                self._log(phase="export", event="failed",
                          failure=failure.value, message=msg)
                return PhaseResult(success=False, message=msg)

            self._log(phase="export", event="done", message=msg)
            return PhaseResult(success=True, message=msg)

        return handler

    # ── Helpers ───────────────────────────────────────────────

    def _seg_channel_idx(self, store: DatasetStore) -> int:
        """Resolve the configured seg_channel_name to an integer index.

        Falls back to 0 if the name is empty or not found (defensive).
        """
        name = self._config.seg_channel_name
        if not name:
            return 0
        from percell4.workflows.phases import _channel_index

        try:
            return _channel_index(store, name)
        except KeyError:
            logger.warning(
                "seg_channel_name %r not found in dataset; falling back to 0",
                name,
            )
            return 0

    def _log(self, **fields) -> None:
        """Forward a structured log entry to the run's RunLog."""
        if self._run_log is not None:
            try:
                self._run_log.log(**fields)
            except OSError:
                logger.exception("run log write failed")
