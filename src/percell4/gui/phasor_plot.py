"""Phasor plot window — 2D histogram of FLIM phasor coordinates.

Full implementation in Phase 8. This is a placeholder stub.
"""

from __future__ import annotations

from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget

from percell4.model import CellDataModel


class PhasorPlotWindow(QMainWindow):
    """Phasor plot window with 2D histogram density and ROI selection."""

    def __init__(self, data_model: CellDataModel) -> None:
        super().__init__()
        self.data_model = data_model
        self.setWindowTitle("PerCell4 — Phasor Plot")
        self.resize(600, 500)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.addWidget(QLabel("Phasor Plot — coming in Phase 8"))

        self._restore_geometry()

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
