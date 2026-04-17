"""Analysis task panel — filter, threshold, measurement, particles.

Receives dependencies via callbacks — no launcher reference for its
own operations. GroupedSegPanel still receives a launcher reference
as a transitional coupling (separate file, separate concern).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
from qtpy.QtCore import QSettings, Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.gui import theme
from percell4.model import CellDataModel


class AnalysisPanel(QWidget):
    """Panel for cell filtering, thresholding, measurement, and particle analysis."""

    def __init__(
        self,
        data_model: CellDataModel,
        *,
        get_repo: Callable[[], Any],
        get_viewer_window: Callable[[], Any | None],
        get_phasor_roi_names: Callable[[], dict[int, str] | None],
        show_window: Callable[[str], None],
        show_status: Callable[[str], None] = lambda _: None,
        launcher=None,  # transitional: only for GroupedSegPanel
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.data_model = data_model
        self._get_repo = get_repo
        self._get_viewer_window = get_viewer_window
        self._get_phasor_roi_names_cb = get_phasor_roi_names
        self._show_window_cb = show_window
        self._show_status = show_status
        self._launcher_for_grouped = launcher  # only for GroupedSegPanel

        # Threshold preview state
        self._thresh_working_image = None
        self._thresh_channel_name = None

        # Particle export state
        self._last_particle_df = None
        self._last_particle_detail_df = None

        self._build_ui()

        # Subscribe to filter changes
        self.data_model.state_changed.connect(self._on_state_changed)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("Analysis")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {theme.TEXT_BRIGHT};"
            f" margin-bottom: 12px; padding-bottom: 4px;"
            f" border-bottom: 1px solid {theme.BORDER};"
        )
        layout.addWidget(title)

        # ── Cell Filter group ──
        filter_group = QGroupBox("Cell Filter")
        filter_layout = QVBoxLayout(filter_group)

        sel_btn_row = QHBoxLayout()
        btn_clear_sel = QPushButton("Clear Selection")
        btn_clear_sel.setToolTip("Deselect all cells and restore viewer to normal")
        btn_clear_sel.clicked.connect(self._on_clear_selection)
        sel_btn_row.addWidget(btn_clear_sel)
        filter_layout.addLayout(sel_btn_row)

        filter_btn_row = QHBoxLayout()
        btn_filter = QPushButton("Filter to Selection")
        btn_filter.setToolTip("Show only the currently selected cells in all windows")
        btn_filter.clicked.connect(self._on_filter_to_selection)
        filter_btn_row.addWidget(btn_filter)

        self._clear_filter_btn = QPushButton("Clear Filter")
        self._clear_filter_btn.setEnabled(False)
        self._clear_filter_btn.clicked.connect(self._on_clear_filter)
        filter_btn_row.addWidget(self._clear_filter_btn)
        filter_layout.addLayout(filter_btn_row)

        self._filter_status_label = QLabel("No filter active")
        self._filter_status_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        filter_layout.addWidget(self._filter_status_label)

        layout.addWidget(filter_group)

        # ── Whole Field Thresholding group ──
        thresh_group = QGroupBox("Whole Field Thresholding")
        thresh_layout = QVBoxLayout(thresh_group)

        thresh_chan_row = QHBoxLayout()
        thresh_chan_row.addWidget(QLabel("Channel:"))
        self._thresh_channel_label = QLabel("None selected")
        self._thresh_channel_label.setStyleSheet(
            f"color: {theme.ACCENT}; font-weight: bold;"
        )
        thresh_chan_row.addWidget(self._thresh_channel_label)
        thresh_chan_row.addStretch()
        thresh_layout.addLayout(thresh_chan_row)

        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Method:"))
        self._thresh_method = QComboBox()
        self._thresh_method.addItems(["Otsu", "Triangle", "Li", "Adaptive", "Manual"])
        method_row.addWidget(self._thresh_method)
        thresh_layout.addLayout(method_row)

        val_row = QHBoxLayout()
        val_row.addWidget(QLabel("Threshold:"))
        self._thresh_value_spin = QDoubleSpinBox()
        self._thresh_value_spin.setRange(0.0, 100000.0)
        self._thresh_value_spin.setValue(0.0)
        self._thresh_value_spin.setDecimals(1)
        self._thresh_value_spin.setToolTip(
            "Auto-computed threshold value. Edit to override manually."
        )
        val_row.addWidget(self._thresh_value_spin)
        thresh_layout.addLayout(val_row)

        sigma_row = QHBoxLayout()
        sigma_row.addWidget(QLabel("Gaussian σ:"))
        self._thresh_sigma = QDoubleSpinBox()
        self._thresh_sigma.setRange(0.0, 20.0)
        self._thresh_sigma.setValue(0.0)
        self._thresh_sigma.setSpecialValueText("None")
        self._thresh_sigma.setSingleStep(0.5)
        sigma_row.addWidget(self._thresh_sigma)
        thresh_layout.addLayout(sigma_row)

        thresh_layout.addWidget(QLabel(
            "1. Preview computes threshold and shows mask.\n"
            "2. Draw ROI in viewer to recalculate from a region.\n"
            "3. Accept to save the mask."
        ))

        btn_preview = QPushButton("Preview Threshold")
        btn_preview.setToolTip(
            "Compute threshold and show preview mask in viewer.\n"
            "Draw/move an ROI to recalculate from that region."
        )
        btn_preview.clicked.connect(self._on_threshold_preview)
        thresh_layout.addWidget(btn_preview)

        btn_accept = QPushButton("Accept && Save Mask to HDF5")
        btn_accept.clicked.connect(self._on_threshold_accept)
        thresh_layout.addWidget(btn_accept)

        self._thresh_result_label = QLabel("")
        self._thresh_result_label.setWordWrap(True)
        thresh_layout.addWidget(self._thresh_result_label)

        layout.addWidget(thresh_group)

        # ── Grouped Thresholding ──
        from percell4.gui.grouped_seg_panel import GroupedSegPanel

        self._grouped_seg_panel = GroupedSegPanel(
            self.data_model, launcher=self._launcher_for_grouped
        )
        grouped_group = QGroupBox("Grouped Thresholding")
        grouped_layout = QVBoxLayout(grouped_group)
        grouped_layout.addWidget(self._grouped_seg_panel)
        layout.addWidget(grouped_group)

        # ── Measurements group ──
        meas_group = QGroupBox("Measurements")
        meas_layout = QVBoxLayout(meas_group)

        meas_layout.addWidget(QLabel(
            "Measures per-cell metrics using the active\n"
            "channel, segmentation, and mask from Data tab."
        ))

        self._meas_result_label = QLabel("")
        self._meas_result_label.setWordWrap(True)
        meas_layout.addWidget(self._meas_result_label)

        btn_measure = QPushButton("Measure Cells")
        btn_measure.clicked.connect(self._on_measure_cells)
        meas_layout.addWidget(btn_measure)

        btn_row = QHBoxLayout()
        btn_plot = QPushButton("Open Data Plot")
        btn_plot.clicked.connect(lambda: self._show_window("data_plot"))
        btn_row.addWidget(btn_plot)

        btn_table = QPushButton("Open Cell Table")
        btn_table.clicked.connect(lambda: self._show_window("cell_table"))
        btn_row.addWidget(btn_table)
        meas_layout.addLayout(btn_row)

        layout.addWidget(meas_group)

        # ── Particle Analysis ──
        particle_group = QGroupBox("Particle Analysis")
        particle_layout = QVBoxLayout(particle_group)

        particle_layout.addWidget(QLabel(
            "Counts particles within each cell using\n"
            "the active mask as the particle source."
        ))

        min_area_row = QHBoxLayout()
        min_area_row.addWidget(QLabel("Min particle area (px):"))
        self._particle_min_area = QSpinBox()
        self._particle_min_area.setRange(1, 10000)
        self._particle_min_area.setValue(1)
        min_area_row.addWidget(self._particle_min_area)
        particle_layout.addLayout(min_area_row)

        btn_particle = QPushButton("Analyze Particles")
        btn_particle.clicked.connect(self._on_analyze_particles)
        particle_layout.addWidget(btn_particle)

        btn_export_particle = QPushButton("Export Particle Data to CSV...")
        btn_export_particle.clicked.connect(self._on_export_particle_csv)
        particle_layout.addWidget(btn_export_particle)

        self._particle_result_label = QLabel("")
        self._particle_result_label.setWordWrap(True)
        particle_layout.addWidget(self._particle_result_label)

        layout.addWidget(particle_group)

        layout.addStretch()

    # ── Helpers ───────────────────────────────────────────────

    def _show_window(self, name: str) -> None:
        self._show_window_cb(name)

    def _update_channel_display(self) -> None:
        """Sync the threshold channel label from the Session's active channel."""
        ch = self.data_model.session.active_channel
        self._thresh_channel_label.setText(ch or "None selected")
        from percell4.gui import theme
        if ch:
            self._thresh_channel_label.setStyleSheet(
                f"color: {theme.ACCENT}; font-weight: bold;"
            )
        else:
            self._thresh_channel_label.setStyleSheet(
                f"color: {theme.TEXT_MUTED};"
            )

    # ── State change routing ─────────────────────────────────

    def _on_state_changed(self, change) -> None:
        if change.filter:
            self._on_filter_state_changed()
        if change.data or change.channel:
            self._update_channel_display()

    # ── Cell Filter ──────────────────────────────────────────

    def _on_clear_selection(self) -> None:
        self.data_model.set_selection([])

    def _on_filter_to_selection(self) -> None:
        selected = self.data_model.selected_ids
        if not selected:
            self._show_status("No cells selected to filter")
            return
        self.data_model.set_filter(list(selected))

    def _on_clear_filter(self) -> None:
        self.data_model.set_filter(None)

    def _on_filter_state_changed(self) -> None:
        if self.data_model.is_filtered:
            n_filtered = len(self.data_model.filtered_df)
            n_total = len(self.data_model.df)
            self._filter_status_label.setText(
                f"Showing {n_filtered} of {n_total} cells"
            )
            self._filter_status_label.setStyleSheet(
                f"color: {theme.ACCENT}; font-weight: bold;"
            )
            self._clear_filter_btn.setEnabled(True)
        else:
            self._filter_status_label.setText("No filter active")
            self._filter_status_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            self._clear_filter_btn.setEnabled(False)

    # ── Whole Field Thresholding ─────────────────────────────

    def _on_threshold_preview(self) -> None:
        # Read channel from Session (not viewer)
        channel_name = self.data_model.session.active_channel
        if not channel_name:
            self._show_status("Select a channel in the Data tab first")
            return

        # Get the image data from the viewer layer (still need viewer for the array)
        viewer_win = self._get_viewer_window()
        if viewer_win is None or viewer_win.viewer is None:
            self._show_status("Open the viewer first")
            return

        # Find the image layer matching the active channel
        active = None
        for layer in viewer_win.viewer.layers:
            if layer.__class__.__name__ == "Image" and layer.name == channel_name:
                active = layer
                break
        if active is None:
            self._show_status(f"Channel '{channel_name}' not found in viewer")
            return

        from percell4.domain.measure.thresholding import (
            THRESHOLD_METHODS,
            apply_gaussian_smoothing,
        )

        image = active.data.astype(np.float32)

        sigma = self._thresh_sigma.value()
        if sigma > 0:
            image = apply_gaussian_smoothing(image, sigma)

        self._thresh_working_image = image
        self._thresh_channel_name = channel_name

        method = self._thresh_method.currentText().lower()
        if method == "manual":
            value = self._thresh_value_spin.value()
            if value <= 0:
                self._show_status("Set a threshold value > 0")
                return
        elif method in THRESHOLD_METHODS:
            _, value = THRESHOLD_METHODS[method](image)
        else:
            self._show_status(f"Unknown method: {method}")
            return

        self._thresh_value_spin.setValue(value)
        mask = (image > value).astype(np.uint8)

        for name in ("_threshold_preview", "_threshold_roi"):
            for layer in list(viewer_win.viewer.layers):
                if layer.name == name:
                    viewer_win.viewer.layers.remove(layer)

        from napari.utils.colormaps import DirectLabelColormap

        yellow_cmap = DirectLabelColormap(
            color_dict={0: "transparent", 1: "yellow", None: "transparent"},
        )
        viewer_win.viewer.add_labels(
            mask,
            name="_threshold_preview",
            opacity=0.5,
            blending="translucent",
            colormap=yellow_cmap,
        )

        viewer_win.viewer.add_shapes(
            [],
            shape_type="rectangle",
            name="_threshold_roi",
            edge_color="yellow",
            edge_width=2,
            face_color=[1, 1, 0, 0.1],
        )

        for layer in viewer_win.viewer.layers:
            if layer.name == "_threshold_roi":
                layer.events.data.connect(self._on_threshold_roi_changed)
                break

        self._update_thresh_stats(mask, value)
        viewer_win.show()
        self._show_status(
            f"Preview: {method} threshold = {value:.1f}. "
            "Draw a rectangle ROI to recalculate from a region."
        )

    def _on_threshold_roi_changed(self, event=None) -> None:
        viewer_win = self._get_viewer_window()
        if viewer_win is None or viewer_win.viewer is None:
            return

        image = self._thresh_working_image
        if image is None:
            return

        roi_image = None
        for layer in viewer_win.viewer.layers:
            if layer.name == "_threshold_roi" and hasattr(layer, "data"):
                if len(layer.data) > 0:
                    coords = np.array(layer.data[0])
                    y_min = max(0, int(coords[:, 0].min()))
                    y_max = min(image.shape[0], int(coords[:, 0].max()))
                    x_min = max(0, int(coords[:, 1].min()))
                    x_max = min(image.shape[1], int(coords[:, 1].max()))
                    if y_max > y_min and x_max > x_min:
                        roi_image = image[y_min:y_max, x_min:x_max]
                break

        if roi_image is None or roi_image.size == 0:
            return

        from percell4.domain.measure.thresholding import THRESHOLD_METHODS

        method = self._thresh_method.currentText().lower()
        if method == "manual":
            return
        if method not in THRESHOLD_METHODS:
            return

        _, value = THRESHOLD_METHODS[method](roi_image)
        self._thresh_value_spin.setValue(value)

        mask = (image > value).astype(np.uint8)

        for layer in viewer_win.viewer.layers:
            if layer.name == "_threshold_preview":
                layer.data = mask
                layer.refresh()
                break

        self._update_thresh_stats(mask, value, from_roi=True)

    def _update_thresh_stats(
        self, mask, value: float, from_roi: bool = False
    ) -> None:
        n_pos = int(mask.sum())
        n_total = mask.size
        pct = 100.0 * n_pos / n_total if n_total > 0 else 0
        roi_note = " (from ROI)" if from_roi else ""
        self._thresh_result_label.setText(
            f"Threshold: {value:.1f}{roi_note}\n"
            f"Positive: {n_pos:,} / {n_total:,} px ({pct:.1f}%)"
        )
        self._thresh_result_label.setStyleSheet(f"color: {theme.WARNING};")

    def _on_threshold_accept(self) -> None:
        viewer_win = self._get_viewer_window()
        if viewer_win is None or viewer_win.viewer is None:
            self._show_status("No preview to accept")
            return

        image = self._thresh_working_image
        channel_name = self._thresh_channel_name or "unknown"
        if image is None:
            self._show_status("Run Preview first")
            return

        value = self._thresh_value_spin.value()
        method = self._thresh_method.currentText().lower()

        for name in ("_threshold_preview", "_threshold_roi"):
            for layer in list(viewer_win.viewer.layers):
                if layer.name == name:
                    viewer_win.viewer.layers.remove(layer)

        try:
            from percell4.application.use_cases.accept_threshold import AcceptThreshold
            from percell4.adapters.napari_viewer import NapariViewerAdapter

            repo = self._get_repo()
            viewer_adapter = NapariViewerAdapter(viewer_win)
            uc = AcceptThreshold(repo, viewer_adapter, self.data_model.session)
            result = uc.execute(image, value, method, channel_name)
        except ValueError as e:
            self._show_status(str(e))
            return

        viewer_win.add_mask(
            (image > value).astype("uint8"), name=result.mask_name
        )

        pct = 100.0 * result.n_positive / result.n_total if result.n_total > 0 else 0
        self._thresh_result_label.setText(
            f"Saved: {result.mask_name}\n"
            f"Threshold: {value:.1f} | {result.n_positive:,} / {result.n_total:,} px ({pct:.1f}%)"
        )
        self._thresh_result_label.setStyleSheet(f"color: {theme.SUCCESS};")
        self._show_status(f"Saved mask '{result.mask_name}' (threshold {value:.1f})")

        self._thresh_working_image = None
        self._thresh_channel_name = None

    # ── Measurements ─────────────────────────────────────────

    def _on_measure_cells(self) -> None:
        selected_metrics = self._show_metric_config_dialog()
        if selected_metrics is None:
            return

        if self.data_model.session.dataset is None:
            self._show_status("No dataset loaded")
            return

        self._show_status("Measuring cells...")

        try:
            from percell4.application.use_cases.measure_cells import MeasureCells

            repo = self._get_repo()
            roi_names = self._get_phasor_roi_names() or None
            uc = MeasureCells(repo, self.data_model.session)
            df = uc.execute(metrics=selected_metrics, roi_names=roi_names)
        except ValueError as e:
            self._show_status(str(e))
            return
        except Exception as e:
            self._show_status(f"Measurement error: {e}")
            return

        n_cells = len(df)
        n_cols = len(df.columns)
        seg_name = self.data_model.active_segmentation
        mask_name = self.data_model.active_mask
        mask_note = f" (mask: {mask_name})" if mask_name else ""
        self._meas_result_label.setText(
            f"Measured {n_cells} cells across multiple channel(s)\n"
            f"{n_cols} columns | seg: {seg_name}{mask_note}"
        )
        self._meas_result_label.setStyleSheet(f"color: {theme.SUCCESS};")
        self._show_status(f"Measured {n_cells} cells, {n_cols} columns")

    def _show_metric_config_dialog(self) -> list[str] | None:
        from percell4.domain.measure.metrics import BUILTIN_METRICS

        dialog = QDialog(self)
        dialog.setWindowTitle("Select Metrics")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Choose which metrics to compute:"))

        selected = self._load_selected_metrics()
        checkboxes: dict[str, QCheckBox] = {}
        for name in BUILTIN_METRICS:
            cb = QCheckBox(name.replace("_", " ").title())
            cb.setChecked(name in selected)
            checkboxes[name] = cb
            layout.addWidget(cb)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return None

        result = [name for name, cb in checkboxes.items() if cb.isChecked()]
        if not result:
            self._show_status("No metrics selected")
            return None
        self._save_selected_metrics(result)
        return result

    @staticmethod
    def _load_selected_metrics() -> list[str]:
        from percell4.domain.measure.metrics import BUILTIN_METRICS
        settings = QSettings("LeeLabPerCell4", "PerCell4")
        raw = settings.value("metrics/selected", defaultValue=None)
        if raw is None:
            return list(BUILTIN_METRICS.keys())
        if isinstance(raw, str):
            raw = [raw]
        return [m for m in raw if m in BUILTIN_METRICS]

    @staticmethod
    def _save_selected_metrics(metrics: list[str]) -> None:
        settings = QSettings("LeeLabPerCell4", "PerCell4")
        settings.setValue("metrics/selected", metrics)

    def _get_phasor_roi_names(self) -> dict[int, str] | None:
        return self._get_phasor_roi_names_cb()

    # ���─ Particle Analysis ────────────────────────────────────

    def _on_analyze_particles(self) -> None:
        min_area = self._particle_min_area.value()
        self._show_status("Analyzing particles...")

        try:
            from percell4.application.use_cases.analyze_particles import AnalyzeParticles

            repo = self._get_repo()
            uc = AnalyzeParticles(repo, self.data_model.session)
            result = uc.execute(min_area=min_area)
        except ValueError as e:
            self._show_status(str(e))
            return
        except Exception as e:
            self._show_status(f"Particle analysis error: {e}")
            return

        self._last_particle_df = result.summary_df
        self._last_particle_detail_df = result.detail_df

        mask_name = self.data_model.session.active_mask or "unknown"
        self._particle_result_label.setText(
            f"{result.total_particles} particles in {result.n_cells} cells\n"
            f"mask: {mask_name} | min area: {min_area} px"
        )
        self._particle_result_label.setStyleSheet(f"color: {theme.SUCCESS};")
        self._show_status(
            f"Found {result.total_particles} particles across {result.n_cells} cells"
        )

    def _on_export_particle_csv(self) -> None:
        detail_df = self._last_particle_detail_df
        if detail_df is None or detail_df.empty:
            self._show_status("No particle data — run Analyze Particles first")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Particle Data", "particles.csv", "CSV (*.csv)"
        )
        if path:
            detail_df.to_csv(path, index=False)
            self._show_status(f"Exported particle data to {path}")
