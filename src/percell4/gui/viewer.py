"""Napari viewer window with layer management side panel.

The viewer displays image channels, label overlays, and mask layers.
Selection sync: clicking a label → CellDataModel.set_selection().
"""

from __future__ import annotations

from qtpy.QtCore import QSettings, Qt
from qtpy.QtWidgets import (
    QComboBox,
    QDockWidget,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.model import CellDataModel

# Colormap mapping for common fluorophore names
CHANNEL_COLORMAPS = {
    "dapi": "blue",
    "hoechst": "blue",
    "gfp": "green",
    "fitc": "green",
    "alexa488": "green",
    "rfp": "red",
    "mcherry": "red",
    "tritc": "red",
    "alexa594": "red",
    "cy5": "magenta",
    "alexa647": "magenta",
    "bf": "gray",
    "brightfield": "gray",
    "dic": "gray",
    "phase": "gray",
}


def _colormap_for_channel(name: str) -> str:
    """Auto-detect colormap from channel name."""
    key = name.lower().replace(" ", "").replace("-", "").replace("_", "")
    for pattern, cmap in CHANNEL_COLORMAPS.items():
        if pattern in key:
            return cmap
    return "gray"


class ViewerWindow(QMainWindow):
    """Napari viewer with a layer management side panel."""

    def __init__(self, data_model: CellDataModel) -> None:
        super().__init__()
        self.data_model = data_model
        self.setWindowTitle("PerCell4 — Viewer")
        self.resize(1000, 800)
        self._viewer = None  # Lazy-created on first show

        # Layer manager side panel
        self._create_layer_panel()

        self._restore_geometry()

    def _ensure_viewer(self) -> None:
        """Lazily create the napari viewer on first use."""
        if self._viewer is not None:
            return

        import napari

        self._viewer = napari.Viewer(show=False)
        self.setCentralWidget(self._viewer.window._qt_viewer)

        # Wire napari label selection → CellDataModel
        self._viewer.layers.events.inserted.connect(self._on_layer_inserted)

    def _create_layer_panel(self) -> None:
        """Create the layer manager dock widget."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)

        # Active segmentation selector
        layout.addWidget(QLabel("Active Segmentation:"))
        self._seg_combo = QComboBox()
        self._seg_combo.setPlaceholderText("None loaded")
        layout.addWidget(self._seg_combo)

        # Active mask selector
        layout.addWidget(QLabel("Active Mask:"))
        self._mask_combo = QComboBox()
        self._mask_combo.setPlaceholderText("None loaded")
        layout.addWidget(self._mask_combo)

        # Spacer
        layout.addSpacing(20)

        # Save buttons
        self._save_labels_btn = QPushButton("Save Labels")
        self._save_labels_btn.setEnabled(False)
        layout.addWidget(self._save_labels_btn)

        self._save_mask_btn = QPushButton("Save Mask")
        self._save_mask_btn.setEnabled(False)
        layout.addWidget(self._save_mask_btn)

        layout.addStretch()

        # Add as dock widget
        dock = QDockWidget("Layer Manager", self)
        dock.setWidget(panel)
        dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    @property
    def viewer(self):
        """Access the napari viewer (creates it if needed)."""
        self._ensure_viewer()
        return self._viewer

    def show(self) -> None:
        """Override show to lazily create the napari viewer."""
        self._ensure_viewer()
        super().show()

    def add_image(self, data, name: str, **kwargs) -> None:
        """Add an image layer with auto-detected colormap."""
        cmap = kwargs.pop("colormap", _colormap_for_channel(name))
        self.viewer.add_image(data, name=name, colormap=cmap, **kwargs)

    def add_labels(self, data, name: str, **kwargs) -> None:
        """Add a labels layer and update the segmentation dropdown."""
        self.viewer.add_labels(data, name=name, **kwargs)
        if self._seg_combo.findText(name) == -1:
            self._seg_combo.addItem(name)
        self._seg_combo.setCurrentText(name)
        self._save_labels_btn.setEnabled(True)

    def add_mask(self, data, name: str, **kwargs) -> None:
        """Add a binary mask as a labels layer and update the mask dropdown."""
        self.viewer.add_labels(data, name=name, opacity=0.4, **kwargs)
        if self._mask_combo.findText(name) == -1:
            self._mask_combo.addItem(name)
        self._mask_combo.setCurrentText(name)
        self._save_mask_btn.setEnabled(True)

    def clear(self) -> None:
        """Remove all layers and reset dropdowns."""
        if self._viewer is not None:
            self._viewer.layers.clear()
        self._seg_combo.clear()
        self._mask_combo.clear()
        self._save_labels_btn.setEnabled(False)
        self._save_mask_btn.setEnabled(False)

    @property
    def active_segmentation(self) -> str | None:
        """Currently selected segmentation name."""
        text = self._seg_combo.currentText()
        return text if text else None

    @property
    def active_mask(self) -> str | None:
        """Currently selected mask name."""
        text = self._mask_combo.currentText()
        return text if text else None

    def _on_layer_inserted(self, event) -> None:
        """Wire selection events when a new labels layer is added."""
        import napari

        layer = event.value
        if isinstance(layer, napari.layers.Labels):
            layer.events.selected_label.connect(self._on_label_selected)

    def _on_label_selected(self, event) -> None:
        """Forward label selection to CellDataModel."""
        label_id = event.value
        if label_id == 0:
            self.data_model.set_selection([])
        else:
            self.data_model.set_selection([label_id])

    def closeEvent(self, event) -> None:
        self._save_geometry()
        self.hide()
        event.ignore()

    def _save_geometry(self) -> None:
        QSettings("LeeLabPerCell4", "PerCell4").setValue(
            "viewer/geometry", self.saveGeometry()
        )

    def _restore_geometry(self) -> None:
        geom = QSettings("LeeLabPerCell4", "PerCell4").value("viewer/geometry")
        if geom:
            self.restoreGeometry(geom)
