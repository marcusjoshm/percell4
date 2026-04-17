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

from percell4.gui.workflows.base_runner import (
    BaseWorkflowRunner,
    PhaseKind,
    PhaseRequest,
    PhaseResult,
)
from percell4.adapters.cellpose import build_cellpose_model
from percell4.store import DatasetStore
from percell4.workflows.failures import DatasetFailure
from percell4.workflows.models import (
    DatasetSource,
    RunMetadata,
    WorkflowConfig,
)
from percell4.workflows.phases import (
    apply_threshold_headless,
    compress_one,
    datasets_without_failures,
    export_run,
    measure_one,
    record_failure,
    segment_one,
    threshold_compute_one,
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
            if self._interactive_qc:
                # Skip datasets that segment marked as failed.
                failed_names = {
                    rec.dataset_name
                    for rec in meta.failures
                    if rec.phase_name == "segment"
                }
                if entry.name not in failed_names:
                    yield PhaseRequest(
                        kind=PhaseKind.INTERACTIVE,
                        phase_name="seg_qc",
                        dataset_index=idx,
                        dataset_total=len(active),
                        dataset_name=entry.name,
                        handler=self._make_seg_qc_handler(entry, idx, len(active)),
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

                if self._interactive_qc:
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

            # Swap the updated entry in place so later phases see the
            # real h5_path.
            for i, e in enumerate(self._working_entries):
                if e.name == entry.name:
                    self._working_entries[i] = updated
                    break
            self._log(phase="compress", dataset=entry.name, event="done")
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

            def _do_segment() -> tuple:
                """Runs in the Worker thread. Pure numpy + h5py, no Qt."""
                return segment_one(
                    store,
                    self._config.cellpose,
                    cellpose_model=self._cellpose_model,
                    channel_idx=seg_ch_idx,
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
            )
            self._active_qc_controller = controller
            self._log(phase="seg_qc", dataset=entry.name, event="opened")
            controller.start()

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

            grouping, failure, msg = threshold_compute_one(store, round_spec)
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

            failure, msg = apply_threshold_headless(store, round_spec, grouping)
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

            self._log(phase=f"threshold_apply:{round_spec.name}",
                      dataset=entry.name, event="done", message=msg)
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
            queue_entry = ThresholdQCQueueEntry(
                viewer_win=viewer_win,
                data_model=data_model,
                entry=entry,
                round_spec=round_spec,
                grouping_result=grouping,
                queue_index=queue_index,
                queue_total=queue_total,
                on_complete=_wrapped_complete,
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

            df, failure, msg = measure_one(
                store,
                round_specs=list(self._config.thresholding_rounds),
            )
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
                return PhaseResult(success=False, message=msg)

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

            self._log(phase="measure", dataset=entry.name, event="done",
                      message=msg)
            return PhaseResult(success=True, message=msg)

        return handler

    def _make_export_handler(self):
        def handler() -> PhaseResult:
            print("  [export] aggregating measurements...", flush=True)
            failure, msg = export_run(
                self._metadata.run_folder, self._config, self._metadata
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
