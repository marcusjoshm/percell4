"""Napari viewer window with full built-in layer controls.

Uses napari's own QMainWindow which includes the layer list, layer controls,
colormaps, opacity, blending, editing tools, and dims slider.
"""

from __future__ import annotations

import logging

from qtpy.QtCore import QSettings

from percell4.model import CellDataModel

logger = logging.getLogger(__name__)

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
    """Wraps napari's own window for image viewing.

    Napari's built-in UI is fully available: layer list, layer controls
    (colormap, opacity, blending, contrast limits), editing tools (paint,
    fill, erase for labels), and dims slider. Label selection syncs to
    CellDataModel.

    If the user closes the napari window, it is recreated on the next show().
    """

    def __init__(self, data_model: CellDataModel) -> None:
        self.data_model = data_model
        self._viewer = None
        self._qt_window = None

    def _is_alive(self) -> bool:
        """Check if the napari Qt window still exists (not deleted by Qt)."""
        if self._qt_window is None:
            return False
        try:
            # Accessing any property on a deleted Qt object raises RuntimeError
            self._qt_window.isVisible()
            return True
        except RuntimeError:
            return False

    def _ensure_viewer(self) -> None:
        """Create or recreate the napari viewer if needed."""
        if self._viewer is not None and self._is_alive():
            return

        # Clean up stale references
        self._viewer = None
        self._qt_window = None

        import napari

        self._viewer = napari.Viewer(
            title="PerCell4 — Viewer",
            show=False,
        )
        self._qt_window = self._viewer.window._qt_window

        # Wire napari label selection → CellDataModel
        self._viewer.layers.events.inserted.connect(self._on_layer_inserted)

        self._restore_geometry()

    @property
    def viewer(self):
        """Access the napari viewer (creates it if needed)."""
        self._ensure_viewer()
        return self._viewer

    def show(self) -> None:
        self._ensure_viewer()
        self._qt_window.show()
        self._qt_window.raise_()
        self._qt_window.activateWindow()

    def hide(self) -> None:
        if self._is_alive():
            self._qt_window.hide()

    def isMinimized(self) -> bool:
        if self._is_alive():
            return self._qt_window.isMinimized()
        return False

    def showNormal(self) -> None:
        if self._is_alive():
            self._qt_window.showNormal()

    def raise_(self) -> None:
        if self._is_alive():
            self._qt_window.raise_()

    def activateWindow(self) -> None:
        if self._is_alive():
            self._qt_window.activateWindow()

    def close(self) -> None:
        if self._is_alive():
            self._save_geometry()
            self._qt_window.hide()

    def add_image(self, data, name: str, **kwargs) -> None:
        """Add an image layer with auto-detected colormap."""
        cmap = kwargs.pop("colormap", _colormap_for_channel(name))
        self.viewer.add_image(data, name=name, colormap=cmap, **kwargs)

    def add_labels(self, data, name: str, **kwargs) -> None:
        """Add a labels layer."""
        self.viewer.add_labels(data, name=name, **kwargs)

    def add_mask(self, data, name: str, **kwargs) -> None:
        """Add a binary mask as a labels layer with low opacity."""
        self.viewer.add_labels(data, name=name, opacity=0.4, **kwargs)

    def clear(self) -> None:
        """Remove all layers."""
        if self._viewer is not None and self._is_alive():
            self._viewer.layers.clear()

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
        if self._is_alive():
            QSettings("LeeLabPerCell4", "PerCell4").setValue(
                "viewer/geometry", self._qt_window.saveGeometry()
            )

    def _restore_geometry(self) -> None:
        geom = QSettings("LeeLabPerCell4", "PerCell4").value("viewer/geometry")
        if geom and self._is_alive():
            self._qt_window.restoreGeometry(geom)
