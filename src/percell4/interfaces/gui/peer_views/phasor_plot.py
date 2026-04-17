"""Phasor plot window — 2D histogram density of FLIM phasor coordinates.

Supports multiple named, colored ROI ellipses. Each ROI represents a
distinct lifetime population. All visible ROIs combine into a single
integer-labeled mask for downstream measurement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import QRectF, QSettings, QTimer, Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from percell4.application.session import Event, Session

COLOR_CYCLE: Final[tuple[str, ...]] = (
    "#3498db", "#e74c3c", "#2ecc71", "#f39c12",
    "#9b59b6", "#1abc9c", "#e67e22", "#34495e",
)


@dataclass
class PhasorROI:
    """Single phasor ROI definition."""

    name: str
    center: tuple[float, float]
    radii: tuple[float, float]
    angle_deg: float
    label: int
    color: str
    visible: bool = True

    @classmethod
    def from_dict(cls, d: dict, label: int, default_color: str) -> PhasorROI:
        """Create from JSON dict with validation."""
        try:
            center = tuple(float(x) for x in d["center"])
            radii = tuple(float(x) for x in d["radii"])
            if len(center) != 2 or len(radii) != 2:
                raise ValueError("center and radii must be 2-element sequences")
            return cls(
                name=str(d["name"]),
                center=center,
                radii=radii,
                angle_deg=float(d.get("angle_deg", 0)),
                label=label,
                color=str(d.get("color", default_color)),
            )
        except (KeyError, TypeError) as e:
            raise ValueError(f"Invalid ROI data: {e}") from e

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "name": self.name,
            "center": list(self.center),
            "radii": list(self.radii),
            "angle_deg": self.angle_deg,
            "color": self.color,
        }


@dataclass
class _ROIWidget:
    """GUI objects for one phasor ROI."""

    roi: pg.RectROI
    curve: pg.PlotCurveItem
    phasor_roi: PhasorROI
    cached_mask: np.ndarray | None = None


class PhasorPlotWindow(QMainWindow):
    """Phasor plot window with multi-ROI support.

    Multiple named, colored elliptical ROIs can be placed on the phasor
    histogram. All visible ROIs combine into a single labeled mask.

    Communication with the viewer is decoupled via signals:
    - preview_mask_ready: emitted when ROI preview mask needs display
    - mask_applied: emitted when user clicks "Apply Visible as Mask"
    The launcher connects these to mediate viewer + HDF5 access.
    """

    # (mask_ndarray, DirectLabelColormap) — for live ROI preview in viewer
    preview_mask_ready = Signal(object, object)
    # (mask_ndarray, color_dict, mask_name) — for final mask application
    mask_applied = Signal(object, object, str)

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self.setWindowTitle("PerCell4 — Phasor Plot")
        self.resize(850, 600)

        self._g_map: np.ndarray | None = None
        self._s_map: np.ndarray | None = None
        self._intensity: np.ndarray | None = None
        self._g_map_unfiltered: np.ndarray | None = None
        self._s_map_unfiltered: np.ndarray | None = None
        self._labels: np.ndarray | None = None
        self._labels_flat: np.ndarray | None = None
        self._total_valid_pixels: int = 0

        self._roi_widgets: list[_ROIWidget] = []
        self._selected_roi_index: int | None = None
        self._colormap_dirty: bool = True
        self._preview_colormap = None

        self._build_ui()
        self._restore_geometry()

        # Debounced preview + filter timers
        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(100)
        self._preview_timer.timeout.connect(self._update_preview)

        self._filter_timer = QTimer()
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._refresh_histogram)
        self._unsubs = [
            self._session.subscribe(Event.FILTER_CHANGED, self._on_filter_changed),
        ]

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # Left: plot + controls
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Controls row
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Harmonic:"))
        self._harmonic_combo = QComboBox()
        self._harmonic_combo.addItems(["1", "2", "3"])
        controls.addWidget(self._harmonic_combo)

        controls.addSpacing(16)
        self._filtered_check = QCheckBox("Filtered")
        # Checkbox styling inherited from global theme
        self._filtered_check.setEnabled(False)
        self._filtered_check.toggled.connect(self._on_filtered_toggled)
        controls.addWidget(self._filtered_check)
        controls.addStretch()
        left_layout.addLayout(controls)

        # Plot
        self._plot = pg.PlotWidget()
        from percell4.gui import theme

        self._plot.setBackground(theme.BACKGROUND)
        self._plot.setAspectLocked(False)
        self._plot.setLabel("bottom", "G")
        self._plot.setLabel("left", "S")
        self._plot.setXRange(-0.005, 1.005, padding=0)
        self._plot.setYRange(0, 0.7, padding=0)
        self._plot.disableAutoRange()
        self._plot.getAxis("bottom").enableAutoSIPrefix(False)
        self._plot.getAxis("left").enableAutoSIPrefix(False)
        left_layout.addWidget(self._plot)

        # Histogram image
        self._hist_item = None

        # Universal circle
        theta = np.linspace(0, np.pi, 200)
        semi_g = 0.5 + 0.5 * np.cos(theta)
        semi_s = 0.5 * np.sin(theta)
        self._semicircle = pg.PlotCurveItem(
            semi_g, semi_s, pen=pg.mkPen(theme.TEXT_LABEL, width=2),
        )
        self._semicircle.setZValue(10)
        self._plot.addItem(self._semicircle)

        main_layout.addWidget(left, stretch=3)

        # Right: ROI panel
        right = QWidget()
        right.setMaximumWidth(220)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)

        # Add/Remove buttons
        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add ROI")
        btn_add.clicked.connect(self._on_add_roi)
        btn_row.addWidget(btn_add)
        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self._on_remove_roi)
        btn_row.addWidget(btn_remove)
        right_layout.addLayout(btn_row)

        # ROI list
        self._roi_list = QListWidget()
        self._roi_list.currentRowChanged.connect(self._on_roi_list_selection)
        right_layout.addWidget(self._roi_list)

        # Selected ROI controls
        sel_group = QGroupBox("Selected ROI")
        sel_layout = QVBoxLayout(sel_group)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.editingFinished.connect(self._on_name_edited)
        name_row.addWidget(self._name_edit)
        sel_layout.addLayout(name_row)

        angle_row = QHBoxLayout()
        angle_row.addWidget(QLabel("Angle:"))
        self._angle_spin = QSpinBox()
        self._angle_spin.setRange(-90, 90)
        self._angle_spin.setValue(0)
        self._angle_spin.setSuffix("°")
        self._angle_spin.valueChanged.connect(self._on_angle_changed)
        angle_row.addWidget(self._angle_spin)
        sel_layout.addLayout(angle_row)

        self._vis_check = QCheckBox("Visible")
        self._vis_check.setChecked(True)
        self._vis_check.toggled.connect(self._on_visibility_toggled)
        sel_layout.addWidget(self._vis_check)

        right_layout.addWidget(sel_group)

        # Apply + Save/Load buttons
        btn_apply = QPushButton("Apply Visible as Mask")
        btn_apply.clicked.connect(self._on_apply_mask)
        right_layout.addWidget(btn_apply)

        io_row = QHBoxLayout()
        btn_save = QPushButton("Save ROIs...")
        btn_save.clicked.connect(self._on_save_rois)
        io_row.addWidget(btn_save)
        btn_load = QPushButton("Load ROIs...")
        btn_load.clicked.connect(self._on_load_rois)
        io_row.addWidget(btn_load)
        right_layout.addLayout(io_row)

        main_layout.addWidget(right, stretch=1)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("No phasor data loaded")

    # ── ROI Management ────────────────────────────────────────

    def get_visible_roi_names(self) -> dict[int, str]:
        """Public API: return {mask_label: roi_name} for all visible ROIs."""
        return {
            w.phasor_roi.label: w.phasor_roi.name
            for w in self._roi_widgets
            if w.phasor_roi.visible
        }

    def _on_add_roi(self) -> None:
        n = len(self._roi_widgets)
        if n >= 10:
            self._status.showMessage("Maximum 10 ROIs", 3000)
            return
        color = COLOR_CYCLE[n % len(COLOR_CYCLE)]
        phasor_roi = PhasorROI(
            name=f"ROI_{n + 1}",
            center=(0.35 + n * 0.05, 0.35),
            radii=(0.10, 0.08),
            angle_deg=0,
            label=n + 1,
            color=color,
        )
        self._create_roi_widget(phasor_roi)
        self._colormap_dirty = True
        self._refresh_roi_list()
        self._roi_list.setCurrentRow(len(self._roi_widgets) - 1)
        self._preview_timer.start()

    def _on_remove_roi(self) -> None:
        if self._selected_roi_index is None or not self._roi_widgets:
            return
        widget = self._roi_widgets.pop(self._selected_roi_index)
        self._plot.removeItem(widget.roi)
        self._plot.removeItem(widget.curve)
        for i, w in enumerate(self._roi_widgets):
            w.phasor_roi.label = i + 1
            w.cached_mask = None
        self._selected_roi_index = None
        self._colormap_dirty = True
        self._session.set_active_mask(None)
        self._refresh_roi_list()
        self._preview_timer.start()

    def _create_roi_widget(self, phasor_roi: PhasorROI) -> None:
        """Create pyqtgraph ROI + curve for a PhasorROI and add to the list."""
        cx, cy = phasor_roi.center
        rx, ry = phasor_roi.radii
        roi = pg.RectROI(
            [cx - rx, cy - ry], [2 * rx, 2 * ry],
            pen=pg.mkPen(phasor_roi.color, width=1, style=Qt.DashLine),
        )
        roi.setZValue(10)
        self._plot.addItem(roi)

        curve = pg.PlotCurveItem(pen=pg.mkPen(phasor_roi.color, width=2))
        curve.setZValue(10)
        self._plot.addItem(curve)

        widget = _ROIWidget(roi=roi, curve=curve, phasor_roi=phasor_roi)
        self._roi_widgets.append(widget)

        # Connect ROI movement — look up widget by identity, not index,
        # so removal/renumbering doesn't break surviving ROIs
        roi.sigRegionChangeFinished.connect(
            lambda _roi, _w=widget: self._on_roi_moved_widget(_w)
        )
        self._update_ellipse_curve_for(widget)

    def _refresh_roi_list(self) -> None:
        """Rebuild the QListWidget from current _roi_widgets."""
        self._roi_list.blockSignals(True)
        self._roi_list.clear()
        for w in self._roi_widgets:
            vis = "✓" if w.phasor_roi.visible else "✗"
            item = QListWidgetItem(f"[{vis}] {w.phasor_roi.name}")
            self._roi_list.addItem(item)
        self._roi_list.blockSignals(False)
        if self._selected_roi_index is not None and self._selected_roi_index < len(self._roi_widgets):
            self._roi_list.setCurrentRow(self._selected_roi_index)

    def _on_roi_list_selection(self, row: int) -> None:
        """User selected a different ROI in the list."""
        if row < 0 or row >= len(self._roi_widgets):
            self._selected_roi_index = None
            return
        self._selected_roi_index = row
        roi = self._roi_widgets[row].phasor_roi
        self._name_edit.blockSignals(True)
        self._name_edit.setText(roi.name)
        self._name_edit.blockSignals(False)
        self._angle_spin.blockSignals(True)
        self._angle_spin.setValue(int(roi.angle_deg))
        self._angle_spin.blockSignals(False)
        self._vis_check.blockSignals(True)
        self._vis_check.setChecked(roi.visible)
        self._vis_check.blockSignals(False)

    def _on_name_edited(self) -> None:
        if self._selected_roi_index is None:
            return
        new_name = self._name_edit.text().strip()
        if not new_name:
            return
        # Enforce unique names
        existing = {w.phasor_roi.name for i, w in enumerate(self._roi_widgets)
                     if i != self._selected_roi_index}
        if new_name in existing:
            new_name = f"{new_name}_2"
            self._name_edit.setText(new_name)
        self._roi_widgets[self._selected_roi_index].phasor_roi.name = new_name
        self._refresh_roi_list()

    def _on_angle_changed(self, value: int) -> None:
        if self._selected_roi_index is None:
            return
        widget = self._roi_widgets[self._selected_roi_index]
        widget.phasor_roi.angle_deg = float(value)
        self._update_ellipse_curve_for(widget)
        widget.cached_mask = None
        self._preview_timer.start()

    def _on_visibility_toggled(self, checked: bool) -> None:
        if self._selected_roi_index is None:
            return
        self._roi_widgets[self._selected_roi_index].phasor_roi.visible = checked
        self._colormap_dirty = True
        self._refresh_roi_list()
        self._preview_timer.start()

    def _on_roi_moved_widget(self, widget: _ROIWidget) -> None:
        """Recompute only the changed ROI's cached mask."""
        if widget not in self._roi_widgets:
            return  # widget was removed
        pos = widget.roi.pos()
        size = widget.roi.size()
        widget.phasor_roi.center = (
            pos.x() + abs(size.x()) / 2,
            pos.y() + abs(size.y()) / 2,
        )
        widget.phasor_roi.radii = (abs(size.x()) / 2, abs(size.y()) / 2)
        self._update_ellipse_curve_for(widget)
        widget.cached_mask = None
        self._preview_timer.start()

    # ── Ellipse drawing ───────────────────────────────────────

    def _update_ellipse_curve_for(self, widget: _ROIWidget) -> None:
        """Redraw the ellipse curve for a specific ROI widget."""
        roi = widget.phasor_roi
        cx, cy = roi.center
        rx, ry = roi.radii
        angle_rad = np.radians(roi.angle_deg)

        if rx < 1e-6 or ry < 1e-6:
            widget.curve.setData([], [])
            return

        theta = np.linspace(0, 2 * np.pi, 200)
        ex = rx * np.cos(theta)
        ey = ry * np.sin(theta)
        cos_a = np.cos(angle_rad)
        sin_a = np.sin(angle_rad)
        ex_rot = cx + ex * cos_a - ey * sin_a
        ey_rot = cy + ex * sin_a + ey * cos_a
        widget.curve.setData(ex_rot, ey_rot)

    # ── Combined mask ─────────────────────────────────────────

    def _get_active_gs_maps(self) -> tuple[np.ndarray, np.ndarray]:
        """Return filtered or unfiltered G/S maps based on checkbox."""
        use_filtered = self._filtered_check.isChecked()
        if not use_filtered and self._g_map_unfiltered is not None:
            return self._g_map_unfiltered, self._s_map_unfiltered
        return self._g_map, self._s_map

    def _compute_combined_mask(self) -> np.ndarray:
        """Combine all visible ROIs into a single labeled uint8 mask.

        Uses cached per-ROI boolean masks. Only uncached ROIs recomputed.
        When a cell filter is active, the mask is restricted to pixels
        belonging to filtered cells only.
        """
        from percell4.flim.phasor import phasor_roi_to_mask

        g, s = self._get_active_gs_maps()
        mask = np.zeros(g.shape, dtype=np.uint8)

        for widget in self._roi_widgets:
            if not widget.phasor_roi.visible:
                continue
            if widget.cached_mask is None:
                roi = widget.phasor_roi
                angle_rad = np.radians(roi.angle_deg)
                widget.cached_mask = phasor_roi_to_mask(
                    g, s, center=roi.center, radii=roi.radii,
                    angle_rad=angle_rad,
                )
            mask[widget.cached_mask] = widget.phasor_roi.label

        # Restrict mask to filtered cells when cell filter is active
        filtered_ids = self._session.filter_ids
        if filtered_ids is not None and self._labels is not None:
            cell_mask = np.isin(self._labels, list(filtered_ids))
            mask[~cell_mask] = 0

        return mask

    def _update_preview(self) -> None:
        """Compute combined mask and emit preview_mask_ready for the launcher."""
        if self._g_map is None or not self._roi_widgets:
            return

        mask = self._compute_combined_mask()

        # Build colormap only when dirty
        if self._colormap_dirty:
            from napari.utils.colormaps import DirectLabelColormap

            color_dict = {0: "transparent", None: "transparent"}
            for w in self._roi_widgets:
                if w.phasor_roi.visible:
                    color_dict[w.phasor_roi.label] = w.phasor_roi.color
            self._preview_colormap = DirectLabelColormap(color_dict=color_dict)
            self._colormap_dirty = False

        # Emit signal — launcher mediates viewer access
        self.preview_mask_ready.emit(mask, self._preview_colormap)

        # Status bar: pixel counts per ROI via bincount
        max_label = max((w.phasor_roi.label for w in self._roi_widgets
                         if w.phasor_roi.visible), default=0)
        if max_label > 0:
            counts = np.bincount(mask.ravel(), minlength=max_label + 1)
            total = self._total_valid_pixels or 1
            parts = []
            for w in self._roi_widgets:
                if w.phasor_roi.visible:
                    lbl = w.phasor_roi.label
                    pct = counts[lbl] / total * 100
                    parts.append(f"{w.phasor_roi.name}: {counts[lbl]:,} ({pct:.1f}%)")
            self._status.showMessage(" | ".join(parts))

    # ── Data ──────────────────────────────────────────────────

    def set_phasor_data(
        self,
        g_map: np.ndarray,
        s_map: np.ndarray,
        intensity: np.ndarray | None = None,
        g_unfiltered: np.ndarray | None = None,
        s_unfiltered: np.ndarray | None = None,
        labels: np.ndarray | None = None,
    ) -> None:
        """Set phasor data and refresh the histogram."""
        self._g_map = g_map
        self._s_map = s_map
        self._intensity = intensity
        self._labels = labels
        self._labels_flat = labels.ravel() if labels is not None else None
        self._total_valid_pixels = int(
            (np.isfinite(g_map) & (g_map != 0)).sum()
        )

        # Invalidate all ROI mask caches
        for w in self._roi_widgets:
            w.cached_mask = None

        if g_unfiltered is not None:
            self._g_map_unfiltered = g_unfiltered
            self._s_map_unfiltered = s_unfiltered
            self._filtered_check.setEnabled(True)
            self._filtered_check.setChecked(True)
        else:
            self._g_map_unfiltered = None
            self._s_map_unfiltered = None
            self._filtered_check.setEnabled(False)
            self._filtered_check.setChecked(False)

        self._refresh_histogram()

    def _on_filtered_toggled(self, checked: bool) -> None:
        # Invalidate all caches when switching filtered/unfiltered
        for w in self._roi_widgets:
            w.cached_mask = None
        self._refresh_histogram()

    def _on_filter_changed(self) -> None:
        """Handle filter changes — debounced histogram refresh."""
        self._filter_timer.start()

    def _refresh_histogram(self) -> None:
        """Render intensity-weighted 2D histogram."""
        if self._g_map is None or self._s_map is None:
            return

        g_display, s_display = self._get_active_gs_maps()
        g_flat = g_display.ravel()
        s_flat = s_display.ravel()

        valid = np.isfinite(g_flat) & np.isfinite(s_flat) & (g_flat != 0)

        # Apply cell-level filter if active
        filtered_ids = self._session.filter_ids
        if filtered_ids is not None and self._labels_flat is not None:
            cell_mask = np.isin(self._labels_flat, list(filtered_ids))
            valid = valid & cell_mask

        g_flat = g_flat[valid]
        s_flat = s_flat[valid]

        if len(g_flat) == 0:
            self._status.showMessage("No valid phasor data")
            return

        if self._intensity is not None:
            weights = self._intensity.ravel()[valid]
        else:
            weights = np.ones(len(g_flat))

        g_range = (-0.005, 1.005)
        s_range = (0.0, 0.7)

        hist, g_edges, s_edges = np.histogram2d(
            g_flat, s_flat,
            bins=300,
            range=[g_range, s_range],
            weights=weights,
        )

        hist_display = np.log1p(hist)

        if self._hist_item is not None:
            self._plot.removeItem(self._hist_item)

        self._hist_item = pg.ImageItem()
        self._plot.addItem(self._hist_item)

        cmap = pg.colormap.get("CET-R4")
        if cmap is None:
            cmap = pg.colormap.get("viridis")
        self._hist_item.setImage(hist_display)
        self._hist_item.setColorMap(cmap)

        self._hist_item.setRect(
            QRectF(g_range[0], s_range[0],
                   g_range[1] - g_range[0],
                   s_range[1] - s_range[0])
        )

        self._semicircle.setZValue(10)
        for w in self._roi_widgets:
            w.roi.setZValue(10)
            w.curve.setZValue(10)

        self._plot.setXRange(*g_range, padding=0)
        self._plot.setYRange(*s_range, padding=0)
        self._plot.getAxis("bottom").enableAutoSIPrefix(False)
        self._plot.getAxis("left").enableAutoSIPrefix(False)

        n_pixels = len(g_flat)
        self._status.showMessage(f"Phasor: {n_pixels:,} valid pixels")

    # ── Apply mask ────────────────────────────────────────────

    def _on_apply_mask(self) -> None:
        """Emit mask_applied signal — launcher handles viewer + HDF5."""
        if self._g_map is None or not self._roi_widgets:
            self._status.showMessage("No phasor data or ROIs", 3000)
            return

        mask = self._compute_combined_mask()
        if mask.max() == 0:
            self._status.showMessage("No visible ROIs to apply", 3000)
            return

        mask_name = "phasor_roi"
        color_dict = {0: "transparent", None: "transparent"}
        for w in self._roi_widgets:
            if w.phasor_roi.visible:
                color_dict[w.phasor_roi.label] = w.phasor_roi.color

        # Emit signal — launcher removes preview, adds mask, saves to HDF5
        self.mask_applied.emit(mask, color_dict, mask_name)
        self._status.showMessage("Multi-ROI mask applied", 3000)

    # ── Save / Load ROIs ──────────────────────────────────────

    def _on_save_rois(self) -> None:
        if not self._roi_widgets:
            self._status.showMessage("No ROIs to save", 3000)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save ROIs", "", "JSON Files (*.json)"
        )
        if not path:
            return
        data = {"rois": [w.phasor_roi.to_dict() for w in self._roi_widgets]}
        Path(path).write_text(json.dumps(data, indent=2))
        self._status.showMessage(f"Saved {len(self._roi_widgets)} ROIs", 3000)

    def _on_load_rois(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load ROIs", "", "JSON Files (*.json)"
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text())
            rois_data = data["rois"]
            if not isinstance(rois_data, list):
                raise ValueError("'rois' must be a list")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            QMessageBox.warning(self, "Load Error", f"Invalid ROI file:\n{e}")
            return

        # Clear existing ROIs
        for w in self._roi_widgets:
            self._plot.removeItem(w.roi)
            self._plot.removeItem(w.curve)
        self._roi_widgets.clear()

        # Create from JSON — labels derived from position
        for i, roi_data in enumerate(rois_data):
            try:
                phasor_roi = PhasorROI.from_dict(
                    roi_data,
                    label=i + 1,
                    default_color=COLOR_CYCLE[i % len(COLOR_CYCLE)],
                )
            except ValueError as e:
                QMessageBox.warning(self, "Load Error", f"ROI {i}: {e}")
                continue
            self._create_roi_widget(phasor_roi)

        self._colormap_dirty = True
        self._selected_roi_index = None
        self._refresh_roi_list()
        if self._roi_widgets:
            self._roi_list.setCurrentRow(0)
        self._preview_timer.start()
        self._status.showMessage(f"Loaded {len(self._roi_widgets)} ROIs", 3000)

    # ── Lifecycle ─────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        for unsub in getattr(self, '_unsubs', []):
            unsub()
        self._save_geometry()
        self.hide()
        event.ignore()

    def _save_geometry(self) -> None:
        QSettings("LeeLabPerCell4", "PerCell4").setValue(
            "phasor_plot/geometry", self.saveGeometry()
        )

    def _restore_geometry(self) -> None:
        geom = QSettings("LeeLabPerCell4", "PerCell4").value("phasor_plot/geometry")
        if geom:
            self.restoreGeometry(geom)
