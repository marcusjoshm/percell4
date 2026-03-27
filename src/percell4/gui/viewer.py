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

# Color cycle for channels without a recognized name
_COLOR_CYCLE = ["green", "magenta", "cyan", "yellow", "red", "blue"]


def _colormap_for_channel(name: str, color_index: int = 0) -> tuple[str, int]:
    """Auto-detect colormap from channel name, or cycle through distinct colors.

    Returns (colormap_name, updated_color_index).
    """
    key = name.lower().replace(" ", "").replace("-", "").replace("_", "")
    for pattern, cmap in CHANNEL_COLORMAPS.items():
        if pattern in key:
            return cmap, color_index
    # No match — assign next color from cycle
    color = _COLOR_CYCLE[color_index % len(_COLOR_CYCLE)]
    return color, color_index + 1


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
        self._color_index = 0
        self._updating_selection = False

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

        # Wire CellDataModel selection + filter → napari display
        self.data_model.selection_changed.connect(self._update_label_display)
        self.data_model.filter_changed.connect(self._update_label_display)

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
        """Add an image layer with auto-detected colormap and additive blending."""
        import numpy as np

        cmap, self._color_index = _colormap_for_channel(name, self._color_index)
        cmap = kwargs.pop("colormap", cmap)
        blending = kwargs.pop("blending", "additive")

        # Set contrast limits from actual data range so sparse images aren't blank
        if "contrast_limits" not in kwargs:
            d = np.asarray(data)
            dmin = float(np.nanmin(d)) if np.any(np.isfinite(d)) else 0.0
            dmax = float(np.nanmax(d)) if np.any(np.isfinite(d)) else 1.0
            if dmax > dmin:
                kwargs["contrast_limits"] = (dmin, dmax)

        self.viewer.add_image(
            data, name=name, colormap=cmap, blending=blending, **kwargs
        )

    def add_labels(self, data, name: str, **kwargs) -> None:
        """Add a labels layer with additive blending."""
        blending = kwargs.pop("blending", "additive")
        self.viewer.add_labels(data, name=name, blending=blending, **kwargs)

    def add_mask(
        self, data, name: str, color_dict: dict | None = None, **kwargs
    ) -> None:
        """Add a mask as a labels layer.

        Args:
            color_dict: optional {label_value: color} for multi-label masks.
                        Defaults to {0: transparent, 1: yellow} for binary masks.
                        Takes precedence over colormap in kwargs.
        """
        from napari.utils.colormaps import DirectLabelColormap

        if color_dict is None:
            color_dict = {0: "transparent", 1: "yellow", None: "transparent"}
        kwargs.pop("colormap", None)  # color_dict takes precedence
        kwargs["colormap"] = DirectLabelColormap(color_dict=color_dict)
        if "opacity" not in kwargs:
            kwargs["opacity"] = 0.5
        self.viewer.add_labels(data, name=name, **kwargs)

    def clear(self) -> None:
        """Remove all layers."""
        self._color_index = 0
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
        if self._updating_selection:
            return
        try:
            source = event.source
            label_id = source.selected_label
        except AttributeError:
            return

        self._updating_selection = True
        try:
            if label_id == 0:
                self.data_model.set_selection([])
            else:
                self.data_model.set_selection([label_id])
        finally:
            self._updating_selection = False

    def _update_label_display(self, *args) -> None:
        """Update labels layer display based on current selection and filter.

        Uses DirectLabelColormap for GPU-side rendering — never modifies layer.data.
        Single-cell uses napari's built-in show_selected_label.
        Multi-cell dims unselected labels via colormap with None default key.
        """
        if self._updating_selection:
            return
        if not self._is_alive():
            return
        labels_layer = self._get_active_labels_layer()
        if labels_layer is None:
            return

        self._updating_selection = True
        try:
            from napari.utils.colormaps import DirectLabelColormap

            selected_ids = self.data_model.selected_ids
            filtered_ids = getattr(self.data_model, "filtered_ids", None)

            if not selected_ids and not filtered_ids:
                # No selection, no filter: show all labels normally
                labels_layer.show_selected_label = False
                return

            if len(selected_ids) == 1 and not filtered_ids:
                # Single cell, no filter: use napari's built-in
                with labels_layer.events.selected_label.blocker():
                    labels_layer.selected_label = selected_ids[0]
                labels_layer.show_selected_label = True
                return

            # Multi-cell and/or filter active: use DirectLabelColormap
            labels_layer.show_selected_label = False

            visible_ids = filtered_ids if filtered_ids else None
            highlight_ids = set(selected_ids) if selected_ids else None

            color_dict = {0: "transparent"}

            if visible_ids and highlight_ids:
                # Both: hide non-filtered, dim filtered non-selected, highlight selected
                color_dict[None] = [0.0, 0.0, 0.0, 0.0]
                for lid in visible_ids:
                    if lid in highlight_ids:
                        color_dict[lid] = [1.0, 1.0, 0.0, 0.8]
                    else:
                        color_dict[lid] = [0.5, 0.5, 0.5, 0.15]
            elif visible_ids:
                # Filter only: show filtered, hide rest
                color_dict[None] = [0.0, 0.0, 0.0, 0.0]
                for lid in visible_ids:
                    color_dict[lid] = [0.3, 0.8, 0.8, 0.5]
            elif highlight_ids:
                # Selection only: highlight selected, dim rest
                color_dict[None] = [0.5, 0.5, 0.5, 0.15]
                for lid in highlight_ids:
                    color_dict[lid] = [1.0, 1.0, 0.0, 0.8]

            with labels_layer.events.colormap.blocker():
                labels_layer.colormap = DirectLabelColormap(
                    color_dict=color_dict
                )
            labels_layer.refresh(extent=False)
        finally:
            self._updating_selection = False

    def _get_active_labels_layer(self):
        """Find the labels layer matching the active segmentation."""
        import napari

        if self._viewer is None:
            return None
        seg_name = self.data_model.active_segmentation
        for layer in self._viewer.layers:
            if isinstance(layer, napari.layers.Labels) and layer.name == seg_name:
                return layer
        # Fallback: return the first labels layer if no name match
        for layer in self._viewer.layers:
            if isinstance(layer, napari.layers.Labels):
                return layer
        return None

    def _save_geometry(self) -> None:
        if self._is_alive():
            QSettings("LeeLabPerCell4", "PerCell4").setValue(
                "viewer/geometry", self._qt_window.saveGeometry()
            )

    def _restore_geometry(self) -> None:
        geom = QSettings("LeeLabPerCell4", "PerCell4").value("viewer/geometry")
        if geom and self._is_alive():
            self._qt_window.restoreGeometry(geom)
