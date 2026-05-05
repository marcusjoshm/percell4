"""Napari viewer window with full built-in layer controls.

Uses napari's own QMainWindow which includes the layer list, layer controls,
colormaps, opacity, blending, editing tools, and dims slider.
"""

from __future__ import annotations

import logging
import weakref

from qtpy.QtCore import QObject, QSettings, Signal

from percell4.config import viewer_presets as vp
from percell4.model import CellDataModel

logger = logging.getLogger(__name__)

# Layer metadata constants for classifying napari Labels layers
PERCELL_TYPE_KEY = "percell_type"
LAYER_TYPE_MASK = "mask"
LAYER_TYPE_SEGMENTATION = "segmentation"

# DEPRECATED: import from percell4.config.viewer_presets directly. Re-exported
# here for one release so external readers (notebooks, scripts) keep working.
CHANNEL_COLORMAPS = vp.CHANNEL_COLORMAPS


# ── Module-level registry for the napari `M` keymap handler ────────
#
# napari's `Labels.bind_key("M", ...)` rewrites `Labels.class_keymap`
# process-wide; the handler receives the Labels instance, not the
# `ViewerWindow` that owns it. We keep a WeakSet of live ViewerWindows
# and pick the one whose napari Viewer contains the firing Labels layer.
# A WeakSet — not a dict — so closed-then-GC'd ViewerWindows drop out
# without bookkeeping. Resolution is O(N_viewers); the launcher creates
# one ViewerWindow, so N is effectively 1.
_VIEWER_WINDOWS: "weakref.WeakSet[ViewerWindow]" = weakref.WeakSet()
# Subscribers (typically LauncherWindow) interested in `M` events.
# Each is a callable taking the originating `ViewerWindow`.
# Stored as a list of weak method refs so a closed launcher drops out.
_M_SUBSCRIBERS: list[weakref.WeakMethod] = []
_M_BIND_KEY_REGISTERED = False


def subscribe_multi_select_requested(slot) -> None:
    """Register `slot` (a bound method) to run on every `M` keystroke.

    Use this from `LauncherWindow.__init__` — it survives the test
    fixture's direct ``launcher._windows["viewer"] = viewer_win`` shim
    that bypasses the lazy viewer-construction wiring.
    """
    _prune_dead_subscribers()
    ref = weakref.WeakMethod(slot)
    _M_SUBSCRIBERS.append(ref)


def _prune_dead_subscribers() -> None:
    _M_SUBSCRIBERS[:] = [r for r in _M_SUBSCRIBERS if r() is not None]


def _dispatch_multi_select_for_viewer(vw: "ViewerWindow") -> None:
    """Emit the `multi_select_requested` signal and run module-level
    subscribers. Shared by the Labels-active and viewer-level paths.

    Subscribers whose underlying QObject has been deleted on the C++
    side (zombie WeakMethod target) are caught and dropped; this
    matters across pytest cases that build and tear down LauncherWindow
    instances within the same process."""
    vw.multi_select_requested.emit()
    _prune_dead_subscribers()
    dead: list[weakref.WeakMethod] = []
    for ref in list(_M_SUBSCRIBERS):
        slot = ref()
        if slot is None:
            continue
        try:
            slot()
        except RuntimeError:
            dead.append(ref)
    for ref in dead:
        try:
            _M_SUBSCRIBERS.remove(ref)
        except ValueError:
            pass


def _on_m_keystroke_labels(layer):
    """Napari `Labels.class_keymap['M']` handler — fires when a Labels
    layer is the active layer. Walks `_VIEWER_WINDOWS` to find the
    owning ViewerWindow.

    Always claims the keystroke (returns None) so napari's native
    `new_label` action never fires while PerCell4 owns the embed.
    """
    for vw in list(_VIEWER_WINDOWS):
        viewer = vw._viewer
        if viewer is None:
            continue
        try:
            if layer in viewer.layers:
                _dispatch_multi_select_for_viewer(vw)
                return
        except Exception:  # noqa: BLE001
            logger.debug("error checking layers for M (labels)", exc_info=True)


def _on_m_keystroke_viewer(viewer):
    """Napari `Viewer.class_keymap['M']` handler — fires when no Labels
    layer is active (image-only scene, or no layers at all). Required
    so the no-labels-layer precondition surfaces a per-cause status
    message instead of silently consuming the keystroke."""
    for vw in list(_VIEWER_WINDOWS):
        if vw._viewer is viewer:
            _dispatch_multi_select_for_viewer(vw)
            return


def _register_m_bind_key_once() -> None:
    """Register the `M` handlers on `Labels.class_keymap` and
    `Viewer.class_keymap` exactly once per process.

    Two bindings because napari's keymap chain only consults the active
    layer's class_keymap; the viewer-level binding picks up the
    no-labels-layer case where Labels.class_keymap is never visited."""
    global _M_BIND_KEY_REGISTERED
    if _M_BIND_KEY_REGISTERED:
        return
    from napari import Viewer
    from napari.layers import Labels

    Labels.bind_key("M", _on_m_keystroke_labels, overwrite=True)
    Viewer.bind_key("M", _on_m_keystroke_viewer, overwrite=True)
    _M_BIND_KEY_REGISTERED = True


