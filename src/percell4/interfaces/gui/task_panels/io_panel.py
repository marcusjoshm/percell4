"""I/O task panel — import, load, close, export.

Extracted from launcher._create_io_panel. Delegates heavy orchestration
(batch compress, dataset loading, viewer population) back to the launcher
via the launcher reference. These will move to use cases in a future pass.
"""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.gui import theme
from percell4.model import CellDataModel


class IoPanel(QWidget):
    """Panel for dataset import, load, close, and export."""

    def __init__(
        self,
        data_model: CellDataModel,
        launcher=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.data_model = data_model
        self._launcher = launcher
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("Import / Export")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {theme.TEXT_BRIGHT};"
            f" margin-bottom: 12px; padding-bottom: 4px;"
            f" border-bottom: 1px solid {theme.BORDER};"
        )
        layout.addWidget(title)

        # ── Import ──
        import_group = QGroupBox("Import")
        import_layout = QVBoxLayout(import_group)

        btn_import = QPushButton("Compress TIFF Dataset...")
        btn_import.clicked.connect(self._on_import_dataset)
        import_layout.addWidget(btn_import)

        btn_load = QPushButton("Load Dataset...")
        btn_load.clicked.connect(self._on_load_dataset)
        import_layout.addWidget(btn_load)

        btn_add_layer = QPushButton("Add Layer to Dataset...")
        btn_add_layer.clicked.connect(self._on_add_layer_to_dataset)
        import_layout.addWidget(btn_add_layer)

        btn_close = QPushButton("Close Dataset")
        btn_close.clicked.connect(self._on_close_dataset)
        import_layout.addWidget(btn_close)

        layout.addWidget(import_group)

        # ── Export ──
        export_group = QGroupBox("Export")
        export_layout = QVBoxLayout(export_group)

        btn_export_csv = QPushButton("Export Measurements to CSV...")
        btn_export_csv.clicked.connect(self._on_export_csv)
        export_layout.addWidget(btn_export_csv)

        btn_export_images = QPushButton("Export Images...")
        btn_export_images.clicked.connect(self._on_export_images)
        export_layout.addWidget(btn_export_images)

        layout.addWidget(export_group)

        layout.addStretch()

    # ── Helpers ───────────────────────────────────────────────

    def _show_status(self, msg: str) -> None:
        if self._launcher is not None:
            self._launcher.statusBar().showMessage(msg)

    # ── Handlers (delegate heavy work to launcher) ───────────

    def _on_import_dataset(self) -> None:
        if self._launcher is not None:
            self._launcher._on_import_dataset()

    def _on_load_dataset(self) -> None:
        if self._launcher is not None:
            self._launcher._on_load_dataset()

    def _on_add_layer_to_dataset(self) -> None:
        if self._launcher is not None:
            self._launcher._on_add_layer_to_dataset()

    def _on_close_dataset(self) -> None:
        if self._launcher is not None:
            self._launcher._on_close_dataset()

    def _on_export_csv(self) -> None:
        if self._launcher is not None:
            self._launcher._on_export_csv()

    def _on_export_images(self) -> None:
        if self._launcher is not None:
            self._launcher._on_export_images()
