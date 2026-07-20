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
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from qtpy.QtCore import QSettings, Qt
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QAbstractItemView,
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
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from percell4.domain.measure.metrics import BUILTIN_METRICS
from percell4.gui._cellpose_settings_form import CellposeSettingsForm
from percell4.gui._dialog_utils import cap_to_screen, wrap_in_scroll
from percell4.store import DatasetStore
from percell4.workflows.channels import ChannelSource, intersect_channels
from percell4.workflows.csv_columns import (
    CORE_OPTIONAL_COLUMNS,
    DEFAULT_CSV_METRICS,
    DEFAULT_CSV_PARTICLE_PER_CELL,
    DEFAULT_CSV_PARTICLE_PER_CHANNEL,
    build_selected_csv_columns,
)
from percell4.workflows.mask_grouping import group_by_mask_signature
from percell4.workflows.models import (
    AdaptiveClipSettings,
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
from percell4.workflows.phases import pick_existing_segmentation

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

_QSETTINGS_ORG = "LeeLabPerCell4"
_QSETTINGS_APP = "PerCell4"
_QSETTINGS_OUTPUT_KEY = "single_cell_threshold_workflow/output_parent"

# Shown (disabled) in the segmentation picker for datasets with no labels yet.
_NO_SEGMENTATION_LABEL = "None — Cellpose will segment"

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

# Matches the `_ROUND_NAME_RE` in `workflows/models.py`. Duplicated here so
# we can live-validate the cell while the user types — reconstructing the
# ThresholdingRound dataclass on every keystroke just to catch a typo
# would be heavy-handed.
_ROUND_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,39}$")

# Rounds table column indexes
_ROUND_COL_NAME = 0
_ROUND_COL_CHANNEL = 1
_ROUND_COL_METRIC = 2
_ROUND_COL_METHOD = 3
_ROUND_COL_ALGO = 4
_ROUND_COL_GMM_MAX = 5
_ROUND_COL_KMEANS_K = 6
_ROUND_COL_SIGMA = 7
_ROUND_COL_DMIN = 8
_ROUND_COL_K = 9
_ROUND_COL_GLOBAL = 10
_ROUND_COL_SIZE_UNIT = 11
_ROUND_COL_CNR_ON = 12
_ROUND_COL_CNR_THR = 13
_ROUND_COL_CNR_FORCED = 14
_ROUND_COL_MIN_SIZE = 15
_ROUND_COL_MIN_SIZE_UNIT = 16
_ROUND_COL_COUNT = 17
_ROUND_COL_HEADERS = (
    "Name",
    "Channel",
    "Metric",
    "Method",
    "Algorithm",
    "GMM max",
    "K-means K",
    "σ",
    "d_min",
    "k",
    "global",
    "Unit",
    "CNR split",
    "CNR thr",
    "GMM 2-pop",
    "Min size",
    "Min unit",
)

# Method dropdown labels. "Grouped Otsu" is the default (the legacy per-group path).
# The two per-cell ALC methods are named to match the Analysis-panel terminology so
# a user reproducing a single-dataset GUI run picks the SAME detector in batch:
#   - "Adaptive Local Clipping (two-pass)" is the two-pass auto-extractor — the exact
#     algorithm the GUI "Adaptive Local Clipping" panel runs (its only mode). Carries
#     an AutoExtractSettings sentinel.
#   - "Adaptive σ-clipping (single-window)" is the older single-window detector, which
#     has NO GUI panel counterpart. Carries an AdaptiveClipSettings sentinel. Kept for
#     configs/users that want it, but clearly distinguished so it is not mistaken for
#     the GUI's "Adaptive Local Clipping".
# The constants' VALUES are display text only; routing and serialization key off the
# sentinel dataclass, so relabeling here does not touch saved run_config.json files.
_METHOD_GROUPED = "Grouped Otsu"
_METHOD_ADAPTIVE = "Adaptive σ-clipping (single-window)"
_METHOD_AUTO_EXTRACT = "Adaptive Local Clipping (two-pass)"


# ── Internal per-dataset record ──────────────────────────────────────────


def _derive_tiff_pending_channel_names(
    selected_token_ids: list[str],
    layer_assignments: dict[str, Any],
) -> list[str]:
    """Resolve workflow-side channel names for a tiff_pending dataset.

    ``selected_token_ids`` carries the raw token IDs from the compress
    dialog (``"00"``, ``"01"``, …). ``layer_assignments`` may map a
    token to a ``LayerAssignment`` with a user-chosen display name.

    When the user did not rename a channel, fall back to the
    ``ch``-prefixed token (``f"ch{ch_id}"``) — this matches what
    ``import_dataset`` actually writes to the HDF5's
    ``/metadata.channel_names`` (see
    ``src/percell4/adapters/importer.py`` default ``f"ch{ch_key}"``).
    Bare-token fallback used to produce a silent mismatch (workflow
    config: ``"02"`` vs HDF5: ``"ch02"``) that wrecked
    ``threshold_compute`` after a long segmentation pass.
    """
    out: list[str] = []
    for ch_id in selected_token_ids:
        override = layer_assignments.get(ch_id)
        name = getattr(override, "name", "") if override is not None else ""
        out.append(name or f"ch{ch_id}")
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
    }

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


# ── Grouped mask picker ──────────────────────────────────────────────────

# Keep the shared mask list compact; it scrolls if a group has many masks.
_MASK_LIST_MAX_H = 90
# Cap the expanded member-name list so a large group can't grow an unbounded
# wall of names; the QListWidget scrolls past this height.
_MEMBER_LIST_MAX_H = 120


def _dataset_count_label(n: int) -> str:
    """"1 dataset" / "N datasets" — proper singular/plural."""
    return f"{n} dataset" if n == 1 else f"{n} datasets"


# Snapshot of prior mask picks, keyed by (signature, split_key) — split_key is
# None for a remainder group, a frozenset of member names for a breakout split.
_PriorSelections = dict[tuple[tuple[str, ...], frozenset[str] | None], set[str]]


def _selected_texts(widget: QListWidget) -> list[str]:
    """Texts of the currently-selected items in a QListWidget, in row order."""
    return [
        widget.item(i).text()
        for i in range(widget.count())
        if widget.item(i).isSelected()
    ]


