"""DilutePhaseMaskController — the dilute-phase round state machine.

Owns the per-workflow in-memory state:

* ``_working_buffer`` — float32 copy of the seed channel image; each
  accepted round's dilated condensed mask is NaN-stamped into it so the
  next round's metric / σ-smoothing / per-pixel threshold are bounded
  to the still-dilute pixels.
* ``_cumulative_condensed`` — running boolean union of every accepted
  round's dilated mask; the final dilute mask is
  ``(seg_labels > 0) & ~cumulative_condensed``.
* ``_round_n`` / ``_locked_config`` / ``_dilation_radius_px`` —
  workflow-scoped configuration that's locked at Start (the panel
  enforces lock; the controller treats it as immutable).

Per round:

1. ``measure_cells(working_buffer, seg_labels, metrics=[metric])``
2. Drop NaN rows from the recomputed metric column.
3. ``Worker(group_cells_gmm | group_cells_kmeans, …)`` off the GUI thread.
4. On worker ``finished`` → spawn a :class:`ThresholdQCController` in
   ``persist_round_outputs=False`` mode wired to ``_on_round_qc_complete``.
5. On QC accept → ``dilation(mask, disk(radius))``; NaN-stamp the
   working buffer; union into cumulative; refresh transient layers.
6. On QC reject → no state change; round counter still signals.

On Done: compose ``in_cell & ~cumulative`` and hand off to
:class:`AcceptDiluteMask`. On Cancel: drop everything; zero ``.h5``
writes. ``_teardown`` is idempotent (``_torn_down`` flag) per the
modal-tool overlay pattern, and uses ``contextlib.suppress`` around
layer removal so Qt/napari teardown order quirks never escape.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray
from qtpy.QtCore import QObject, Signal

from percell4.adapters.hdf5_store import Hdf5DatasetRepository
from percell4.application.use_cases.accept_dilute_mask import AcceptDiluteMask
from percell4.domain.measure.grouper import (
    group_cells_gmm,
    group_cells_kmeans,
)
from percell4.domain.measure.measurer import measure_cells
from percell4.gui._grouped_threshold_settings import GroupedThresholdConfig
from percell4.gui.threshold_qc import ThresholdQCController
from percell4.gui.workers import Worker

if TYPE_CHECKING:
    from percell4.application.session import Session
    from percell4.domain.dataset import DatasetHandle
    from percell4.gui.viewer import ViewerWindow
    from percell4.model import CellDataModel
    from percell4.store import DatasetStore

logger = logging.getLogger(__name__)


# Transient napari layer names — leading underscore tags them as
# workflow-private so the launcher's segmentation classifier ignores
# them (per the codebase-wide convention documented in
# docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md).
_LAYER_WORKFLOW_VIEW = "_dilute_workflow_view"
_LAYER_WORKFLOW_CONDENSED = "_dilute_workflow_condensed"


class DilutePhaseMaskController(QObject):
    """Owns the dilute-phase round state machine.

    Constructed once per workflow run by the panel. The panel holds the
    only strong reference; teardown happens on Done / Cancel / error.
    """

    # Round counter (1-indexed) emitted after each round completes.
    round_complete = Signal(int)

    # Emitted exactly once at the end of a successful Done flow.
    workflow_done = Signal()

    # Emitted exactly once at the end of a Cancel flow.
    workflow_cancelled = Signal()

    # Emitted on a recoverable error (e.g. all-NaN round); the workflow
    # state is unchanged and the user may try another round.
    error = Signal(str)

    def __init__(
        self,
        viewer_win: "ViewerWindow",
        data_model: "CellDataModel",
        store: "DatasetStore",
        session: "Session",
        channel_image: NDArray[np.float32],
        seg_labels: NDArray[np.int32],
        locked_config: GroupedThresholdConfig,
        dilation_radius_px: int,
        final_mask_name: str,
        handle: "DatasetHandle",
        channel: str = "",
    ) -> None:
        super().__init__()
        self._viewer_win = viewer_win
        self._data_model = data_model
        self._store = store
        self._session = session
        self._seg_labels = seg_labels
        self._locked_config = locked_config
        self._dilation_radius_px = int(dilation_radius_px)
        self._final_mask_name = final_mask_name
        self._handle = handle
        # The session's active channel name. Threaded purely so the QC
        # modal's status text and (suppressed) /groups/<name> path key
        # use a real name. In persist_round_outputs=False mode the
        # /groups/ write doesn't happen, so a missing/empty value is
        # tolerable — but pass the real name when available.
        self._channel = channel

        # Round-scoped state.
        self._working_buffer: NDArray[np.float32] = (
            channel_image.copy().astype(np.float32)
        )
        self._cumulative_condensed: NDArray[np.bool_] = np.zeros(
            channel_image.shape, dtype=bool,
        )
        self._round_n: int = 0
        self._round_qc_controller: ThresholdQCController | None = None
        self._grouping_worker: Worker | None = None
        self._torn_down: bool = False

        # Capture initial contrast limits from the SEED image (not the
        # working buffer, which may already contain NaN if the caller
        # pre-subtracted). napari recomputes contrast on every add_image
        # call, so subsequent rounds with more NaN pixels would otherwise
        # drift visibly. Pin once here and pass on every round.
        finite = channel_image[np.isfinite(channel_image)]
        if finite.size > 0:
            self._initial_contrast_limits: tuple[float, float] | None = (
                float(finite.min()),
                float(finite.max()),
            )
        else:
            self._initial_contrast_limits = None

    # ── Public API ──────────────────────────────────────────────

    def start_round(self) -> None:
        """Kick off the next round's pipeline: metric → group → QC.

        Increments the round counter immediately so the panel's "running
        round N" label is correct even if the metric step fails fast.
        Failures emit ``error(...)`` and leave the buffer/cumulative
        untouched so the user may retry.
        """
        self._round_n += 1

        # Per-cell metric on the live working buffer. The builtin
        # metrics are already NaN-safe (np.nanmean / np.nansum etc.),
        # so cells that became all-NaN in earlier rounds drop out as
        # NaN rows here and we filter them below.
        metric = self._locked_config.metric
        try:
            df = measure_cells(
                self._working_buffer,
                self._seg_labels,
                metrics=[metric],
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("dilute round %d: measure_cells raised", self._round_n)
            self.error.emit(
                f"Round {self._round_n}: measure_cells failed — {exc}"
            )
            return

        if df.empty or metric not in df.columns:
            self.error.emit(
                f"Round {self._round_n} has insufficient cells with "
                "finite metric values — all cells may be fully subtracted"
            )
            return

        finite_mask = df[metric].notna()
        values = df.loc[finite_mask, metric].to_numpy(dtype=np.float64)
        cell_labels = df.loc[finite_mask, "label"].to_numpy(dtype=np.int32)

        if len(values) < 2:
            self.error.emit(
                f"Round {self._round_n} has insufficient cells with "
                "finite metric values — all cells may be fully subtracted"
            )
            return

        # Build the grouping worker. The chosen algorithm is locked at
        # workflow start; the panel guarantees ``locked_config`` is
        # immutable after Round 1.
        algorithm = self._locked_config.algorithm
        if algorithm == "GMM":
            worker = Worker(
                group_cells_gmm,
                values,
                cell_labels,
                criterion=self._locked_config.gmm_criterion,
                max_components=self._locked_config.gmm_max_components,
            )
        elif algorithm == "K-means":
            worker = Worker(
                group_cells_kmeans,
                values,
                cell_labels,
                n_clusters=self._locked_config.kmeans_n_clusters,
            )
        else:  # pragma: no cover - GroupedThresholdConfig restricts this
            self.error.emit(
                f"Round {self._round_n}: unknown algorithm "
                f"{algorithm!r} in locked config"
            )
            return

        worker.finished.connect(self._on_grouping_finished)
        worker.error.connect(self._on_grouping_error)
        self._grouping_worker = worker
        worker.start()

    def finish(self) -> None:
        """Persist the final dilute mask and tear down transient state."""
        in_cell = self._seg_labels > 0
        dilute_mask = in_cell & ~self._cumulative_condensed

        repo = Hdf5DatasetRepository()
        use_case = AcceptDiluteMask(repo=repo, session=self._session)
        try:
            use_case.execute(
                handle=self._handle,
                mask_name=self._final_mask_name,
                mask=dilute_mask,
            )
        except Exception as exc:
            logger.exception("dilute finish: AcceptDiluteMask raised")
            self.error.emit(f"Failed to save dilute mask: {exc}")
            # Don't tear down — the panel may want to retry or cancel.
            return

        self._teardown()
        self.workflow_done.emit()

    def cancel(self) -> None:
        """Drop all in-memory state without writing to the store."""
        self._teardown()
        self.workflow_cancelled.emit()

    # ── Worker → QC plumbing ────────────────────────────────────

    def _on_grouping_finished(self, grouping_result) -> None:
        """Worker finished — spawn the per-round ThresholdQCController."""
        # Release the worker reference; it has finished its lone task.
        self._grouping_worker = None

        if self._torn_down:
            # User cancelled mid-flight; ignore the late result.
            return

        try:
            qc = ThresholdQCController(
                viewer_win=self._viewer_win,
                data_model=self._data_model,
                store=self._store,
                grouping_result=grouping_result,
                channel_image=self._working_buffer,
                seg_labels=self._seg_labels,
                channel=self._channel or self._locked_config.metric,
                metric=self._locked_config.metric,
                sigma=self._locked_config.sigma,
                mask_name=f"_dilute_round_{self._round_n}",
                on_complete=self._on_round_qc_complete,
                persist_round_outputs=False,
            )
        except Exception as exc:
            logger.exception("dilute round %d: QC init failed", self._round_n)
            self.error.emit(
                f"Round {self._round_n}: threshold QC init failed — {exc}"
            )
            return

        self._round_qc_controller = qc
        try:
            qc.start()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("dilute round %d: QC start raised", self._round_n)
            self.error.emit(
                f"Round {self._round_n}: threshold QC start failed — {exc}"
            )

    def _on_grouping_error(self, worker_error) -> None:
        """Worker raised — surface and clear the worker reference."""
        self._grouping_worker = None
        msg = getattr(worker_error, "message", str(worker_error))
        self.error.emit(
            f"Round {self._round_n}: cell grouping failed — {msg}"
        )

    def _on_round_qc_complete(
        self,
        success: bool,
        msg: str,
        mask_or_none: NDArray | None,
    ) -> None:
        """ThresholdQCController has finished a round (accept or reject).

        On accept: dilate the accepted mask, NaN-stamp the working
        buffer, union into ``_cumulative_condensed``, and refresh the
        transient napari layers. On reject (or no mask handed back):
        leave state untouched. Either way, the round counter has
        already advanced and ``round_complete`` fires once.
        """
        # Release the QC reference — its own _cleanup_all ran in
        # _finish before calling us back.
        self._round_qc_controller = None

        if self._torn_down:
            return

        if (not success) or mask_or_none is None:
            logger.info(
                "dilute round %d: rejected (%s) — working buffer unchanged",
                self._round_n, msg,
            )
            self.round_complete.emit(self._round_n)
            return

        # ``skimage.morphology.dilation`` is the recommended replacement
        # for ``binary_dilation`` (scheduled for removal in 0.28); for a
        # symmetric ``disk`` footprint they are equivalent.
        from skimage.morphology import dilation, disk

        accepted = mask_or_none.astype(bool, copy=False)
        if self._dilation_radius_px > 0:
            dilated = dilation(
                accepted, footprint=disk(self._dilation_radius_px),
            ).astype(bool, copy=False)
        else:
            dilated = accepted

        # NaN-stamp the working buffer at every dilated pixel. Downstream
        # consumers (next round's measure_cells + apply_gaussian_smoothing
        # + per-pixel threshold) are all NaN-aware via U1+U2+U3.
        self._working_buffer[dilated] = np.nan
        self._cumulative_condensed |= dilated

        self._refresh_transient_layers()
        self.round_complete.emit(self._round_n)

    # ── Viewer side effects ─────────────────────────────────────

    def _refresh_transient_layers(self) -> None:
        """Drop and re-add ``_dilute_workflow_view`` + condensed overlay.

        Both layers are leading-underscore transient. ``contrast_limits``
        is pinned from the seed image so napari doesn't drift as more
        pixels become NaN across rounds.
        """
        viewer = getattr(self._viewer_win, "viewer", None)
        if viewer is None:
            return

        self._remove_layer(_LAYER_WORKFLOW_VIEW)
        self._remove_layer(_LAYER_WORKFLOW_CONDENSED)

        add_image_kwargs: dict = {"name": _LAYER_WORKFLOW_VIEW}
        if self._initial_contrast_limits is not None:
            add_image_kwargs["contrast_limits"] = list(
                self._initial_contrast_limits,
            )
        with contextlib.suppress(Exception):
            viewer.add_image(self._working_buffer, **add_image_kwargs)

        with contextlib.suppress(Exception):
            viewer.add_labels(
                self._cumulative_condensed.astype(np.uint8),
                name=_LAYER_WORKFLOW_CONDENSED,
            )

    def _remove_layer(self, name: str) -> None:
        """Remove a named layer if present; tolerate napari/Qt teardown."""
        viewer = getattr(self._viewer_win, "viewer", None)
        if viewer is None:
            return
        try:
            layers = list(viewer.layers)
        except Exception:
            return
        for layer in layers:
            if getattr(layer, "name", None) == name:
                with contextlib.suppress(Exception):
                    viewer.layers.remove(layer)

    # ── Teardown ────────────────────────────────────────────────

    def _teardown(self) -> None:
        """Idempotently drop transient layers and worker/QC references."""
        if self._torn_down:
            return
        self._torn_down = True

        # Remove transient layers — both are leading-underscore
        # workflow-private. Wrap in suppress; layer-removal during Qt
        # shutdown can raise spuriously.
        with contextlib.suppress(Exception):
            self._remove_layer(_LAYER_WORKFLOW_VIEW)
        with contextlib.suppress(Exception):
            self._remove_layer(_LAYER_WORKFLOW_CONDENSED)

        # Clear references. If the QC controller is still active (e.g.
        # Cancel pressed while the QC dock is open), let it tear its
        # own layers down.
        if self._round_qc_controller is not None:
            with contextlib.suppress(Exception):
                self._round_qc_controller._cleanup_all()
            self._round_qc_controller = None

        # A pending worker has nothing to clean up — the run completes
        # on its own thread. Just drop our reference.
        self._grouping_worker = None