class ViewerWindow(QObject):
    """Wraps napari's own window for image viewing.

    Napari's built-in UI is fully available: layer list, layer controls
    (colormap, opacity, blending, contrast limits), editing tools (paint,
    fill, erase for labels), and dims slider. Label selection syncs to
    CellDataModel.

    If the user closes the napari window, it is recreated on the next show().
    """

    # Emitted when the user presses `M` on the napari canvas. The
    # launcher subscribes and runs its `_on_multi_select` slot. Bound
    # via `Labels.bind_key`, which beats napari's native `new_label`
    # action because `Labels.class_keymap` is checked before any
    # viewer-level keymap.
    multi_select_requested = Signal()

    def __init__(self, data_model: CellDataModel) -> None:
        super().__init__()
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
        _VIEWER_WINDOWS.add(self)

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

        # Install the process-wide `M` handler before any napari Viewer
        # exists so `Labels.class_keymap` already wins over the native
        # new_label action by the time a Labels layer is added.
        _register_m_bind_key_once()

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

        cmap, self._color_index = vp._colormap_for_channel(name, self._color_index)
        cmap = kwargs.pop("colormap", cmap)
        blending = kwargs.pop("blending", vp.IMAGE_DEFAULT_BLENDING)

        # Opacity: None sentinel = don't pass kwarg. Caller > preset > napari default.
        opacity = kwargs.pop("opacity", vp.IMAGE_DEFAULT_OPACITY)
        if opacity is not None:
            kwargs["opacity"] = opacity

        # Contrast limits precedence: caller > preset override > auto-from-data.
        if "contrast_limits" not in kwargs:
            if vp.IMAGE_DEFAULT_CONTRAST_OVERRIDE is not None:
                kwargs["contrast_limits"] = vp.IMAGE_DEFAULT_CONTRAST_OVERRIDE
            else:
                # Auto-from-data so sparse images aren't blank
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
        blending = kwargs.pop("blending", vp.LABELS_DEFAULT_BLENDING)
        metadata = kwargs.pop("metadata", {})
        metadata.setdefault(PERCELL_TYPE_KEY, LAYER_TYPE_SEGMENTATION)

        # Opacity: None sentinel = don't pass kwarg. Caller > preset > napari default.
        opacity = kwargs.pop("opacity", vp.LABELS_DEFAULT_OPACITY)
        if opacity is not None:
            kwargs["opacity"] = opacity

        # Colormap precedence: caller-passed colormap wins; else preset color_dict
        # (wrapped lazily in DirectLabelColormap); else napari default (random palette).
        # Asymmetry note: this differs from add_mask, where BINARY_MASK_COLOR_DICT
        # is the contract and silently overrides any caller colormap.
        if "colormap" not in kwargs and vp.LABELS_DEFAULT_COLOR_DICT is not None:
            from napari.utils.colormaps import DirectLabelColormap
            kwargs["colormap"] = DirectLabelColormap(
                color_dict=dict(vp.LABELS_DEFAULT_COLOR_DICT)
            )

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
            color_dict = dict(vp.BINARY_MASK_COLOR_DICT)
        kwargs.pop("colormap", None)  # color_dict takes precedence
        cmap = DirectLabelColormap(color_dict=color_dict)

        blending = kwargs.pop("blending", vp.MASK_DEFAULT_BLENDING)

        if name in self.viewer.layers:
            layer = self.viewer.layers[name]
            layer.data = data
            layer.colormap = cmap
            layer.blending = blending
            layer.metadata[PERCELL_TYPE_KEY] = LAYER_TYPE_MASK
        else:
            if "opacity" not in kwargs:
                kwargs["opacity"] = vp.MASK_DEFAULT_OPACITY
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
            # Bind `M` per-instance so it beats both `Labels.class_keymap`
            # (which napari's action_manager hijacks back to `new_label`
            # whenever a Labels layer is registered) and `viewer.keymap`.
            # Per the napari keymap chain `[user_keymap, layer.keymap,
            # layer.class_keymap, viewer.keymap, viewer.class_keymap]`,
            # the per-instance `keymap` wins.
            layer.bind_key("M", _on_m_keystroke_labels, overwrite=True)

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
        if change.mask:
            self._push_active_layer_to_napari(
                self.data_model.session.active_mask, LAYER_TYPE_MASK
            )
        if change.segmentation:
            self._push_active_layer_to_napari(
                self.data_model.session.active_segmentation,
                LAYER_TYPE_SEGMENTATION,
            )

    def _push_active_layer_to_napari(
        self, layer_name: str | None, percell_type: str
    ) -> None:
        """Reflect a session active_mask/active_segmentation write into napari.

        Sets ``viewer.layers.selection.active`` to the layer matching
        ``layer_name`` and ``percell_type``. No-op if the viewer is torn down,
        the name is empty, or no matching layer exists. Guarded by
        ``_is_originator`` so the napari side-effect cannot loop back into
        session via any future bidirectional wiring.
        """
        if not self._is_alive() or self._viewer is None:
            return
        if not layer_name:
            return
        layer = self._find_layer_by_name_and_type(layer_name, percell_type)
        if layer is None:
            logger.debug(
                "no napari layer matches name=%r percell_type=%r",
                layer_name,
                percell_type,
            )
            return
        self._is_originator = True
        try:
            self._viewer.layers.selection.active = layer
        finally:
            self._is_originator = False

    def _find_layer_by_name_and_type(self, name: str, percell_type: str):
        """Find a Labels layer whose ``name`` and ``metadata[PERCELL_TYPE_KEY]``
        match the requested values. Returns the layer or ``None``."""
        import napari

        if self._viewer is None:
            return None
        for layer in self._viewer.layers:
            if not isinstance(layer, napari.layers.Labels):
                continue
            if layer.name != name:
                continue
            if layer.metadata.get(PERCELL_TYPE_KEY) == percell_type:
                return layer
        return None

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
                color_dict[None] = list(vp.TRANSPARENT_RGBA)
                for lid in visible_ids:
                    if lid in highlight_ids:
                        color_dict[lid] = list(vp.SELECTION_HIGHLIGHT_RGBA)
                    else:
                        color_dict[lid] = list(vp.SELECTION_DIM_NONSELECTED)
            elif visible_ids:
                # Filter only: show filtered, hide rest
                color_dict[None] = list(vp.TRANSPARENT_RGBA)
                for lid in visible_ids:
                    color_dict[lid] = list(vp.FILTER_ONLY_VISIBLE_RGBA)
            elif highlight_ids:
                # Selection only: highlight selected, dim rest
                color_dict[None] = list(vp.TRANSPARENT_RGBA)
                for lid in highlight_ids:
                    color_dict[lid] = list(vp.SELECTION_HIGHLIGHT_RGBA)

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

        if not self._is_alive() or self._viewer is None:
            return
        primary = self._get_active_labels_layer()
        if primary is None:
            return

        color_dict = {0: "transparent", None: "transparent"}
        for lid in staged_ids:
            color_dict[int(lid)] = list(vp.STAGED_OVERLAY_COLOR)
        cmap = DirectLabelColormap(color_dict=color_dict)

        if vp.STAGED_OVERLAY_LAYER_NAME in self._viewer.layers:
            overlay = self._viewer.layers[vp.STAGED_OVERLAY_LAYER_NAME]
            overlay.data = primary.data
            overlay.colormap = cmap
        else:
            self._viewer.add_labels(
                primary.data,
                name=vp.STAGED_OVERLAY_LAYER_NAME,
                colormap=cmap,
                opacity=vp.STAGED_OVERLAY_OPACITY,
                blending=vp.STAGED_OVERLAY_BLENDING,
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

        if not self._is_alive() or self._viewer is None:
            return
        if vp.STAGED_OVERLAY_LAYER_NAME not in self._viewer.layers:
            return
        color_dict = {0: "transparent", None: "transparent"}
        for lid in staged_ids:
            color_dict[int(lid)] = list(vp.STAGED_OVERLAY_COLOR)
        overlay = self._viewer.layers[vp.STAGED_OVERLAY_LAYER_NAME]
        overlay.colormap = DirectLabelColormap(color_dict=color_dict)

    def remove_staged_overlay(self) -> None:
        """Remove the staging overlay layer. Idempotent."""
        if not self._is_alive() or self._viewer is None:
            return
        if vp.STAGED_OVERLAY_LAYER_NAME in self._viewer.layers:
            del self._viewer.layers[vp.STAGED_OVERLAY_LAYER_NAME]

    def launch_multi_select_tool(self, launcher) -> str:
        """Open the modal multi-label selection tool.

        Constructs a :class:`MultiLabelSelectController` and retains a
        reference so Qt doesn't GC it mid-session.

        Returns a per-cause result string:
          - ``"ok"`` — controller installed and shown.
          - ``"viewer_not_alive"`` — napari Qt window is gone.
          - ``"workflow_locked"`` — another batch tool owns the lock.
          - ``"no_labels_layer"`` — no Labels layer matches the active
            segmentation (or there is none at all).
        """
        from percell4.gui.multi_select import MultiLabelSelectController

        self._ensure_viewer()
        if not self.is_viewer_alive():
            return "viewer_not_alive"
        if getattr(launcher, "is_workflow_locked", False):
            return "workflow_locked"
        if self.active_labels_layer_or_none() is None:
            return "no_labels_layer"

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
            # Preconditions passed above but the controller's own show
            # check refused — surface a generic reason for resilience.
            return "viewer_not_alive"
        self._multi_select_controller = controller
        return "ok"