class _MaskGroup:
    """One rendered group of datasets sharing an available-mask signature.

    ``members`` are the datasets in this (sub-)group; ``list_widget`` is the
    single shared mask multi-select they all use (``None`` for the no-mask
    group). ``has_masks`` is tracked explicitly rather than via widget-enabled
    state, because a checkable QGroupBox disables ALL its children when
    unchecked — so ``isEnabled()`` can't distinguish "no masks here" from
    "mask-reuse mode off". ``toggle_btn`` / ``members_list`` are the collapsible
    member-name affordance, present only for multi-member groups.

    ``split_key`` identifies a breakout sub-group (U4): ``None`` for the
    default/remainder group, or the ``frozenset`` of member display names split
    off. Two ``_MaskGroup`` rows can share a ``signature`` but carry a different
    ``split_key`` and hold independent selections. ``split_btn`` / ``merge_btn``
    are the breakout controls.
    """

    __slots__ = (
        "signature",
        "members",
        "list_widget",
        "has_masks",
        "toggle_btn",
        "members_list",
        "split_key",
        "split_btn",
        "merge_btn",
    )

    def __init__(
        self,
        *,
        signature: tuple[str, ...],
        members: list[_PendingDataset],
        list_widget: QListWidget | None,
        has_masks: bool,
        split_key: frozenset[str] | None = None,
        toggle_btn: QToolButton | None = None,
        members_list: QListWidget | None = None,
        split_btn: QPushButton | None = None,
        merge_btn: QPushButton | None = None,
    ) -> None:
        self.signature = signature
        self.members = members
        self.list_widget = list_widget
        self.has_masks = has_masks
        self.split_key = split_key
        self.toggle_btn = toggle_btn
        self.members_list = members_list
        self.split_btn = split_btn
        self.merge_btn = merge_btn


# ── Dialog ──────────────────────────────────────────────────────────────


