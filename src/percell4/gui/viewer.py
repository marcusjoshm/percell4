"""Napari viewer window with full layer controls and a PerCell4 side panel.

Uses napari's own QMainWindow (which includes the layer list, layer controls,
colormaps, opacity, blending, etc.) and adds a custom PerCell4 dock panel
for segmentation/mask management and selection sync.
"""

from __future__ import annotations

from qtpy.QtCore import QSettings, Qt
from qtpy.QtWidgets import (
    QComboBox,
    QDockWidget,
    QLabel,
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


class ViewerWindow:
    """Wraps napari's own window and adds a PerCell4 layer manager panel.

    This is NOT a QMainWindow subclass — napari manages its own window.
    We add a custom dock widget to napari's window for PerCell4-specific
    controls (active segmentation/mask selectors, save buttons).

    Napari's built-in UI is fully available:
    - Layer list (left panel) with visibility toggles
    - Layer controls (colormap, opacity, blending, contrast limits)
    - All editing tools (paint, fill, erase for labels)
    - Dims slider for multi-dimensional data
    """

    def __init__(self, data_model: CellDataModel) -> None:
        self.data_model = data_model
        self._viewer = None
        self._qt_window = None
        self._percell_dock = None

    def _ensure_viewer(self) -> None:
        """Lazily create the napari viewer on first use."""
        if self._viewer is not None:
            return

        import napari

        self._viewer = napari.Viewer(
            title="PerCell4 — Viewer",
            show=False,
        )
        self._qt_window = self._viewer.window._qt_window

        # Add our custom PerCell4 panel as a dock widget
        self._add_percell_panel()

        # Wire napari label selection → CellDataModel
        self._viewer.layers.events.inserted.connect(self._on_layer_inserted)

        # Restore window geometry
        self._restore_geometry()

    def _add_percell_panel(self) -> None:
        """Add the PerCell4 layer manager dock to napari's window."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignTop)

        title = QLabel("PerCell4")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        layout.addSpacing(8)

        # Active segmentation selector
        layout.addWidget(QLabel("Active Segmentation:"))
        self._seg_combo = QComboBox()
        self._seg_combo.setPlaceholderText("None loaded")
        layout.addWidget(self._seg_combo)

        layout.addSpacing(4)

        # Active mask selector
        layout.addWidget(QLabel("Active Mask:"))
        self._mask_combo = QComboBox()
        self._mask_combo.setPlaceholderText("None loaded")
        layout.addWidget(self._mask_combo)

        layout.addSpacing(12)

        # Save buttons
        self._save_labels_btn = QPushButton("Save Labels to HDF5")
        self._save_labels_btn.setEnabled(False)
        layout.addWidget(self._save_labels_btn)

        self._save_mask_btn = QPushButton("Save Mask to HDF5")
        self._save_mask_btn.setEnabled(False)
        layout.addWidget(self._save_mask_btn)

        layout.addStretch()

        # Add to napari's window as a dock widget on the right
        dock = QDockWidget("PerCell4", self._qt_window)
        dock.setWidget(panel)
        dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self._qt_window.addDockWidget(Qt.RightDockWidgetArea, dock)
        self._percell_dock = dock

    @property
    def viewer(self):
        """Access the napari viewer (creates it if needed)."""
        self._ensure_viewer()
        return self._viewer

    def show(self) -> None:
        """Show the napari viewer window."""
        self._ensure_viewer()
        self._qt_window.show()
        self._qt_window.raise_()
        self._qt_window.activateWindow()

    def hide(self) -> None:
        """Hide the napari viewer window (preserve state)."""
        if self._qt_window is not None:
            self._qt_window.hide()

    def isMinimized(self) -> bool:
        if self._qt_window is not None:
            return self._qt_window.isMinimized()
        return False

    def showNormal(self) -> None:
        if self._qt_window is not None:
            self._qt_window.showNormal()

    def raise_(self) -> None:
        if self._qt_window is not None:
            self._qt_window.raise_()

    def activateWindow(self) -> None:
        if self._qt_window is not None:
            self._qt_window.activateWindow()

    def close(self) -> None:
        """Close the napari viewer window."""
        if self._qt_window is not None:
            self._save_geometry()
            self._qt_window.hide()

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
        if self._seg_combo is not None:
            self._seg_combo.clear()
        if self._mask_combo is not None:
            self._mask_combo.clear()
            self._save_labels_btn.setEnabled(False)
            self._save_mask_btn.setEnabled(False)

    @property
    def active_segmentation(self) -> str | None:
        """Currently selected segmentation name."""
        if self._seg_combo is None:
            return None
        text = self._seg_combo.currentText()
        return text if text else None

    @property
    def active_mask(self) -> str | None:
        """Currently selected mask name."""
        if self._mask_combo is None:
            return None
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

    def _save_geometry(self) -> None:
        if self._qt_window is not None:
            QSettings("LeeLabPerCell4", "PerCell4").setValue(
                "viewer/geometry", self._qt_window.saveGeometry()
            )

    def _restore_geometry(self) -> None:
        geom = QSettings("LeeLabPerCell4", "PerCell4").value("viewer/geometry")
        if geom and self._qt_window is not None:
            self._qt_window.restoreGeometry(geom)
