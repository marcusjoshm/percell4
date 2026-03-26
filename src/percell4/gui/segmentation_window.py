"""Segmentation window — dedicated window for all segmentation methods.

Three distinct sections: Cellpose (SAM), Load ROIs, and Manual Drawing.
Each has its own controls and actions.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from qtpy.QtCore import QSettings, Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from percell4.model import CellDataModel


class SegmentationWindow(QMainWindow):
    """Dedicated window for cell segmentation with multiple methods."""

    def __init__(
        self,
        data_model: CellDataModel,
        launcher=None,
    ) -> None:
        super().__init__()
        self.data_model = data_model
        self._launcher = launcher  # reference to access viewer, store, etc.
        self.setWindowTitle("PerCell4 — Segmentation")
        self.resize(420, 500)

        self._build_ui()
        self._apply_style()
        self._restore_geometry()

    def _build_ui(self) -> None:
        from qtpy.QtWidgets import QScrollArea

        # Scroll area wrapping all content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self.setCentralWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignTop)

        title = QLabel("Segmentation")
        title.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #ffffff;"
            " margin-bottom: 8px; padding-bottom: 4px;"
            " border-bottom: 1px solid #3a3a3a;"
        )
        layout.addWidget(title)

        # Channel display
        chan_row = QHBoxLayout()
        chan_row.addWidget(QLabel("Active Channel:"))
        self._channel_label = QLabel("None selected")
        self._channel_label.setStyleSheet("color: #4ea8de; font-weight: bold;")
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
        self._cp_gpu.setStyleSheet("QCheckBox { color: #e0e0e0; }")
        cp_layout.addWidget(self._cp_gpu)

        btn_run_cp = QPushButton("Run Cellpose")
        btn_run_cp.clicked.connect(self._on_run_cellpose)
        cp_layout.addWidget(btn_run_cp)

        layout.addWidget(cp_group)

        # ── Load ROIs section ─────────────────────────────────
        roi_group = QGroupBox("Load ROIs")
        roi_layout = QVBoxLayout(roi_group)

        btn_imagej = QPushButton("Import ImageJ ROIs (.zip)...")
        btn_imagej.clicked.connect(self._on_import_imagej)
        roi_layout.addWidget(btn_imagej)

        btn_cellpose_seg = QPushButton("Import Cellpose _seg.npy...")
        btn_cellpose_seg.clicked.connect(self._on_import_cellpose_seg)
        roi_layout.addWidget(btn_cellpose_seg)

        layout.addWidget(roi_group)

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
            "so you can draw a new cell with napari's paint tool."
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

        # Status bar
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

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
            self.statusBar().showMessage("Open a dataset in the viewer first")
            return

        # Get active image layer
        active_layer = viewer_win.viewer.layers.selection.active
        if active_layer is None or active_layer.__class__.__name__ != "Image":
            image_layers = [
                layer for layer in viewer_win.viewer.layers
                if layer.__class__.__name__ == "Image"
            ]
            if not image_layers:
                self.statusBar().showMessage("No image loaded in viewer")
                return
            active_layer = image_layers[0]

        image = active_layer.data
        model_type = self._cp_model.currentText()
        diameter = self._cp_diameter.value() if self._cp_diameter.value() > 0 else None
        gpu = self._cp_gpu.isChecked()

        self.statusBar().showMessage(f"Running Cellpose ({model_type})...")

        from percell4.gui.workers import Worker
        from percell4.segment.cellpose import run_cellpose

        self._worker = Worker(
            run_cellpose,
            image,
            model_type=model_type,
            diameter=diameter,
            gpu=gpu,
        )
        self._worker.finished.connect(self._on_cellpose_done)
        self._worker.error.connect(
            lambda msg: self.statusBar().showMessage(f"Error: {msg}")
        )
        self._worker.start()

    def _on_cellpose_done(self, masks) -> None:
        from percell4.segment.postprocess import (
            filter_edge_cells,
            filter_small_cells,
            relabel_sequential,
        )

        labels, edge_removed = filter_edge_cells(masks)
        labels, small_removed = filter_small_cells(labels, min_area=15)
        labels = relabel_sequential(labels)
        n_cells = int(labels.max())

        self.statusBar().showMessage(
            f"Done: {n_cells} cells "
            f"({edge_removed} edge, {small_removed} small removed)"
        )

        seg_name = f"cellpose_{n_cells}"

        # Write to HDF5
        store = getattr(self._launcher, "_current_store", None)
        if store is not None:
            store.write_labels(seg_name, labels)

        # Display in viewer
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is not None:
            viewer_win.add_labels(labels, name=seg_name)

        # Update active segmentation dropdown
        self.data_model.set_active_segmentation(seg_name)

    # ── Load ROIs ─────────────────────────────────────────────

    def _get_image_shape(self):
        """Get (H, W) from the first image layer in the viewer."""
        if self._launcher is None:
            return None
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            return None
        for layer in viewer_win.viewer.layers:
            if layer.__class__.__name__ == "Image":
                return layer.data.shape[-2:]
        return None

    def _on_import_imagej(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import ImageJ ROIs", "",
            "ROI Files (*.zip);;All Files (*)"
        )
        if not path:
            return

        shape = self._get_image_shape()
        if shape is None:
            self.statusBar().showMessage("Load an image first")
            return

        try:
            from percell4.segment.roi_import import import_imagej_rois

            labels = import_imagej_rois(path, shape)
            n_cells = int(labels.max())
            name = f"roi_import_{n_cells}"

            store = getattr(self._launcher, "_current_store", None)
            if store is not None:
                store.write_labels(name, labels)

            viewer_win = self._launcher._windows.get("viewer")
            if viewer_win is not None:
                viewer_win.add_labels(labels, name=name)

            if hasattr(self._launcher, "_active_seg_combo"):
                combo = self._launcher._active_seg_combo
                if combo.findText(name) == -1:
                    combo.addItem(name)
                combo.setCurrentText(name)

            self.statusBar().showMessage(
                f"Imported {n_cells} ROIs from {Path(path).name}"
            )
        except ImportError:
            QMessageBox.warning(
                self, "Missing Dependency",
                "roifile package required.\nInstall: pip install roifile"
            )
        except Exception as e:
            self.statusBar().showMessage(f"ROI import error: {e}")

    def _on_import_cellpose_seg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Cellpose Segmentation", "",
            "Numpy Files (*.npy);;All Files (*)"
        )
        if not path:
            return

        try:
            from percell4.segment.roi_import import import_cellpose_seg

            labels = import_cellpose_seg(path)
            n_cells = int(labels.max())
            name = f"cellpose_import_{n_cells}"

            store = getattr(self._launcher, "_current_store", None)
            if store is not None:
                store.write_labels(name, labels)

            viewer_win = self._launcher._windows.get("viewer")
            if viewer_win is not None:
                viewer_win.add_labels(labels, name=name)

            if hasattr(self._launcher, "_active_seg_combo"):
                combo = self._launcher._active_seg_combo
                if combo.findText(name) == -1:
                    combo.addItem(name)
                combo.setCurrentText(name)

            self.statusBar().showMessage(
                f"Imported {n_cells} cells from {Path(path).name}"
            )
        except Exception as e:
            self.statusBar().showMessage(f"Import error: {e}")

    # ── Manual Drawing ────────────────────────────────────────

    def _on_create_empty_labels(self) -> None:
        viewer_win = self._launcher._windows.get("viewer") if self._launcher else None
        if viewer_win is None or viewer_win.viewer is None:
            self.statusBar().showMessage("Open a dataset in the viewer first")
            return

        shape = self._get_image_shape()
        if shape is None:
            self.statusBar().showMessage("Load an image first")
            return

        labels = np.zeros(shape, dtype=np.int32)
        viewer_win.add_labels(labels, name="manual")
        self.statusBar().showMessage(
            "Empty labels layer created — use napari paint tools to draw cells"
        )

    def _get_active_labels_layer(self):
        """Get the active segmentation labels layer from the viewer.

        Uses the model's active_segmentation name to find the correct layer.
        Falls back to napari's selected layer or first labels layer.
        """
        if self._launcher is None:
            return None
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            return None

        import napari

        # First: try to find the layer matching model's active segmentation
        active_name = self.data_model.active_segmentation
        if active_name:
            for layer in viewer_win.viewer.layers:
                if isinstance(layer, napari.layers.Labels) and layer.name == active_name:
                    return layer

        # Fallback: napari's currently selected layer
        active = viewer_win.viewer.layers.selection.active
        if active is not None and isinstance(active, napari.layers.Labels):
            return active

        # Last resort: first labels layer
        for layer in viewer_win.viewer.layers:
            if isinstance(layer, napari.layers.Labels):
                return layer
        return None

    def _select_labels_layer_in_viewer(self, labels_layer) -> None:
        """Ensure the given labels layer is the active selection in napari."""
        if self._launcher is None:
            return
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            return
        viewer_win.viewer.layers.selection.active = labels_layer

    def _on_delete_selected_label(self) -> None:
        """Delete the currently selected label from the active labels layer."""
        labels_layer = self._get_active_labels_layer()
        if labels_layer is None:
            self.statusBar().showMessage("No labels layer active")
            return

        selected_id = labels_layer.selected_label
        if selected_id == 0:
            self.statusBar().showMessage("No label selected (click a cell first)")
            return

        # Zero out all pixels with this label
        data = labels_layer.data.copy()
        count = int(np.sum(data == selected_id))
        data[data == selected_id] = 0
        labels_layer.data = data
        labels_layer.selected_label = 0
        labels_layer.refresh()

        self.statusBar().showMessage(
            f"Deleted label {selected_id} ({count} pixels removed)"
        )

    def _on_add_new_label(self) -> None:
        """Set the active segmentation layer to polygon mode with next ID.

        Automatically selects the active segmentation layer in napari
        so the viewer is immediately ready to draw.
        """
        labels_layer = self._get_active_labels_layer()
        if labels_layer is None:
            self.statusBar().showMessage("No labels layer active — load or create one first")
            return

        # Select this layer in napari so it receives the drawing
        self._select_labels_layer_in_viewer(labels_layer)

        next_id = int(labels_layer.data.max()) + 1
        labels_layer.selected_label = next_id
        labels_layer.mode = "polygon"

        # Bring the viewer to front
        viewer_win = self._launcher._windows.get("viewer") if self._launcher else None
        if viewer_win is not None:
            viewer_win.show()

        self.statusBar().showMessage(
            f"Label {next_id} — draw cell boundary with polygon tool"
        )

    def _on_relabel_sequential(self) -> None:
        """Relabel the active labels layer to sequential IDs [1..N]."""
        labels_layer = self._get_active_labels_layer()
        if labels_layer is None:
            self.statusBar().showMessage("No labels layer active")
            return

        from percell4.segment.postprocess import relabel_sequential

        old_data = labels_layer.data
        new_data = relabel_sequential(np.asarray(old_data, dtype=np.int32))
        n_cells = int(new_data.max())
        labels_layer.data = new_data
        labels_layer.refresh()

        self.statusBar().showMessage(
            f"Relabeled to {n_cells} sequential cells"
        )

    # ── Label Cleanup ─────────────────────────────────────────

    def _on_cleanup_preview(self) -> None:
        """Highlight cells that would be removed by current settings."""
        labels_layer = self._get_active_labels_layer()
        if labels_layer is None:
            self._cleanup_status.setText("No labels layer active.")
            self._cleanup_status.setStyleSheet("color: #ff6666;")
            return

        from percell4.segment.postprocess import filter_edge_cells, filter_small_cells

        labels = np.asarray(labels_layer.data, dtype=np.int32)
        margin = self._cleanup_margin.value()
        min_area = self._cleanup_min_area.value()

        filtered = labels
        edge_removed = 0
        small_removed = 0

        if margin >= 0:
            filtered, edge_removed = filter_edge_cells(filtered, edge_margin=margin)
        if min_area > 0:
            filtered, small_removed = filter_small_cells(filtered, min_area=min_area)

        total_removed = edge_removed + small_removed

        viewer_win = self._launcher._windows.get("viewer") if self._launcher else None
        if viewer_win is None or viewer_win.viewer is None:
            return

        # Remove old preview layer if it exists
        for layer in list(viewer_win.viewer.layers):
            if layer.name == "_cleanup_preview":
                viewer_win.viewer.layers.remove(layer)
                break

        if total_removed == 0:
            self._cleanup_status.setText("No cells to remove at these settings.")
            self._cleanup_status.setStyleSheet("color: #888888;")
            self._btn_apply_cleanup.setEnabled(False)
            return

        # Create highlight: cells that would be removed shown as label 1
        removed_mask = (labels > 0) & (filtered == 0)
        highlight = np.where(removed_mask, 1, 0).astype(np.int32)

        viewer_win.viewer.add_labels(
            highlight,
            name="_cleanup_preview",
            opacity=0.5,
            blending="translucent",
        )

        parts = []
        if edge_removed:
            parts.append(f"{edge_removed} edge")
        if small_removed:
            parts.append(f"{small_removed} small")
        self._cleanup_status.setText(
            f"{total_removed} cells to remove ({', '.join(parts)})."
        )
        self._cleanup_status.setStyleSheet("color: #ffaa44;")
        self._btn_apply_cleanup.setEnabled(True)

    def _on_cleanup_apply(self) -> None:
        """Remove highlighted cells from the label layer."""
        labels_layer = self._get_active_labels_layer()
        if labels_layer is None:
            self._cleanup_status.setText("No labels layer active.")
            self._cleanup_status.setStyleSheet("color: #ff6666;")
            return

        from percell4.segment.postprocess import (
            filter_edge_cells,
            filter_small_cells,
            relabel_sequential,
        )

        labels = np.asarray(labels_layer.data, dtype=np.int32)
        margin = self._cleanup_margin.value()
        min_area = self._cleanup_min_area.value()

        filtered = labels
        edge_removed = 0
        small_removed = 0

        if margin >= 0:
            filtered, edge_removed = filter_edge_cells(filtered, edge_margin=margin)
        if min_area > 0:
            filtered, small_removed = filter_small_cells(filtered, min_area=min_area)

        total_removed = edge_removed + small_removed

        # Relabel to sequential after removal
        filtered = relabel_sequential(filtered)
        n_remaining = int(filtered.max())

        # Update the labels layer
        labels_layer.data = filtered
        labels_layer.refresh()

        # Remove preview layer
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
        self._cleanup_status.setStyleSheet("color: #66cc66;")

        self.statusBar().showMessage(
            f"Cleanup: removed {total_removed}, {n_remaining} cells remaining"
        )

    # ── Save ──────────────────────────────────────────────────

    def _on_save_labels(self) -> None:
        store = getattr(self._launcher, "_current_store", None) if self._launcher else None
        if store is None:
            self.statusBar().showMessage("No dataset loaded")
            return

        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            self.statusBar().showMessage("Viewer not open")
            return

        # Find active labels layer
        active = viewer_win.viewer.layers.selection.active
        if active is not None and active.__class__.__name__ == "Labels":
            name = active.name
            data = active.data
        else:
            for layer in viewer_win.viewer.layers:
                if layer.__class__.__name__ == "Labels":
                    name = layer.name
                    data = layer.data
                    break
            else:
                self.statusBar().showMessage("No labels layer to save")
                return

        count = store.write_labels(name, np.asarray(data, dtype=np.int32))
        self.statusBar().showMessage(f"Saved labels '{name}' ({count} pixels)")

    # ── Lifecycle ─────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._save_geometry()
        self.hide()
        event.ignore()

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QLabel { color: #e0e0e0; }
            QGroupBox {
                color: #ffffff;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 16px;
            }
            QGroupBox::title {
                color: #4ea8de;
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                background-color: #2a2a2a;
                color: #ffffff;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover { background-color: #3a3a3a; border-color: #4ea8de; }
            QComboBox, QSpinBox {
                background-color: #2a2a2a;
                color: #ffffff;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QStatusBar {
                background-color: #0d1b2a;
                color: #a0a0a0;
            }
        """)

    def _save_geometry(self) -> None:
        QSettings("LeeLabPerCell4", "PerCell4").setValue(
            "segmentation/geometry", self.saveGeometry()
        )

    def _restore_geometry(self) -> None:
        geom = QSettings("LeeLabPerCell4", "PerCell4").value("segmentation/geometry")
        if geom:
            self.restoreGeometry(geom)
