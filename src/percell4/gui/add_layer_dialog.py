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
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class AddLayerDialog(QDialog):
    """Dialog for adding layers to a loaded HDF5 dataset.

    Provides tabs for different import sources: single TIFF, batch TIFF
    discovery, ImageJ ROIs, and Cellpose segmentation files.
    """

    def __init__(self, parent, store, data_model, viewer_win) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Layer to Dataset")
        self.setMinimumWidth(700)
        self.resize(750, 550)

        self._store = store
        self._data_model = data_model
        self._viewer_win = viewer_win
        self._launcher = parent

        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_single_tiff_tab(), "Single TIFF")
        self._tabs.addTab(self._build_batch_tiff_tab(), "Discover TIFFs")
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
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed:\n{e}")

    # ------------------------------------------------------------------
    # Tab: Discover TIFFs (batch into existing dataset)
    # ------------------------------------------------------------------

    def _build_batch_tiff_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Source
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Directory:"))
        self._batch_source_edit = QLineEdit()
        self._batch_source_edit.setReadOnly(True)
        self._batch_source_edit.setPlaceholderText("Select a folder...")
        src_row.addWidget(self._batch_source_edit, 1)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._on_browse_batch)
        src_row.addWidget(btn_browse)
        layout.addLayout(src_row)

        # Discovery mode
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Discovery:"))
        self._batch_discovery_combo = QComboBox()
        self._batch_discovery_combo.addItems(["Flat Directory"])
        mode_row.addWidget(self._batch_discovery_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # Channel list with name + type
        self._batch_ch_container = QVBoxLayout()
        layout.addLayout(self._batch_ch_container)
        self._batch_channel_configs: dict[str, _ChannelRowWidgets] = {}

        # Tile stitching
        self._batch_stitch_check = QCheckBox("Tile Stitching")
        self._batch_stitch_check.toggled.connect(
            lambda c: self._batch_stitch_widget.setVisible(c)
        )
        layout.addWidget(self._batch_stitch_check)

        self._batch_stitch_widget = QWidget()
        stitch_layout = QHBoxLayout(self._batch_stitch_widget)
        stitch_layout.setContentsMargins(20, 0, 0, 0)
        stitch_layout.addWidget(QLabel("Rows:"))
        self._batch_stitch_rows = QSpinBox()
        self._batch_stitch_rows.setRange(1, 100)
        stitch_layout.addWidget(self._batch_stitch_rows)
        stitch_layout.addWidget(QLabel("Cols:"))
        self._batch_stitch_cols = QSpinBox()
        self._batch_stitch_cols.setRange(1, 100)
        stitch_layout.addWidget(self._batch_stitch_cols)
        stitch_layout.addWidget(QLabel("Pattern:"))
        self._batch_stitch_type = QComboBox()
        self._batch_stitch_type.addItems(
            ["row_by_row", "column_by_column", "snake_by_row", "snake_by_column"]
        )
        stitch_layout.addWidget(self._batch_stitch_type)
        stitch_layout.addStretch()
        self._batch_stitch_widget.setVisible(False)
        layout.addWidget(self._batch_stitch_widget)

        self._batch_summary = QLabel("")
        layout.addWidget(self._batch_summary)

        layout.addStretch()

        btn_import = QPushButton("Import Selected Channels")
        btn_import.clicked.connect(self._on_import_batch)
        layout.addWidget(btn_import)
        return tab

    def _on_browse_batch(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Source Directory")
        if not path:
            return
        self._batch_source_edit.setText(path)
        self._run_batch_discovery()

    def _run_batch_discovery(self) -> None:
        source = self._batch_source_edit.text().strip()
        if not source:
            return

        from percell4.io.models import TokenConfig
        from percell4.io.scanner import FileScanner

        root = Path(source)
        scanner = FileScanner(TokenConfig())
        scan = scanner.scan(path=root)

        if not scan.files:
            self._batch_summary.setText("No TIFF files found")
            return

        # Clear existing rows
        while self._batch_ch_container.count():
            child = self._batch_ch_container.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._batch_channel_configs.clear()

        channels = sorted(scan.channels, key=_sort_key)
        tiles = sorted(scan.tiles, key=_sort_key)

        for ch in channels:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 2, 0, 2)

            cb = QCheckBox(f"ch{ch}")
            cb.setChecked(True)
            row.addWidget(cb)

            name_edit = QLineEdit(f"ch{ch}")
            name_edit.setFixedWidth(120)
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

        parts = [f"Channels: {len(channels)}"]
        if tiles:
            parts.append(f"Tiles: {len(tiles)} (s{tiles[0]}–s{tiles[-1]})")
        self._batch_summary.setText("    ".join(parts))

        if tiles:
            self._batch_stitch_check.setChecked(True)

    def _on_import_batch(self) -> None:
        source = self._batch_source_edit.text().strip()
        if not source:
            return

        from percell4.io.importer import import_dataset
        from percell4.io.models import TileConfig, TokenConfig

        selected = {}
        for ch_id, cfg in self._batch_channel_configs.items():
            if cfg.checkbox.isChecked():
                selected[ch_id] = (cfg.name_edit.text().strip() or f"ch{ch_id}",
                                   cfg.type_combo.currentText())

        if not selected:
            return

        # Split into intensity vs labels vs masks
        intensity_channels = {k for k, (_, t) in selected.items() if t == "Channel"}
        label_channels = {k: n for k, (n, t) in selected.items() if t == "Segmentation"}
        mask_channels = {k: n for k, (n, t) in selected.items() if t == "Mask"}

        tile_config = None
        if self._batch_stitch_check.isChecked():
            tile_config = TileConfig(
                grid_rows=self._batch_stitch_rows.value(),
                grid_cols=self._batch_stitch_cols.value(),
                grid_type=self._batch_stitch_type.currentText(),
            )

        from qtpy.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            # Import intensity channels into a temp .h5
            # then merge into the existing store
            from percell4.io.models import LayerAssignment, LayerType
            from percell4.io.scanner import FileScanner

            scanner = FileScanner(TokenConfig())
            scan = scanner.scan(path=Path(source))

            # Group files by channel
            from collections import defaultdict
            by_channel: dict[str, list] = defaultdict(list)
            for f in scan.files:
                ch = f.tokens.get("channel", "")
                by_channel[ch].append(f)

            import tifffile
            from percell4.io.assembler import assemble_tiles, project_z
            from percell4.io.models import TileConfig as TC

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
                    array = assemble_tiles(tile_groups, tile_config)
                elif len(tile_groups) == 1:
                    array = next(iter(tile_groups.values()))
                else:
                    # No tiles — just use the first image
                    array = tile_groups.get(0, next(iter(tile_groups.values())))

                self._write_layer(name, layer_type, array)

            self._refresh_viewer()
            self.statusBar_msg(f"Imported {len(selected)} channels from {Path(source).name}")
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
            from percell4.segment.roi_import import import_imagej_rois
            labels = import_imagej_rois(path, shape)
            n_cells = int(labels.max())
            name = self._roi_name_edit.text().strip() or f"roi_import_{n_cells}"
            self._store.write_labels(name, labels)
            if self._viewer_win is not None:
                self._viewer_win.add_labels(labels, name=name)
            self._data_model.set_active_segmentation(name)
            self.statusBar_msg(f"Imported {n_cells} ROIs as '{name}'")
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
            from percell4.segment.roi_import import import_cellpose_seg
            labels = import_cellpose_seg(path)
            n_cells = int(labels.max())
            name = self._cp_name_edit.text().strip() or f"cellpose_import_{n_cells}"
            self._store.write_labels(name, labels)
            if self._viewer_win is not None:
                self._viewer_win.add_labels(labels, name=name)
            self._data_model.set_active_segmentation(name)
            self.statusBar_msg(f"Imported {n_cells} cells as '{name}'")
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
    # Styling
    # ------------------------------------------------------------------

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: #e0e0e0; }
            QTabWidget::pane {
                border: 1px solid #3a3a3a;
                background-color: #1e1e1e;
            }
            QTabBar::tab {
                background-color: #252525;
                color: #cccccc;
                border: 1px solid #3a3a3a;
                padding: 6px 14px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #1e1e1e;
                color: #4ea8de;
                border-bottom-color: #1e1e1e;
            }
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
        """)


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
