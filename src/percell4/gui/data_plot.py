"""Data plot window — scatter plot of per-cell metrics.

Full implementation in Phase 6. This is a placeholder stub.
"""

from __future__ import annotations

from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget

from percell4.model import CellDataModel


class DataPlotWindow(QMainWindow):
    """Scatter plot window for per-cell metric visualization."""

    def __init__(self, data_model: CellDataModel) -> None:
        super().__init__()
        self.data_model = data_model
        self.setWindowTitle("PerCell4 — Data Plot")
        self.resize(600, 500)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.addWidget(QLabel("Data Plot — coming in Phase 6"))

        self._restore_geometry()

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
