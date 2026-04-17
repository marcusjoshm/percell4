"""Segmentation panel — all segmentation methods in one panel.

Three distinct sections: Cellpose (SAM), Load ROIs, and Manual Drawing,
plus Label Cleanup. Embedded as a sidebar tab in the launcher.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.model import CellDataModel


class SegmentationPanel(QWidget):
    """Panel for cell segmentation with multiple methods.

    Designed to be embedded in the launcher's sidebar content area.
    Communicates with the viewer and store via the launcher reference.
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

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignTop)

        from percell4.gui import theme

        title = QLabel("Segmentation")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {theme.TEXT_BRIGHT};"
            f" margin-bottom: 12px; padding-bottom: 4px;"
            f" border-bottom: 1px solid {theme.BORDER};"
        )
        layout.addWidget(title)

        # Channel display
        chan_row = QHBoxLayout()
        chan_row.addWidget(QLabel("Active Channel:"))
        self._channel_label = QLabel("None selected")
        self._channel_label.setStyleSheet(f"color: {theme.ACCENT}; font-weight: bold;")
        chan_row.addWidget(self._channel_label)
        chan_row.addStretch()
        layout.addLayout(chan_row)

        # ── Cellpose section ──────────────────────────────────
        cp_group = QGroupBox("Cellpose")
        cp_layout = QVBoxLayout(cp_group)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self._cp_model = QComboBox()
        self._cp_model.addItems(["cpsam", "cyto3", "cyto2", "cyto", "nuclei"])
        model_row.addWidget(self._cp_model)
        cp_layout.addLayout(model_row)

        diam_row = QHBoxLayout()
        diam_row.addWidget(QLabel("Diameter:"))
        self._cp_diameter = QSpinBox()
        self._cp_diameter.setRange(0, 500)
        self._cp_diameter.setValue(30)
        self._cp_diameter.setSpecialValueText("Auto")
        diam_row.addWidget(self._cp_diameter)
        cp_layout.addLayout(diam_row)

        self._cp_gpu = QCheckBox("Use GPU")
        self._cp_gpu.setStyleSheet(f"QCheckBox {{ color: {theme.TEXT}; }}")
        cp_layout.addWidget(self._cp_gpu)

        btn_run_cp = QPushButton("Run Cellpose")
        btn_run_cp.clicked.connect(self._on_run_cellpose)
        cp_layout.addWidget(btn_run_cp)

        layout.addWidget(cp_group)

        # ── Manual Editing section ─────────────────────────────
        draw_group = QGroupBox("Manual Editing")
        draw_layout = QVBoxLayout(draw_group)

        draw_layout.addWidget(QLabel(
            "Create, add, or remove labels using napari's\n"
            "built-in paint/fill/erase tools."
        ))

        btn_new_labels = QPushButton("Create Empty Labels Layer")
        btn_new_labels.clicked.connect(self._on_create_empty_labels)
        draw_layout.addWidget(btn_new_labels)

        btn_delete_label = QPushButton("Delete Selected Label")
        btn_delete_label.setToolTip(
            "Click a cell in the viewer, then click this button\n"
            "to remove that label from the active labels layer."
        )
        btn_delete_label.clicked.connect(self._on_delete_selected_label)
        draw_layout.addWidget(btn_delete_label)

        btn_add_label = QPushButton("Add New Label (next ID)")
        btn_add_label.setToolTip(
            "Sets the active paint label to the next available ID\n"
            "so you can draw a new cell with napari's polygon tool."
        )
        btn_add_label.clicked.connect(self._on_add_new_label)
        draw_layout.addWidget(btn_add_label)

        btn_relabel = QPushButton("Clean Up Labels (relabel sequential)")
        btn_relabel.setToolTip(
            "Renumber all labels to be sequential [1, 2, 3, ...]\n"
            "after adding or deleting cells."
        )
        btn_relabel.clicked.connect(self._on_relabel_sequential)
        draw_layout.addWidget(btn_relabel)

        layout.addWidget(draw_group)

        # ── Label Cleanup section ─────────────────────────────
        cleanup_group = QGroupBox("Label Cleanup")
        cleanup_layout = QVBoxLayout(cleanup_group)

        cleanup_layout.addWidget(QLabel(
            "Remove partial cells at image edges and\n"
            "cells below a minimum area threshold."
        ))

        margin_row = QHBoxLayout()
        margin_row.addWidget(QLabel("Edge margin (px):"))
        self._cleanup_margin = QSpinBox()
        self._cleanup_margin.setRange(0, 200)
        self._cleanup_margin.setValue(0)
        self._cleanup_margin.setToolTip(
            "0 = remove cells touching the image border.\n"
            ">0 = also remove cells within this many pixels of the border."
        )
        margin_row.addWidget(self._cleanup_margin)
        cleanup_layout.addLayout(margin_row)

        min_area_row = QHBoxLayout()
        min_area_row.addWidget(QLabel("Min cell area (px):"))
        self._cleanup_min_area = QSpinBox()
        self._cleanup_min_area.setRange(0, 10000)
        self._cleanup_min_area.setValue(0)
        self._cleanup_min_area.setToolTip(
            "Remove cells with fewer pixels than this threshold.\n"
            "0 = no area filtering."
        )
        min_area_row.addWidget(self._cleanup_min_area)
        cleanup_layout.addLayout(min_area_row)

        btn_preview = QPushButton("Preview Removal")
        btn_preview.setToolTip(
            "Highlight cells that would be removed in red.\n"
            "Does not modify the labels layer."
        )
        btn_preview.clicked.connect(self._on_cleanup_preview)
        cleanup_layout.addWidget(btn_preview)

        self._btn_apply_cleanup = QPushButton("Apply Removal")
        self._btn_apply_cleanup.setEnabled(False)
        self._btn_apply_cleanup.clicked.connect(self._on_cleanup_apply)
        cleanup_layout.addWidget(self._btn_apply_cleanup)

        self._cleanup_status = QLabel("")
        self._cleanup_status.setWordWrap(True)
        cleanup_layout.addWidget(self._cleanup_status)

        layout.addWidget(cleanup_group)

        # ── Save ──────────────────────────────────────────────
        save_group = QGroupBox("Save")
        save_layout = QVBoxLayout(save_group)

        btn_save = QPushButton("Save Labels to HDF5")
        btn_save.clicked.connect(self._on_save_labels)
        save_layout.addWidget(btn_save)

        layout.addWidget(save_group)

        layout.addStretch()

    # ── Status helper ─────────────────────────────────────────

    def _show_status(self, msg: str) -> None:
        """Show a status message in the launcher's status bar."""
        if self._launcher is not None:
            self._launcher.statusBar().showMessage(msg)

    # ── Channel tracking ──────────────────────────────────────

    def update_channel_label(self) -> None:
        """Update the active channel label from the viewer."""
        if self._launcher is None:
            return
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            self._channel_label.setText("None selected")
            return
        active = viewer_win.viewer.layers.selection.active
        if active is not None and active.__class__.__name__ == "Image":
            self._channel_label.setText(active.name)
        else:
            for layer in viewer_win.viewer.layers:
                if layer.__class__.__name__ == "Image":
                    self._channel_label.setText(layer.name)
                    return
            self._channel_label.setText("No image loaded")

    # ── Cellpose ──────────────────────────────────────────────

    def _on_run_cellpose(self) -> None:
        if self._launcher is None:
            return
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            self._show_status("Open a dataset in the viewer first")
            return

        active_layer = viewer_win.viewer.layers.selection.active
        if active_layer is None or active_layer.__class__.__name__ != "Image":
            image_layers = [
                layer for layer in viewer_win.viewer.layers
                if layer.__class__.__name__ == "Image"
            ]
            if not image_layers:
                self._show_status("No image loaded in viewer")
                return
            active_layer = image_layers[0]

        image = active_layer.data
        model_type = self._cp_model.currentText()
        diameter = self._cp_diameter.value() if self._cp_diameter.value() > 0 else None
        gpu = self._cp_gpu.isChecked()

        self._show_status(f"Running Cellpose ({model_type})...")

        from percell4.gui.workers import Worker
        from percell4.segment.cellpose import run_cellpose

        self._worker = Worker(
            run_cellpose, image, model_type=model_type, diameter=diameter, gpu=gpu,
        )
        self._worker.finished.connect(self._on_cellpose_done)
        self._worker.error.connect(lambda msg: self._show_status(f"Error: {msg}"))
        self._worker.start()

    def _on_cellpose_done(self, masks) -> None:
        from percell4.application.use_cases.segment_cells import SegmentCells
        from percell4.adapters.hdf5_store import Hdf5DatasetRepository

        # Delegate post-processing + store write to the use case
        try:
            repo = Hdf5DatasetRepository()
            uc = SegmentCells(repo, self.data_model.session)
            result = uc.finalize(masks)
        except ValueError as e:
            self._show_status(f"Error: {e}")
            return

        self._show_status(
            f"Done: {result.n_cells} cells "
            f"({result.edge_removed} edge, {result.small_removed} small removed)"
        )

        # Add labels layer to viewer
        viewer_win = self._launcher._windows.get("viewer") if self._launcher else None
        if viewer_win is not None:
            viewer_win.add_labels(result.labels, name=result.seg_name)

    # ── Load ROIs ─────────────────────────────────────────────

    def _get_image_shape(self):
        if self._launcher is None:
            return None
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            return None
        for layer in viewer_win.viewer.layers:
            if layer.__class__.__name__ == "Image":
                return layer.data.shape[-2:]
        return None

    # ── Manual Editing ────────────────────────────────────────

    def _on_create_empty_labels(self) -> None:
        viewer_win = self._launcher._windows.get("viewer") if self._launcher else None
        if viewer_win is None or viewer_win.viewer is None:
            self._show_status("Open a dataset in the viewer first")
            return
        shape = self._get_image_shape()
        if shape is None:
            self._show_status("Load an image first")
            return
        labels = np.zeros(shape, dtype=np.int32)
        viewer_win.add_labels(labels, name="manual")
        self.data_model.set_active_segmentation("manual")
        self._show_status("Empty labels layer created — use napari tools to draw cells")

    def _get_active_labels_layer(self):
        if self._launcher is None:
            return None
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            return None
        import napari
        active_name = self.data_model.active_segmentation
        if active_name:
            for layer in viewer_win.viewer.layers:
                if isinstance(layer, napari.layers.Labels) and layer.name == active_name:
                    return layer
        active = viewer_win.viewer.layers.selection.active
        if active is not None and isinstance(active, napari.layers.Labels):
            return active
        for layer in viewer_win.viewer.layers:
            if isinstance(layer, napari.layers.Labels):
                return layer
        return None

    def _select_labels_layer_in_viewer(self, labels_layer) -> None:
        if self._launcher is None:
            return
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            return
        viewer_win.viewer.layers.selection.active = labels_layer

    def _on_delete_selected_label(self) -> None:
        labels_layer = self._get_active_labels_layer()
        if labels_layer is None:
            self._show_status("No labels layer active")
            return
        selected_id = labels_layer.selected_label
        if selected_id == 0:
            self._show_status("No label selected (click a cell first)")
            return
        data = labels_layer.data.copy()
        count = int(np.sum(data == selected_id))
        data[data == selected_id] = 0
        labels_layer.data = data
        labels_layer.selected_label = 0
        labels_layer.refresh()
        self._show_status(f"Deleted label {selected_id} ({count} pixels removed)")

    def _on_add_new_label(self) -> None:
        from qtpy.QtCore import QTimer
        from qtpy.QtWidgets import QApplication

        labels_layer = self._get_active_labels_layer()
        if labels_layer is None:
            self._show_status("No labels layer active — load or create one first")
            return

        # Step 1: Select the layer in napari FIRST
        self._select_labels_layer_in_viewer(labels_layer)

        # Step 2: Show and raise the viewer
        viewer_win = self._launcher._windows.get("viewer") if self._launcher else None
        if viewer_win is not None:
            viewer_win.show()

        # Step 3: Let Qt process the layer selection before setting mode
        QApplication.processEvents()

        # Step 4: Set the label ID
        next_id = int(labels_layer.data.max()) + 1
        labels_layer.selected_label = next_id

        # Step 5: Defer polygon mode activation to ensure napari is ready
        def _activate_polygon():
            try:
                labels_layer.mode = "polygon"
            except Exception:
                pass

        QTimer.singleShot(100, _activate_polygon)
        self._show_status(f"Label {next_id} — draw cell boundary with polygon tool")

    def _on_relabel_sequential(self) -> None:
        labels_layer = self._get_active_labels_layer()
        if labels_layer is None:
            self._show_status("No labels layer active")
            return
        from percell4.segment.postprocess import relabel_sequential
        old_data = labels_layer.data
        new_data = relabel_sequential(np.asarray(old_data, dtype=np.int32))
        n_cells = int(new_data.max())
        labels_layer.data = new_data
        labels_layer.refresh()
        self._show_status(f"Relabeled to {n_cells} sequential cells")

    # ── Label Cleanup ─────────────────────────────────────────

    def _run_cleanup_filters(
        self, labels: np.ndarray,
    ) -> tuple[np.ndarray, int, int, int]:
        """Apply edge and area filters. Returns (filtered, edge_removed, small_removed, total)."""
        from percell4.segment.postprocess import filter_edge_cells, filter_small_cells

        margin = self._cleanup_margin.value()
        min_area = self._cleanup_min_area.value()

        filtered = labels
        edge_removed = 0
        small_removed = 0
        if margin >= 0:
            filtered, edge_removed = filter_edge_cells(filtered, edge_margin=margin)
        if min_area > 0:
            filtered, small_removed = filter_small_cells(filtered, min_area=min_area)

        return filtered, edge_removed, small_removed, edge_removed + small_removed

    def _on_cleanup_preview(self) -> None:
        labels_layer = self._get_active_labels_layer()
        if labels_layer is None:
            self._cleanup_status.setText("No labels layer active.")
            self._cleanup_status.setStyleSheet(f"color: {theme.ERROR};")
            return

        labels = np.asarray(labels_layer.data, dtype=np.int32)
        filtered, edge_removed, small_removed, total_removed = (
            self._run_cleanup_filters(labels)
        )

        viewer_win = self._launcher._windows.get("viewer") if self._launcher else None
        if viewer_win is None or viewer_win.viewer is None:
            return

        for layer in list(viewer_win.viewer.layers):
            if layer.name == "_cleanup_preview":
                viewer_win.viewer.layers.remove(layer)
                break

        if total_removed == 0:
            self._cleanup_status.setText("No cells to remove at these settings.")
            self._cleanup_status.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            self._btn_apply_cleanup.setEnabled(False)
            return

        removed_mask = (labels > 0) & (filtered == 0)
        highlight = np.where(removed_mask, 1, 0).astype(np.int32)
        viewer_win.viewer.add_labels(
            highlight, name="_cleanup_preview", opacity=0.5, blending="translucent",
        )

        parts = []
        if edge_removed:
            parts.append(f"{edge_removed} edge")
        if small_removed:
            parts.append(f"{small_removed} small")
        self._cleanup_status.setText(
            f"{total_removed} cells to remove ({', '.join(parts)})."
        )
        self._cleanup_status.setStyleSheet(f"color: {theme.WARNING};")
        self._btn_apply_cleanup.setEnabled(True)

    def _on_cleanup_apply(self) -> None:
        labels_layer = self._get_active_labels_layer()
        if labels_layer is None:
            self._cleanup_status.setText("No labels layer active.")
            self._cleanup_status.setStyleSheet(f"color: {theme.ERROR};")
            return

        from percell4.segment.postprocess import relabel_sequential

        labels = np.asarray(labels_layer.data, dtype=np.int32)
        filtered, edge_removed, small_removed, total_removed = (
            self._run_cleanup_filters(labels)
        )

        filtered = relabel_sequential(filtered)
        n_remaining = int(filtered.max())

        labels_layer.data = filtered
        labels_layer.refresh()

        viewer_win = self._launcher._windows.get("viewer") if self._launcher else None
        if viewer_win is not None and viewer_win.viewer is not None:
            for layer in list(viewer_win.viewer.layers):
                if layer.name == "_cleanup_preview":
                    viewer_win.viewer.layers.remove(layer)
                    break

        self._btn_apply_cleanup.setEnabled(False)
        parts = []
        if edge_removed:
            parts.append(f"{edge_removed} edge")
        if small_removed:
            parts.append(f"{small_removed} small")
        self._cleanup_status.setText(
            f"Removed {total_removed} cells ({', '.join(parts)}). "
            f"{n_remaining} cells remaining."
        )
        self._cleanup_status.setStyleSheet(f"color: {theme.SUCCESS};")
        self._show_status(f"Cleanup: removed {total_removed}, {n_remaining} remaining")

    # ── Save ──────────────────────────────────────────────────

    def _on_save_labels(self) -> None:
        store = getattr(self._launcher, "_current_store", None) if self._launcher else None
        if store is None:
            self._show_status("No dataset loaded")
            return
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            self._show_status("Viewer not open")
            return

        import napari
        active = viewer_win.viewer.layers.selection.active
        if active is not None and isinstance(active, napari.layers.Labels):
            name = active.name
            data = active.data
        else:
            labels_layer = self._get_active_labels_layer()
            if labels_layer is None:
                self._show_status("No labels layer to save")
                return
            name = labels_layer.name
            data = labels_layer.data

        count = store.write_labels(name, np.asarray(data, dtype=np.int32))
        self._show_status(f"Saved labels '{name}' ({count} pixels)")
