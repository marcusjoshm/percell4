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
import re
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
from percell4.domain.io.cross_format import IntensityChannel
from percell4.domain.io.models import (
    CrossFormatRule,
    TokenConfig,
)
from percell4.gui._dialog_utils import (
    blocking_progress_modality,
    cap_to_screen,
    center_on_screen,
    detach_window,
    wrap_in_scroll,
)
from percell4.gui._flim_bin_form import FlimBinParamsForm, RotateFlipForm
from percell4.gui._stitching_form import StitchingForm
from percell4.gui.tcspc_tab_state import build_rule_from_preset
from percell4.io.paths import scan_files

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
        detach_window(self)
        center_on_screen(self)

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
        # channel_name -> .bin token string (e.g. "CA-SiR" -> "1"). Required
        # when channel names don't parse via the digit-suffix heuristic.
        self._channel_token_overrides: dict[str, str] = {}
        # Set of tokens discovered in the first paired group's .bin filenames.
        self._available_bin_tokens: list[str] = []

        # ── Widgets ──
        self._dataset_table: QTableWidget | None = None
        self._source_root_edit: QLineEdit | None = None
        self._groups_label: QLabel | None = None
        self._pairing_table: QTableWidget | None = None
        self._csv_status_label: QLabel | None = None
        self._channel_tokens_table: QTableWidget | None = None
        self._channel_tokens_status_label: QLabel | None = None
        self._validate_log: QPlainTextEdit | None = None
        self._run_btn: QPushButton | None = None
        self._validate_btn: QPushButton | None = None

        # Section 5 widgets — owned by the shared canonical forms so
        # the batch dialog and single-dataset TCSPC tab stay in lockstep.
        self._stitching_form: StitchingForm | None = None
        self._rotate_flip_form: RotateFlipForm | None = None
        self._flim_bin_form: FlimBinParamsForm | None = None
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
        body.addWidget(self._build_section_channel_tokens())
        body.addWidget(self._build_section_csv())
        body.addWidget(self._build_section_stitching())
        body.addWidget(self._build_section_conflict_policy())
        body.addWidget(self._build_section_validation())
        body.addStretch()

        # Keep a reference to the scroll wrapper so _show_summary can swap
        # it out cleanly. Hiding only `content` leaves the scroll viewport
        # visible and the summary view appends below it — see PR #9.
        self._form_scroll = wrap_in_scroll(content)
        outer.addWidget(self._form_scroll, 1)

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

    def _build_section_channel_tokens(self) -> QGroupBox:
        """One row per unique channel name across the selected ``.h5`` files,
        with a combo of ``.bin`` tokens discovered from the first paired group.

        Mirrors the per-channel token override UI in
        :class:`add_layer_dialog.AddLayerDialog` (``_tcspc_render_table`` →
        ``_on_tcspc_token_picked``). For semantic channel names (``mNG``,
        ``CA-SiR``) where the digit-suffix heuristic yields an empty token,
        the user maps each name to the correct ``.bin`` channel number here.
        """
        box = QGroupBox(
            "4. Channel → .bin token mapping (one row per channel name; "
            "applies to every dataset)"
        )
        layout = QVBoxLayout(box)

        self._channel_tokens_status_label = QLabel(
            "Pair at least one dataset with a group folder to discover "
            "available .bin channel tokens."
        )
        self._channel_tokens_status_label.setWordWrap(True)
        layout.addWidget(self._channel_tokens_status_label)

        self._channel_tokens_table = QTableWidget(0, 2)
        self._channel_tokens_table.setHorizontalHeaderLabels(
            [".h5 channel name", ".bin channel token"]
        )
        self._channel_tokens_table.horizontalHeader().setStretchLastSection(True)
        self._channel_tokens_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self._channel_tokens_table.verticalHeader().setVisible(False)
        self._channel_tokens_table.setMinimumHeight(140)
        layout.addWidget(self._channel_tokens_table)

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
        """Section 5 — Stitching + orientation + raw ``.bin`` geometry.

        Three canonical widgets rather than one combined form: the stitching
        grid is shared with every other tiling surface, while rotate/flip and
        the raw ``.bin`` geometry are TCSPC-only. Building them here rather
        than reimplementing is what keeps this dialog from drifting against
        the single-dataset TCSPC tab — the recurring root cause of every
        regression in PR #9.

        Registration controls stay visible on this surface, unchanged.
        """
        box = QGroupBox("5. Stitching & orientation (applied to every dataset)")
        layout = QVBoxLayout(box)
        self._stitching_form = StitchingForm(
            show_registration=True, show_fusion=False, title=""
        )
        self._rotate_flip_form = RotateFlipForm()
        self._flim_bin_form = FlimBinParamsForm()
        for form in (
            self._stitching_form,
            self._rotate_flip_form,
            self._flim_bin_form,
        ):
            form.changed.connect(self._invalidate_run)
            layout.addWidget(form)
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
        self._refresh_channel_tokens_table()
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
        self._refresh_channel_tokens_table()
        self._invalidate_run()

    def _on_dataset_check_changed(self) -> None:
        """React to an Include-column checkbox toggle.

        Pairing and channel-tokens tables read from ``_checked_datasets()``,
        so they have to rebuild whenever the checked set changes — otherwise
        they stay stale until the next add/remove triggers a refresh.
        """
        self._refresh_pairing_table()
        self._refresh_channel_tokens_table()
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
        # Pairing + channel-tokens tables are rebuilt from _checked_datasets(),
        # so they go stale the instant a checkbox flips. Refreshing here keeps
        # the dialog coherent — otherwise the next add/remove click triggers
        # a refresh whose side effect looks like the button did extra work.
        check.stateChanged.connect(self._on_dataset_check_changed)
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
        # Pairing changed → re-scan available .bin tokens from whichever
        # group is now first-paired, then re-render the channel-tokens table.
        self._refresh_channel_tokens_table()
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
        self._refresh_channel_tokens_table()
        self._invalidate_run()

    # ────────────────────────────────────────────────────────────
    # Channel-token override section (Section 4)
    # ────────────────────────────────────────────────────────────

    def _discover_bin_tokens(self, group_folder: Path) -> list[str]:
        """Scan a group's ``.bin`` files for distinct channel tokens.

        Files matching ``_ch(\\d+)`` contribute their captured digit token.
        Files with no ``_chN`` token (e.g., single-channel LASX exports
        like ``decay.bin``) contribute token ``"0"`` so the user can pair
        the single channel via Section 4. Mirrors the same fallback in
        ``cross_format._extract_token``.

        Returns sorted-by-numeric-value list of tokens (so ``"1"`` comes
        before ``"10"`` for typical LAS X exports).
        """
        if group_folder is None or not group_folder.exists():
            return []
        tokens: set[str] = set()
        for p in scan_files(group_folder, "*.bin", recursive=True):
            m = re.search(r"_ch(\d+)", p.stem)
            if m:
                tokens.add(m.group(1))
            else:
                # Single-channel fallback: surface "0" so the user can pair
                # a token-less .bin with their intensity channel.
                tokens.add("0")
        try:
            return sorted(tokens, key=int)
        except ValueError:
            return sorted(tokens)

    def _all_selected_channel_names(self) -> list[str]:
        """Distinct channel names across every checked dataset, preserving
        first-seen order so the UI is deterministic."""
        seen: list[str] = []
        seen_set: set[str] = set()
        for path in self._checked_datasets():
            try:
                channel_names, _ = self._read_store_summary(path)
            except Exception:  # noqa: BLE001
                continue
            for name in channel_names:
                if name not in seen_set:
                    seen.append(name)
                    seen_set.add(name)
        return seen

    def _refresh_channel_tokens_table(self) -> None:
        """Rebuild the channel-tokens table from the current dataset selection
        and the first available paired group.

        Default-mapping rule per channel name: if the digit-suffix heuristic
        produces a token that's in the discovered list, use it. Otherwise
        leave it at ``(unmapped)`` for the user to pick. The user's previous
        explicit picks are preserved across refreshes.
        """
        assert self._channel_tokens_table is not None
        assert self._channel_tokens_status_label is not None

        # 1. Pick the first paired group to scan for available tokens.
        first_group: Path | None = next(
            (g for g in self._pairings.values() if g is not None), None
        )
        self._available_bin_tokens = (
            self._discover_bin_tokens(first_group) if first_group else []
        )
        if first_group is None:
            self._channel_tokens_status_label.setText(
                "Pair at least one dataset with a group folder to discover "
                "available .bin channel tokens."
            )
        elif not self._available_bin_tokens:
            self._channel_tokens_status_label.setText(
                f"No '_ch(N)' tokens found in {first_group.name}/*.bin — "
                "the .bin filenames may use a different naming convention."
            )
        else:
            self._channel_tokens_status_label.setText(
                f"Discovered .bin tokens (from {first_group.name}): "
                f"{', '.join(self._available_bin_tokens)}"
            )

        # 2. Rebuild rows from the union of channel names across selected .h5.
        channel_names = self._all_selected_channel_names()
        self._channel_tokens_table.setRowCount(0)
        for ch in channel_names:
            row = self._channel_tokens_table.rowCount()
            self._channel_tokens_table.insertRow(row)
            name_item = QTableWidgetItem(ch)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self._channel_tokens_table.setItem(row, 0, name_item)

            combo = QComboBox()
            combo.addItem("(unmapped)", "")
            for tok in self._available_bin_tokens:
                combo.addItem(tok, tok)
            # Default mapping: prefer the user's previous explicit pick,
            # else the digit-suffix heuristic if it lands in the discovered
            # token list, else (unmapped).
            seeded = self._channel_token_overrides.get(ch, "")
            if not seeded:
                m = re.search(r"(\d+)$", ch)
                if m and m.group(1) in self._available_bin_tokens:
                    seeded = m.group(1)
            if seeded:
                idx = combo.findData(seeded)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(
                lambda _i, c=ch, w=combo: self._on_channel_token_picked(c, w)
            )
            self._channel_tokens_table.setCellWidget(row, 1, combo)
            # Persist seeded default so Validate / Run pick it up.
            if seeded:
                self._channel_token_overrides[ch] = seeded

    def _on_channel_token_picked(self, channel_name: str, combo: QComboBox) -> None:
        token = combo.currentData() or ""
        if token:
            self._channel_token_overrides[channel_name] = token
        else:
            self._channel_token_overrides.pop(channel_name, None)
        self._invalidate_run()

    def _build_intensity_channels_overrides(self) -> dict[Path, list[IntensityChannel]]:
        """Translate ``_channel_token_overrides`` into per-item override lists.

        Reads each item's ``channel_names`` and builds an
        ``IntensityChannel`` for every channel, threading the user's token
        pick into the matcher. Channel names without a pick are left with
        an empty token (they'll fail to bind, which is intentional — the
        user opted out for that channel).
        """
        overrides: dict[Path, list[IntensityChannel]] = {}
        for path in self._checked_datasets():
            try:
                channel_names, _ = self._read_store_summary(path)
            except Exception:  # noqa: BLE001
                continue
            overrides[path] = [
                IntensityChannel(
                    name=ch,
                    token=self._channel_token_overrides.get(ch, ""),
                    base_stem=None,
                )
                for ch in channel_names
            ]
        return overrides

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

        assert self._stitching_form is not None
        token_config = TokenConfig()
        tile_config = self._stitching_form.tile_config()
        # Frequency is per-dataset (from CSV) and lands in /metadata via
        # _calibration_attrs; the shared form's FlimConfig only carries
        # geometry. Picking 80.0 here is a no-op for the actual write path.
        assert self._flim_bin_form is not None
        flim_config = self._flim_bin_form.flim_config(frequency_mhz=80.0)
        # Same preset the single-dataset TCSPC tab uses: direct token equality
        # first (driven by the per-channel-name overrides set in Section 4),
        # base-stem fallback for .bin files without a channel token.
        cross_format_rule: CrossFormatRule = build_rule_from_preset()
        assert self._rotate_flip_form is not None
        rotate_k = self._rotate_flip_form.rotation_k()
        flip_axis = self._rotate_flip_form.flip_axis()
        force = bool(self._conflict_overwrite_radio and self._conflict_overwrite_radio.isChecked())

        progress = QProgressDialog(
            "Running batch TCSPC append…", "Cancel", 0, len(items), self
        )
        # Must stay modal: this loop polls wasCanceled() without pumping
        # events itself, so it depends on setValue()'s modal-only
        # processEvents. See blocking_progress_modality for why macOS
        # needs ApplicationModal rather than WindowModal here.
        progress.setWindowModality(blocking_progress_modality())
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

        overrides = self._build_intensity_channels_overrides()
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
            intensity_channels_overrides=overrides or None,
        )
        progress.setValue(len(items))
        self._show_summary(report)

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
        """Replace the form view with a results summary view.

        The form lives in ``self._form_scroll`` (a ``QScrollArea`` wrapping
        the section widgets) plus the pinned button row beneath it. Hiding
        only the inner content widget leaves the scroll viewport visible
        and the summary appends below it (PR #9 review). We hide the scroll
        wrapper itself and insert the summary in its place.
        """
        if self._form_scroll is not None:
            self._form_scroll.hide()

        summary = QWidget()
        layout = QVBoxLayout(summary)
        layout.setContentsMargins(0, 0, 0, 0)
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

        # Insert above the existing pinned [Validate] [Run] [Close] row so
        # the summary occupies the same space the form's scroll wrapper did.
        outer = self.layout()
        outer.insertWidget(0, summary, 1)
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
            # Surface matcher diagnostics — these explain "wrote no channels"
            # when channel names don't parse via the digit-suffix heuristic.
            unmatched = getattr(result.append_report, "unmatched", ()) or ()
            if unmatched:
                lines.append(f"    unmatched .bin files: {len(unmatched)}")
                for p in list(unmatched)[:5]:
                    lines.append(f"      • {p.name}")
                if len(unmatched) > 5:
                    lines.append(f"      … and {len(unmatched) - 5} more")
            ambiguous = getattr(result.append_report, "ambiguous", ()) or ()
            if ambiguous:
                lines.append(f"    ambiguous .bin files: {len(ambiguous)}")
                for entry in list(ambiguous)[:5]:
                    # entry is (Path, tuple[str, ...]) — path + matching names
                    p, names = entry
                    lines.append(f"      • {p.name} → {', '.join(names)}")
                if len(ambiguous) > 5:
                    lines.append(f"      … and {len(ambiguous) - 5} more")
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
