"""Add Layer dialog for importing images/ROIs into a loaded HDF5 dataset.

Consolidates all layer-addition workflows:
- Single TIFF import (channel, segmentation, or mask)
- Batch TIFF discovery (same features as compress dialog) into an existing dataset
- ImageJ ROI .zip import (segmentation)
- Cellpose _seg.npy import (segmentation)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from percell4.application.use_cases.add_decay_to_dataset import (
    AppendReport,
    add_decay_to_dataset,
)
from percell4.domain.io.cross_format import (
    IntensityChannel,
    match_bin_to_intensity,
)
from percell4.domain.io.models import (
    FlimConfig,
    TileConfig,
    TokenConfig,
)
from percell4.gui.tcspc_tab_state import TcspcTabState


class AddLayerDialog(QDialog):
    """Dialog for adding layers to a loaded HDF5 dataset.

    Provides tabs for different import sources: single TIFF, batch TIFF
    discovery, ImageJ ROIs, and Cellpose segmentation files.
    """

    def __init__(self, parent, store, data_model, viewer_win) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Layer to Dataset")
        self.setMinimumWidth(700)
        # Cap the dialog height to a fraction of the screen so it never
        # exceeds the screen in any direction; per-tab scroll areas (TCSPC,
        # Discover TIFFs) handle content that overflows.
        self.resize(800, 700)
        if parent is not None and hasattr(parent, "screen"):
            try:
                screen_geom = parent.screen().availableGeometry()
                self.setMaximumHeight(int(screen_geom.height() * 0.9))
                self.setMaximumWidth(int(screen_geom.width() * 0.9))
            except Exception:  # noqa: BLE001
                pass

        self._store = store
        self._data_model = data_model
        self._viewer_win = viewer_win
        self._launcher = parent

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_single_tiff_tab(), "Single TIFF")
        self._tabs.addTab(self._build_batch_tiff_tab(), "Discover TIFFs")
        self._tabs.addTab(self._build_tcspc_tab(), "TCSPC (.bin)")
        self._tabs.addTab(self._build_roi_tab(), "ImageJ ROIs (.zip)")
        self._tabs.addTab(self._build_cellpose_tab(), "Cellpose (.npy)")
        layout.addWidget(self._tabs)

    # ------------------------------------------------------------------
    # Tab: Single TIFF
    # ------------------------------------------------------------------

    def _build_single_tiff_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # File picker
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("File:"))
        self._single_file_edit = QLineEdit()
        self._single_file_edit.setReadOnly(True)
        self._single_file_edit.setPlaceholderText("Select a TIFF file...")
        file_row.addWidget(self._single_file_edit, 1)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._on_browse_single_tiff)
        file_row.addWidget(btn_browse)
        layout.addLayout(file_row)

        # Name + type
        config_row = QHBoxLayout()
        config_row.addWidget(QLabel("Name:"))
        self._single_name_edit = QLineEdit()
        self._single_name_edit.setPlaceholderText("Layer name")
        config_row.addWidget(self._single_name_edit, 1)
        config_row.addWidget(QLabel("Type:"))
        self._single_type_combo = QComboBox()
        self._single_type_combo.addItems(["Channel", "Segmentation", "Mask"])
        config_row.addWidget(self._single_type_combo)
        layout.addLayout(config_row)

        layout.addStretch()

        btn_import = QPushButton("Import")
        btn_import.clicked.connect(self._on_import_single_tiff)
        layout.addWidget(btn_import)
        return tab

    def _on_browse_single_tiff(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select TIFF", "",
            "TIFF Files (*.tif *.tiff);;All Files (*)",
        )
        if path:
            self._single_file_edit.setText(path)
            if not self._single_name_edit.text().strip():
                self._single_name_edit.setText(Path(path).stem)

    def _on_import_single_tiff(self) -> None:
        path = self._single_file_edit.text().strip()
        if not path:
            return
        name = self._single_name_edit.text().strip()
        if not name:
            name = Path(path).stem
        layer_type = self._single_type_combo.currentText()

        try:
            import tifffile
            array = tifffile.imread(path)
            if array.ndim > 2:
                array = array[0] if array.ndim == 3 else array[0, 0]
            self._write_layer(name, layer_type, array)
            self._refresh_viewer()
            self.statusBar_msg(f"Added {layer_type.lower()} '{name}'")
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed:\n{e}")

    # ------------------------------------------------------------------
    # Tab: Discover TIFFs (mirrors Compress dialog layout)
    # ------------------------------------------------------------------

    def _build_batch_tiff_tab(self) -> QWidget:
        from qtpy.QtWidgets import QScrollArea

        tab = QWidget()
        outer = QVBoxLayout(tab)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)

        # ── Source ──
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Directory:"))
        self._batch_source_edit = QLineEdit()
        self._batch_source_edit.setReadOnly(True)
        self._batch_source_edit.setPlaceholderText("Select a folder containing TIFFs...")
        src_row.addWidget(self._batch_source_edit, 1)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._on_browse_batch)
        src_row.addWidget(btn_browse)
        layout.addLayout(src_row)

        # ── Discovery mode ──
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Discovery:"))
        self._batch_discovery_combo = QComboBox()
        self._batch_discovery_combo.addItems(["Subdirectory", "Flat Directory"])
        self._batch_discovery_combo.setToolTip(
            "Subdirectory: each child folder = one dataset.\n"
            "Flat Directory: groups files by stripping token patterns\n"
            "(channel, tile, etc.) from filenames."
        )
        self._batch_discovery_combo.currentIndexChanged.connect(
            lambda _: self._run_batch_discovery()
        )
        mode_row.addWidget(self._batch_discovery_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # ── Datasets + Channels (side by side) ──
        lists_row = QHBoxLayout()

        # Left: datasets
        ds_group = QGroupBox("Datasets")
        ds_layout = QVBoxLayout(ds_group)
        ds_btn_row = QHBoxLayout()
        btn_ds_all = QPushButton("Select All")
        btn_ds_all.clicked.connect(
            lambda: self._set_list_check(self._batch_ds_list, Qt.Checked)
        )
        btn_ds_none = QPushButton("Deselect All")
        btn_ds_none.clicked.connect(
            lambda: self._set_list_check(self._batch_ds_list, Qt.Unchecked)
        )
        ds_btn_row.addWidget(btn_ds_all)
        ds_btn_row.addWidget(btn_ds_none)
        ds_btn_row.addStretch()
        self._batch_ds_count = QLabel("")
        ds_btn_row.addWidget(self._batch_ds_count)
        ds_layout.addLayout(ds_btn_row)
        self._batch_ds_list = QListWidget()
        ds_layout.addWidget(self._batch_ds_list)
        lists_row.addWidget(ds_group, 3)

        # Right: channels with name + type
        ch_group = QGroupBox("Channels")
        ch_layout = QVBoxLayout(ch_group)
        ch_btn_row = QHBoxLayout()
        btn_ch_all = QPushButton("Select All")
        btn_ch_all.clicked.connect(self._on_batch_select_all_ch)
        btn_ch_none = QPushButton("Deselect All")
        btn_ch_none.clicked.connect(self._on_batch_deselect_all_ch)
        ch_btn_row.addWidget(btn_ch_all)
        ch_btn_row.addWidget(btn_ch_none)
        ch_btn_row.addStretch()
        ch_layout.addLayout(ch_btn_row)

        self._batch_ch_container = QVBoxLayout()
        ch_layout.addLayout(self._batch_ch_container)
        self._batch_channel_configs: dict[str, _ChannelRowWidgets] = {}
        lists_row.addWidget(ch_group, 2)

        layout.addLayout(lists_row)

        # ── Discovery summary ──
        self._batch_summary = QLabel("")
        self._batch_summary.setWordWrap(True)
        layout.addWidget(self._batch_summary)

        # ── Settings ──
        settings_group = QGroupBox("Settings")
        settings_layout = QVBoxLayout(settings_group)

        z_row = QHBoxLayout()
        z_row.addWidget(QLabel("Z-Projection:"))
        self._batch_z_combo = QComboBox()
        self._batch_z_combo.addItems(["mip", "mean", "sum", "none"])
        z_row.addWidget(self._batch_z_combo)
        z_row.addStretch()
        settings_layout.addLayout(z_row)

        # Tile stitching
        self._batch_stitch_check = QCheckBox("Tile Stitching")
        self._batch_stitch_check.toggled.connect(
            lambda c: self._batch_stitch_widget.setVisible(c)
        )
        settings_layout.addWidget(self._batch_stitch_check)

        self._batch_stitch_widget = QWidget()
        stitch_layout = QHBoxLayout(self._batch_stitch_widget)
        stitch_layout.setContentsMargins(20, 0, 0, 0)
        stitch_layout.addWidget(QLabel("Rows:"))
        self._batch_stitch_rows = QSpinBox()
        self._batch_stitch_rows.setRange(1, 100)
        self._batch_stitch_rows.setValue(1)
        stitch_layout.addWidget(self._batch_stitch_rows)
        stitch_layout.addWidget(QLabel("Cols:"))
        self._batch_stitch_cols = QSpinBox()
        self._batch_stitch_cols.setRange(1, 100)
        self._batch_stitch_cols.setValue(1)
        stitch_layout.addWidget(self._batch_stitch_cols)
        stitch_layout.addWidget(QLabel("Pattern:"))
        self._batch_stitch_type = QComboBox()
        self._batch_stitch_type.addItems(
            ["row_by_row", "column_by_column", "snake_by_row", "snake_by_column"]
        )
        stitch_layout.addWidget(self._batch_stitch_type)
        stitch_layout.addWidget(QLabel("Start:"))
        self._batch_stitch_order = QComboBox()
        self._batch_stitch_order.addItems(
            ["right_down", "right_up", "left_down", "left_up",
             "top_left", "top_right", "bottom_left", "bottom_right"]
        )
        stitch_layout.addWidget(self._batch_stitch_order)
        stitch_layout.addStretch()
        self._batch_stitch_widget.setVisible(False)
        settings_layout.addWidget(self._batch_stitch_widget)

        layout.addWidget(settings_group)

        # ── Token Patterns (collapsible) ──
        self._batch_token_group = QGroupBox("Advanced: Token Patterns")
        self._batch_token_group.setCheckable(True)
        self._batch_token_group.setChecked(False)
        token_layout = QVBoxLayout(self._batch_token_group)

        self._batch_tok_channel = QLineEdit(r"_ch(\d+)")
        self._batch_tok_timepoint = QLineEdit(r"_t(\d+)")
        self._batch_tok_zslice = QLineEdit(r"_z(\d+)")
        self._batch_tok_tile = QLineEdit(r"_s(\d+)")

        for label_text, widget in [
            ("Channel:", self._batch_tok_channel),
            ("Timepoint:", self._batch_tok_timepoint),
            ("Z-slice:", self._batch_tok_zslice),
            ("Tile:", self._batch_tok_tile),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(80)
            row.addWidget(lbl)
            row.addWidget(widget)
            token_layout.addLayout(row)

        btn_rescan = QPushButton("Re-scan with new patterns")
        btn_rescan.clicked.connect(self._run_batch_discovery)
        token_layout.addWidget(btn_rescan)

        layout.addWidget(self._batch_token_group)
        self._batch_token_group.toggled.connect(
            lambda checked: [
                w.setVisible(checked)
                for w in self._batch_token_group.findChildren(QWidget)
                if w is not self._batch_token_group
            ]
        )
        # Hide initially
        for w in self._batch_token_group.findChildren(QWidget):
            if w is not self._batch_token_group:
                w.setVisible(False)

        layout.addStretch()

        # ── Import button (pinned below scroll) ──
        btn_import = QPushButton("Import Selected Channels")
        btn_import.clicked.connect(self._on_import_batch)
        outer.addWidget(btn_import)

        return tab

    def _on_browse_batch(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Source Directory")
        if not path:
            return
        self._batch_source_edit.setText(path)
        self._run_batch_discovery()

    def _batch_token_config(self) -> TokenConfig:
        from percell4.domain.io.models import TokenConfig
        return TokenConfig(
            channel=self._batch_tok_channel.text().strip() or None,
            timepoint=self._batch_tok_timepoint.text().strip() or None,
            z_slice=self._batch_tok_zslice.text().strip() or None,
            tile=self._batch_tok_tile.text().strip() or None,
        )

    def _run_batch_discovery(self) -> None:
        source = self._batch_source_edit.text().strip()
        if not source:
            return

        root = Path(source)
        token_config = self._batch_token_config()

        from percell4.domain.io.discovery import discover_by_subdirectory, discover_flat

        try:
            if self._batch_discovery_combo.currentIndex() == 0:
                datasets = discover_by_subdirectory(root, token_config)
            else:
                datasets = discover_flat(root, token_config)
        except Exception as e:
            self._batch_summary.setText(f"Error: {e}")
            return

        self._batch_datasets = datasets

        # Aggregate tokens across all datasets
        all_channels: set[str] = set()
        all_tiles: set[str] = set()
        all_z: set[str] = set()
        all_tp: set[str] = set()
        for ds in datasets:
            if ds.scan_result:
                all_channels.update(ds.scan_result.channels)
                all_tiles.update(ds.scan_result.tiles)
                all_z.update(ds.scan_result.z_slices)
                all_tp.update(ds.scan_result.timepoints)

        channels = sorted(all_channels, key=_sort_key)
        tiles = sorted(all_tiles, key=_sort_key)
        z_slices = sorted(all_z, key=_sort_key)
        timepoints = sorted(all_tp, key=_sort_key)

        # Populate dataset list
        self._batch_ds_list.blockSignals(True)
        self._batch_ds_list.clear()
        for ds in datasets:
            item = QListWidgetItem(ds.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, ds.name)
            self._batch_ds_list.addItem(item)
        self._batch_ds_list.blockSignals(False)
        self._batch_ds_count.setText(
            f"{len(datasets)} dataset{'s' if len(datasets) != 1 else ''}"
        )

        # Populate channel rows
        while self._batch_ch_container.count():
            child = self._batch_ch_container.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._batch_channel_configs.clear()

        for ch in channels:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 2, 0, 2)

            cb = QCheckBox(f"ch{ch}")
            cb.setChecked(True)
            row.addWidget(cb)

            name_edit = QLineEdit(f"ch{ch}")
            name_edit.setFixedWidth(100)
            row.addWidget(name_edit)

            type_combo = QComboBox()
            type_combo.addItems(["Channel", "Segmentation", "Mask"])
            type_combo.setFixedWidth(110)
            row.addWidget(type_combo)
            row.addStretch()

            self._batch_ch_container.addWidget(row_widget)
            self._batch_channel_configs[ch] = _ChannelRowWidgets(
                checkbox=cb, name_edit=name_edit, type_combo=type_combo,
            )

        # Summary
        parts = []
        if tiles:
            parts.append(f"Tiles: {len(tiles)} (s{tiles[0]}\u2013s{tiles[-1]})")
        if z_slices:
            parts.append(f"Z-slices: {len(z_slices)} (z{z_slices[0]}\u2013z{z_slices[-1]})")
        if timepoints:
            parts.append(f"Timepoints: {len(timepoints)} (t{timepoints[0]}\u2013t{timepoints[-1]})")
        if not parts:
            parts.append("No tiles, z-slices, or timepoints detected")
        self._batch_summary.setText("    ".join(parts))

        if tiles and not self._batch_stitch_check.isChecked():
            self._batch_stitch_check.setChecked(True)

    def _on_batch_select_all_ch(self) -> None:
        for cfg in self._batch_channel_configs.values():
            cfg.checkbox.setChecked(True)

    def _on_batch_deselect_all_ch(self) -> None:
        for cfg in self._batch_channel_configs.values():
            cfg.checkbox.setChecked(False)

    def _set_list_check(self, list_widget: QListWidget, state) -> None:
        list_widget.blockSignals(True)
        for i in range(list_widget.count()):
            list_widget.item(i).setCheckState(state)
        list_widget.blockSignals(False)

    def _on_import_batch(self) -> None:
        if not hasattr(self, "_batch_datasets"):
            return


        # Gather selected datasets
        selected_ds_names: set[str] = set()
        for i in range(self._batch_ds_list.count()):
            item = self._batch_ds_list.item(i)
            if item.checkState() == Qt.Checked:
                selected_ds_names.add(item.data(Qt.UserRole))

        # Gather selected channels
        selected = {}
        for ch_id, cfg in self._batch_channel_configs.items():
            if cfg.checkbox.isChecked():
                selected[ch_id] = (
                    cfg.name_edit.text().strip() or f"ch{ch_id}",
                    cfg.type_combo.currentText(),
                )

        if not selected_ds_names or not selected:
            return

        tile_config = None
        if self._batch_stitch_check.isChecked():
            tile_config = TileConfig(
                grid_rows=self._batch_stitch_rows.value(),
                grid_cols=self._batch_stitch_cols.value(),
                grid_type=self._batch_stitch_type.currentText(),
                order=self._batch_stitch_order.currentText(),
            )

        token_config = self._batch_token_config()

        from qtpy.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            import tifffile

            from percell4.domain.io.assembler import assemble_tiles
            from percell4.domain.io.scanner import FileScanner

            imported_count = 0
            for ds in self._batch_datasets:
                if ds.name not in selected_ds_names:
                    continue

                # Scan this dataset's files
                scanner = FileScanner(token_config)
                if ds.files:
                    scan = scanner.scan(files=[str(f.path) if hasattr(f, "path") else str(f) for f in ds.files])
                else:
                    scan = scanner.scan(path=ds.source_dir)

                # Group by channel
                from collections import defaultdict
                by_channel: dict[str, list] = defaultdict(list)
                for f in scan.files:
                    ch = f.tokens.get("channel", "")
                    by_channel[ch].append(f)

                for ch_id in sorted(selected.keys(), key=_sort_key):
                    if ch_id not in by_channel:
                        continue

                    files = by_channel[ch_id]
                    name, layer_type = selected[ch_id]

                    # Group by tile, load and stitch
                    tile_groups: dict[int, np.ndarray] = {}
                    for f in files:
                        tile_idx = int(f.tokens.get("tile", "0"))
                        img = tifffile.imread(str(f.path))
                        if img.ndim > 2:
                            img = img[0] if img.ndim == 3 else img[0, 0]
                        tile_groups[tile_idx] = img

                    if tile_config and len(tile_groups) > 1:
                        array = assemble_tiles(
                            tile_groups,
                            grid_rows=tile_config.grid_rows,
                            grid_cols=tile_config.grid_cols,
                            grid_type=tile_config.grid_type,
                            order=tile_config.order,
                        )
                    else:
                        array = next(iter(tile_groups.values()))

                    self._write_layer(name, layer_type, array)
                    imported_count += 1

            self._refresh_viewer()
            self.statusBar_msg(f"Imported {imported_count} layers")
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Import failed:\n{e}")
        finally:
            QApplication.restoreOverrideCursor()

    # ------------------------------------------------------------------
    # Tab: ImageJ ROIs
    # ------------------------------------------------------------------

    def _build_roi_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("ROI .zip file:"))
        self._roi_file_edit = QLineEdit()
        self._roi_file_edit.setReadOnly(True)
        file_row.addWidget(self._roi_file_edit, 1)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._on_browse_roi)
        file_row.addWidget(btn_browse)
        layout.addLayout(file_row)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Layer name:"))
        self._roi_name_edit = QLineEdit()
        self._roi_name_edit.setPlaceholderText("auto-generated from ROI count")
        name_row.addWidget(self._roi_name_edit)
        layout.addLayout(name_row)

        layout.addStretch()

        btn_import = QPushButton("Import ROIs as Segmentation")
        btn_import.clicked.connect(self._on_import_roi)
        layout.addWidget(btn_import)
        return tab

    def _on_browse_roi(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ImageJ ROI File", "",
            "ROI Files (*.zip);;All Files (*)",
        )
        if path:
            self._roi_file_edit.setText(path)

    def _on_import_roi(self) -> None:
        path = self._roi_file_edit.text().strip()
        if not path:
            return

        shape = self._get_image_shape()
        if shape is None:
            QMessageBox.warning(self, "Error", "Load an image first to determine shape")
            return

        try:
            from percell4.adapters.roi_import import import_imagej_rois
            labels = import_imagej_rois(path, shape)
            n_cells = int(labels.max())
            name = self._roi_name_edit.text().strip() or f"roi_import_{n_cells}"
            self._store.write_labels(name, labels)
            if self._viewer_win is not None:
                self._viewer_win.add_labels(labels, name=name)
            self._data_model.set_active_segmentation(name)
            self.statusBar_msg(f"Imported {n_cells} ROIs as '{name}'")
            self.accept()
        except ImportError:
            QMessageBox.warning(
                self, "Missing Dependency",
                "roifile package required.\nInstall: pip install roifile",
            )
        except Exception as e:
            QMessageBox.warning(self, "Error", f"ROI import error:\n{e}")

    # ------------------------------------------------------------------
    # Tab: Cellpose _seg.npy
    # ------------------------------------------------------------------

    def _build_cellpose_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("_seg.npy file:"))
        self._cp_file_edit = QLineEdit()
        self._cp_file_edit.setReadOnly(True)
        file_row.addWidget(self._cp_file_edit, 1)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._on_browse_cellpose)
        file_row.addWidget(btn_browse)
        layout.addLayout(file_row)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Layer name:"))
        self._cp_name_edit = QLineEdit()
        self._cp_name_edit.setPlaceholderText("auto-generated from cell count")
        name_row.addWidget(self._cp_name_edit)
        layout.addLayout(name_row)

        layout.addStretch()

        btn_import = QPushButton("Import as Segmentation")
        btn_import.clicked.connect(self._on_import_cellpose)
        layout.addWidget(btn_import)
        return tab

    def _on_browse_cellpose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Cellpose Segmentation", "",
            "Numpy Files (*.npy);;All Files (*)",
        )
        if path:
            self._cp_file_edit.setText(path)

    def _on_import_cellpose(self) -> None:
        path = self._cp_file_edit.text().strip()
        if not path:
            return
        try:
            from percell4.adapters.roi_import import import_cellpose_seg
            labels = import_cellpose_seg(path)
            n_cells = int(labels.max())
            name = self._cp_name_edit.text().strip() or f"cellpose_import_{n_cells}"
            self._store.write_labels(name, labels)
            if self._viewer_win is not None:
                self._viewer_win.add_labels(labels, name=name)
            self._data_model.set_active_segmentation(name)
            self.statusBar_msg(f"Imported {n_cells} cells as '{name}'")
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Import error:\n{e}")

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _write_layer(self, name: str, layer_type: str, array: np.ndarray) -> None:
        """Write an array to the store as the specified layer type."""
        if layer_type == "Channel":
            array = array.astype(np.float32)
            try:
                existing = self._store.read_array("intensity")
                if existing.ndim == 2:
                    stacked = np.stack([existing, array], axis=0)
                else:
                    stacked = np.concatenate(
                        [existing, array[np.newaxis]], axis=0
                    )
                self._store.write_array(
                    "intensity", stacked, attrs={"dims": ["C", "H", "W"]},
                )
                meta = self._store.metadata
                names = list(meta.get("channel_names", []))
                names.append(name)
                self._store.set_metadata({
                    "channel_names": names,
                    "n_channels": len(names),
                })
            except KeyError:
                self._store.write_array(
                    "intensity", array, attrs={"dims": ["H", "W"]},
                )
                self._store.set_metadata({
                    "channel_names": [name],
                    "n_channels": 1,
                })
        elif layer_type == "Segmentation":
            self._store.write_labels(name, array)
        elif layer_type == "Mask":
            binary = (array > 0).astype(np.uint8)
            self._store.write_mask(name, binary)

    def _refresh_viewer(self) -> None:
        """Refresh the viewer and data tab from the store."""
        if hasattr(self._launcher, "_update_data_tab_from_store"):
            self._launcher._update_data_tab_from_store()
        if hasattr(self._launcher, "_populate_viewer_from_store"):
            self._launcher._populate_viewer_from_store()

    def _get_image_shape(self) -> tuple[int, int] | None:
        """Get the (H, W) shape from the current dataset's intensity."""
        try:
            intensity = self._store.read_array("intensity")
            if intensity.ndim == 2:
                return intensity.shape
            return intensity.shape[-2:]
        except (KeyError, Exception):
            return None

    def statusBar_msg(self, msg: str) -> None:
        if hasattr(self._launcher, "statusBar"):
            self._launcher.statusBar().showMessage(msg)

    # ------------------------------------------------------------------
    # Tab: TCSPC (.bin) append
    # ------------------------------------------------------------------

    def _build_tcspc_tab(self) -> QWidget:
        """Add TCSPC `.bin` files to the existing dataset's `/decay/<channel>`.

        UI conforms to the Compress-TIFF-Datasets dialog conventions:
        - "Tile Stitching" checkbox toggles a row of Rows / Cols / Pattern
          / Start controls (raw-string values, same labels and widget shape
          as compress_dialog so the user only learns the controls once).
        - "FLIM .bin Parameters" QGroupBox carries laser frequency, X/Y/T
          dims, dtype, dim order, header bytes, and per-channel
          calibration (phase / modulation per existing TIFF channel).
        - The mapping table is one row per existing TIFF channel showing
          how many `.bin` tiles will stitch into it. `.bin` files are
          always exported as individual tiles, so listing tiles
          individually was noise.

        The whole tab is wrapped in a QScrollArea — same pattern as
        ``_build_batch_tiff_tab`` — because the FLIM Parameters group
        with per-channel calibration grows past the dialog height once
        an experiment has 3+ channels.
        """
        from qtpy.QtWidgets import QScrollArea

        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        widget = QWidget()
        scroll.setWidget(widget)
        layout = QVBoxLayout(widget)

        # ── Source directory ────────────────────────────────────────
        dir_row = QHBoxLayout()
        self._tcspc_dir_edit = QLineEdit()
        self._tcspc_dir_edit.setPlaceholderText(
            "Source directory containing .bin files…"
        )
        dir_row.addWidget(self._tcspc_dir_edit)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_tcspc_browse)
        dir_row.addWidget(browse_btn)
        layout.addLayout(dir_row)

        # Token matching is direct equality between the channel's token
        # (set per-row in the table dropdown) and the .bin filename's
        # ``_ch(\\d+)`` token. No padding/offset transformation — if your
        # channel and bin token strings don't match exactly, fix the
        # filenames or pick a different token from the dropdown.

        # ── Tile Stitching (matches compress_dialog convention) ─────
        self._tcspc_stitch_check = QCheckBox("Tile Stitching")
        self._tcspc_stitch_check.toggled.connect(self._on_tcspc_stitch_toggled)
        layout.addWidget(self._tcspc_stitch_check)

        self._tcspc_stitch_widget = QWidget()
        stitch_layout = QHBoxLayout(self._tcspc_stitch_widget)
        stitch_layout.setContentsMargins(20, 0, 0, 0)
        stitch_layout.addWidget(QLabel("Rows:"))
        self._tcspc_stitch_rows = QSpinBox()
        self._tcspc_stitch_rows.setRange(1, 100)
        self._tcspc_stitch_rows.setValue(1)
        stitch_layout.addWidget(self._tcspc_stitch_rows)
        stitch_layout.addWidget(QLabel("Cols:"))
        self._tcspc_stitch_cols = QSpinBox()
        self._tcspc_stitch_cols.setRange(1, 100)
        self._tcspc_stitch_cols.setValue(1)
        stitch_layout.addWidget(self._tcspc_stitch_cols)
        stitch_layout.addWidget(QLabel("Pattern:"))
        self._tcspc_stitch_type = QComboBox()
        self._tcspc_stitch_type.addItems(
            ["row_by_row", "column_by_column", "snake_by_row", "snake_by_column"]
        )
        stitch_layout.addWidget(self._tcspc_stitch_type)
        stitch_layout.addWidget(QLabel("Start:"))
        self._tcspc_stitch_order = QComboBox()
        self._tcspc_stitch_order.addItems(
            [
                "right_down", "right_up", "left_down", "left_up",
                "top_left", "top_right", "bottom_left", "bottom_right",
            ]
        )
        stitch_layout.addWidget(self._tcspc_stitch_order)
        stitch_layout.addStretch()
        self._tcspc_stitch_widget.setVisible(False)
        layout.addWidget(self._tcspc_stitch_widget)

        # ── Rotation + Flip (LASX vs TIFF orientation) ──────────────
        # Both transforms apply to /decay/<ch> only (never /intensity).
        # T-axis untouched in both cases, so per-pixel decay curves and
        # the raw phasor histogram are preserved. Rotation runs first,
        # then flip — when both are non-trivial the user can compose
        # them. The phasor plot's intensity weighting derives from
        # /decay itself (flim_panel.py, apply_wavelet.py), so these
        # transforms are safe for FLIM analysis.
        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("Rotate stitched array:"))
        self._tcspc_rotation_combo = QComboBox()
        self._tcspc_rotation_combo.addItem("None", 0)
        self._tcspc_rotation_combo.addItem("90° CCW", 1)
        self._tcspc_rotation_combo.addItem("180°", 2)
        self._tcspc_rotation_combo.addItem("90° CW", 3)
        rot_row.addWidget(self._tcspc_rotation_combo)
        rot_row.addWidget(QLabel("Flip:"))
        self._tcspc_flip_combo = QComboBox()
        # ``None`` is no flip. axis=0 mirrors top↔bottom (np.flipud).
        # axis=1 mirrors left↔right (np.fliplr).
        self._tcspc_flip_combo.addItem("None", -1)
        self._tcspc_flip_combo.addItem("Vertical (top ↔ bottom)", 0)
        self._tcspc_flip_combo.addItem("Horizontal (left ↔ right)", 1)
        rot_row.addWidget(self._tcspc_flip_combo)
        rot_row.addStretch()
        layout.addLayout(rot_row)

        # ── Debug: write .bin-derived intensity as napari layers ────
        # Off by default — when checked, after Append the dialog computes
        # the per-pixel sum over T for each /decay/<ch> just written and
        # appends it to /intensity as a new channel "<ch>_bin". The user
        # can then visually overlay it against the existing TIFF intensity
        # to check spatial alignment / verify the stitch matches compress.
        self._tcspc_debug_intensity_check = QCheckBox(
            "Debug: also write .bin-derived intensity as napari layer(s)"
        )
        self._tcspc_debug_intensity_check.setToolTip(
            "Adds <channel>_bin layers to /intensity for visual comparison "
            "against the existing channel layer. Off by default — turn on "
            "when troubleshooting an apparent stitch mismatch."
        )
        layout.addWidget(self._tcspc_debug_intensity_check)

        # ── FLIM .bin Parameters (matches compress_dialog convention) ──
        self._tcspc_flim_group = QGroupBox("FLIM .bin Parameters")
        self._tcspc_flim_group.setCheckable(True)
        self._tcspc_flim_group.setChecked(False)
        self._tcspc_flim_group.setToolTip(
            "Parameters for raw binary TCSPC histogram (.bin) files.\n"
            "Per-channel calibration is persisted to /metadata so phasor\n"
            "computation can use it later."
        )
        flim_layout = QFormLayout(self._tcspc_flim_group)

        self._tcspc_flim_freq = QDoubleSpinBox()
        self._tcspc_flim_freq.setRange(0.1, 1000.0)
        self._tcspc_flim_freq.setValue(80.0)
        self._tcspc_flim_freq.setDecimals(1)
        self._tcspc_flim_freq.setSuffix(" MHz")
        flim_layout.addRow("Laser frequency:", self._tcspc_flim_freq)

        self._tcspc_bin_x = QSpinBox()
        self._tcspc_bin_x.setRange(1, 10000)
        self._tcspc_bin_x.setValue(512)
        flim_layout.addRow("X dimension:", self._tcspc_bin_x)

        self._tcspc_bin_y = QSpinBox()
        self._tcspc_bin_y.setRange(1, 10000)
        self._tcspc_bin_y.setValue(512)
        flim_layout.addRow("Y dimension:", self._tcspc_bin_y)

        self._tcspc_bin_t = QSpinBox()
        self._tcspc_bin_t.setRange(1, 4096)
        self._tcspc_bin_t.setValue(132)
        flim_layout.addRow("Time bins:", self._tcspc_bin_t)

        self._tcspc_bin_dtype = QComboBox()
        self._tcspc_bin_dtype.addItems(["uint32", "uint16", "float32", "uint8"])
        flim_layout.addRow("Data type:", self._tcspc_bin_dtype)

        self._tcspc_bin_dim_order = QComboBox()
        self._tcspc_bin_dim_order.addItems(["YXT", "XYT", "TYX"])
        flim_layout.addRow("Dimension order:", self._tcspc_bin_dim_order)

        self._tcspc_bin_header = QSpinBox()
        self._tcspc_bin_header.setRange(0, 10000)
        self._tcspc_bin_header.setValue(0)
        self._tcspc_bin_header.setSpecialValueText("Auto-detect")
        flim_layout.addRow("Header bytes:", self._tcspc_bin_header)

        cal_label = QLabel("Per-channel calibration (phase / modulation):")
        flim_layout.addRow(cal_label)
        self._tcspc_flim_cal_container = QVBoxLayout()
        flim_layout.addRow(self._tcspc_flim_cal_container)
        self._tcspc_channel_calibrations: dict[str, _TcspcCalibration] = {}

        self._tcspc_flim_group.toggled.connect(self._on_tcspc_flim_group_toggled)
        layout.addWidget(self._tcspc_flim_group)
        self._on_tcspc_flim_group_toggled(False)

        # ── Scan + match button ─────────────────────────────────────
        scan_row = QHBoxLayout()
        scan_btn = QPushButton("Scan && Match")
        scan_btn.clicked.connect(self._on_tcspc_scan)
        scan_row.addWidget(scan_btn)
        scan_row.addStretch()
        layout.addLayout(scan_row)

        # ── Channel mapping table (one row per channel) ─────────────
        # Column 1 (.bin token) is editable: user types the channel token
        # that .bin filenames carry for this TIFF channel. Editing this
        # cell re-runs the match so the table refreshes immediately.
        self._tcspc_table = QTableWidget(0, 5)
        self._tcspc_table.setHorizontalHeaderLabels(
            [
                "Channel",
                ".bin token",
                "Matched .bin tiles",
                "Replace existing",
                "Status",
            ]
        )
        self._tcspc_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._tcspc_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self._tcspc_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )
        layout.addWidget(self._tcspc_table)

        # ── Counter + accept ────────────────────────────────────────
        accept_row = QHBoxLayout()
        self._tcspc_counter = QLabel("No bin files scanned.")
        accept_row.addWidget(self._tcspc_counter)
        accept_row.addStretch()
        self._tcspc_accept_btn = QPushButton("Append decay layers")
        self._tcspc_accept_btn.setEnabled(False)
        self._tcspc_accept_btn.clicked.connect(self._on_tcspc_accept)
        accept_row.addWidget(self._tcspc_accept_btn)
        layout.addLayout(accept_row)

        # State + cached bin paths
        self._tcspc_state = TcspcTabState()
        self._tcspc_bin_files: list[Path] = []

        return tab

    def _on_tcspc_browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select .bin source directory")
        if d:
            self._tcspc_dir_edit.setText(d)

    def _on_tcspc_stitch_toggled(self, checked: bool) -> None:
        self._tcspc_stitch_widget.setVisible(checked)

    def _on_tcspc_flim_group_toggled(self, checked: bool) -> None:
        for child in self._tcspc_flim_group.findChildren(QWidget):
            if child is not self._tcspc_flim_group:
                child.setVisible(checked)

    def _on_tcspc_scan(self) -> None:
        d = self._tcspc_dir_edit.text().strip()
        if not d:
            self.statusBar_msg("Pick a directory first.")
            return
        source = Path(d)
        if not source.is_dir():
            self.statusBar_msg(f"Not a directory: {source}")
            return
        bins = sorted(p for p in source.rglob("*.bin") if p.is_file())
        if not bins:
            self._tcspc_bin_files = []
            self._tcspc_table.setRowCount(0)
            self._tcspc_counter.setText("No .bin files found.")
            self._tcspc_accept_btn.setEnabled(False)
            return
        self._tcspc_bin_files = bins

        # Discover the set of distinct channel tokens actually present in
        # the scanned .bin filenames — this becomes the dropdown options
        # in the table's ".bin token" column. Sorted numerically when
        # possible so "1", "2", "10" come out as 1, 2, 10 rather than
        # 1, 10, 2.
        self._tcspc_available_bin_tokens = self._tcspc_discover_bin_tokens(bins)

        # Pre-fill stitching widgets from /metadata if compress recorded
        # the original TileConfig there — keeps add-layer's tile placement
        # byte-identical to compress's. Falls back to compress's UI defaults
        # when the metadata is absent (older .h5 files).
        self._tcspc_seed_stitching_from_metadata()

        # Build IntensityChannel records from store metadata (matches U3 logic)
        intensity = self._tcspc_intensity_channels()
        existing_decay = self._store.list_groups("decay")
        self._tcspc_state.set_intensity(intensity, existing_decay)
        # Rebuild calibration widgets to mirror existing channels
        self._tcspc_rebuild_calibration_widgets([c.name for c in intensity])

        self._tcspc_run_match()

    def _tcspc_seed_stitching_from_metadata(self) -> None:
        """Pre-fill the Tile Stitching controls from /metadata if present.

        Compress writes ``stitch_grid_rows``, ``stitch_grid_cols``,
        ``stitch_grid_type``, ``stitch_order`` to /metadata when it imports
        with stitching. Reading those back here means picking ``Auto: zero
        pad with offset`` and clicking Scan & Match against a compress-
        produced .h5 will replicate the exact tile placement compress used,
        so the resulting decay aligns with the existing intensity / mask /
        labels and the spatial Filtered phasor matches.
        """
        meta = self._store.metadata
        rows = meta.get("stitch_grid_rows")
        cols = meta.get("stitch_grid_cols")
        if rows is None or cols is None:
            return
        # Only enable the stitching checkbox when the dataset actually has
        # a multi-tile grid; 1×1 means single-tile, no stitching needed.
        try:
            rows = int(rows)
            cols = int(cols)
        except (TypeError, ValueError):
            return
        if rows * cols <= 1:
            return
        self._tcspc_stitch_check.setChecked(True)
        self._tcspc_stitch_rows.setValue(rows)
        self._tcspc_stitch_cols.setValue(cols)
        grid_type = meta.get("stitch_grid_type")
        if grid_type:
            idx = self._tcspc_stitch_type.findText(str(grid_type))
            if idx >= 0:
                self._tcspc_stitch_type.setCurrentIndex(idx)
        order = meta.get("stitch_order")
        if order:
            idx = self._tcspc_stitch_order.findText(str(order))
            if idx >= 0:
                self._tcspc_stitch_order.setCurrentIndex(idx)
        self.statusBar_msg(
            f"Pre-filled stitching from dataset metadata: "
            f"{rows}×{cols} {grid_type or ''} {order or ''}".strip()
        )

    def _tcspc_discover_bin_tokens(self, bin_files: list[Path]) -> list[str]:
        """Return the distinct channel tokens parsed from .bin filenames.

        Uses the default ``TokenConfig`` channel pattern (``_ch(\\d+)``).
        Tokens are de-duplicated and sorted numerically when possible.
        """
        import re
        config = TokenConfig()
        if not config.channel:
            return []
        tokens: set[str] = set()
        for p in bin_files:
            m = re.search(config.channel, p.stem)
            if m:
                tokens.add(m.group(1))

        def _sort(t: str) -> tuple[int, str]:
            try:
                return (0, f"{int(t):020d}")
            except ValueError:
                return (1, t)

        return sorted(tokens, key=_sort)

    def _tcspc_intensity_channels(self) -> list[IntensityChannel]:
        """Build IntensityChannel list, seeding the cross-format token.

        Direct token equality only — no pad/offset transformation. Seeding
        order:

        1. User override stored in ``_tcspc_channel_token_overrides`` (set
           when the user picks from the table's dropdown).
        2. Positional pick from the actually-discovered tokens —
           ``available[i]`` for the i-th channel. This makes the common
           case (3 channels, .bin files with 3 tokens like ``00 / 01 /
           02``) match correctly without any manual setup.
        3. Channel name's digit suffix as a final fallback (covers the
           ``ch00`` / ``ch01`` / ``ch02`` channel-name convention).
        4. Empty string if nothing else applies — user picks via dropdown.
        """
        meta = self._store.metadata
        channel_names = list(meta.get("channel_names", []))
        base_stems = list(meta.get("channel_base_stems", []))
        if not hasattr(self, "_tcspc_channel_token_overrides"):
            self._tcspc_channel_token_overrides: dict[str, str] = {}
        available = getattr(self, "_tcspc_available_bin_tokens", []) or []

        out = []
        for i, name in enumerate(channel_names):
            import re
            override = self._tcspc_channel_token_overrides.get(name)
            if override is not None:
                token = override
            else:
                # Positional pick from discovered tokens — most reliable
                # for the typical case where N .bin tokens align 1:1 with
                # N intensity channels.
                if i < len(available):
                    token = available[i]
                else:
                    m = re.search(r"(\d+)$", name)
                    token = m.group(1) if m else ""
                self._tcspc_channel_token_overrides[name] = token
            base_stem = base_stems[i] if i < len(base_stems) else None
            out.append(IntensityChannel(name=name, token=token, base_stem=base_stem))
        return out

    def _tcspc_rebuild_calibration_widgets(self, channel_names: list[str]) -> None:
        """Create one (phase, modulation) row per existing TIFF channel.

        Pre-fills phase / modulation spinboxes from existing
        ``/metadata.flim_cal_phase_<channel>`` and
        ``flim_cal_mod_<channel>`` if those attrs exist — critical for
        the case where the file was previously imported with calibration
        and the user is appending more decay. Without pre-filling, the
        defaults (0.0 / 1.0) get persisted on Append and compute_phasor
        skips the calibration step entirely (its guard is
        ``if cal_phase != 0.0 or cal_mod != 1.0:``), producing an
        uncalibrated phasor that looks broken even though the decay
        bytes are correct.

        Also auto-fills the laser frequency and auto-checks the FLIM
        Parameters group when existing metadata indicates FLIM.
        """
        # Clear any existing widgets
        while self._tcspc_flim_cal_container.count():
            item = self._tcspc_flim_cal_container.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._tcspc_channel_calibrations.clear()

        meta = self._store.metadata

        # Frequency
        existing_freq = meta.get("flim_frequency_mhz")
        if existing_freq is not None:
            try:
                self._tcspc_flim_freq.setValue(float(existing_freq))
            except (TypeError, ValueError):
                pass

        any_flim_meta_present = (
            existing_freq is not None
            or any(k.startswith("flim_cal_") for k in meta)
        )

        for name in channel_names:
            group = QGroupBox(f"Channel {name}")
            form = QFormLayout(group)

            phase_spin = QDoubleSpinBox()
            phase_spin.setRange(-6.283, 6.283)
            phase_spin.setDecimals(4)
            phase_spin.setSuffix(" rad")
            existing_phase = meta.get(f"flim_cal_phase_{name}")
            try:
                phase_spin.setValue(float(existing_phase) if existing_phase is not None else 0.0)
            except (TypeError, ValueError):
                phase_spin.setValue(0.0)
            form.addRow("Phase:", phase_spin)

            mod_spin = QDoubleSpinBox()
            mod_spin.setRange(0.0, 10.0)
            mod_spin.setDecimals(4)
            existing_mod = meta.get(f"flim_cal_mod_{name}")
            try:
                mod_spin.setValue(float(existing_mod) if existing_mod is not None else 1.0)
            except (TypeError, ValueError):
                mod_spin.setValue(1.0)
            form.addRow("Modulation:", mod_spin)

            self._tcspc_flim_cal_container.addWidget(group)
            self._tcspc_channel_calibrations[name] = _TcspcCalibration(
                phase_spin=phase_spin, mod_spin=mod_spin
            )

        # Auto-check the FLIM Parameters group when the dataset already has
        # FLIM metadata. This means persistence is ON by default for these
        # datasets — if the user clicks Append without touching calibration,
        # the existing values get re-written verbatim instead of being
        # silently replaced by 0.0 / 1.0 defaults.
        if any_flim_meta_present and not self._tcspc_flim_group.isChecked():
            self._tcspc_flim_group.setChecked(True)

        # Re-apply collapsed state so the new widgets honor the group's state
        self._on_tcspc_flim_group_toggled(self._tcspc_flim_group.isChecked())

    def _tcspc_run_match(self) -> None:
        # Direct token equality + base-stem fallback. No pad/offset.
        rule = self._tcspc_state.build_selected_rule()
        result = match_bin_to_intensity(
            self._tcspc_bin_files,
            list(self._tcspc_state.intensity_channels),
            rule,
            TokenConfig(),
        )
        self._tcspc_state.apply_match(self._tcspc_bin_files, result)
        self._tcspc_render_table()

    def _tcspc_channel_groups(self) -> list[tuple[str, list[Path], bool, bool]]:
        """Group rows by target channel name. Returns
        ``(channel_name, [bin_paths], has_conflict, replace_checked)`` per
        existing TIFF channel. Channels with zero matched bins still appear
        (as 'no tiles assigned')."""
        by_channel: dict[str, list[Path]] = {}
        for ch in self._tcspc_state.intensity_channels:
            by_channel[ch.name] = []
        for row in self._tcspc_state.rows:
            ch = row.effective_channel
            if ch in by_channel:
                by_channel[ch].append(row.bin_path)

        out = []
        for name, paths in by_channel.items():
            has_conflict = name in self._tcspc_state.existing_decay_channels
            # replace_checked is OR of any row's flag for that channel — at the
            # channel-grouped UI we just track one checkbox per channel.
            replace_checked = self._tcspc_replace_state.get(name, False)
            out.append((name, paths, has_conflict, replace_checked))
        return out

    def _tcspc_render_table(self) -> None:
        # Initialize per-channel replace state map on first render
        if not hasattr(self, "_tcspc_replace_state"):
            self._tcspc_replace_state: dict[str, bool] = {}

        groups = self._tcspc_channel_groups()
        self._tcspc_table.setRowCount(0)
        for row_idx, (name, paths, has_conflict, replace_checked) in enumerate(groups):
            self._tcspc_table.insertRow(row_idx)
            # Col 0: channel name (read-only)
            ch_item = QTableWidgetItem(name)
            ch_item.setFlags(ch_item.flags() & ~Qt.ItemIsEditable)
            self._tcspc_table.setItem(row_idx, 0, ch_item)
            # Col 1: .bin token dropdown — options are the distinct
            # channel tokens actually present in the scanned .bin filenames
            # (so the user picks from real values, not types blind).
            current_token = self._tcspc_channel_token_overrides.get(name, "")
            token_combo = QComboBox()
            token_combo.addItem("(unmapped)", "")
            available = getattr(self, "_tcspc_available_bin_tokens", []) or []
            for tok in available:
                token_combo.addItem(tok, tok)
            # If the seeded current token isn't among the discovered ones
            # (e.g., positional fallback before any tokens were present),
            # add it as a hint so the user can see it was tried.
            if current_token and current_token not in available:
                token_combo.addItem(f"{current_token} (not in files)", current_token)
            idx = token_combo.findData(current_token)
            if idx >= 0:
                token_combo.setCurrentIndex(idx)
            token_combo.setToolTip(
                f"Channel token in .bin filenames that maps to '{name}'.\n"
                "Options are the distinct tokens found in the scanned .bin files."
            )
            token_combo.currentIndexChanged.connect(
                lambda _i, ch=name, c=token_combo: self._on_tcspc_token_picked(ch, c)
            )
            self._tcspc_table.setCellWidget(row_idx, 1, token_combo)
            # Col 2: matched-bin summary
            if not paths:
                summary = "no tiles assigned"
            elif len(paths) == 1:
                summary = f"1 tile: {paths[0].name}"
            else:
                summary = f"{len(paths)} tiles: {paths[0].name} … {paths[-1].name}"
            self._tcspc_table.setItem(row_idx, 2, QTableWidgetItem(summary))
            # Col 3: replace checkbox (only when conflict)
            if has_conflict:
                cb = QCheckBox()
                cb.setChecked(replace_checked)
                cb.stateChanged.connect(
                    lambda state, ch=name: self._on_tcspc_channel_replace(ch, state)
                )
                self._tcspc_table.setCellWidget(row_idx, 3, cb)
            else:
                empty = QTableWidgetItem("—")
                empty.setFlags(empty.flags() & ~Qt.ItemIsEditable)
                self._tcspc_table.setItem(row_idx, 3, empty)
            # Col 4: status
            status = self._tcspc_channel_status(name, paths, has_conflict, replace_checked)
            status_item = QTableWidgetItem(status)
            status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
            self._tcspc_table.setItem(row_idx, 4, status_item)
        # Counter + accept
        self._tcspc_counter.setText(self._tcspc_count_summary())
        self._tcspc_accept_btn.setEnabled(self._tcspc_can_accept())

    def _on_tcspc_token_picked(self, channel_name: str, combo: QComboBox) -> None:
        """User picked a .bin token from the dropdown — store + re-run match."""
        new_token = combo.currentData() or ""
        if not hasattr(self, "_tcspc_channel_token_overrides"):
            self._tcspc_channel_token_overrides = {}
        self._tcspc_channel_token_overrides[channel_name] = new_token
        # Rebuild the IntensityChannel records with the new token, re-set on
        # the state, then re-run the match. We deliberately do NOT rebuild
        # calibration widgets here (channel set didn't change).
        intensity = self._tcspc_intensity_channels()
        existing_decay = self._store.list_groups("decay")
        self._tcspc_state.set_intensity(intensity, existing_decay)
        if self._tcspc_bin_files:
            self._tcspc_run_match()

    def _tcspc_channel_status(
        self, name: str, paths: list[Path], has_conflict: bool, replace_checked: bool,
    ) -> str:
        if not paths:
            return "no tiles assigned (will skip)"
        if has_conflict and not replace_checked:
            return "conflict (decay exists)"
        if has_conflict and replace_checked:
            return "will REPLACE existing"
        return f"{len(paths)} tile(s) ready"

    def _tcspc_count_summary(self) -> str:
        n_bins = len(self._tcspc_state.rows)
        unm = len(self._tcspc_state.unmatched_paths())
        amb = len(self._tcspc_state.ambiguous_paths())
        n_channels_with_bins = sum(
            1 for _, paths, *_ in self._tcspc_channel_groups() if paths
        )
        parts = [f"{n_bins} bin file(s) → {n_channels_with_bins} channel(s)"]
        if unm:
            parts.append(f"{unm} unmatched")
        if amb:
            parts.append(f"{amb} ambiguous")
        return " — ".join(parts)

    def _tcspc_can_accept(self) -> bool:
        groups = self._tcspc_channel_groups()
        any_assigned = any(paths for _, paths, *_ in groups)
        if not any_assigned:
            return False
        # Every conflicting channel that has tiles assigned must have replace checked
        for name, paths, has_conflict, replace_checked in groups:
            if paths and has_conflict and not replace_checked:
                return False
        return True

    def _on_tcspc_channel_replace(self, channel_name: str, state) -> None:
        self._tcspc_replace_state[channel_name] = bool(state)
        self._tcspc_render_table()

    def _on_tcspc_accept(self) -> None:
        rule = self._tcspc_state.build_selected_rule()
        if self._tcspc_stitch_check.isChecked():
            tile_config = TileConfig(
                grid_rows=self._tcspc_stitch_rows.value(),
                grid_cols=self._tcspc_stitch_cols.value(),
                grid_type=self._tcspc_stitch_type.currentText(),
                order=self._tcspc_stitch_order.currentText(),
            )
        else:
            tile_config = TileConfig(grid_rows=1, grid_cols=1)
        flim_config = self._tcspc_build_flim_config()
        rotate_k = int(self._tcspc_rotation_combo.currentData() or 0)
        flip_axis_value = self._tcspc_flip_combo.currentData()
        flip_axis = (
            int(flip_axis_value)
            if flip_axis_value is not None and int(flip_axis_value) in (0, 1)
            else None
        )
        # Any conflict-row marked for replace forces force=True for that append
        force = any(self._tcspc_replace_state.values()) if hasattr(
            self, "_tcspc_replace_state"
        ) else False

        # Pass the user's per-channel token overrides (built into
        # IntensityChannel records) so the use case doesn't re-derive
        # tokens from semantic channel names. Without this, channels
        # named ``CA-SiR`` / ``mNG`` / ``mTQ2`` would all get an empty
        # token and the matcher would return zero bindings.
        intensity_channels = list(self._tcspc_state.intensity_channels)
        try:
            report = add_decay_to_dataset(
                h5_path=self._store.path,
                source_dir=Path(self._tcspc_dir_edit.text()),
                token_config=TokenConfig(),
                tile_config=tile_config,
                flim_config=flim_config,
                cross_format_rule=rule,
                rotate_k=rotate_k,
                flip_axis=flip_axis,
                force=force,
                intensity_channels=intensity_channels,
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Append failed", str(e))
            return

        # Always persist FLIM calibration whenever any channel has a
        # non-default value (phase != 0.0 OR modulation != 1.0). This
        # guards against the case where the FLIM group is collapsed but
        # the spinboxes still hold the values pre-filled from existing
        # metadata — without this, a click-Append-without-touching-FLIM
        # would not refresh the metadata at all, but if calibration was
        # previously written under a different channel-name spelling
        # those keys would stay stale.
        has_real_cal = any(
            cal.phase_spin.value() != 0.0 or cal.mod_spin.value() != 1.0
            for cal in self._tcspc_channel_calibrations.values()
        )
        if self._tcspc_flim_group.isChecked() or has_real_cal:
            self._tcspc_persist_flim_metadata()

        # Debug: append .bin-derived intensity layers if requested
        if (
            report.written
            and self._tcspc_debug_intensity_check.isChecked()
        ):
            try:
                self._tcspc_write_bin_intensity_debug_layers(list(report.written))
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(
                    self,
                    "Debug intensity write failed",
                    f"Decay layers were appended successfully, but the debug "
                    f"intensity layers could not be written: {e}",
                )

        self._tcspc_show_report(report)

    def _tcspc_write_bin_intensity_debug_layers(self, channel_names: list[str]) -> None:
        """For each appended decay channel, compute the per-pixel sum over T
        and append it to /intensity as a new channel ``<channel>_bin``.

        Writes nothing if the resulting intensity image would have a
        different (H, W) shape than the existing /intensity (e.g., the
        user appended decay at a different stitched size). Updates
        /metadata.channel_names so napari renders the new channel
        alongside the existing ones.
        """
        import h5py
        decay_intensities: dict[str, np.ndarray] = {}
        with h5py.File(self._store.path, "r") as f:
            for ch in channel_names:
                ds = f.get(f"decay/{ch}")
                if ds is None:
                    continue
                # Sum over T axis — produces (H, W) float32. For very large
                # decays (5GB+), sum tile-by-tile via h5py chunk iteration
                # would be cheaper, but a one-shot sum is simplest and the
                # debug toggle is opt-in so a brief peak-memory spike is OK.
                arr = ds[...].astype(np.float64).sum(axis=-1).astype(np.float32)
                decay_intensities[ch] = arr

        if not decay_intensities:
            return

        # Read existing /intensity to know its (H, W) and (C, …) shape
        try:
            existing = self._store.read_array("intensity")
        except KeyError:
            existing = None
        meta = self._store.metadata
        existing_names = list(meta.get("channel_names", []))

        if existing is not None and existing.ndim == 3:
            existing_h, existing_w = existing.shape[1], existing.shape[2]
        elif existing is not None and existing.ndim == 2:
            existing_h, existing_w = existing.shape
            existing = existing[np.newaxis, :, :]  # promote to (1, H, W)
        else:
            existing_h = existing_w = None

        kept: dict[str, np.ndarray] = {}
        skipped: list[str] = []
        for ch, arr in decay_intensities.items():
            if existing_h is not None and arr.shape != (existing_h, existing_w):
                skipped.append(f"{ch} ({arr.shape} vs intensity {(existing_h, existing_w)})")
                continue
            kept[ch] = arr

        if not kept:
            self.statusBar_msg(
                f"Skipped debug intensity write — shape mismatch: {', '.join(skipped)}"
            )
            return

        # Stack: existing channels followed by the new <ch>_bin channels
        new_arrays = list(kept.values())
        new_names = [f"{ch}_bin" for ch in kept.keys()]
        if existing is None:
            stacked = np.stack(new_arrays, axis=0).astype(np.float32)
            all_names = new_names
        else:
            stacked = np.concatenate(
                [existing, np.stack(new_arrays, axis=0)], axis=0
            ).astype(np.float32)
            all_names = existing_names + new_names

        self._store.write_array(
            "intensity", stacked, attrs={"dims": ["C", "H", "W"]}
        )
        self._store.set_metadata({
            "channel_names": all_names,
            "n_channels": len(all_names),
        })
        msg = f"Wrote debug intensity layers: {', '.join(new_names)}"
        if skipped:
            msg += f" (skipped due to shape mismatch: {', '.join(skipped)})"
        self.statusBar_msg(msg)

    def _tcspc_build_flim_config(self) -> FlimConfig:
        """Build a FlimConfig from the FLIM Parameters group state.

        When the group is unchecked, returns FlimConfig() defaults.
        """
        if not self._tcspc_flim_group.isChecked():
            return FlimConfig()
        # Per-channel calibration as a tuple of (phase, modulation) pairs ordered
        # by intensity channel order — matches FlimConfig.channel_calibrations.
        cals = []
        for ch in self._tcspc_state.intensity_channels:
            cal = self._tcspc_channel_calibrations.get(ch.name)
            if cal is not None:
                cals.append((cal.phase_spin.value(), cal.mod_spin.value()))
        return FlimConfig(
            frequency_mhz=self._tcspc_flim_freq.value(),
            channel_calibrations=tuple(cals),
            bin_x=self._tcspc_bin_x.value(),
            bin_y=self._tcspc_bin_y.value(),
            bin_t=self._tcspc_bin_t.value(),
            bin_dtype=self._tcspc_bin_dtype.currentText(),
            bin_dim_order=self._tcspc_bin_dim_order.currentText(),
            bin_header_bytes=self._tcspc_bin_header.value(),
        )

    def _tcspc_persist_flim_metadata(self) -> None:
        """Write per-channel FLIM calibration + frequency to /metadata.

        Uses the same attr keys as the rest of the app (``flim_cal_phase_<ch>``,
        ``flim_cal_mod_<ch>``, ``flim_frequency_mhz``) so phasor computation
        can pick them up later.
        """
        attrs = {"flim_frequency_mhz": float(self._tcspc_flim_freq.value())}
        for ch_name, cal in self._tcspc_channel_calibrations.items():
            attrs[f"flim_cal_phase_{ch_name}"] = float(cal.phase_spin.value())
            attrs[f"flim_cal_mod_{ch_name}"] = float(cal.mod_spin.value())
        try:
            self._store.set_metadata(attrs)
        except Exception:  # noqa: BLE001
            # Metadata write is best-effort; the decay layers already landed
            pass

    def _tcspc_show_report(self, report: AppendReport) -> None:
        if report.errors and not report.written:
            # Full failure — keep the dialog open so the user can fix and retry.
            QMessageBox.warning(
                self,
                "Append failed",
                "No decay layers were written.\n\n"
                + "\n".join(f"{k}: {v}" for k, v in report.errors.items()),
            )
            return
        # At least one decay layer landed — refresh the viewer, surface the
        # outcome (including any partial failures), then close the dialog.
        # Matches the convention of the other tabs (e.g., Single TIFF calls
        # ``self.accept()`` after a successful import).
        msg = (
            f"Appended {len(report.written)} decay layer(s): "
            f"{', '.join(report.written)}\n\n"
            "Existing phasor for these channels was invalidated — "
            "re-run Compute Phasor to see updated results."
        )
        if report.errors:
            msg += (
                "\n\nFailures:\n"
                + "\n".join(f"  {k}: {v}" for k, v in report.errors.items())
            )
        QMessageBox.information(self, "Append complete", msg)
        self._refresh_viewer()
        self.statusBar_msg(
            f"Appended {len(report.written)} decay layer(s) to "
            f"{self._store.path.name}"
        )
        self.accept()

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------



class _ChannelRowWidgets:
    """Holds widgets for a single channel row in batch discovery."""

    __slots__ = ("checkbox", "name_edit", "type_combo")

    def __init__(self, checkbox, name_edit, type_combo) -> None:
        self.checkbox = checkbox
        self.name_edit = name_edit
        self.type_combo = type_combo


def _sort_key(val: str) -> tuple[int, str]:
    try:
        return (0, str(int(val)).zfill(10))
    except ValueError:
        return (1, val)


class _TcspcCalibration:
    """Holds the FLIM phase/modulation widgets for one TCSPC-tab channel."""

    __slots__ = ("phase_spin", "mod_spin")

    def __init__(self, phase_spin: QDoubleSpinBox, mod_spin: QDoubleSpinBox) -> None:
        self.phase_spin = phase_spin
        self.mod_spin = mod_spin
