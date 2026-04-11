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
from percell4.segment.cellpose import build_cellpose_model
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
        # construction cost (seconds to minutes on CPU).
        active = datasets_without_failures(self._working_entries, meta)
        for idx, entry in enumerate(active):
            request = PhaseRequest(
                kind=PhaseKind.UNATTENDED,
                phase_name="segment",
                dataset_index=idx,
                dataset_total=len(active),
                dataset_name=entry.name,
                handler=self._make_segment_handler(entry),
            )
            yield request

        # ── Per-round: threshold compute + headless apply ─
        for round_idx, round_spec in enumerate(cfg.thresholding_rounds):
            active = datasets_without_failures(self._working_entries, meta)
            phase_name = f"threshold:{round_spec.name}"

            for idx, entry in enumerate(active):
                request = PhaseRequest(
                    kind=PhaseKind.UNATTENDED,
                    phase_name=phase_name,
                    dataset_index=idx,
                    dataset_total=len(active),
                    dataset_name=entry.name,
                    sub_progress=f"round {round_idx + 1}/{len(cfg.thresholding_rounds)}",
                    handler=self._make_threshold_handler(entry, round_spec),
                )
                yield request

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
                channel_idx=0,
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

    def _make_threshold_handler(self, entry, round_spec):
        def handler() -> PhaseResult:
            try:
                store = DatasetStore(entry.h5_path)
            except Exception as e:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name=f"threshold:{round_spec.name}",
                    failure=DatasetFailure.THRESHOLD_ERROR,
                    message=f"open store failed: {e}",
                )
                return PhaseResult(success=False, message=str(e))

            # 1) Compute the grouping for this (dataset, round).
            grouping, failure, msg = threshold_compute_one(store, round_spec)
            if failure is not None:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name=f"threshold:{round_spec.name}",
                    failure=failure,
                    message=msg,
                )
                self._log(phase=f"threshold:{round_spec.name}",
                          dataset=entry.name, event="compute_failed",
                          failure=failure.value, message=msg)
                return PhaseResult(success=False, message=msg)

            # 2) Apply the thresholds headlessly (Phase 4 stand-in for
            # Phase 6's interactive QC controller). Phase 6 will
            # replace this with a call to ThresholdQCController.
            failure, msg = apply_threshold_headless(store, round_spec, grouping)
            if failure is not None:
                record_failure(
                    self._metadata,
                    dataset_name=entry.name,
                    phase_name=f"threshold:{round_spec.name}",
                    failure=failure,
                    message=msg,
                )
                self._log(phase=f"threshold:{round_spec.name}",
                          dataset=entry.name, event="apply_failed",
                          failure=failure.value, message=msg)
                return PhaseResult(success=False, message=msg)

            self._log(phase=f"threshold:{round_spec.name}",
                      dataset=entry.name, event="done", message=msg)
            return PhaseResult(success=True, message=msg)

        return handler

    def _make_measure_handler(self, entry):
        def handler() -> PhaseResult:
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

    def _log(self, **fields) -> None:
        """Forward a structured log entry to the run's RunLog."""
        if self._run_log is not None:
            try:
                self._run_log.log(**fields)
            except OSError:
                logger.exception("run log write failed")
