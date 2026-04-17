"""Data task panel — active layers, layer management, dataset info.

Extracted from launcher._create_data_panel + associated handlers.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.gui import theme
from percell4.model import CellDataModel


class DataPanel(QWidget):
    """Panel for active layers, layer management, and dataset info."""

    def __init__(
        self,
        data_model: CellDataModel,
        *,
        get_store: Callable[[], Any | None],
        get_viewer_window: Callable[[], Any | None],
        get_h5_path: Callable[[], str | None],
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.data_model = data_model
        self._get_store = get_store
        self._get_viewer_window = get_viewer_window
        self._get_h5_path = get_h5_path
        self._show_status = show_status
        self._build_ui()

        # Subscribe to model state changes for active layer sync
        self.data_model.state_changed.connect(self._on_state_changed)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("Data")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {theme.TEXT_BRIGHT};"
            f" margin-bottom: 12px; padding-bottom: 4px;"
            f" border-bottom: 1px solid {theme.BORDER};"
        )
        layout.addWidget(title)

        # ── Active layers ──
        layers_group = QGroupBox("Active Layers")
        layers_layout = QVBoxLayout(layers_group)

        chan_row = QHBoxLayout()
        chan_row.addWidget(QLabel("Active Channel:"))
        self._data_channel_label = QLabel("None selected")
        self._data_channel_label.setStyleSheet(
            f"color: {theme.ACCENT}; font-weight: bold;"
        )
        chan_row.addWidget(self._data_channel_label)
        chan_row.addStretch()
        layers_layout.addLayout(chan_row)

        seg_row = QHBoxLayout()
        seg_row.addWidget(QLabel("Active Segmentation:"))
        self._active_seg_combo = QComboBox()
        self._active_seg_combo.setPlaceholderText("None")
        self._active_seg_combo.currentTextChanged.connect(
            self._on_active_seg_combo_changed
        )
        seg_row.addWidget(self._active_seg_combo)
        layers_layout.addLayout(seg_row)

        mask_row = QHBoxLayout()
        mask_row.addWidget(QLabel("Active Mask:"))
        self._active_mask_combo = QComboBox()
        self._active_mask_combo.setPlaceholderText("None")
        self._active_mask_combo.currentTextChanged.connect(
            self._on_active_mask_combo_changed
        )
        mask_row.addWidget(self._active_mask_combo)
        layers_layout.addLayout(mask_row)

        layout.addWidget(layers_group)

        # ── Layer Management ──
        mgmt_group = QGroupBox("Layer Management")
        mgmt_layout = QVBoxLayout(mgmt_group)

        mgmt_layout.addWidget(QLabel("Segmentations:"))
        seg_mgmt_row = QHBoxLayout()
        self._mgmt_seg_combo = QComboBox()
        self._mgmt_seg_combo.setPlaceholderText("Select segmentation")
        seg_mgmt_row.addWidget(self._mgmt_seg_combo)
        btn_rename_seg = QPushButton("Rename")
        btn_rename_seg.clicked.connect(lambda: self._on_rename_layer("labels"))
        seg_mgmt_row.addWidget(btn_rename_seg)
        btn_delete_seg = QPushButton("Delete")
        btn_delete_seg.clicked.connect(lambda: self._on_delete_layer("labels"))
        seg_mgmt_row.addWidget(btn_delete_seg)
        mgmt_layout.addLayout(seg_mgmt_row)

        mgmt_layout.addWidget(QLabel("Masks:"))
        mask_mgmt_row = QHBoxLayout()
        self._mgmt_mask_combo = QComboBox()
        self._mgmt_mask_combo.setPlaceholderText("Select mask")
        mask_mgmt_row.addWidget(self._mgmt_mask_combo)
        btn_rename_mask = QPushButton("Rename")
        btn_rename_mask.clicked.connect(lambda: self._on_rename_layer("masks"))
        mask_mgmt_row.addWidget(btn_rename_mask)
        btn_delete_mask = QPushButton("Delete")
        btn_delete_mask.clicked.connect(lambda: self._on_delete_layer("masks"))
        mask_mgmt_row.addWidget(btn_delete_mask)
        mgmt_layout.addLayout(mask_mgmt_row)

        mgmt_layout.addWidget(QLabel("Channels:"))
        chan_mgmt_row = QHBoxLayout()
        self._mgmt_chan_combo = QComboBox()
        self._mgmt_chan_combo.setPlaceholderText("Select channel")
        chan_mgmt_row.addWidget(self._mgmt_chan_combo)
        btn_rename_chan = QPushButton("Rename")
        btn_rename_chan.clicked.connect(self._on_rename_channel)
        chan_mgmt_row.addWidget(btn_rename_chan)
        btn_delete_chan = QPushButton("Delete")
        btn_delete_chan.clicked.connect(self._on_delete_channel)
        chan_mgmt_row.addWidget(btn_delete_chan)
        mgmt_layout.addLayout(chan_mgmt_row)

        layout.addWidget(mgmt_group)

        # ── Dataset Info ──
        info_group = QGroupBox("Dataset Info")
        info_layout = QVBoxLayout(info_group)
        self._info_label = QLabel("No dataset loaded")
        self._info_label.setWordWrap(True)
        info_layout.addWidget(self._info_label)
        layout.addWidget(info_group)

        layout.addStretch()

    # ── Helpers ───────────────────────────────────────────────

    def _get_viewer_win(self):
        return self._get_viewer_window()

    # ─�� State change routing ─────────────────────────────────

    def _on_state_changed(self, change) -> None:
        if change.segmentation:
            name = self.data_model.active_segmentation
            self._on_model_active_seg_changed(name)
        if change.mask:
            name = self.data_model.active_mask
            self._on_model_active_mask_changed(name)

    # ── Active layer sync ────────────────────────────────────

    def _on_active_seg_combo_changed(self, name: str) -> None:
        if name:
            self.data_model.set_active_segmentation(name)

    def _on_active_mask_combo_changed(self, name: str) -> None:
        if name:
            self.data_model.set_active_mask(name)

    def _on_model_active_seg_changed(self, name: str) -> None:
        if name:
            self._active_seg_combo.blockSignals(True)
            if self._active_seg_combo.findText(name) < 0:
                self._active_seg_combo.addItem(name)
            self._active_seg_combo.setCurrentText(name)
            self._active_seg_combo.blockSignals(False)
        self.refresh_management_combos()
        self.refresh_dataset_info()

    def _on_model_active_mask_changed(self, name: str) -> None:
        if name:
            self._active_mask_combo.blockSignals(True)
            if self._active_mask_combo.findText(name) < 0:
                self._active_mask_combo.addItem(name)
            self._active_mask_combo.setCurrentText(name)
            self._active_mask_combo.blockSignals(False)
        self.refresh_management_combos()
        self.refresh_dataset_info()

    # ── Layer Management ─────────────────────────────────────

    def refresh_management_combos(self) -> None:
        """Refresh all management dropdowns from the current store."""
        store = self._get_store()

        self._mgmt_seg_combo.clear()
        if store is not None:
            for name in store.list_labels():
                self._mgmt_seg_combo.addItem(name)

        self._mgmt_mask_combo.clear()
        if store is not None:
            for name in store.list_masks():
                self._mgmt_mask_combo.addItem(name)

        self._mgmt_chan_combo.clear()
        viewer_win = self._get_viewer_win()
        if viewer_win is not None and viewer_win.viewer is not None:
            for layer in viewer_win.viewer.layers:
                if layer.__class__.__name__ == "Image":
                    self._mgmt_chan_combo.addItem(layer.name)

    def refresh_active_combos(self) -> None:
        """Refresh the active segmentation/mask dropdowns.

        Block signals during repopulation to prevent spurious intermediate
        state changes.
        """
        store = self._get_store()
        mask_set = set(store.list_masks()) if store is not None else set()

        self._active_seg_combo.blockSignals(True)
        current = self._active_seg_combo.currentText()
        self._active_seg_combo.clear()
        if store is not None:
            for name in store.list_labels():
                if name not in mask_set:
                    self._active_seg_combo.addItem(name)
        if current and self._active_seg_combo.findText(current) >= 0:
            self._active_seg_combo.setCurrentText(current)
        self._active_seg_combo.blockSignals(False)

        self._active_mask_combo.blockSignals(True)
        current = self._active_mask_combo.currentText()
        self._active_mask_combo.clear()
        if store is not None:
            for name in store.list_masks():
                self._active_mask_combo.addItem(name)
        if current and self._active_mask_combo.findText(current) >= 0:
            self._active_mask_combo.setCurrentText(current)
        self._active_mask_combo.blockSignals(False)

    def refresh_dataset_info(self) -> None:
        """Refresh the Dataset Info label from the current store."""
        store = self._get_store()
        h5_path = self._get_h5_path()
        if store is None or h5_path is None:
            self._info_label.setText("No dataset loaded")
            return
        try:
            n_labels = len(store.list_labels())
            n_masks = len(store.list_masks())
            with store.open_read() as s:
                intensity = s.read_array("intensity")
                shape = intensity.shape
            self._info_label.setText(
                f"File: {Path(h5_path).name}\n"
                f"Shape: {shape}\n"
                f"Labels: {n_labels}  |  Masks: {n_masks}"
            )
        except Exception:
            pass

    def update_channel_label(self, name: str | None = None) -> None:
        """Update the active channel display."""
        if name:
            self._data_channel_label.setText(name)
        else:
            self._data_channel_label.setText("None selected")

    def clear_ui(self) -> None:
        """Reset all UI state (called on dataset close)."""
        self._info_label.setText("No dataset loaded")
        self._active_seg_combo.blockSignals(True)
        self._active_seg_combo.clear()
        self._active_seg_combo.blockSignals(False)
        self._active_mask_combo.blockSignals(True)
        self._active_mask_combo.clear()
        self._active_mask_combo.blockSignals(False)
        self._mgmt_seg_combo.clear()
        self._mgmt_mask_combo.clear()
        self._mgmt_chan_combo.clear()
        self._data_channel_label.setText("None selected")

    def _on_rename_layer(self, prefix: str) -> None:
        combo = self._mgmt_seg_combo if prefix == "labels" else self._mgmt_mask_combo
        old_name = combo.currentText()
        if not old_name:
            self._show_status("Nothing selected to rename")
            return

        new_name, ok = QInputDialog.getText(
            self, "Rename", f"New name for '{old_name}':", text=old_name
        )
        if not ok or not new_name or new_name == old_name:
            return

        store = self._get_store()
        if store is not None:
            try:
                store.rename_item(f"{prefix}/{old_name}", f"{prefix}/{new_name}")
            except ValueError as e:
                self._show_status(str(e))
                return

        viewer_win = self._get_viewer_win()
        if viewer_win is not None and viewer_win.viewer is not None:
            for layer in viewer_win.viewer.layers:
                if layer.name == old_name:
                    layer.name = new_name
                    break

        self.refresh_management_combos()
        self.refresh_active_combos()
        self._show_status(f"Renamed '{old_name}' → '{new_name}'")

    def _on_delete_layer(self, prefix: str) -> None:
        combo = self._mgmt_seg_combo if prefix == "labels" else self._mgmt_mask_combo
        name = combo.currentText()
        if not name:
            self._show_status("Nothing selected to delete")
            return

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete '{name}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        store = self._get_store()
        if store is not None:
            store.delete_item(f"{prefix}/{name}")

        viewer_win = self._get_viewer_win()
        if viewer_win is not None and viewer_win.viewer is not None:
            for layer in list(viewer_win.viewer.layers):
                if layer.name == name:
                    viewer_win.viewer.layers.remove(layer)
                    break

        self.refresh_management_combos()
        self.refresh_active_combos()
        self._show_status(f"Deleted '{name}'")

    def _on_rename_channel(self) -> None:
        old_name = self._mgmt_chan_combo.currentText()
        if not old_name:
            self._show_status("Nothing selected to rename")
            return

        new_name, ok = QInputDialog.getText(
            self, "Rename Channel", f"New name for '{old_name}':", text=old_name
        )
        if not ok or not new_name or new_name == old_name:
            return

        store = self._get_store()
        if store is not None:
            meta = store.metadata
            names = list(meta.get("channel_names", []))
            if old_name in names:
                names[names.index(old_name)] = new_name
                store.set_metadata({"channel_names": names})

        viewer_win = self._get_viewer_win()
        if viewer_win is not None and viewer_win.viewer is not None:
            for layer in viewer_win.viewer.layers:
                if layer.name == old_name:
                    layer.name = new_name
                    break

        self.refresh_management_combos()
        self._show_status(f"Renamed channel '{old_name}' → '{new_name}'")

    def _on_delete_channel(self) -> None:
        name = self._mgmt_chan_combo.currentText()
        if not name:
            self._show_status("Nothing selected to delete")
            return

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete channel '{name}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        viewer_win = self._get_viewer_win()
        if viewer_win is not None and viewer_win.viewer is not None:
            for layer in list(viewer_win.viewer.layers):
                if layer.name == name:
                    viewer_win.viewer.layers.remove(layer)
                    break

        self.refresh_management_combos()
        self._show_status(f"Deleted channel '{name}'")
