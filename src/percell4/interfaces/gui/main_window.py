"""Minimal main window for the hex architecture entry point.

Stage 1: just a Load Dataset button to prove the vertical slice works.
This will grow as more use cases are migrated.
"""

from __future__ import annotations

from pathlib import Path

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from percell4.application.use_cases.load_dataset import LoadDataset


class HexMainWindow(QMainWindow):
    """Minimal main window — Stage 1 proof of concept.

    Wires UI actions to use cases. No business logic here.
    """

    def __init__(
        self,
        load_dataset: LoadDataset,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._load_dataset = load_dataset

        self.setWindowTitle("PerCell4 (hex)")
        self.resize(400, 200)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("PerCell4 — Hexagonal Architecture (Stage 1)")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        btn_load = QPushButton("Load Dataset...")
        btn_load.clicked.connect(self._on_load_dataset)
        layout.addWidget(btn_load)

        layout.addStretch()

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

    def _on_load_dataset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Dataset", "", "HDF5 Files (*.h5);;All Files (*)"
        )
        if not path:
            return

        try:
            handle = self._load_dataset.execute(Path(path))
            self.statusBar().showMessage(f"Loaded: {handle.name}")
        except FileNotFoundError as e:
            self.statusBar().showMessage(str(e))
        except Exception as e:
            self.statusBar().showMessage(f"Error: {e}")
