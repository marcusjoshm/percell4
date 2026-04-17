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

from qtpy.QtCore import QSettings
from qtpy.QtGui import QColor
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
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from percell4.domain.measure.metrics import BUILTIN_METRICS
from percell4.store import DatasetStore
from percell4.workflows.channels import ChannelSource, intersect_channels
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    GmmCriterion,
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

_CELLPOSE_MODELS = ("cpsam", "cyto3", "cyto2", "cyto", "nuclei")

# Always-on identity columns prepended to the CSV column picker.
_ALWAYS_ON_COLUMNS = ("dataset", "cell_id", "label")

# Core per-cell columns the user may opt into.
_CORE_OPTIONAL_COLUMNS = (
    "centroid_y",
    "centroid_x",
    "bbox_y",
    "bbox_x",
    "bbox_h",
    "bbox_w",
    "area",
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
_ROUND_COL_ALGO = 3
_ROUND_COL_GMM_MAX = 4
_ROUND_COL_KMEANS_K = 5
_ROUND_COL_SIGMA = 6
_ROUND_COL_COUNT = 7
_ROUND_COL_HEADERS = (
    "Name",
    "Channel",
    "Metric",
    "Algorithm",
    "GMM max",
    "K-means K",
    "σ",
)


# ── Internal per-dataset record ──────────────────────────────────────────


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


# ── Dialog ──────────────────────────────────────────────────────────────


class WorkflowConfigDialog(QDialog):
    """Modal configuration dialog for the single-cell thresholding workflow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Single-cell thresholding analysis workflow")
        self.setModal(True)
        self.resize(960, 720)

        # State
        self._pending_datasets: list[_PendingDataset] = []
        self._selected_csv_channels: set[str] = set()
        self._selected_csv_metrics: set[str] = set()
        self._workflow_config: WorkflowConfig | None = None

        self._build_ui()
        self._refresh_dataset_tree()
        self._refresh_column_picker()
        self._update_start_enabled()

    # ── UI construction ───────────────────────────────────────

    def _build_ui(self) -> None:
        from qtpy.QtWidgets import QScrollArea

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Wrap the content in a scroll area so the dialog is usable on
        # smaller screens (the full layout can be quite tall with many
        # rounds / columns).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        layout.addWidget(self._build_datasets_group(), stretch=3)
        layout.addWidget(self._build_cellpose_group())
        layout.addWidget(self._build_rounds_group(), stretch=2)
        layout.addWidget(self._build_columns_group())
        layout.addWidget(self._build_output_group())
        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

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

        btn_add_tiff = QPushButton("Add .tiff source...")
        btn_add_tiff.clicked.connect(self._on_add_tiff_source)
        btn_row.addWidget(btn_add_tiff)

        btn_add_tiff_folder = QPushButton("Add .tiff folder...")
        btn_add_tiff_folder.clicked.connect(self._on_add_tiff_folder)
        btn_row.addWidget(btn_add_tiff_folder)

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

        self._cp_model = QComboBox()
        self._cp_model.addItems(_CELLPOSE_MODELS)
        self._cp_model.setCurrentText("cpsam")
        form.addRow("Model:", self._cp_model)

        self._cp_diameter = QDoubleSpinBox()
        self._cp_diameter.setRange(0.0, 500.0)
        self._cp_diameter.setSingleStep(1.0)
        self._cp_diameter.setValue(30.0)
        self._cp_diameter.setToolTip("0 = auto-detect")
        form.addRow("Diameter (px):", self._cp_diameter)

        self._cp_gpu = QCheckBox("Use GPU")
        self._cp_gpu.setChecked(True)
        form.addRow("", self._cp_gpu)

        self._cp_flow = QDoubleSpinBox()
        self._cp_flow.setRange(0.0, 10.0)
        self._cp_flow.setSingleStep(0.1)
        self._cp_flow.setValue(0.4)
        form.addRow("Flow threshold:", self._cp_flow)

        self._cp_cellprob = QDoubleSpinBox()
        self._cp_cellprob.setRange(-10.0, 10.0)
        self._cp_cellprob.setSingleStep(0.1)
        self._cp_cellprob.setValue(0.0)
        form.addRow("Cellprob threshold:", self._cp_cellprob)

        self._cp_min_size = QSpinBox()
        self._cp_min_size.setRange(0, 100000)
        self._cp_min_size.setValue(15)
        form.addRow("Min cell size (px):", self._cp_min_size)

        note = QLabel(
            "Edge-touching cells are always removed (workflow invariant)."
        )
        note.setStyleSheet("color: #888; font-style: italic;")
        form.addRow("", note)

        return box

    def _build_rounds_group(self) -> QGroupBox:
        box = QGroupBox("Thresholding Rounds (ordered)")
        outer = QVBoxLayout(box)

        self._rounds_table = QTableWidget(0, _ROUND_COL_COUNT)
        self._rounds_table.setHorizontalHeaderLabels(_ROUND_COL_HEADERS)
        self._rounds_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self._rounds_table.horizontalHeader().setStretchLastSection(False)
        self._rounds_table.verticalHeader().setVisible(False)
        self._rounds_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._rounds_table.setSelectionMode(QTableWidget.SingleSelection)
        self._rounds_table.setMinimumHeight(100)
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

    def _on_add_tiff_source(self) -> None:
        self._add_tiff_via_compress_dialog()

    def _on_add_tiff_folder(self) -> None:
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

        channel_names = sorted(cfg.selected_channels)
        if not channel_names:
            self._dataset_status.setText(
                "No channels selected in the compress dialog — nothing to add."
            )
            return

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
                compress_plan={
                    "source_dir": str(ds.source_dir) if ds.source_dir else "",
                    "files": [str(f.path) for f in ds.files],
                    "output_path": str(ds.output_path),
                    "z_project_method": cfg.z_project_method,
                    "selected_channels": sorted(cfg.selected_channels),
                },
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
        metric_combo.setCurrentText("mean_intensity")
        self._rounds_table.setCellWidget(row, _ROUND_COL_METRIC, metric_combo)

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
        gmm_spin.setValue(4)
        self._rounds_table.setCellWidget(row, _ROUND_COL_GMM_MAX, gmm_spin)

        # KMeans k
        kmeans_spin = QSpinBox()
        kmeans_spin.setRange(2, 20)
        kmeans_spin.setValue(3)
        self._rounds_table.setCellWidget(row, _ROUND_COL_KMEANS_K, kmeans_spin)

        # Gaussian sigma
        sigma_spin = QDoubleSpinBox()
        sigma_spin.setRange(0.0, 20.0)
        sigma_spin.setSingleStep(0.1)
        sigma_spin.setValue(1.0)
        self._rounds_table.setCellWidget(row, _ROUND_COL_SIGMA, sigma_spin)

        self._update_algo_columns_enabled(row)
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
            "gmm_max": self._rounds_table.cellWidget(
                row, _ROUND_COL_GMM_MAX
            ).value(),
            "kmeans_k": self._rounds_table.cellWidget(
                row, _ROUND_COL_KMEANS_K
            ).value(),
            "sigma": self._rounds_table.cellWidget(
                row, _ROUND_COL_SIGMA
            ).value(),
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
            row, _ROUND_COL_GMM_MAX
        ).setValue(int(data["gmm_max"]))
        self._rounds_table.cellWidget(
            row, _ROUND_COL_KMEANS_K
        ).setValue(int(data["kmeans_k"]))
        self._rounds_table.cellWidget(
            row, _ROUND_COL_SIGMA
        ).setValue(float(data["sigma"]))
        self._update_algo_columns_enabled(row)

    def _update_algo_columns_enabled(self, row: int) -> None:
        algo_combo = self._rounds_table.cellWidget(row, _ROUND_COL_ALGO)
        if algo_combo is None:
            return
        is_gmm = algo_combo.currentText() == ThresholdAlgorithm.GMM.value
        gmm_spin = self._rounds_table.cellWidget(row, _ROUND_COL_GMM_MAX)
        kmeans_spin = self._rounds_table.cellWidget(row, _ROUND_COL_KMEANS_K)
        if gmm_spin is not None:
            gmm_spin.setEnabled(is_gmm)
        if kmeans_spin is not None:
            kmeans_spin.setEnabled(not is_gmm)

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

        # Prune selected channels/metrics to those still valid.
        valid_channels = set(intersected)
        self._selected_csv_channels &= valid_channels

        self._update_csv_summary()

    def _update_csv_summary(self) -> None:
        """Update the summary label under the Configure CSV Export button."""
        n_ch = len(self._selected_csv_channels)
        n_met = len(self._selected_csv_metrics)
        round_names = self._round_names_from_table()
        if n_ch == 0 and n_met == 0:
            self._csv_summary_label.setText(
                "No channels or metrics selected yet. "
                "Click 'Configure CSV Export...' to choose."
            )
        else:
            parts = [f"{n_ch} channel(s)", f"{n_met} metric(s)"]
            if round_names:
                parts.append(f"{len(round_names)} round(s)")
            col_count = self._estimate_csv_column_count()
            self._csv_summary_label.setText(
                f"CSV export: {', '.join(parts)} → ~{col_count} columns. "
                f"Core columns (label, centroid, area) always included."
            )

    def _estimate_csv_column_count(self) -> int:
        """Rough count of the CSV columns that will be produced."""
        n_ch = len(self._selected_csv_channels)
        n_met = len(self._selected_csv_metrics)
        round_names = self._round_names_from_table()
        n_rounds = len(round_names)
        # identity (3) + core (7) + ch×met + group_per_round + ch×met×round×2 (in/out)
        return (
            len(_ALWAYS_ON_COLUMNS)
            + len(_CORE_OPTIONAL_COLUMNS)
            + n_ch * n_met
            + n_rounds
            + n_ch * n_met * n_rounds * 2
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
        dialog.resize(450, 640)
        layout = QVBoxLayout(dialog)

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

        # ── Note ──
        note = QLabel(
            "The exported CSVs will contain every combination of the "
            "selected channels × metrics, plus core columns (label, "
            "centroid, area), group assignments per round, and per-round "
            "inside/outside columns. The full measurements.parquet "
            "always contains everything regardless of this selection."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        layout.addWidget(note)

        # ── Buttons ──
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec_() != QDialog.Accepted:
            return

        self._selected_csv_channels = {
            ch for ch, cb in ch_cbs.items() if cb.isChecked()
        }
        self._selected_csv_metrics = {
            name for name, cb in met_cbs.items() if cb.isChecked()
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
        has_rounds = self._rounds_table.rowCount() > 0
        self._start_btn.setEnabled(has_datasets and has_rounds)

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

        if self._rounds_table.rowCount() == 0:
            self._warn("Add at least one thresholding round.")
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

        # Build rounds and validate each round's channel is in the intersection.
        try:
            rounds = self._rounds_from_table(intersected)
        except ValueError as e:
            self._warn(str(e))
            return None

        # Cellpose settings.
        try:
            cellpose = CellposeSettings(
                model=self._cp_model.currentText(),
                diameter=float(self._cp_diameter.value()),
                gpu=self._cp_gpu.isChecked(),
                flow_threshold=float(self._cp_flow.value()),
                cellprob_threshold=float(self._cp_cellprob.value()),
                min_size=int(self._cp_min_size.value()),
            )
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

        selected_cols = self._build_selected_csv_columns(intersected, rounds)

        seg_channel = self._cp_seg_channel.currentText()
        if not seg_channel or seg_channel.startswith("("):
            self._warn("Choose a segmentation channel in the Cellpose settings.")
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
            )
        except ValueError as e:
            self._warn(f"Configuration invalid: {e}")
            return None

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
                    )
                )
            except ValueError as e:
                raise ValueError(f"Round {row + 1}: {e}") from e
        return rounds

    def _build_selected_csv_columns(
        self,
        intersected: list[str],
        rounds: list[ThresholdingRound],
    ) -> list[str]:
        """Compute the full list of CSV columns from the user's channel + metric selection.

        Returns the cross-product of selected channels × selected metrics,
        plus core columns, group columns, and per-round in/out columns.
        Identity columns (dataset, cell_id, label) are always prepended by
        the export step regardless of what's in this list.
        """
        cols: list[str] = list(_CORE_OPTIONAL_COLUMNS)
        channels = [ch for ch in intersected if ch in self._selected_csv_channels]
        metrics = sorted(self._selected_csv_metrics)
        round_names = [r.name for r in rounds]

        # {channel}_{metric} whole-cell columns
        for ch in channels:
            for m in metrics:
                cols.append(f"{ch}_{m}")

        # group_{round_name} columns
        for rn in round_names:
            cols.append(f"group_{rn}")

        # {channel}_{metric}_in_{round} / _out_{round} columns
        for ch in channels:
            for m in metrics:
                for rn in round_names:
                    cols.append(f"{ch}_{m}_in_{rn}")
                    cols.append(f"{ch}_{m}_out_{rn}")

        return cols

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
