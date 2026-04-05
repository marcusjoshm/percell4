"""Data plot window — interactive scatter plot of per-cell metrics.

Two-layer rendering: base scatter (all points, cached) + highlight scatter
(selected points only, fast redraws). Column selectors let the user choose
any numeric metric for X and Y axes.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import QEvent, QRectF, QSettings, Qt, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from percell4.model import CellDataModel, StateChange


class SelectionViewBox(pg.ViewBox):
    """ViewBox that supports Shift+drag for rectangle selection.

    Normal drag = pan/zoom (default). Shift+drag = rubber-band rectangle
    that emits sigSelectionComplete with a QRectF in data coordinates.
    """

    sigSelectionComplete = Signal(object)  # QRectF in data coordinates

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() == Qt.LeftButton and ev.modifiers() & Qt.ShiftModifier:
            ev.accept()
            if ev.isStart():
                self.updateScaleBox(ev.buttonDownPos(), ev.pos())
            elif ev.isFinish():
                self.rbScaleBox.hide()
                p1 = ev.buttonDownPos()
                p2 = ev.pos()
                rect = QRectF(p1, p2).normalized()
                data_rect = self.childGroup.mapRectFromParent(rect)
                self.sigSelectionComplete.emit(data_rect)
            else:
                self.updateScaleBox(ev.buttonDownPos(), ev.pos())
        else:
            super().mouseDragEvent(ev, axis)


class DataPlotWindow(QMainWindow):
    """Scatter plot window for per-cell metric visualization.

    Features:
    - Two-layer scatter: base (all points, cyan) + highlight (selected, red)
    - X/Y column dropdown selectors (populated from DataFrame numeric columns)
    - Click point → select cell → syncs to viewer + table
    - Ctrl-click → additive selection (toggle)
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

        # Bootstrap from existing model state (late-joining window)
        if not self.data_model.df.empty:
            self._on_state_changed(StateChange(data=True, filter=True, selection=True))

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
        # Reset view button
        self._reset_btn = QPushButton("Reset View")
        self._reset_btn.setMaximumWidth(80)
        self._reset_btn.clicked.connect(self._on_reset_view)
        controls.addWidget(self._reset_btn)
        controls.addStretch()
        layout.addLayout(controls)

        # Plot widget with custom ViewBox for Shift+drag selection
        self._vb = SelectionViewBox()
        self._plot = pg.PlotWidget(viewBox=self._vb)
        from percell4.gui import theme

        self._plot.setBackground(theme.BACKGROUND)
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        self._plot.installEventFilter(self)  # for Escape key
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
        self.data_model.state_changed.connect(self._on_state_changed)
        self._x_combo.currentTextChanged.connect(self._refresh_plot)
        self._y_combo.currentTextChanged.connect(self._refresh_plot)
        self._base_scatter.sigClicked.connect(self._on_point_clicked)
        self._vb.sigSelectionComplete.connect(self._on_rect_selected)

    # ── Unified state change handler ─────────────────────────

    def _on_state_changed(self, change) -> None:
        """Handle all model state changes in one atomic pass.

        Ordering matters: rebuild dropdowns before refreshing the plot,
        and only update highlights if we didn't already do a full refresh.
        """
        if change.data:
            self._rebuild_dropdowns()
        if change.data or change.filter:
            self._refresh_plot()
        elif change.selection:
            # Only update highlights if we didn't already refresh the full plot
            # (which internally re-applies highlights anyway).
            # Note: outbound clicks cause a redundant highlight redraw via the
            # signal round-trip — accepted as cheap and harmless for simpler code.
            self._update_selection_highlights()

    def _rebuild_dropdowns(self) -> None:
        """Populate X/Y column dropdowns from current DataFrame.

        blockSignals prevents currentTextChanged from firing during population,
        which would trigger redundant _refresh_plot calls.
        """
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
            default_y = [c for c in numeric_cols if c != self._x_combo.currentText()]
            if default_y:
                self._y_combo.setCurrentText(default_y[0])

        self._x_combo.blockSignals(False)
        self._y_combo.blockSignals(False)

    def _update_selection_highlights(self) -> None:
        """Update highlight scatter to reflect current model selection."""
        label_ids = self.data_model.selected_ids

        if self._labels_array is None or self._x_data is None:
            self._highlight_scatter.clear()
            return

        if not label_ids:
            self._highlight_scatter.clear()
            self._status.showMessage(
                f"Total: {len(self._labels_array)} cells"
            )
            return

        mask = np.isin(self._labels_array, label_ids)

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
        """Redraw the base scatter from current DataFrame and column selections.

        Uses filtered_df for row data, df for column availability.
        """
        df = self.data_model.filtered_df
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

        # Array-based setData — 10-20x faster than dict-based addPoints
        self._base_scatter.setData(x=x, y=y)

        self._plot.setLabel("bottom", x_col)
        self._plot.setLabel("left", y_col)

        # Re-apply current selection highlight after rebuilding scatter
        self._update_selection_highlights()

        self._status.showMessage(f"Total: {len(labels)} cells")

    # ── Click handling ────────────────────────────────────────

    def _on_point_clicked(self, scatter_item, points, ev) -> None:
        """Handle click on scatter point → select cell. Ctrl-click toggles."""
        if not points or self._labels_array is None:
            return
        idx = points[0].index()
        if idx is None or idx >= len(self._labels_array):
            return
        label_id = int(self._labels_array[idx])

        if ev.modifiers() & Qt.ControlModifier:
            # Ctrl-click: toggle this label in/out of selection
            current = set(self.data_model.selected_ids)
            if label_id in current:
                current.discard(label_id)
            else:
                current.add(label_id)
            self.data_model.set_selection(list(current))
        else:
            self.data_model.set_selection([label_id])

    def _on_rect_selected(self, data_rect: QRectF) -> None:
        """Select all points within the Shift+drag rectangle."""
        if self._x_data is None or self._labels_array is None:
            return
        mask = (
            (self._x_data >= data_rect.left())
            & (self._x_data <= data_rect.right())
            & (self._y_data >= data_rect.top())
            & (self._y_data <= data_rect.bottom())
        )
        selected_labels = self._labels_array[mask].tolist()
        if selected_labels:
            self.data_model.set_selection([int(lid) for lid in selected_labels])

    def _on_reset_view(self) -> None:
        """Reset zoom/pan to auto-range."""
        self._plot.autoRange()

    def eventFilter(self, obj, event) -> bool:
        """Escape key clears selection."""
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.data_model.set_selection([])
            return True
        return super().eventFilter(obj, event)

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
