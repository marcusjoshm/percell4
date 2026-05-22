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
import pandas as pd

from percell4.gui.threshold_qc import ThresholdQCController
from percell4.gui.workflows.base_runner import PhaseResult
from percell4.domain.measure.grouper import GroupingResult
from percell4.store import DatasetStore
from percell4.workflows.models import ThresholdingRound, WorkflowDatasetEntry
from percell4.workflows.phases import _channel_from_frame, _channel_index

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
        seg_name: str = "cellpose_qc",
    ) -> None:
        self._viewer_win = viewer_win
        self._data_model = data_model
        self._entry = entry
        self._round_spec = round_spec
        self._grouping_result = grouping_result
        self._queue_index = queue_index
        self._queue_total = queue_total
        self._on_complete = on_complete
        self._seg_name = seg_name

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
            seg_labels = self._store.read_labels(self._seg_name)
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

        # Add the channel image as a background layer in the viewer so
        # the user sees the actual data underneath the _group_preview
        # overlay. The ThresholdQCController only adds the group-preview
        # labels layer; without this the viewer would show a blank canvas
        # with colored cells floating in the void.
        try:
            self._viewer_win.add_image(
                channel_image,
                name=f"{self._round_spec.channel} ({self._entry.name})",
            )
        except Exception:
            logger.exception("failed to add channel image layer for threshold QC")

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


