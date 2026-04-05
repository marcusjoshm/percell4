"""Compress dialog for converting TIFF directories to HDF5 datasets.

Replaces ImportDialog with support for batch compression. Discovers
datasets from a root directory and lets the user select which datasets
and channels to compress. Operates at the semantic level (datasets,
channels) rather than individual files.
"""

from __future__ import annotations

import copy
from pathlib import Path

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.io.models import (
    CompressConfig,
    DatasetGuiState,
    DatasetSpec,
    TileConfig,
    TokenConfig,
)


class CompressDialog(QDialog):
    """Dialog for discovering and compressing TIFF datasets to HDF5.

    Presents semantic-level selection: pick datasets (left list) and
    channels (right list). Tiles, z-slices, and timepoints are shown
    as informational summaries with global configuration.
    """

    def __init__(self, parent=None, project_dir: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compress TIFF Dataset")
        self.setMinimumWidth(700)
        self.resize(750, 650)
        self._project_dir = project_dir

        self._datasets: list[DatasetSpec] = []
        self._gui_states: dict[str, DatasetGuiState] = {}
        self._all_channels: list[str] = []
        self._all_tiles: list[str] = []
        self._all_z_slices: list[str] = []
        self._all_timepoints: list[str] = []
        self._discovery_generation = 0

        self._build_ui()
        self._apply_style()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background-color: #1e1e1e; border: none; }"
        )
        outer.addWidget(scroll)

        content = QWidget()
        content.setStyleSheet("QWidget { background-color: #1e1e1e; }")
        scroll.setWidget(content)
        layout = QVBoxLayout(content)

        # ── Source ──
        src_group = QGroupBox("Source")
        src_layout = QVBoxLayout(src_group)

        row_src = QHBoxLayout()
        row_src.addWidget(QLabel("Directory:"))
        self._source_edit = QLineEdit()
        self._source_edit.setPlaceholderText(
            "Select a folder containing TIFFs..."
        )
        self._source_edit.setReadOnly(True)
        row_src.addWidget(self._source_edit, 1)
        btn_browse_src = QPushButton("Browse...")
        btn_browse_src.clicked.connect(self._on_browse_source)
        row_src.addWidget(btn_browse_src)
        src_layout.addLayout(row_src)

        row_out = QHBoxLayout()
        row_out.addWidget(QLabel("Output:"))
        self._output_edit = QLineEdit()
        self._output_edit.setPlaceholderText(
            "Defaults to source parent directory"
        )
        row_out.addWidget(self._output_edit, 1)
        btn_browse_out = QPushButton("Browse...")
        btn_browse_out.clicked.connect(self._on_browse_output)
        row_out.addWidget(btn_browse_out)
        src_layout.addLayout(row_out)

        layout.addWidget(src_group)

        # ── Discovery Mode ──
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Discovery:"))
        self._discovery_combo = QComboBox()
        self._discovery_combo.addItems(["Subdirectory", "Flat Directory"])
        self._discovery_combo.setToolTip(
            "Subdirectory: each child folder = one dataset.\n"
            "Flat Directory: groups files by stripping token patterns\n"
            "(channel, tile, etc.) from filenames."
        )
        self._discovery_combo.currentIndexChanged.connect(
            self._on_discovery_mode_changed
        )
        mode_row.addWidget(self._discovery_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # ── Datasets + Channels (side by side) ──
        lists_row = QHBoxLayout()

        # Left: datasets
        ds_group = QGroupBox("Datasets")
        ds_layout = QVBoxLayout(ds_group)

        ds_btn_row = QHBoxLayout()
        btn_ds_all = QPushButton("Select All")
        btn_ds_all.clicked.connect(self._on_select_all_datasets)
        btn_ds_none = QPushButton("Deselect All")
        btn_ds_none.clicked.connect(self._on_deselect_all_datasets)
        ds_btn_row.addWidget(btn_ds_all)
        ds_btn_row.addWidget(btn_ds_none)
        ds_btn_row.addStretch()
        self._ds_count_label = QLabel("")
        ds_btn_row.addWidget(self._ds_count_label)
        ds_layout.addLayout(ds_btn_row)

        self._ds_list = QListWidget()
        ds_layout.addWidget(self._ds_list)
        lists_row.addWidget(ds_group, 3)

        # Right: channels
        ch_group = QGroupBox("Channels")
        ch_layout = QVBoxLayout(ch_group)

        ch_btn_row = QHBoxLayout()
        btn_ch_all = QPushButton("Select All")
        btn_ch_all.clicked.connect(self._on_select_all_channels)
        btn_ch_none = QPushButton("Deselect All")
        btn_ch_none.clicked.connect(self._on_deselect_all_channels)
        ch_btn_row.addWidget(btn_ch_all)
        ch_btn_row.addWidget(btn_ch_none)
        ch_btn_row.addStretch()
        ch_layout.addLayout(ch_btn_row)

        self._ch_list = QListWidget()
        ch_layout.addWidget(self._ch_list)
        lists_row.addWidget(ch_group, 1)

        layout.addLayout(lists_row)

        # ── Discovery summary ──
        self._summary_label = QLabel("")
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        # ── Settings ──
        settings_group = QGroupBox("Settings")
        settings_layout = QVBoxLayout(settings_group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Z-Projection:"))
        self._z_combo = QComboBox()
        self._z_combo.addItems(["mip", "mean", "sum", "none"])
        row1.addWidget(self._z_combo)
        row1.addStretch()
        settings_layout.addLayout(row1)

        # Tile stitching
        self._stitch_check = QCheckBox("Tile Stitching")
        self._stitch_check.toggled.connect(self._on_stitch_toggled)
        settings_layout.addWidget(self._stitch_check)

        self._stitch_widget = QWidget()
        stitch_layout = QHBoxLayout(self._stitch_widget)
        stitch_layout.setContentsMargins(20, 0, 0, 0)
        stitch_layout.addWidget(QLabel("Rows:"))
        self._stitch_rows = QSpinBox()
        self._stitch_rows.setRange(1, 100)
        self._stitch_rows.setValue(1)
        stitch_layout.addWidget(self._stitch_rows)
        stitch_layout.addWidget(QLabel("Cols:"))
        self._stitch_cols = QSpinBox()
        self._stitch_cols.setRange(1, 100)
        self._stitch_cols.setValue(1)
        stitch_layout.addWidget(self._stitch_cols)
        stitch_layout.addWidget(QLabel("Pattern:"))
        self._stitch_type = QComboBox()
        self._stitch_type.addItems(
            ["row_by_row", "column_by_column", "snake_by_row", "snake_by_column"]
        )
        stitch_layout.addWidget(self._stitch_type)
        stitch_layout.addWidget(QLabel("Start:"))
        self._stitch_order = QComboBox()
        self._stitch_order.addItems(
            ["right_down", "right_up", "left_down", "left_up",
             "top_left", "top_right", "bottom_left", "bottom_right"]
        )
        stitch_layout.addWidget(self._stitch_order)
        stitch_layout.addStretch()
        self._stitch_widget.setVisible(False)
        settings_layout.addWidget(self._stitch_widget)

        layout.addWidget(settings_group)

        # ── Token Patterns (collapsible) ──
        self._token_group = QGroupBox("Advanced: Token Patterns")
        self._token_group.setCheckable(True)
        self._token_group.setChecked(False)
        token_layout = QVBoxLayout(self._token_group)

        self._tok_channel = QLineEdit(r"_ch(\d+)")
        self._tok_timepoint = QLineEdit(r"_t(\d+)")
        self._tok_zslice = QLineEdit(r"_z(\d+)")
        self._tok_tile = QLineEdit(r"_s(\d+)")

        for label_text, widget in [
            ("Channel:", self._tok_channel),
            ("Timepoint:", self._tok_timepoint),
            ("Z-slice:", self._tok_zslice),
            ("Tile:", self._tok_tile),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(80)
            row.addWidget(lbl)
            row.addWidget(widget)
            token_layout.addLayout(row)

        btn_rescan = QPushButton("Re-scan with new patterns")
        btn_rescan.clicked.connect(self._run_discovery)
        token_layout.addWidget(btn_rescan)

        layout.addWidget(self._token_group)
        self._token_group.toggled.connect(self._on_token_group_toggled)
        self._on_token_group_toggled(False)

        layout.addStretch()

        # ── Action buttons (pinned below scroll) ──
        action_row = QHBoxLayout()
        action_row.addStretch()
        self._btn_compress = QPushButton("Compress")
        self._btn_compress.setEnabled(False)
        self._btn_compress.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        action_row.addWidget(self._btn_compress)
        action_row.addWidget(btn_cancel)
        outer.addLayout(action_row)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def compress_config(self) -> CompressConfig:
        """Materialize all dialog state into a CompressConfig."""
        output_dir = None
        if self._output_edit.text().strip():
            output_dir = Path(self._output_edit.text().strip())

        selected_channels = set()
        for i in range(self._ch_list.count()):
            item = self._ch_list.item(i)
            if item.checkState() == Qt.Checked:
                selected_channels.add(item.data(Qt.UserRole))

        tile_config = None
        if self._stitch_check.isChecked():
            tile_config = TileConfig(
                grid_rows=self._stitch_rows.value(),
                grid_cols=self._stitch_cols.value(),
                grid_type=self._stitch_type.currentText(),
                order=self._stitch_order.currentText(),
            )

        # Sync dataset check states
        checked_names = set()
        for i in range(self._ds_list.count()):
            item = self._ds_list.item(i)
            if item.checkState() == Qt.Checked:
                checked_names.add(item.data(Qt.UserRole))

        gui_states = {}
        for ds in self._datasets:
            gui_states[ds.name] = DatasetGuiState(
                checked=ds.name in checked_names,
            )

        return CompressConfig(
            z_project_method=self._z_combo.currentText(),
            token_config=self._current_token_config(),
            output_dir=output_dir,
            selected_channels=selected_channels,
            tile_config=tile_config,
            datasets=list(self._datasets),
            gui_states=gui_states,
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_browse_source(self) -> None:
        start_dir = self._project_dir or ""
        path = QFileDialog.getExistingDirectory(
            self, "Select Source Directory", start_dir
        )
        if not path:
            return
        self._source_edit.setText(path)
        if not self._output_edit.text().strip():
            self._output_edit.setText(str(Path(path).parent))
        self._run_discovery()

    def _on_browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", self._output_edit.text()
        )
        if path:
            self._output_edit.setText(path)

    def _on_discovery_mode_changed(self, index: int) -> None:
        if self._source_edit.text().strip():
            self._run_discovery()

    def _on_stitch_toggled(self, checked: bool) -> None:
        self._stitch_widget.setVisible(checked)

    def _on_token_group_toggled(self, checked: bool) -> None:
        for child in self._token_group.findChildren(QWidget):
            if child is not self._token_group:
                child.setVisible(checked)

    def _on_select_all_datasets(self) -> None:
        self._set_list_check_state(self._ds_list, Qt.Checked)

    def _on_deselect_all_datasets(self) -> None:
        self._set_list_check_state(self._ds_list, Qt.Unchecked)

    def _on_select_all_channels(self) -> None:
        self._set_list_check_state(self._ch_list, Qt.Checked)

    def _on_deselect_all_channels(self) -> None:
        self._set_list_check_state(self._ch_list, Qt.Unchecked)

    def _set_list_check_state(
        self, list_widget: QListWidget, state: Qt.CheckState
    ) -> None:
        list_widget.blockSignals(True)
        for i in range(list_widget.count()):
            list_widget.item(i).setCheckState(state)
        list_widget.blockSignals(False)
        self._update_compress_button()

    # ------------------------------------------------------------------
    # Discovery + list population
    # ------------------------------------------------------------------

    def _current_token_config(self) -> TokenConfig:
        return TokenConfig(
            channel=self._tok_channel.text().strip() or None,
            timepoint=self._tok_timepoint.text().strip() or None,
            z_slice=self._tok_zslice.text().strip() or None,
            tile=self._tok_tile.text().strip() or None,
        )

    def _run_discovery(self) -> None:
        source = self._source_edit.text().strip()
        if not source:
            return

        self._discovery_generation += 1
        gen = self._discovery_generation

        root = Path(source)
        token_config = self._current_token_config()
        output_dir = None
        if self._output_edit.text().strip():
            output_dir = Path(self._output_edit.text().strip())

        from percell4.io.discovery import discover_by_subdirectory, discover_flat

        try:
            if self._discovery_combo.currentIndex() == 0:
                datasets = discover_by_subdirectory(
                    root, token_config, output_dir
                )
            else:
                datasets = discover_flat(root, token_config, output_dir)
        except Exception as e:
            self._ds_count_label.setText(f"Error: {e}")
            return

        if gen != self._discovery_generation:
            return

        self._datasets = datasets
        self._aggregate_tokens()
        self._populate_lists()

    def _aggregate_tokens(self) -> None:
        """Collect all unique channels, tiles, z-slices, timepoints."""
        channels: set[str] = set()
        tiles: set[str] = set()
        z_slices: set[str] = set()
        timepoints: set[str] = set()

        for ds in self._datasets:
            if ds.scan_result:
                channels.update(ds.scan_result.channels)
                tiles.update(ds.scan_result.tiles)
                z_slices.update(ds.scan_result.z_slices)
                timepoints.update(ds.scan_result.timepoints)
            else:
                for f in ds.files:
                    if "channel" in f.tokens:
                        channels.add(f.tokens["channel"])
                    if "tile" in f.tokens:
                        tiles.add(f.tokens["tile"])
                    if "z_slice" in f.tokens:
                        z_slices.add(f.tokens["z_slice"])
                    if "timepoint" in f.tokens:
                        timepoints.add(f.tokens["timepoint"])

        self._all_channels = sorted(channels, key=_sort_key)
        self._all_tiles = sorted(tiles, key=_sort_key)
        self._all_z_slices = sorted(z_slices, key=_sort_key)
        self._all_timepoints = sorted(timepoints, key=_sort_key)

    def _populate_lists(self) -> None:
        # ── Datasets ──
        self._ds_list.blockSignals(True)
        self._ds_list.clear()
        for ds in self._datasets:
            item = QListWidgetItem(ds.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, ds.name)
            self._ds_list.addItem(item)
        self._ds_list.blockSignals(False)

        n = len(self._datasets)
        self._ds_count_label.setText(
            f"{n} dataset{'s' if n != 1 else ''}"
        )

        # ── Channels ──
        self._ch_list.blockSignals(True)
        self._ch_list.clear()
        for ch in self._all_channels:
            item = QListWidgetItem(f"ch{ch}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, ch)
            self._ch_list.addItem(item)
        self._ch_list.blockSignals(False)

        # ── Summary ──
        parts = []
        if self._all_tiles:
            t = self._all_tiles
            parts.append(
                f"Tiles: {len(t)} (s{t[0]}–s{t[-1]})"
            )
        if self._all_z_slices:
            z = self._all_z_slices
            parts.append(f"Z-slices: {len(z)} (z{z[0]}–z{z[-1]})")
        if self._all_timepoints:
            tp = self._all_timepoints
            parts.append(
                f"Timepoints: {len(tp)} (t{tp[0]}–t{tp[-1]})"
            )
        if not parts:
            parts.append("No tiles, z-slices, or timepoints detected")
        self._summary_label.setText("    ".join(parts))

        # Auto-enable stitching if tiles detected
        if self._all_tiles and not self._stitch_check.isChecked():
            self._stitch_check.setChecked(True)

        self._update_compress_button()

    def _update_compress_button(self) -> None:
        any_ds = any(
            self._ds_list.item(i).checkState() == Qt.Checked
            for i in range(self._ds_list.count())
        )
        any_ch = any(
            self._ch_list.item(i).checkState() == Qt.Checked
            for i in range(self._ch_list.count())
        )
        self._btn_compress.setEnabled(any_ds and any_ch)

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------

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
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
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
            QPushButton:disabled { color: #666666; }
            QCheckBox { color: #e0e0e0; }
            QLabel { color: #cccccc; }
            QListWidget {
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                outline: none;
            }
            QListWidget::item { padding: 3px 4px; }
            QListWidget::item:selected {
                background-color: #2a4a6b;
                color: #ffffff;
            }
            QListWidget::item:hover:!selected {
                background-color: #2a2a2a;
            }
            QListWidget::indicator { width: 16px; height: 16px; }
            QListWidget::indicator:unchecked {
                border: 1px solid #555555;
                border-radius: 3px;
                background-color: #2a2a2a;
            }
            QListWidget::indicator:checked {
                border: 1px solid #4ea8de;
                border-radius: 3px;
                background-color: #4ea8de;
            }
        """)


def _sort_key(val: str) -> tuple[int, str]:
    """Sort token values numerically if possible, else alphabetically."""
    try:
        return (0, str(int(val)).zfill(10))
    except ValueError:
        return (1, val)
