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
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.gui._grouped_threshold_settings import GroupedThresholdSettingsWidget
from percell4.gui._resource_name_prompt import prompt_for_resource_name
from percell4.model import CellDataModel

logger = logging.getLogger(__name__)


def slice_to_active_frame(channel_image, seg_labels, timepoint):
    """Slice a ``(T, H, W)`` channel and/or labels to the active 2D frame.

    Grouped thresholding's interactive QC is single-frame. On a time-lapse
    dataset both the channel layer and a ``(T, H, W)`` labels stack are sliced
    to the displayed timepoint so the 2D measurer/QC never receive a
    ``(T, H, W)`` stack against 2D labels — the source of the
    ``IndexError: ... dimension is 6 but corresponding boolean dimension is 485``
    crash. A 2D *time-invariant* label is left as-is. Returns
    ``(channel_2d, labels_2d)`` (int32 labels preserved). Pure (no Qt).
    """
    ch = np.asarray(channel_image)
    if ch.ndim == 3:
        ch = ch[timepoint]
    lbl = np.asarray(seg_labels)
    if lbl.ndim == 3:
        lbl = lbl[timepoint]
    return ch, lbl


class GroupedSegPanel(QWidget):
    """Panel for grouped thresholding workflow."""

    def __init__(
        self,
        data_model: CellDataModel,
        *,
        get_store: Callable[[], Any | None] = lambda: None,
        get_viewer_window: Callable[[], Any | None] = lambda: None,
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.data_model = data_model
        self._get_store = get_store
        self._get_viewer_window = get_viewer_window
        self._show_status_cb = show_status
        self._worker = None
        self._qc_controller = None

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

        # Time-lapse: the interactive measure -> group -> QC flow is single-frame.
        # Slice the channel and (T,H,W) labels to the displayed timepoint so the
        # 2D measurer never receives a (T,H,W) stack (the '6 vs 485' IndexError).
        # The accepted mask is stored 2D (time-invariant) for that frame's
        # grouping; per-frame (T,H,W) grouped masks are produced by the batch
        # workflow runner (see TimelapseThresholdQCQueueEntry).
        n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)
        if n_timepoints > 1:
            t = int(viewer_win.viewer.dims.current_step[0])
            channel_image, seg_labels = slice_to_active_frame(
                channel_image, seg_labels, t
            )

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
        from percell4.gui.workers import Worker
        from percell4.domain.measure.measurer import measure_cells

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
