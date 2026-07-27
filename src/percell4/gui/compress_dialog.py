"""Compress dialog for converting TIFF directories to HDF5 datasets.

Replaces ImportDialog with support for batch compression. Discovers
datasets from a root directory and lets the user select which datasets
and channels to compress. Operates at the semantic level (datasets,
channels) rather than individual files.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.domain.io.models import (
    CompressConfig,
    DatasetGuiState,
    DatasetSpec,
    LayerAssignment,
    LayerType,
    TileConfig,
    TokenConfig,
)
from percell4.domain.io.naming import channel_display_name
from percell4.gui._dialog_utils import cap_to_screen, wrap_in_scroll

# Index of the "Tokenless (by name)" entry in the Discovery combo.
_TOKENLESS_INDEX = 2


class CompressDialog(QDialog):
    """Dialog for discovering and compressing TIFF datasets to HDF5.

    Presents semantic-level selection: pick datasets (left list) and
    channels (right list). Auto mode imports all selected channels as
    intensity. Manual mode allows renaming and assigning layer types.
    """

    def __init__(self, parent=None, project_dir: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compress TIFF Dataset")
        self.setMinimumWidth(750)
        self.resize(800, 700)
        cap_to_screen(self)
        self._project_dir = project_dir

        self._datasets: list[DatasetSpec] = []
        self._all_channels: list[str] = []
        self._all_tiles: list[str] = []
        self._all_z_slices: list[str] = []
        self._all_timepoints: list[str] = []
        self._discovery_generation = 0
        # In Tokenless mode, discovery synthesizes a channel regex from the
        # derived names; cache it so _current_token_config threads the identical
        # regex into import_dataset (discovery <-> importer parity).
        self._tokenless_token_config: TokenConfig | None = None

        # Manual mode state: per-channel config (shared across datasets)
        self._channel_configs: dict[str, _ChannelConfig] = {}

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        content = QWidget()
        layout = QVBoxLayout(content)
        outer.addWidget(wrap_in_scroll(content))

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

        # ── Discovery Mode + Auto/Manual ──
        options_row = QHBoxLayout()
        options_row.addWidget(QLabel("Discovery:"))
        self._discovery_combo = QComboBox()
        self._discovery_combo.addItems(
            ["Subdirectory", "Flat Directory", "Tokenless (by name)"]
        )
        self._discovery_combo.setToolTip(
            "Subdirectory: each child folder = one dataset.\n"
            "Flat Directory: groups files by stripping token patterns\n"
            "(channel, tile, etc.) from filenames.\n"
            "Tokenless (by name): no chXX token needed — the shared leading\n"
            "prefix becomes the .h5 name and the trailing name becomes the\n"
            "channel (e.g. ..._DNA, ..._SG_mask). Use Manual mode to rename\n"
            "a mis-derived channel or assign it as mask / segmentation."
        )
        self._discovery_combo.currentIndexChanged.connect(
            self._on_discovery_mode_changed
        )
        options_row.addWidget(self._discovery_combo)

        options_row.addSpacing(30)
        options_row.addWidget(QLabel("Mode:"))
        self._auto_radio = QRadioButton("Auto")
        self._auto_radio.setChecked(True)
        self._auto_radio.toggled.connect(self._on_mode_changed)
        self._manual_radio = QRadioButton("Manual")
        options_row.addWidget(self._auto_radio)
        options_row.addWidget(self._manual_radio)
        options_row.addStretch()
        layout.addLayout(options_row)

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
        # itemChanged fires on every checkbox toggle — without this wire the
        # Compress button only refreshes via the Select All / Deselect All
        # paths, so a user who tries to enable Compress by ticking a single
        # dataset sees no effect.
        self._ds_list.itemChanged.connect(self._update_compress_button)
        ds_layout.addWidget(self._ds_list)
        lists_row.addWidget(ds_group, 3)

        # Right: channels (auto mode = simple checkboxes)
        self._ch_group = QGroupBox("Channels")
        ch_layout = QVBoxLayout(self._ch_group)

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
        # See note on _ds_list.itemChanged — the channel list has the same
        # symmetry: a single-channel toggle must refresh Compress's
        # enablement.
        self._ch_list.itemChanged.connect(self._update_compress_button)
        ch_layout.addWidget(self._ch_list)

        # Manual mode: channel config panel (hidden in auto mode)
        self._manual_ch_panel = QWidget()
        manual_ch_layout = QVBoxLayout(self._manual_ch_panel)
        manual_ch_layout.setContentsMargins(0, 4, 0, 0)

        # This will be populated dynamically per-channel
        self._manual_ch_container = QVBoxLayout()
        manual_ch_layout.addLayout(self._manual_ch_container)
        self._manual_ch_panel.setVisible(False)
        ch_layout.addWidget(self._manual_ch_panel)

        lists_row.addWidget(self._ch_group, 2)

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
        # Value rides in itemData, never the display text or the index — the
        # PR #9 drift precedent, and what lets the label change later without
        # breaking TileConfig construction. Label == value for now.
        for _gt in ("row_by_row", "column_by_column", "snake_by_row", "snake_by_column"):
            self._stitch_type.addItem(_gt, _gt)
        stitch_layout.addWidget(self._stitch_type)
        stitch_layout.addWidget(QLabel("Start:"))
        self._stitch_order = QComboBox()
        for _o in (
            "right_down", "right_up", "left_down", "left_up",
            "top_left", "top_right", "bottom_left", "bottom_right",
        ):
            self._stitch_order.addItem(_o, _o)
        stitch_layout.addWidget(self._stitch_order)
        # ── Overlap-aware registration (phase-correlation) ──
        # Overlap is stored as a FRACTION in TileConfig; the spinbox shows
        # a percentage. Register opts into the phase-correlation path,
        # gated at the importer on register ∧ overlap>0 ∧ grid>1×1.
        stitch_layout.addWidget(QLabel("Overlap:"))
        self._stitch_overlap = QDoubleSpinBox()
        self._stitch_overlap.setRange(0.0, 99.0)
        self._stitch_overlap.setSuffix("%")
        self._stitch_overlap.setValue(0.0)
        stitch_layout.addWidget(self._stitch_overlap)
        self._stitch_register = QCheckBox(
            "Register overlapping tiles (phase correlation)"
        )
        stitch_layout.addWidget(self._stitch_register)
        stitch_layout.addWidget(QLabel("Reference:"))
        # Reference channel identified by NAME. Populated from discovered
        # channels (``chXX``), editable so a free-text name is also accepted.
        # itemData carries the name verbatim (not an index).
        self._stitch_reference = QComboBox()
        self._stitch_reference.setEditable(True)
        stitch_layout.addWidget(self._stitch_reference)
        # ── Overlap fusion ──
        # "None" keeps each overlap pixel from a single tile (measurement-correct;
        # forced for FLIM datasets). "Linear Blending" feathers the seam for a
        # display mosaic. itemData carries the TileConfig value verbatim.
        stitch_layout.addWidget(QLabel("Fusion:"))
        self._stitch_fusion = QComboBox()
        self._stitch_fusion.addItem("None", "none")
        self._stitch_fusion.addItem("Linear Blending", "linear_blending")
        self._stitch_fusion.setToolTip(
            "How overlapping pixels combine. None = single tile (no intensity "
            "distortion; required when FLIM decay is present). Linear Blending "
            "= feathered seam for display (intensity-only datasets)."
        )
        stitch_layout.addWidget(self._stitch_fusion)
        stitch_layout.addStretch()
        self._stitch_widget.setVisible(False)
        settings_layout.addWidget(self._stitch_widget)

        # Creation spatial bin -- locks the dataset's native_shape at
        # compress time. Cannot change after.
        bin_row = QHBoxLayout()
        bin_row.addWidget(QLabel("Creation spatial bin (k):"))
        self._creation_bin_spin = QSpinBox()
        self._creation_bin_spin.setRange(1, 16)
        self._creation_bin_spin.setValue(1)
        self._creation_bin_spin.setToolTip(
            "Sum-bin every source channel and .bin tile k×k at import. "
            "Defines /metadata.native_shape for the new dataset. The view-bin "
            "spinner on SessionWindow can downsample further at read time, "
            "but creation_bin cannot be changed after compress. Default 1 = "
            "no binning."
        )
        # Wire valueChanged at construction even though no listener cares
        # right now -- ensures any later test that drives the user-edit
        # signal path doesn't bypass the controller (per
        # docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md).
        self._creation_bin_spin.valueChanged.connect(
            self._on_creation_bin_changed
        )
        bin_row.addWidget(self._creation_bin_spin)
        bin_row.addStretch()
        settings_layout.addLayout(bin_row)

        layout.addWidget(settings_group)

        # ── FLIM .bin Parameters (auto-shown when .bin files detected) ──
        self._flim_group = QGroupBox("FLIM .bin Parameters")
        self._flim_group.setCheckable(True)
        self._flim_group.setChecked(False)
        self._flim_group.setToolTip(
            "Parameters for raw binary TCSPC histogram (.bin) files.\n"
            "Auto-enabled when .bin files are detected during discovery."
        )
        flim_layout = QFormLayout(self._flim_group)

        self._flim_freq = QDoubleSpinBox()
        self._flim_freq.setRange(0.1, 1000.0)
        self._flim_freq.setValue(80.0)
        self._flim_freq.setDecimals(1)
        self._flim_freq.setSuffix(" MHz")
        flim_layout.addRow("Laser frequency:", self._flim_freq)

        self._bin_x = QSpinBox()
        self._bin_x.setRange(1, 10000)
        self._bin_x.setValue(512)
        flim_layout.addRow("X dimension:", self._bin_x)

        self._bin_y = QSpinBox()
        self._bin_y.setRange(1, 10000)
        self._bin_y.setValue(512)
        flim_layout.addRow("Y dimension:", self._bin_y)

        self._bin_t = QSpinBox()
        self._bin_t.setRange(1, 4096)
        self._bin_t.setValue(132)
        flim_layout.addRow("Time bins:", self._bin_t)

        self._bin_dtype = QComboBox()
        self._bin_dtype.addItems(["uint32", "uint16", "float32", "uint8"])
        flim_layout.addRow("Data type:", self._bin_dtype)

        self._bin_dim_order = QComboBox()
        self._bin_dim_order.addItems(["YXT", "XYT", "TYX"])
        flim_layout.addRow("Dimension order:", self._bin_dim_order)

        self._bin_header = QSpinBox()
        self._bin_header.setRange(0, 10000)
        self._bin_header.setValue(0)
        self._bin_header.setSpecialValueText("Auto-detect")
        flim_layout.addRow("Header bytes:", self._bin_header)

        cal_label = QLabel("Per-channel calibration (phase / modulation):")
        flim_layout.addRow(cal_label)
        self._flim_cal_container = QVBoxLayout()
        flim_layout.addRow(self._flim_cal_container)
        self._channel_calibrations: dict[str, _CalibrationConfig] = {}

        self._flim_group.toggled.connect(self._on_flim_group_toggled)
        layout.addWidget(self._flim_group)
        self._on_flim_group_toggled(False)

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

        is_manual = self._manual_radio.isChecked()

        # Gather selected channels
        selected_channels: set[str] = set()
        if is_manual:
            for ch_id, cfg in self._channel_configs.items():
                if cfg.checkbox.isChecked():
                    selected_channels.add(ch_id)
        else:
            for i in range(self._ch_list.count()):
                item = self._ch_list.item(i)
                if item.checkState() == Qt.Checked:
                    selected_channels.add(item.data(Qt.UserRole))

        # Gather layer assignments from manual mode
        layer_assignments: dict[str, LayerAssignment] | None = None
        if is_manual:
            layer_assignments = {}
            for ch_id, cfg in self._channel_configs.items():
                if cfg.checkbox.isChecked():
                    layer_assignments[ch_id] = LayerAssignment(
                        layer_type=LayerType(cfg.type_combo.currentText().lower()),
                        name=cfg.name_edit.text().strip() or channel_display_name(ch_id),
                    )

        tile_config = None
        if self._stitch_check.isChecked():
            ref = self._stitch_reference.currentText().strip()
            tile_config = TileConfig(
                grid_rows=self._stitch_rows.value(),
                grid_cols=self._stitch_cols.value(),
                grid_type=self._stitch_type.currentData(),
                order=self._stitch_order.currentData(),
                # Spinbox shows a percentage; TileConfig stores a fraction.
                overlap=self._stitch_overlap.value() / 100.0,
                register=self._stitch_register.isChecked(),
                reference_channel=ref or None,
                fusion_method=self._stitch_fusion.currentData() or "none",
            )

        # Dataset check states + name overrides
        checked_names: set[str] = set()
        dataset_name_overrides: dict[str, str] = {}
        for i in range(self._ds_list.count()):
            item = self._ds_list.item(i)
            original_name = item.data(Qt.UserRole)
            if item.checkState() == Qt.Checked:
                checked_names.add(original_name)
            display_name = item.text()
            if display_name != original_name:
                dataset_name_overrides[original_name] = display_name

        gui_states: dict[str, DatasetGuiState] = {}
        for ds in self._datasets:
            gui_states[ds.name] = DatasetGuiState(
                checked=ds.name in checked_names,
            )

        flim_params: dict | None = None
        if self._flim_group.isChecked():
            channel_calibrations: dict[str, dict[str, float]] = {}
            for ch_id, cal in self._channel_calibrations.items():
                ch_name = channel_display_name(ch_id)
                channel_calibrations[ch_name] = {
                    "phase": cal.phase_spin.value(),
                    "modulation": cal.mod_spin.value(),
                }
            flim_params = {
                "frequency_mhz": self._flim_freq.value(),
                "channel_calibrations": channel_calibrations,
                "bin_dimensions": {
                    "x_dim": self._bin_x.value(),
                    "y_dim": self._bin_y.value(),
                    "t_dim": self._bin_t.value(),
                    "dtype": self._bin_dtype.currentText(),
                    "dim_order": self._bin_dim_order.currentText(),
                    "header_bytes": self._bin_header.value(),
                },
            }

        # Re-resolve each DatasetSpec.output_path against the current
        # Output field. Discovery bakes output_path at scan time, but
        # the user can edit Output afterward without re-running
        # discovery — this materialization is the single point where
        # the typed value becomes authoritative. Without this, edits to
        # the Output field are silently discarded and .h5 files land
        # at the auto-fill location (the parent of the source dir).
        datasets = list(self._datasets)
        if output_dir is not None:
            datasets = [
                replace(ds, output_path=output_dir / f"{ds.name}.h5")
                for ds in datasets
            ]

        return CompressConfig(
            z_project_method=self._z_combo.currentText(),
            token_config=self._current_token_config(),
            output_dir=output_dir,
            selected_channels=selected_channels,
            tile_config=tile_config,
            datasets=datasets,
            gui_states=gui_states,
            layer_assignments=layer_assignments,
            dataset_name_overrides=dataset_name_overrides,
            flim_params=flim_params,
            creation_bin=int(self._creation_bin_spin.value()),
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
        # Tokenless mode derives the channel regex itself — the free-text token
        # patterns are irrelevant, so hide that group to avoid confusion.
        self._token_group.setVisible(index != _TOKENLESS_INDEX)
        if self._source_edit.text().strip():
            self._run_discovery()

    def _on_mode_changed(self, checked: bool) -> None:
        """Toggle between auto and manual mode."""
        is_manual = self._manual_radio.isChecked()
        self._ch_list.setVisible(not is_manual)
        self._manual_ch_panel.setVisible(is_manual)
        # Make dataset names editable in manual mode
        for i in range(self._ds_list.count()):
            item = self._ds_list.item(i)
            flags = item.flags()
            if is_manual:
                item.setFlags(flags | Qt.ItemIsEditable)
            else:
                item.setFlags(flags & ~Qt.ItemIsEditable)

    def _on_creation_bin_changed(self, _value: int) -> None:
        """Placeholder slot for the creation-bin spinner.

        The value is read in ``compress_config`` at Compress time, so no
        cross-widget side effect is needed here today. The slot exists so
        the user-edit signal is wired at construction (per the
        qt-wire-user-edit-signals convention).
        """

    def _on_stitch_toggled(self, checked: bool) -> None:
        self._stitch_widget.setVisible(checked)

    def _on_token_group_toggled(self, checked: bool) -> None:
        for child in self._token_group.findChildren(QWidget):
            if child is not self._token_group:
                child.setVisible(checked)

    def _on_flim_group_toggled(self, checked: bool) -> None:
        for child in self._flim_group.findChildren(QWidget):
            if child is not self._flim_group:
                child.setVisible(checked)

    def _on_select_all_datasets(self) -> None:
        self._set_list_check_state(self._ds_list, Qt.Checked)

    def _on_deselect_all_datasets(self) -> None:
        self._set_list_check_state(self._ds_list, Qt.Unchecked)

    def _on_select_all_channels(self) -> None:
        if self._manual_radio.isChecked():
            for cfg in self._channel_configs.values():
                cfg.checkbox.setChecked(True)
        else:
            self._set_list_check_state(self._ch_list, Qt.Checked)
        self._update_compress_button()

    def _on_deselect_all_channels(self) -> None:
        if self._manual_radio.isChecked():
            for cfg in self._channel_configs.values():
                cfg.checkbox.setChecked(False)
        else:
            self._set_list_check_state(self._ch_list, Qt.Unchecked)
        self._update_compress_button()

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
        # Tokenless mode: return the regex synthesized from the derived channel
        # names so both discovery and import_dataset run the identical pattern.
        if (
            self._discovery_combo.currentIndex() == _TOKENLESS_INDEX
            and self._tokenless_token_config is not None
        ):
            return self._tokenless_token_config
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
        output_dir = None
        if self._output_edit.text().strip():
            output_dir = Path(self._output_edit.text().strip())

        from percell4.domain.io.discovery import (
            discover_by_subdirectory,
            discover_flat,
            discover_tokenless,
        )

        mode_idx = self._discovery_combo.currentIndex()
        try:
            if mode_idx == _TOKENLESS_INDEX:
                # Derives its own channel regex from the filenames; cache it so
                # _current_token_config threads the identical pattern to import.
                datasets, self._tokenless_token_config = discover_tokenless(
                    root, output_dir
                )
                if not datasets:
                    self._tokenless_token_config = None
                    self._datasets = []
                    self._aggregate_tokens()
                    self._populate_lists()
                    self._ds_count_label.setText(
                        "No name-suffixed TIFFs found to group"
                    )
                    return
            elif mode_idx == 0:
                self._tokenless_token_config = None
                datasets = discover_by_subdirectory(
                    root, self._current_token_config(), output_dir
                )
            else:
                self._tokenless_token_config = None
                datasets = discover_flat(
                    root, self._current_token_config(), output_dir
                )
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
        is_manual = self._manual_radio.isChecked()

        # ── Datasets ──
        self._ds_list.blockSignals(True)
        self._ds_list.clear()
        for ds in self._datasets:
            item = QListWidgetItem(ds.name)
            flags = item.flags() | Qt.ItemIsUserCheckable
            if is_manual:
                flags |= Qt.ItemIsEditable
            item.setFlags(flags)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, ds.name)  # original name
            self._ds_list.addItem(item)
        self._ds_list.blockSignals(False)

        n = len(self._datasets)
        self._ds_count_label.setText(
            f"{n} dataset{'s' if n != 1 else ''}"
        )

        # ── Channels (auto mode list) ──
        self._ch_list.blockSignals(True)
        self._ch_list.clear()
        for ch in self._all_channels:
            item = QListWidgetItem(channel_display_name(ch))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, ch)
            self._ch_list.addItem(item)
        self._ch_list.blockSignals(False)

        # ── Channels (manual mode panel) ──
        # Built before the reference combo so the combo can read each
        # channel's (possibly renamed) name from its name_edit.
        self._build_manual_channel_panel()

        # ── Registration reference-channel combo ──
        # Seeded from each channel's CURRENT name (see _refresh_reference_combo).
        self._refresh_reference_combo()

        # ── FLIM per-channel calibration rows ──
        self._build_calibration_panel()

        # ── Summary ──
        parts = []
        if self._all_tiles:
            t = self._all_tiles
            parts.append(f"Tiles: {len(t)} (s{t[0]}\u2013s{t[-1]})")
        if self._all_z_slices:
            z = self._all_z_slices
            parts.append(f"Z-slices: {len(z)} (z{z[0]}\u2013z{z[-1]})")
        if self._all_timepoints:
            tp = self._all_timepoints
            parts.append(f"Timepoints: {len(tp)} (t{tp[0]}\u2013t{tp[-1]})")
        if not parts:
            parts.append("No tiles, z-slices, or timepoints detected")
        self._summary_label.setText("    ".join(parts))

        # Auto-enable stitching if tiles detected
        if self._all_tiles and not self._stitch_check.isChecked():
            self._stitch_check.setChecked(True)

        # Auto-enable FLIM section if any .bin files detected
        has_bin = any(
            f.path.suffix.lower() == ".bin"
            for ds in self._datasets
            for f in ds.files
        )
        if has_bin and not self._flim_group.isChecked():
            self._flim_group.setChecked(True)

        self._update_compress_button()

    def _build_manual_channel_panel(self) -> None:
        """Build the manual mode channel configuration widgets."""
        # Clear existing
        while self._manual_ch_container.count():
            child = self._manual_ch_container.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self._channel_configs.clear()

        for ch in self._all_channels:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 2, 0, 2)

            cb = QCheckBox(channel_display_name(ch))
            cb.setChecked(True)
            cb.toggled.connect(self._update_compress_button)
            row.addWidget(cb)

            name_edit = QLineEdit(channel_display_name(ch))
            name_edit.setPlaceholderText("Name")
            name_edit.setFixedWidth(100)
            # A rename here is the name the importer keys its registration
            # tiles by, so keep the reference-channel combo in sync live.
            name_edit.textChanged.connect(self._refresh_reference_combo)
            row.addWidget(name_edit)

            type_combo = QComboBox()
            type_combo.addItems(["Channel", "Segmentation", "Mask"])
            type_combo.setFixedWidth(110)
            row.addWidget(type_combo)

            row.addStretch()

            self._manual_ch_container.addWidget(row_widget)
            self._channel_configs[ch] = _ChannelConfig(
                checkbox=cb, name_edit=name_edit, type_combo=type_combo
            )

    def _refresh_reference_combo(self) -> None:
        """Rebuild the registration reference-channel combo from each channel's
        CURRENT name.

        In Manual mode a channel may be renamed (ch00 -> "ER"); the importer
        keys registration tiles by that renamed layer name, so the reference
        must be selectable by the same name — the chXX id no longer exists
        post-rename. Falls back to ``chXX`` for an unnamed channel (and in Auto
        mode, where the name_edits hold their chXX defaults). itemData carries
        the name verbatim (not an index), matching the round-trip convention.
        Preserves the user's pick by channel position across a rename, or by
        text for a free-typed entry. Wired to each name_edit's textChanged in
        _build_manual_channel_panel so it stays live.
        """
        combo = self._stitch_reference
        prev_text = combo.currentText().strip()
        prev_idx = combo.currentIndex()  # -1 when the text was free-typed
        combo.blockSignals(True)
        combo.clear()
        for ch in self._all_channels:
            cfg = self._channel_configs.get(ch)
            name = (cfg.name_edit.text().strip() if cfg else "") or channel_display_name(ch)
            combo.addItem(name, name)
        if 0 <= prev_idx < combo.count():
            combo.setCurrentIndex(prev_idx)  # same channel position, new name
        elif prev_text:
            idx = combo.findText(prev_text)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setCurrentText(prev_text)  # genuine free-text pick
        combo.blockSignals(False)

    def _build_calibration_panel(self) -> None:
        """Build per-channel phase/modulation widgets for FLIM calibration.

        Mirrors the historical ImportDialog._discover_channels layout:
        one QGroupBox per channel with "Phase:" + "Modulation:" form rows.
        Calibration is applied as a Cartesian rotation in
        ``compute_phasor`` using the values stored as
        ``flim_cal_phase_<ch>`` / ``flim_cal_mod_<ch>`` HDF5 metadata —
        the same convention flimfret/preprocessing.py uses for its
        phi_cal / m_cal correction.
        """
        while self._flim_cal_container.count():
            child = self._flim_cal_container.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._channel_calibrations.clear()

        if not self._all_channels:
            return

        for ch in self._all_channels:
            ch_name = channel_display_name(ch)
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

            self._flim_cal_container.addWidget(group)
            self._channel_calibrations[ch] = _CalibrationConfig(
                phase_spin=phase_spin, mod_spin=mod_spin
            )

        # New widgets default to visible — re-apply collapsed state if the
        # FLIM group is currently unchecked so they don't appear orphaned.
        self._on_flim_group_toggled(self._flim_group.isChecked())

    def _update_compress_button(self) -> None:
        any_ds = any(
            self._ds_list.item(i).checkState() == Qt.Checked
            for i in range(self._ds_list.count())
        )
        if self._manual_radio.isChecked():
            any_ch = any(
                cfg.checkbox.isChecked() for cfg in self._channel_configs.values()
            )
        else:
            any_ch = any(
                self._ch_list.item(i).checkState() == Qt.Checked
                for i in range(self._ch_list.count())
            )
        self._btn_compress.setEnabled(any_ds and any_ch)

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------



class _ChannelConfig:
    """Holds the manual-mode widgets for a single channel."""

    __slots__ = ("checkbox", "name_edit", "type_combo")

    def __init__(
        self, checkbox: QCheckBox, name_edit: QLineEdit, type_combo: QComboBox
    ) -> None:
        self.checkbox = checkbox
        self.name_edit = name_edit
        self.type_combo = type_combo


class _CalibrationConfig:
    """Holds the FLIM phase/modulation widgets for a single channel."""

    __slots__ = ("phase_spin", "mod_spin")

    def __init__(self, phase_spin: QDoubleSpinBox, mod_spin: QDoubleSpinBox) -> None:
        self.phase_spin = phase_spin
        self.mod_spin = mod_spin


def _sort_key(val: str) -> tuple[int, str]:
    """Sort token values numerically if possible, else alphabetically."""
    try:
        return (0, str(int(val)).zfill(10))
    except ValueError:
        return (1, val)
