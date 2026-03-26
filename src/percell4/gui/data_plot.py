"""Data plot window — interactive scatter plot of per-cell metrics.

Two-layer rendering: base scatter (all points, cached) + highlight scatter
(selected points only, fast redraws). Column selectors let the user choose
any numeric metric for X and Y axes.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import QSettings
from qtpy.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from percell4.model import CellDataModel


class DataPlotWindow(QMainWindow):
    """Scatter plot window for per-cell metric visualization.

    Features:
    - Two-layer scatter: base (all points, cyan) + highlight (selected, red)
    - X/Y column dropdown selectors (populated from DataFrame numeric columns)
    - Click point → select cell → syncs to viewer + table
    - Listens to CellDataModel signals for updates and selection
    """

    def __init__(self, data_model: CellDataModel) -> None:
        super().__init__()
        self.data_model = data_model
        self.setWindowTitle("PerCell4 — Data Plot")
        self.resize(650, 550)

        self._labels_array: np.ndarray | None = None
        self._x_data: np.ndarray | None = None
        self._y_data: np.ndarray | None = None

        self._build_ui()
        self._connect_signals()
        self._restore_geometry()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)

        # Column selectors
        controls = QHBoxLayout()
        controls.addWidget(QLabel("X:"))
        self._x_combo = QComboBox()
        self._x_combo.setMinimumWidth(120)
        controls.addWidget(self._x_combo)
        controls.addSpacing(16)
        controls.addWidget(QLabel("Y:"))
        self._y_combo = QComboBox()
        self._y_combo.setMinimumWidth(120)
        controls.addWidget(self._y_combo)
        controls.addStretch()
        layout.addLayout(controls)

        # Plot widget
        self._plot = pg.PlotWidget()
        self._plot.setBackground("#1e1e1e")
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        layout.addWidget(self._plot)

        # Base scatter — all points, uniform style, cached
        self._base_scatter = pg.ScatterPlotItem(
            size=6,
            pen=pg.mkPen(None),
            brush=pg.mkBrush(0, 200, 220, 120),
            pxMode=True,
            hoverable=True,
            hoverPen=pg.mkPen("w", width=1),
            hoverSize=10,
        )
        self._plot.addItem(self._base_scatter)

        # Highlight scatter — selected points only, fast redraws
        self._highlight_scatter = pg.ScatterPlotItem(
            size=12,
            pen=pg.mkPen("r", width=2),
            brush=pg.mkBrush(255, 50, 50, 100),
            pxMode=True,
        )
        self._plot.addItem(self._highlight_scatter)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("No data")

    def _connect_signals(self) -> None:
        self.data_model.data_updated.connect(self._on_data_updated)
        self.data_model.selection_changed.connect(self._on_selection_changed)
        self._x_combo.currentTextChanged.connect(self._refresh_plot)
        self._y_combo.currentTextChanged.connect(self._refresh_plot)
        self._base_scatter.sigClicked.connect(self._on_point_clicked)

    # ── Data model reactions ──────────────────────────────────

    def _on_data_updated(self) -> None:
        """Rebuild column dropdowns and refresh the scatter."""
        df = self.data_model.df
        if df.empty:
            self._x_combo.clear()
            self._y_combo.clear()
            self._base_scatter.clear()
            self._highlight_scatter.clear()
            self._status.showMessage("No data")
            return

        # Populate dropdowns with numeric columns (excluding label, bbox)
        skip = {"label", "bbox_y", "bbox_x", "bbox_h", "bbox_w"}
        numeric_cols = [
            c for c in df.select_dtypes(include=[np.number]).columns
            if c not in skip
        ]

        # Block signals to avoid triggering refresh during population
        self._x_combo.blockSignals(True)
        self._y_combo.blockSignals(True)

        prev_x = self._x_combo.currentText()
        prev_y = self._y_combo.currentText()

        self._x_combo.clear()
        self._y_combo.clear()
        self._x_combo.addItems(numeric_cols)
        self._y_combo.addItems(numeric_cols)

        # Restore previous selection if still valid, else pick defaults
        if prev_x in numeric_cols:
            self._x_combo.setCurrentText(prev_x)
        elif "area" in numeric_cols:
            self._x_combo.setCurrentText("area")

        if prev_y in numeric_cols:
            self._y_combo.setCurrentText(prev_y)
        elif len(numeric_cols) >= 2:
            # Pick second column as default Y
            default_y = [c for c in numeric_cols if c != self._x_combo.currentText()]
            if default_y:
                self._y_combo.setCurrentText(default_y[0])

        self._x_combo.blockSignals(False)
        self._y_combo.blockSignals(False)

        self._refresh_plot()

    def _on_selection_changed(self, label_ids: list[int]) -> None:
        """Update highlight scatter with selected cells only."""
        if self._labels_array is None or self._x_data is None:
            self._highlight_scatter.clear()
            return

        if not label_ids:
            self._highlight_scatter.clear()
            self._status.showMessage(
                f"Total: {len(self._labels_array)} cells"
            )
            return

        id_set = set(label_ids)
        mask = np.array([lid in id_set for lid in self._labels_array])

        if not np.any(mask):
            self._highlight_scatter.clear()
            return

        self._highlight_scatter.setData(
            x=self._x_data[mask],
            y=self._y_data[mask],
        )
        self._status.showMessage(
            f"Selected: {mask.sum()} | Total: {len(self._labels_array)} cells"
        )

    # ── Plot refresh ──────────────────────────────────────────

    def _refresh_plot(self) -> None:
        """Redraw the base scatter from current DataFrame and column selections."""
        df = self.data_model.df
        x_col = self._x_combo.currentText()
        y_col = self._y_combo.currentText()

        if df.empty or not x_col or not y_col:
            self._base_scatter.clear()
            self._highlight_scatter.clear()
            return

        if x_col not in df.columns or y_col not in df.columns:
            return

        x = df[x_col].values.astype(float)
        y = df[y_col].values.astype(float)
        labels = df["label"].values if "label" in df.columns else np.arange(len(df))

        # Filter out NaN
        valid = np.isfinite(x) & np.isfinite(y)
        x = x[valid]
        y = y[valid]
        labels = labels[valid]

        self._x_data = x
        self._y_data = y
        self._labels_array = labels

        # Set data with label IDs attached to each point
        spots = [
            {"pos": (xi, yi), "data": int(lid)}
            for xi, yi, lid in zip(x, y, labels)
        ]
        self._base_scatter.clear()
        self._base_scatter.addPoints(spots)

        self._plot.setLabel("bottom", x_col)
        self._plot.setLabel("left", y_col)

        # Re-apply current selection highlight
        self._on_selection_changed(self.data_model.selected_ids)

        self._status.showMessage(f"Total: {len(labels)} cells")

    # ── Click handling ────────────────────────────────────────

    def _on_point_clicked(self, scatter_item, points, ev) -> None:
        """Handle click on scatter point → select cell."""
        if not points:
            return
        # Get the label ID from the first clicked point
        label_id = points[0].data()
        if label_id is not None:
            self.data_model.set_selection([int(label_id)])

    # ── Lifecycle ─────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._save_geometry()
        self.hide()
        event.ignore()

    def _save_geometry(self) -> None:
        QSettings("LeeLabPerCell4", "PerCell4").setValue(
            "data_plot/geometry", self.saveGeometry()
        )

    def _restore_geometry(self) -> None:
        geom = QSettings("LeeLabPerCell4", "PerCell4").value("data_plot/geometry")
        if geom:
            self.restoreGeometry(geom)
