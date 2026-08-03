"""Per-dataset wrapper around :class:`DilutePhaseMaskController` for the runner.

Sibling of :class:`ThresholdQCQueueEntry` for the new Phase 5
(dilute-phase mask). The single-dataset
:class:`DilutePhaseMaskController` already handles the per-round flow
(metric → group → QC → dilate → NaN-subtract → repeat). This wrapper
adapts it to the workflow runner's per-dataset interactive
``PhaseRequest`` protocol:

- Loads the dataset's channel image (at ``dilute_settings.channel``)
  and the post-QC labels (``/labels/cellpose_qc``).
- Constructs a :class:`GroupedThresholdConfig` from the canonical
  :class:`DiluteSettings` carried on ``WorkflowConfig``.
- Instantiates :class:`DilutePhaseMaskController` with
  ``session_free=True`` — the launcher session must NOT be mutated
  per-dataset during a batch run (origin Tier 1 finding from
  ce-doc-review).
- Builds a minimal queue-aware dock window (``Run another round /
  Done — save and continue / Cancel run``) and forwards controller
  signals into the runner's :class:`PhaseResult` callback.
- Tracks the per-dataset round count and writes it to
  ``RunMetadata.per_dataset_dilute_round_counts`` at workflow_done,
  so :mod:`percell4.workflows.phases.export_run` can populate
  ``summary_datasets.csv``'s ``n_rounds_dilute`` column.

Tier 2 UX decisions documented in the U5 commit:

- Cancel button cancels the **entire run** (matches the seg-QC /
  threshold-QC convention via :attr:`PhaseResult.cancelled`).
  Users advance individual datasets via "Done — save and continue"
  (which saves the mask) rather than a "skip this dataset" button.
- Auto-starts round 1 when the dock opens — the researcher has
  already committed to dilute at workflow configure time; no extra
  click required.
- Between-round dock state: status text updates after each
  ``round_complete``; all three buttons are enabled (the QC modal
  hides itself between rounds, so the dock owns the next-action
  decision).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
from qtpy.QtCore import QObject, Qt
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.domain.dataset import DatasetHandle
from percell4.gui._grouped_threshold_settings import GroupedThresholdConfig
from percell4.gui.workflows.base_runner import PhaseResult
from percell4.gui.workflows.dilute_phase.controller import DilutePhaseMaskController
from percell4.store import DatasetStore
from percell4.workflows.models import (
    DiluteSettings,
    ThresholdAlgorithm,
    WorkflowDatasetEntry,
)
from percell4.workflows.phases import _channel_index

if TYPE_CHECKING:
    from percell4.application.session import Session
    from percell4.gui.viewer import ViewerWindow
    from percell4.model import CellDataModel

logger = logging.getLogger(__name__)


# ── DiluteSettings → GroupedThresholdConfig adapter ─────────────────────


_ALGORITHM_TO_GUI_LABEL: dict[ThresholdAlgorithm, str] = {
    ThresholdAlgorithm.GMM: "GMM",
    ThresholdAlgorithm.KMEANS: "K-means",
}


def _to_grouped_config(settings: DiluteSettings) -> GroupedThresholdConfig:
    """Translate :class:`DiluteSettings` to the controller's locked-config form.

    ``GroupedThresholdConfig`` is the GUI-snapshot form used by the
    single-dataset dilute UI and the controller. It carries
    ``algorithm`` as the user-facing label (``"GMM"`` / ``"K-means"``)
    rather than the canonical ``ThresholdAlgorithm`` StrEnum. This
    adapter is the one place the workflow's canonical encoding meets
    the controller's legacy GUI-label encoding — see plan's Tier 3
    finding from ce-doc-review for the rationale (DiluteSettings
    deliberately uses the canonical form so ``run_config.json`` has
    one consistent algorithm shape across rounds and dilute).
    """
    return GroupedThresholdConfig(
        metric=settings.metric,
        algorithm=_ALGORITHM_TO_GUI_LABEL[settings.algorithm],
        gmm_criterion=settings.gmm_criterion.value,
        gmm_max_components=settings.gmm_max_components,
        kmeans_n_clusters=settings.kmeans_n_clusters,
        sigma=settings.gaussian_sigma,
    )


# ── Queue entry ────────────────────────────────────────────────────────


class DilutePhaseQueueEntry(QObject):
    """Drives one dataset through the dilute-phase controller for Phase 5.

    Unlike :class:`ThresholdQCQueueEntry` (a plain class), this wrapper
    is a ``QObject`` because :class:`DilutePhaseMaskController` emits
    Qt signals (``round_complete``, ``workflow_done``,
    ``workflow_cancelled``, ``error``). The wrapper holds Qt slots that
    forward those signals into the runner's ``on_complete`` callback.
    """

    def __init__(
        self,
        *,
        entry: WorkflowDatasetEntry,
        dilute_settings: DiluteSettings,
        viewer_win: ViewerWindow,
        data_model: CellDataModel,
        session: Session,
        queue_index: int,
        queue_total: int,
        on_complete: Callable[[PhaseResult], None],
        on_round_complete: Callable[[str, int], None] | None = None,
        seg_name: str = "cellpose_qc",
    ) -> None:
        super().__init__()
        self._entry = entry
        self._dilute_settings = dilute_settings
        self._viewer_win = viewer_win
        self._data_model = data_model
        self._session = session
        self._queue_index = queue_index
        self._queue_total = queue_total
        self._on_complete = on_complete
        self._seg_name = seg_name
        # Optional per-dataset round-count callback — the runner uses
        # this to persist completed counts into
        # RunMetadata.per_dataset_dilute_round_counts at workflow_done.
        self._on_round_complete = on_round_complete

        self._store: DatasetStore | None = None
        self._controller: DilutePhaseMaskController | None = None
        self._dock: QMainWindow | None = None
        self._status_label: QLabel | None = None
        self._btn_run: QPushButton | None = None
        self._btn_done: QPushButton | None = None
        self._btn_cancel: QPushButton | None = None
        self._rounds_completed = 0
        self._finished = False

    # ── Public API ───────────────────────────────────────────

    def start(self) -> None:
        """Open the dataset, build the dock, instantiate the controller.

        Auto-starts round 1 once setup is complete — the researcher
        already committed to dilute when they enabled it in the config
        dialog; no separate "begin" click required.
        """
        # Clear any layers left over from previous datasets / phases.
        try:
            viewer = self._viewer_win.viewer
            if viewer is not None:
                viewer.layers.clear()
        except Exception:
            pass

        try:
            self._viewer_win.set_subtitle(
                f"Dilute Phase 5 — {self._entry.name} "
                f"({self._queue_index + 1}/{self._queue_total})"
            )
        except Exception:
            pass

        try:
            self._store = DatasetStore(self._entry.h5_path)
            channel_idx = _channel_index(self._store, self._dilute_settings.channel)
            # The dilute controller is single-frame. On a time-lapse dataset
            # operate on frame 0 (read_channel requires an explicit timepoint on
            # a time-stacked array, U1); the dilute mask is stored 2D
            # (time-invariant). Full per-frame (T,H,W) dilute is a deferred
            # follow-up (mirror TimelapseThresholdQCQueueEntry).
            n_timepoints = int(self._store.metadata.get("n_timepoints", 1) or 1)
            tp = 0 if n_timepoints > 1 else None
            channel_image = self._store.read_channel(
                "intensity", channel_idx, timepoint=tp
            )
            seg_labels = self._store.read_labels(self._seg_name, timepoint=tp)
        except Exception as e:
            logger.exception(
                "dilute queue: failed to load %s for dilute phase",
                self._entry.name,
            )
            self._finish(
                PhaseResult(success=False, message=f"load failed: {e}")
            )
            return

        if seg_labels is None or int(seg_labels.max()) == 0:
            # No cells to dilute — skip this dataset with a soft pass.
            self._finish(
                PhaseResult(success=True, message="no cells to dilute (auto-skip)")
            )
            return

        # Provide a background image layer so the user sees their data
        # behind the controller's transient overlays.
        try:
            self._viewer_win.add_image(
                channel_image,
                name=(
                    f"{self._dilute_settings.channel} "
                    f"({self._entry.name})"
                ),
            )
        except Exception:
            logger.exception("dilute queue: failed to add channel image layer")

        # Build the controller in session-free mode (batch-mode carve-out).
        handle = DatasetHandle(
            path=self._entry.h5_path,
            metadata={"channel_names": list(self._entry.channel_names)},
        )
        try:
            self._controller = DilutePhaseMaskController(
                viewer_win=self._viewer_win,
                data_model=self._data_model,
                store=self._store,
                session=self._session,
                channel_image=channel_image.astype(np.float32, copy=False),
                seg_labels=seg_labels.astype(np.int32, copy=False),
                locked_config=_to_grouped_config(self._dilute_settings),
                dilation_radius_px=self._dilute_settings.dilation_radius_px,
                final_mask_name=self._dilute_settings.mask_name,
                handle=handle,
                channel=self._dilute_settings.channel,
                session_free=True,
            )
        except Exception as e:
            logger.exception("dilute queue: controller init failed")
            self._finish(
                PhaseResult(
                    success=False,
                    message=f"controller init failed: {e}",
                )
            )
            return

        # Connect controller signals into our forwarding slots.
        self._controller.round_complete.connect(self._on_round_complete_slot)
        self._controller.workflow_done.connect(self._on_workflow_done_slot)
        self._controller.workflow_cancelled.connect(self._on_workflow_cancelled_slot)
        self._controller.error.connect(self._on_error_slot)

        # Build the queue dock UI.
        self._dock = self._build_dock()
        self._dock.show()
        try:
            self._dock.raise_()
            self._dock.activateWindow()
        except Exception:
            pass

        # Auto-start round 1 — see module docstring for rationale.
        self._set_status(
            f"Round 1 in progress — {self._entry.name} "
            f"({self._queue_index + 1}/{self._queue_total})"
        )
        self._set_buttons_enabled(False)
        try:
            self._controller.start_round()
        except Exception as e:
            logger.exception("dilute queue: start_round raised")
            self._finish(
                PhaseResult(
                    success=False,
                    message=f"start_round failed: {e}",
                )
            )

    def cancel(self) -> None:
        """External cancel — forward to the controller."""
        if self._controller is not None:
            try:
                self._controller.cancel()
            except Exception:
                logger.exception("dilute queue: cancel forwarding failed")

    # ── Dock construction ────────────────────────────────────

    def _build_dock(self) -> QMainWindow:
        win = QMainWindow()
        win.setWindowFlag(Qt.Window, True)
        win.setWindowTitle(
            f"Dilute-phase mask — {self._entry.name} "
            f"({self._queue_index + 1}/{self._queue_total})"
        )
        win.setMinimumWidth(420)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        self._status_label = QLabel("Initializing…")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        info = QLabel(
            f"Dilute mask: <b>{self._dilute_settings.mask_name}</b><br>"
            f"Channel: {self._dilute_settings.channel} &nbsp; "
            f"Dilation: {self._dilute_settings.dilation_radius_px}px &nbsp; "
            f"Metric: {self._dilute_settings.metric}"
        )
        info.setStyleSheet("color: #888;")
        layout.addWidget(info)

        btn_row = QHBoxLayout()
        self._btn_run = QPushButton("Run another round")
        self._btn_run.setToolTip(
            "Compute the next round's per-cell metric, cluster cells, "
            "and open the threshold QC modal."
        )
        self._btn_run.clicked.connect(self._on_run_clicked)
        btn_row.addWidget(self._btn_run)

        self._btn_done = QPushButton("Done — save and continue")
        self._btn_done.setToolTip(
            "Save the accumulated dilute mask to /masks/<name> and "
            "advance to the next dataset."
        )
        self._btn_done.clicked.connect(self._on_done_clicked)
        btn_row.addWidget(self._btn_done)

        self._btn_cancel = QPushButton("Cancel run")
        self._btn_cancel.setToolTip(
            "Cancel the entire workflow run. No mask is saved for this "
            "dataset and downstream phases (measurement, export) are "
            "skipped."
        )
        self._btn_cancel.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self._btn_cancel)

        layout.addLayout(btn_row)
        win.setCentralWidget(central)
        return win

    def _set_status(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(text)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for btn in (self._btn_run, self._btn_done, self._btn_cancel):
            if btn is not None:
                btn.setEnabled(enabled)

    # ── Button slots ─────────────────────────────────────────

    def _on_run_clicked(self) -> None:
        if self._controller is None:
            return
        n = self._rounds_completed + 1
        self._set_status(f"Round {n} in progress…")
        self._set_buttons_enabled(False)
        try:
            self._controller.start_round()
        except Exception as e:
            logger.exception("dilute queue: start_round raised")
            self._on_error_slot(f"start_round failed: {e}")

    def _on_done_clicked(self) -> None:
        if self._controller is None:
            return
        self._set_status("Saving dilute mask…")
        self._set_buttons_enabled(False)
        try:
            self._controller.finish()
        except Exception as e:
            logger.exception("dilute queue: finish raised")
            self._on_error_slot(f"finish failed: {e}")

    def _on_cancel_clicked(self) -> None:
        if self._controller is None:
            return
        self._set_status("Cancelling…")
        self._set_buttons_enabled(False)
        try:
            self._controller.cancel()
        except Exception:
            logger.exception("dilute queue: cancel raised")
            # Force a cancel even if the controller misbehaved.
            self._on_workflow_cancelled_slot()

    # ── Controller signal slots ──────────────────────────────

    def _on_round_complete_slot(self, n: int) -> None:
        self._rounds_completed = int(n)
        self._set_status(
            f"Round {n} accepted. Cumulative dilute mask updated. "
            f"Choose next action."
        )
        self._set_buttons_enabled(True)

    def _on_workflow_done_slot(self) -> None:
        # Pass the per-dataset round count to the runner so it can
        # populate RunMetadata.per_dataset_dilute_round_counts.
        if self._on_round_complete is not None:
            try:
                self._on_round_complete(self._entry.name, self._rounds_completed)
            except Exception:
                logger.exception("dilute queue: round-count callback raised")
        self._finish(
            PhaseResult(
                success=True,
                message=(
                    f"saved /masks/{self._dilute_settings.mask_name} "
                    f"after {self._rounds_completed} rounds"
                ),
            )
        )

    def _on_workflow_cancelled_slot(self) -> None:
        self._finish(
            PhaseResult(
                success=False,
                message="user cancelled during dilute phase",
                cancelled=True,
            )
        )

    def _on_error_slot(self, msg: str) -> None:
        # Errors are non-fatal at the controller level (the user can
        # try another round). Surface in status and re-enable buttons
        # so the user can retry or cancel.
        self._set_status(f"Error: {msg}")
        self._set_buttons_enabled(True)

    # ── Cleanup ──────────────────────────────────────────────

    def _finish(self, result: PhaseResult) -> None:
        if self._finished:
            return
        self._finished = True

        # Close the dock and release Qt references.
        try:
            if self._dock is not None:
                self._dock.close()
                self._dock = None
        except Exception:
            logger.exception("dilute queue: dock close raised")

        # Drop the controller — its own _teardown ran in finish/cancel
        # before emitting the terminal signal.
        self._controller = None

        cb = self._on_complete
        self._on_complete = None
        if cb is not None:
            try:
                cb(result)
            except Exception:
                logger.exception("dilute queue: on_complete callback raised")
