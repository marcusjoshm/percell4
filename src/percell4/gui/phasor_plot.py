"""Phasor plot window — 2D histogram density of FLIM phasor coordinates.

Adapted from flimfret/phasor_plot_utils.py plotting logic for pyqtgraph.
Intensity-weighted histogram with universal circle contour.
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
    """Phasor plot window with intensity-weighted 2D histogram.

    Matches flimfret's phasor_plot_utils.py visualization:
    - Intensity-weighted histogram (not just pixel counts)
    - Universal circle from X²+Y²-X=0 contour
    - G=[0, 1], S=[0, 0.7] axis range
    - EllipseROI for population selection
    """

    def __init__(self, data_model: CellDataModel, launcher=None) -> None:
        super().__init__()
        self.data_model = data_model
        self._launcher = launcher
        self.setWindowTitle("PerCell4 — Phasor Plot")
        self.resize(650, 600)

        self._g_map: np.ndarray | None = None
        self._s_map: np.ndarray | None = None
        self._intensity: np.ndarray | None = None
        self._g_map_unfiltered: np.ndarray | None = None
        self._s_map_unfiltered: np.ndarray | None = None

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
        self._plot.setBackground("w")
        self._plot.setAspectLocked(False)
        self._plot.setLabel("bottom", "G")
        self._plot.setLabel("left", "S")
        self._plot.setXRange(-0.005, 1.005, padding=0)
        self._plot.setYRange(0, 0.7, padding=0)
        layout.addWidget(self._plot)

        # Histogram image item (will be recreated on each refresh)
        self._hist_item = None

        # Universal circle: contour of X²+Y²-X=0
        # Parametric form: center (0.5, 0), radius 0.5
        theta = np.linspace(0, np.pi, 200)
        semi_g = 0.5 + 0.5 * np.cos(theta)
        semi_s = 0.5 * np.sin(theta)
        self._semicircle = pg.PlotCurveItem(
            semi_g, semi_s,
            pen=pg.mkPen("k", width=2),
        )
        self._semicircle.setZValue(10)
        self._plot.addItem(self._semicircle)

        # Ellipse ROI for phasor selection
        self._roi = pg.EllipseROI(
            [0.3, 0.15], [0.2, 0.15],
            pen=pg.mkPen("b", width=2),
        )
        self._roi.setZValue(10)
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
        intensity: np.ndarray | None = None,
        g_unfiltered: np.ndarray | None = None,
        s_unfiltered: np.ndarray | None = None,
    ) -> None:
        """Set phasor data and refresh the histogram.

        Parameters
        ----------
        g_map, s_map : phasor coordinate maps (filtered if available)
        intensity : photon count map for intensity-weighted histogram
        g_unfiltered, s_unfiltered : unfiltered phasor for toggle
        """
        self._g_map = g_map
        self._s_map = s_map
        self._intensity = intensity

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
        self._refresh_histogram()

    def _refresh_histogram(self) -> None:
        """Render intensity-weighted 2D histogram (matching flimfret style)."""
        if self._g_map is None or self._s_map is None:
            return

        # Choose filtered or unfiltered
        use_filtered = self._filtered_check.isChecked()
        if not use_filtered and self._g_map_unfiltered is not None:
            g_display = self._g_map_unfiltered
            s_display = self._s_map_unfiltered
        else:
            g_display = self._g_map
            s_display = self._s_map

        g_flat = g_display.ravel()
        s_flat = s_display.ravel()

        # Remove NaN and zero pixels
        valid = np.isfinite(g_flat) & np.isfinite(s_flat) & (g_flat != 0)
        g_flat = g_flat[valid]
        s_flat = s_flat[valid]

        if len(g_flat) == 0:
            self._status.showMessage("No valid phasor data")
            return

        # Intensity weights (matching flimfret: weights=intensity)
        if self._intensity is not None:
            weights = self._intensity.ravel()[valid]
        else:
            weights = np.ones(len(g_flat))

        # Histogram with flimfret axis ranges
        g_range = (-0.005, 1.005)
        s_range = (0.0, 0.7)
        n_bins = 300  # good resolution

        hist, g_edges, s_edges = np.histogram2d(
            g_flat, s_flat,
            bins=n_bins,
            range=[g_range, s_range],
            weights=weights,
        )

        # Log scaling for dynamic range
        hist_display = np.log1p(hist)

        # Remove old histogram
        if self._hist_item is not None:
            self._plot.removeItem(self._hist_item)

        # Create new ImageItem with explicit rect positioning
        self._hist_item = pg.ImageItem()
        self._plot.addItem(self._hist_item)

        # setImage: image[x, y] where x=col (G), y=row (S) in pyqtgraph
        # histogram2d returns hist[g_bin, s_bin] — this maps directly to
        # ImageItem's [x, y] convention (no transpose needed)
        cmap = pg.colormap.get("CET-R4")
        if cmap is None:
            cmap = pg.colormap.get("viridis")
        self._hist_item.setImage(hist_display)
        self._hist_item.setColorMap(cmap)

        # Position: map pixel grid to data coordinates using setRect
        from qtpy.QtCore import QRectF

        self._hist_item.setRect(
            QRectF(g_range[0], s_range[0],
                   g_range[1] - g_range[0],
                   s_range[1] - s_range[0])
        )

        # Ensure overlays stay on top
        self._semicircle.setZValue(10)
        self._roi.setZValue(10)

        # Set axis range
        self._plot.setXRange(*g_range, padding=0)
        self._plot.setYRange(*s_range, padding=0)

        n_pixels = len(g_flat)
        self._status.showMessage(f"Phasor: {n_pixels:,} valid pixels")

    def _on_roi_changed(self) -> None:
        """Update the live preview mask in napari when the ROI moves."""
        if self._g_map is None:
            return
        mask = self._get_roi_mask()
        if mask is None:
            return

        mask_uint8 = mask.astype(np.uint8)
        n_inside = int(mask_uint8.sum())
        n_total = int((np.isfinite(self._g_map) & (self._g_map != 0)).sum())

        # Update live preview in napari
        if self._launcher is not None:
            viewer_win = self._launcher._windows.get("viewer")
            if viewer_win is not None and viewer_win.viewer is not None:
                # Update existing preview layer or create one
                preview_name = "_phasor_roi_preview"
                for layer in viewer_win.viewer.layers:
                    if layer.name == preview_name:
                        layer.data = mask_uint8
                        layer.refresh()
                        break
                else:
                    # First time — create the preview layer
                    from napari.utils.colormaps import DirectLabelColormap

                    cmap = DirectLabelColormap(
                        color_dict={0: "transparent", 1: "cyan", None: "transparent"},
                    )
                    viewer_win.viewer.add_labels(
                        mask_uint8,
                        name=preview_name,
                        opacity=0.4,
                        blending="translucent",
                        colormap=cmap,
                    )

        if n_total > 0:
            pct = 100.0 * n_inside / n_total
            self._status.showMessage(
                f"ROI: {n_inside:,} pixels ({pct:.1f}%) | Total: {n_total:,}"
            )

    def _get_roi_mask(self) -> np.ndarray | None:
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
        """Save the current phasor ROI mask to HDF5 and finalize."""
        mask = self._get_roi_mask()
        if mask is None:
            self._status.showMessage("No phasor data loaded")
            return

        mask_uint8 = mask.astype(np.uint8)
        n_inside = int(mask_uint8.sum())
        mask_name = "phasor_roi"

        if self._launcher is not None:
            viewer_win = self._launcher._windows.get("viewer")

            # Remove preview layer
            if viewer_win is not None and viewer_win.viewer is not None:
                for layer in list(viewer_win.viewer.layers):
                    if layer.name == "_phasor_roi_preview":
                        viewer_win.viewer.layers.remove(layer)
                        break

            # Add final mask layer
            if viewer_win is not None:
                viewer_win.add_mask(mask_uint8, name=mask_name)

            # Save to HDF5
            store = getattr(self._launcher, "_current_store", None)
            if store is not None:
                store.write_mask(mask_name, mask_uint8)

            # Set as active mask
            self.data_model.set_active_mask(mask_name)

        n_total = int((np.isfinite(self._g_map) & (self._g_map != 0)).sum())
        pct = 100.0 * n_inside / n_total if n_total > 0 else 0
        self._status.showMessage(
            f"Mask saved: {n_inside:,} pixels ({pct:.1f}%) as '{mask_name}'"
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
