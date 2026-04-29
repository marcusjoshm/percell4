"""Napari viewer window with full built-in layer controls.

Uses napari's own QMainWindow which includes the layer list, layer controls,
colormaps, opacity, blending, editing tools, and dims slider.
"""

from __future__ import annotations

import logging

from qtpy.QtCore import QSettings

from percell4.model import CellDataModel

logger = logging.getLogger(__name__)

# Layer metadata constants for classifying napari Labels layers
PERCELL_TYPE_KEY = "percell_type"
LAYER_TYPE_MASK = "mask"
LAYER_TYPE_SEGMENTATION = "segmentation"

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
        self._is_originator = False
        self._selected_label_forwarding_suspended = False
        self._original_colormaps: dict[str, object] = {}  # {layer_name: colormap}
        self._hidden_mask_layers: dict[str, float] = {}  # {layer_name: original_opacity}
        # Held so Qt doesn't GC the multi-select controller mid-session.
        self._multi_select_controller = None

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

        # Wire CellDataModel state changes → napari display.
        # Single signal replaces the old coalescing timer — no rapid-fire to coalesce.
        self.data_model.state_changed.connect(self._on_state_changed)

        self._restore_geometry()

    @property
    def viewer(self):
        """Access the napari viewer (creates it if needed)."""
        self._ensure_viewer()
        return self._viewer

    def set_subtitle(self, subtitle: str) -> None:
        """Update the window title to show a subtitle (e.g. filename)."""
        self._ensure_viewer()
        if subtitle:
            self._qt_window.setWindowTitle(f"PerCell4 — Viewer — {subtitle}")
        else:
            self._qt_window.setWindowTitle("PerCell4 — Viewer")

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
        metadata = kwargs.pop("metadata", {})
        metadata.setdefault(PERCELL_TYPE_KEY, LAYER_TYPE_SEGMENTATION)
        self.viewer.add_labels(
            data, name=name, blending=blending, metadata=metadata, **kwargs
        )

    def add_mask(
        self, data, name: str, color_dict: dict | None = None, **kwargs
    ) -> None:
        """Add a mask as a labels layer (idempotent).

        If a layer with the same name already exists, updates its data and
        colormap in-place instead of creating a duplicate (which would trigger
        napari's auto-rename to ``name [1]``).

        Args:
            color_dict: optional {label_value: color} for multi-label masks.
                        Defaults to {0: transparent, 1: yellow} for binary masks.
                        Takes precedence over colormap in kwargs.
        """
        from napari.utils.colormaps import DirectLabelColormap

        if color_dict is None:
            color_dict = {0: "transparent", 1: "yellow", None: "transparent"}
        kwargs.pop("colormap", None)  # color_dict takes precedence
        cmap = DirectLabelColormap(color_dict=color_dict)

        blending = kwargs.pop("blending", "additive")

        if name in self.viewer.layers:
            layer = self.viewer.layers[name]
            layer.data = data
            layer.colormap = cmap
            layer.blending = blending
            layer.metadata[PERCELL_TYPE_KEY] = LAYER_TYPE_MASK
        else:
            if "opacity" not in kwargs:
                kwargs["opacity"] = 0.5
            self.viewer.add_labels(
                data,
                name=name,
                colormap=cmap,
                blending=blending,
                metadata={PERCELL_TYPE_KEY: LAYER_TYPE_MASK},
                **kwargs,
            )

    def clear(self) -> None:
        """Remove all layers."""
        self._color_index = 0
        self._original_colormaps.clear()
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
        if self._is_originator:
            return
        if self._selected_label_forwarding_suspended:
            # A modal tool (e.g. multi-select) owns label clicks. Don't
            # wipe the current selection from napari's own pick handler
            # while the tool is active.
            return
        try:
            source = event.source
            label_id = source.selected_label
        except AttributeError:
            return

        self._is_originator = True
        try:
            if label_id == 0:
                self.data_model.set_selection([])
            else:
                self.data_model.set_selection([label_id])
        finally:
            self._is_originator = False

    def _on_state_changed(self, change) -> None:
        """Handle model state changes."""
        if self._is_originator:
            return
        if change.filter or change.selection:
            self._update_label_display()

    def _update_label_display(self) -> None:
        """Update labels layer display based on current selection and filter.

        Uses DirectLabelColormap for GPU-side rendering — never modifies layer.data.
        Single-cell uses napari's built-in show_selected_label.
        Multi-cell dims unselected labels via colormap with None default key.
        """
        if self._is_originator:
            return
        if not self._is_alive():
            return
        labels_layer = self._get_active_labels_layer()
        if labels_layer is None:
            return

        self._is_originator = True
        try:
            from napari.utils.colormaps import DirectLabelColormap

            selected_ids = self.data_model.selected_ids
            filtered_ids = self.data_model.filtered_ids

            if not selected_ids and not filtered_ids:
                # No selection, no filter: restore original colormap
                labels_layer.show_selected_label = False
                self._restore_colormap(labels_layer)
                self._restore_mask_layers()
                return

            # Any selection or filter: use DirectLabelColormap exclusively.
            # We avoid napari's show_selected_label because transitioning
            # from that mode to DirectLabelColormap causes a rendering glitch
            # where the first colormap application after show_selected_label
            # doesn't take effect.
            labels_layer.show_selected_label = False

            # Save original colormap before first replacement
            self._save_colormap(labels_layer)

            # Hide mask layers so their colors don't override selection colors
            self._hide_mask_layers()

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
                color_dict[None] = [0.0, 0.0, 0.0, 0.0]
                for lid in highlight_ids:
                    color_dict[lid] = [1.0, 1.0, 0.0, 0.8]

            labels_layer.colormap = DirectLabelColormap(
                color_dict=color_dict
            )
        finally:
            self._is_originator = False

    def _save_colormap(self, layer) -> None:
        """Save the layer's original colormap if not already cached."""
        if layer.name not in self._original_colormaps:
            self._original_colormaps[layer.name] = layer.colormap

    def _restore_colormap(self, layer) -> None:
        """Restore the layer's original colormap if we previously replaced it."""
        if layer.name in self._original_colormaps:
            layer.colormap = self._original_colormaps.pop(layer.name)

    def _hide_mask_layers(self) -> None:
        """Hide mask layers during selection/filter highlighting.

        Mask layers (e.g., phasor_roi) sit above the segmentation layer and
        their colors override the selection colormap. Hide them temporarily
        and restore when selection/filter is cleared.
        """
        import napari

        if self._viewer is None:
            return
        seg_name = self.data_model.active_segmentation
        for layer in self._viewer.layers:
            if not isinstance(layer, napari.layers.Labels):
                continue
            if layer.name == seg_name or layer.name.startswith("_"):
                continue
            if layer.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_MASK:
                if layer.name not in self._hidden_mask_layers and layer.visible:
                    self._hidden_mask_layers[layer.name] = layer.opacity
                    layer.visible = False

    def _restore_mask_layers(self) -> None:
        """Restore mask layers hidden by _hide_mask_layers."""
        if self._viewer is None or not self._hidden_mask_layers:
            return
        for name, opacity in list(self._hidden_mask_layers.items()):
            try:
                layer = self._viewer.layers[name]
                layer.visible = True
                layer.opacity = opacity
            except KeyError:
                pass
        self._hidden_mask_layers.clear()

    def _get_active_labels_layer(self):
        """Find the segmentation labels layer for selection highlighting.

        Matches active_segmentation by name. Falls back to the first Labels
        layer that isn't a mask/preview layer.
        """
        import napari

        if self._viewer is None:
            return None

        seg_name = self.data_model.active_segmentation
        if seg_name:
            for layer in self._viewer.layers:
                if isinstance(layer, napari.layers.Labels) and layer.name == seg_name:
                    return layer

        # Fallback: find a segmentation layer (skip masks/previews)
        for layer in self._viewer.layers:
            if not isinstance(layer, napari.layers.Labels):
                continue
            if layer.name.startswith("_"):
                continue
            if layer.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_MASK:
                continue
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

    # ── Multi-select tool integration ──────────────────────────
    #
    # These methods satisfy the `StagedRenderer` Protocol in
    # `percell4.gui.multi_select`. They are also used by any future
    # modal viewer tool that wants an overlay layer + forwarding
    # suspension pattern.

    def is_viewer_alive(self) -> bool:
        """Public alias for :meth:`_is_alive` — used by modal tools."""
        return self._is_alive()

    def active_labels_layer_or_none(self):
        """Return the active Labels layer, or None if the viewer is
        torn down or has no Labels layer matching the active
        segmentation."""
        if not self._is_alive():
            return None
        return self._get_active_labels_layer()

    def suspend_selected_label_forwarding(self) -> None:
        """Stop forwarding napari's ``selected_label`` events to the
        model for the duration of a modal tool. Idempotent."""
        self._selected_label_forwarding_suspended = True

    def resume_selected_label_forwarding(self) -> None:
        """Resume forwarding ``selected_label`` events. Idempotent."""
        self._selected_label_forwarding_suspended = False

    def add_staged_overlay(self, staged_ids) -> None:
        """Add (or replace) a read-only Labels layer that renders only
        ``staged_ids`` in cyan. Shares the primary layer's data by
        reference — no copy."""
        from napari.utils.colormaps import DirectLabelColormap

        from percell4.gui.multi_select import (
            _OVERLAY_LAYER_NAME,
            _STAGED_COLOR,
        )

        if not self._is_alive() or self._viewer is None:
            return
        primary = self._get_active_labels_layer()
        if primary is None:
            return

        color_dict = {0: "transparent", None: "transparent"}
        for lid in staged_ids:
            color_dict[int(lid)] = list(_STAGED_COLOR)
        cmap = DirectLabelColormap(color_dict=color_dict)

        if _OVERLAY_LAYER_NAME in self._viewer.layers:
            overlay = self._viewer.layers[_OVERLAY_LAYER_NAME]
            overlay.data = primary.data
            overlay.colormap = cmap
        else:
            self._viewer.add_labels(
                primary.data,
                name=_OVERLAY_LAYER_NAME,
                colormap=cmap,
                opacity=0.6,
                blending="translucent",
                metadata={PERCELL_TYPE_KEY: "multi_select_staged"},
            )
            # Keep the primary labels layer as the active layer so
            # mouse_drag_callbacks appended to it still fire on click.
            try:
                self._viewer.layers.selection.active = primary
            except Exception:  # noqa: BLE001
                logger.debug("could not restore active layer after overlay add")

    def update_staged_overlay(self, staged_ids) -> None:
        """Rebuild the overlay layer's colormap to render
        ``staged_ids``. Called from the controller's coalesced
        refresh timer. No-op if the overlay has been removed."""
        from napari.utils.colormaps import DirectLabelColormap

        from percell4.gui.multi_select import (
            _OVERLAY_LAYER_NAME,
            _STAGED_COLOR,
        )

        if not self._is_alive() or self._viewer is None:
            return
        if _OVERLAY_LAYER_NAME not in self._viewer.layers:
            return
        color_dict = {0: "transparent", None: "transparent"}
        for lid in staged_ids:
            color_dict[int(lid)] = list(_STAGED_COLOR)
        overlay = self._viewer.layers[_OVERLAY_LAYER_NAME]
        overlay.colormap = DirectLabelColormap(color_dict=color_dict)

    def remove_staged_overlay(self) -> None:
        """Remove the staging overlay layer. Idempotent."""
        from percell4.gui.multi_select import _OVERLAY_LAYER_NAME

        if not self._is_alive() or self._viewer is None:
            return
        if _OVERLAY_LAYER_NAME in self._viewer.layers:
            del self._viewer.layers[_OVERLAY_LAYER_NAME]

    def launch_multi_select_tool(self, launcher) -> bool:
        """Open the modal multi-label selection tool.

        Constructs a :class:`MultiLabelSelectController` and retains a
        reference so Qt doesn't GC it mid-session. Returns False if
        the tool could not be installed (no labels layer, workflow
        already locked, viewer not alive).
        """
        from percell4.gui.multi_select import MultiLabelSelectController

        self._ensure_viewer()
        # Tear down any previous controller before opening a new one.
        prev = self._multi_select_controller
        if prev is not None:
            try:
                prev.cancel()
            except Exception:  # noqa: BLE001
                logger.debug("previous multi-select cancel raised", exc_info=True)
            self._multi_select_controller = None

        controller = MultiLabelSelectController(
            viewer_win=self,
            data_model=self.data_model,
            launcher=launcher,
        )
        ok = controller.show()
        if not ok:
            return False
        self._multi_select_controller = controller
        return True
