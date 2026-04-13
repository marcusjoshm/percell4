"""Per-dataset wrapper around :class:`ThresholdQCController` for the runner.

The existing :class:`ThresholdQCController` already handles the per-group
QC flow (group preview → per-group threshold QC → accept/skip). This
wrapper adapts it to the workflow runner's per-dataset interactive
PhaseRequest protocol:

- Loads the dataset's channel image and labels
- Instantiates ``ThresholdQCController`` with ``write_measurements_to_store=False``
  so only ``/masks/<round>`` and ``/groups/<round>`` are written (per the
  Phase 1 tech-debt note — the workflow owns measurement persistence
  separately)
- Forwards the controller's ``on_complete(success, msg)`` callback into
  the runner's :class:`PhaseResult` + advance
- Keeps a persistent handle on the controller to prevent Qt GC mid-flight

The runner creates one wrapper per (dataset, round) pair, so the
complete Phase 3/5/... sequence becomes: yield threshold-compute
(UNATTENDED) to populate the GroupingResult, then yield this wrapper
(INTERACTIVE) to let the user accept / reject each group's threshold.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np

from percell4.gui.threshold_qc import ThresholdQCController
from percell4.gui.workflows.base_runner import PhaseResult
from percell4.measure.grouper import GroupingResult
from percell4.store import DatasetStore
from percell4.workflows.models import ThresholdingRound, WorkflowDatasetEntry
from percell4.workflows.phases import _channel_index

logger = logging.getLogger(__name__)


class ThresholdQCQueueEntry:
    """Drives one (dataset, round) pair through the ThresholdQCController.

    Not a QObject itself — the controller it wraps already is one. We
    just hold the per-dataset state plus the completion callback that
    bridges back into the runner.
    """

    def __init__(
        self,
        *,
        viewer_win,
        data_model,
        entry: WorkflowDatasetEntry,
        round_spec: ThresholdingRound,
        grouping_result: GroupingResult,
        queue_index: int,
        queue_total: int,
        on_complete: Callable[[PhaseResult], None],
    ) -> None:
        self._viewer_win = viewer_win
        self._data_model = data_model
        self._entry = entry
        self._round_spec = round_spec
        self._grouping_result = grouping_result
        self._queue_index = queue_index
        self._queue_total = queue_total
        self._on_complete = on_complete

        self._store: DatasetStore | None = None
        self._controller: ThresholdQCController | None = None
        self._finished = False

    def start(self) -> None:
        """Load the dataset and start the interactive threshold QC controller."""
        # Clear stale layers from the previous dataset's threshold QC
        # (or from the seg QC session). The ThresholdQCController's
        # _cleanup_all removes its own temp layers when a dataset finishes,
        # but any mask layers written to the viewer by _finalize for the
        # PREVIOUS dataset would still be visible — and they don't belong
        # to the current dataset.
        try:
            viewer = self._viewer_win.viewer
            if viewer is not None:
                viewer.layers.clear()
        except Exception:
            pass

        # Set the viewer title so the user knows WHICH dataset + round
        # is being thresholded (issue: only group count was visible).
        try:
            self._viewer_win.set_subtitle(
                f"Threshold QC — {self._entry.name} — "
                f"round: {self._round_spec.name} "
                f"({self._queue_index + 1}/{self._queue_total})"
            )
        except Exception:
            pass

        try:
            self._store = DatasetStore(self._entry.h5_path)
            channel_idx = _channel_index(self._store, self._round_spec.channel)
            channel_image = self._store.read_channel("intensity", channel_idx)
            seg_labels = self._store.read_labels("cellpose_qc")
        except Exception as e:
            logger.exception(
                "threshold QC: failed to load %s for round %s",
                self._entry.name,
                self._round_spec.name,
            )
            self._finish(
                PhaseResult(
                    success=False,
                    message=f"load failed: {e}",
                )
            )
            return

        if seg_labels is None or int(seg_labels.max()) == 0:
            # Empty segmentation — skip QC, advance without writing masks.
            self._finish(
                PhaseResult(
                    success=True,
                    message="no cells to QC (auto-skip)",
                )
            )
            return

        try:
            self._controller = ThresholdQCController(
                viewer_win=self._viewer_win,
                data_model=self._data_model,
                store=self._store,
                grouping_result=self._grouping_result,
                channel_image=channel_image.astype(np.float32, copy=False),
                seg_labels=seg_labels.astype(np.int32, copy=False),
                channel=self._round_spec.channel,
                metric=self._round_spec.metric,
                sigma=self._round_spec.gaussian_sigma,
                mask_name=self._round_spec.name,
                on_complete=self._on_controller_complete,
                # Critical: do NOT write /measurements — the workflow
                # owns that artifact in its run folder. See
                # docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md
                write_measurements_to_store=False,
            )
        except Exception as e:
            logger.exception("failed to instantiate ThresholdQCController")
            self._finish(
                PhaseResult(
                    success=False,
                    message=f"controller init failed: {e}",
                )
            )
            return

        try:
            self._controller.start()
        except Exception as e:
            logger.exception("ThresholdQCController.start() raised")
            self._finish(
                PhaseResult(
                    success=False,
                    message=f"controller start failed: {e}",
                )
            )

    def _on_controller_complete(self, success: bool, msg: str) -> None:
        """Callback ThresholdQCController fires when the user finishes."""
        self._finish(PhaseResult(success=success, message=msg))

    def _finish(self, result: PhaseResult) -> None:
        if self._finished:
            return
        self._finished = True

        # Drop the controller reference — its _cleanup_all already ran
        # from its own _finish path before calling on_complete.
        self._controller = None

        cb = self._on_complete
        self._on_complete = None
        if cb is not None:
            try:
                cb(result)
            except Exception:
                logger.exception("threshold QC on_complete callback raised")
