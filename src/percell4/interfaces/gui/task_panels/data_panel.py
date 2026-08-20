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
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.gui import theme
from percell4.gui._dialog_utils import message_box, text_input
from percell4.model import CellDataModel


def _format_pixel_size_lines(
    pixel_size_um: float | None, active_bin: int,
) -> str:
    """Format the Dataset Info pixel-size line(s).

    Returns one line for the stored (creation-bin-scaled) pixel size,
    plus a second line for the view-bin effective size when
    ``active_bin > 1``. ``None`` pixel size renders as ``unknown`` so
    legacy datasets without TIFF resolution metadata are explicit.
    """
    if pixel_size_um is None or pixel_size_um <= 0:
        return "Pixel size: unknown"
    area_um2 = pixel_size_um * pixel_size_um
    base = f"Pixel size: {pixel_size_um:.4f} µm/px ({area_um2:.5f} µm²/px)"
    if active_bin and active_bin > 1:
        eff = pixel_size_um * active_bin
        eff_area = eff * eff
        return (
            f"{base}\n"
            f"View-bin pixel size: {eff:.4f} µm/px ({eff_area:.5f} µm²/px)"
        )
    return base


def _format_description_lines(description: str | None) -> str:
    """Format the Dataset Info description block.

    Renders the description in full -- the label word-wraps, and nothing is
    truncated or elided, because a truncated description defeats the point
    of storing it. ``None`` renders as an explicit "not set" line so the
    absence is distinguishable from the feature being missing.
    """
    if not description:
        return "Description: not set"
    return f"Description:\n{description}"


# Sentinel for refresh_dataset_info's optional description override. A plain
# ``None`` default could not distinguish "caller passed nothing" from
# "caller knows there is no description".
_UNSET = object()


