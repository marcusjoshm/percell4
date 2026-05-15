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
        # List events refresh the management dropdowns. Active-selection
        # display lives in the SessionWindow now; this panel handles only
        # rename/delete/metadata.
        if change.channel_list:
            self.refresh_management_combos()
        if change.segmentation_list:
            self._refresh_seg_combos()
            self.refresh_dataset_info()
        if change.mask_list:
            self._refresh_mask_combos()
            self.refresh_dataset_info()
        if change.segmentation or change.mask:
            self.refresh_dataset_info()

    def _refresh_seg_combos(self) -> None:
        """Re-list the Management Segmentations dropdown from the store."""
        store = self._get_store()
        self._mgmt_seg_combo.blockSignals(True)
        self._mgmt_seg_combo.clear()
        if store is not None:
            for name in store.list_labels():
                self._mgmt_seg_combo.addItem(name)
        self._mgmt_seg_combo.blockSignals(False)

    def _refresh_mask_combos(self) -> None:
        """Re-list the Management Masks dropdown from the store."""
        store = self._get_store()
        self._mgmt_mask_combo.blockSignals(True)
        self._mgmt_mask_combo.clear()
        if store is not None:
            for name in store.list_masks():
                self._mgmt_mask_combo.addItem(name)
        self._mgmt_mask_combo.blockSignals(False)

    # ── Layer Management ─────────────────────────────────────

    def refresh_management_combos(self) -> None:
        """Refresh all management dropdowns from the current store.

        Channel names come from ``session.dataset.metadata["channel_names"]``
        (the canonical source the Active Channel combo also uses). Sourcing
        from ``viewer.layers`` alone is timing-fragile — if this runs before
        ``_populate_viewer_from_store`` adds the layers, the combo lands
        with zero items, which renders as an unclickable dropdown in Qt
        (an empty QComboBox doesn't open its popup). Napari Image layers
        not already in metadata are unioned in afterward so orphan names
        like ``ch<N>`` remain deletable from this UI.
        """
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
        seen: set[str] = set()
        session = self.data_model.session
        if session.dataset is not None:
            for name in session.dataset.metadata.get("channel_names", []):
                if name not in seen:
                    self._mgmt_chan_combo.addItem(name)
                    seen.add(name)
        viewer_win = self._get_viewer_win()
        if viewer_win is not None and viewer_win.viewer is not None:
            for layer in viewer_win.viewer.layers:
                if layer.__class__.__name__ == "Image" and layer.name not in seen:
                    self._mgmt_chan_combo.addItem(layer.name)
                    seen.add(layer.name)

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

    def clear_ui(self) -> None:
        """Reset all UI state (called on dataset close)."""
        self._info_label.setText("No dataset loaded")
        self._mgmt_seg_combo.clear()
        self._mgmt_mask_combo.clear()
        self._mgmt_chan_combo.clear()

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

        session = self.data_model.session
        if session.dataset is not None and store is not None:
            if prefix == "masks":
                session.refresh_resource_lists(mask_names=store.list_masks())
                if session.active_mask == old_name:
                    session.set_active_mask(new_name)
            else:  # prefix == "labels"
                session.refresh_resource_lists(segmentation_names=store.list_labels())
                if session.active_segmentation == old_name:
                    session.set_active_segmentation(new_name)

        self.refresh_management_combos()
        self._refresh_seg_combos()
        self._refresh_mask_combos()
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

        session = self.data_model.session
        if session.dataset is not None and store is not None:
            if prefix == "masks":
                session.refresh_resource_lists(mask_names=store.list_masks())
                if session.active_mask == name:
                    session.set_active_mask(None)
            else:  # prefix == "labels"
                session.refresh_resource_lists(segmentation_names=store.list_labels())
                if session.active_segmentation == name:
                    session.set_active_segmentation(None)

        self.refresh_management_combos()
        self._refresh_seg_combos()
        self._refresh_mask_combos()
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
            try:
                store.rename_channel(old_name, new_name)
            except ValueError as e:
                self._show_status(f"Rename failed: {e}")
                return

        # Sync the in-memory handle metadata so use cases see the new name
        # without requiring a dataset reload.
        session = self.data_model.session
        handle = session.dataset
        if handle is not None:
            meta = handle.metadata
            names = list(meta.get("channel_names", []))
            if old_name in names:
                names[names.index(old_name)] = new_name
            for key_prefix in ("flim_cal_phase_", "flim_cal_mod_"):
                old_key = f"{key_prefix}{old_name}"
                new_key = f"{key_prefix}{new_name}"
                if old_key in meta:
                    meta[new_key] = meta.pop(old_key)
            session.refresh_resource_lists(channel_names=names)
            if session.active_channel == old_name:
                session.set_active_channel(new_name)

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
            f"Permanently delete channel '{name}' and its FLIM data "
            "(decay, phasor, calibration) from the .h5 file? "
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        session = self.data_model.session
        store = self._get_store()
        if store is None:
            self._show_status("No dataset loaded")
            return

        # /metadata.channel_names is the canonical list, but napari can hold
        # layers whose names don't appear there. Two ways this happens:
        #   (1) ``<ch>_bin`` debug layers and other appended-after-import
        #       layers that were tracked in channel_names at one point but
        #       got partially cleaned (channel_names was rewritten without
        #       updating /intensity, leaving extra slices on disk).
        #   (2) /intensity has more slices than channel_names entries —
        #       the launcher names them ``f"ch{i}"`` for i >= len(names),
        #       which appear in napari but aren't in metadata.
        # The user's intent in clicking Delete is "make this channel go
        # away permanently" regardless of which case. We resolve the
        # /intensity slice index from either the metadata position OR the
        # ``ch<N>`` fallback name and slice the array accordingly.
        import re
        names = list(store.metadata.get("channel_names", []))
        in_metadata = name in names

        # Resolve the /intensity slice index for this layer name
        slice_idx: int | None = None
        if in_metadata:
            slice_idx = names.index(name)
        else:
            m = re.fullmatch(r"ch(\d+)", name)
            if m:
                candidate = int(m.group(1))
                # Valid orphan-slice index = past channel_names AND within
                # intensity.shape[0]. We check the shape inside the
                # try-block below.
                slice_idx = candidate

        try:
            intensity = store.read_array("intensity")
        except KeyError:
            intensity = None

        if intensity is not None and slice_idx is not None and intensity.ndim == 3:
            if slice_idx < intensity.shape[0]:
                if intensity.shape[0] <= 1:
                    store.delete_item("intensity")
                else:
                    keep = [i for i in range(intensity.shape[0]) if i != slice_idx]
                    new_intensity = intensity[keep, :, :]
                    store.write_array(
                        "intensity", new_intensity, attrs={"dims": ["C", "H", "W"]},
                    )
            else:
                # Layer name suggested an index past the current /intensity.
                # Nothing to slice on disk; the napari layer removal below
                # still happens.
                pass
        elif intensity is not None and intensity.ndim == 2 and in_metadata:
            # 2D — single-channel dataset, deletion empties it
            store.delete_item("intensity")

        # Update channel_names if the deleted layer was in metadata.
        if in_metadata:
            new_names = [n for n in names if n != name]
            store.set_metadata({
                "channel_names": new_names,
                "n_channels": len(new_names),
            })
            handle = session.dataset
            if handle is not None:
                meta = handle.metadata
                meta["channel_names"] = new_names
                meta["n_channels"] = len(new_names)

        # Drop derived FLIM artifacts for this channel — always attempt,
        # because they may exist independently of channel_names (e.g., a
        # stale ``/decay/<name>`` from an earlier import that left the
        # /metadata stale).
        for path in (
            f"decay/{name}",
            f"phasor/{name}",
            f"provenance/decay/{name}",
        ):
            try:
                store.delete_item(path)
            except Exception:  # noqa: BLE001
                pass

        # Drop calibration metadata for this channel — always attempt.
        try:
            import h5py
            with h5py.File(store.path, "a") as f:
                if "metadata" in f:
                    for k in (f"flim_cal_phase_{name}", f"flim_cal_mod_{name}"):
                        if k in f["metadata"].attrs:
                            del f["metadata"].attrs[k]
        except Exception:  # noqa: BLE001
            pass

        # Sync the in-memory handle metadata for FLIM keys regardless.
        handle = session.dataset
        if handle is not None:
            meta = handle.metadata
            for key_prefix in ("flim_cal_phase_", "flim_cal_mod_"):
                k = f"{key_prefix}{name}"
                if k in meta:
                    del meta[k]

        # Notify subscribers that the channel inventory changed (R4).
        cur_handle = session.dataset
        if cur_handle is not None:
            session.refresh_resource_lists(
                channel_names=list(cur_handle.metadata.get("channel_names", [])),
            )

        # Clear active-channel selection if it pointed at the deleted one.
        if session.active_channel == name:
            session.set_active_channel(None)

        # Remove the napari layer.
        viewer_win = self._get_viewer_win()
        if viewer_win is not None and viewer_win.viewer is not None:
            for layer in list(viewer_win.viewer.layers):
                if layer.name == name:
                    viewer_win.viewer.layers.remove(layer)
                    break

        self.refresh_management_combos()
        if in_metadata:
            self._show_status(f"Deleted channel '{name}' permanently")
        else:
            self._show_status(
                f"Removed orphan layer '{name}' (was not in channel_names; "
                "any associated decay / phasor / calibration also cleared)"
            )
