"""I/O task panel — import, load, close, export.

Receives action callbacks at construction — no launcher reference.
Each button click delegates to the injected callback.
"""

from __future__ import annotations

from collections.abc import Callable

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QMenu,
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
        on_export_phasor_npz: Callable[[], None],
        on_batch_tcspc: Callable[[], None] = lambda: None,
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_import = on_import
        self._on_load = on_load
        self._on_add_layer = on_add_layer
        self._on_batch_tcspc = on_batch_tcspc
        self._on_close = on_close
        self._on_export_csv = on_export_csv
        self._on_export_images = on_export_images
        self._on_export_phasor_npz = on_export_phasor_npz
        self._show_status = show_status
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(theme.section_label("I/O"))

        btn_new = QPushButton("New Dataset...")
        btn_new.setToolTip("Create a new dataset by compressing a TIFF dataset into HDF5.")
        btn_new.clicked.connect(lambda: self._on_import())
        layout.addWidget(btn_new)

        btn_open = QPushButton("Open Dataset...")
        btn_open.setToolTip("Open an existing .h5 dataset.")
        btn_open.clicked.connect(lambda: self._on_load())
        layout.addWidget(btn_open)

        # ── Add Data ▾ — menu of ways to add data to the open dataset ──
        btn_add = QPushButton("Add Data")
        add_menu = QMenu(btn_add)
        act_layer = add_menu.addAction("Layer...")
        act_layer.triggered.connect(lambda: self._on_add_layer())
        act_batch = add_menu.addAction("Batch TCSPC...")
        act_batch.setToolTip(
            "Append .bin decay layers to many existing datasets at once "
            "(uses one calibration CSV)."
        )
        act_batch.triggered.connect(lambda: self._on_batch_tcspc())
        btn_add.setMenu(add_menu)
        layout.addWidget(btn_add)

        btn_close = QPushButton("Close Dataset")
        btn_close.clicked.connect(lambda: self._on_close())
        layout.addWidget(btn_close)

        # ── Export ▾ — menu of export targets ──
        btn_export = QPushButton("Export")
        export_menu = QMenu(btn_export)
        act_csv = export_menu.addAction("Measurements (CSV)...")
        act_csv.triggered.connect(lambda: self._on_export_csv())
        act_images = export_menu.addAction("Images (TIFF)...")
        act_images.triggered.connect(lambda: self._on_export_images())
        act_phasor = export_menu.addAction("Phasor (.npz)...")
        act_phasor.setToolTip(
            "Export cached phasor data for every channel as one .npz file per "
            "channel for use with external Python scripts."
        )
        act_phasor.triggered.connect(lambda: self._on_export_phasor_npz())
        btn_export.setMenu(export_menu)
        layout.addWidget(btn_export)

        layout.addStretch()