def _read_layer_bin_attrs(store, group: str) -> dict[str, int | None]:
    """Read each ``/{group}/<name>``'s ``created_at_bin`` attr, if present.

    Returns ``{name: created_at_bin or None}``. A single h5py open
    avoids one read per item (which mattered on real-user datasets
    with dozens of segmentations / masks). Failures are silent --
    annotation is a UX detail; missing attrs shouldn't break the list.
    """
    import h5py

    result: dict[str, int | None] = {}
    try:
        with h5py.File(store.path, "r") as f:
            if group not in f:
                return result
            for name in f[group]:
                attrs = f[f"{group}/{name}"].attrs
                if "created_at_bin" in attrs:
                    try:
                        result[name] = int(attrs["created_at_bin"])
                    except (TypeError, ValueError):
                        result[name] = None
                else:
                    result[name] = None
    except Exception:
        pass
    return result


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

        layout.addWidget(theme.section_label("Data"))

        # ── Dataset Management ──
        # Named for the dataset, not only its layers: the description below
        # belongs to the dataset itself, and this is the launcher's one
        # place for add/rename/delete against an open dataset.
        mgmt_group = QGroupBox("Dataset Management")
        self._mgmt_group = mgmt_group
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

        mgmt_layout.addWidget(QLabel("Description:"))
        desc_mgmt_row = QHBoxLayout()
        desc_mgmt_row.addStretch()
        self._btn_edit_description = QPushButton("Edit...")
        self._btn_edit_description.setToolTip(
            "Add, change, or remove this dataset's experiment description."
        )
        self._btn_edit_description.clicked.connect(self._on_edit_description)
        desc_mgmt_row.addWidget(self._btn_edit_description)
        mgmt_layout.addLayout(desc_mgmt_row)

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
        if change.bin:
            # The view-bin display lives in the info label, and the
            # combo annotations (display "[k=N]" for results created at
            # a different bin) are bin-relative -- both refresh on a
            # bin change. DataPanel does NOT write session.active_bin:
            # the SessionWindow SpinBox owns that.
            self.refresh_dataset_info()
            self.refresh_management_combos()

    def _refresh_seg_combos(self) -> None:
        """Re-list the Management Segmentations dropdown from the store.

        Each item's display text is suffixed with ``[k=N]`` when the
        underlying ``/labels/<name>`` carries a ``created_at_bin`` attr
        (U5 naming convention). The combo's userData stores the clean
        underlying name so rename/delete handlers don't need to parse
        the annotation off.
        """
        store = self._get_store()
        self._mgmt_seg_combo.blockSignals(True)
        self._mgmt_seg_combo.clear()
        if store is not None:
            attrs_by_name = _read_layer_bin_attrs(store, "labels")
            for name in store.list_labels():
                self._add_combo_item_with_bin_annotation(
                    self._mgmt_seg_combo, name, attrs_by_name.get(name)
                )
        self._mgmt_seg_combo.blockSignals(False)

    def _refresh_mask_combos(self) -> None:
        """Re-list the Management Masks dropdown from the store.

        Annotates each item with ``[k=N]`` when the underlying mask
        carries a ``created_at_bin`` attr; see :meth:`_refresh_seg_combos`.
        """
        store = self._get_store()
        self._mgmt_mask_combo.blockSignals(True)
        self._mgmt_mask_combo.clear()
        if store is not None:
            attrs_by_name = _read_layer_bin_attrs(store, "masks")
            for name in store.list_masks():
                self._add_combo_item_with_bin_annotation(
                    self._mgmt_mask_combo, name, attrs_by_name.get(name)
                )
        self._mgmt_mask_combo.blockSignals(False)

    def _add_combo_item_with_bin_annotation(
        self, combo, name: str, created_at_bin: int | None
    ) -> None:
        """Add ``name`` to ``combo`` with the canonical name as userData
        and a display string that suffixes ``[k=N]`` when the layer was
        produced at a non-default bin.
        """
        from qtpy.QtCore import Qt
        if created_at_bin is not None and int(created_at_bin) > 1:
            display = f"{name}  [k={int(created_at_bin)}]"
        else:
            display = name
        combo.addItem(display)
        combo.setItemData(combo.count() - 1, name, Qt.UserRole)

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
            attrs_by_name = _read_layer_bin_attrs(store, "labels")
            for name in store.list_labels():
                self._add_combo_item_with_bin_annotation(
                    self._mgmt_seg_combo, name, attrs_by_name.get(name)
                )

        self._mgmt_mask_combo.clear()
        if store is not None:
            attrs_by_name = _read_layer_bin_attrs(store, "masks")
            for name in store.list_masks():
                self._add_combo_item_with_bin_annotation(
                    self._mgmt_mask_combo, name, attrs_by_name.get(name)
                )

        self._mgmt_chan_combo.clear()
        seen: set[str] = set()
        session = self.data_model.session
        if session.dataset is not None:
            for name in session.dataset.metadata.get("channel_names", []):
                if name not in seen:
                    self._mgmt_chan_combo.addItem(name)
                    seen.add(name)
        viewer_win = self._get_viewer_win()
        if viewer_win is not None and viewer_win.existing_viewer is not None:
            for layer in viewer_win.viewer.layers:
                if layer.__class__.__name__ != "Image":
                    continue
                # Underscore-prefixed layers are transient overlays (e.g.
                # ``_cellpose_preview``), not channels -- same convention
                # the Labels paths use; see
                # docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md §5.
                if layer.name.startswith("_"):
                    continue
                if layer.name not in seen:
                    self._mgmt_chan_combo.addItem(layer.name)
                    seen.add(layer.name)

    def refresh_dataset_info(self, description: Any = _UNSET) -> None:
        """Refresh the Dataset Info label from the current store.

        Shows the canonical on-disk shape, the dataset's native (k=1)
        H, W from /metadata.native_shape (U1), the current session
        view bin from session.active_bin (U4), and the dataset's free-text
        description. The active-bin line is read-only here -- the
        SessionWindow SpinBox owns the toggle.

        ``description`` lets a caller that just wrote a description pass the
        known-good text instead of having it re-read from disk. HDF5 keeps a
        per-process metadata cache, so a read opened right after a write in
        the same process can serve a stale value -- see
        ``docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md``.
        Omit it and the description is read from the store as usual.
        """
        store = self._get_store()
        h5_path = self._get_h5_path()
        if store is None or h5_path is None:
            self._info_label.setText("No dataset loaded")
            return
        try:
            n_labels = len(store.list_labels())
            n_masks = len(store.list_masks())
            # Shape only — read HDF5 metadata, never the array. Reading the
            # full intensity here (it ran twice per load) was the dominant
            # large-file load cost.
            shape = store.array_shape("intensity")
            session = self.data_model.session
            meta = store.metadata
            native_shape = meta.get("native_shape")
            creation_bin = meta.get("creation_bin", 1)
            active_bin = session.active_bin
            pixel_size_um = meta.get("pixel_size_um")
            native_line = (
                f"Native: {tuple(native_shape)}"
                if native_shape is not None
                else "Native: (unknown)"
            )
            # Names the two distinct binning values so neither reads as the
            # other: the import-time value is explicitly "Imported at", and the
            # live session value matches the Session window's "Pixel Binning:"
            # Selector label.
            bin_line = (
                f"Imported at binning: {creation_bin}  |  "
                f"Pixel binning: {active_bin}"
            )
            pixel_size_lines = _format_pixel_size_lines(
                pixel_size_um, active_bin,
            )
            # Read the description in its own guard: a description that
            # fails to read must not blank the facts above it.
            if description is _UNSET:
                try:
                    description = store.description
                except Exception:  # noqa: BLE001
                    description = None
            description_lines = _format_description_lines(description)
            self._info_label.setText(
                f"File: {Path(h5_path).name}\n"
                f"Shape: {shape}\n"
                f"{native_line}\n"
                f"{bin_line}\n"
                f"{pixel_size_lines}\n"
                f"Labels: {n_labels}  |  Masks: {n_masks}\n"
                f"{description_lines}"
            )
        except Exception:
            pass

    def clear_ui(self) -> None:
        """Reset all UI state (called on dataset close)."""
        self._info_label.setText("No dataset loaded")
        self._mgmt_seg_combo.clear()
        self._mgmt_mask_combo.clear()
        self._mgmt_chan_combo.clear()

    # ── Dataset description ───────────────────────────────────

    def _prompt_for_description(self, current: str | None):
        """Show the description editor. Seam for tests to drive the dialog."""
        from percell4.gui.description_dialog import edit_description

        return edit_description(self, current)

    def _on_edit_description(self) -> None:
        """Edit the loaded dataset's description, then refresh the display.

        The refresh is handed the text we just wrote rather than re-reading
        it: HDF5 keeps a per-process metadata cache, so a read opened right
        after a write in the same process can serve a stale value. The same
        known-good text goes into the session's dataset snapshot, which is
        frozen at load time and would otherwise keep the old description.
        See ``docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md``.
        """
        store = self._get_store()
        if store is None:
            self._show_status("Open a dataset first — no dataset is loaded")
            return

        try:
            current = store.description
        except Exception as exc:  # noqa: BLE001
            self._show_status(f"Could not read the description: {exc}")
            return

        result = self._prompt_for_description(current)
        if not result.accepted:
            return

        new_text = None if result.clear else result.text
        try:
            # The store returns what it actually stored -- blank text is a
            # clear -- so the display never shows text the file does not hold.
            stored = store.set_description(new_text)
        except Exception as exc:  # noqa: BLE001
            self._show_status(f"Could not save the description: {exc}")
            return

        self._sync_description_snapshot(stored)
        self.refresh_dataset_info(description=stored)
        self._show_status(
            "Description cleared" if stored is None else "Description saved"
        )

    def _sync_description_snapshot(self, description: str | None) -> None:
        """Push the known-good description into the session's dataset snapshot.

        ``DatasetHandle`` is frozen but its ``metadata`` dict is mutable,
        which is the documented way to keep in-memory readers in step with a
        just-completed write.
        """
        handle = self.data_model.session.dataset
        if handle is None:
            return
        if description is None:
            handle.metadata.pop("description", None)
        else:
            handle.metadata["description"] = description

    def _on_rename_layer(self, prefix: str) -> None:
        combo = self._mgmt_seg_combo if prefix == "labels" else self._mgmt_mask_combo
        # Read the clean underlying name from Qt.UserRole, falling back to
        # display text for the channel combo (which doesn't carry a [k=N]
        # annotation) and for items added before this change.
        old_name = combo.currentData(Qt.UserRole) or combo.currentText()
        if not old_name:
            self._show_status("Nothing selected to rename")
            return

        new_name, ok = text_input(
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
        if viewer_win is not None and viewer_win.existing_viewer is not None:
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
        # Clean name from Qt.UserRole (annotations like "[k=3]" must not
        # leak into the HDF5 path).
        name = combo.currentData(Qt.UserRole) or combo.currentText()
        if not name:
            self._show_status("Nothing selected to delete")
            return

        reply = message_box(
            self,
            "Confirm Delete",
            f"Delete '{name}'? This cannot be undone.",
            icon=QMessageBox.Question,
            buttons=QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        store = self._get_store()
        if store is not None:
            store.delete_item(f"{prefix}/{name}")

        viewer_win = self._get_viewer_win()
        if viewer_win is not None and viewer_win.existing_viewer is not None:
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

        new_name, ok = text_input(
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
        # without requiring a dataset reload. Re-read channel_names from the
        # store rather than patching the old list by index: renaming an
        # unnamed /intensity slice (a ``ch<N>`` placeholder) APPENDS a name
        # and bumps n_channels, which no in-memory substitution can produce.
        session = self.data_model.session
        handle = session.dataset
        if handle is not None:
            meta = handle.metadata
            if store is not None:
                names = list(store.metadata.get("channel_names", []))
                meta["n_channels"] = len(names)
            else:
                names = list(meta.get("channel_names", []))
                if old_name in names:
                    names[names.index(old_name)] = new_name
            meta["channel_names"] = names
            for key_prefix in ("flim_cal_phase_", "flim_cal_mod_"):
                old_key = f"{key_prefix}{old_name}"
                new_key = f"{key_prefix}{new_name}"
                if old_key in meta:
                    meta[new_key] = meta.pop(old_key)
            session.refresh_resource_lists(channel_names=names)
            if session.active_channel == old_name:
                session.set_active_channel(new_name)

        viewer_win = self._get_viewer_win()
        if viewer_win is not None and viewer_win.existing_viewer is not None:
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

        reply = message_box(
            self,
            "Confirm Delete",
            f"Permanently delete channel '{name}' and its FLIM data "
            "(decay, phasor, calibration) from the .h5 file? "
            "This cannot be undone.",
            icon=QMessageBox.Question,
            buttons=QMessageBox.Yes | QMessageBox.No,
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
        from percell4.domain.io.layout import placeholder_channel_index

        names = list(store.metadata.get("channel_names", []))
        in_metadata = name in names

        # Resolve the /intensity slice index for this layer name. The
        # placeholder→index rule lives beside the code that synthesizes the
        # name (domain/io/layout.py) so this path and rename_channel's
        # promotion resolve the same slice.
        slice_idx: int | None = None
        if in_metadata:
            slice_idx = names.index(name)
        else:
            # Valid orphan-slice index = past channel_names AND within
            # intensity.shape[0]. We check the shape inside the
            # try-block below.
            slice_idx = placeholder_channel_index(name)

        try:
            intensity = store.read_array("intensity")
        except KeyError:
            intensity = None

        if intensity is not None and slice_idx is not None:
            # Disambiguate a leading T axis from a leading C axis via
            # n_timepoints before slicing: on (T,H,W) deleting the channel
            # empties /intensity (never slice a timepoint); on (T,C,H,W) slice
            # the C axis. Non-time-lapse (C,H,W)/(H,W) behavior is unchanged.
            from percell4.domain.io.layout import plan_channel_deletion

            nt = int(store.metadata.get("n_timepoints", 1) or 1)
            action, new_intensity, dims = plan_channel_deletion(
                intensity, slice_idx, nt
            )
            if action == "delete":
                # Only empty a genuinely 2D dataset when the layer is a real
                # channel (a ch<N> orphan on a 2D dataset must not delete it).
                if intensity.ndim != 2 or in_metadata:
                    store.delete_item("intensity")
            elif action == "write":
                store.write_array("intensity", new_intensity, attrs={"dims": dims})
            # "noop": index past the channel axis; napari layer removal still
            # happens below.

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
        if viewer_win is not None and viewer_win.existing_viewer is not None:
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
