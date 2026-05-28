"""Segmentation panel — all segmentation methods in one panel.

Three distinct sections: Cellpose (SAM), Load ROIs, and Manual Drawing,
plus Label Cleanup. Embedded as a sidebar tab in the launcher.
"""

from __future__ import annotations

import logging
import weakref
from pathlib import Path

import numpy as np
from qtpy.QtCore import Qt, QTimer
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

from percell4.config import viewer_presets as vp
from percell4.gui import theme
from percell4.gui._resource_name_prompt import prompt_for_resource_name
from percell4.model import CellDataModel

logger = logging.getLogger(__name__)

# Delay between the last paint event and the HDF5 flush. Long enough to
# coalesce a single brush stroke (napari emits `paint` per drag frame),
# short enough that pressing close right after a stroke still saves.
_PAINT_AUTOSAVE_DEBOUNCE_MS = 200


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

        # Auto-save state: a single debounced timer + the most recently
        # modified Labels layer (held weakly so a removed layer can GC).
        # We track which viewer instance we've already wired so reopening
        # the same viewer doesn't double-subscribe.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(_PAINT_AUTOSAVE_DEBOUNCE_MS)
        self._autosave_timer.timeout.connect(self._flush_pending_autosave)
        self._pending_autosave_layer: "weakref.ref | None" = None
        self._wired_viewer_id: int | None = None
        self._wired_layer_ids: set[int] = set()

        self._build_ui()

        # Wire auto-save on dataset load
        self.data_model.state_changed.connect(self._on_state_changed)

    def _on_state_changed(self, change) -> None:
        if change.segmentation or change.data:
            # Relabel sequential renumbers labels, which would scramble the
            # track ids of a tracked segmentation — disable it for those.
            self._sync_relabel_enabled()
        if change.data:
            # A dataset was loaded (or cleared). Wire auto-save so that any
            # napari-level paint/erase or in-place mutations on Labels
            # layers get persisted without the user remembering to click
            # "Save Labels to HDF5".
            # Defer to the next event-loop tick: ``Session.set_dataset``
            # emits ``state_changed.data`` *before* the launcher's load path
            # creates the viewer window and adds Labels layers, so wiring
            # synchronously would bail out with viewer_win=None and miss the
            # ``layers.events.inserted`` events for layers added later in
            # the same call.
            QTimer.singleShot(0, self._wire_paint_autosave)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignTop)

        from percell4.gui import theme

        layout.addWidget(theme.section_label("Segmentation"))

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

        self._cp_remove_edges = QCheckBox("Remove edge cells")
        self._cp_remove_edges.setChecked(True)
        self._cp_remove_edges.setStyleSheet(f"QCheckBox {{ color: {theme.TEXT}; }}")
        self._cp_remove_edges.setToolTip(
            "Discard cells touching the image border after segmentation.\n"
            "Untick to keep partial edge cells (e.g., for tiled imaging)."
        )
        cp_layout.addWidget(self._cp_remove_edges)

        btn_run_cp = QPushButton("Run Cellpose")
        btn_run_cp.clicked.connect(self._on_run_cellpose)
        cp_layout.addWidget(btn_run_cp)

        layout.addWidget(cp_group)

        # ── Tracking section (time-lapse) ──────────────────────
        track_group = QGroupBox("Tracking (time-lapse)")
        track_layout = QVBoxLayout(track_group)
        track_layout.addWidget(QLabel(
            "Link the active segmentation across timepoints so each\n"
            "cell keeps one ID over time. Divisions are recorded as\n"
            "parent → daughter lineage."
        ))
        self._btn_track = QPushButton("Track Cells Across Timepoints")
        self._btn_track.setToolTip(
            "Requires a time-lapse dataset (filenames with _tN tokens) and a\n"
            "segmentation covering every timepoint. Writes a new "
            "'<segmentation>_tracked' resource (the original is kept for easy\n"
            "editing) where each cell's label value is its track ID, plus a\n"
            "lineage table."
        )
        self._btn_track.clicked.connect(self._on_track_cells)
        track_layout.addWidget(self._btn_track)
        layout.addWidget(track_group)

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

        btn_relabel = self._btn_relabel = QPushButton(
            "Clean Up Labels (relabel sequential)"
        )
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

    # ── Auto-save (manual edits → HDF5) ───────────────────────
    #
    # The Manual Editing group, napari's own paint/erase tools, and the
    # Label Cleanup section all mutate ``labels_layer.data`` in memory.
    # Without auto-save, those edits are lost when the dataset is closed.
    # This panel persists every mutation: button-driven edits write
    # synchronously inside the handler; brush/erase strokes are coalesced
    # by ``_autosave_timer`` to avoid one HDF5 write per drag frame.

    def _persist_labels_layer(self, layer) -> bool:
        """Write a Labels layer's current data to HDF5. Returns True on success."""
        if layer is None:
            return False
        store = (
            getattr(self._launcher, "_current_store", None)
            if self._launcher is not None
            else None
        )
        if store is None:
            return False
        try:
            store.write_labels(
                layer.name, np.asarray(layer.data, dtype=np.int32)
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.exception("auto-save of labels '%s' failed", layer.name)
            self._show_status(f"Auto-save failed for '{layer.name}': {e}")
            return False

    def _wire_paint_autosave(self) -> None:
        """Subscribe to the viewer's layer-add events and to existing Labels.

        Idempotent per viewer instance — re-runs are cheap. Called when the
        ``data`` slice of ``state_changed`` fires (i.e., a dataset was loaded
        or cleared).
        """
        viewer_win = (
            self._launcher._windows.get("viewer") if self._launcher else None
        )
        if viewer_win is None:
            return
        try:
            napari_viewer = viewer_win.viewer
        except Exception:  # noqa: BLE001
            return
        if napari_viewer is None:
            return

        viewer_id = id(napari_viewer)
        if self._wired_viewer_id != viewer_id:
            # New (or first-ever) viewer instance — drop stale per-layer
            # subscriptions and subscribe to its insertion stream.
            self._wired_layer_ids.clear()
            try:
                napari_viewer.layers.events.inserted.connect(
                    self._on_viewer_layer_inserted
                )
            except Exception:  # noqa: BLE001
                logger.debug("could not connect viewer layers.inserted", exc_info=True)
            self._wired_viewer_id = viewer_id

        # Subscribe to any Labels layers already present (the load path
        # adds them before this method runs on the same `change.data`).
        import napari

        for layer in napari_viewer.layers:
            if isinstance(layer, napari.layers.Labels):
                self._subscribe_labels_layer(layer)

    def _on_viewer_layer_inserted(self, event) -> None:
        """Wire a freshly added Labels layer for paint auto-save."""
        import napari

        layer = event.value
        if isinstance(layer, napari.layers.Labels):
            self._subscribe_labels_layer(layer)

    def _subscribe_labels_layer(self, layer) -> None:
        """Connect a Labels layer's paint events to the debounced save slot."""
        layer_id = id(layer)
        if layer_id in self._wired_layer_ids:
            return
        try:
            layer.events.paint.connect(self._on_labels_paint)
        except Exception:  # noqa: BLE001
            logger.debug(
                "labels layer %r missing paint event", getattr(layer, "name", "?"),
                exc_info=True,
            )
            return
        self._wired_layer_ids.add(layer_id)

    def _on_labels_paint(self, event) -> None:
        """Coalesce paint events; the timer fires after the user pauses."""
        try:
            layer = event.source
        except AttributeError:
            return
        self._pending_autosave_layer = weakref.ref(layer)
        self._autosave_timer.start()

    def _flush_pending_autosave(self) -> None:
        """Timer callback — write the most recently painted layer to HDF5."""
        if self._pending_autosave_layer is None:
            return
        layer = self._pending_autosave_layer()
        self._pending_autosave_layer = None
        if layer is None:
            return
        self._persist_labels_layer(layer)

    # ── Cellpose ──────────────────────────────────────────────

    def _on_run_cellpose(self) -> None:
        if self._launcher is None:
            return

        # Read the active channel from Session (the canonical source —
        # the SessionWindow is the only Selector site for active_channel).
        channel_name = self.data_model.session.active_channel
        if not channel_name:
            self._show_status("Select a channel in the Session window first")
            return

        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            self._show_status("Open a dataset in the viewer first")
            return

        # Find the image layer matching the active channel
        active_layer = None
        for layer in viewer_win.viewer.layers:
            if layer.__class__.__name__ == "Image" and layer.name == channel_name:
                active_layer = layer
                break
        if active_layer is None:
            self._show_status(f"Channel '{channel_name}' not found in viewer")
            return

        # Prompt for the segmentation name BEFORE starting Cellpose — cancel
        # is cheap up front, expensive after the worker has run for minutes.
        # Default "cellpose"; refuse-and-re-prompt on collision with any
        # existing /labels/<name> segmentation. Mirrors the Apply Current
        # Phasor as Mask flow via the shared helper.
        store = getattr(self._launcher, "_current_store", None)
        existing_seg: set[str] = set()
        if store is not None:
            try:
                existing_seg = set(store.list_labels()) - set(store.list_masks())
            except Exception:  # noqa: BLE001 — best-effort; collision check is advisory
                existing_seg = set()
        # Capture the session view bin at the moment the worker is queued
        # (BEFORE the user can toggle the SpinBox mid-flight). The captured
        # value travels with the worker results so finalize() upsamples and
        # names against the correct bin, even if the user has since toggled.
        active_bin = self.data_model.session.active_bin
        chosen_name = prompt_for_resource_name(
            self,
            title="Save Segmentation",
            label="Segmentation name:",
            default="cellpose",
            existing_names=existing_seg,
            bin=active_bin,
        )
        if chosen_name is None:
            return
        self._cellpose_pending_name = chosen_name
        self._cellpose_pending_bin = active_bin

        import numpy as np

        image = active_layer.data
        model_type = self._cp_model.currentText()
        diameter = self._cp_diameter.value() if self._cp_diameter.value() > 0 else None
        gpu = self._cp_gpu.isChecked()

        from percell4.gui.workers import Worker
        from percell4.adapters.cellpose import run_cellpose, run_cellpose_stack

        # A time-lapse channel layer is (T, H, W): segment every frame and
        # write one (T, H, W) raw-label resource. A 2D layer is the historical
        # single-frame path. (run_cellpose reads a 3D array as multichannel,
        # so the stack must go through run_cellpose_stack.)
        if np.asarray(image).ndim == 3:
            n_t = int(np.asarray(image).shape[0])
            self._show_status(
                f"Running Cellpose ({model_type}) on {n_t} timepoints..."
            )
            self._worker = Worker(
                run_cellpose_stack,
                image,
                model_type=model_type,
                diameter=diameter,
                gpu=gpu,
            )
        else:
            self._show_status(f"Running Cellpose ({model_type})...")
            self._worker = Worker(
                run_cellpose, image, model_type=model_type, diameter=diameter, gpu=gpu,
            )
        self._worker.finished.connect(self._on_cellpose_done)
        self._worker.error.connect(self._on_cellpose_error)
        self._worker.start()

    def _on_cellpose_error(self, err) -> None:
        from percell4.gui.torch_error import handle_worker_error

        if not handle_worker_error(self, err, context="Cellpose"):
            self._show_status(f"Error: {err.exc_type}: {err.message}")

    def _on_cellpose_done(self, masks) -> None:
        from percell4.application.use_cases.segment_cells import SegmentCells
        from percell4.adapters.hdf5_store import Hdf5DatasetRepository

        # Delegate post-processing + store write to the use case. The
        # captured bin (set at queue time) is what the finalize call uses
        # for upsampling -- a mid-flight session.set_active_bin must NOT
        # alter the resolution of an in-flight worker's result.
        try:
            repo = Hdf5DatasetRepository()
            uc = SegmentCells(repo, self.data_model.session)
            result = uc.finalize(
                masks,
                remove_edge_cells=self._cp_remove_edges.isChecked(),
                name=getattr(self, "_cellpose_pending_name", None),
                view_bin=getattr(self, "_cellpose_pending_bin", 1),
            )
        except ValueError as e:
            self._show_status(f"Error: {e}")
            return
        finally:
            # Always clear both stashes so a subsequent Run gets a fresh prompt.
            self._cellpose_pending_name = None
            self._cellpose_pending_bin = 1

        self._show_status(
            f"Done: {result.n_cells} cells "
            f"({result.edge_removed} edge, {result.small_removed} small removed)"
        )

        # Add labels layer to viewer
        viewer_win = self._launcher._windows.get("viewer") if self._launcher else None
        if viewer_win is not None:
            viewer_win.add_labels(result.labels, name=result.seg_name)

    # ── Tracking (time-lapse) ─────────────────────────────────

    def _on_track_cells(self) -> None:
        """Creator: track the active segmentation across timepoints.

        Runs TrackCells synchronously (it mutates the session, which must
        stay on the main thread), then adds the tracked labels layer to the
        viewer. TrackCells already wrote the resource, refreshed the
        inventory, and set it active (Creator contract).
        """
        from percell4.adapters.hdf5_store import Hdf5DatasetRepository
        from percell4.adapters.laptrack_tracker import LaptrackTracker
        from percell4.application.use_cases.track_cells import TrackCells
        from percell4.domain.errors import NoDatasetError

        session = self.data_model.session
        if session.n_timepoints <= 1:
            self._show_status(
                "Tracking needs a time-lapse dataset (filenames with _tN tokens)."
            )
            return
        seg_name = session.active_segmentation
        if not seg_name:
            self._show_status("Select a segmentation to track first.")
            return

        self._show_status(
            f"Tracking cells across {session.n_timepoints} timepoints..."
        )
        repo = Hdf5DatasetRepository()
        uc = TrackCells(repo, session, LaptrackTracker())
        try:
            result = uc.execute(seg_name)
        except (ValueError, NoDatasetError) as e:
            self._show_status(f"Tracking failed: {e}")
            return

        viewer_win = self._launcher._windows.get("viewer") if self._launcher else None
        if viewer_win is not None:
            # Creator step: add the new tracked layer to the viewer. The raw
            # segmentation layer is preserved (both remain selectable).
            viewer_win.add_labels(result.tracked_labels, name=result.seg_name)
            # Overlay tracks/lineage if measurements for this resource exist.
            try:
                lineage = repo.read_tracks(session.dataset, result.seg_name)
            except KeyError:
                lineage = None
            measurements = self.data_model.df
            if (
                measurements is not None
                and not measurements.empty
                and "track_id" in measurements.columns
            ):
                viewer_win.show_tracks_from_measurements(
                    measurements, lineage_df=lineage, name=f"{result.seg_name}_tracks"
                )

        self._show_status(
            f"Tracked: {result.n_tracks} tracks, {result.n_divisions} divisions. "
            f"Saved as '{result.seg_name}'."
        )

    # ── Load ROIs ─────────────────────────────────────────────

    def _get_image_shape(self):
        """Get the spatial shape of the current dataset's image data."""
        # Prefer Session → viewer layer for the active channel
        channel_name = self.data_model.session.active_channel
        if self._launcher is None:
            return None
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            return None
        if channel_name:
            for layer in viewer_win.viewer.layers:
                if layer.__class__.__name__ == "Image" and layer.name == channel_name:
                    return layer.data.shape[-2:]
        # Fallback: first image layer
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
        # Persist immediately so the labels/manual entry exists in HDF5
        # even if the user closes the dataset before painting anything.
        new_layer = self._get_active_labels_layer()
        if new_layer is not None and new_layer.name == "manual":
            if self._persist_labels_layer(new_layer):
                # Refresh the session's segmentation inventory so
                # SessionWindow (and any other SEGMENTATION_LIST_CHANGED
                # subscriber) learns about the new "manual" entry without
                # requiring a dataset reload. Mirrors the canonical
                # Creator pattern at
                # ``application/use_cases/segment_cells.py:209``.
                store = (
                    getattr(self._launcher, "_current_store", None)
                    if self._launcher
                    else None
                )
                if store is not None:
                    mask_set = set(store.list_masks())
                    seg_names = [
                        n for n in store.list_labels() if n not in mask_set
                    ]
                    self.data_model.session.refresh_resource_lists(
                        segmentation_names=seg_names
                    )
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

    def _current_timepoint(self) -> int:
        """The displayed timepoint (napari dims axis 0), or 0 if not time-lapse."""
        if self._launcher is None:
            return 0
        viewer_win = self._launcher._windows.get("viewer")
        if viewer_win is None or viewer_win.viewer is None:
            return 0
        dims = viewer_win.viewer.dims
        if dims.ndim >= 3 and len(dims.current_step) > 0:
            return int(dims.current_step[0])
        return 0

    def _active_seg_is_tracked(self) -> bool:
        """True when the active segmentation has a stored lineage table.

        A tracked segmentation's label value IS the track id, so relabeling
        or compacting ids would break cross-timepoint identity.
        """
        name = self.data_model.active_segmentation
        if not name:
            return False
        store = (
            getattr(self._launcher, "_current_store", None)
            if self._launcher is not None
            else None
        )
        if store is None:
            return False
        try:
            return name in store.list_tracks()
        except Exception:  # noqa: BLE001 — advisory check, never fatal
            return False

    def _sync_relabel_enabled(self) -> None:
        """Disable relabel-sequential when the active segmentation is tracked."""
        btn = getattr(self, "_btn_relabel", None)
        if btn is None:
            return
        tracked = self._active_seg_is_tracked()
        btn.setEnabled(not tracked)
        if tracked:
            btn.setToolTip(
                "Disabled for a tracked segmentation: relabeling would scramble "
                "the track IDs that link cells across timepoints."
            )

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
        # For a time-lapse (T, H, W) labels layer, delete only within the
        # displayed timepoint — the same label id is a different physical cell
        # in other frames (raw seg) or a cell that legitimately persists
        # (tracked seg), so it must not be wiped across the whole stack.
        if data.ndim == 3:
            t = max(0, min(self._current_timepoint(), data.shape[0] - 1))
            frame = data[t]
            count = int(np.sum(frame == selected_id))
            frame[frame == selected_id] = 0
            scope = f" at timepoint {t}"
        else:
            count = int(np.sum(data == selected_id))
            data[data == selected_id] = 0
            scope = ""
        labels_layer.data = data
        labels_layer.selected_label = 0
        labels_layer.refresh()
        self._persist_labels_layer(labels_layer)
        self._show_status(
            f"Deleted label {selected_id} ({count} pixels removed{scope})"
        )

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
        if self._active_seg_is_tracked():
            self._show_status(
                "Relabel is disabled for a tracked segmentation — it would "
                "scramble the track IDs."
            )
            return
        from percell4.domain.segmentation.postprocess import relabel_sequential
        data = np.asarray(labels_layer.data, dtype=np.int32).copy()
        # Frame-scope on a time-lapse stack: renumber only the displayed
        # timepoint so the other frames' labels are untouched.
        if data.ndim == 3:
            t = max(0, min(self._current_timepoint(), data.shape[0] - 1))
            data[t] = relabel_sequential(data[t])
            n_cells = int(data[t].max())
            scope = f" at timepoint {t}"
        else:
            data = relabel_sequential(data)
            n_cells = int(data.max())
            scope = ""
        labels_layer.data = data
        labels_layer.refresh()
        self._persist_labels_layer(labels_layer)
        self._show_status(f"Relabeled to {n_cells} sequential cells{scope}")

    # ── Label Cleanup ─────────────────────────────────────────

    def _run_cleanup_filters(
        self, labels: np.ndarray,
    ) -> tuple[np.ndarray, int, int, int]:
        """Apply edge and area filters. Returns (filtered, edge_removed, small_removed, total)."""
        from percell4.domain.segmentation.postprocess import filter_edge_cells, filter_small_cells

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

        full = np.asarray(labels_layer.data, dtype=np.int32)
        # Frame-scope on a time-lapse stack: preview removals only in the
        # displayed timepoint.
        t = None
        if full.ndim == 3:
            t = max(0, min(self._current_timepoint(), full.shape[0] - 1))
            labels = full[t]
        else:
            labels = full
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
        frame_highlight = np.where(removed_mask, 1, 0).astype(np.int32)
        # Build an overlay matching the layer's rank so it tracks the slider;
        # only the displayed frame is marked for a time-lapse stack.
        if t is not None:
            highlight = np.zeros(full.shape, dtype=np.int32)
            highlight[t] = frame_highlight
        else:
            highlight = frame_highlight
        viewer_win.viewer.add_labels(
            highlight,
            name="_cleanup_preview",
            opacity=vp.LABELS_OVERLAY_DEFAULT_OPACITY,
            blending=vp.LABELS_OVERLAY_DEFAULT_BLENDING,
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

        from percell4.domain.segmentation.postprocess import relabel_sequential

        full = np.asarray(labels_layer.data, dtype=np.int32).copy()
        tracked = self._active_seg_is_tracked()
        # Frame-scope on a time-lapse stack: clean up only the displayed frame.
        t = None
        if full.ndim == 3:
            t = max(0, min(self._current_timepoint(), full.shape[0] - 1))
            frame = full[t]
        else:
            frame = full
        filtered, edge_removed, small_removed, total_removed = (
            self._run_cleanup_filters(frame)
        )

        # Compacting ids would break a tracked segmentation's track IDs, so
        # skip the relabel step there — just remove the filtered cells.
        if not tracked:
            filtered = relabel_sequential(filtered)
        n_remaining = int((np.unique(filtered) > 0).sum())

        if t is not None:
            full[t] = filtered
            out_data = full
        else:
            out_data = filtered
        labels_layer.data = out_data
        labels_layer.refresh()
        self._persist_labels_layer(labels_layer)

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
