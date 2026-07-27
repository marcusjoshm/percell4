"""Configuration dialog for the single-cell thresholding workflow.

Collects the full :class:`WorkflowConfig` before a batch run starts:

- Dataset picker (individual ``.h5`` files, a folder of ``.h5`` files, a
  single ``.tiff`` source, or a batch of ``.tiff`` folders — the latter
  two nested via the existing :class:`CompressDialog`)
- Cellpose settings group
- Ordered list of thresholding rounds (inline ``QTableWidget``)
- CSV column picker driven by the current channel intersection × rounds
- Output parent folder (remembered via ``QSettings``)

Start button validation runs channel intersection (handling both
``h5_existing`` and ``tiff_pending`` sources), prompts the user to drop
outliers or abort, builds the frozen :class:`WorkflowConfig` (which runs
``__post_init__`` validation), and rejects with a ``QMessageBox.warning``
on any failure.

The dialog is a standard value-capture ``QDialog``: call ``exec_()``,
check the return, read ``.workflow_config`` on Accepted. The configured
run is NOT started here — the caller (launcher Start button) owns that.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from qtpy.QtCore import QSettings, Qt
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
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from percell4.domain.io.models import LayerType
from percell4.domain.io.naming import channel_display_name
from percell4.domain.measure.metrics import BUILTIN_METRICS
from percell4.gui._cellpose_settings_form import CellposeSettingsForm
from percell4.gui._dialog_utils import cap_to_screen, wrap_in_scroll
from percell4.gui.workflows.single_cell.round_card import (
    METHOD_AUTO_EXTRACT,
    RoundCard,
)
from percell4.store import DatasetStore
from percell4.workflows.channels import ChannelSource, intersect_channels
from percell4.workflows.csv_columns import (
    CORE_OPTIONAL_COLUMNS,
    DEFAULT_CSV_METRICS,
    DEFAULT_CSV_PARTICLE_PER_CELL,
    DEFAULT_CSV_PARTICLE_PER_CHANNEL,
    build_selected_csv_columns,
)
from percell4.workflows.masks import intersect_masks
from percell4.workflows.models import (
    AutoExtractSettings,
    CellposeSettings,
    CnrClassifySettings,
    DatasetSource,
    DiluteSettings,
    EdgeMode,
    GmmCriterion,
    ParticleSettings,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

_QSETTINGS_ORG = "LeeLabPerCell4"
_QSETTINGS_APP = "PerCell4"
_QSETTINGS_OUTPUT_KEY = "single_cell_threshold_workflow/output_parent"

# Always-on identity columns prepended to the CSV column picker.
_ALWAYS_ON_COLUMNS = ("dataset", "cell_id", "label")

# Core per-cell columns the user may opt into. Aliased to the shared
# Qt-free source of truth (also consumed by percell4-batch-measure).
_CORE_OPTIONAL_COLUMNS = CORE_OPTIONAL_COLUMNS

# Particle-analysis per-cell summary metrics (U7). Single value per cell;
# the CSV column shape is "<round_name>_<metric>" — one column per
# (round × metric) when particle analysis is enabled.
_PARTICLE_PER_CELL_METRICS = (
    "particle_count",
    "total_particle_area",
    "mean_particle_area",
    "max_particle_area",
    "particle_coverage_fraction",
)

# Particle-analysis per-channel summary metrics (U7). One value per
# (cell, channel). The CSV column shape is "<round_name>_<channel>_<metric>"
# — one column per (round × channel × metric).
#
# The set mirrors BUILTIN_METRICS' intensity metrics (area is excluded
# since the particle's area is a per-cell quantity rolled up via
# particle_count / total_particle_area / mean_particle_area).
# Aggregation per cell uses each metric's natural reducer (see
# _PARTICLE_AGGREGATORS in particle.py).
_PARTICLE_PER_CHANNEL_METRICS = (
    "particle_mean_intensity",
    "particle_max_intensity",
    "particle_min_intensity",
    "particle_integrated_intensity",
    "particle_std_intensity",
    "particle_median_intensity",
    "particle_mode_intensity",
    "particle_sg_ratio",
)

# The rounds editor is a vertical list of RoundCard widgets (see round_card.py),
# not a table. Method labels and the value-dict contract live on the card;
# METHOD_GROUPED / METHOD_AUTO_EXTRACT are imported for the ThresholdingRound build.


# ── Internal per-dataset record ──────────────────────────────────────────


def _derive_tiff_pending_channel_names(
    selected_token_ids: list[str],
    layer_assignments: dict[str, Any],
) -> list[str]:
    """Resolve workflow-side channel names for a tiff_pending dataset.

    ``selected_token_ids`` carries the raw token IDs from the compress
    dialog — numeric (``"00"``, ``"01"``, …) for the ``chXX`` convention or a
    channel name (``"DNA"``, ``"SG_mask"``) for a tokenless import.
    ``layer_assignments`` may map a token to a ``LayerAssignment`` with a
    user-chosen display name.

    When the user did not rename a channel, fall back to
    :func:`channel_display_name` — the single shared helper the importer
    (producer) also uses to write ``/metadata.channel_names``. This keeps the
    workflow side and the HDF5 side byte-for-byte in sync for both numeric
    (``"02"`` → ``"ch02"``) and name (``"DNA"`` → ``"DNA"``) tokens. A bare-token
    fallback used to produce a silent mismatch (workflow config ``"02"`` vs HDF5
    ``"ch02"``) that wrecked ``threshold_compute`` after a long segmentation pass.

    Tokens assigned a ``segmentation`` or ``mask`` layer type are skipped:
    ``import_dataset`` routes those into ``/labels`` and ``/masks`` and never
    appends them to ``/metadata.channel_names``. Including them here would
    offer a mask as a selectable *channel* in the rounds and Cellpose combos,
    and would poison ``intersect_channels`` with a name no dataset reports.
    """
    out: list[str] = []
    for ch_id in selected_token_ids:
        override = layer_assignments.get(ch_id)
        if override is not None:
            layer_type = getattr(override, "layer_type", LayerType.CHANNEL)
            if layer_type != LayerType.CHANNEL:
                continue
        name = getattr(override, "name", "") if override is not None else ""
        out.append(name or channel_display_name(ch_id))
    return out


def _build_compress_plan(
    ds: Any,
    gui_state: Any,
    cfg: Any,
    selected_token_ids: list[str],
    layer_assignments_payload: dict[str, Any],
) -> dict[str, Any]:
    """Serialize a ``DatasetSpec`` + ``CompressConfig`` into a compress_plan dict.

    The plan is the JSON-safe payload persisted into ``run_config.json``
    and consumed by ``percell4.workflows.phases.compress_one``. Pulled
    out of ``_add_tiff_via_compress_dialog`` so the construction is
    unit-testable without instantiating the Qt dialog.

    ``tile_config`` is taken from the per-dataset
    ``DatasetGuiState.tile_config_override`` if present, otherwise from
    the global ``CompressConfig.tile_config``. Omitted entirely when
    neither is set. Forgetting this key was the bug that caused
    multi-tile single-cell workflow runs to land in the .h5 with only
    the first scene's pixels.
    """
    plan: dict[str, Any] = {
        "source_dir": str(ds.source_dir) if ds.source_dir else "",
        "files": [str(f.path) for f in ds.files],
        "output_path": str(ds.output_path),
        "z_project_method": cfg.z_project_method,
        "selected_channels": list(selected_token_ids),
        "layer_assignments": layer_assignments_payload,
        # Sum-binning factor. ``compress_one`` has always read this key; the
        # producer never wrote it, so the dialog's binning spinbox was
        # silently ignored on the workflow path.
        "creation_bin": int(getattr(cfg, "creation_bin", 1)),
    }

    # Filename-token regexes. For a tokenless (name-suffixed) import this is
    # the pattern ``discover_tokenless`` synthesized inside the CompressDialog;
    # for a normal import it is the default or whatever the user edited.
    # Omitting it made ``import_dataset`` fall back to ``TokenConfig()``
    # (channel = ``_ch(\d+)``), which matches nothing for tokenless sources —
    # every file grouped under "", the selected_channels filter dropped all
    # groups, and the .h5 landed with no /intensity and empty channel_names.
    # Patterns are Optional[str]; a disabled token stays JSON null.
    token_config = getattr(cfg, "token_config", None)
    if token_config is not None:
        plan["token_config"] = {
            "channel": token_config.channel,
            "timepoint": token_config.timepoint,
            "z_slice": token_config.z_slice,
            "tile": token_config.tile,
        }

    # FLIM/TCSPC calibration. Built entirely from spinbox ints, floats, and
    # combobox strings, so it is JSON-safe as-is. Without it a TIFF-start run
    # on FLIM-bearing sources produces an .h5 with no usable phasor data.
    flim_params = getattr(cfg, "flim_params", None)
    if flim_params:
        plan["flim_params"] = flim_params

    tile_config = (
        getattr(gui_state, "tile_config_override", None) if gui_state else None
    ) or getattr(cfg, "tile_config", None)
    if tile_config is not None:
        ref = getattr(tile_config, "reference_channel", None)
        plan["tile_config"] = {
            "grid_rows": int(tile_config.grid_rows),
            "grid_cols": int(tile_config.grid_cols),
            "grid_type": str(tile_config.grid_type),
            "order": str(tile_config.order),
            # Overlap-aware registration fields (hop 2). A dropped key here
            # silently disables the feature, so they ride alongside the grid.
            "overlap": float(getattr(tile_config, "overlap", 0.0)),
            "register": bool(getattr(tile_config, "register", False)),
            "reference_channel": str(ref) if ref else None,
        }

    return plan


class _PendingDataset:
    """Lightweight record of one user-added dataset inside the dialog.

    Stored directly on the dialog (not as a dataclass) because we need a
    mutable ``display_name`` slot for the disambiguation pass.
    """

    __slots__ = (
        "display_name",
        "source",
        "h5_path",
        "channel_names",
        "compress_plan",
    )

    def __init__(
        self,
        *,
        display_name: str,
        source: DatasetSource,
        h5_path: Path,
        channel_names: list[str],
        compress_plan: dict[str, Any] | None = None,
    ) -> None:
        self.display_name = display_name
        self.source = source
        self.h5_path = h5_path
        self.channel_names = channel_names
        self.compress_plan = compress_plan

    def dedupe_key(self) -> Any:
        """Identity used to skip duplicates on add.

        For existing ``.h5`` files, the resolved path is the unique ID.
        For pending tiff sources, the (source_dir, file tuple) is.
        """
        if self.source is DatasetSource.H5_EXISTING:
            try:
                return ("h5", str(self.h5_path.resolve()))
            except OSError:
                return ("h5", str(self.h5_path))
        # tiff_pending: the compress plan carries the identity
        plan = self.compress_plan or {}
        src_dir = plan.get("source_dir", "")
        files = tuple(plan.get("files", ()))
        return ("tiff", str(src_dir), files)

    def to_entry(self) -> WorkflowDatasetEntry:
        return WorkflowDatasetEntry(
            name=self.display_name,
            source=self.source,
            h5_path=self.h5_path,
            channel_names=list(self.channel_names),
            compress_plan=self.compress_plan,
        )


# ── Two-pane mask group builder ──────────────────────────────────────────

# Keep each checklist compact; it scrolls if it has many rows.
_MASK_PANE_MAX_H = 160


def _checked_texts(list_widget: QListWidget) -> list[str]:
    """Texts of the checked items in a checkable QListWidget, in row order."""
    return [
        list_widget.item(i).text()
        for i in range(list_widget.count())
        if list_widget.item(i).checkState() == Qt.Checked
    ]


def _set_all_checked(list_widget: QListWidget, checked: bool) -> None:
    """Check or uncheck every item in a checkable QListWidget."""
    state = Qt.Checked if checked else Qt.Unchecked
    for i in range(list_widget.count()):
        list_widget.item(i).setCheckState(state)


def _single_checked(list_widget: QListWidget) -> str | None:
    """The text of the one checked item in a single-select list, or None."""
    checked = _checked_texts(list_widget)
    return checked[0] if checked else None


class _MaskGroupPanel:
    """One two-pane group: a Datasets checklist driving a Masks checklist.

    ``ds_list`` holds every mask-bearing dataset (checkable); ``mask_list`` holds
    the intersection of the checked datasets' available masks (checkable).
    ``remove_btn`` is ``None`` for the first (non-removable) panel. ``container``
    is the widget added to the groups layout.
    """

    __slots__ = ("container", "ds_list", "mask_list", "remove_btn")

    def __init__(
        self,
        *,
        container: QWidget,
        ds_list: QListWidget,
        mask_list: QListWidget,
        remove_btn: QPushButton | None,
    ) -> None:
        self.container = container
        self.ds_list = ds_list
        self.mask_list = mask_list
        self.remove_btn = remove_btn


class _SegGroupPanel:
    """One two-pane group: a Datasets checklist and a single-select Segmentation
    list (the segmentation layer common to the checked datasets that they should
    use). Unlike the mask panel, the right list is pick-one (exclusive checks).
    ``remove_btn`` is ``None`` for the first (non-removable) panel.
    """

    __slots__ = ("container", "ds_list", "seg_list", "remove_btn")

    def __init__(
        self,
        *,
        container: QWidget,
        ds_list: QListWidget,
        seg_list: QListWidget,
        remove_btn: QPushButton | None,
    ) -> None:
        self.container = container
        self.ds_list = ds_list
        self.seg_list = seg_list
        self.remove_btn = remove_btn


# ── Dialog ──────────────────────────────────────────────────────────────


class WorkflowConfigDialog(QDialog):
    """Modal configuration dialog for the single-cell thresholding workflow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Single-cell thresholding analysis workflow")
        self.setModal(True)
        self.resize(960, 860)
        cap_to_screen(self)

        # State
        self._pending_datasets: list[_PendingDataset] = []
        # Default CSV column selection (user can change via Configure
        # CSV Export). Channels auto-select to all intersected until the
        # user makes an explicit choice (tracked by _csv_channels_auto).
        self._selected_csv_channels: set[str] = set()
        self._csv_channels_auto = True
        self._selected_csv_metrics: set[str] = set(DEFAULT_CSV_METRICS)
        # U7 particle metrics — independent picker state (only applied
        # to CSV columns when particle analysis is enabled at run time).
        self._selected_csv_particle_per_cell: set[str] = set(
            DEFAULT_CSV_PARTICLE_PER_CELL
        )
        self._selected_csv_particle_per_channel: set[str] = set(
            DEFAULT_CSV_PARTICLE_PER_CHANNEL
        )
        self._workflow_config: WorkflowConfig | None = None

        self._build_ui()
        self._refresh_dataset_tree()
        self._refresh_round_channels()
        self._refresh_column_picker()
        self._update_start_enabled()

    # ── UI construction ───────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        layout.addWidget(self._build_datasets_group(), stretch=3)
        layout.addWidget(self._build_cellpose_group())
        layout.addWidget(self._build_segmentation_group())
        layout.addWidget(self._build_mask_selection_group())
        self._rounds_group_box = self._build_rounds_group()
        layout.addWidget(self._rounds_group_box, stretch=2)
        layout.addWidget(self._build_particles_group())
        layout.addWidget(self._build_dilute_group())
        layout.addWidget(self._build_columns_group())
        layout.addWidget(self._build_output_group())
        layout.addStretch()

        outer.addWidget(wrap_in_scroll(content), stretch=1)

        # Dialog buttons — outside the scroll area so Start/Cancel
        # are always visible at the bottom.
        btn_box = QDialogButtonBox(QDialogButtonBox.Cancel)
        self._start_btn = QPushButton("Start")
        self._start_btn.setDefault(True)
        btn_box.addButton(self._start_btn, QDialogButtonBox.AcceptRole)
        btn_box.rejected.connect(self.reject)
        self._start_btn.clicked.connect(self._on_start_clicked)
        btn_bar = QWidget()
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(12, 6, 12, 6)
        btn_layout.addWidget(btn_box)
        outer.addWidget(btn_bar)

    def _build_datasets_group(self) -> QGroupBox:
        box = QGroupBox("Datasets")
        outer = QVBoxLayout(box)

        self._dataset_tree = QTreeWidget()
        self._dataset_tree.setHeaderLabels(("Name", "Source", "Path", "Channels"))
        self._dataset_tree.setRootIsDecorated(False)
        self._dataset_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self._dataset_tree.setMinimumHeight(120)
        self._dataset_tree.header().setStretchLastSection(True)
        self._dataset_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._dataset_tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self._dataset_tree.header().setSectionResizeMode(
            2, QHeaderView.Stretch
        )
        outer.addWidget(self._dataset_tree, stretch=1)

        btn_row = QHBoxLayout()
        btn_add_h5 = QPushButton("Add .h5 files...")
        btn_add_h5.clicked.connect(self._on_add_h5_files)
        btn_row.addWidget(btn_add_h5)

        btn_add_h5_folder = QPushButton("Add folder of .h5...")
        btn_add_h5_folder.clicked.connect(self._on_add_h5_folder)
        btn_row.addWidget(btn_add_h5_folder)

        btn_add_tiff = QPushButton("Add .tiff files...")
        btn_add_tiff.setToolTip(
            "Open the compress dataset dialog to discover .tiff files "
            "(single dataset or a folder of datasets) and configure "
            "compression. Channels you rename or deselect there are "
            "carried into this run's config."
        )
        btn_add_tiff.clicked.connect(self._on_add_tiff_files)
        btn_row.addWidget(btn_add_tiff)

        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self._on_remove_dataset)
        btn_row.addWidget(btn_remove)

        btn_row.addStretch()
        outer.addLayout(btn_row)

        # Status line for dedupe toasts, validation hints, etc.
        self._dataset_status = QLabel("")
        self._dataset_status.setStyleSheet("color: #888;")
        outer.addWidget(self._dataset_status)

        return box

    def _build_cellpose_group(self) -> QGroupBox:
        box = QGroupBox("Cellpose Settings (applied to every dataset)")
        form = QFormLayout(box)

        self._cp_seg_channel = QComboBox()
        self._cp_seg_channel.setToolTip(
            "Which channel to feed to Cellpose for segmentation. "
            "Populated from the intersection of all selected datasets."
        )
        form.addRow("Segmentation channel:", self._cp_seg_channel)

        # The seven inference controls (Model, Diameter, GPU, Flow, Cellprob,
        # Min size, Saturation) plus the Gaussian-blur Sigma row live in the
        # shared CellposeSettingsForm so this dialog and the Segment panel
        # cannot drift. Seed the diameter at 300 to preserve this dialog's
        # historical default. The surface-specific rows (seg-channel above;
        # seg-name and edge-mode/margin below) stay here.
        self._cp_form = CellposeSettingsForm(
            initial=CellposeSettings(diameter=300.0)
        )
        form.addRow(self._cp_form)

        # Segmentation-layer name (was hardcoded as "cellpose_qc" before
        # this evolution; now configurable so a researcher can keep
        # multiple Cellpose parameterizations on the same .h5).
        self._cp_seg_name = QLineEdit("cp_mask")
        self._cp_seg_name.setToolTip(
            "HDF5 path component for the Cellpose-produced segmentation "
            "(/labels/<name>). Downstream phases (seg-QC, thresholding, "
            "dilute, measure) all read and write under this name. Pick a "
            "different name to keep multiple Cellpose parameterizations "
            "on the same .h5 without overwriting each other."
        )
        form.addRow("Segmentation layer name:", self._cp_seg_name)

        # Edge-mode selector. Replaces the pre-evolution "edge cells
        # always removed" invariant with a per-run choice. Labels and
        # tooltips are researcher-facing, not implementation-facing.
        self._edge_mode = QComboBox()
        self._edge_mode.addItem(
            "Exclude (default)", EdgeMode.EXCLUDE
        )
        self._edge_mode.addItem(
            "Include — count as whole cells",
            EdgeMode.INCLUDE_AS_NORMAL,
        )
        self._edge_mode.addItem(
            "Include — synthesize edge-cohort row",
            EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
        )
        # Default to the size-normalized cohort mode (the workflow's
        # primary use case is phase-separation analysis where the edge
        # cohort matters). The user can switch to Exclude / Include-as-normal.
        self._edge_mode.setCurrentIndex(
            self._edge_mode.findData(EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT)
        )
        self._edge_mode.setToolTip(
            "How the workflow handles cells touching the image border.\n\n"
            "Exclude — Remove edge cells in Phase 1. They are not measured. "
            "Recommended default: edge cells are partial and would bias "
            "sum/area metrics.\n\n"
            "Include — count as whole cells — Keep edge cells in labels. "
            "They participate in clustering, thresholding, and per-cell "
            "measurement as if they were whole cells. They appear in the "
            "parquet flagged with is_edge=True; their metric values are "
            "biased low for sum/area-style metrics.\n\n"
            "Include — synthesize edge-cohort row — Keep edge cells like "
            "above AND emit one extra synthetic row per dataset "
            "(cell_id=-1, is_edge_synthetic=True) whose metric values are "
            "sum(M across edge cells) / N_theoretical, where "
            "N_theoretical = sum(edge_area) / mean(whole_area). The "
            "synthetic row represents the edge ring as a count-normalized "
            "whole-cell equivalent."
        )
        form.addRow("Edge cells:", self._edge_mode)

        # Edge margin (px) — applies to both Phase 1's edge filter
        # (when edge_mode == EXCLUDE) and Phase 7's edge-cohort identification
        # (when edge_mode == INCLUDE_AS_SIZE_NORMALIZED_COHORT). 0 = strict
        # border-touching only.
        self._edge_margin = QSpinBox()
        self._edge_margin.setRange(0, 500)
        self._edge_margin.setValue(100)
        self._edge_margin.setToolTip(
            "Pixel margin from the image border that counts as 'edge'.\n\n"
            "0 (default): strict border-touching cells only.\n"
            "N > 0: cells within N pixels of any border are treated as edge.\n\n"
            "Used by Phase 1 filtering in 'Exclude' mode, and by the "
            "edge-cohort identification at measurement time in "
            "'synthesize edge-cohort row' mode. Has no effect in "
            "'count as whole cells' mode (every cell is treated equally)."
        )
        form.addRow("Edge margin (px):", self._edge_margin)

        return box

    # ── Segmentation selection ────────────────────────────────

    def _build_segmentation_group(self) -> QGroupBox:
        box = QGroupBox("Segmentation Selection")
        outer = QVBoxLayout(box)
        note = QLabel(
            "Datasets that already have a segmentation skip Cellpose and start "
            "at thresholding. Check datasets on the left; the right pane lists "
            "the segmentation layers common to them — pick the one they should "
            "use. Leave a group with nothing picked to use each dataset's "
            "default (tracked layers preferred). Add a group to override the "
            "segmentation for a subset. Datasets with no segmentation will be "
            "segmented by Cellpose."
        )
        note.setWordWrap(True)
        outer.addWidget(note)

        # Opt-in QC for already-segmented datasets. When checked (default),
        # each pre-segmented dataset opens its selected layer in the
        # segmentation-QC editor before thresholding; when unchecked, the
        # existing labels are used as-is. Read pull-style in
        # _try_build_config (like the Cellpose "Use GPU" checkbox). Datasets
        # segmented by Cellpose inside this run always run seg-QC regardless.
        self._run_seg_qc = QCheckBox(
            "Run segmentation QC on already-segmented datasets"
        )
        self._run_seg_qc.setChecked(True)
        self._run_seg_qc.setToolTip(
            "When checked, datasets that arrive already segmented (e.g. from "
            "percell4-batch) open their selected segmentation layer in the QC "
            "editor so you can review and correct it before thresholding. "
            "Uncheck to trust the existing segmentation and go straight to "
            "group thresholding. Datasets segmented by Cellpose during this "
            "run always run seg-QC. Skipped for time-lapse datasets."
        )
        outer.addWidget(self._run_seg_qc)

        # Datasets with no /labels can't have their segmentation overridden and
        # are omitted; a note reports how many will be Cellpose-segmented.
        self._seg_excluded_note = QLabel("")
        self._seg_excluded_note.setStyleSheet("color: #888;")
        self._seg_excluded_note.setWordWrap(True)
        outer.addWidget(self._seg_excluded_note)

        # Host for the stacked group panels + an Add-group button below them.
        self._seg_groups_host = QWidget()
        self._seg_groups_layout = QVBoxLayout(self._seg_groups_host)
        self._seg_groups_layout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._seg_groups_host)

        add_row = QHBoxLayout()
        self._add_seg_group_btn = QPushButton("Add group")
        self._add_seg_group_btn.setToolTip(
            "Add another datasets/segmentation group to override the "
            "segmentation for a subset of datasets (later groups win)."
        )
        self._add_seg_group_btn.clicked.connect(self._on_add_seg_group)
        add_row.addWidget(self._add_seg_group_btn)
        add_row.addStretch()
        outer.addLayout(add_row)

        # Cache of display_name -> available segmentations, refreshed with the
        # queue so a checkbox toggle doesn't re-open every .h5.
        self._segs_by_name: dict[str, list[str]] = {}
        self._seg_group_panels: list[_SegGroupPanel] = []
        return box

    def _dataset_segmentations(self, pd: _PendingDataset) -> list[str]:
        """List a dataset's segmentation (/labels) resources, or [] if none.

        Only existing ``.h5`` datasets can have segmentations; tiff-pending
        datasets have no labels until Cellpose runs.
        """
        if pd.source is not DatasetSource.H5_EXISTING:
            return []
        try:
            return DatasetStore(pd.h5_path).list_labels()
        except Exception:  # noqa: BLE001 — best-effort; missing/corrupt -> none
            logger.exception("could not list labels for %s", pd.display_name)
            return []

    def _refresh_segmentation_picker(self) -> None:
        """Rebuild the segmentation group panels' contents from the queue.

        Datasets with no ``/labels`` are omitted (Cellpose will segment them).
        Each panel's dataset checklist is repopulated preserving prior checks;
        its segmentation list is then recomputed from the intersection of the
        checked datasets' layers, preserving the prior single pick. At least one
        group always exists.
        """
        host = getattr(self, "_seg_groups_host", None)
        if host is None:
            return
        self._segs_by_name = {
            pd.display_name: self._dataset_segmentations(pd)
            for pd in self._pending_datasets
        }
        seg_ds_names = [name for name, segs in self._segs_by_name.items() if segs]
        n_excluded = len(self._pending_datasets) - len(seg_ds_names)
        self._seg_excluded_note.setText(
            f"{n_excluded} dataset(s) have no existing segmentation and will be "
            "segmented by Cellpose."
            if n_excluded
            else ""
        )

        if not self._seg_group_panels:
            self._append_seg_group()
        for i, panel in enumerate(self._seg_group_panels):
            self._repopulate_seg_panel_datasets(
                panel, seg_ds_names, check_new=(i == 0)
            )
            self._recompute_panel_segs(panel)

    def _append_seg_group(self) -> _SegGroupPanel:
        """Build a segmentation group panel, add it to the layout, register it."""
        removable = bool(self._seg_group_panels)
        panel = self._build_seg_group_panel(removable=removable)
        self._seg_group_panels.append(panel)
        self._seg_groups_layout.addWidget(panel.container)
        return panel

    def _on_add_seg_group(self) -> None:
        """Add-group button handler: append an empty group and populate it."""
        panel = self._append_seg_group()
        seg_ds_names = [name for name, segs in self._segs_by_name.items() if segs]
        self._repopulate_seg_panel_datasets(panel, seg_ds_names, check_new=False)
        self._recompute_panel_segs(panel)

    def _build_seg_group_panel(self, *, removable: bool) -> _SegGroupPanel:
        """Build one two-pane segmentation group panel (Datasets | Segmentation).

        Mirrors ``_build_mask_group_panel`` but the right list is single-pick
        (exclusive checks, no Select All) since a dataset has one segmentation.
        """
        container = QGroupBox()
        v = QVBoxLayout(container)
        v.setContentsMargins(8, 8, 8, 8)

        lists_row = QHBoxLayout()

        # Left: datasets checklist + Select All / Deselect All.
        ds_box = QGroupBox("Datasets")
        ds_layout = QVBoxLayout(ds_box)
        ds_btn_row = QHBoxLayout()
        ds_all = QPushButton("Select All")
        ds_none = QPushButton("Deselect All")
        ds_btn_row.addWidget(ds_all)
        ds_btn_row.addWidget(ds_none)
        ds_btn_row.addStretch()
        ds_layout.addLayout(ds_btn_row)
        ds_list = QListWidget()
        ds_list.setMaximumHeight(_MASK_PANE_MAX_H)
        ds_layout.addWidget(ds_list)
        lists_row.addWidget(ds_box, 3)

        # Right: single-pick segmentation list (intersection of checked datasets).
        seg_box = QGroupBox("Segmentation (pick one; common to checked datasets)")
        seg_layout = QVBoxLayout(seg_box)
        seg_list = QListWidget()
        seg_list.setMaximumHeight(_MASK_PANE_MAX_H)
        seg_layout.addWidget(seg_list)
        lists_row.addWidget(seg_box, 2)

        v.addLayout(lists_row)

        remove_btn: QPushButton | None = None
        if removable:
            rm_row = QHBoxLayout()
            rm_row.addStretch()
            remove_btn = QPushButton("Remove group")
            rm_row.addWidget(remove_btn)
            v.addLayout(rm_row)

        panel = _SegGroupPanel(
            container=container,
            ds_list=ds_list,
            seg_list=seg_list,
            remove_btn=remove_btn,
        )

        # Wire user-edit signals (qt-wire-user-edit-signals). Panels persist
        # across refreshes, so capturing `panel` by value is stable.
        ds_list.itemChanged.connect(
            lambda _item, p=panel: self._recompute_panel_segs(p)
        )
        seg_list.itemChanged.connect(
            lambda item, p=panel: self._on_seg_item_checked(p, item)
        )
        ds_all.clicked.connect(
            lambda _=False, p=panel: self._select_all_seg_datasets(p, True)
        )
        ds_none.clicked.connect(
            lambda _=False, p=panel: self._select_all_seg_datasets(p, False)
        )
        if remove_btn is not None:
            remove_btn.clicked.connect(
                lambda _=False, p=panel: self._remove_seg_group(p)
            )
        return panel

    def _repopulate_seg_panel_datasets(
        self,
        panel: _SegGroupPanel,
        seg_ds_names: list[str],
        *,
        check_new: bool,
    ) -> None:
        """Rebuild a seg panel's dataset checklist (see _repopulate_panel_datasets)."""
        prior_checked = set(_checked_texts(panel.ds_list))
        prior_all = {
            panel.ds_list.item(i).text() for i in range(panel.ds_list.count())
        }
        panel.ds_list.blockSignals(True)
        panel.ds_list.clear()
        for name in seg_ds_names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            checked = name in prior_checked if name in prior_all else check_new
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            panel.ds_list.addItem(item)
        panel.ds_list.blockSignals(False)

    def _recompute_panel_segs(self, panel: _SegGroupPanel) -> None:
        """Recompute a seg panel's single-pick list from its checked datasets.

        Options are the intersection of the checked datasets' segmentation layers
        (reusing ``intersect_masks`` — a generic sorted set-intersection). The
        prior pick is preserved when still available; a group with nothing picked
        lets the runner auto-detect each dataset's preferred (tracked) layer.
        """
        checked_ds = _checked_texts(panel.ds_list)
        common = intersect_masks(self._segs_by_name.get(n, []) for n in checked_ds)
        prior = _single_checked(panel.seg_list)
        panel.seg_list.blockSignals(True)
        panel.seg_list.clear()
        for name in common:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name == prior else Qt.Unchecked)
            panel.seg_list.addItem(item)
        panel.seg_list.blockSignals(False)

    def _on_seg_item_checked(
        self, panel: _SegGroupPanel, item: QListWidgetItem
    ) -> None:
        """Enforce single-pick: checking one segmentation unchecks the others."""
        if item.checkState() != Qt.Checked:
            return
        panel.seg_list.blockSignals(True)
        for i in range(panel.seg_list.count()):
            other = panel.seg_list.item(i)
            if other is not item and other.checkState() == Qt.Checked:
                other.setCheckState(Qt.Unchecked)
        panel.seg_list.blockSignals(False)

    def _select_all_seg_datasets(
        self, panel: _SegGroupPanel, checked: bool
    ) -> None:
        """Check/uncheck every dataset in a seg panel, then recompute its list."""
        panel.ds_list.blockSignals(True)
        _set_all_checked(panel.ds_list, checked)
        panel.ds_list.blockSignals(False)
        self._recompute_panel_segs(panel)

    def _remove_seg_group(self, panel: _SegGroupPanel) -> None:
        """Remove a segmentation group panel (first panel is not removable)."""
        if panel not in self._seg_group_panels:
            return
        self._seg_group_panels.remove(panel)
        self._seg_groups_layout.removeWidget(panel.container)
        panel.container.deleteLater()

    @property
    def segmentation_overrides(self) -> dict[str, str]:
        """Per-dataset chosen segmentation — later groups override earlier ones.

        Keyed by the dataset's display name; passed to
        ``SingleCellThresholdingRunner(segmentation_overrides=...)``. Each group
        with a picked segmentation assigns it to its checked datasets, and a
        later group wins. Datasets left unpicked are omitted, so the runner falls
        back to its own auto-detection (the tracked/preferred layer). Datasets
        with no segmentation on disk are never shown here (Cellpose will run).
        """
        overrides: dict[str, str] = {}
        for panel in getattr(self, "_seg_group_panels", []):
            seg = _single_checked(panel.seg_list)
            if seg is None:
                continue
            for ds_name in _checked_texts(panel.ds_list):
                overrides[ds_name] = seg
        return overrides

    # ── Existing-mask reuse ───────────────────────────────────

    def _build_mask_selection_group(self) -> QGroupBox:
        """Checkable group to reuse existing /masks instead of thresholding.

        When checked, the run skips the Threshold Rounds step and measures the
        masks you assign per dataset via a two-pane group builder: check datasets
        on the left, and the right pane lists the masks common to them; check the
        masks to measure. Add a group to give a subset of datasets extra masks —
        a dataset's final selection is the UNION of the masks across every group
        it is checked in. Pre-run config control (dialog-local state only); it
        never touches the live session.
        """
        box = QGroupBox("Use existing masks (skip thresholding rounds)")
        box.setCheckable(True)
        box.setChecked(False)
        box.setToolTip(
            "When checked, the workflow does NOT compute threshold rounds. "
            "Instead it measures the mask layer(s) you assign per dataset via "
            "the two-pane group builder below, running per-cell measurement + "
            "particle analysis + export on them. Uncheck to configure Threshold "
            "Rounds as usual."
        )
        self._mask_selection_group = box
        outer = QVBoxLayout(box)
        note = QLabel(
            "Check datasets on the left; the right pane lists the masks common "
            "to them. Check the masks to measure. Add a group to give a subset "
            "of datasets extra masks — each dataset measures the union of masks "
            "across the groups it is in. The Threshold Rounds section is hidden "
            "while this is on."
        )
        note.setWordWrap(True)
        outer.addWidget(note)

        # Datasets with no /masks can't be assigned any and are omitted; a note
        # reports how many were hidden.
        self._mask_excluded_note = QLabel("")
        self._mask_excluded_note.setStyleSheet("color: #888;")
        self._mask_excluded_note.setWordWrap(True)
        outer.addWidget(self._mask_excluded_note)

        # Host for the stacked group panels + an Add-group button below them.
        self._mask_groups_host = QWidget()
        self._mask_groups_layout = QVBoxLayout(self._mask_groups_host)
        self._mask_groups_layout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._mask_groups_host)

        add_row = QHBoxLayout()
        self._add_mask_group_btn = QPushButton("Add group")
        self._add_mask_group_btn.setToolTip(
            "Add another datasets/masks group so a subset of datasets can "
            "measure extra masks (e.g. masks unique to a few datasets)."
        )
        self._add_mask_group_btn.clicked.connect(self._on_add_mask_group)
        add_row.addWidget(self._add_mask_group_btn)
        add_row.addStretch()
        outer.addLayout(add_row)

        # Cache of display_name -> available masks, refreshed with the queue so a
        # checkbox toggle doesn't re-open every .h5.
        self._masks_by_name: dict[str, list[str]] = {}
        self._mask_group_panels: list[_MaskGroupPanel] = []
        box.toggled.connect(self._on_mask_reuse_toggled)
        return box

    def _on_mask_reuse_toggled(self, checked: bool) -> None:
        """Hide the Threshold Rounds group when reusing existing masks.

        Mask assignments in the group panels are preserved across toggles (the
        panels are not rebuilt here), so toggling is non-destructive.
        """
        box = getattr(self, "_rounds_group_box", None)
        if box is not None:
            box.setVisible(not checked)
        self._update_start_enabled()

    def _dataset_masks(self, pd: _PendingDataset) -> list[str]:
        """List a dataset's mask (/masks) resources, or [] if none.

        Only existing ``.h5`` datasets can have masks; tiff-pending
        datasets have none until a threshold round runs.
        """
        if pd.source is not DatasetSource.H5_EXISTING:
            return []
        try:
            return DatasetStore(pd.h5_path).list_masks()
        except Exception:  # noqa: BLE001 — best-effort; missing/corrupt -> none
            logger.exception("could not list masks for %s", pd.display_name)
            return []

    def _refresh_mask_picker(self) -> None:
        """Rebuild the mask-group panels' contents from the current queue.

        Datasets that expose no ``/masks`` are omitted (they can't be assigned a
        mask). Each panel's dataset checklist is repopulated preserving prior
        checks by name; its mask checklist is then recomputed from the
        intersection of the checked datasets' masks, preserving prior mask checks.
        At least one group always exists.
        """
        host = getattr(self, "_mask_groups_host", None)
        if host is None:
            return
        # Cache available masks once per refresh so a checkbox toggle doesn't
        # re-open every .h5.
        self._masks_by_name = {
            pd.display_name: self._dataset_masks(pd) for pd in self._pending_datasets
        }
        mask_ds_names = [name for name, masks in self._masks_by_name.items() if masks]
        n_excluded = len(self._pending_datasets) - len(mask_ds_names)
        self._mask_excluded_note.setText(
            f"{n_excluded} dataset(s) have no existing masks and are not shown."
            if n_excluded
            else ""
        )

        if not self._mask_group_panels:
            self._append_mask_group()
        for i, panel in enumerate(self._mask_group_panels):
            # The first panel auto-includes newly-added datasets (group 1 stays
            # the whole batch, and the common masks appear immediately); added
            # subset-groups start new datasets unchecked.
            self._repopulate_panel_datasets(panel, mask_ds_names, check_new=(i == 0))
            self._recompute_panel_masks(panel)
        self._update_start_enabled()

    def _append_mask_group(self) -> _MaskGroupPanel:
        """Build a group panel, add it to the layout, and register it."""
        removable = bool(self._mask_group_panels)  # first panel is not removable
        panel = self._build_mask_group_panel(removable=removable)
        self._mask_group_panels.append(panel)
        self._mask_groups_layout.addWidget(panel.container)
        return panel

    def _on_add_mask_group(self) -> None:
        """Add-group button handler: append an empty group and populate it."""
        panel = self._append_mask_group()
        mask_ds_names = [name for name, masks in self._masks_by_name.items() if masks]
        self._repopulate_panel_datasets(panel, mask_ds_names, check_new=False)
        self._recompute_panel_masks(panel)
        self._update_start_enabled()

    def _build_mask_group_panel(self, *, removable: bool) -> _MaskGroupPanel:
        """Build one two-pane group panel (Datasets | Masks) widget tree."""
        container = QGroupBox()
        v = QVBoxLayout(container)
        v.setContentsMargins(8, 8, 8, 8)

        lists_row = QHBoxLayout()

        # Left: datasets checklist + Select All / Deselect All.
        ds_box = QGroupBox("Datasets")
        ds_layout = QVBoxLayout(ds_box)
        ds_btn_row = QHBoxLayout()
        ds_all = QPushButton("Select All")
        ds_none = QPushButton("Deselect All")
        ds_btn_row.addWidget(ds_all)
        ds_btn_row.addWidget(ds_none)
        ds_btn_row.addStretch()
        ds_layout.addLayout(ds_btn_row)
        ds_list = QListWidget()
        ds_list.setMaximumHeight(_MASK_PANE_MAX_H)
        ds_layout.addWidget(ds_list)
        lists_row.addWidget(ds_box, 3)

        # Right: masks checklist (intersection of the checked datasets).
        mask_box = QGroupBox("Masks (common to checked datasets)")
        mask_layout = QVBoxLayout(mask_box)
        mask_btn_row = QHBoxLayout()
        mask_all = QPushButton("Select All")
        mask_none = QPushButton("Deselect All")
        mask_btn_row.addWidget(mask_all)
        mask_btn_row.addWidget(mask_none)
        mask_btn_row.addStretch()
        mask_layout.addLayout(mask_btn_row)
        mask_list = QListWidget()
        mask_list.setMaximumHeight(_MASK_PANE_MAX_H)
        mask_layout.addWidget(mask_list)
        lists_row.addWidget(mask_box, 2)

        v.addLayout(lists_row)

        remove_btn: QPushButton | None = None
        if removable:
            rm_row = QHBoxLayout()
            rm_row.addStretch()
            remove_btn = QPushButton("Remove group")
            rm_row.addWidget(remove_btn)
            v.addLayout(rm_row)

        panel = _MaskGroupPanel(
            container=container,
            ds_list=ds_list,
            mask_list=mask_list,
            remove_btn=remove_btn,
        )

        # Wire user-edit signals (qt-wire-user-edit-signals). Panels persist
        # across queue refreshes, so capturing `panel` by value is stable.
        ds_list.itemChanged.connect(
            lambda _item, p=panel: self._recompute_panel_masks(p)
        )
        mask_list.itemChanged.connect(self._update_start_enabled)
        ds_all.clicked.connect(
            lambda _=False, p=panel: self._select_all_datasets(p, True)
        )
        ds_none.clicked.connect(
            lambda _=False, p=panel: self._select_all_datasets(p, False)
        )
        mask_all.clicked.connect(
            lambda _=False, p=panel: self._select_all_masks(p, True)
        )
        mask_none.clicked.connect(
            lambda _=False, p=panel: self._select_all_masks(p, False)
        )
        if remove_btn is not None:
            remove_btn.clicked.connect(
                lambda _=False, p=panel: self._remove_mask_group(p)
            )
        return panel

    def _repopulate_panel_datasets(
        self,
        panel: _MaskGroupPanel,
        mask_ds_names: list[str],
        *,
        check_new: bool,
    ) -> None:
        """Rebuild a panel's dataset checklist.

        Datasets already in the list keep their check state; a dataset newly
        added to the queue is checked only when ``check_new`` — the first panel
        auto-includes new datasets so "group 1" stays the whole batch, while
        added subset-groups leave new datasets unchecked.
        """
        prior_checked = set(_checked_texts(panel.ds_list))
        prior_all = {
            panel.ds_list.item(i).text() for i in range(panel.ds_list.count())
        }
        panel.ds_list.blockSignals(True)
        panel.ds_list.clear()
        for name in mask_ds_names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            checked = name in prior_checked if name in prior_all else check_new
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            panel.ds_list.addItem(item)
        panel.ds_list.blockSignals(False)

    def _recompute_panel_masks(self, panel: _MaskGroupPanel) -> None:
        """Recompute a panel's mask checklist from its checked datasets.

        The masks are the intersection of the checked datasets' available masks;
        prior mask checks are preserved by name (a mask that is no longer common
        to the checked datasets falls out).
        """
        checked_ds = _checked_texts(panel.ds_list)
        common = intersect_masks(self._masks_by_name.get(n, []) for n in checked_ds)
        prior = set(_checked_texts(panel.mask_list))
        panel.mask_list.blockSignals(True)
        panel.mask_list.clear()
        for mask in common:
            item = QListWidgetItem(mask)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if mask in prior else Qt.Unchecked)
            panel.mask_list.addItem(item)
        panel.mask_list.blockSignals(False)
        self._update_start_enabled()

    def _select_all_datasets(self, panel: _MaskGroupPanel, checked: bool) -> None:
        """Check/uncheck every dataset in a panel, then recompute its masks once."""
        panel.ds_list.blockSignals(True)
        _set_all_checked(panel.ds_list, checked)
        panel.ds_list.blockSignals(False)
        self._recompute_panel_masks(panel)

    def _select_all_masks(self, panel: _MaskGroupPanel, checked: bool) -> None:
        """Check/uncheck every mask in a panel, then re-gate Start once."""
        panel.mask_list.blockSignals(True)
        _set_all_checked(panel.mask_list, checked)
        panel.mask_list.blockSignals(False)
        self._update_start_enabled()

    def _remove_mask_group(self, panel: _MaskGroupPanel) -> None:
        """Remove a group panel (the first, non-removable panel has no button)."""
        if panel not in self._mask_group_panels:
            return
        self._mask_group_panels.remove(panel)
        self._mask_groups_layout.removeWidget(panel.container)
        panel.container.deleteLater()
        self._update_start_enabled()

    @property
    def existing_mask_selections(self) -> dict[str, list[str]]:
        """Per-dataset selected mask names — the UNION across all groups.

        Each group contributes its checked masks to every dataset it has checked;
        a dataset's value is the union across the groups it is in. Datasets or
        groups with no checked masks are omitted, so a value is never an empty
        list. Keys are dataset display names. Independent of the outer group's
        checked state (the caller gates on ``use_existing_masks``), so selections
        survive a non-destructive toggle.
        """
        acc: dict[str, set[str]] = {}
        for panel in getattr(self, "_mask_group_panels", []):
            masks = _checked_texts(panel.mask_list)
            if not masks:
                continue
            for ds_name in _checked_texts(panel.ds_list):
                acc.setdefault(ds_name, set()).update(masks)
        return {name: sorted(masks) for name, masks in acc.items() if masks}

    def _build_rounds_group(self) -> QGroupBox:
        box = QGroupBox("Thresholding Rounds (ordered)")
        outer = QVBoxLayout(box)

        # Ordered source of truth: one RoundCard per round, in display order.
        self._round_cards: list[RoundCard] = []

        # Cards live in a scroll area so many rounds never force the dialog wider
        # or taller than the screen — the group box scrolls internally instead.
        self._rounds_scroll = QScrollArea()
        self._rounds_scroll.setWidgetResizable(True)
        # Tall enough to show ~2 full rounds before scrolling.
        self._rounds_scroll.setMinimumHeight(380)
        self._rounds_container = QWidget()
        self._rounds_layout = QVBoxLayout(self._rounds_container)
        self._rounds_layout.setContentsMargins(0, 0, 0, 0)
        self._rounds_layout.setSpacing(8)
        self._rounds_layout.setAlignment(Qt.AlignTop)
        # Empty-state placeholder shown when no rounds exist.
        self._rounds_empty_label = QLabel(
            "No rounds yet \u2014 click Add Round to begin."
        )
        self._rounds_empty_label.setStyleSheet("color: gray; font-style: italic;")
        self._rounds_layout.addWidget(self._rounds_empty_label)
        self._rounds_scroll.setWidget(self._rounds_container)
        outer.addWidget(self._rounds_scroll, stretch=1)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add Round")
        btn_add.clicked.connect(self._on_add_round)
        btn_row.addWidget(btn_add)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        return box

    def _build_particles_group(self) -> QGroupBox:
        """Optional particle analysis (U7).

        When the group is checked, the measure phase additionally:
        - Merges per-cell particle summary columns (counts, total/mean/max
          area, coverage_fraction, per-channel intensity aggregates) into
          measurements.parquet, prefixed by the round name.
        - Writes a per-particle detail file (particles.parquet +
          particles.csv) to the run folder, with one row per detected
          particle (cell_id, round_name, particle_id, area, centroid,
          per-channel intensities).
        """
        box = QGroupBox("Include particle analysis")
        # Hold an explicit reference so the build path doesn't have to
        # walk the widget tree from the spinbox to find this group.
        self._particle_group = box
        box.setCheckable(True)
        box.setChecked(True)
        box.setToolTip(
            "Optional: count and measure connected-component particles "
            "within each cell, using each grouped-threshold round's mask "
            "as the particle-vs-background classifier. Adds per-cell "
            "summary columns to measurements.parquet (prefixed with the "
            "round name) and writes per-particle detail rows to "
            "particles.parquet/csv in the run folder."
        )
        form = QFormLayout(box)

        # Min particle area: paired value + unit. QDoubleSpinBox covers
        # both modes — integer step for px, fractional for µm². Switching
        # units does not auto-convert the entered number (the µm² mode is
        # resolved per-dataset inside the workflow phase using each
        # dataset's pixel_size_um, so a px↔µm² convert here would imply a
        # single canonical pixel size that doesn't exist at config time).
        min_area_row = QWidget()
        min_area_layout = QHBoxLayout(min_area_row)
        min_area_layout.setContentsMargins(0, 0, 0, 0)

        self._particle_min_area = QDoubleSpinBox()
        self._particle_min_area.setRange(0.0, 1_000_000.0)
        # Always keep 4 decimal places of precision in the underlying
        # value so toggling px → µm² → px doesn't quantize a fractional
        # µm² entry to zero. Only the step changes per unit (below).
        self._particle_min_area.setDecimals(4)
        self._particle_min_area.setSingleStep(1.0)
        self._particle_min_area.setValue(0.0)
        self._particle_min_area.setToolTip(
            "Minimum particle area. Connected components smaller than "
            "this are dropped. 0 = keep every component (including "
            "single-pixel hits). The unit follows the selector to the "
            "right — px² is a pixel-count area (e.g. 9 = 9 total pixels, "
            "not 9×9); µm² mode is converted to pixels per dataset using "
            "that dataset's pixel size."
        )

        self._particle_min_area_unit = QComboBox()
        self._particle_min_area_unit.addItem("px²", userData="px")
        self._particle_min_area_unit.addItem("µm²", userData="um2")
        self._particle_min_area_unit.setCurrentIndex(0)
        self._particle_min_area_unit.setToolTip(
            "Unit for Min particle area. px² applies a uniform pixel-count "
            "area threshold to every dataset. µm² resolves to a per-dataset "
            "pixel threshold using each dataset's TIFF pixel size — "
            "datasets without a known pixel size will fail their "
            "particle phase explicitly rather than silently default."
        )
        # Wire the signal at construction so tests using
        # `combo.setCurrentIndex(i)` (which fires currentIndexChanged)
        # exercise the runtime decimals/step swap. Matches the
        # qt-wire-user-edit-signals convention.
        self._particle_min_area_unit.currentIndexChanged.connect(
            self._on_min_area_unit_changed,
        )

        min_area_layout.addWidget(self._particle_min_area, stretch=1)
        min_area_layout.addWidget(self._particle_min_area_unit)
        form.addRow("Min particle area:", min_area_row)

        note = QLabel(
            "Particle analysis runs against every thresholding round's "
            "mask. To analyze only a subset of rounds, configure fewer "
            "rounds — there is no per-round toggle in this version."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-style: italic;")
        form.addRow("", note)

        return box

    def _on_min_area_unit_changed(self) -> None:
        """Re-tune the spinbox step when the unit combo flips.

        Switching units does NOT auto-convert the entered numeric value
        — the user re-states intent. px gets integer step; µm² gets
        fractional precision. ``decimals`` is fixed at construction so
        a fractional value entered in µm² mode is not silently
        quantized to zero on a transient px detour.
        """
        unit = self._particle_min_area_unit.currentData()
        if unit == "um2":
            self._particle_min_area.setSingleStep(0.01)
        else:
            self._particle_min_area.setSingleStep(1.0)

    def _build_dilute_group(self) -> QGroupBox:
        """Optional Phase 5 (dilute-phase mask) configuration.

        Wraps `mask_name`, `dilation_radius_px`, `channel`, and a
        ThresholdingRound-shaped settings block in a checkable group box.
        Settings here are locked at workflow Start — the runner picks
        them up once and never re-reads. Per origin R11.
        """
        box = QGroupBox("Generate dilute-phase mask")
        box.setCheckable(True)
        box.setChecked(True)
        box.setToolTip(
            "Optional: insert a per-dataset interactive dilute-phase mask "
            "generation phase between thresholding rounds and measurement. "
            "Reuses the existing single-dataset dilute UI as the inner loop; "
            "each dataset runs as many rounds as the researcher decides."
        )
        form = QFormLayout(box)

        # Default mask name so the checked-by-default group doesn't block
        # Start with an empty-required-field warning. The user can rename
        # or uncheck the group.
        self._dilute_mask_name = QLineEdit("dilute")
        self._dilute_mask_name.setPlaceholderText("e.g. dilute")
        self._dilute_mask_name.setToolTip(
            "Name of the final dilute mask. Written to /masks/<name> in "
            "each dataset's h5. Must be unique against every thresholding "
            "round name in this run."
        )
        form.addRow("Mask name:", self._dilute_mask_name)

        self._dilute_channel = QComboBox()
        self._dilute_channel.setToolTip(
            "Which channel from /intensity to feed to the per-round metric "
            "computation. Populated from the channel intersection across "
            "selected datasets."
        )
        form.addRow("Channel:", self._dilute_channel)

        self._dilute_dilation_px = QSpinBox()
        self._dilute_dilation_px.setRange(1, 200)
        self._dilute_dilation_px.setValue(5)
        self._dilute_dilation_px.setToolTip(
            "Pixel radius used to dilate each round's accepted condensed "
            "mask before subtracting it from the working buffer."
        )
        form.addRow("Dilation radius (px):", self._dilute_dilation_px)

        self._dilute_metric = QComboBox()
        self._dilute_metric.addItems(sorted(BUILTIN_METRICS.keys()))
        self._dilute_metric.setCurrentText("median_intensity")
        form.addRow("Metric:", self._dilute_metric)

        self._dilute_algorithm = QComboBox()
        self._dilute_algorithm.addItem("GMM", ThresholdAlgorithm.GMM)
        self._dilute_algorithm.addItem("K-means", ThresholdAlgorithm.KMEANS)
        form.addRow("Algorithm:", self._dilute_algorithm)

        self._dilute_gmm_criterion = QComboBox()
        self._dilute_gmm_criterion.addItem("BIC", GmmCriterion.BIC)
        self._dilute_gmm_criterion.addItem("Silhouette", GmmCriterion.SILHOUETTE)
        form.addRow("GMM criterion:", self._dilute_gmm_criterion)

        self._dilute_gmm_max = QSpinBox()
        self._dilute_gmm_max.setRange(2, 20)
        self._dilute_gmm_max.setValue(10)
        form.addRow("GMM max components:", self._dilute_gmm_max)

        self._dilute_kmeans_n = QSpinBox()
        self._dilute_kmeans_n.setRange(2, 20)
        self._dilute_kmeans_n.setValue(3)
        form.addRow("K-means n_clusters:", self._dilute_kmeans_n)

        self._dilute_sigma = QDoubleSpinBox()
        self._dilute_sigma.setRange(0.0, 50.0)
        self._dilute_sigma.setSingleStep(0.1)
        self._dilute_sigma.setValue(0.0)
        form.addRow("Gaussian σ:", self._dilute_sigma)

        # Settings-lock note (Tier 2 doc-review finding).
        lock_note = QLabel(
            "These settings are locked at workflow Start and apply to "
            "every dataset in this run. Each dataset runs as many rounds "
            "as you choose interactively."
        )
        lock_note.setWordWrap(True)
        lock_note.setStyleSheet("color: #888; font-style: italic;")
        form.addRow("", lock_note)

        return box

    def _build_columns_group(self) -> QGroupBox:
        box = QGroupBox("CSV Export")
        outer = QVBoxLayout(box)

        note = QLabel(
            "The full measurements.parquet always contains every column. "
            "Configure which channels and metrics appear in the exported "
            "combined.csv and per-dataset CSVs."
        )
        note.setStyleSheet("color: #888;")
        note.setWordWrap(True)
        outer.addWidget(note)

        btn_row = QHBoxLayout()
        btn_configure = QPushButton("Configure CSV Export...")
        btn_configure.clicked.connect(self._on_configure_csv_export)
        btn_row.addWidget(btn_configure)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        self._csv_summary_label = QLabel("No channels or metrics selected yet.")
        self._csv_summary_label.setWordWrap(True)
        self._csv_summary_label.setStyleSheet("color: #aaa;")
        outer.addWidget(self._csv_summary_label)

        return box

    def _build_output_group(self) -> QGroupBox:
        box = QGroupBox("Output Folder")
        row = QHBoxLayout(box)

        self._output_edit = QLineEdit()
        qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        default_out = qs.value(_QSETTINGS_OUTPUT_KEY, "", type=str)
        if default_out:
            self._output_edit.setText(default_out)
        self._output_edit.setPlaceholderText(
            "Parent folder for run_<timestamp>/..."
        )
        row.addWidget(self._output_edit, stretch=1)

        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._on_browse_output)
        row.addWidget(btn_browse)

        return box

    # ── Dataset picker handlers ───────────────────────────────

    def _on_add_h5_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add .h5 datasets",
            "",
            "HDF5 files (*.h5 *.hdf5)",
        )
        if not paths:
            return
        added, skipped = self._add_h5_paths([Path(p) for p in paths])
        self._toast_add_result(added, skipped)

    def _on_add_h5_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Add folder of .h5 datasets", ""
        )
        if not folder:
            return
        folder_path = Path(folder)
        # Non-recursive by default — keeps the behaviour predictable.
        h5_files = sorted(folder_path.glob("*.h5")) + sorted(
            folder_path.glob("*.hdf5")
        )
        if not h5_files:
            self._dataset_status.setText(
                f"No .h5 files found in {folder_path}"
            )
            return
        added, skipped = self._add_h5_paths(h5_files)
        self._toast_add_result(added, skipped)

    def _on_add_tiff_files(self) -> None:
        """Open the compress dataset dialog for tiff discovery + config.

        The CompressDialog itself handles both single-source and
        folder-of-datasets cases — this single entry point covers both.
        """
        self._add_tiff_via_compress_dialog()

    def _on_remove_dataset(self) -> None:
        selected = self._dataset_tree.selectedItems()
        if not selected:
            return
        indexes_to_remove = sorted(
            {self._dataset_tree.indexOfTopLevelItem(i) for i in selected},
            reverse=True,
        )
        for idx in indexes_to_remove:
            if 0 <= idx < len(self._pending_datasets):
                self._pending_datasets.pop(idx)
        self._refresh_dataset_tree()
        self._refresh_round_channels()
        self._refresh_column_picker()
        self._update_start_enabled()

    # ── Dataset picker internals ──────────────────────────────

    def _add_h5_paths(
        self, paths: Iterable[Path]
    ) -> tuple[int, list[str]]:
        """Add each .h5 path; return (n_added, list_of_skipped_labels).

        Skipped reasons: already in the list, not a file, channel read
        failure.
        """
        added = 0
        skipped: list[str] = []
        for path in paths:
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if not resolved.is_file():
                skipped.append(f"{path.name} (not a file)")
                continue
            try:
                channel_names = self._read_h5_channels(resolved)
            except Exception as e:
                logger.exception("failed to read channel names from %s", resolved)
                skipped.append(f"{path.name} ({e.__class__.__name__})")
                continue

            pd = _PendingDataset(
                display_name=resolved.stem,
                source=DatasetSource.H5_EXISTING,
                h5_path=resolved,
                channel_names=channel_names,
            )
            if self._add_pending(pd):
                added += 1
            else:
                skipped.append(f"{path.name} (duplicate)")

        self._refresh_dataset_tree()
        self._refresh_round_channels()
        self._refresh_column_picker()
        self._update_start_enabled()
        return added, skipped

    def _read_h5_channels(self, path: Path) -> list[str]:
        """Read ``/metadata.channel_names`` from an existing h5 file."""
        store = DatasetStore(path)
        meta = store.metadata
        raw = meta.get("channel_names", [])
        if raw is None:
            return []
        # h5py returns numpy arrays of bytes sometimes; coerce to str.
        result: list[str] = []
        for name in raw:
            if isinstance(name, bytes):
                result.append(name.decode("utf-8", errors="replace"))
            else:
                result.append(str(name))
        return result

    def _add_tiff_via_compress_dialog(self) -> None:
        """Open the existing CompressDialog nested inside this dialog.

        On Accept, capture ``dialog.compress_config`` IMMEDIATELY (before
        ``deleteLater``), per the dialog-value-capture rule in
        ``docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md``.
        Every dataset the user checked becomes a ``tiff_pending`` entry.
        """
        from percell4.gui.compress_dialog import CompressDialog

        dialog = CompressDialog(parent=self)
        if dialog.exec_() != QDialog.Accepted:
            dialog.deleteLater()
            return

        # Capture immediately before deleteLater.
        try:
            cfg = dialog.compress_config
        finally:
            dialog.deleteLater()

        selected_token_ids = sorted(cfg.selected_channels)
        layer_assignments = cfg.layer_assignments or {}
        channel_names = _derive_tiff_pending_channel_names(
            selected_token_ids, layer_assignments,
        )
        if not channel_names:
            self._dataset_status.setText(
                "No channels selected in the compress dialog — nothing to add."
            )
            return

        # Serialize layer_assignments so Phase 0 (compress_one) can pass
        # them through to import_dataset. Each entry is JSON-safe.
        layer_assignments_payload = {
            ch_id: {
                "layer_type": assignment.layer_type.value
                if hasattr(assignment.layer_type, "value")
                else str(assignment.layer_type),
                "name": assignment.name,
            }
            for ch_id, assignment in layer_assignments.items()
        }

        added = 0
        skipped: list[str] = []
        for ds in cfg.datasets:
            state = cfg.gui_states.get(ds.name)
            if state is None or not state.checked:
                continue
            display_name = cfg.dataset_name_overrides.get(ds.name, ds.name)
            pd = _PendingDataset(
                display_name=display_name,
                source=DatasetSource.TIFF_PENDING,
                h5_path=Path(ds.output_path),
                channel_names=list(channel_names),
                compress_plan=_build_compress_plan(
                    ds=ds,
                    gui_state=state,
                    cfg=cfg,
                    selected_token_ids=selected_token_ids,
                    layer_assignments_payload=layer_assignments_payload,
                ),
            )
            if self._add_pending(pd):
                added += 1
            else:
                skipped.append(f"{display_name} (duplicate)")

        self._refresh_dataset_tree()
        self._refresh_round_channels()
        self._refresh_column_picker()
        self._update_start_enabled()
        self._toast_add_result(added, skipped)

    def _add_pending(self, pd: _PendingDataset) -> bool:
        """Add one pending dataset if not a duplicate. Returns True on success.

        Also disambiguates the display name against existing entries by
        appending ``(2)``, ``(3)``, etc. as needed.
        """
        new_key = pd.dedupe_key()
        for existing in self._pending_datasets:
            if existing.dedupe_key() == new_key:
                return False

        taken = {existing.display_name for existing in self._pending_datasets}
        if pd.display_name in taken:
            base = pd.display_name
            n = 2
            while f"{base} ({n})" in taken:
                n += 1
            pd.display_name = f"{base} ({n})"

        self._pending_datasets.append(pd)
        return True

    def _refresh_dataset_tree(self) -> None:
        self._dataset_tree.clear()
        for pd in self._pending_datasets:
            item = QTreeWidgetItem(
                [
                    pd.display_name,
                    pd.source.value,
                    str(pd.h5_path),
                    ", ".join(pd.channel_names) if pd.channel_names else "(none)",
                ]
            )
            self._dataset_tree.addTopLevelItem(item)
        # Keep the segmentation + mask pickers in sync with the dataset queue.
        self._refresh_segmentation_picker()
        self._refresh_mask_picker()

    def _toast_add_result(self, added: int, skipped: list[str]) -> None:
        parts: list[str] = []
        if added:
            parts.append(f"Added {added}")
        if skipped:
            parts.append(f"Skipped {len(skipped)}: {', '.join(skipped[:3])}")
            if len(skipped) > 3:
                parts[-1] += f", +{len(skipped) - 3} more"
        self._dataset_status.setText(" · ".join(parts) if parts else "")

    # ── Rounds table ──────────────────────────────────────────

    # ── Rounds (card list) ────────────────────────────────────

    def _on_add_round(self) -> None:
        card = self._make_round_card(len(self._round_cards))
        self._round_cards.append(card)
        self._rounds_layout.addWidget(card)
        self._refresh_round_state()
        # Give the new card visible feedback: scroll it into view.
        self._rounds_scroll.ensureWidgetVisible(card)

    def _make_round_card(self, index: int) -> RoundCard:
        card = RoundCard(index, self._current_intersection())
        card.name_changed.connect(self._refresh_column_picker)
        card.channel_changed.connect(self._refresh_column_picker)
        card.method_changed.connect(self._refresh_column_picker)
        card.move_up_requested.connect(self._on_card_move_up)
        card.move_down_requested.connect(self._on_card_move_down)
        card.remove_requested.connect(self._on_card_remove)
        return card

    def _on_card_remove(self, card: RoundCard) -> None:
        if card not in self._round_cards:
            return
        self._round_cards.remove(card)
        self._rounds_layout.removeWidget(card)
        card.deleteLater()
        self._refresh_round_state()

    def _on_card_move_up(self, card: RoundCard) -> None:
        i = self._round_cards.index(card)
        if i > 0:
            self._reorder_cards(i, i - 1)

    def _on_card_move_down(self, card: RoundCard) -> None:
        i = self._round_cards.index(card)
        if i < len(self._round_cards) - 1:
            self._reorder_cards(i, i + 1)

    def _reorder_cards(self, a: int, b: int) -> None:
        """Swap two cards in the list and re-lay-out. Widget state rides along
        with the card object, so every field is preserved by construction."""
        self._round_cards[a], self._round_cards[b] = (
            self._round_cards[b],
            self._round_cards[a],
        )
        # Re-insert every card in the new order (Qt keeps only one parent slot).
        for card in self._round_cards:
            self._rounds_layout.removeWidget(card)
        for card in self._round_cards:
            self._rounds_layout.addWidget(card)
        self._refresh_round_state()

    def _refresh_round_state(self) -> None:
        """Renumber headers, gate the boundary move buttons, toggle the empty-state
        placeholder, and refresh the downstream column picker + Start gate."""
        n = len(self._round_cards)
        self._rounds_empty_label.setVisible(n == 0)
        for i, card in enumerate(self._round_cards):
            card.set_index(i)
            card.set_move_enabled(up=i > 0, down=i < n - 1)
        self._refresh_column_picker()
        self._update_start_enabled()

    def _round_dicts(self) -> list[dict[str, Any]]:
        return [card.to_dict() for card in self._round_cards]

    def _refresh_round_channels(self) -> None:
        """Repopulate every card's Channel combo from the current intersection,
        preserving each card's pick. Called when the dataset set changes."""
        channels = self._current_intersection()
        for card in self._round_cards:
            card.set_channels(channels)

    # ── Channel intersection + column picker ──────────────────

    def _current_intersection(self) -> list[str]:
        """Compute the intersection across the currently added datasets."""
        sources: list[ChannelSource] = [
            (pd.display_name, list(pd.channel_names))
            for pd in self._pending_datasets
            if pd.channel_names
        ]
        intersected, _outliers = intersect_channels(sources)
        return intersected

    def _refresh_column_picker_async(self, _text: str = "") -> None:
        """Trampoline for combo signals (drops the emitted text argument)."""
        self._refresh_column_picker()

    def _refresh_column_picker(self) -> None:
        """Update the seg channel combo and the CSV summary label.

        Called whenever the dataset list or rounds change. The old giant
        flat column list is replaced by a compact "Configure CSV Export..."
        dialog that the user opens on demand.
        """
        intersected = self._current_intersection()

        # Update the segmentation channel combo.
        prev_seg = self._cp_seg_channel.currentText()
        self._cp_seg_channel.blockSignals(True)
        self._cp_seg_channel.clear()
        if intersected:
            self._cp_seg_channel.addItems(intersected)
            self._cp_seg_channel.setEnabled(True)
            # Restore previous selection if still valid.
            idx = self._cp_seg_channel.findText(prev_seg)
            if idx >= 0:
                self._cp_seg_channel.setCurrentIndex(idx)
        else:
            self._cp_seg_channel.addItem("(add datasets first)")
            self._cp_seg_channel.setEnabled(False)
        self._cp_seg_channel.blockSignals(False)

        # Mirror the same channel-list population for the dilute combo.
        prev_dilute = self._dilute_channel.currentText()
        self._dilute_channel.blockSignals(True)
        self._dilute_channel.clear()
        if intersected:
            self._dilute_channel.addItems(intersected)
            self._dilute_channel.setEnabled(True)
            idx = self._dilute_channel.findText(prev_dilute)
            if idx >= 0:
                self._dilute_channel.setCurrentIndex(idx)
        else:
            self._dilute_channel.addItem("(add datasets first)")
            self._dilute_channel.setEnabled(False)
        self._dilute_channel.blockSignals(False)

        # Channel selection: until the user makes an explicit choice in
        # the Configure CSV Export dialog, default to ALL intersected
        # channels (so the common "export every channel" case needs no
        # interaction). Once the user has picked explicitly
        # (_csv_channels_auto = False), just prune to valid channels.
        valid_channels = set(intersected)
        if self._csv_channels_auto:
            self._selected_csv_channels = set(valid_channels)
        else:
            self._selected_csv_channels &= valid_channels

        self._update_csv_summary()

    def _update_csv_summary(self) -> None:
        """Update the summary label under the Configure CSV Export button."""
        n_ch = len(self._selected_csv_channels)
        n_met = len(self._selected_csv_metrics)
        n_ppc = len(self._selected_csv_particle_per_cell)
        n_ppch = len(self._selected_csv_particle_per_channel)
        round_names = self._round_names_from_cards()
        if n_ch == 0 and n_met == 0 and n_ppc == 0 and n_ppch == 0:
            self._csv_summary_label.setText(
                "No channels or metrics selected yet. "
                "Click 'Configure CSV Export...' to choose."
            )
        else:
            parts = [f"{n_ch} channel(s)", f"{n_met} metric(s)"]
            if round_names:
                parts.append(f"{len(round_names)} round(s)")
            if n_ppc or n_ppch:
                parts.append(f"{n_ppc + n_ppch} particle metric(s)")
            col_count = self._estimate_csv_column_count()
            self._csv_summary_label.setText(
                f"CSV export: {', '.join(parts)} → ~{col_count} columns. "
                f"Core columns (label, centroid, area) always included."
            )

    def _estimate_csv_column_count(self) -> int:
        """Rough count of the CSV columns that will be produced."""
        n_ch = len(self._selected_csv_channels)
        n_met = len(self._selected_csv_metrics)
        n_ppc = len(self._selected_csv_particle_per_cell)
        n_ppch = len(self._selected_csv_particle_per_channel)
        round_names = self._round_names_from_cards()
        n_rounds = len(round_names)
        # identity (3) + core (7) + ch×met + group_per_round + ch×met×round×2 (in/out)
        # + per-cell particle cols (round × ppc) + per-channel particle cols (round × ch × ppch)
        return (
            len(_ALWAYS_ON_COLUMNS)
            + len(_CORE_OPTIONAL_COLUMNS)
            + n_ch * n_met
            + n_rounds
            # ch × met × round (in_<round> only — no _out_)
            + n_ch * n_met * n_rounds
            + n_rounds * n_ppc
            + n_rounds * n_ch * n_ppch
        )

    def _on_configure_csv_export(self) -> None:
        """Open a compact dialog for selecting channels + metrics to export.

        Two sections of checkboxes: one for channels (from the current
        intersection) and one for metrics (from BUILTIN_METRICS). The
        cross-product is computed automatically — the user doesn't have
        to scroll a 200-item list. Matches the pattern of the existing
        Measure Cells metric-selection dialog in the launcher.
        """
        intersected = self._current_intersection()
        if not intersected:
            QMessageBox.warning(
                self,
                "No channels available",
                "Add at least one dataset so channels can be detected.",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Configure CSV Export Columns")
        dialog.setModal(True)
        dialog.resize(560, 720)

        # Outer layout holds the scroll area + the OK/Cancel button row.
        # Each section keeps its natural height inside the scroll area;
        # the dialog itself is capped to a screen-friendly size.
        outer_layout = QVBoxLayout(dialog)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Scrollable content widget.
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # ── Channels section ──
        ch_box = QGroupBox("Channels to include in CSV")
        ch_layout = QVBoxLayout(ch_box)
        ch_cbs: dict[str, QCheckBox] = {}
        for ch in intersected:
            cb = QCheckBox(ch)
            cb.setChecked(ch in self._selected_csv_channels)
            ch_cbs[ch] = cb
            ch_layout.addWidget(cb)

        ch_btn_row = QHBoxLayout()
        ch_all = QPushButton("All")
        ch_all.clicked.connect(lambda: [cb.setChecked(True) for cb in ch_cbs.values()])
        ch_btn_row.addWidget(ch_all)
        ch_none = QPushButton("None")
        ch_none.clicked.connect(lambda: [cb.setChecked(False) for cb in ch_cbs.values()])
        ch_btn_row.addWidget(ch_none)
        ch_btn_row.addStretch()
        ch_layout.addLayout(ch_btn_row)
        layout.addWidget(ch_box)

        # ── Metrics section ──
        met_box = QGroupBox("Metrics to include in CSV")
        met_layout = QVBoxLayout(met_box)
        met_cbs: dict[str, QCheckBox] = {}
        for name in sorted(BUILTIN_METRICS.keys()):
            cb = QCheckBox(name.replace("_", " ").title())
            cb.setObjectName(name)  # store the original key
            cb.setChecked(name in self._selected_csv_metrics)
            met_cbs[name] = cb
            met_layout.addWidget(cb)

        met_btn_row = QHBoxLayout()
        met_all = QPushButton("All")
        met_all.clicked.connect(lambda: [cb.setChecked(True) for cb in met_cbs.values()])
        met_btn_row.addWidget(met_all)
        met_none = QPushButton("None")
        met_none.clicked.connect(lambda: [cb.setChecked(False) for cb in met_cbs.values()])
        met_btn_row.addWidget(met_none)
        met_btn_row.addStretch()
        met_layout.addLayout(met_btn_row)
        layout.addWidget(met_box)

        # ── Particle metrics (per cell) ──
        # Always shown so the picker is stateful regardless of whether
        # particle analysis is currently enabled. When it's off, the
        # CSV writer in export_run filters out columns that don't
        # exist in the df, so pre-selected particle metrics are safe.
        ppc_box = QGroupBox(
            "Particle metrics — per cell "
            "(used only when particle analysis is enabled)"
        )
        ppc_layout = QVBoxLayout(ppc_box)
        ppc_cbs: dict[str, QCheckBox] = {}
        for name in _PARTICLE_PER_CELL_METRICS:
            cb = QCheckBox(name.replace("_", " ").title())
            cb.setObjectName(name)
            cb.setChecked(name in self._selected_csv_particle_per_cell)
            ppc_cbs[name] = cb
            ppc_layout.addWidget(cb)

        ppc_btn_row = QHBoxLayout()
        ppc_all = QPushButton("All")
        ppc_all.clicked.connect(
            lambda: [cb.setChecked(True) for cb in ppc_cbs.values()]
        )
        ppc_btn_row.addWidget(ppc_all)
        ppc_none = QPushButton("None")
        ppc_none.clicked.connect(
            lambda: [cb.setChecked(False) for cb in ppc_cbs.values()]
        )
        ppc_btn_row.addWidget(ppc_none)
        ppc_btn_row.addStretch()
        ppc_layout.addLayout(ppc_btn_row)
        layout.addWidget(ppc_box)

        # ── Particle metrics (per channel) ──
        ppch_box = QGroupBox(
            "Particle metrics — per channel "
            "(used only when particle analysis is enabled)"
        )
        ppch_layout = QVBoxLayout(ppch_box)
        ppch_cbs: dict[str, QCheckBox] = {}
        for name in _PARTICLE_PER_CHANNEL_METRICS:
            cb = QCheckBox(name.replace("_", " ").title())
            cb.setObjectName(name)
            cb.setChecked(name in self._selected_csv_particle_per_channel)
            ppch_cbs[name] = cb
            ppch_layout.addWidget(cb)

        ppch_btn_row = QHBoxLayout()
        ppch_all = QPushButton("All")
        ppch_all.clicked.connect(
            lambda: [cb.setChecked(True) for cb in ppch_cbs.values()]
        )
        ppch_btn_row.addWidget(ppch_all)
        ppch_none = QPushButton("None")
        ppch_none.clicked.connect(
            lambda: [cb.setChecked(False) for cb in ppch_cbs.values()]
        )
        ppch_btn_row.addWidget(ppch_none)
        ppch_btn_row.addStretch()
        ppch_layout.addLayout(ppch_btn_row)
        layout.addWidget(ppch_box)

        # ── Note ──
        note = QLabel(
            "The exported CSVs will contain every combination of the "
            "selected channels × metrics, plus core columns (label, "
            "centroid, area), group assignments per round, and per-round "
            "inside/outside columns. When particle analysis is enabled, "
            "the selected per-cell particle metrics produce one column "
            "per round (<round>_<metric>) and the per-channel particle "
            "metrics produce one column per round × channel "
            "(<round>_<channel>_<metric>). The full measurements.parquet "
            "always contains everything regardless of this selection."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        layout.addWidget(note)

        # Put the content in a scroll area so each section keeps its
        # natural height while the dialog stays at a screen-friendly
        # size. Without this the metrics list compresses to fit when
        # all five sections are expanded (iteration-3 user feedback).
        outer_layout.addWidget(wrap_in_scroll(content), stretch=1)

        # ── Buttons (outside the scroll area, always visible) ──
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        btn_bar = QWidget()
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(12, 6, 12, 12)
        btn_layout.addWidget(buttons)
        outer_layout.addWidget(btn_bar)

        if dialog.exec_() != QDialog.Accepted:
            return

        # The user made an explicit channel choice — stop auto-selecting
        # all channels on subsequent dataset changes.
        self._csv_channels_auto = False
        self._selected_csv_channels = {
            ch for ch, cb in ch_cbs.items() if cb.isChecked()
        }
        self._selected_csv_metrics = {
            name for name, cb in met_cbs.items() if cb.isChecked()
        }
        self._selected_csv_particle_per_cell = {
            name for name, cb in ppc_cbs.items() if cb.isChecked()
        }
        self._selected_csv_particle_per_channel = {
            name for name, cb in ppch_cbs.items() if cb.isChecked()
        }
        self._update_csv_summary()

    def _round_names_from_cards(self) -> list[str]:
        """Valid round names, in order — for the CSV column picker."""
        return [
            card.to_dict()["name"]
            for card in self._round_cards
            if card.name_is_valid()
        ]

    # ── Output folder ─────────────────────────────────────────

    def _on_browse_output(self) -> None:
        start = self._output_edit.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self, "Choose output parent folder", start
        )
        if folder:
            self._output_edit.setText(folder)

    # ── Start button: validation + accept ────────────────────

    def _update_start_enabled(self) -> None:
        has_datasets = bool(self._pending_datasets)
        if self._mask_selection_group.isChecked():
            # Mask-reuse mode: the rounds table is hidden, so the gate is
            # "at least one dataset has a mask selected" instead of rounds.
            has_work = bool(self.existing_mask_selections)
        else:
            has_work = len(self._round_cards) > 0
        self._start_btn.setEnabled(has_datasets and has_work)

    def _on_start_clicked(self) -> None:
        """Validate the current state and, on success, accept the dialog."""
        cfg = self._try_build_config()
        if cfg is None:
            return
        self._workflow_config = cfg
        self._save_output_setting()
        self.accept()

    def _try_build_config(self) -> WorkflowConfig | None:
        """Build and validate a :class:`WorkflowConfig`.

        Returns the config on success, or ``None`` on any validation
        error (after showing the user a warning dialog). The dialog stays
        open so the user can correct the problem.
        """
        if not self._pending_datasets:
            self._warn("Add at least one dataset before starting.")
            return None

        use_existing_masks = self._mask_selection_group.isChecked()
        if not use_existing_masks and len(self._round_cards) == 0:
            self._warn(
                "Add at least one thresholding round, or enable "
                "'Use existing masks (skip thresholding rounds)'."
            )
            return None

        # Channel intersection — with outlier prompt.
        kept_datasets = self._resolve_channel_intersection()
        if kept_datasets is None:
            return None  # user aborted the prompt

        intersected = list(
            intersect_channels(
                [(pd.display_name, list(pd.channel_names)) for pd in kept_datasets]
            )[0]
        )
        if not intersected:
            self._warn(
                "No channels are shared across the selected datasets. "
                "Remove mismatched datasets or pick a different folder."
            )
            return None

        # Existing-mask reuse: skip the rounds entirely and measure the
        # selected masks instead (either/or per run). Restrict the selection
        # to datasets that survived the channel-intersection prune.
        kept_names = {pd.display_name for pd in kept_datasets}
        existing_mask_selections: dict[str, list[str]] = {}
        if use_existing_masks:
            existing_mask_selections = {
                name: masks
                for name, masks in self.existing_mask_selections.items()
                if name in kept_names
            }
            if not existing_mask_selections:
                self._warn(
                    "Select at least one mask for at least one dataset, "
                    "or uncheck 'Use existing masks (skip thresholding rounds)'."
                )
                return None
            rounds = []
        else:
            # Build rounds and validate each round's channel is in the intersection.
            try:
                rounds = self._rounds_from_cards(intersected)
            except ValueError as e:
                self._warn(str(e))
                return None

            # Pre-flight: a per-cell round needs a pixel size only when its size
            # knob is in µm. px-unit rounds and auto-detect are px-native and need
            # none. Catch the µm case now rather than as a per-dataset failure after
            # a long run. Only h5 datasets can be checked up front (tiff_pending
            # datasets are compressed during the run); their pixel size is enforced
            # by the runtime backstop in apply_threshold_headless.
            needs_pixel_size = any(
                (r.adaptive_clip is not None and r.adaptive_clip.d_min_unit == "um")
                or (
                    r.auto_extract is not None
                    and r.auto_extract.smallest_particle_um is not None
                    and r.auto_extract.smallest_particle_unit == "um"
                )
                or (r.min_particle_size > 0 and r.min_particle_size_unit == "um2")
                for r in rounds
            )
            if needs_pixel_size:
                missing = self._datasets_without_pixel_size(kept_datasets)
                if missing:
                    self._warn(
                        "µm d_min / smallest-particle / µm² min-size values need a "
                        "pixel size (µm/px) on every dataset, but it is missing on: "
                        + ", ".join(missing)
                        + ". Set the pixel size on these datasets, switch the round's "
                        "Unit / Min unit to px, or use auto-detect (Smallest = 0).\n\n"
                        "Note: .tiff datasets cannot be checked here because their "
                        ".h5 does not exist until the run compresses them. They are "
                        "checked immediately after compression instead, and any that "
                        "lack a pixel size are failed then rather than mid-run."
                    )
                    return None

        # Cellpose settings — read from the shared form.
        try:
            cellpose = self._cp_form.settings()
        except ValueError as e:
            self._warn(f"Cellpose settings invalid: {e}")
            return None

        # Output parent — must be non-empty and writable.
        out_text = self._output_edit.text().strip()
        if not out_text:
            self._warn("Choose an output parent folder.")
            return None
        output_parent = Path(out_text)
        if output_parent.exists() and not output_parent.is_dir():
            self._warn(f"Output parent is not a directory: {output_parent}")
            return None

        if use_existing_masks:
            # Round names for the CSV columns are the measured mask names.
            union_masks: list[str] = []
            for masks in existing_mask_selections.values():
                for m in masks:
                    if m not in union_masks:
                        union_masks.append(m)
            channels = [ch for ch in intersected if ch in self._selected_csv_channels]
            selected_cols = build_selected_csv_columns(
                channels,
                union_masks,
                metrics=self._selected_csv_metrics,
                particle_per_cell=self._selected_csv_particle_per_cell,
                particle_per_channel=self._selected_csv_particle_per_channel,
            )
        else:
            selected_cols = self._build_selected_csv_columns(intersected, rounds)

        seg_channel = self._cp_seg_channel.currentText()
        if not seg_channel or seg_channel.startswith("("):
            self._warn("Choose a segmentation channel in the Cellpose settings.")
            return None

        # Edge-mode selector value.
        edge_mode = self._edge_mode.currentData()
        if edge_mode is None:
            edge_mode = EdgeMode.EXCLUDE

        # Optional dilute settings.
        dilute_settings = self._try_build_dilute_settings(intersected)
        if dilute_settings is False:
            # Validation error already surfaced.
            return None

        # Optional particle analysis. The group is checkable so we can
        # detect enabled vs disabled directly from the group's state.
        particle_settings: ParticleSettings | None = None
        if self._particle_group.isChecked():
            unit = self._particle_min_area_unit.currentData() or "px"
            try:
                particle_settings = ParticleSettings(
                    min_area=float(self._particle_min_area.value()),
                    min_area_unit=str(unit),
                )
            except ValueError as e:
                self._warn(f"Particle settings invalid: {e}")
                return None

        entries = [pd.to_entry() for pd in kept_datasets]
        try:
            return WorkflowConfig(
                datasets=entries,
                cellpose=cellpose,
                thresholding_rounds=rounds,
                selected_csv_columns=selected_cols,
                output_parent=output_parent,
                seg_channel_name=seg_channel,
                edge_mode=edge_mode,
                edge_margin_px=int(self._edge_margin.value()),
                dilute_settings=dilute_settings,
                cellpose_segmentation_name=self._cp_seg_name.text().strip()
                or "cp_mask",
                particle_settings=particle_settings,
                run_seg_qc_on_existing=self._run_seg_qc.isChecked(),
                use_existing_masks=use_existing_masks,
                existing_mask_selections=existing_mask_selections,
            )
        except ValueError as e:
            self._warn(f"Configuration invalid: {e}")
            return None

    def _try_build_dilute_settings(
        self, intersected_channels: list[str]
    ) -> DiluteSettings | None | bool:
        """Construct DiluteSettings from the dilute group when checked.

        Returns ``None`` when the group is unchecked (dilute disabled).
        Returns a ``DiluteSettings`` instance on success. Returns
        ``False`` (sentinel for "validation failed; dialog stays open")
        when the user enabled dilute but the inputs are invalid.
        """
        dilute_group = self._dilute_mask_name.parent()
        if not isinstance(dilute_group, QGroupBox) or not dilute_group.isChecked():
            return None

        mask_name = self._dilute_mask_name.text().strip()
        if not mask_name:
            self._warn(
                "Dilute mask name is required when dilute generation is enabled."
            )
            return False

        channel = self._dilute_channel.currentText()
        if not channel or channel.startswith("("):
            self._warn(
                "Pick a dilute channel from the intersection of dataset channels."
            )
            return False
        if intersected_channels and channel not in intersected_channels:
            self._warn(
                f"Dilute channel {channel!r} is not in the channel intersection "
                f"{intersected_channels}."
            )
            return False

        try:
            return DiluteSettings(
                mask_name=mask_name,
                dilation_radius_px=int(self._dilute_dilation_px.value()),
                channel=channel,
                metric=self._dilute_metric.currentText(),
                algorithm=self._dilute_algorithm.currentData() or ThresholdAlgorithm.GMM,
                gmm_criterion=self._dilute_gmm_criterion.currentData() or GmmCriterion.BIC,
                gmm_max_components=int(self._dilute_gmm_max.value()),
                kmeans_n_clusters=int(self._dilute_kmeans_n.value()),
                gaussian_sigma=float(self._dilute_sigma.value()),
            )
        except ValueError as e:
            self._warn(f"Dilute settings invalid: {e}")
            return False

    def _resolve_channel_intersection(
        self,
    ) -> list[_PendingDataset] | None:
        """Run the intersection + outlier prompt. Returns kept datasets or None.

        Matches the brainstorm rule: if every dataset has zero overlap
        with the rest, the config dialog shows a prompt offering
        "Proceed without these N datasets" vs "Abort and fix".
        """
        sources: list[ChannelSource] = [
            (pd.display_name, list(pd.channel_names))
            for pd in self._pending_datasets
        ]
        intersected, outliers = intersect_channels(sources)

        if intersected:
            return list(self._pending_datasets)

        # Empty intersection. Phase 1's simplified rule returns all
        # dataset names as outliers in this case. The user needs to
        # either fix the selection or drop datasets until the remaining
        # set shares channels.
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("No shared channels")
        box.setText(
            "The selected datasets do not share a common channel. "
            "The run cannot proceed without at least one channel "
            "present in every dataset."
        )
        box.setInformativeText(
            "Remove the datasets that don't match and try again.\n\n"
            f"Datasets flagged: {', '.join(outliers)}"
        )
        abort = box.addButton("Cancel run", QMessageBox.RejectRole)
        box.addButton("OK", QMessageBox.AcceptRole)
        box.setDefaultButton(abort)
        box.exec_()
        return None

    def _rounds_from_cards(
        self, intersected: list[str]
    ) -> list[ThresholdingRound]:
        """Build a list of :class:`ThresholdingRound` from the round cards.

        Raises ``ValueError`` (caught upstream) on any per-round validation
        failure; the message is prefixed with the round number so the user
        can find it. Only two methods exist — Grouped Otsu and Adaptive Local
        Clipping (two-pass); a card only ever sets ``auto_extract`` (ALC) or
        neither (Grouped Otsu), never ``adaptive_clip``.
        """
        rounds: list[ThresholdingRound] = []
        for i, data in enumerate(self._round_dicts()):
            if data["channel"] not in intersected:
                raise ValueError(
                    f"Round {i + 1} ({data['name']!r}) references channel "
                    f"{data['channel']!r}, which is not in the intersection "
                    f"{intersected}."
                )
            try:
                algo = ThresholdAlgorithm(data["algorithm"])
            except ValueError as e:
                raise ValueError(f"Round {i + 1}: {e}") from e
            method = data.get("method")
            size_unit = data.get("size_unit", "um")
            sigma = float(data["sigma"])  # σ is the detector presmooth for ALC rounds
            d_min = float(data["d_min_um"])
            auto_extract = None
            if method == METHOD_AUTO_EXTRACT:
                # 0 (the auto-extraction floor) means auto-detect the smallest → None.
                auto_extract = AutoExtractSettings(
                    smallest_particle_um=(d_min if d_min > 0 else None),
                    presmooth_sigma_px=sigma,
                    smallest_particle_unit=size_unit,
                )
            # Guided CNR split is opt-in and valid only on the per-cell ALC round;
            # the card disables the checkbox on Grouped Otsu, but guard the build too.
            cnr_classify = None
            if data.get("cnr_classify") and method == METHOD_AUTO_EXTRACT:
                # GMM 2-pop overrides the guided threshold with a forced two-group split.
                cnr_classify = CnrClassifySettings(
                    threshold=float(data["cnr_threshold"]),
                    forced=bool(data.get("cnr_forced", False)),
                )
            try:
                rounds.append(
                    ThresholdingRound(
                        name=data["name"],
                        channel=data["channel"],
                        metric=data["metric"],
                        algorithm=algo,
                        gmm_criterion=GmmCriterion.BIC,
                        gmm_max_components=int(data["gmm_max"]),
                        kmeans_n_clusters=int(data["kmeans_k"]),
                        gaussian_sigma=float(data["sigma"]),
                        adaptive_clip=None,
                        auto_extract=auto_extract,
                        cnr_classify=cnr_classify,
                        min_particle_size=float(data.get("min_particle_size", 0.0)),
                        min_particle_size_unit=data.get("min_particle_size_unit", "px"),
                    )
                )
            except ValueError as e:
                raise ValueError(f"Round {i + 1}: {e}") from e
        return rounds

    def _datasets_without_pixel_size(self, kept_datasets: list[Any]) -> list[str]:
        """Names of h5_existing datasets that lack a usable pixel_size_um.

        tiff_pending datasets are skipped (no h5 yet); a dataset whose store
        cannot be opened is reported as missing so the user investigates."""
        missing: list[str] = []
        for pd_ in kept_datasets:
            if pd_.source is not DatasetSource.H5_EXISTING:
                continue
            try:
                ps = DatasetStore(pd_.h5_path).metadata.get("pixel_size_um")
            except Exception:
                ps = None
            if not ps or float(ps) <= 0:
                missing.append(pd_.display_name)
        return missing

    def _build_selected_csv_columns(
        self,
        intersected: list[str],
        rounds: list[ThresholdingRound],
    ) -> list[str]:
        """Compute the full list of CSV columns from the user's channel + metric selection.

        Delegates to the shared Qt-free :func:`build_selected_csv_columns`
        (also used by ``percell4-batch-measure``) so the GUI and CLI exports
        cannot drift. Identity columns (dataset, cell_id, label) are always
        prepended by the export step regardless of what's in this list. The
        ``_out_<round>`` overlap variants are intentionally NOT emitted —
        measure_one drops them from the parquet too.
        """
        channels = [ch for ch in intersected if ch in self._selected_csv_channels]
        return build_selected_csv_columns(
            channels,
            [r.name for r in rounds],
            metrics=self._selected_csv_metrics,
            particle_per_cell=self._selected_csv_particle_per_cell,
            particle_per_channel=self._selected_csv_particle_per_channel,
        )

    def _save_output_setting(self) -> None:
        out = self._output_edit.text().strip()
        if out:
            qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
            qs.setValue(_QSETTINGS_OUTPUT_KEY, out)

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "Configuration incomplete", message)

    # ── Public API ────────────────────────────────────────────

    @property
    def workflow_config(self) -> WorkflowConfig | None:
        """The validated :class:`WorkflowConfig`, or ``None`` if not accepted."""
        return self._workflow_config