class TimelapseThresholdQCQueueEntry:
    """Per-timepoint interactive threshold QC for a time-lapse dataset.

    For a time-lapse dataset the grouping is a ``dict[int, GroupingResult]``
    keyed by timepoint (from :func:`threshold_compute_one`). This wrapper runs
    the single-frame :class:`ThresholdQCController` once per timepoint, in
    order — the user QCs one frame's groups, accepts, and the next timepoint
    opens — accumulating each frame's accepted 2D mask.

    After the final timepoint it writes the ``(T, H, W)`` ``/masks/<round>``
    resource plus the concatenated ``/groups/<round>`` table (carrying a
    ``timepoint`` column) — byte-for-byte the same shape the headless
    per-frame path (:func:`apply_threshold_headless`) produces, so every
    downstream phase (measure, export) is unchanged regardless of whether the
    masks came from interactive QC or headless Otsu.

    Each controller runs with ``persist_round_outputs=False``: it hands its
    accepted union back through the arity-3 ``on_complete`` and writes nothing
    itself. Persistence happens once, here, after all frames are done.
    """

    def __init__(
        self,
        *,
        viewer_win,
        data_model,
        entry: WorkflowDatasetEntry,
        round_spec: ThresholdingRound,
        grouping_by_timepoint: dict[int, GroupingResult],
        queue_index: int,
        queue_total: int,
        on_complete: Callable[[PhaseResult], None],
        seg_name: str = "cellpose_qc",
    ) -> None:
        self._viewer_win = viewer_win
        self._data_model = data_model
        self._entry = entry
        self._round_spec = round_spec
        self._grouping_by_timepoint = grouping_by_timepoint
        self._queue_index = queue_index
        self._queue_total = queue_total
        self._on_complete = on_complete
        self._seg_name = seg_name

        self._store: DatasetStore | None = None
        self._channel_idx: int | None = None
        self._n_timepoints = 0
        self._cursor = 0
        self._mask_frames: dict[int, np.ndarray] = {}
        self._group_dfs: list[pd.DataFrame] = []
        self._controller: ThresholdQCController | None = None
        self._finished = False

    def start(self) -> None:
        """Open the dataset and begin QC at the first timepoint."""
        try:
            self._store = DatasetStore(self._entry.h5_path)
            self._channel_idx = _channel_index(self._store, self._round_spec.channel)
            self._n_timepoints = int(
                self._store.metadata.get("n_timepoints", 1) or 1
            )
        except Exception as e:
            logger.exception(
                "timelapse threshold QC: failed to load %s for round %s",
                self._entry.name,
                self._round_spec.name,
            )
            self._finish(PhaseResult(success=False, message=f"load failed: {e}"))
            return
        self._process_next()

    def _process_next(self) -> None:
        """Open the controller for the next timepoint, or persist + finish."""
        if self._cursor >= self._n_timepoints:
            self._persist_and_finish()
            return

        t = self._cursor

        # Clear the previous frame's layers — the controller's _cleanup_all
        # removes its own temp layers, but the channel image WE add stays.
        try:
            viewer = self._viewer_win.viewer
            if viewer is not None:
                viewer.layers.clear()
        except Exception:
            pass

        try:
            self._viewer_win.set_subtitle(
                f"Threshold QC — {self._entry.name} — round: {self._round_spec.name} "
                f"— timepoint {t + 1}/{self._n_timepoints} "
                f"({self._queue_index + 1}/{self._queue_total})"
            )
        except Exception:
            pass

        try:
            labels = self._store.read_labels(self._seg_name, timepoint=t)
            image = _channel_from_frame(
                self._store.read_array_frame("intensity", t), self._channel_idx
            )
        except Exception as e:
            logger.exception(
                "timelapse threshold QC: failed to load frame %d", t
            )
            self._finish(
                PhaseResult(success=False, message=f"frame {t} load failed: {e}")
            )
            return

        grouping = self._grouping_by_timepoint.get(t)
        if grouping is None or labels is None or int(labels.max()) == 0:
            # No groupable cells in this frame — empty mask, no group rows.
            # Mirrors apply_threshold_headless's per-frame skip.
            if labels is not None:
                self._mask_frames[t] = np.zeros(labels.shape, dtype=np.uint8)
            self._cursor += 1
            self._process_next()
            return

        # Background channel layer so the user sees the data under the
        # group-preview overlay the controller adds.
        try:
            self._viewer_win.add_image(
                image,
                name=f"{self._round_spec.channel} ({self._entry.name}) t{t}",
            )
        except Exception:
            logger.exception("failed to add channel image layer for threshold QC")

        try:
            self._controller = ThresholdQCController(
                viewer_win=self._viewer_win,
                data_model=self._data_model,
                store=self._store,
                grouping_result=grouping,
                channel_image=image.astype(np.float32, copy=False),
                seg_labels=labels.astype(np.int32, copy=False),
                channel=self._round_spec.channel,
                metric=self._round_spec.metric,
                sigma=self._round_spec.gaussian_sigma,
                mask_name=self._round_spec.name,
                on_complete=self._on_frame_complete,
                # The workflow owns /measurements; this wrapper owns the
                # (T,H,W) /masks + /groups writes (deferred to _persist_and_finish).
                write_measurements_to_store=False,
                persist_round_outputs=False,
            )
        except Exception as e:
            logger.exception(
                "timelapse threshold QC: controller init failed at t=%d", t
            )
            self._finish(
                PhaseResult(success=False, message=f"controller t{t} init failed: {e}")
            )
            return

        try:
            self._controller.start()
        except Exception as e:
            logger.exception(
                "timelapse threshold QC: controller start raised at t=%d", t
            )
            self._finish(
                PhaseResult(success=False, message=f"controller t{t} start failed: {e}")
            )

    def _on_frame_complete(
        self, success: bool, msg: str, mask: np.ndarray | None
    ) -> None:
        """Arity-3 callback the per-frame ThresholdQCController fires.

        ``mask`` is the accepted 2D union for the current timepoint (the
        controller built it but did not persist it, since
        ``persist_round_outputs=False``).
        """
        t = self._cursor

        if not success:
            # User cancelled / skipped — abort the whole dataset's round.
            self._finish(PhaseResult(success=False, message=msg))
            return

        grouping = self._grouping_by_timepoint.get(t)
        if mask is not None:
            self._mask_frames[t] = np.asarray(mask, dtype=np.uint8)

        # Build this frame's group-assignment rows (label -> group) in the
        # same shape the headless path writes, tagged with the timepoint.
        if grouping is not None:
            col_name = f"group_{self._round_spec.channel}_{self._round_spec.metric}"
            gdf = grouping.group_assignments.reset_index()
            gdf.columns = ["label", col_name]
            gdf["timepoint"] = t
            self._group_dfs.append(gdf)

        self._controller = None
        self._cursor += 1
        self._process_next()

    def _persist_and_finish(self) -> None:
        """Stack per-frame masks into (T,H,W) and write /masks + /groups."""
        try:
            # Determine the frame shape from the first mask we captured.
            frame_shape = None
            for t in range(self._n_timepoints):
                m = self._mask_frames.get(t)
                if m is not None:
                    frame_shape = m.shape
                    break
            if frame_shape is None:
                self._finish(
                    PhaseResult(
                        success=True,
                        message="no cells to QC in any timepoint (auto-skip)",
                    )
                )
                return

            frames = [
                self._mask_frames.get(t, np.zeros(frame_shape, dtype=np.uint8)).astype(
                    np.uint8
                )
                for t in range(self._n_timepoints)
            ]
            combined = np.stack(frames, axis=0).astype(np.uint8)  # (T, H, W)
            self._store.write_mask(self._round_spec.name, combined)

            col_name = f"group_{self._round_spec.channel}_{self._round_spec.metric}"
            groups_all = (
                pd.concat(self._group_dfs, ignore_index=True)
                if self._group_dfs
                else pd.DataFrame(columns=["label", col_name, "timepoint"])
            )
            self._store.write_dataframe(
                f"/groups/{self._round_spec.name}", groups_all
            )
        except Exception as e:
            logger.exception("timelapse threshold QC: persist failed")
            self._finish(PhaseResult(success=False, message=f"persist failed: {e}"))
            return

        self._finish(
            PhaseResult(
                success=True,
                message=(
                    f"{int(combined.sum())} positive pixels across "
                    f"{len(self._group_dfs)} timepoint(s)"
                ),
            )
        )

    def _finish(self, result: PhaseResult) -> None:
        if self._finished:
            return
        self._finished = True
        self._controller = None

        cb = self._on_complete
        self._on_complete = None
        if cb is not None:
            try:
                cb(result)
            except Exception:
                logger.exception(
                    "timelapse threshold QC on_complete callback raised"
                )
