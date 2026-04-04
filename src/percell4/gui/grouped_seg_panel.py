"""Grouped Thresholding panel — expression-level grouping + per-group thresholding.

This is NOT cell segmentation (Cellpose/boundary drawing). Grouped thresholding
creates binary masks by intensity thresholding, grouping cells by expression
level to handle polyclonal data where a single global threshold fails.

Embedded as a sidebar tab in the launcher's Workflows section.
Communicates with the viewer and store via the launcher reference.
"""

from __future__ import annotations

import logging

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.measure.metrics import BUILTIN_METRICS
from percell4.model import CellDataModel

logger = logging.getLogger(__name__)


class GroupedSegPanel(QWidget):
    """Panel for grouped thresholding workflow.

    Designed to be embedded in the launcher's Workflows tab.
    """

    def __init__(
        self,
        data_model: CellDataModel,
        launcher=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.data_model = data_model
        self._launcher = launcher
        self._worker = None
        self._qc_controller = None

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignTop)

        title = QLabel("Grouped Thresholding")
        title.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #ffffff;"
            " margin-bottom: 12px; padding-bottom: 4px;"
            " border-bottom: 1px solid #3a3a3a;"
        )
        layout.addWidget(title)

        # ── Channel selector ──
        chan_row = QHBoxLayout()
        chan_row.addWidget(QLabel("Channel:"))
        self._channel_combo = QComboBox()
        chan_row.addWidget(self._channel_combo)
        layout.addLayout(chan_row)

        # ── Metric selector ──
        metric_row = QHBoxLayout()
        metric_row.addWidget(QLabel("Metric:"))
        self._metric_combo = QComboBox()
        self._metric_combo.addItems(list(BUILTIN_METRICS.keys()))
        self._metric_combo.setCurrentText("mean_intensity")
        metric_row.addWidget(self._metric_combo)
        layout.addLayout(metric_row)

        # ── Algorithm selector ──
        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel("Algorithm:"))
        self._algo_combo = QComboBox()
        self._algo_combo.addItems(["GMM", "K-means"])
        self._algo_combo.currentTextChanged.connect(self._on_algorithm_changed)
        algo_row.addWidget(self._algo_combo)
        layout.addLayout(algo_row)

        # ── GMM Options ──
        self._gmm_group = QGroupBox("GMM Options")
        gmm_layout = QVBoxLayout(self._gmm_group)

        crit_row = QHBoxLayout()
        crit_row.addWidget(QLabel("Criterion:"))
        self._criterion_combo = QComboBox()
        self._criterion_combo.addItems(["BIC", "Silhouette"])
        crit_row.addWidget(self._criterion_combo)
        gmm_layout.addLayout(crit_row)

        max_row = QHBoxLayout()
        max_row.addWidget(QLabel("Max components:"))
        self._max_components = QSpinBox()
        self._max_components.setRange(2, 20)
        self._max_components.setValue(10)
        max_row.addWidget(self._max_components)
        gmm_layout.addLayout(max_row)

        layout.addWidget(self._gmm_group)

        # ── K-means Options ──
        self._kmeans_group = QGroupBox("K-means Options")
        km_layout = QVBoxLayout(self._kmeans_group)

        k_row = QHBoxLayout()
        k_row.addWidget(QLabel("Number of groups:"))
        self._n_clusters = QSpinBox()
        self._n_clusters.setRange(2, 20)
        self._n_clusters.setValue(3)
        k_row.addWidget(self._n_clusters)
        km_layout.addLayout(k_row)

        layout.addWidget(self._kmeans_group)
        self._kmeans_group.setVisible(False)

        # ── Threshold Options ──
        thresh_group = QGroupBox("Threshold Options")
        thresh_layout = QVBoxLayout(thresh_group)

        sigma_row = QHBoxLayout()
        sigma_row.addWidget(QLabel("Gaussian \u03c3:"))
        self._sigma = QDoubleSpinBox()
        self._sigma.setRange(0.0, 20.0)
        self._sigma.setValue(0.0)
        self._sigma.setSingleStep(0.5)
        sigma_row.addWidget(self._sigma)
        thresh_layout.addLayout(sigma_row)

        layout.addWidget(thresh_group)

        # ── Run button ──
        self._run_btn = QPushButton("Run Grouped Thresholding")
        self._run_btn.setStyleSheet(
            "QPushButton { background-color: #2d7d46; color: white;"
            " padding: 8px; font-weight: bold; border-radius: 4px; }"
            " QPushButton:hover { background-color: #35a355; }"
        )
        self._run_btn.clicked.connect(self._on_run)
        layout.addWidget(self._run_btn)

        # ── Status ──
        self._status = QLabel("Ready")
        self._status.setStyleSheet("color: #888888; font-style: italic;")
        layout.addWidget(self._status)

        layout.addStretch()

    # ── Slots ──

    def _on_algorithm_changed(self, text: str) -> None:
        self._gmm_group.setVisible(text == "GMM")
        self._kmeans_group.setVisible(text == "K-means")

    def update_channels(self) -> None:
        """Refresh channel dropdown from viewer image layers."""
        self._channel_combo.clear()
        if self._launcher is None:
            return
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            return
        for layer in viewer_win.viewer.layers:
            if layer.__class__.__name__ == "Image":
                self._channel_combo.addItem(layer.name)

    def _show_status(self, msg: str) -> None:
        self._status.setText(msg)
        if self._launcher is not None:
            self._launcher.statusBar().showMessage(msg)

    # ── Run workflow ──

    def _on_run(self) -> None:
        if self._launcher is None:
            return

        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            self._show_status("Open a dataset in the viewer first")
            return

        store = getattr(self._launcher, "_current_store", None)
        if store is None:
            self._show_status("No dataset loaded")
            return

        channel = self._channel_combo.currentText()
        if not channel:
            self._show_status("Select a channel")
            return

        metric = self._metric_combo.currentText()
        sigma = self._sigma.value()

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

        # Re-run detection
        mask_name = f"grouped_{channel}_{metric}"
        existing_masks = store.list_masks() if hasattr(store, "list_masks") else []
        if mask_name in existing_masks:
            reply = QMessageBox.question(
                self,
                "Grouped Thresholding",
                f"Mask '{mask_name}' already exists.\n\nOverwrite it?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.No:
                # Auto-increment name
                i = 2
                while f"{mask_name}_v{i}" in existing_masks:
                    i += 1
                mask_name = f"{mask_name}_v{i}"

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
        from percell4.measure.measurer import measure_cells

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
        self._worker.error.connect(lambda msg: self._show_status(f"Measure error: {msg}"))
        self._worker.start()

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

        algo = self._algo_combo.currentText()
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
            from percell4.measure.grouper import group_cells_gmm
            criterion = self._criterion_combo.currentText().lower()
            max_comp = self._max_components.value()
            self._worker = Worker(
                group_cells_gmm, values, cell_labels,
                criterion=criterion, max_components=max_comp,
            )
        else:
            from percell4.measure.grouper import group_cells_kmeans
            n_clusters = self._n_clusters.value()
            self._worker = Worker(
                group_cells_kmeans, values, cell_labels, n_clusters=n_clusters,
            )

        self._worker.finished.connect(self._on_grouping_done)
        self._worker.error.connect(lambda msg: self._show_status(f"Grouping error: {msg}"))
        self._worker.start()

    def _on_grouping_done(self, result) -> None:
        ctx = self._grouping_context
        self._show_status(
            f"Found {result.n_groups} groups "
            f"(means: {', '.join(f'{m:.1f}' for m in result.group_means)})"
        )

        # Launch the QC controller
        from percell4.gui.threshold_qc import ThresholdQCController

        viewer_win = self._launcher._windows.get("viewer")
        store = getattr(self._launcher, "_current_store", None)

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
