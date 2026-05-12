"""Batch TCSPC append dialog.

Adds ``.bin`` decay layers to many existing ``.h5`` datasets in one pass.
Wires user input — selected datasets, a parent root of group folders,
manual pairing, a long-format calibration CSV, and one global tile /
orientation config — into :func:`batch_add_decay`.

The dialog is the *only* layer that knows about Qt, ``Session``, and
``ProjectIndex``. The orchestrator and CSV parser it drives are pure
Python and tested independently (see ``tests/test_application/`` and
``tests/test_io/``).

The Run flow uses the codebase's established ``QProgressDialog`` GUI-thread
loop pattern (see ``main_window._run_batch_compress``) rather than a
``QThread`` worker. Cancellation honors the progress dialog's Cancel button
between datasets; within a single dataset's append, the UI is intentionally
frozen — the trade-off matches the compress flow.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import h5py
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from percell4.application.use_cases.batch_add_decay import (
    BatchAppendItem,
    BatchAppendReport,
    BatchItemResult,
    batch_add_decay,
    validate_batch_inputs,
    validate_calibration_csv_against_selection,
)
from percell4.domain.errors import CalibrationCSVError
from percell4.domain.io.calibration_csv import (
    BatchCalibration,
    parse_calibration_csv,
)
from percell4.domain.io.models import (
    BaseStemRule,
    CrossFormatRule,
    FlimConfig,
    TileConfig,
    TokenConfig,
)
from percell4.gui._dialog_utils import cap_to_screen, wrap_in_scroll
from percell4.store import DatasetStore

_NO_PAIR_LABEL = "— select —"
_SKIP_LABEL = "— skip —"
_AUTO_PAIR_THRESHOLD = 0.6


class BatchTCSPCDialog(QDialog):
    """Multi-dataset TCSPC append dialog."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        session: Any = None,
        show_status: Callable[[str], None] = lambda _msg: None,
        get_project_index: Callable[[], Any] = lambda: None,
        orchestrator: Callable[..., BatchAppendReport] = batch_add_decay,
        validator: Callable[..., Any] = validate_batch_inputs,
        csv_parser: Callable[[Path], BatchCalibration] = parse_calibration_csv,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Batch TCSPC Append")
        self.setMinimumWidth(820)
        self.resize(900, 760)
        cap_to_screen(self)

        # Injected callables — see module docstring for rationale.
        self._session = session
        self._show_status = show_status
        self._get_project_index = get_project_index
        self._orchestrator = orchestrator
        self._validator = validator
        self._csv_parser = csv_parser

        # ── Pure-Python state ──
        self._datasets: list[Path] = []  # selected .h5 paths
        self._source_root: Path | None = None
        self._groups: list[Path] = []  # immediate subfolders of source_root
        # h5_path -> group folder Path or None
        self._pairings: dict[Path, Path | None] = {}
        self._calibration: BatchCalibration | None = None
        self._validated: bool = False
        self._suppress_pair_signal: bool = False

        # ── Widgets ──
        self._dataset_table: QTableWidget | None = None
        self._source_root_edit: QLineEdit | None = None
        self._groups_label: QLabel | None = None
        self._pairing_table: QTableWidget | None = None
        self._csv_status_label: QLabel | None = None
        self._validate_log: QPlainTextEdit | None = None
        self._run_btn: QPushButton | None = None
        self._validate_btn: QPushButton | None = None

        # Stitching widgets
        self._grid_rows_spin: QSpinBox | None = None
        self._grid_cols_spin: QSpinBox | None = None
        self._grid_type_combo: QComboBox | None = None
        self._order_combo: QComboBox | None = None
        self._rotate_combo: QComboBox | None = None
        self._flip_combo: QComboBox | None = None
        self._conflict_skip_radio: QRadioButton | None = None
        self._conflict_overwrite_radio: QRadioButton | None = None

        # Form vs summary swap state
        self._content_widget: QWidget | None = None
        self._summary_widget: QWidget | None = None

        self._build_ui()
        self._maybe_load_from_project()

    # ────────────────────────────────────────────────────────────
    # UI construction
    # ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        content = QWidget()
        self._content_widget = content
        body = QVBoxLayout(content)
        body.setSpacing(10)

        body.addWidget(self._build_section_datasets())
        body.addWidget(self._build_section_source_root())
        body.addWidget(self._build_section_pairing())
        body.addWidget(self._build_section_csv())
        body.addWidget(self._build_section_stitching())
        body.addWidget(self._build_section_conflict_policy())
        body.addWidget(self._build_section_validation())
        body.addStretch()

        outer.addWidget(wrap_in_scroll(content))

        # Pinned button row outside the scroll area.
        btn_row = QHBoxLayout()
        self._validate_btn = QPushButton("Validate")
        self._validate_btn.clicked.connect(self._on_validate)
        self._run_btn = QPushButton("Run")
        self._run_btn.clicked.connect(self._on_run)
        self._run_btn.setEnabled(False)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self._validate_btn)
        btn_row.addWidget(self._run_btn)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

    def _build_section_datasets(self) -> QGroupBox:
        box = QGroupBox("1. Datasets")
        layout = QVBoxLayout(box)

        self._dataset_table = QTableWidget(0, 4)
        self._dataset_table.setHorizontalHeaderLabels(
            ["Include", "Filename", "Channels", "/decay status"]
        )
        self._dataset_table.horizontalHeader().setStretchLastSection(True)
        self._dataset_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self._dataset_table.verticalHeader().setVisible(False)
        # Default to ~6 visible rows so typical 3–12-dish batches fit
        # without scrolling per-row inside the table.
        self._dataset_table.setMinimumHeight(200)
        layout.addWidget(self._dataset_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add datasets…")
        add_btn.clicked.connect(self._on_add_datasets)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._on_remove_datasets)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return box

    def _build_section_source_root(self) -> QGroupBox:
        box = QGroupBox("2. .bin source root")
        layout = QFormLayout(box)

        row = QHBoxLayout()
        self._source_root_edit = QLineEdit()
        self._source_root_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse_root)
        row.addWidget(self._source_root_edit, 1)
        row.addWidget(browse_btn)
        layout.addRow("Root folder:", row)

        self._groups_label = QLabel("Discovered groups: 0")
        layout.addRow("", self._groups_label)

        return box

    def _build_section_pairing(self) -> QGroupBox:
        box = QGroupBox("3. Pairing (dataset ↔ group folder)")
        layout = QVBoxLayout(box)

        self._pairing_table = QTableWidget(0, 3)
        self._pairing_table.setHorizontalHeaderLabels(
            ["Dataset", "Group folder", "Calibration"]
        )
        self._pairing_table.horizontalHeader().setStretchLastSection(True)
        self._pairing_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self._pairing_table.verticalHeader().setVisible(False)
        self._pairing_table.setMinimumHeight(200)
        layout.addWidget(self._pairing_table)

        auto_btn = QPushButton("Auto-pair by name")
        auto_btn.clicked.connect(self._on_auto_pair)
        row = QHBoxLayout()
        row.addWidget(auto_btn)
        row.addStretch()
        layout.addLayout(row)

        return box

    def _build_section_csv(self) -> QGroupBox:
        box = QGroupBox("4. Calibration CSV (long format)")
        layout = QHBoxLayout(box)
        browse_btn = QPushButton("Choose CSV…")
        browse_btn.clicked.connect(self._on_load_csv)
        self._csv_status_label = QLabel("No CSV loaded.")
        layout.addWidget(browse_btn)
        layout.addWidget(self._csv_status_label, 1)
        return box

    def _build_section_stitching(self) -> QGroupBox:
        box = QGroupBox("5. Stitching & orientation (applied to every dataset)")
        layout = QFormLayout(box)

        grid_row = QHBoxLayout()
        self._grid_rows_spin = QSpinBox()
        self._grid_rows_spin.setRange(1, 32)
        self._grid_rows_spin.setValue(4)
        self._grid_cols_spin = QSpinBox()
        self._grid_cols_spin.setRange(1, 32)
        self._grid_cols_spin.setValue(4)
        grid_row.addWidget(QLabel("rows"))
        grid_row.addWidget(self._grid_rows_spin)
        grid_row.addSpacing(12)
        grid_row.addWidget(QLabel("cols"))
        grid_row.addWidget(self._grid_cols_spin)
        grid_row.addStretch()
        layout.addRow("Grid:", grid_row)

        # Mirrors the single-dataset TCSPC tab in add_layer_dialog.py
        # (and compress_dialog.py): same item lists, same item-data carrier
        # pattern, same labels. Drift between batch and single-shot
        # dialogs is a recurring footgun — see PR #9 review.
        self._grid_type_combo = QComboBox()
        self._grid_type_combo.addItems(
            ["row_by_row", "column_by_column", "snake_by_row", "snake_by_column"]
        )
        layout.addRow("Pattern:", self._grid_type_combo)

        self._order_combo = QComboBox()
        self._order_combo.addItems(
            [
                "right_down", "right_up", "left_down", "left_up",
                "top_left", "top_right", "bottom_left", "bottom_right",
            ]
        )
        layout.addRow("Start:", self._order_combo)

        # Rotation/flip combos carry their semantic value via itemData
        # rather than by list-index. Same convention as
        # add_layer_dialog.py:_tcspc_rotation_combo / _tcspc_flip_combo.
        self._rotate_combo = QComboBox()
        self._rotate_combo.addItem("None", 0)
        self._rotate_combo.addItem("90° CCW", 1)
        self._rotate_combo.addItem("180°", 2)
        self._rotate_combo.addItem("90° CW", 3)
        layout.addRow("Rotate stitched array:", self._rotate_combo)

        self._flip_combo = QComboBox()
        # ``-1`` = no flip; ``0`` = vertical (top↔bottom, np.flipud);
        # ``1`` = horizontal (left↔right, np.fliplr).
        self._flip_combo.addItem("None", -1)
        self._flip_combo.addItem("Vertical (top ↔ bottom)", 0)
        self._flip_combo.addItem("Horizontal (left ↔ right)", 1)
        layout.addRow("Flip:", self._flip_combo)

        # Settings changes invalidate Run.
        for w in (
            self._grid_rows_spin,
            self._grid_cols_spin,
        ):
            w.valueChanged.connect(self._invalidate_run)
        for w in (
            self._grid_type_combo,
            self._order_combo,
            self._rotate_combo,
            self._flip_combo,
        ):
            w.currentIndexChanged.connect(self._invalidate_run)

        return box

    def _build_section_conflict_policy(self) -> QGroupBox:
        box = QGroupBox("6. If a /decay layer already exists")
        layout = QHBoxLayout(box)
        self._conflict_skip_radio = QRadioButton("Skip existing layers")
        self._conflict_skip_radio.setChecked(True)
        self._conflict_overwrite_radio = QRadioButton("Overwrite all")
        group = QButtonGroup(self)
        group.addButton(self._conflict_skip_radio)
        group.addButton(self._conflict_overwrite_radio)
        self._conflict_skip_radio.toggled.connect(self._invalidate_run)
        layout.addWidget(self._conflict_skip_radio)
        layout.addWidget(self._conflict_overwrite_radio)
        layout.addStretch()
        return box

    def _build_section_validation(self) -> QGroupBox:
        box = QGroupBox("7. Pre-flight report")
        layout = QVBoxLayout(box)
        self._validate_log = QPlainTextEdit()
        self._validate_log.setReadOnly(True)
        self._validate_log.setPlaceholderText(
            "Click Validate to check pairings, calibration coverage, and decay collisions."
        )
        self._validate_log.setFixedHeight(160)
        layout.addWidget(self._validate_log)
        return box

    # ────────────────────────────────────────────────────────────
    # Slots — section 1 (datasets)
    # ────────────────────────────────────────────────────────────

    def _maybe_load_from_project(self) -> None:
        """If a ProjectIndex is available, prefill the dataset table from it."""
        index = self._get_project_index()
        if index is None:
            return
        try:
            df = index.load()
            paths = [Path(p) for p in df["path"].tolist()]
        except Exception:  # noqa: BLE001 — best-effort prefill
            return
        for path in paths:
            self._add_dataset_row(path, checked=True)
        self._invalidate_run()

    def _on_add_datasets(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add datasets", "", "HDF5 files (*.h5);;All files (*)"
        )
        for p in paths:
            path = Path(p)
            if any(path == d for d in self._datasets):
                continue
            self._add_dataset_row(path, checked=True)
        self._refresh_pairing_table()
        self._invalidate_run()

    def _on_remove_datasets(self) -> None:
        assert self._dataset_table is not None
        rows = sorted(
            {idx.row() for idx in self._dataset_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self._dataset_table.removeRow(row)
            del self._datasets[row]
        self._refresh_pairing_table()
        self._invalidate_run()

    def _add_dataset_row(self, path: Path, *, checked: bool) -> None:
        assert self._dataset_table is not None
        try:
            channel_names, existing_decay = self._read_store_summary(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "Couldn't read dataset",
                f"{path.name}: {exc}",
            )
            return

        self._datasets.append(path)
        row = self._dataset_table.rowCount()
        self._dataset_table.insertRow(row)

        check = QCheckBox()
        check.setChecked(checked)
        check.stateChanged.connect(self._invalidate_run)
        # Wrap checkbox in a tiny widget so it centers in the cell.
        wrapper = QWidget()
        wlay = QHBoxLayout(wrapper)
        wlay.addWidget(check)
        wlay.setAlignment(Qt.AlignCenter)
        wlay.setContentsMargins(0, 0, 0, 0)
        self._dataset_table.setCellWidget(row, 0, wrapper)
        wrapper.setProperty("checkbox", check)

        self._dataset_table.setItem(row, 1, QTableWidgetItem(path.name))
        self._dataset_table.setItem(row, 2, QTableWidgetItem(", ".join(channel_names)))
        decay_text = (
            "all channels present"
            if existing_decay and set(channel_names).issubset(existing_decay)
            else (
                f"{len(existing_decay)} channel(s) present"
                if existing_decay
                else "none"
            )
        )
        self._dataset_table.setItem(row, 3, QTableWidgetItem(decay_text))

    # ────────────────────────────────────────────────────────────
    # Slots — section 2 (source root + group discovery)
    # ────────────────────────────────────────────────────────────

    def _on_browse_root(self) -> None:
        assert self._source_root_edit is not None
        chosen = QFileDialog.getExistingDirectory(self, "Pick .bin source root")
        if not chosen:
            return
        self._source_root = Path(chosen)
        self._source_root_edit.setText(chosen)
        self._refresh_groups()
        self._refresh_pairing_table()
        self._invalidate_run()

    def _refresh_groups(self) -> None:
        assert self._groups_label is not None
        if self._source_root is None:
            self._groups = []
            self._groups_label.setText("Discovered groups: 0")
            return
        try:
            subs = sorted(p for p in self._source_root.iterdir() if p.is_dir())
        except OSError as exc:
            QMessageBox.warning(
                self, "Couldn't read folder", f"{self._source_root}: {exc}"
            )
            self._groups = []
            self._groups_label.setText("Discovered groups: 0")
            return
        self._groups = subs
        self._groups_label.setText(f"Discovered groups: {len(subs)}")

    # ────────────────────────────────────────────────────────────
    # Slots — section 3 (pairing)
    # ────────────────────────────────────────────────────────────

    def _refresh_pairing_table(self) -> None:
        """Rebuild the pairing table from currently checked datasets."""
        assert self._pairing_table is not None
        self._suppress_pair_signal = True
        try:
            self._pairing_table.setRowCount(0)
            checked = self._checked_datasets()
            for path in checked:
                row = self._pairing_table.rowCount()
                self._pairing_table.insertRow(row)
                self._pairing_table.setItem(row, 0, QTableWidgetItem(path.stem))
                combo = QComboBox()
                combo.addItems(
                    [_NO_PAIR_LABEL, _SKIP_LABEL, *(g.name for g in self._groups)]
                )
                # Reflect any prior pairing for this path.
                prior = self._pairings.get(path)
                if prior is not None:
                    idx = combo.findText(prior.name)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                combo.currentIndexChanged.connect(
                    lambda _i, p=path, c=combo: self._on_pair_changed(p, c)
                )
                self._pairing_table.setCellWidget(row, 1, combo)
                cal_text = self._calibration_summary_text(path.stem)
                self._pairing_table.setItem(row, 2, QTableWidgetItem(cal_text))
        finally:
            self._suppress_pair_signal = False

    def _on_pair_changed(self, dataset_path: Path, combo: QComboBox) -> None:
        if self._suppress_pair_signal:
            return
        text = combo.currentText()
        if text in (_NO_PAIR_LABEL, _SKIP_LABEL):
            self._pairings[dataset_path] = None
        else:
            chosen = next((g for g in self._groups if g.name == text), None)
            self._pairings[dataset_path] = chosen
            # Uniqueness invariant: any other dataset that holds this group
            # gets reset to "— select —".
            self._enforce_pairing_uniqueness(dataset_path, chosen)
        self._invalidate_run()

    def _enforce_pairing_uniqueness(
        self, owner: Path, group: Path | None
    ) -> None:
        if group is None:
            return
        assert self._pairing_table is not None
        self._suppress_pair_signal = True
        try:
            for row in range(self._pairing_table.rowCount()):
                name_item = self._pairing_table.item(row, 0)
                if name_item is None:
                    continue
                # Resolve the dataset for this row by stem matching.
                row_path = next(
                    (d for d in self._datasets if d.stem == name_item.text()),
                    None,
                )
                if row_path is None or row_path == owner:
                    continue
                if self._pairings.get(row_path) == group:
                    self._pairings[row_path] = None
                    combo = self._pairing_table.cellWidget(row, 1)
                    if isinstance(combo, QComboBox):
                        combo.setCurrentIndex(0)  # back to "— select —"
        finally:
            self._suppress_pair_signal = False

    def _on_auto_pair(self) -> None:
        """Fill pairing dropdowns by name-similarity (one group per dataset)."""
        assert self._pairing_table is not None
        used_groups: set[Path] = set()
        for row in range(self._pairing_table.rowCount()):
            name_item = self._pairing_table.item(row, 0)
            if name_item is None:
                continue
            dataset = next(
                (d for d in self._datasets if d.stem == name_item.text()),
                None,
            )
            if dataset is None:
                continue
            available = [g for g in self._groups if g not in used_groups]
            best, score = _best_match(dataset.stem, available)
            if best is not None and score >= _AUTO_PAIR_THRESHOLD:
                self._pairings[dataset] = best
                used_groups.add(best)
                combo = self._pairing_table.cellWidget(row, 1)
                if isinstance(combo, QComboBox):
                    idx = combo.findText(best.name)
                    if idx >= 0:
                        self._suppress_pair_signal = True
                        combo.setCurrentIndex(idx)
                        self._suppress_pair_signal = False
        self._invalidate_run()

    # ────────────────────────────────────────────────────────────
    # Slots — section 4 (CSV)
    # ────────────────────────────────────────────────────────────

    def _on_load_csv(self) -> None:
        assert self._csv_status_label is not None
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose calibration CSV", "", "CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        try:
            self._calibration = self._csv_parser(Path(path))
        except CalibrationCSVError as exc:
            QMessageBox.critical(
                self,
                "CSV parse failed",
                "\n".join(exc.errors[:30])
                + ("\n…" if len(exc.errors) > 30 else ""),
            )
            self._calibration = None
            self._csv_status_label.setText("No CSV loaded.")
            self._refresh_pairing_table()
            self._invalidate_run()
            return
        n_datasets = len(self._calibration.datasets())
        n_rows = sum(len(chans) for chans in self._calibration.rows.values())
        self._csv_status_label.setText(
            f"Loaded: {n_rows} rows / {n_datasets} datasets"
        )
        self._refresh_pairing_table()
        self._invalidate_run()

    def _calibration_summary_text(self, dataset_stem: str) -> str:
        if self._calibration is None:
            return "(no CSV)"
        rows = self._calibration.rows.get(dataset_stem)
        if not rows:
            return "(no rows for this dataset)"
        first_freq = next(iter(rows.values())).frequency_mhz
        chs = ", ".join(
            f"{ch}=({c.phase:.3f}, {c.modulation:.3f})" for ch, c in rows.items()
        )
        return f"freq={first_freq}, {chs}"

    # ────────────────────────────────────────────────────────────
    # Slots — validate + run
    # ────────────────────────────────────────────────────────────

    def _invalidate_run(self) -> None:
        self._validated = False
        if self._run_btn is not None:
            self._run_btn.setEnabled(False)

    def _on_validate(self) -> None:
        assert self._validate_log is not None
        items, channel_names_per_item, existing_decay_per_item, errors = (
            self._build_items_and_metadata()
        )
        log_lines: list[str] = []
        if errors:
            log_lines.append("Pre-flight failed:")
            log_lines.extend(f"  • {e}" for e in errors)
            self._validate_log.setPlainText("\n".join(log_lines))
            self._validated = False
            self._run_btn.setEnabled(False) if self._run_btn else None
            return

        force = self._conflict_overwrite_radio.isChecked()
        report = self._validator(
            items,
            channel_names_per_item=channel_names_per_item,
            force=force,
            existing_decay_per_item=existing_decay_per_item,
        )

        if report.pairing_errors:
            log_lines.append("Pairing errors:")
            log_lines.extend(f"  • {e}" for e in report.pairing_errors)
        if report.csv_coverage_errors:
            log_lines.append("CSV coverage errors:")
            log_lines.extend(f"  • {e}" for e in report.csv_coverage_errors)
        if report.frequency_consistency_errors:
            log_lines.append("Frequency-consistency errors:")
            log_lines.extend(
                f"  • {e}" for e in report.frequency_consistency_errors
            )
        if report.source_dir_uniqueness_errors:
            log_lines.append("Group-pairing conflicts:")
            log_lines.extend(
                f"  • {e}" for e in report.source_dir_uniqueness_errors
            )
        if report.decay_collision_warnings:
            log_lines.append("Warnings (will not block Run):")
            log_lines.extend(
                f"  • {w}" for w in report.decay_collision_warnings
            )

        if report.is_passing:
            log_lines.insert(0, f"✓ Pre-flight passed for {len(items)} dataset(s).")
            self._validated = True
            if self._run_btn:
                self._run_btn.setEnabled(True)
        else:
            log_lines.insert(0, "✗ Pre-flight failed.")
            self._validated = False
            if self._run_btn:
                self._run_btn.setEnabled(False)

        self._validate_log.setPlainText("\n".join(log_lines))

    def _on_run(self) -> None:
        if not self._validated:
            return
        items, _ch_per, _decay_per, errors = self._build_items_and_metadata()
        if errors:
            QMessageBox.critical(self, "Cannot run", "\n".join(errors))
            return

        token_config = TokenConfig()
        tile_config = self._build_tile_config()
        flim_config = FlimConfig()
        cross_format_rule: CrossFormatRule = BaseStemRule()
        rotate_k = (
            int(self._rotate_combo.currentData()) if self._rotate_combo else 0
        )
        flip_axis = self._flip_axis_value()
        force = bool(self._conflict_overwrite_radio and self._conflict_overwrite_radio.isChecked())

        progress = QProgressDialog(
            "Running batch TCSPC append…", "Cancel", 0, len(items), self
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        completed_index = {"n": 0}

        def progress_cb(item: BatchAppendItem, result: BatchItemResult) -> None:
            completed_index["n"] += 1
            n = completed_index["n"]
            progress.setValue(n)
            progress.setLabelText(
                f"({n}/{len(items)}) {item.h5_path.name} — {result.status}"
            )
            # Session in-place metadata sync — only when the active session
            # matches this item AND the write succeeded (or skipped, which
            # still wrote calibration).
            if result.status in ("succeeded", "skipped_no_changes"):
                self._sync_active_session_metadata(item)

        report = self._orchestrator(
            items,
            token_config=token_config,
            tile_config=tile_config,
            flim_config=flim_config,
            cross_format_rule=cross_format_rule,
            rotate_k=rotate_k,
            flip_axis=flip_axis,
            force=force,
            progress_callback=progress_cb,
            cancel_check=lambda: progress.wasCanceled(),
        )
        progress.setValue(len(items))
        self._show_summary(report)

    def _flip_axis_value(self) -> int | None:
        """Read flip combo's itemData. ``-1`` (no flip) → ``None``; ``0`` / ``1`` → that axis."""
        if self._flip_combo is None:
            return None
        data = self._flip_combo.currentData()
        if data is None or int(data) < 0:
            return None
        return int(data)

    def _build_tile_config(self) -> TileConfig:
        assert (
            self._grid_rows_spin is not None
            and self._grid_cols_spin is not None
            and self._grid_type_combo is not None
            and self._order_combo is not None
        )
        return TileConfig(
            grid_rows=self._grid_rows_spin.value(),
            grid_cols=self._grid_cols_spin.value(),
            grid_type=self._grid_type_combo.currentText(),
            order=self._order_combo.currentText(),
        )

    def _build_items_and_metadata(
        self,
    ) -> tuple[
        list[BatchAppendItem],
        dict[Path, list[str]],
        dict[Path, set[str]],
        list[str],
    ]:
        """Materialize the user's selections into orchestrator inputs.

        Returns ``(items, channel_names_per_item, existing_decay_per_item, errors)``.
        ``errors`` is non-empty when the dialog state is incoherent enough
        that we can't even build the validator inputs (e.g., no CSV loaded);
        the Validate slot surfaces those before calling the validator.
        """
        errors: list[str] = []
        if self._calibration is None:
            errors.append("Load a calibration CSV before validating.")

        checked = self._checked_datasets()
        if not checked:
            errors.append("Check at least one dataset to include in the batch.")

        if errors:
            return [], {}, {}, errors

        # CSV cross-check: every selected dataset's stem must have rows.
        csv_errors = validate_calibration_csv_against_selection(
            self._calibration,  # type: ignore[arg-type]
            [d.stem for d in checked],
        )
        errors.extend(csv_errors)

        # Build BatchAppendItems for datasets that have a pairing AND
        # calibration rows. Datasets missing either land in `errors`.
        items: list[BatchAppendItem] = []
        channel_names_per_item: dict[Path, list[str]] = {}
        existing_decay_per_item: dict[Path, set[str]] = {}

        for path in checked:
            group = self._pairings.get(path)
            if group is None:
                errors.append(
                    f"{path.name}: no group folder paired (or set to skip)"
                )
                continue
            calibration = (
                dict(self._calibration.rows.get(path.stem, {}))
                if self._calibration
                else {}
            )
            try:
                channel_names, existing_decay = self._read_store_summary(path)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path.name}: cannot read metadata ({exc})")
                continue
            channel_names_per_item[path] = list(channel_names)
            existing_decay_per_item[path] = set(existing_decay)
            items.append(
                BatchAppendItem(
                    h5_path=path,
                    source_dir=group,
                    calibration=calibration,
                )
            )

        return items, channel_names_per_item, existing_decay_per_item, errors

    # ────────────────────────────────────────────────────────────
    # Summary view
    # ────────────────────────────────────────────────────────────

    def _show_summary(self, report: BatchAppendReport) -> None:
        """Replace the form widget with a results summary view."""
        if self._content_widget is not None:
            self._content_widget.hide()

        summary = QWidget()
        layout = QVBoxLayout(summary)
        title = QLabel("Batch TCSPC Append — Summary")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(_render_summary_text(report))
        layout.addWidget(text, 1)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("Copy report to clipboard")
        copy_btn.clicked.connect(
            lambda: _copy_text_to_clipboard(_render_summary_text(report))
        )
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(copy_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self.layout().addWidget(summary)
        self._summary_widget = summary

        # Status bar message via the injected callable.
        succeeded = len(report.by_status("succeeded"))
        failed = len(report.by_status("failed"))
        skipped = len(report.by_status("skipped_no_changes"))
        cancelled = len(report.by_status("cancelled"))
        self._show_status(
            f"Batch TCSPC: {succeeded} succeeded, {failed} failed, "
            f"{skipped} skipped, {cancelled} cancelled"
        )

    # ────────────────────────────────────────────────────────────
    # Session sync
    # ────────────────────────────────────────────────────────────

    def _sync_active_session_metadata(self, item: BatchAppendItem) -> None:
        """Defeat h5py library-level metadata cache when the active session
        points at the same .h5 the batch just wrote.

        Documented gotcha in
        ``docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md``.
        The orchestrator can't do this because it's Qt/Session-free; the
        dialog does it after each successful item, gated on path match.
        """
        session = self._session
        if session is None or session.dataset is None:
            return
        try:
            if Path(session.dataset.path) != item.h5_path:
                return
        except Exception:  # noqa: BLE001
            return
        from percell4.application.use_cases.batch_add_decay import (
            _calibration_attrs,
        )

        attrs = _calibration_attrs(item.calibration)
        try:
            session.dataset.metadata.update(attrs)
        except Exception:  # noqa: BLE001 — best-effort in-place sync
            pass

    # ────────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────────

    def _checked_datasets(self) -> list[Path]:
        assert self._dataset_table is not None
        out: list[Path] = []
        for row, path in enumerate(self._datasets):
            wrapper = self._dataset_table.cellWidget(row, 0)
            if wrapper is None:
                continue
            check = wrapper.property("checkbox")
            if isinstance(check, QCheckBox) and check.isChecked():
                out.append(path)
        return out

    def _read_store_summary(self, path: Path) -> tuple[list[str], set[str]]:
        """Read ``channel_names`` and the set of existing ``/decay/<ch>`` names."""
        with h5py.File(path, "r") as f:
            channels: list[str] = []
            existing: set[str] = set()
            if "metadata" in f:
                raw = f["metadata"].attrs.get("channel_names")
                if raw is not None:
                    channels = [str(c) for c in raw]
            if "decay" in f:
                existing = {str(k) for k in f["decay"].keys()}
        return channels, existing


# ── Module-level pure helpers ─────────────────────────────────────────────


def _best_match(query: str, candidates: list[Path]) -> tuple[Path | None, float]:
    """Return the best name-similarity match (by SequenceMatcher ratio) or (None, 0)."""
    best: Path | None = None
    best_score: float = 0.0
    q_lower = query.lower()
    for cand in candidates:
        score = difflib.SequenceMatcher(None, q_lower, cand.name.lower()).ratio()
        if score > best_score:
            best_score = score
            best = cand
    return best, best_score


def _render_summary_text(report: BatchAppendReport) -> str:
    """Pure-function summary rendering used by both the view and clipboard copy."""
    lines = ["Batch TCSPC Append — Summary", ""]
    for result in report.items:
        bullet = {
            "succeeded": "✓",
            "skipped_no_changes": "○",
            "failed": "✗",
            "cancelled": "⊘",
            "not_run": "—",
        }.get(result.status, "?")
        lines.append(
            f"{bullet} {result.item.h5_path.name}  [{result.status}]"
        )
        if result.append_report:
            if result.append_report.written:
                lines.append(
                    f"    written: {', '.join(result.append_report.written)}"
                )
            for ch, err in result.append_report.errors.items():
                lines.append(f"    {ch}: {err}")
        if result.error:
            lines.append(f"    error: {result.error}")
    succeeded = len(report.by_status("succeeded"))
    failed = len(report.by_status("failed"))
    skipped = len(report.by_status("skipped_no_changes"))
    cancelled = len(report.by_status("cancelled"))
    lines.extend(
        [
            "",
            f"Totals: {succeeded} succeeded, {failed} failed, "
            f"{skipped} skipped, {cancelled} cancelled",
        ]
    )
    return "\n".join(lines)


def _copy_text_to_clipboard(text: str) -> None:
    from qtpy.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    app.clipboard().setText(text)
