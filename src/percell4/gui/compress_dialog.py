"""Compress dialog for converting TIFF directories to HDF5 datasets.

Replaces ImportDialog with support for batch compression. Discovers
datasets from a root directory and lets the user select which to compress.
"""

from __future__ import annotations

import copy
from pathlib import Path

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from percell4.io.models import (
    CompressConfig,
    DatasetGuiState,
    TokenConfig,
)


class CompressDialog(QDialog):
    """Dialog for discovering and compressing TIFF datasets to HDF5.

    Supports both single-dataset and batch compression. The dialog
    auto-detects: one dataset shows a simple view, multiple datasets
    show a tree with checkboxes for selection.
    """

    def __init__(self, parent=None, project_dir: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compress TIFF Dataset")
        self.setMinimumWidth(600)
        self.resize(650, 600)
        self._project_dir = project_dir

        self._datasets: list[DatasetSpec] = []
        self._gui_states: dict[str, DatasetGuiState] = {}
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
        scroll.setStyleSheet("QScrollArea { background-color: #1e1e1e; border: none; }")
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
        self._source_edit.setPlaceholderText("Select a folder containing TIFFs...")
        self._source_edit.setReadOnly(True)
        row_src.addWidget(self._source_edit, 1)
        btn_browse_src = QPushButton("Browse...")
        btn_browse_src.clicked.connect(self._on_browse_source)
        row_src.addWidget(btn_browse_src)
        src_layout.addLayout(row_src)

        row_out = QHBoxLayout()
        row_out.addWidget(QLabel("Output:"))
        self._output_edit = QLineEdit()
        self._output_edit.setPlaceholderText("Defaults to source parent directory")
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
        self._discovery_combo.currentIndexChanged.connect(self._on_discovery_mode_changed)
        mode_row.addWidget(self._discovery_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # ── Auto/Manual toggle ──
        toggle_row = QHBoxLayout()
        toggle_row.addWidget(QLabel("Mode:"))
        self._auto_radio = QRadioButton("Auto")
        self._auto_radio.setChecked(True)
        self._manual_radio = QRadioButton("Manual")
        toggle_row.addWidget(self._auto_radio)
        toggle_row.addWidget(self._manual_radio)
        toggle_row.addStretch()
        layout.addLayout(toggle_row)

        # ── Dataset Tree ──
        tree_group = QGroupBox("Discovered Datasets")
        tree_layout = QVBoxLayout(tree_group)

        btn_row = QHBoxLayout()
        btn_select_all = QPushButton("Select All")
        btn_select_all.clicked.connect(self._on_select_all)
        btn_deselect_all = QPushButton("Deselect All")
        btn_deselect_all.clicked.connect(self._on_deselect_all)
        btn_row.addWidget(btn_select_all)
        btn_row.addWidget(btn_deselect_all)
        btn_row.addStretch()
        self._dataset_count_label = QLabel("")
        btn_row.addWidget(self._dataset_count_label)
        tree_layout.addLayout(btn_row)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Name", "Files", "Channels", "Output"])
        self._tree.setColumnWidth(0, 200)
        self._tree.setColumnWidth(1, 50)
        self._tree.setColumnWidth(2, 100)
        self._tree.header().setStretchLastSection(True)
        self._tree.setRootIsDecorated(True)
        self._tree.itemChanged.connect(self._on_item_changed)
        tree_layout.addWidget(self._tree)

        layout.addWidget(tree_group)

        # ── Global Settings ──
        settings_group = QGroupBox("Settings")
        settings_layout = QHBoxLayout(settings_group)

        settings_layout.addWidget(QLabel("Z-Projection:"))
        self._z_combo = QComboBox()
        self._z_combo.addItems(["mip", "mean", "sum", "none"])
        settings_layout.addWidget(self._z_combo)
        settings_layout.addStretch()

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

        layout.addWidget(self._token_group)
        # Hide token content when unchecked
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
    # Properties — single materialized config, read once after exec_()
    # ------------------------------------------------------------------

    @property
    def compress_config(self) -> CompressConfig:
        """Materialize all dialog state into a CompressConfig."""
        output_dir = None
        if self._output_edit.text().strip():
            output_dir = Path(self._output_edit.text().strip())

        return CompressConfig(
            z_project_method=self._z_combo.currentText(),
            token_config=self._current_token_config(),
            output_dir=output_dir,
            datasets=list(self._datasets),
            gui_states=copy.deepcopy(self._gui_states),
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
            self._run_discovery()

    def _on_discovery_mode_changed(self, index: int) -> None:
        if self._source_edit.text().strip():
            self._run_discovery()

    def _on_token_group_toggled(self, checked: bool) -> None:
        for child in self._token_group.findChildren(QWidget):
            if child is not self._token_group:
                child.setVisible(checked)

    def _on_select_all(self) -> None:
        self._set_all_check_state(Qt.Checked)

    def _on_deselect_all(self) -> None:
        self._set_all_check_state(Qt.Unchecked)

    def _set_all_check_state(self, state: Qt.CheckState) -> None:
        self._tree.blockSignals(True)
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            item.setCheckState(0, state)
            for j in range(item.childCount()):
                item.child(j).setCheckState(0, state)
        self._tree.blockSignals(False)
        self._sync_gui_states_from_tree()
        self._update_compress_button()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return

        self._tree.blockSignals(True)

        # Parent changed → propagate to children
        if item.childCount() > 0:
            state = item.checkState(0)
            if state != Qt.PartiallyChecked:
                for i in range(item.childCount()):
                    item.child(i).setCheckState(0, state)
        else:
            # Child changed → update parent tri-state
            parent = item.parent()
            if parent is not None:
                self._update_parent_check_state(parent)

        self._tree.blockSignals(False)
        self._sync_gui_states_from_tree()
        self._update_compress_button()

    def _update_parent_check_state(self, parent: QTreeWidgetItem) -> None:
        checked = sum(
            1
            for i in range(parent.childCount())
            if parent.child(i).checkState(0) == Qt.Checked
        )
        total = parent.childCount()
        if checked == 0:
            parent.setCheckState(0, Qt.Unchecked)
        elif checked == total:
            parent.setCheckState(0, Qt.Checked)
        else:
            parent.setCheckState(0, Qt.PartiallyChecked)

    # ------------------------------------------------------------------
    # Discovery + tree population
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
                datasets = discover_by_subdirectory(root, token_config, output_dir)
            else:
                datasets = discover_flat(root, token_config, output_dir)
        except Exception as e:
            self._dataset_count_label.setText(f"Error: {e}")
            return

        # Guard against stale discovery
        if gen != self._discovery_generation:
            return

        self._datasets = datasets
        # Initialize gui states for new datasets, preserve existing
        for ds in datasets:
            if ds.name not in self._gui_states:
                self._gui_states[ds.name] = DatasetGuiState()
        self._populate_tree()

    def _populate_tree(self) -> None:
        self._tree.setUpdatesEnabled(False)
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            for ds in self._datasets:
                gs = self._gui_states.get(ds.name, DatasetGuiState())
                channels_str = ", ".join(
                    sorted(ds.scan_result.channels) if ds.scan_result else []
                )
                parent = QTreeWidgetItem(
                    self._tree,
                    [ds.name, str(len(ds.files)), channels_str, ds.output_path.name],
                )
                parent.setFlags(
                    parent.flags() | Qt.ItemIsUserCheckable
                )
                parent.setCheckState(
                    0, Qt.Checked if gs.checked else Qt.Unchecked
                )
                parent.setData(0, Qt.UserRole, ds.name)

                for f in ds.files:
                    child = QTreeWidgetItem(parent, [f.path.name, "", "", ""])
                    child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                    child.setCheckState(0, parent.checkState(0))
        finally:
            self._tree.blockSignals(False)
            self._tree.setUpdatesEnabled(True)

        n = len(self._datasets)
        self._dataset_count_label.setText(
            f"{n} dataset{'s' if n != 1 else ''} found"
        )
        self._update_compress_button()

    def _sync_gui_states_from_tree(self) -> None:
        """Sync check states from tree back to gui_states."""
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            name = item.data(0, Qt.UserRole)
            if name and name in self._gui_states:
                self._gui_states[name].checked = (
                    item.checkState(0) != Qt.Unchecked
                )

    def _update_compress_button(self) -> None:
        any_checked = any(gs.checked for gs in self._gui_states.values())
        has_datasets = len(self._datasets) > 0
        self._btn_compress.setEnabled(has_datasets and any_checked)

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
            QLineEdit:focus, QComboBox:focus {
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
            QCheckBox, QRadioButton { color: #e0e0e0; }
            QLabel { color: #cccccc; }
            QTreeWidget {
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                outline: none;
            }
            QTreeWidget::item { padding: 3px 2px; }
            QTreeWidget::item:selected {
                background-color: #2a4a6b;
                color: #ffffff;
            }
            QTreeWidget::item:hover:!selected {
                background-color: #2a2a2a;
            }
            QTreeWidget::indicator { width: 16px; height: 16px; }
            QTreeWidget::indicator:unchecked {
                border: 1px solid #555555;
                border-radius: 3px;
                background-color: #2a2a2a;
            }
            QTreeWidget::indicator:checked {
                border: 1px solid #4ea8de;
                border-radius: 3px;
                background-color: #4ea8de;
            }
            QTreeWidget::indicator:indeterminate {
                border: 1px solid #4ea8de;
                border-radius: 3px;
                background-color: #2a4a6b;
            }
            QHeaderView::section {
                background-color: #252525;
                color: #4ea8de;
                border: none;
                border-right: 1px solid #3a3a3a;
                border-bottom: 1px solid #3a3a3a;
                padding: 4px 8px;
                font-weight: bold;
            }
        """)