class WorkflowConfigDialog(QDialog):
    """Modal configuration dialog for the single-cell thresholding workflow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Single-cell thresholding analysis workflow")
        self.setModal(True)
        self.resize(960, 720)
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
            "at thresholding. Choose which segmentation layer each one's "
            "thresholding rounds and measurement should use (tracked layers "
            "are preferred by default). Datasets with no segmentation will be "
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

        # Rebuilt by _refresh_segmentation_picker whenever the dataset queue
        # changes. Each row: dataset name -> combo of its /labels resources.
        self._seg_form_host = QWidget()
        self._seg_form = QFormLayout(self._seg_form_host)
        self._seg_form.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._seg_form_host)
        self._seg_combos: list[tuple[_PendingDataset, QComboBox]] = []
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
        """Rebuild the per-dataset segmentation combos from the current queue."""
        form = getattr(self, "_seg_form", None)
        if form is None:
            return
        while form.rowCount():
            form.removeRow(0)
        self._seg_combos = []
        for pd in self._pending_datasets:
            labels = self._dataset_segmentations(pd)
            combo = QComboBox()
            if labels:
                combo.addItems(labels)
                default = pick_existing_segmentation(labels)
                if default in labels:
                    combo.setCurrentText(default)
            else:
                combo.addItem(_NO_SEGMENTATION_LABEL)
                combo.setEnabled(False)  # nothing to choose; Cellpose will run
            self._seg_combos.append((pd, combo))
            form.addRow(pd.display_name, combo)

    @property
    def segmentation_overrides(self) -> dict[str, str]:
        """Per-dataset chosen segmentation, for datasets that already have one.

        Keyed by the dataset's (final) display name; passed to
        ``SingleCellThresholdingRunner(segmentation_overrides=...)``. Datasets
        with no segmentation (Cellpose will run) are omitted.
        """
        overrides: dict[str, str] = {}
        for pd, combo in getattr(self, "_seg_combos", []):
            if not combo.isEnabled():
                continue
            choice = combo.currentText()
            if choice and choice != _NO_SEGMENTATION_LABEL:
                overrides[pd.display_name] = choice
        return overrides

    # ── Existing-mask reuse ───────────────────────────────────

    def _build_mask_selection_group(self) -> QGroupBox:
        """Checkable group to reuse existing /masks instead of thresholding.

        When checked, the run skips the Threshold Rounds step entirely and
        measures the selected masks (either/or per run). Datasets exposing an
        identical set of ``/masks`` layers are grouped into one shared picker
        (``group_by_mask_signature``); a subset of a group can be split off with
        its own selection. This is a pre-run config control (dialog-local state
        only); it never touches the live session.
        """
        box = QGroupBox("Use existing masks (skip thresholding rounds)")
        box.setCheckable(True)
        box.setChecked(False)
        box.setToolTip(
            "When checked, the workflow does NOT compute threshold rounds. "
            "Instead it measures the mask layer(s) you select per dataset, "
            "running per-cell measurement + particle analysis + export on "
            "them. Uncheck to configure Threshold Rounds as usual."
        )
        self._mask_selection_group = box
        outer = QVBoxLayout(box)
        note = QLabel(
            "Datasets that expose the same available mask layers are grouped: "
            "pick masks once per group and they apply to every member. Expand a "
            "group to see its datasets. The Threshold Rounds section is hidden "
            "while this is on."
        )
        note.setWordWrap(True)
        outer.addWidget(note)
        self._mask_form_host = QWidget()
        self._mask_form = QFormLayout(self._mask_form_host)
        self._mask_form.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._mask_form_host)
        # One row per _MaskGroup (datasets sharing an available-mask signature).
        # Rebuilt by _refresh_mask_picker whenever the queue changes.
        self._mask_groups: list[_MaskGroup] = []
        # Breakout state (U4): per signature, the list of split-off member sets
        # (the remainder is implicit — every member not in any split). Empty
        # until the user splits a group.
        self._mask_breakouts: dict[tuple[str, ...], list[frozenset[str]]] = {}
        # Seed selection for a just-created split, consumed on the next refresh
        # so the new sub-group inherits its parent's current picks.
        self._split_seeds: _PriorSelections = {}
        box.toggled.connect(self._on_mask_reuse_toggled)
        return box

    def _on_mask_reuse_toggled(self, checked: bool) -> None:
        """Hide the Threshold Rounds group when reusing existing masks.

        Mask selections in the per-dataset lists are preserved across
        toggles (the list widgets are not rebuilt here), so toggling is
        non-destructive.
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
        """Rebuild the grouped mask picker from the current queue.

        Datasets exposing an identical set of available ``/masks`` layers are
        collapsed into one shared multi-select list (``group_by_mask_signature``);
        the no-mask group is rendered last and is non-selectable. A signature
        may be further partitioned into breakout sub-groups (U4). Prior
        selections are preserved by (signature, split_key) so a queue refresh
        (or re-open) doesn't silently drop the user's picks.
        """
        form = getattr(self, "_mask_form", None)
        if form is None:
            return
        # Snapshot prior selections keyed by (signature, split_key) — stable
        # across regroup; indices and display names shift when datasets are
        # added/removed. Then fold in any pending split seed so a just-created
        # sub-group inherits its parent's picks.
        prior: _PriorSelections = {
            (group.signature, group.split_key): set(_selected_texts(group.list_widget))
            for group in getattr(self, "_mask_groups", [])
            if group.has_masks and group.list_widget is not None
        }
        prior.update(getattr(self, "_split_seeds", {}))
        self._split_seeds = {}
        while form.rowCount():
            form.removeRow(0)
        self._mask_groups = []

        by_name = {pd.display_name: pd for pd in self._pending_datasets}
        plans = group_by_mask_signature(
            (pd.display_name, self._dataset_masks(pd))
            for pd in self._pending_datasets
        )
        # Prune breakout partitions for signatures no longer in the queue, so a
        # remove-all-then-re-add doesn't resurrect a stale split (group_by_mask_
        # signature only emits signatures that still have a present member).
        live_sigs = {plan.signature for plan in plans}
        self._mask_breakouts = {
            sig: splits
            for sig, splits in self._mask_breakouts.items()
            if sig in live_sigs
        }
        all_no_mask = bool(plans) and all(not plan.signature for plan in plans)
        for plan in plans:
            if not plan.signature:  # no-mask group — single non-selectable row
                members = [by_name[name] for name in plan.member_names]
                group = self._make_mask_group(plan.signature, members, None, prior)
                self._mask_groups.append(group)
                form.addRow(
                    self._build_mask_group_row(group, all_no_mask=all_no_mask)
                )
                continue
            # Subdivide this signature into remainder + breakout sub-groups.
            for split_key, names, prior_key in self._subgroups_for(
                plan.signature, plan.member_names
            ):
                members = [by_name[name] for name in names]
                group = self._make_mask_group(
                    plan.signature, members, split_key, prior, prior_key=prior_key
                )
                self._mask_groups.append(group)
                form.addRow(
                    self._build_mask_group_row(group, all_no_mask=all_no_mask)
                )

    def _subgroups_for(
        self, signature: tuple[str, ...], member_names: list[str]
    ) -> list[tuple[frozenset[str] | None, list[str], frozenset[str] | None]]:
        """Partition a signature's members into remainder + breakout sub-groups.

        Returns ``(split_key, names, prior_key)`` triples: the remainder first
        (``split_key=None``), then one entry per non-empty split. ``prior_key``
        is the split's pre-reconciliation frozenset (``None`` for the remainder),
        so the caller can recover a snapshot stored under the larger key when a
        split shrinks. Reconciles the stored partition against the current
        membership — dropped datasets fall out of their split, new same-signature
        datasets land in the remainder — and re-normalizes ``_mask_breakouts`` so
        emptied splits do not linger.
        """
        stored = self._mask_breakouts.get(signature, [])
        present = set(member_names)
        # (original stored split, reconciled-to-present split)
        reconciled: list[tuple[frozenset[str], frozenset[str]]] = []
        for split in stored:
            keep = frozenset(n for n in split if n in present)
            if keep:
                reconciled.append((split, keep))
        if reconciled:
            self._mask_breakouts[signature] = [kept for _orig, kept in reconciled]
        else:
            self._mask_breakouts.pop(signature, None)

        split_members = (
            set().union(*[kept for _orig, kept in reconciled]) if reconciled else set()
        )
        remainder = [n for n in member_names if n not in split_members]
        out: list[tuple[frozenset[str] | None, list[str], frozenset[str] | None]] = []
        if remainder:
            out.append((None, remainder, None))
        for original, kept in reconciled:
            out.append((kept, [n for n in member_names if n in kept], original))
        return out

    def _make_mask_group(
        self,
        signature: tuple[str, ...],
        members: list[_PendingDataset],
        split_key: frozenset[str] | None,
        prior: _PriorSelections,
        *,
        prior_key: frozenset[str] | None = None,
    ) -> _MaskGroup:
        """Build a _MaskGroup + its shared mask list, restoring prior picks.

        Falls back to ``prior_key`` when the snapshot has no entry for the
        current ``split_key`` — this migrates a selection when a split shrinks
        (a member was removed), so the surviving members keep their masks.
        """
        if not signature:  # no-mask group — no selectable list
            return _MaskGroup(
                signature=signature,
                members=members,
                list_widget=None,
                has_masks=False,
                split_key=split_key,
            )
        keep = prior.get((signature, split_key))
        if keep is None and prior_key is not None and prior_key != split_key:
            keep = prior.get((signature, prior_key))
        keep = keep or set()
        lst = QListWidget()
        # MultiSelection: a plain click toggles an item on/off, so a previously
        # chosen mask unselects with one click — no Ctrl modifier required.
        lst.setSelectionMode(QAbstractItemView.MultiSelection)
        lst.setMaximumHeight(_MASK_LIST_MAX_H)
        lst.addItems(list(signature))
        for i in range(lst.count()):
            if lst.item(i).text() in keep:
                lst.item(i).setSelected(True)
        # Toggling a mask's selection changes whether Start may fire.
        lst.itemSelectionChanged.connect(self._update_start_enabled)
        return _MaskGroup(
            signature=signature,
            members=members,
            list_widget=lst,
            has_masks=True,
            split_key=split_key,
        )

    def _build_mask_group_row(
        self, group: _MaskGroup, *, all_no_mask: bool
    ) -> QWidget:
        """Container for one group row: header, optional member list, mask list.

        A singleton group shows the member name inline; a multi-member group
        shows a collapse toggle that reveals the member names. The remainder of
        a multi-member group offers a "Split selected" control; a breakout
        sub-group offers "Merge back" (U4). The no-mask group renders its header
        (and, when it is the only group, an inline explanation) with no
        selectable list.
        """
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        if not group.has_masks:
            self._add_group_header(group, v, suffix=" — no masks available")
            if all_no_mask:
                msg = QLabel(
                    "No masks available in the loaded datasets — add datasets "
                    "that already contain /masks, or use Threshold Rounds instead."
                )
                msg.setWordWrap(True)
                msg.setStyleSheet("color: #888; font-style: italic;")
                v.addWidget(msg)
            return container

        is_split = group.split_key is not None
        # Only the remainder of a multi-member group can be split further.
        splittable = not is_split and len(group.members) > 1
        self._add_group_header(group, v, splittable=splittable)
        if is_split:
            merge_btn = QPushButton("Merge back into group")
            merge_btn.clicked.connect(
                lambda _checked=False, sig=group.signature, key=group.split_key: (
                    self._merge_split(sig, key)
                )
            )
            group.merge_btn = merge_btn
            v.addWidget(merge_btn)
        v.addWidget(group.list_widget)
        return container

    def _add_group_header(
        self,
        group: _MaskGroup,
        layout: QVBoxLayout,
        *,
        suffix: str = "",
        splittable: bool = False,
    ) -> None:
        """Add a group header (and optional member list / split control).

        Singleton groups show the sole member's name inline (a collapse toggle
        would save no space and hide the only identity). Multi-member groups get
        a checkable QToolButton that reveals a capped, scrollable member list.
        When ``splittable``, that member list is selectable and a "Split
        selected into new group" button appears with it (U4 breakout).
        """
        n = len(group.members)
        if n == 1:
            label = QLabel(group.members[0].display_name + suffix)
            if suffix:
                label.setStyleSheet("color: #888;")
            layout.addWidget(label)
            return

        toggle = QToolButton()
        toggle.setCheckable(True)
        toggle.setChecked(False)
        toggle.setArrowType(Qt.RightArrow)
        toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toggle.setText(_dataset_count_label(n) + suffix)
        toggle.setStyleSheet("QToolButton { border: none; }")

        members_list = QListWidget()
        members_list.addItems([pd.display_name for pd in group.members])
        members_list.setSelectionMode(
            QAbstractItemView.MultiSelection
            if splittable
            else QAbstractItemView.NoSelection
        )
        members_list.setMaximumHeight(_MEMBER_LIST_MAX_H)
        members_list.setVisible(False)
        members_list.setStyleSheet("color: #aaa;")

        # Direct slot (no reference-capturing lambda): the toggle owns the list,
        # and both are destroyed together on the next rebuild.
        toggle.toggled.connect(members_list.setVisible)
        # Arrow flip captures only the toggle itself (destroyed with this row),
        # so no stale cross-object reference survives a refresh.
        toggle.toggled.connect(
            lambda checked, t=toggle: t.setArrowType(
                Qt.DownArrow if checked else Qt.RightArrow
            )
        )
        group.toggle_btn = toggle
        group.members_list = members_list
        layout.addWidget(toggle)
        layout.addWidget(members_list)

        if splittable:
            split_btn = QPushButton("Split selected into new group")
            split_btn.setToolTip(
                "Move the checked datasets into their own group so they can "
                "measure a different set of masks."
            )
            split_btn.setVisible(False)
            toggle.toggled.connect(split_btn.setVisible)
            split_btn.clicked.connect(
                lambda _checked=False, sig=group.signature, mlw=members_list: (
                    self._split_selected(sig, mlw)
                )
            )
            group.split_btn = split_btn
            layout.addWidget(split_btn)

    def _find_mask_group(
        self, signature: tuple[str, ...], split_key: frozenset[str] | None
    ) -> _MaskGroup | None:
        """Locate the live _MaskGroup for a (signature, split_key), or None."""
        for group in getattr(self, "_mask_groups", []):
            if group.signature == signature and group.split_key == split_key:
                return group
        return None

    def _split_selected(
        self, signature: tuple[str, ...], members_list: QListWidget
    ) -> None:
        """Split the checked remainder members into a new breakout sub-group.

        Seeds the new sub-group's mask selection from the remainder's current
        picks, then rebuilds. No-op unless at least one — but not all — members
        are checked (splitting all would leave an empty remainder).
        """
        selected = frozenset(_selected_texts(members_list))
        if not selected or len(selected) >= members_list.count():
            return
        remainder = self._find_mask_group(signature, None)
        seed: set[str] = set()
        if remainder is not None and remainder.list_widget is not None:
            seed = set(_selected_texts(remainder.list_widget))
        self._mask_breakouts.setdefault(signature, []).append(selected)
        self._split_seeds[(signature, selected)] = seed
        self._refresh_mask_picker()
        self._update_start_enabled()

    def _merge_split(
        self, signature: tuple[str, ...], split_key: frozenset[str]
    ) -> None:
        """Merge a breakout sub-group back into its signature's remainder."""
        splits = [
            s for s in self._mask_breakouts.get(signature, []) if s != split_key
        ]
        if splits:
            self._mask_breakouts[signature] = splits
        else:
            self._mask_breakouts.pop(signature, None)
        self._refresh_mask_picker()
        self._update_start_enabled()

    @property
    def existing_mask_selections(self) -> dict[str, list[str]]:
        """Per-dataset selected mask names (only datasets with a selection).

        Fans each group's shared selection out to one entry per member, keyed
        by the dataset's display name. No-mask groups and empty selections are
        omitted, so a value is never an empty list. Independent of the outer
        group's checked state — the caller gates on ``use_existing_masks``
        separately — so selections survive a non-destructive toggle (Qt
        disables the lists but keeps the selection model).
        """
        selections: dict[str, list[str]] = {}
        for group in getattr(self, "_mask_groups", []):
            if not group.has_masks or group.list_widget is None:
                continue
            chosen = _selected_texts(group.list_widget)
            if not chosen:
                continue
            for pd in group.members:
                selections[pd.display_name] = list(chosen)
        return selections

    def _build_rounds_group(self) -> QGroupBox:
        box = QGroupBox("Thresholding Rounds (ordered)")
        outer = QVBoxLayout(box)

        self._rounds_table = QTableWidget(0, _ROUND_COL_COUNT)
        self._rounds_table.setHorizontalHeaderLabels(_ROUND_COL_HEADERS)
        # Interactive column resizing — the user can drag column borders.
        # Seed sensible initial widths so the embedded combos/spinboxes
        # aren't cramped on first open; the last column stretches to fill.
        header = self._rounds_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        _initial_round_col_widths = {
            _ROUND_COL_NAME: 110,
            _ROUND_COL_CHANNEL: 150,
            _ROUND_COL_METRIC: 190,
            _ROUND_COL_METHOD: 170,
            _ROUND_COL_ALGO: 110,
            _ROUND_COL_GMM_MAX: 100,
            _ROUND_COL_KMEANS_K: 100,
            _ROUND_COL_SIGMA: 90,
            _ROUND_COL_DMIN: 100,
            _ROUND_COL_K: 70,
            _ROUND_COL_GLOBAL: 60,
            _ROUND_COL_SIZE_UNIT: 70,
            _ROUND_COL_CNR_ON: 80,
            _ROUND_COL_CNR_THR: 80,
            _ROUND_COL_CNR_FORCED: 90,
            _ROUND_COL_MIN_SIZE: 90,
            _ROUND_COL_MIN_SIZE_UNIT: 70,
        }
        for col, width in _initial_round_col_widths.items():
            self._rounds_table.setColumnWidth(col, width)
        self._rounds_table.verticalHeader().setVisible(False)
        self._rounds_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._rounds_table.setSelectionMode(QTableWidget.SingleSelection)
        # Taller default so several rounds are visible without scrolling.
        self._rounds_table.setMinimumHeight(200)
        self._rounds_table.itemChanged.connect(self._on_round_item_changed)
        outer.addWidget(self._rounds_table, stretch=1)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add Round")
        btn_add.clicked.connect(self._on_add_round)
        btn_row.addWidget(btn_add)

        btn_remove = QPushButton("Remove Round")
        btn_remove.clicked.connect(self._on_remove_round)
        btn_row.addWidget(btn_remove)

        btn_up = QPushButton("↑")
        btn_up.setFixedWidth(32)
        btn_up.setToolTip("Move selected round up")
        btn_up.clicked.connect(self._on_round_up)
        btn_row.addWidget(btn_up)

        btn_down = QPushButton("↓")
        btn_down.setFixedWidth(32)
        btn_down.setToolTip("Move selected round down")
        btn_down.clicked.connect(self._on_round_down)
        btn_row.addWidget(btn_down)

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
            "right — µm² mode is converted to pixels per dataset using "
            "that dataset's pixel size."
        )

        self._particle_min_area_unit = QComboBox()
        self._particle_min_area_unit.addItem("px", userData="px")
        self._particle_min_area_unit.addItem("µm²", userData="um2")
        self._particle_min_area_unit.setCurrentIndex(0)
        self._particle_min_area_unit.setToolTip(
            "Unit for Min particle area. px applies a uniform pixel "
            "threshold to every dataset. µm² resolves to a per-dataset "
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

    def _on_add_round(self) -> None:
        intersected = self._current_intersection()
        row = self._rounds_table.rowCount()
        self._rounds_table.insertRow(row)

        # Name column — plain editable text cell with live regex validation.
        name_item = QTableWidgetItem(f"round_{row + 1}")
        self._rounds_table.setItem(row, _ROUND_COL_NAME, name_item)

        # Channel combo populated from the current intersection. If no
        # intersection yet, leave a hint placeholder.
        channel_combo = QComboBox()
        if intersected:
            channel_combo.addItems(intersected)
        else:
            channel_combo.addItem("(add datasets first)")
            channel_combo.setEnabled(False)
        channel_combo.currentTextChanged.connect(self._refresh_column_picker_async)
        self._rounds_table.setCellWidget(row, _ROUND_COL_CHANNEL, channel_combo)

        # Metric combo
        metric_combo = QComboBox()
        metric_combo.addItems(sorted(BUILTIN_METRICS.keys()))
        metric_combo.setCurrentText("median_intensity")
        self._rounds_table.setCellWidget(row, _ROUND_COL_METRIC, metric_combo)

        # Method combo — Grouped Otsu vs per-cell Adaptive sigma clipping.
        # Selecting Adaptive greys the grouping columns (GMM/K-means) and enables
        # the d_min/k columns; Grouped Otsu does the inverse.
        method_combo = QComboBox()
        method_combo.addItems([_METHOD_GROUPED, _METHOD_ADAPTIVE, _METHOD_AUTO_EXTRACT])
        method_combo.setToolTip(
            "Grouped Otsu: per-intensity-group Otsu (uses Algorithm/GMM/K-means).\n"
            "Adaptive Local Clipping (two-pass): the SAME detector the GUI 'Adaptive\n"
            "Local Clipping' panel runs — a per-cell two-pass extractor driven by the\n"
            "smallest particle Ø (d_min; 0 = auto-detect). Pick this to reproduce a\n"
            "single-dataset GUI run in batch; the Min size column = the panel's Min\n"
            "particle size.\n"
            "Adaptive σ-clipping (single-window): an older per-cell single-window\n"
            "detector (d_min + per-cell noise floor). It has NO GUI panel — do not use\n"
            "it to match a GUI result.\n"
            "Both per-cell methods use σ as the detector presmooth and ignore grouping;\n"
            "µm units need a pixel size."
        )
        method_combo.currentTextChanged.connect(
            lambda _text, r=row: self._on_method_changed(r)
        )
        self._rounds_table.setCellWidget(row, _ROUND_COL_METHOD, method_combo)

        # Algorithm combo — toggles which of gmm_max / kmeans_k is enabled.
        algo_combo = QComboBox()
        algo_combo.addItems(
            [ThresholdAlgorithm.GMM.value, ThresholdAlgorithm.KMEANS.value]
        )
        algo_combo.currentTextChanged.connect(
            lambda _text, r=row: self._update_algo_columns_enabled(r)
        )
        self._rounds_table.setCellWidget(row, _ROUND_COL_ALGO, algo_combo)

        # GMM max components
        gmm_spin = QSpinBox()
        gmm_spin.setRange(2, 20)
        gmm_spin.setValue(10)
        self._rounds_table.setCellWidget(row, _ROUND_COL_GMM_MAX, gmm_spin)

        # KMeans k
        kmeans_spin = QSpinBox()
        kmeans_spin.setRange(2, 20)
        kmeans_spin.setValue(3)
        self._rounds_table.setCellWidget(row, _ROUND_COL_KMEANS_K, kmeans_spin)

        # σ — pre-threshold smoothing for grouped Otsu; the per-cell DETECTOR
        # presmooth (Gaussian blur) for both ALC methods. For ALC it is seeded to
        # the validated 1.0 on entering the method (see _on_method_changed) so it is
        # never silently 0 (which collapses detection).
        sigma_spin = QDoubleSpinBox()
        sigma_spin.setRange(0.0, 20.0)
        sigma_spin.setSingleStep(0.1)
        sigma_spin.setValue(0.0)
        sigma_spin.setToolTip(
            "Grouped Otsu: pre-threshold Gaussian smoothing.\n"
            "Adaptive / Auto-extraction: the detector's per-cell presmooth (Gaussian "
            "blur σ, px). Seeded to the validated 1.0 when you pick an ALC method."
        )
        self._rounds_table.setCellWidget(row, _ROUND_COL_SIGMA, sigma_spin)

        # d_min — the smallest particle diameter, in the Unit column's unit. Drives
        # the single-window adaptive detector and the auto-extraction fine pass. For
        # auto-extraction, 0 = auto-detect the smallest particle (the "auto window").
        # The per-method minimum (set in _update_method_columns_enabled) keeps
        # adaptive > 0 while allowing 0 for auto-extraction.
        dmin_spin = QDoubleSpinBox()
        dmin_spin.setRange(0.02, 10000.0)
        dmin_spin.setDecimals(3)
        dmin_spin.setSingleStep(0.05)
        dmin_spin.setValue(0.40)
        dmin_spin.setToolTip(
            "Smallest particle diameter to detect, in the Unit column's unit (µm uses "
            "the dataset pixel size; px is used directly — for datasets without a "
            "pixel size). Auto-extraction only: 0 = auto-detect the smallest particle."
        )
        self._rounds_table.setCellWidget(row, _ROUND_COL_DMIN, dmin_spin)

        # k — adaptive sigma multiplier (greyed for auto-extraction, which fixes the
        # fine-pass k at 1 and derives the coarse-pass k automatically).
        k_spin = QDoubleSpinBox()
        k_spin.setRange(0.0, 20.0)
        k_spin.setSingleStep(0.25)
        k_spin.setValue(1.0)
        self._rounds_table.setCellWidget(row, _ROUND_COL_K, k_spin)

        # global — threshold every cell against ONE pooled σ (1.4826·MAD over all
        # cell pixels) at the same k, instead of each cell's own per-cell σ.
        # Adaptive sigma clipping only (auto-extraction derives its floor per cell).
        global_check = QCheckBox()
        global_check.setToolTip(
            "Global σ: threshold every cell against one shared noise floor "
            "(1.4826·MAD pooled over all cell pixels) at the same k, instead of "
            "each cell's own per-cell σ. Adaptive sigma clipping only."
        )
        self._rounds_table.setCellWidget(row, _ROUND_COL_GLOBAL, global_check)

        # Unit — px or µm for d_min. µm resolves via the dataset pixel size; px is
        # used directly (for datasets that lack a pixel size). Switching units does
        # NOT auto-convert the value.
        unit_combo = QComboBox()
        unit_combo.addItem("µm", userData="um")
        unit_combo.addItem("px", userData="px")
        unit_combo.setCurrentIndex(0)  # µm — the historical default
        unit_combo.setToolTip(
            "Unit for d_min. µm resolves to pixels per dataset using each dataset's "
            "pixel size; px applies the value directly — pick px for datasets that "
            "have no pixel size metadata."
        )
        self._rounds_table.setCellWidget(row, _ROUND_COL_SIZE_UNIT, unit_combo)

        # CNR split (guided) — opt-in subpopulation classification of the produced
        # feature mask; valid only on per-cell (ALC) rounds.
        cnr_check = QCheckBox()
        cnr_check.setToolTip(
            "Split the produced foci into 1–2 populations by contrast-to-noise "
            "ratio at the CNR threshold (guided). Writes <round>_low / <round>_high "
            "masks + a /classification/<round> table. ALC rounds only; single-"
            "timepoint datasets only."
        )
        cnr_check.stateChanged.connect(
            lambda _state, r=row: self._update_cnr_columns_enabled(r)
        )
        self._rounds_table.setCellWidget(row, _ROUND_COL_CNR_ON, cnr_check)

        # CNR threshold — the guided split value; enabled only when CNR split is on.
        cnr_thr_spin = QDoubleSpinBox()
        cnr_thr_spin.setRange(0.01, 1000.0)
        cnr_thr_spin.setDecimals(2)
        cnr_thr_spin.setSingleStep(0.5)
        cnr_thr_spin.setValue(5.0)
        self._rounds_table.setCellWidget(row, _ROUND_COL_CNR_THR, cnr_thr_spin)

        # GMM 2-pop — forced always-2 subpopulation classification. When checked it
        # OVERRIDES the CNR threshold: the split is placed by a data-driven GMM
        # two-group boundary regardless of the CNR thr value (which is then greyed).
        cnr_forced_check = QCheckBox()
        cnr_forced_check.setToolTip(
            "Forced always-2 subpopulation classification (GMM two-group split). "
            "OVERRIDES the CNR threshold: the boundary is placed by a data-driven "
            "GaussianMixture two-group fit instead of the guided CNR value. Enabled "
            "only when CNR split is on."
        )
        cnr_forced_check.stateChanged.connect(
            lambda _state, r=row: self._update_cnr_columns_enabled(r)
        )
        self._rounds_table.setCellWidget(row, _ROUND_COL_CNR_FORCED, cnr_forced_check)

        # Min size — a method-agnostic post-mask size filter applied to every
        # method's produced mask: connected components below this area are dropped
        # before the mask is written. 0 keeps every particle. The unit (px² / µm²)
        # is set by the adjacent Min unit combo; µm² needs a dataset pixel size.
        min_size_spin = QDoubleSpinBox()
        min_size_spin.setRange(0.0, 1_000_000.0)
        min_size_spin.setDecimals(2)
        min_size_spin.setSingleStep(1.0)
        min_size_spin.setValue(0.0)
        min_size_spin.setToolTip(
            "Minimum particle size: drop connected components below this area from "
            "the round's mask (any method). 0 = keep every particle. The unit is set "
            "by the Min unit column; µm² resolves via the dataset pixel size."
        )
        self._rounds_table.setCellWidget(row, _ROUND_COL_MIN_SIZE, min_size_spin)

        # Min unit — px² or µm² for the Min size value. µm² resolves to a per-dataset
        # pixel threshold; px² applies the value directly. Switching units does NOT
        # auto-convert the value (mirrors the d_min Unit column).
        min_unit_combo = QComboBox()
        min_unit_combo.addItem("px²", userData="px")
        min_unit_combo.addItem("µm²", userData="um2")
        min_unit_combo.setCurrentIndex(0)  # px² — no pixel size required
        min_unit_combo.setToolTip(
            "Unit for Min size. px² applies the value directly; µm² resolves to "
            "pixels per dataset using each dataset's pixel size — pick px² for "
            "datasets that have no pixel size metadata."
        )
        self._rounds_table.setCellWidget(row, _ROUND_COL_MIN_SIZE_UNIT, min_unit_combo)

        self._update_method_columns_enabled(row)
        self._refresh_column_picker()
        self._update_start_enabled()

    def _on_remove_round(self) -> None:
        row = self._rounds_table.currentRow()
        if row < 0:
            return
        self._rounds_table.removeRow(row)
        self._refresh_column_picker()
        self._update_start_enabled()

    def _on_round_up(self) -> None:
        row = self._rounds_table.currentRow()
        if row <= 0:
            return
        self._swap_rounds(row, row - 1)
        self._rounds_table.setCurrentCell(row - 1, 0)

    def _on_round_down(self) -> None:
        row = self._rounds_table.currentRow()
        if row < 0 or row >= self._rounds_table.rowCount() - 1:
            return
        self._swap_rounds(row, row + 1)
        self._rounds_table.setCurrentCell(row + 1, 0)

    def _swap_rounds(self, a: int, b: int) -> None:
        """Swap rows a and b in the rounds table.

        QTableWidget has no first-class row swap; the cleanest approach
        is to copy the round data to/from a list of dicts, swap in Python,
        then repopulate.
        """
        rounds_data = [self._read_round_row(i) for i in range(self._rounds_table.rowCount())]
        rounds_data[a], rounds_data[b] = rounds_data[b], rounds_data[a]
        # Rebuild the table.
        self._rounds_table.blockSignals(True)
        while self._rounds_table.rowCount():
            self._rounds_table.removeRow(0)
        for data in rounds_data:
            self._rounds_table.blockSignals(False)
            self._on_add_round()
            self._rounds_table.blockSignals(True)
            row = self._rounds_table.rowCount() - 1
            self._write_round_row(row, data)
        self._rounds_table.blockSignals(False)
        self._refresh_column_picker()

    def _read_round_row(self, row: int) -> dict[str, Any]:
        name_item = self._rounds_table.item(row, _ROUND_COL_NAME)
        return {
            "name": name_item.text() if name_item else "",
            "channel": self._rounds_table.cellWidget(
                row, _ROUND_COL_CHANNEL
            ).currentText(),
            "metric": self._rounds_table.cellWidget(
                row, _ROUND_COL_METRIC
            ).currentText(),
            "algorithm": self._rounds_table.cellWidget(
                row, _ROUND_COL_ALGO
            ).currentText(),
            "method": self._rounds_table.cellWidget(
                row, _ROUND_COL_METHOD
            ).currentText(),
            "gmm_max": self._rounds_table.cellWidget(
                row, _ROUND_COL_GMM_MAX
            ).value(),
            "kmeans_k": self._rounds_table.cellWidget(
                row, _ROUND_COL_KMEANS_K
            ).value(),
            "sigma": self._rounds_table.cellWidget(
                row, _ROUND_COL_SIGMA
            ).value(),
            "d_min_um": self._rounds_table.cellWidget(
                row, _ROUND_COL_DMIN
            ).value(),
            "k": self._rounds_table.cellWidget(
                row, _ROUND_COL_K
            ).value(),
            "global_sigma": self._rounds_table.cellWidget(
                row, _ROUND_COL_GLOBAL
            ).isChecked(),
            "size_unit": self._rounds_table.cellWidget(
                row, _ROUND_COL_SIZE_UNIT
            ).currentData(),
            "cnr_classify": self._rounds_table.cellWidget(
                row, _ROUND_COL_CNR_ON
            ).isChecked(),
            "cnr_threshold": self._rounds_table.cellWidget(
                row, _ROUND_COL_CNR_THR
            ).value(),
            "cnr_forced": self._rounds_table.cellWidget(
                row, _ROUND_COL_CNR_FORCED
            ).isChecked(),
            "min_particle_size": self._rounds_table.cellWidget(
                row, _ROUND_COL_MIN_SIZE
            ).value(),
            "min_particle_size_unit": self._rounds_table.cellWidget(
                row, _ROUND_COL_MIN_SIZE_UNIT
            ).currentData(),
        }

    def _write_round_row(self, row: int, data: dict[str, Any]) -> None:
        name_item = QTableWidgetItem(data["name"])
        self._rounds_table.setItem(row, _ROUND_COL_NAME, name_item)
        ch_combo = self._rounds_table.cellWidget(row, _ROUND_COL_CHANNEL)
        idx = ch_combo.findText(data["channel"])
        if idx >= 0:
            ch_combo.setCurrentIndex(idx)
        self._rounds_table.cellWidget(
            row, _ROUND_COL_METRIC
        ).setCurrentText(data["metric"])
        self._rounds_table.cellWidget(
            row, _ROUND_COL_ALGO
        ).setCurrentText(data["algorithm"])
        self._rounds_table.cellWidget(
            row, _ROUND_COL_METHOD
        ).setCurrentText(data.get("method", _METHOD_GROUPED))
        self._rounds_table.cellWidget(
            row, _ROUND_COL_GMM_MAX
        ).setValue(int(data["gmm_max"]))
        self._rounds_table.cellWidget(
            row, _ROUND_COL_KMEANS_K
        ).setValue(int(data["kmeans_k"]))
        self._rounds_table.cellWidget(
            row, _ROUND_COL_SIGMA
        ).setValue(float(data["sigma"]))
        dmin_w = self._rounds_table.cellWidget(row, _ROUND_COL_DMIN)
        # Set the per-method floor BEFORE the value so a 0 (auto-detect, valid only
        # for auto-extraction) is not clamped up to the adaptive minimum.
        dmin_w.setMinimum(self._dmin_minimum_for(data.get("method", _METHOD_GROUPED)))
        dmin_w.setValue(float(data.get("d_min_um", 0.40)))
        self._rounds_table.cellWidget(
            row, _ROUND_COL_K
        ).setValue(float(data.get("k", 1.0)))
        self._rounds_table.cellWidget(
            row, _ROUND_COL_GLOBAL
        ).setChecked(bool(data.get("global_sigma", False)))
        unit_combo = self._rounds_table.cellWidget(row, _ROUND_COL_SIZE_UNIT)
        unit_idx = unit_combo.findData(data.get("size_unit", "um"))
        unit_combo.setCurrentIndex(unit_idx if unit_idx >= 0 else 0)
        self._rounds_table.cellWidget(
            row, _ROUND_COL_CNR_ON
        ).setChecked(bool(data.get("cnr_classify", False)))
        self._rounds_table.cellWidget(
            row, _ROUND_COL_CNR_THR
        ).setValue(float(data.get("cnr_threshold", 5.0)))
        self._rounds_table.cellWidget(
            row, _ROUND_COL_CNR_FORCED
        ).setChecked(bool(data.get("cnr_forced", False)))
        self._rounds_table.cellWidget(
            row, _ROUND_COL_MIN_SIZE
        ).setValue(float(data.get("min_particle_size", 0.0)))
        min_unit_combo = self._rounds_table.cellWidget(row, _ROUND_COL_MIN_SIZE_UNIT)
        min_unit_idx = min_unit_combo.findData(data.get("min_particle_size_unit", "px"))
        min_unit_combo.setCurrentIndex(min_unit_idx if min_unit_idx >= 0 else 0)
        self._update_method_columns_enabled(row)

    def _is_adaptive_row(self, row: int) -> bool:
        method_combo = self._rounds_table.cellWidget(row, _ROUND_COL_METHOD)
        return method_combo is not None and method_combo.currentText() == _METHOD_ADAPTIVE

    def _is_auto_extract_row(self, row: int) -> bool:
        method_combo = self._rounds_table.cellWidget(row, _ROUND_COL_METHOD)
        return (
            method_combo is not None
            and method_combo.currentText() == _METHOD_AUTO_EXTRACT
        )

    def _is_alc_row(self, row: int) -> bool:
        """Per-cell Adaptive Local Clipping row (single-window OR two-pass) — the
        rows that may opt into guided CNR subpopulation classification."""
        return self._is_adaptive_row(row) or self._is_auto_extract_row(row)

    @staticmethod
    def _dmin_minimum_for(method: str) -> float:
        """The d_min spinbox floor for a method: auto-extraction allows 0 (= auto-
        detect the smallest particle); adaptive requires a positive diameter."""
        return 0.0 if method == _METHOD_AUTO_EXTRACT else 0.02

    def _on_method_changed(self, row: int) -> None:
        """User changed the row's Method. Seed the ALC presmooth default (σ = 1.0)
        when entering an ALC method from σ = 0, so the detector presmooth is the
        validated value rather than the grouped-Otsu 0 (which collapses detection),
        then re-gate the columns. Programmatic writes call
        ``_update_method_columns_enabled`` directly and skip this seeding, preserving
        a saved σ."""
        if self._is_alc_row(row):
            sigma = self._rounds_table.cellWidget(row, _ROUND_COL_SIGMA)
            if sigma is not None and sigma.value() == 0.0:
                sigma.setValue(1.0)
        self._update_method_columns_enabled(row)

    def _update_algo_columns_enabled(self, row: int) -> None:
        """Grey GMM-max / K-means-K per the Algorithm combo — but only when the
        round is a Grouped Otsu round. Per-cell (adaptive / auto-extraction) rounds
        keep both greyed (they ignore intensity grouping). Values are retained
        either way, so switching back restores prior input."""
        algo_combo = self._rounds_table.cellWidget(row, _ROUND_COL_ALGO)
        if algo_combo is None:
            return
        is_alc = self._is_alc_row(row)
        is_gmm = algo_combo.currentText() == ThresholdAlgorithm.GMM.value
        gmm_spin = self._rounds_table.cellWidget(row, _ROUND_COL_GMM_MAX)
        kmeans_spin = self._rounds_table.cellWidget(row, _ROUND_COL_KMEANS_K)
        if gmm_spin is not None:
            gmm_spin.setEnabled(not is_alc and is_gmm)
        if kmeans_spin is not None:
            kmeans_spin.setEnabled(not is_alc and not is_gmm)

    def _update_method_columns_enabled(self, row: int) -> None:
        """Gate columns by the Method combo. Both ALC methods enable d_min + Unit
        and use σ as the detector presmooth; adaptive also enables k (auto-extraction
        derives its k automatically). Both grey Algorithm + grouping; Grouped Otsu
        inverts. CNR-split controls are enabled only on per-cell (ALC) rows. Values
        are retained when greyed so toggling Method back restores prior input."""
        adaptive = self._is_adaptive_row(row)
        auto_extract = self._is_auto_extract_row(row)
        is_alc = adaptive or auto_extract
        algo_combo = self._rounds_table.cellWidget(row, _ROUND_COL_ALGO)
        if algo_combo is not None:
            algo_combo.setEnabled(not is_alc)
        # d_min is the smallest-particle knob for BOTH ALC methods; its floor varies
        # by method (auto-extraction allows 0 = auto-detect).
        dmin = self._rounds_table.cellWidget(row, _ROUND_COL_DMIN)
        if dmin is not None:
            method = (
                _METHOD_AUTO_EXTRACT if auto_extract
                else (_METHOD_ADAPTIVE if adaptive else _METHOD_GROUPED)
            )
            new_min = self._dmin_minimum_for(method)
            if dmin.minimum() != new_min:
                dmin.setMinimum(new_min)
            dmin.setEnabled(is_alc)
        # k is the adaptive sigma multiplier only (auto-extraction's k is automatic).
        k = self._rounds_table.cellWidget(row, _ROUND_COL_K)
        if k is not None:
            k.setEnabled(adaptive)
        # global σ is the k companion — adaptive only (auto-extraction derives its
        # noise floor per cell). Clear a stale check when leaving adaptive so a
        # greyed toggle can't silently ride along.
        global_check = self._rounds_table.cellWidget(row, _ROUND_COL_GLOBAL)
        if global_check is not None:
            if not adaptive and global_check.isChecked():
                global_check.setChecked(False)
            global_check.setEnabled(adaptive)
        # The Unit applies to d_min on any per-cell ALC row; greyed for Grouped Otsu.
        unit_combo = self._rounds_table.cellWidget(row, _ROUND_COL_SIZE_UNIT)
        if unit_combo is not None:
            unit_combo.setEnabled(is_alc)
        # σ is live everywhere: pre-threshold smoothing for Grouped Otsu, and the
        # detector presmooth (Gaussian blur) for both ALC methods.
        sigma = self._rounds_table.cellWidget(row, _ROUND_COL_SIGMA)
        if sigma is not None:
            sigma.setEnabled(True)
        # GMM/K-means enablement depends on both Method and Algorithm.
        self._update_algo_columns_enabled(row)
        # CNR-split controls are valid only on per-cell ALC rows.
        self._update_cnr_columns_enabled(row)

    def _update_cnr_columns_enabled(self, row: int) -> None:
        """The CNR-split checkbox is enabled only on per-cell (ALC) rows; the CNR
        threshold and the GMM 2-pop override are enabled only when the checkbox is
        on. The GMM 2-pop override, when checked, greys the CNR threshold (it is
        overridden by the forced two-group split). On a non-ALC row the split
        checkbox is unchecked and disabled so a meaningless split can't be set."""
        is_alc = self._is_alc_row(row)
        check = self._rounds_table.cellWidget(row, _ROUND_COL_CNR_ON)
        thr = self._rounds_table.cellWidget(row, _ROUND_COL_CNR_THR)
        forced = self._rounds_table.cellWidget(row, _ROUND_COL_CNR_FORCED)
        if check is not None:
            if not is_alc and check.isChecked():
                check.setChecked(False)  # clear a stale split on a non-ALC row
            check.setEnabled(is_alc)
        checked = check is not None and check.isChecked()
        # The GMM 2-pop override is meaningful only when the CNR split is on; clear a
        # stale check when the split is off so a greyed override can't ride along.
        if forced is not None:
            if not (is_alc and checked) and forced.isChecked():
                forced.setChecked(False)
            forced.setEnabled(is_alc and checked)
        is_forced = forced is not None and forced.isChecked()
        # The threshold drives guided mode only; forced mode overrides it, so grey it.
        if thr is not None:
            thr.setEnabled(is_alc and checked and not is_forced)

    def _on_round_item_changed(self, item: QTableWidgetItem) -> None:
        """Live-validate the Name column against the round-name regex."""
        if item.column() != _ROUND_COL_NAME:
            return
        text = item.text()
        if _ROUND_NAME_RE.match(text):
            item.setBackground(QColor(0, 0, 0, 0))  # reset
            item.setToolTip("")
        else:
            item.setBackground(QColor("#5b2a2a"))  # dark red
            item.setToolTip(
                f"Round name must match {_ROUND_NAME_RE.pattern} "
                "(letters/digits/_/-, max 40 chars, non-numeric start)"
            )
        self._refresh_column_picker()

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
        round_names = self._round_names_from_table()
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
        round_names = self._round_names_from_table()
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

    def _round_names_from_table(self) -> list[str]:
        names: list[str] = []
        for row in range(self._rounds_table.rowCount()):
            item = self._rounds_table.item(row, _ROUND_COL_NAME)
            if item is not None and _ROUND_NAME_RE.match(item.text()):
                names.append(item.text())
        return names

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
            has_work = self._rounds_table.rowCount() > 0
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
        if not use_existing_masks and self._rounds_table.rowCount() == 0:
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
                rounds = self._rounds_from_table(intersected)
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
                        "Unit / Min unit to px, or use auto-detect (Smallest = 0)."
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

    def _rounds_from_table(
        self, intersected: list[str]
    ) -> list[ThresholdingRound]:
        """Build a list of :class:`ThresholdingRound` from the table rows.

        Raises ``ValueError`` (caught upstream) on any per-row validation
        failure; the message is prefixed with the row number so the user
        can find it.
        """
        rounds: list[ThresholdingRound] = []
        for row in range(self._rounds_table.rowCount()):
            data = self._read_round_row(row)
            if data["channel"] not in intersected:
                raise ValueError(
                    f"Round {row + 1} ({data['name']!r}) references channel "
                    f"{data['channel']!r}, which is not in the intersection "
                    f"{intersected}."
                )
            try:
                algo = ThresholdAlgorithm(data["algorithm"])
            except ValueError as e:
                raise ValueError(f"Round {row + 1}: {e}") from e
            # Each method carries exactly one settings sentinel; only the selected
            # method's sentinel is built, so a row reconfigured from another method
            # does not trip the mutual-exclusion check.
            method = data.get("method")
            size_unit = data.get("size_unit", "um")
            sigma = float(data["sigma"])  # σ is the detector presmooth for ALC rounds
            d_min = float(data["d_min_um"])  # the merged size column serves both ALC methods
            adaptive = None
            auto_extract = None
            if method == _METHOD_ADAPTIVE:
                adaptive = AdaptiveClipSettings(
                    d_min_um=d_min,
                    k=float(data["k"]),
                    presmooth_sigma_px=sigma,
                    d_min_unit=size_unit,
                    global_sigma=bool(data.get("global_sigma", False)),
                )
            elif method == _METHOD_AUTO_EXTRACT:
                # 0 (the auto-extraction floor) means auto-detect the smallest → None.
                auto_extract = AutoExtractSettings(
                    smallest_particle_um=(d_min if d_min > 0 else None),
                    presmooth_sigma_px=sigma,
                    smallest_particle_unit=size_unit,
                )
            # Guided CNR split is opt-in and valid only on per-cell ALC rounds; the
            # dialog disables the checkbox elsewhere, but guard the build too.
            cnr_classify = None
            if data.get("cnr_classify") and method in (
                _METHOD_ADAPTIVE,
                _METHOD_AUTO_EXTRACT,
            ):
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
                        adaptive_clip=adaptive,
                        auto_extract=auto_extract,
                        cnr_classify=cnr_classify,
                        min_particle_size=float(data.get("min_particle_size", 0.0)),
                        min_particle_size_unit=data.get("min_particle_size_unit", "px"),
                    )
                )
            except ValueError as e:
                raise ValueError(f"Round {row + 1}: {e}") from e
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
