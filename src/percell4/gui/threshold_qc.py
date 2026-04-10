"""Interactive threshold QC controller for grouped thresholding.

NOT cell segmentation — this creates binary masks via intensity thresholding,
with cells grouped by expression level for polyclonal data.

Manages two phases:
1. Group QC visualization (colored cells + histogram dock widget)
2. Per-group interactive thresholding loop with accept/skip/back
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray
from qtpy.QtCore import QObject, QTimer, Qt
from qtpy.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    import pandas as pd

    from percell4.gui.viewer import ViewerWindow
    from percell4.measure.grouper import GroupingResult
    from percell4.model import CellDataModel
    from percell4.store import DatasetStore

from percell4.gui import theme

logger = logging.getLogger(__name__)

# Temporary layer names — underscore prefix avoids dropdown pollution
_LAYER_GROUP_PREVIEW = "_group_preview"
_LAYER_GROUP_IMAGE = "_group_image"
_LAYER_THRESHOLD_PREVIEW = "_group_threshold_preview"
_LAYER_ROI = "_group_roi"

# Group colors (categorical, up to 10 groups)
_GROUP_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


class GroupStatus(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    SKIPPED = "skipped"


@dataclass
class GroupState:
    group_id: int
    cell_labels: NDArray[np.int32]
    status: GroupStatus = GroupStatus.PENDING
    mask: NDArray | None = None
    threshold_value: float | None = None


class ThresholdQCController(QObject):
    """Controller for the grouped thresholding QC workflow.

    Orchestrates group preview visualization and per-group thresholding.
    """

    def __init__(
        self,
        viewer_win: ViewerWindow,
        data_model: CellDataModel,
        store: DatasetStore | None,
        grouping_result: GroupingResult,
        channel_image: NDArray,
        seg_labels: NDArray[np.int32],
        channel: str,
        metric: str,
        sigma: float,
        mask_name: str,
        on_complete: Callable[[bool, str], None] | None = None,
        write_measurements_to_store: bool = True,
    ) -> None:
        super().__init__()
        self._viewer_win = viewer_win
        self._data_model = data_model
        self._store = store
        self._result = grouping_result
        self._channel_image = channel_image
        self._seg_labels = seg_labels
        self._channel = channel
        self._metric = metric
        self._sigma = sigma
        self._mask_name = mask_name
        self._on_complete = on_complete
        # When False, /masks/<name> and /groups/<name> are still written,
        # but /measurements is left alone. Used by the batch workflow runner,
        # which owns measurement persistence separately.
        self._write_measurements_to_store = write_measurements_to_store

        # Build group states
        self._groups: list[GroupState] = []
        for gid in range(1, grouping_result.n_groups + 1):
            mask = grouping_result.group_assignments == gid
            cell_labels = grouping_result.group_assignments.index[mask].values.astype(np.int32)
            self._groups.append(GroupState(group_id=gid, cell_labels=cell_labels))

        self._current_index = 0

        # Pre-compute lookup table for fast group mask generation
        max_label = int(seg_labels.max()) + 1
        self._label_to_group = np.zeros(max_label, dtype=np.int32)
        for gs in self._groups:
            for cl in gs.cell_labels:
                if cl < max_label:
                    self._label_to_group[cl] = gs.group_id

        # Pre-compute smoothed image if needed
        if sigma > 0:
            from percell4.measure.thresholding import apply_gaussian_smoothing
            self._smoothed_image = apply_gaussian_smoothing(
                channel_image.astype(np.float32), sigma
            )
        else:
            self._smoothed_image = channel_image.astype(np.float32)

        # Reusable buffer for group images
        self._group_image_buffer = np.zeros_like(channel_image, dtype=np.float32)

        # Windows (for cleanup)
        self._preview_window = None
        self._qc_window = None
        self._hidden_layers: dict[str, bool] = {}  # layer_name → original visibility
        self._preview_pending = False

    # ── Phase 1: Group Preview ──

    def start(self) -> None:
        """Show group QC visualization."""
        viewer = self._viewer_win.viewer
        if viewer is None:
            self._finish(False, "Viewer not available")
            return

        self._show_group_preview()

    def _show_group_preview(self) -> None:
        """Show colored cells + histogram for group validation."""
        from napari.utils.colormaps import DirectLabelColormap

        viewer = self._viewer_win.viewer

        # Build a label array where each cell gets its group number
        group_index = self._label_to_group[self._seg_labels]

        # Build colormap: each group gets a distinct color
        color_dict = {0: "transparent", None: "transparent"}
        for i, gs in enumerate(self._groups):
            color_dict[gs.group_id] = _GROUP_COLORS[i % len(_GROUP_COLORS)]

        cmap = DirectLabelColormap(color_dict=color_dict)

        # Remove old preview if exists
        self._remove_layer(_LAYER_GROUP_PREVIEW)

        viewer.add_labels(
            group_index,
            name=_LAYER_GROUP_PREVIEW,
            opacity=0.5,
            blending="translucent",
            colormap=cmap,
            metadata={"percell_type": "group_preview"},
        )

        # Build preview dock widget
        self._build_preview_dock()
        self._viewer_win.show()
        # Re-raise preview after viewer.show() so it isn't hidden behind the viewer
        if self._preview_window is not None:
            self._preview_window.raise_()
            self._preview_window.activateWindow()

    def _build_preview_dock(self) -> None:
        """Build the group preview as a separate window with histogram and buttons."""
        from qtpy.QtWidgets import QMainWindow

        win = QMainWindow()
        win.setWindowTitle("Group Preview")
        win.setMinimumSize(500, 450)
        win.setStyleSheet(f"background-color: {theme.BACKGROUND}; color: {theme.TEXT_BRIGHT};")

        widget = QWidget()
        layout = QVBoxLayout(widget)

        title = QLabel("Group Preview")
        title.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {theme.TEXT_BRIGHT};")
        layout.addWidget(title)

        # Interactive histogram — clicking a bar selects cells in that bin
        try:
            import pyqtgraph as pg

            plot = pg.PlotWidget()
            plot.setBackground(theme.BACKGROUND)
            plot.getAxis("bottom").enableAutoSIPrefix(False)
            plot.getAxis("left").enableAutoSIPrefix(False)
            plot.setLabel("bottom", f"{self._metric}")
            plot.setLabel("left", "Count")

            col_name = f"{self._channel}_{self._metric}"
            df = self._data_model.df

            n_bins = 50

            if df is not None and col_name in df.columns:
                # Use only grouped cells
                grouped_labels = set()
                for gs in self._groups:
                    grouped_labels.update(int(cl) for cl in gs.cell_labels)
                grouped_df = df[df["label"].isin(list(grouped_labels))].copy()
                vals_all = grouped_df[col_name].dropna()

                if len(vals_all) > 0:
                    bin_edges = np.linspace(vals_all.min(), vals_all.max(), n_bins + 1)
                    bar_width = (bin_edges[1] - bin_edges[0]) * 0.95

                    # Assign each cell to a bin for histogram counts
                    bin_indices = np.digitize(vals_all.values, bin_edges) - 1
                    bin_indices = np.clip(bin_indices, 0, n_bins - 1)
                    grouped_df = grouped_df.loc[vals_all.index]
                    grouped_df["_bin"] = bin_indices

                    # Plot stacked bars per group (bottom-up)
                    bar_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
                    cumulative = np.zeros(n_bins, dtype=float)

                    for i, gs in enumerate(self._groups):
                        group_cells = set(int(cl) for cl in gs.cell_labels)
                        group_df = grouped_df[grouped_df["label"].isin(group_cells)]
                        counts = np.zeros(n_bins, dtype=float)
                        for _, row in group_df.iterrows():
                            counts[int(row["_bin"])] += 1

                        color = _GROUP_COLORS[i % len(_GROUP_COLORS)]
                        bar = pg.BarGraphItem(
                            x=bar_centers, height=counts, width=bar_width,
                            y0=cumulative,
                            brush=pg.mkBrush(color + "80"),
                            pen=pg.mkPen(color, width=1),
                        )
                        plot.addItem(bar)
                        cumulative += counts

            plot.setMinimumHeight(250)
            layout.addWidget(plot)
        except ImportError:
            layout.addWidget(QLabel("(pyqtgraph not available for histogram)"))

        # Group summary — each group is a clickable button that selects its cells
        summary = QLabel(f"Groups found: {len(self._groups)}")
        summary.setStyleSheet(f"color: {theme.TEXT_BRIGHT}; font-weight: bold;")
        layout.addWidget(summary)

        for i, gs in enumerate(self._groups):
            mean_val = self._result.group_means[i]
            color = _GROUP_COLORS[i % len(_GROUP_COLORS)]
            btn = QPushButton(
                f"Group {gs.group_id}: {len(gs.cell_labels)} cells (mean: {mean_val:.1f})"
            )
            btn.setStyleSheet(
                f"QPushButton {{ color: {color}; background: transparent;"
                f" border: 1px solid {color}; border-radius: 3px;"
                f" padding: 4px; text-align: left; }}"
                f" QPushButton:hover {{ background: {color}30; }}"
            )
            cell_labels = [int(cl) for cl in gs.cell_labels]
            btn.clicked.connect(lambda _, labels=cell_labels: self._on_group_select(labels))
            layout.addWidget(btn)

        # Buttons
        btn_row = QHBoxLayout()
        proceed_btn = QPushButton("Proceed to Thresholding")
        proceed_btn.setStyleSheet(
            f"background-color: {theme.ACTION_GREEN}; color: white; padding: 6px; font-weight: bold;"
        )
        proceed_btn.clicked.connect(self._on_proceed)
        btn_row.addWidget(proceed_btn)

        regroup_btn = QPushButton("Re-group")
        regroup_btn.clicked.connect(self._on_regroup)
        btn_row.addWidget(regroup_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)

        win.setCentralWidget(widget)
        win.show()
        win.raise_()
        win.activateWindow()
        self._preview_window = win

    def _on_proceed(self) -> None:
        """Remove preview and start per-group thresholding."""
        self._data_model.set_selection([])  # clear any histogram selections
        self._close_preview_window()
        self._remove_layer(_LAYER_GROUP_PREVIEW)
        self._current_index = 0
        self._show_group_qc()

    def _on_regroup(self) -> None:
        """Remove preview and return control to the panel."""
        self._cleanup_all()
        self._finish(False, "Re-group requested — adjust parameters and run again")

    def _on_group_select(self, labels: list[int]) -> None:
        """Group button clicked — select all cells in that group."""
        self._data_model.set_selection(labels)

    def _on_cancel(self) -> None:
        """Discard everything."""
        self._cleanup_all()
        self._finish(False, "Grouped thresholding cancelled")

    # ── Phase 2: Per-Group Thresholding ──

    def _show_group_qc(self) -> None:
        """Set up the QC view for the current group."""
        if self._current_index >= len(self._groups):
            self._finalize()
            return

        viewer = self._viewer_win.viewer
        if viewer is None:
            self._finish(False, "Viewer closed")
            return

        # Hide all non-QC layers so only the group image, preview, and ROI are visible
        qc_layer_names = {_LAYER_GROUP_IMAGE, _LAYER_THRESHOLD_PREVIEW, _LAYER_ROI}
        if not self._hidden_layers:
            # First group — record original visibility and hide
            for layer in viewer.layers:
                if layer.name not in qc_layer_names:
                    self._hidden_layers[layer.name] = layer.visible
                    layer.visible = False

        gs = self._groups[self._current_index]
        group_id = gs.group_id

        # Build group cell mask using lookup table
        group_cell_mask = self._label_to_group[self._seg_labels] == group_id

        # Build group image using reusable buffer
        self._group_image_buffer[:] = 0
        np.copyto(self._group_image_buffer, self._smoothed_image, where=group_cell_mask)
        self._current_group_mask = group_cell_mask

        # Add/update group image layer
        self._remove_layer(_LAYER_GROUP_IMAGE)
        viewer.add_image(
            self._group_image_buffer.copy(),  # copy since buffer is reused
            name=_LAYER_GROUP_IMAGE,
            colormap="gray",
            blending="additive",
        )

        # Compute initial threshold
        from percell4.measure.thresholding import THRESHOLD_METHODS
        pixels = self._group_image_buffer[group_cell_mask]
        if len(pixels) > 0 and pixels.max() > 0:
            _, value = THRESHOLD_METHODS["otsu"](pixels)
        else:
            value = 0.0

        # Create preview mask
        preview = np.where(
            group_cell_mask & (self._group_image_buffer > value), 1, 0
        ).astype(np.uint8)

        self._remove_layer(_LAYER_THRESHOLD_PREVIEW)
        from napari.utils.colormaps import DirectLabelColormap
        yellow_cmap = DirectLabelColormap(
            color_dict={0: "transparent", 1: "yellow", None: "transparent"},
        )
        viewer.add_labels(
            preview,
            name=_LAYER_THRESHOLD_PREVIEW,
            opacity=0.5,
            blending="translucent",
            colormap=yellow_cmap,
        )

        # Add shapes layer for ROI
        self._remove_layer(_LAYER_ROI)
        viewer.add_shapes(
            [],
            shape_type="rectangle",
            name=_LAYER_ROI,
            edge_color="yellow",
            edge_width=2,
            face_color=[1, 1, 0, 0.1],
            blending="additive",
        )

        # Wire ROI changes — listen to mode changes so we update only when
        # the user finishes drawing (exits add_rectangle mode back to pan_zoom
        # or direct), not during the drag.  Also listen to data changes but
        # only act when the mode is not an "add" mode.
        self._roi_layer = None
        self._last_roi_count = 0
        for layer in viewer.layers:
            if layer.name == _LAYER_ROI:
                self._roi_layer = layer
                layer.events.data.connect(self._on_roi_data_changed)
                break

        # Build/update QC window
        self._build_qc_dock(value)

        self._current_threshold = value
        self._current_method = "otsu"

    def _build_qc_dock(self, initial_value: float) -> None:
        """Build or rebuild the per-group QC as a separate window."""
        from qtpy.QtWidgets import QMainWindow

        self._remove_qc_dock()

        gs = self._groups[self._current_index]

        win = QMainWindow()
        win.setWindowTitle(f"Threshold QC — Group {gs.group_id} of {len(self._groups)}")
        win.setMinimumSize(350, 300)
        win.setStyleSheet(f"background-color: {theme.BACKGROUND}; color: {theme.TEXT_BRIGHT};")

        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Title
        title = QLabel(f"Group {gs.group_id} of {len(self._groups)}")
        title.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {theme.TEXT_BRIGHT};")
        layout.addWidget(title)

        # Method selector
        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Method:"))
        self._method_combo = QComboBox()
        self._method_combo.addItems(["Otsu", "Triangle", "Li", "Adaptive"])
        self._method_combo.currentTextChanged.connect(self._on_method_changed)
        method_row.addWidget(self._method_combo)
        layout.addLayout(method_row)

        # Stats
        self._thresh_label = QLabel(f"Threshold: {initial_value:.1f}")
        self._thresh_label.setStyleSheet(f"color: {theme.ACCENT};")
        layout.addWidget(self._thresh_label)

        self._pixels_label = QLabel("Positive pixels: —")
        self._pixels_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(self._pixels_label)

        self._fraction_label = QLabel("Positive fraction: —")
        self._fraction_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(self._fraction_label)

        self._update_stats_display(initial_value)

        # Buttons
        btn_layout = QVBoxLayout()

        row1 = QHBoxLayout()
        accept_btn = QPushButton("Accept")
        accept_btn.setStyleSheet(
            f"background-color: {theme.ACTION_GREEN}; color: white; padding: 6px; font-weight: bold;"
        )
        accept_btn.clicked.connect(self._on_accept)
        row1.addWidget(accept_btn)

        skip_btn = QPushButton("Skip")
        skip_btn.clicked.connect(self._on_skip)
        row1.addWidget(skip_btn)
        btn_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._on_back)
        self._back_btn.setEnabled(self._current_index > 0)
        row2.addWidget(self._back_btn)

        skip_remaining_btn = QPushButton("Skip Remaining")
        skip_remaining_btn.clicked.connect(self._on_skip_remaining)
        row2.addWidget(skip_remaining_btn)
        btn_layout.addLayout(row2)

        layout.addLayout(btn_layout)
        layout.addStretch()

        win.setCentralWidget(widget)
        win.show()
        win.raise_()
        win.activateWindow()
        self._qc_window = win

    # ── Live Preview ──

    def _on_roi_data_changed(self, event=None) -> None:
        """ROI data changed — only update if a shape was completed (not mid-draw).

        During drawing, napari fires data events for every mouse move.
        We detect completion by checking if the shape count increased
        while the mode is still an add mode, then delay the update
        slightly so napari finishes its internal state first.
        """
        if self._roi_layer is None:
            return
        current_count = len(self._roi_layer.data)
        mode = str(getattr(self._roi_layer, "mode", ""))
        # Only update when:
        # - The shape count changed (new shape completed or shape deleted)
        # - OR the mode is not an add mode (user is editing/moving existing shapes)
        is_adding = "add" in mode
        count_changed = current_count != self._last_roi_count
        if is_adding and not count_changed:
            return  # mid-draw, ignore
        self._last_roi_count = current_count
        if not self._preview_pending:
            self._preview_pending = True
            QTimer.singleShot(50, self._update_preview)

    def _on_method_changed(self, text: str) -> None:
        """Threshold method changed — recompute preview."""
        self._current_method = text.lower()
        if not self._preview_pending:
            self._preview_pending = True
            QTimer.singleShot(0, self._update_preview)

    def _update_preview(self) -> None:
        """Recompute threshold and update preview layer."""
        self._preview_pending = False

        viewer = self._viewer_win.viewer
        if viewer is None:
            return

        group_cell_mask = self._current_group_mask
        method = self._current_method

        # Extract pixels within ROI (if drawn) and group cell mask
        pixels_mask = group_cell_mask.copy()
        roi_drawn = False

        for layer in viewer.layers:
            if layer.name == _LAYER_ROI and len(layer.data) > 0:
                # Build ROI mask from all rectangles
                roi_mask = np.zeros_like(group_cell_mask, dtype=bool)
                for shape_data in layer.data:
                    coords = np.array(shape_data)
                    y_min = max(0, int(coords[:, 0].min()))
                    y_max = min(group_cell_mask.shape[0], int(coords[:, 0].max()))
                    x_min = max(0, int(coords[:, 1].min()))
                    x_max = min(group_cell_mask.shape[1], int(coords[:, 1].max()))
                    roi_mask[y_min:y_max, x_min:x_max] = True
                pixels_mask = group_cell_mask & roi_mask
                roi_drawn = True
                break

        source_pixels = self._group_image_buffer[pixels_mask]
        if len(source_pixels) == 0 or source_pixels.max() == 0:
            return

        # Compute threshold
        from percell4.measure.thresholding import THRESHOLD_METHODS
        if method not in THRESHOLD_METHODS:
            return

        if method == "adaptive":
            # For adaptive, restrict to bounding box of group cells
            rows, cols = np.where(group_cell_mask)
            if len(rows) == 0:
                return
            y_min, y_max = rows.min(), rows.max() + 1
            x_min, x_max = cols.min(), cols.max() + 1
            crop = self._group_image_buffer[y_min:y_max, x_min:x_max]
            mask_crop, value = THRESHOLD_METHODS["adaptive"](crop)
            # Build full preview from crop
            preview = np.zeros_like(group_cell_mask, dtype=np.uint8)
            preview[y_min:y_max, x_min:x_max] = mask_crop
            preview[~group_cell_mask] = 0
        else:
            _, value = THRESHOLD_METHODS[method](source_pixels)
            preview = np.where(
                group_cell_mask & (self._group_image_buffer > value), 1, 0
            ).astype(np.uint8)

        self._current_threshold = value

        # Update preview layer
        for layer in viewer.layers:
            if layer.name == _LAYER_THRESHOLD_PREVIEW:
                layer.data = preview
                layer.refresh()
                break

        self._update_stats_display(value)

    def _update_stats_display(self, value: float) -> None:
        """Update the threshold statistics labels."""
        if not hasattr(self, "_thresh_label"):
            return
        group_cell_mask = self._current_group_mask
        preview = (
            group_cell_mask & (self._group_image_buffer > value)
        )
        n_pos = int(preview.sum())
        n_total = int(group_cell_mask.sum())
        fraction = n_pos / n_total if n_total > 0 else 0.0

        self._thresh_label.setText(f"Threshold: {value:.1f}")
        self._pixels_label.setText(f"Positive pixels: {n_pos:,}")
        self._fraction_label.setText(f"Positive fraction: {fraction:.3f}")

    # ── Button Actions ──

    def _on_accept(self) -> None:
        gs = self._groups[self._current_index]
        group_cell_mask = self._current_group_mask
        value = self._current_threshold

        if self._current_method == "adaptive":
            # Re-derive mask from adaptive
            from percell4.measure.thresholding import THRESHOLD_METHODS
            rows, cols = np.where(group_cell_mask)
            if len(rows) > 0:
                y_min, y_max = rows.min(), rows.max() + 1
                x_min, x_max = cols.min(), cols.max() + 1
                crop = self._group_image_buffer[y_min:y_max, x_min:x_max]
                mask_crop, _ = THRESHOLD_METHODS["adaptive"](crop)
                mask = np.zeros_like(group_cell_mask, dtype=np.uint8)
                mask[y_min:y_max, x_min:x_max] = mask_crop
                mask[~group_cell_mask] = 0
            else:
                mask = np.zeros_like(group_cell_mask, dtype=np.uint8)
        else:
            mask = np.where(
                group_cell_mask & (self._group_image_buffer > value), 1, 0
            ).astype(np.uint8)

        gs.mask = mask
        gs.threshold_value = value
        gs.status = GroupStatus.ACCEPTED

        self._advance()

    def _on_skip(self) -> None:
        gs = self._groups[self._current_index]
        gs.mask = np.zeros(self._seg_labels.shape, dtype=np.uint8)
        gs.status = GroupStatus.SKIPPED
        self._advance()

    def _on_back(self) -> None:
        if self._current_index > 0:
            self._current_index -= 1
            self._groups[self._current_index].status = GroupStatus.PENDING
            self._groups[self._current_index].mask = None
            self._show_group_qc()

    def _on_skip_remaining(self) -> None:
        for i in range(self._current_index, len(self._groups)):
            self._groups[i].mask = np.zeros(self._seg_labels.shape, dtype=np.uint8)
            self._groups[i].status = GroupStatus.SKIPPED
        self._finalize()

    def _advance(self) -> None:
        self._current_index += 1
        if self._current_index >= len(self._groups):
            self._finalize()
        else:
            self._show_group_qc()

    # ── Finalization ──

    def _finalize(self) -> None:
        """Combine masks and store results."""
        self._cleanup_all()

        # Combine masks (in-place union)
        combined = np.zeros(self._seg_labels.shape, dtype=np.uint8)
        for gs in self._groups:
            if gs.mask is not None:
                np.maximum(combined, gs.mask, out=combined)

        # Save mask to HDF5
        if self._store is not None:
            self._store.write_mask(self._mask_name, combined)

            # Save group mapping for persistence across re-measurement
            import pandas as pd
            col_name = f"group_{self._channel}_{self._metric}"
            group_df = self._result.group_assignments.reset_index()
            group_df.columns = ["label", col_name]
            self._store.write_dataframe(f"/groups/{self._mask_name}", group_df)

        # Add group column to DataFrame
        col_name = f"group_{self._channel}_{self._metric}"
        df = self._data_model.df
        if df is not None:
            df = df.assign(
                **{col_name: df["label"].map(self._result.group_assignments)}
            )
            self._data_model.set_measurements(df)

            # Persist updated DataFrame (skipped by the batch workflow runner,
            # which owns measurement persistence in its own run folder).
            if self._store is not None and self._write_measurements_to_store:
                self._store.write_dataframe("/measurements", df)

        # Show final mask in viewer
        viewer = self._viewer_win.viewer
        if viewer is not None:
            self._viewer_win.add_mask(combined, name=self._mask_name)
            self._data_model.set_active_mask(self._mask_name)

        n_accepted = sum(1 for gs in self._groups if gs.status == GroupStatus.ACCEPTED)
        n_skipped = sum(1 for gs in self._groups if gs.status == GroupStatus.SKIPPED)
        msg = (
            f"Grouped thresholding complete: {n_accepted} accepted, "
            f"{n_skipped} skipped. Mask saved as '{self._mask_name}'."
        )
        self._finish(True, msg)

    # ── Cleanup ──

    def _cleanup_all(self) -> None:
        """Remove all temporary layers and windows, restore hidden layers."""
        self._close_preview_window()
        self._remove_qc_dock()
        for name in (_LAYER_GROUP_PREVIEW, _LAYER_GROUP_IMAGE,
                     _LAYER_THRESHOLD_PREVIEW, _LAYER_ROI):
            self._remove_layer(name)

        # Restore original visibility of layers hidden during QC
        viewer = self._viewer_win.viewer
        if viewer is not None and self._hidden_layers:
            for layer in viewer.layers:
                if layer.name in self._hidden_layers:
                    layer.visible = self._hidden_layers[layer.name]
        self._hidden_layers = {}

    def _remove_layer(self, name: str) -> None:
        viewer = self._viewer_win.viewer
        if viewer is None:
            return
        for layer in list(viewer.layers):
            if layer.name == name:
                try:
                    viewer.layers.remove(layer)
                except Exception:
                    pass

    def _close_preview_window(self) -> None:
        if hasattr(self, "_preview_window") and self._preview_window is not None:
            try:
                self._preview_window.close()
            except Exception:
                pass
            self._preview_window = None

    def _remove_qc_dock(self) -> None:
        if hasattr(self, "_qc_window") and self._qc_window is not None:
            try:
                self._qc_window.close()
            except Exception:
                pass
            self._qc_window = None

    def _finish(self, success: bool, msg: str) -> None:
        logger.info(msg)
        if self._on_complete is not None:
            self._on_complete(success, msg)
