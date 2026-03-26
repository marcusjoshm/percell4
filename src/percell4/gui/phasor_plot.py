"""Phasor plot window — 2D histogram density of FLIM phasor coordinates.

Uses pyqtgraph ImageItem with histogram2d for rendering (handles millions
of pixels). EllipseROI for selecting lifetime populations. Universal
semicircle overlay.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import QSettings
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from percell4.model import CellDataModel


class PhasorPlotWindow(QMainWindow):
    """Phasor plot window with 2D histogram density and ROI selection.

    Features:
    - 2D histogram rendered as ImageItem (handles millions of pixels)
    - Universal semicircle overlay
    - EllipseROI for selecting lifetime populations
    - Harmonic selector
    - Apply as Mask button
    """

    def __init__(self, data_model: CellDataModel) -> None:
        super().__init__()
        self.data_model = data_model
        self.setWindowTitle("PerCell4 — Phasor Plot")
        self.resize(650, 600)

        self._g_map: np.ndarray | None = None
        self._s_map: np.ndarray | None = None
        self._g_map_unfiltered: np.ndarray | None = None
        self._s_map_unfiltered: np.ndarray | None = None
        self._hist_bins = 256

        self._build_ui()
        self._restore_geometry()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)

        # Controls
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Harmonic:"))
        self._harmonic_combo = QComboBox()
        self._harmonic_combo.addItems(["1", "2", "3"])
        controls.addWidget(self._harmonic_combo)

        controls.addSpacing(16)
        self._filtered_check = QCheckBox("Filtered")
        self._filtered_check.setStyleSheet("QCheckBox { color: #e0e0e0; }")
        self._filtered_check.setEnabled(False)
        self._filtered_check.toggled.connect(self._on_filtered_toggled)
        controls.addWidget(self._filtered_check)

        controls.addStretch()

        btn_apply = QPushButton("Apply as Mask")
        btn_apply.clicked.connect(self._on_apply_mask)
        controls.addWidget(btn_apply)

        layout.addLayout(controls)

        # Plot
        self._plot = pg.PlotWidget()
        self._plot.setBackground("#1e1e1e")
        self._plot.setAspectLocked(False)
        self._plot.setLabel("bottom", "G")
        self._plot.setLabel("left", "S")
        layout.addWidget(self._plot)

        # Histogram image item
        self._hist_item = pg.ImageItem()
        self._plot.addItem(self._hist_item)

        # Universal semicircle
        theta = np.linspace(0, np.pi, 200)
        semi_g = 0.5 + 0.5 * np.cos(theta)
        semi_s = 0.5 * np.sin(theta)
        self._plot.plot(
            semi_g, semi_s,
            pen=pg.mkPen("w", width=2, style=pg.QtCore.Qt.DashLine),
        )

        # Ellipse ROI for phasor selection
        self._roi = pg.EllipseROI(
            [0.2, 0.05], [0.2, 0.15],
            pen=pg.mkPen("r", width=2),
        )
        self._plot.addItem(self._roi)
        self._roi.sigRegionChangeFinished.connect(self._on_roi_changed)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("No phasor data loaded")

    def set_phasor_data(
        self,
        g_map: np.ndarray,
        s_map: np.ndarray,
        g_unfiltered: np.ndarray | None = None,
        s_unfiltered: np.ndarray | None = None,
    ) -> None:
        """Set phasor data and refresh the histogram.

        If unfiltered data is provided, the Filtered checkbox toggles
        between filtered and unfiltered views.
        """
        self._g_map = g_map
        self._s_map = s_map
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
        """Toggle between filtered and unfiltered phasor display."""
        if checked and self._g_map is not None:
            # Show filtered (already in _g_map/_s_map)
            pass
        elif not checked and self._g_map_unfiltered is not None:
            # Swap to unfiltered for display
            pass
        self._refresh_histogram()

    def _refresh_histogram(self) -> None:
        """Render the phasor data as a 2D histogram density image."""
        if self._g_map is None or self._s_map is None:
            return

        # Choose filtered or unfiltered based on checkbox
        use_filtered = self._filtered_check.isChecked()
        if not use_filtered and self._g_map_unfiltered is not None:
            g_display = self._g_map_unfiltered
            s_display = self._s_map_unfiltered
        else:
            g_display = self._g_map
            s_display = self._s_map

        g_flat = g_display.ravel()
        s_flat = s_display.ravel()

        # Remove NaN pixels
        valid = np.isfinite(g_flat) & np.isfinite(s_flat)
        g_flat = g_flat[valid]
        s_flat = s_flat[valid]

        if len(g_flat) == 0:
            self._status.showMessage("No valid phasor data")
            return

        # 2D histogram
        g_range = (-0.1, 1.1)
        s_range = (-0.1, 0.7)
        hist, g_edges, s_edges = np.histogram2d(
            g_flat, s_flat,
            bins=self._hist_bins,
            range=[g_range, s_range],
        )

        # Log scale for dynamic range
        hist_log = np.log1p(hist).T  # transpose: histogram2d returns (x,y)

        # Remove old image and create fresh one to force visual update
        if self._hist_item is not None:
            self._plot.removeItem(self._hist_item)

        self._hist_item = pg.ImageItem()
        self._plot.addItem(self._hist_item)

        # Apply colormap and set image
        cmap = pg.colormap.get("viridis")
        self._hist_item.setImage(hist_log)
        self._hist_item.setColorMap(cmap)

        # Position the image to match phasor coordinates
        scale_x = (g_range[1] - g_range[0]) / hist_log.shape[1]
        scale_y = (s_range[1] - s_range[0]) / hist_log.shape[0]
        self._hist_item.setTransform(
            pg.QtGui.QTransform()
            .translate(g_range[0], s_range[0])
            .scale(scale_x, scale_y)
        )

        # Force auto-range update
        self._plot.autoRange()

        n_pixels = len(g_flat)
        self._status.showMessage(f"Phasor: {n_pixels:,} valid pixels")

    def _on_roi_changed(self) -> None:
        """Update ROI statistics when the ellipse is moved/resized."""
        if self._g_map is None:
            return

        mask = self._get_roi_mask()
        if mask is None:
            return

        n_inside = int(mask.sum())
        n_total = int(np.isfinite(self._g_map).sum())
        if n_total > 0:
            pct = 100.0 * n_inside / n_total
            self._status.showMessage(
                f"ROI: {n_inside:,} pixels ({pct:.1f}%) | Total: {n_total:,}"
            )

    def _get_roi_mask(self) -> np.ndarray | None:
        """Get the spatial mask from the current ROI position."""
        if self._g_map is None:
            return None

        from percell4.flim.phasor import phasor_roi_to_mask

        state = self._roi.getState()
        pos = state["pos"]
        size = state["size"]
        center = (pos[0] + size[0] / 2, pos[1] + size[1] / 2)
        radii = (size[0] / 2, size[1] / 2)

        return phasor_roi_to_mask(self._g_map, self._s_map, center, radii)

    def _on_apply_mask(self) -> None:
        """Apply the ROI as a spatial mask (stub — needs launcher wiring)."""
        mask = self._get_roi_mask()
        if mask is None:
            self._status.showMessage("No phasor data loaded")
            return
        n_inside = int(mask.sum())
        self._status.showMessage(
            f"Mask created: {n_inside:,} pixels. "
            "Wire to launcher to save as HDF5 mask."
        )

    # ── Lifecycle ─────────────────────────────────────────────

    def closeEvent(self, event) -> None:
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
