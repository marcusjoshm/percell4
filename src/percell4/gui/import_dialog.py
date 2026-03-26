"""Import dialog for converting TIFF directories to HDF5 datasets."""

from __future__ import annotations

from pathlib import Path

from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from percell4.io.models import TileConfig, TokenConfig


class ImportDialog(QDialog):
    """Dialog for importing TIFF files into an HDF5 dataset.

    Collects all parameters needed for ``import_dataset()``:
    source directory, output path, token patterns, tile config,
    z-projection method, and metadata (condition, replicate, notes).
    """

    def __init__(self, parent=None, project_dir: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Dataset")
        self.setMinimumWidth(550)
        self._project_dir = project_dir

        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ── Source directory ──────────────────────────────────
        src_group = QGroupBox("Source")
        src_layout = QFormLayout(src_group)

        src_row = QHBoxLayout()
        self._source_dir = QLineEdit()
        self._source_dir.setPlaceholderText("Select TIFF directory...")
        src_row.addWidget(self._source_dir)
        btn_browse_src = QPushButton("Browse...")
        btn_browse_src.clicked.connect(self._browse_source)
        src_row.addWidget(btn_browse_src)
        src_layout.addRow("TIFF Directory:", src_row)

        out_row = QHBoxLayout()
        self._output_path = QLineEdit()
        self._output_path.setPlaceholderText("Auto-generated from source name")
        out_row.addWidget(self._output_path)
        btn_browse_out = QPushButton("Browse...")
        btn_browse_out.clicked.connect(self._browse_output)
        out_row.addWidget(btn_browse_out)
        src_layout.addRow("Output .h5:", out_row)

        layout.addWidget(src_group)

        # ── Token patterns ────────────────────────────────────
        token_group = QGroupBox("Filename Tokens")
        token_layout = QFormLayout(token_group)

        self._tok_channel = QLineEdit(r"_ch(\d+)")
        token_layout.addRow("Channel:", self._tok_channel)

        self._tok_timepoint = QLineEdit(r"_t(\d+)")
        token_layout.addRow("Timepoint:", self._tok_timepoint)

        self._tok_z = QLineEdit(r"_z(\d+)")
        token_layout.addRow("Z-slice:", self._tok_z)

        self._tok_tile = QLineEdit(r"_s(\d+)")
        token_layout.addRow("Tile:", self._tok_tile)

        layout.addWidget(token_group)

        # ── Tile stitching (collapsible) ──────────────────────
        self._tile_enabled = QCheckBox("Enable tile stitching")
        layout.addWidget(self._tile_enabled)

        self._tile_group = QGroupBox("Tile Stitching")
        tile_layout = QFormLayout(self._tile_group)

        self._tile_rows = QSpinBox()
        self._tile_rows.setRange(1, 100)
        self._tile_rows.setValue(1)
        tile_layout.addRow("Grid rows:", self._tile_rows)

        self._tile_cols = QSpinBox()
        self._tile_cols.setRange(1, 100)
        self._tile_cols.setValue(1)
        tile_layout.addRow("Grid cols:", self._tile_cols)

        self._tile_type = QComboBox()
        self._tile_type.addItems([
            "row_by_row", "column_by_column", "snake_by_row", "snake_by_column"
        ])
        tile_layout.addRow("Grid type:", self._tile_type)

        self._tile_order = QComboBox()
        self._tile_order.addItems([
            "right_down", "right_up", "left_down", "left_up"
        ])
        tile_layout.addRow("Order:", self._tile_order)

        self._tile_group.setVisible(False)
        self._tile_enabled.toggled.connect(self._tile_group.setVisible)
        layout.addWidget(self._tile_group)

        # ── Z-projection ──────────────────────────────────────
        z_group = QGroupBox("Z-Projection")
        z_layout = QFormLayout(z_group)

        self._z_method = QComboBox()
        self._z_method.addItems(["mip", "mean", "sum", "none"])
        z_layout.addRow("Method:", self._z_method)

        layout.addWidget(z_group)

        # ── Metadata ──────────────────────────────────────────
        meta_group = QGroupBox("Dataset Metadata")
        meta_layout = QFormLayout(meta_group)

        self._meta_condition = QLineEdit()
        self._meta_condition.setPlaceholderText("e.g., control, treated")
        meta_layout.addRow("Condition:", self._meta_condition)

        self._meta_replicate = QLineEdit()
        self._meta_replicate.setPlaceholderText("e.g., 1, 2, 3")
        meta_layout.addRow("Replicate:", self._meta_replicate)

        self._meta_notes = QLineEdit()
        meta_layout.addRow("Notes:", self._meta_notes)

        layout.addWidget(meta_group)

        # ── Buttons ───────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_source(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select TIFF Directory")
        if path:
            self._source_dir.setText(path)
            # Auto-generate output path
            if not self._output_path.text():
                name = Path(path).name
                if self._project_dir:
                    out = str(Path(self._project_dir) / f"{name}.h5")
                else:
                    out = str(Path(path).parent / f"{name}.h5")
                self._output_path.setText(out)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Dataset As", "", "HDF5 Files (*.h5)"
        )
        if path:
            self._output_path.setText(path)

    # ── Result accessors ──────────────────────────────────────

    @property
    def source_dir(self) -> str:
        return self._source_dir.text()

    @property
    def output_path(self) -> str:
        return self._output_path.text()

    @property
    def token_config(self) -> TokenConfig:
        def _or_none(text: str) -> str | None:
            return text.strip() if text.strip() else None

        return TokenConfig(
            channel=_or_none(self._tok_channel.text()),
            timepoint=_or_none(self._tok_timepoint.text()),
            z_slice=_or_none(self._tok_z.text()),
            tile=_or_none(self._tok_tile.text()),
        )

    @property
    def tile_config(self) -> TileConfig | None:
        if not self._tile_enabled.isChecked():
            return None
        return TileConfig(
            grid_rows=self._tile_rows.value(),
            grid_cols=self._tile_cols.value(),
            grid_type=self._tile_type.currentText(),
            order=self._tile_order.currentText(),
        )

    @property
    def z_project_method(self) -> str | None:
        method = self._z_method.currentText()
        return None if method == "none" else method

    @property
    def condition(self) -> str:
        return self._meta_condition.text()

    @property
    def replicate(self) -> str:
        return self._meta_replicate.text()

    @property
    def notes(self) -> str:
        return self._meta_notes.text()

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: #e0e0e0; }
            QGroupBox {
                color: #ffffff;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 16px;
            }
            QGroupBox::title {
                color: #4ea8de;
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QLineEdit, QComboBox, QSpinBox {
                background-color: #2a2a2a;
                color: #ffffff;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border-color: #4ea8de;
            }
            QPushButton {
                background-color: #2a2a2a;
                color: #ffffff;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover { background-color: #3a3a3a; border-color: #4ea8de; }
            QCheckBox { color: #e0e0e0; }
            QLabel { color: #cccccc; }
            QDialogButtonBox QPushButton { min-width: 80px; }
        """)
