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
        self.resize(450, 600)

        self._build_ui()
        self._apply_style()
        self._restore_geometry()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
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

        # ── Manual Drawing section ────────────────────────────
        draw_group = QGroupBox("Manual Drawing")
        draw_layout = QVBoxLayout(draw_group)

        draw_layout.addWidget(QLabel(
            "Create a new empty labels layer in napari,\n"
            "then use the paint/fill/erase tools to\n"
            "draw cell boundaries manually."
        ))

        btn_new_labels = QPushButton("Create Empty Labels Layer")
        btn_new_labels.clicked.connect(self._on_create_empty_labels)
        draw_layout.addWidget(btn_new_labels)

        layout.addWidget(draw_group)

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
        if hasattr(self._launcher, "_active_seg_combo"):
            combo = self._launcher._active_seg_combo
            if combo.findText(seg_name) == -1:
                combo.addItem(seg_name)
            combo.setCurrentText(seg_name)

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
