"""Import dialog for converting TIFF directories to HDF5 datasets."""

from __future__ import annotations

from pathlib import Path

from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
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
        self.setMinimumWidth(500)
        self.resize(500, 500)
        self._project_dir = project_dir

        self._build_ui()

    def _build_ui(self) -> None:
        from qtpy.QtWidgets import QScrollArea

        outer_layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer_layout.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)

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
            "top_left", "bottom_left", "top_right", "bottom_right"
        ])
        tile_layout.addRow("Start corner:", self._tile_order)

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

        # ── FLIM / TCSPC ──────────────────────────────────────
        self._flim_enabled = QCheckBox("Dataset contains TCSPC FLIM data")
        self._flim_enabled.setStyleSheet("QCheckBox { color: #e0e0e0; }")
        layout.addWidget(self._flim_enabled)

        self._flim_group = QGroupBox("FLIM Parameters")
        flim_layout = QVBoxLayout(self._flim_group)

        freq_form = QFormLayout()
        self._flim_freq = QDoubleSpinBox()
        self._flim_freq.setRange(0.1, 1000.0)
        self._flim_freq.setValue(80.0)
        self._flim_freq.setDecimals(1)
        self._flim_freq.setSuffix(" MHz")
        freq_form.addRow("Laser frequency:", self._flim_freq)
        flim_layout.addLayout(freq_form)

        # Per-channel calibration
        btn_discover = QPushButton("Discover Channels")
        btn_discover.setToolTip(
            "Scan the source directory for .bin files and\n"
            "detect channels. Enter calibration per channel."
        )
        btn_discover.clicked.connect(self._discover_channels)
        flim_layout.addWidget(btn_discover)

        self._channel_cal_container = QVBoxLayout()
        flim_layout.addLayout(self._channel_cal_container)
        self._channel_calibrations: dict[str, dict] = {}  # ch_name -> {phase_spin, mod_spin}

        # .bin file parameters (only for raw binary TCSPC)
        self._bin_group = QGroupBox(".bin File Dimensions (raw binary only)")
        bin_layout = QFormLayout(self._bin_group)

        self._bin_x = QSpinBox()
        self._bin_x.setRange(1, 10000)
        self._bin_x.setValue(512)
        bin_layout.addRow("X dimension:", self._bin_x)

        self._bin_y = QSpinBox()
        self._bin_y.setRange(1, 10000)
        self._bin_y.setValue(512)
        bin_layout.addRow("Y dimension:", self._bin_y)

        self._bin_t = QSpinBox()
        self._bin_t.setRange(1, 4096)
        self._bin_t.setValue(132)
        bin_layout.addRow("Time bins:", self._bin_t)

        self._bin_dtype = QComboBox()
        self._bin_dtype.addItems(["uint32", "uint16", "float32", "uint8"])
        bin_layout.addRow("Data type:", self._bin_dtype)

        self._bin_dim_order = QComboBox()
        self._bin_dim_order.addItems(["YXT", "XYT", "TYX"])
        bin_layout.addRow("Dimension order:", self._bin_dim_order)

        self._bin_header = QSpinBox()
        self._bin_header.setRange(0, 10000)
        self._bin_header.setValue(0)
        self._bin_header.setSpecialValueText("Auto-detect")
        bin_layout.addRow("Header bytes:", self._bin_header)

        flim_layout.addWidget(self._bin_group)

        self._flim_group.setVisible(False)
        self._flim_enabled.toggled.connect(self._flim_group.setVisible)
        layout.addWidget(self._flim_group)

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

        # ── Buttons (outside scroll area, always visible) ─────
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer_layout.addWidget(buttons)

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

    def _discover_channels(self) -> None:
        """Scan source directory for .bin files and create per-channel calibration."""
        import re

        source = self._source_dir.text()
        if not source:
            return

        source_path = Path(source)
        if not source_path.is_dir():
            return

        # Find .bin files and extract channel tokens
        channel_pattern = self._tok_channel.text()
        channels: set[str] = set()
        for f in sorted(source_path.glob("*.bin")):
            if channel_pattern:
                m = re.search(channel_pattern, f.stem)
                if m:
                    channels.add(m.group(1))

        if not channels:
            # Also check TIFF files with TCSPC token
            for f in sorted(source_path.glob("*.tif")) + sorted(source_path.glob("*.tiff")):
                if "TCSPC" in f.stem.upper() and channel_pattern:
                    m = re.search(channel_pattern, f.stem)
                    if m:
                        channels.add(m.group(1))

        if not channels:
            return

        # Clear old channel calibration widgets
        while self._channel_cal_container.count():
            item = self._channel_cal_container.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._channel_calibrations.clear()

        # Create calibration fields per channel
        for ch in sorted(channels):
            ch_name = f"ch{ch}"
            group = QGroupBox(f"Channel {ch_name}")
            form = QFormLayout(group)

            phase_spin = QDoubleSpinBox()
            phase_spin.setRange(-6.283, 6.283)
            phase_spin.setValue(0.0)
            phase_spin.setDecimals(4)
            phase_spin.setSuffix(" rad")
            form.addRow("Phase:", phase_spin)

            mod_spin = QDoubleSpinBox()
            mod_spin.setRange(0.0, 10.0)
            mod_spin.setValue(1.0)
            mod_spin.setDecimals(4)
            form.addRow("Modulation:", mod_spin)

            self._channel_cal_container.addWidget(group)
            self._channel_calibrations[ch_name] = {
                "phase_spin": phase_spin,
                "mod_spin": mod_spin,
            }

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

    @property
    def has_flim(self) -> bool:
        return self._flim_enabled.isChecked()

    @property
    def flim_frequency_mhz(self) -> float:
        return self._flim_freq.value()

    @property
    def flim_channel_calibrations(self) -> dict[str, dict[str, float]]:
        """Per-channel calibration: {ch_name: {phase: float, modulation: float}}."""
        result = {}
        for ch_name, widgets in self._channel_calibrations.items():
            result[ch_name] = {
                "phase": widgets["phase_spin"].value(),
                "modulation": widgets["mod_spin"].value(),
            }
        return result

    @property
    def bin_dimensions(self) -> dict:
        """Dimensions for .bin TCSPC files."""
        return {
            "x_dim": self._bin_x.value(),
            "y_dim": self._bin_y.value(),
            "t_dim": self._bin_t.value(),
            "dtype": self._bin_dtype.currentText(),
            "dim_order": self._bin_dim_order.currentText(),
            "header_bytes": self._bin_header.value(),
        }

