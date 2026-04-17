"""I/O task panel — import, load, close, export.

Receives action callbacks at construction — no launcher reference.
Each button click delegates to the injected callback.
"""

from __future__ import annotations

from collections.abc import Callable

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.gui import theme


class IoPanel(QWidget):
    """Panel for dataset import, load, close, and export.

    All actions are injected as callbacks — the panel has no knowledge
    of the launcher, use cases, or any other component.
    """

    def __init__(
        self,
        *,
        on_import: Callable[[], None],
        on_load: Callable[[], None],
        on_add_layer: Callable[[], None],
        on_close: Callable[[], None],
        on_export_csv: Callable[[], None],
        on_export_images: Callable[[], None],
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_import = on_import
        self._on_load = on_load
        self._on_add_layer = on_add_layer
        self._on_close = on_close
        self._on_export_csv = on_export_csv
        self._on_export_images = on_export_images
        self._show_status = show_status
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
        btn_import.clicked.connect(lambda: self._on_import())
        import_layout.addWidget(btn_import)

        btn_load = QPushButton("Load Dataset...")
        btn_load.clicked.connect(lambda: self._on_load())
        import_layout.addWidget(btn_load)

        btn_add_layer = QPushButton("Add Layer to Dataset...")
        btn_add_layer.clicked.connect(lambda: self._on_add_layer())
        import_layout.addWidget(btn_add_layer)

        btn_close = QPushButton("Close Dataset")
        btn_close.clicked.connect(lambda: self._on_close())
        import_layout.addWidget(btn_close)

        layout.addWidget(import_group)

        # ── Export ──
        export_group = QGroupBox("Export")
        export_layout = QVBoxLayout(export_group)

        btn_export_csv = QPushButton("Export Measurements to CSV...")
        btn_export_csv.clicked.connect(lambda: self._on_export_csv())
        export_layout.addWidget(btn_export_csv)

        btn_export_images = QPushButton("Export Images...")
        btn_export_images.clicked.connect(lambda: self._on_export_images())
        export_layout.addWidget(btn_export_images)

        layout.addWidget(export_group)

        layout.addStretch()
