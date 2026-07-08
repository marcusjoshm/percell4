"""Grouped Thresholding panel — expression-level grouping + per-group thresholding.

This is NOT cell segmentation (Cellpose/boundary drawing). Grouped thresholding
creates binary masks by intensity thresholding, grouping cells by expression
level to handle polyclonal data where a single global threshold fails.

Embedded as a sidebar tab in the launcher's Analysis section.
Receives dependencies via callbacks — no launcher reference.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.gui._grouped_threshold_settings import GroupedThresholdSettingsWidget
from percell4.gui._resource_name_prompt import prompt_for_resource_name
from percell4.model import CellDataModel

logger = logging.getLogger(__name__)


class GroupedSegPanel(QWidget):
    """Panel for grouped thresholding workflow."""

    def __init__(
        self,
        data_model: CellDataModel,
        *,
        get_store: Callable[[], Any | None] = lambda: None,
        get_viewer_window: Callable[[], Any | None] = lambda: None,
        show_status: Callable[[str], None] = lambda _: None,
        repopulate_viewer: Callable[[], None] = lambda: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.data_model = data_model
        self._get_store = get_store
        self._get_viewer_window = get_viewer_window
        self._show_status_cb = show_status
        # Restores the dataset's viewer layers after the per-timepoint QC (which
        # clears the viewer between frames). Wired from the launcher's
        # _populate_viewer_from_store.
        self._repopulate_viewer_cb = repopulate_viewer
        self._worker = None
        self._qc_controller = None
        self._tl_qc_entry = None  # holds the per-timepoint QC driver (anti-GC)
        self._tl_qc_mask_name: str | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignTop)

        # ── Settings (metric / algorithm / GMM-or-K-means / Gaussian sigma) ──
        # Single source of truth shared with the dilute-phase workflow.
        self._settings_widget = GroupedThresholdSettingsWidget(self)
        layout.addWidget(self._settings_widget)

        # ── Run button ──
        self._run_btn = QPushButton("Run Grouped Thresholding")
        from percell4.gui import theme

        self._run_btn.setStyleSheet(
            f"QPushButton {{ background-color: {theme.ACTION_GREEN}; color: white;"
            f" padding: 8px; font-weight: bold; border-radius: 4px; }}"
            f" QPushButton:hover {{ background-color: {theme.ACTION_GREEN_HOVER}; }}"
        )
        self._run_btn.clicked.connect(self._on_run)
        layout.addWidget(self._run_btn)

        # ── Status ──
        self._status = QLabel("Ready")
        self._status.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-style: italic;")
        layout.addWidget(self._status)

        layout.addStretch()

    # ── Slots ──

    def _show_status(self, msg: str) -> None:
        self._status.setText(msg)
        self._show_status_cb(msg)

    # ── Run workflow ──

    def _on_run(self) -> None:
        viewer_win = self._get_viewer_window()
        if viewer_win is None or viewer_win.viewer is None:
            self._show_status("Open a dataset in the viewer first")
            return

        store = self._get_store()
        if store is None:
            self._show_status("No dataset loaded")
            return

        # Read the active channel from Session (canonical — SessionWindow
        # is the only Selector site).
        channel = self.data_model.session.active_channel
        if not channel:
            self._show_status("Select a channel in the Session window first")
            return

        config = self._settings_widget.current_config()
        metric = config.metric
        sigma = config.sigma

        # Get the channel image
        channel_image = None
        for layer in viewer_win.viewer.layers:
            if layer.__class__.__name__ == "Image" and layer.name == channel:
                channel_image = layer.data
                break
        if channel_image is None:
            self._show_status(f"Channel '{channel}' not found in viewer")
            return

        # Get segmentation labels
        seg_name = self.data_model.active_segmentation
        if seg_name is None:
            self._show_status("No segmentation loaded. Run Cellpose first.")
            return
        labels_layer = None
        for layer in viewer_win.viewer.layers:
            if layer.name == seg_name:
                labels_layer = layer
                break
        if labels_layer is None:
            self._show_status(f"Segmentation '{seg_name}' not found in viewer")
            return
        seg_labels = labels_layer.data.astype(np.int32)

        # Prompt for mask name. Default "grouped"; refuse-and-re-prompt on
        # collision with any existing /masks/<name>. Mirrors the Apply
        # Current Phasor as Mask flow via the shared helper. Cancel aborts
        # the run before any measurement / grouping worker starts.
        existing_masks = store.list_masks() if hasattr(store, "list_masks") else []
        mask_name = prompt_for_resource_name(
            self,
            title="Save Grouped Thresholding Mask",
            label="Mask name:",
            default="grouped",
            existing_names=existing_masks,
        )
        if mask_name is None:
            return

        # Time-lapse: threshold EVERY timepoint. Drive one interactive QC per
        # timepoint (the user QCs each frame's groups in turn) and stack the
        # accepted per-frame masks into a (T,H,W) /masks/<name> resource -- the
        # same artifact the batch single-cell workflow produces. The single-
        # timepoint path below is unchanged.
        n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)
        if n_timepoints > 1:
            self._run_timelapse_grouped(
                store, viewer_win, channel, seg_name, mask_name, config
            )
            return

        # Check if measurement exists, auto-compute if needed
        col_name = f"{channel}_{metric}"
        df = self.data_model.df

        if df is None or df.empty:
            self._show_status("No measurements. Computing...")
            self._auto_measure_then_group(
                channel, channel_image, seg_labels, metric, sigma, mask_name,
            )
            return

        if col_name not in df.columns:
            self._show_status(f"Computing {metric} for {channel}...")
            self._auto_measure_then_group(
                channel, channel_image, seg_labels, metric, sigma, mask_name,
            )
            return

        # Measurements exist — proceed directly to grouping
        self._run_grouping(channel, channel_image, seg_labels, metric, sigma, mask_name)

    def _auto_measure_then_group(
        self, channel, channel_image, seg_labels, metric, sigma, mask_name,
    ) -> None:
        from percell4.domain.measure.measurer import measure_cells
        from percell4.gui.workers import Worker

        self._pending = {
            "channel": channel,
            "channel_image": channel_image,
            "seg_labels": seg_labels,
            "metric": metric,
            "sigma": sigma,
            "mask_name": mask_name,
        }

        self._worker = Worker(measure_cells, channel_image, seg_labels, metrics=[metric])
        self._worker.finished.connect(self._on_measure_done)
        self._worker.error.connect(self._on_measure_error)
        self._worker.start()

    def _on_measure_error(self, err) -> None:
        from percell4.gui.torch_error import handle_worker_error

        if not handle_worker_error(self, err, context="Measure"):
            self._show_status(f"Measure error: {err.exc_type}: {err.message}")

    def _on_measure_done(self, new_df) -> None:
        p = self._pending
        channel = p["channel"]
        metric = p["metric"]
        col_name = f"{channel}_{metric}"

        # Merge into existing DataFrame
        existing = self.data_model.df
        if existing is not None and not existing.empty:
            label_to_val = dict(zip(new_df["label"], new_df[metric]))
            df = existing.assign(**{col_name: existing["label"].map(label_to_val)})
        else:
            # First measurement — rename metric column with channel prefix
            df = new_df.rename(columns={metric: col_name})

        self.data_model.set_measurements(df)
        self._show_status(f"Measured {metric} for {channel}")
        self._run_grouping(
            p["channel"], p["channel_image"], p["seg_labels"],
            p["metric"], p["sigma"], p["mask_name"],
        )

    def _run_grouping(
        self, channel, channel_image, seg_labels, metric, sigma, mask_name,
    ) -> None:
        from percell4.gui.workers import Worker

        col_name = f"{channel}_{metric}"
        df = self.data_model.df

        # Extract values for filtered cells only
        if self.data_model.is_filtered:
            filtered = self.data_model.filtered_df
        else:
            filtered = df

        values = filtered[col_name].dropna().values.astype(np.float64)
        cell_labels = filtered.loc[filtered[col_name].notna(), "label"].values.astype(np.int32)

        if len(values) == 0:
            self._show_status("No valid measurements to group")
            return

        config = self._settings_widget.current_config()
        algo = config.algorithm
        self._show_status(f"Grouping {len(values)} cells with {algo}...")

        self._grouping_context = {
            "channel": channel,
            "channel_image": channel_image,
            "seg_labels": seg_labels,
            "metric": metric,
            "sigma": sigma,
            "mask_name": mask_name,
            "col_name": col_name,
        }

        if algo == "GMM":
            from percell4.domain.measure.grouper import group_cells_gmm
            self._worker = Worker(
                group_cells_gmm, values, cell_labels,
                criterion=config.gmm_criterion,
                max_components=config.gmm_max_components,
            )
        else:
            from percell4.domain.measure.grouper import group_cells_kmeans
            self._worker = Worker(
                group_cells_kmeans, values, cell_labels,
                n_clusters=config.kmeans_n_clusters,
            )

        self._worker.finished.connect(self._on_grouping_done)
        self._worker.error.connect(self._on_grouping_error)
        self._worker.start()

    def _on_grouping_error(self, err) -> None:
        from percell4.gui.torch_error import handle_worker_error

        if not handle_worker_error(self, err, context="Grouping"):
            self._show_status(f"Grouping error: {err.exc_type}: {err.message}")

    def _on_grouping_done(self, result) -> None:
        ctx = self._grouping_context
        self._show_status(
            f"Found {result.n_groups} groups "
            f"(means: {', '.join(f'{m:.1f}' for m in result.group_means)})"
        )

        # Launch the QC controller
        from percell4.gui.threshold_qc import ThresholdQCController

        viewer_win = self._get_viewer_window()
        store = self._get_store()

        self._qc_controller = ThresholdQCController(
            viewer_win=viewer_win,
            data_model=self.data_model,
            store=store,
            grouping_result=result,
            channel_image=ctx["channel_image"],
            seg_labels=ctx["seg_labels"],
            channel=ctx["channel"],
            metric=ctx["metric"],
            sigma=ctx["sigma"],
            mask_name=ctx["mask_name"],
            on_complete=self._on_qc_complete,
        )
        self._qc_controller.start()

    def _on_qc_complete(self, success: bool, msg: str) -> None:
        self._show_status(msg)
        self._qc_controller = None

    # ── Time-lapse: per-timepoint grouped thresholding ────────

    def _run_timelapse_grouped(
        self, store, viewer_win, channel, seg_name, mask_name, config
    ) -> None:
        """Threshold every timepoint via one interactive QC per frame.

        Builds the per-timepoint groupings (per-frame measure + cluster) and
        drives :class:`TimelapseThresholdQCQueueEntry`, which runs the single-
        frame QC controller once per timepoint and stacks the accepted per-frame
        masks into a ``(T, H, W)`` ``/masks/<name>`` resource (the same artifact
        the batch single-cell workflow produces).
        """
        from percell4.gui.workflows.single_cell.threshold_qc_queue import (
            TimelapseThresholdQCQueueEntry,
        )
        from percell4.workflows.models import (
            DatasetSource,
            GmmCriterion,
            ThresholdAlgorithm,
            ThresholdingRound,
            WorkflowDatasetEntry,
        )
        from percell4.workflows.phases import threshold_compute_one

        algo = (
            ThresholdAlgorithm.GMM
            if config.algorithm == "GMM"
            else ThresholdAlgorithm.KMEANS
        )
        try:
            round_spec = ThresholdingRound(
                name=mask_name,
                channel=channel,
                metric=config.metric,
                algorithm=algo,
                gmm_criterion=GmmCriterion(str(config.gmm_criterion).lower()),
                gmm_max_components=config.gmm_max_components,
                kmeans_n_clusters=config.kmeans_n_clusters,
                gaussian_sigma=config.sigma,
            )
        except ValueError as e:
            self._show_status(
                f"Cannot threshold per timepoint with these settings: {e}"
            )
            return

        # Per-frame measure + cluster -> dict[timepoint, GroupingResult].
        self._show_status("Grouping each timepoint…")
        grouping_by_timepoint, failure, msg = threshold_compute_one(
            store, round_spec, seg_name
        )
        if failure is not None or not isinstance(grouping_by_timepoint, dict):
            self._show_status(f"Grouping failed: {msg}")
            return

        entry = WorkflowDatasetEntry(
            name=store.path.stem,
            source=DatasetSource.H5_EXISTING,
            h5_path=store.path,
            channel_names=tuple(store.metadata.get("channel_names", [])),
            compress_plan=None,
        )

        self._show_status(
            f"QC each of {len(grouping_by_timepoint)} timepoint(s) — "
            "accept (Ctrl+Enter) to advance to the next frame."
        )
        self._tl_qc_mask_name = mask_name
        self._tl_qc_entry = TimelapseThresholdQCQueueEntry(
            viewer_win=viewer_win,
            data_model=self.data_model,
            entry=entry,
            round_spec=round_spec,
            grouping_by_timepoint=grouping_by_timepoint,
            queue_index=0,
            queue_total=1,
            on_complete=self._on_timelapse_qc_complete,
            seg_name=seg_name,
        )
        self._tl_qc_entry.start()

    def _on_timelapse_qc_complete(self, result) -> None:
        """After the per-timepoint QC: restore the viewer and select the mask.

        The QC driver cleared the viewer between frames and already wrote the
        ``(T, H, W)`` ``/masks/<name>`` + ``/groups/<name>`` resources; restore
        the dataset's layers and (on success) auto-select the new mask.
        """
        self._tl_qc_entry = None
        self._repopulate_viewer()
        if not getattr(result, "success", False):
            self._show_status(
                f"Grouped thresholding cancelled: {getattr(result, 'message', '')}"
            )
            return
        store = self._get_store()
        if store is not None:
            self.data_model.session.refresh_resource_lists(
                mask_names=store.list_masks()
            )
        if self._tl_qc_mask_name:
            self.data_model.set_active_mask(self._tl_qc_mask_name)
        self._show_status(
            f"Grouped thresholding done across timepoints: "
            f"'{self._tl_qc_mask_name}' — {getattr(result, 'message', '')}"
        )

    def _repopulate_viewer(self) -> None:
        try:
            self._repopulate_viewer_cb()
        except Exception:  # noqa: BLE001 — viewer restore is best-effort
            logger.exception("grouped threshold: viewer repopulate failed")
