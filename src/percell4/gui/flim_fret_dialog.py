"""FLIM-FRET analysis setup dialog.

Modal ``QDialog`` that drives the FLIM-FRET workflow:

- User picks a source folder; discovery scans ``*.h5`` / ``*.hdf5`` and
  pre-screens each file against the suffix-based qualification rule.
  Non-qualifying datasets are excluded from the donor/DA dropdowns with
  a reason; a summary label shows the count.
- Pair table: one row per (donor, donor+acceptor) pairing with a Configure
  button that opens a sub-dialog for per-pair layer selection (mask,
  phasor, lifetime, plus segmentations in single-cell mode).
- Global single-cell toggle re-runs discovery (because ``/labels/`` is now
  required) and marks any pair whose Configure no longer satisfies the
  segmentation invariant.
- Start builds and freezes a ``FlimFretConfig``, creates a timestamped
  run folder, drives a ``QProgressDialog``-based per-pair loop calling the
  Qt-free orchestrator, writes one combined CSV atomically, and shows a
  summary message.

This dialog is an **Action** per
``docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md``:
it reads no session state, writes no session state, and adds no napari
layers. It only reads from disk and writes a CSV.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
from qtpy.QtCore import QSettings, Qt
from qtpy.QtWidgets import (
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
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from percell4.application.use_cases.flim_fret_discovery import (
    DatasetCandidate,
    discover_flim_fret_candidates,
    list_lifetime_channel_names,
)
from percell4.application.use_cases.run_flim_fret import run_flim_fret
from percell4.gui._dialog_utils import cap_to_screen, wrap_in_scroll
from percell4.store import DatasetStore
from percell4.workflows.artifacts import create_run_folder, write_atomic
from percell4.workflows.models import (
    FlimFretConfig,
    FlimFretPair,
    FlimFretPairResult,
    FlimFretReport,
    FlimFretStatus,
)
from percell4.workflows.run_log import RunLog

logger = logging.getLogger(__name__)

# QSettings keys — same org/app convention as single-cell workflow.
_QSETTINGS_ORG = "LeeLabPerCell4"
_QSETTINGS_APP = "PerCell4"
_QSETTINGS_OUTPUT_KEY = "flim_fret_workflow/output_parent"
_QSETTINGS_SOURCE_KEY = "flim_fret_workflow/source_folder"


# ── Per-pair Configure state ───────────────────────────────


class _PairConfig:
    """Mutable holder for one pair's Configure picks.

    The pair table widget remembers the user's per-pair layer selections
    so toggling single-cell or re-opening Configure preserves prior picks.
    Segmentation picks are kept across single-cell toggle so re-enabling
    restores them (per plan §I1 resolution).
    """

    def __init__(self) -> None:
        self.donor_mask: str = ""
        self.donor_phasor: str = ""
        self.donor_lifetime: str = ""
        self.da_mask: str = ""
        self.da_phasor: str = ""
        self.da_lifetime: str = ""
        # Kept across single-cell toggles.
        self.donor_segmentation: str = ""
        self.da_segmentation: str = ""
        # Reset to True any time donor/DA dataset selection changes.
        self.configured: bool = False

    def is_complete(self, *, single_cell: bool) -> bool:
        if not self.configured:
            return False
        required = [
            self.donor_mask,
            self.donor_phasor,
            self.donor_lifetime,
            self.da_mask,
            self.da_phasor,
            self.da_lifetime,
        ]
        if single_cell:
            required += [self.donor_segmentation, self.da_segmentation]
        return all(required)


# ── Dialog ─────────────────────────────────────────────────


class FlimFretDialog(QDialog):
    """Setup dialog for the FLIM-FRET analysis workflow.

    Optional ``orchestrator`` and ``runner_factory`` parameters exist for
    testing — production callers don't pass them.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        orchestrator: Callable[..., FlimFretReport] = run_flim_fret,
        discovery: Callable[..., list[DatasetCandidate]] = discover_flim_fret_candidates,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("FLIM-FRET analysis")
        self.setMinimumWidth(820)
        self.resize(900, 720)
        cap_to_screen(self)

        # Injected callables.
        self._orchestrator = orchestrator
        self._discovery = discovery

        # Pure-Python state.
        self._source_folder: Path | None = None
        # path -> DatasetCandidate
        self._candidates: dict[Path, DatasetCandidate] = {}
        # row index -> _PairConfig
        self._pair_configs: list[_PairConfig] = []

        # Result captured on accept() so callers can read it.
        self._workflow_config: FlimFretConfig | None = None
        self._last_report: FlimFretReport | None = None
        self._last_run_folder: Path | None = None

        # Widgets.
        self._single_cell_check: QCheckBox | None = None
        self._source_folder_edit: QLineEdit | None = None
        self._output_folder_edit: QLineEdit | None = None
        self._discovery_status_label: QLabel | None = None
        self._pair_table: QTableWidget | None = None
        self._start_btn: QPushButton | None = None

        self._build_ui()
        self._restore_qsettings_paths()
        self._update_run_button()

    # ── Public API ────────────────────────────────────────

    @property
    def workflow_config(self) -> FlimFretConfig | None:
        """Frozen config captured on accept(). ``None`` if rejected."""
        return self._workflow_config

    @property
    def last_run_folder(self) -> Path | None:
        return self._last_run_folder

    @property
    def last_report(self) -> FlimFretReport | None:
        return self._last_report

    # ── UI construction ──────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setSpacing(10)

        body.addWidget(self._build_section_mode())
        body.addWidget(self._build_section_folders())
        body.addWidget(self._build_section_pairs())
        body.addStretch()

        outer.addWidget(wrap_in_scroll(content), 1)

        # Pinned button row outside the scroll area.
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._start_btn = QPushButton("Start")
        self._start_btn.clicked.connect(self._on_start_clicked)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(cancel_btn)
        outer.addLayout(btn_row)

    def _build_section_mode(self) -> QGroupBox:
        box = QGroupBox("1. Mode")
        layout = QVBoxLayout(box)
        self._single_cell_check = QCheckBox("Single-cell analysis")
        self._single_cell_check.setToolTip(
            "Segment both donor and DA datasets; emit one CSV row per DA "
            "cell using a pooled donor reference."
        )
        self._single_cell_check.toggled.connect(self._on_single_cell_toggled)
        layout.addWidget(self._single_cell_check)
        return box

    def _build_section_folders(self) -> QGroupBox:
        box = QGroupBox("2. Folders")
        layout = QFormLayout(box)

        src_row = QHBoxLayout()
        self._source_folder_edit = QLineEdit()
        self._source_folder_edit.setReadOnly(True)
        self._source_folder_edit.setPlaceholderText(
            "Pick a folder containing .h5 datasets…"
        )
        src_browse = QPushButton("Browse…")
        src_browse.clicked.connect(self._on_browse_source)
        src_row.addWidget(self._source_folder_edit, 1)
        src_row.addWidget(src_browse)
        layout.addRow("Source folder:", src_row)

        out_row = QHBoxLayout()
        self._output_folder_edit = QLineEdit()
        self._output_folder_edit.setReadOnly(True)
        self._output_folder_edit.setPlaceholderText(
            "Pick a parent folder for the run output…"
        )
        out_browse = QPushButton("Browse…")
        out_browse.clicked.connect(self._on_browse_output)
        out_row.addWidget(self._output_folder_edit, 1)
        out_row.addWidget(out_browse)
        layout.addRow("Output parent folder:", out_row)

        self._discovery_status_label = QLabel(
            "Datasets eligible for FLIM-FRET need at least one mask whose "
            "name ends in '_mask', one ending in '_phasor', and one channel "
            "ending in '_lifetime'."
        )
        self._discovery_status_label.setWordWrap(True)
        layout.addRow("", self._discovery_status_label)

        return box

    def _build_section_pairs(self) -> QGroupBox:
        box = QGroupBox("3. Pairs")
        layout = QVBoxLayout(box)

        self._pair_table = QTableWidget(0, 4)
        self._pair_table.setHorizontalHeaderLabels(
            ["Pair name", "Donor .h5", "DA .h5", "Configure"]
        )
        self._pair_table.horizontalHeader().setStretchLastSection(False)
        self._pair_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self._pair_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self._pair_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )
        self._pair_table.verticalHeader().setVisible(False)
        self._pair_table.setMinimumHeight(180)
        layout.addWidget(self._pair_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add pair")
        add_btn.clicked.connect(self._on_add_pair)
        rem_btn = QPushButton("Remove pair")
        rem_btn.clicked.connect(self._on_remove_pair)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rem_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return box

    # ── State helpers ────────────────────────────────────

    def _is_single_cell(self) -> bool:
        return bool(
            self._single_cell_check and self._single_cell_check.isChecked()
        )

    def _eligible_paths(self) -> list[Path]:
        return [p for p, c in sorted(self._candidates.items()) if c.qualifies]

    def _restore_qsettings_paths(self) -> None:
        qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        src = qs.value(_QSETTINGS_SOURCE_KEY, "", type=str)
        out = qs.value(_QSETTINGS_OUTPUT_KEY, "", type=str)
        if src and Path(src).is_dir():
            self._set_source_folder(Path(src))
        if out:
            assert self._output_folder_edit is not None
            self._output_folder_edit.setText(out)

    def _save_qsettings_paths(self) -> None:
        qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        if self._source_folder is not None:
            qs.setValue(_QSETTINGS_SOURCE_KEY, str(self._source_folder))
        assert self._output_folder_edit is not None
        out = self._output_folder_edit.text().strip()
        if out:
            qs.setValue(_QSETTINGS_OUTPUT_KEY, out)

    # ── Folder browse handlers ───────────────────────────

    def _on_browse_source(self) -> None:
        assert self._source_folder_edit is not None
        start = (
            str(self._source_folder)
            if self._source_folder
            else self._source_folder_edit.text() or ""
        )
        folder = QFileDialog.getExistingDirectory(
            self, "Pick FLIM-FRET source folder", start
        )
        if not folder:
            return
        new_path = Path(folder)
        if self._pair_configs and new_path != self._source_folder:
            resp = QMessageBox.question(
                self,
                "Clear pair table?",
                f"Changing the source folder will clear {len(self._pair_configs)} "
                f"configured pair(s). Continue?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return
            self._clear_pairs()
        self._set_source_folder(new_path)

    def _on_browse_output(self) -> None:
        assert self._output_folder_edit is not None
        start = self._output_folder_edit.text() or ""
        folder = QFileDialog.getExistingDirectory(
            self, "Pick output parent folder", start
        )
        if not folder:
            return
        self._output_folder_edit.setText(folder)
        self._update_run_button()

    def _set_source_folder(self, folder: Path) -> None:
        self._source_folder = folder
        assert self._source_folder_edit is not None
        self._source_folder_edit.setText(str(folder))
        self._rediscover()

    def _rediscover(self) -> None:
        """Run discovery and refresh dataset dropdowns in the pair table."""
        if self._source_folder is None:
            self._candidates = {}
        else:
            try:
                candidates = self._discovery(
                    self._source_folder, single_cell=self._is_single_cell()
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("FLIM-FRET discovery failed")
                QMessageBox.warning(
                    self, "Discovery failed", f"Could not scan folder: {exc}"
                )
                candidates = []
            self._candidates = {c.path: c for c in candidates}
        self._refresh_discovery_status_label()
        self._refresh_pair_table_dropdowns()
        self._update_run_button()

    def _refresh_discovery_status_label(self) -> None:
        assert self._discovery_status_label is not None
        if self._source_folder is None:
            self._discovery_status_label.setText(
                "Pick a source folder to discover eligible datasets."
            )
            return
        eligible = self._eligible_paths()
        excluded = [
            c for c in self._candidates.values() if not c.qualifies
        ]
        lines = [
            f"{len(eligible)} eligible dataset(s) found in {self._source_folder.name}."
        ]
        if excluded:
            lines.append(
                f"Excluded ({len(excluded)}): "
                + "; ".join(
                    f"{c.path.name}: {', '.join(c.reasons)}"
                    for c in excluded[:5]
                )
                + ("…" if len(excluded) > 5 else "")
            )
        self._discovery_status_label.setText("\n".join(lines))

    # ── Single-cell toggle ───────────────────────────────

    def _on_single_cell_toggled(self, _checked: bool) -> None:
        # Re-run discovery because /labels/* is now required.
        self._rediscover()
        # Configure state is preserved; per-pair "needs (re)configure" is
        # surfaced via _update_run_button + Configure button label below.
        self._refresh_pair_table_button_labels()
        self._update_run_button()

    # ── Pair table operations ───────────────────────────

    def _on_add_pair(self) -> None:
        assert self._pair_table is not None
        row = self._pair_table.rowCount()
        self._pair_table.insertRow(row)
        self._pair_configs.append(_PairConfig())

        # Pair name (editable, defaults to "pair_<N>")
        name_item = QTableWidgetItem(f"pair_{row + 1}")
        self._pair_table.setItem(row, 0, name_item)

        # Donor and DA combos
        donor_combo = self._build_dataset_combo(row, is_donor=True)
        da_combo = self._build_dataset_combo(row, is_donor=False)
        self._pair_table.setCellWidget(row, 1, donor_combo)
        self._pair_table.setCellWidget(row, 2, da_combo)

        # Configure button
        cfg_btn = QPushButton("Configure")
        cfg_btn.clicked.connect(lambda _checked, r=row: self._on_configure_pair(r))
        self._pair_table.setCellWidget(row, 3, cfg_btn)

        # Wire user-edit signals for validation per the qt-wire-user-edit-signals
        # convention.
        self._pair_table.itemChanged.connect(self._on_table_item_changed)
        self._update_run_button()

    def _on_remove_pair(self) -> None:
        assert self._pair_table is not None
        rows = sorted(
            {idx.row() for idx in self._pair_table.selectedIndexes()},
            reverse=True,
        )
        if not rows:
            # If nothing selected, remove the last row.
            rows = (
                [self._pair_table.rowCount() - 1]
                if self._pair_table.rowCount() > 0
                else []
            )
        for r in rows:
            self._pair_table.removeRow(r)
            if 0 <= r < len(self._pair_configs):
                del self._pair_configs[r]
        # Rebind Configure button row indices.
        self._rebind_configure_buttons()
        self._update_run_button()

    def _clear_pairs(self) -> None:
        assert self._pair_table is not None
        self._pair_table.setRowCount(0)
        self._pair_configs.clear()
        self._update_run_button()

    def _rebind_configure_buttons(self) -> None:
        assert self._pair_table is not None
        for r in range(self._pair_table.rowCount()):
            btn = self._pair_table.cellWidget(r, 3)
            if isinstance(btn, QPushButton):
                try:
                    btn.clicked.disconnect()
                except (TypeError, RuntimeError):
                    pass
                btn.clicked.connect(
                    lambda _checked, row=r: self._on_configure_pair(row)
                )

    def _build_dataset_combo(self, row: int, *, is_donor: bool) -> QComboBox:
        combo = QComboBox()
        for p in self._eligible_paths():
            combo.addItem(p.name, userData=p)
        combo.currentIndexChanged.connect(
            lambda _idx, r=row, donor=is_donor: self._on_dataset_combo_changed(r, donor)
        )
        return combo

    def _refresh_pair_table_dropdowns(self) -> None:
        """Repopulate every donor/DA combo with the current eligible set."""
        assert self._pair_table is not None
        for r in range(self._pair_table.rowCount()):
            for col in (1, 2):
                combo = self._pair_table.cellWidget(r, col)
                if not isinstance(combo, QComboBox):
                    continue
                current = combo.currentData()
                combo.blockSignals(True)
                combo.clear()
                eligible = self._eligible_paths()
                for p in eligible:
                    combo.addItem(p.name, userData=p)
                # Restore previous selection if it's still eligible.
                if current in eligible:
                    combo.setCurrentIndex(eligible.index(current))
                else:
                    combo.setCurrentIndex(0 if eligible else -1)
                    # Selection lost → mark pair as needing reconfigure.
                    if 0 <= r < len(self._pair_configs):
                        self._pair_configs[r].configured = False
                combo.blockSignals(False)
        self._refresh_pair_table_button_labels()

    def _refresh_pair_table_button_labels(self) -> None:
        assert self._pair_table is not None
        single = self._is_single_cell()
        for r in range(self._pair_table.rowCount()):
            btn = self._pair_table.cellWidget(r, 3)
            if not isinstance(btn, QPushButton):
                continue
            cfg = self._pair_configs[r] if r < len(self._pair_configs) else None
            if cfg is None or not cfg.is_complete(single_cell=single):
                btn.setText("Configure")
                btn.setToolTip(
                    "Configure layer selections for this pair. "
                    "Pair is incomplete until configured."
                )
            else:
                btn.setText("Reconfigure")
                btn.setToolTip("Edit this pair's layer selections.")

    def _on_dataset_combo_changed(self, row: int, _is_donor: bool) -> None:
        # Dataset choice changed → previous Configure picks may be stale.
        if 0 <= row < len(self._pair_configs):
            self._pair_configs[row].configured = False
        self._refresh_pair_table_button_labels()
        self._update_run_button()

    def _on_table_item_changed(self, _item: QTableWidgetItem) -> None:
        self._update_run_button()

    # ── Configure sub-dialog ────────────────────────────

    def _on_configure_pair(self, row: int) -> None:
        assert self._pair_table is not None
        donor_combo = self._pair_table.cellWidget(row, 1)
        da_combo = self._pair_table.cellWidget(row, 2)
        if not isinstance(donor_combo, QComboBox) or not isinstance(da_combo, QComboBox):
            return
        donor_path = donor_combo.currentData()
        da_path = da_combo.currentData()
        if donor_path is None or da_path is None:
            QMessageBox.warning(
                self,
                "Pick datasets first",
                "Select donor and DA datasets before configuring.",
            )
            return
        if Path(donor_path) == Path(da_path):
            QMessageBox.warning(
                self,
                "Donor and DA must differ",
                "Donor and DA datasets within one pair must be different files.",
            )
            return

        cfg = self._pair_configs[row]
        sub = _ConfigurePairDialog(
            self,
            donor_path=Path(donor_path),
            da_path=Path(da_path),
            single_cell=self._is_single_cell(),
            initial=cfg,
        )
        if sub.exec_() == QDialog.Accepted:
            sub.apply_to(cfg)
            cfg.configured = True
            self._refresh_pair_table_button_labels()
        self._update_run_button()

    # ── Start button enable logic ───────────────────────

    def _update_run_button(self) -> None:
        if self._start_btn is None:
            return
        self._start_btn.setEnabled(self._build_config_or_none() is not None)

    def _build_config_or_none(self) -> FlimFretConfig | None:
        """Return a frozen config if every UI invariant holds, else None.

        Errors during construction are swallowed here (only used for the
        Start button enable check); ``_on_start_clicked`` re-builds and
        surfaces a QMessageBox on failure.
        """
        try:
            return self._build_config()
        except (ValueError, RuntimeError):
            return None

    def _build_config(self) -> FlimFretConfig:
        assert self._pair_table is not None
        assert self._output_folder_edit is not None

        out_str = self._output_folder_edit.text().strip()
        if not out_str:
            raise RuntimeError("Pick an output parent folder.")

        single = self._is_single_cell()
        pairs: list[FlimFretPair] = []
        seen_names: set[str] = set()
        for r in range(self._pair_table.rowCount()):
            name_item = self._pair_table.item(r, 0)
            donor_combo = self._pair_table.cellWidget(r, 1)
            da_combo = self._pair_table.cellWidget(r, 2)
            if not isinstance(donor_combo, QComboBox) or not isinstance(da_combo, QComboBox):
                raise RuntimeError(f"Pair row {r}: missing dropdowns.")
            name = (name_item.text() if name_item else "").strip()
            if not name:
                raise RuntimeError(f"Pair row {r}: name is empty.")
            if name in seen_names:
                raise RuntimeError(f"Pair name {name!r} is duplicated.")
            seen_names.add(name)

            donor_path = donor_combo.currentData()
            da_path = da_combo.currentData()
            if donor_path is None or da_path is None:
                raise RuntimeError(f"Pair {name!r}: pick donor and DA datasets.")

            cfg = self._pair_configs[r]
            if not cfg.is_complete(single_cell=single):
                raise RuntimeError(f"Pair {name!r}: Configure is incomplete.")

            pairs.append(
                FlimFretPair(
                    name=name,
                    donor_h5=Path(donor_path),
                    da_h5=Path(da_path),
                    donor_mask=cfg.donor_mask,
                    donor_phasor=cfg.donor_phasor,
                    donor_lifetime=cfg.donor_lifetime,
                    da_mask=cfg.da_mask,
                    da_phasor=cfg.da_phasor,
                    da_lifetime=cfg.da_lifetime,
                    donor_segmentation=cfg.donor_segmentation if single else None,
                    da_segmentation=cfg.da_segmentation if single else None,
                )
            )

        return FlimFretConfig(
            pairs=pairs, single_cell=single, output_parent=Path(out_str)
        )

    # ── Start handler ────────────────────────────────────

    def _on_start_clicked(self) -> None:
        try:
            config = self._build_config()
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Cannot start", str(exc))
            return

        try:
            run_folder = create_run_folder(
                config.output_parent,
                prefix="flim_fret_run",
                create_subdirs=False,
            )
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Cannot create run folder",
                f"Could not create run folder under {config.output_parent}: {exc}",
            )
            return

        log = RunLog(run_folder)

        progress = QProgressDialog(
            "Running FLIM-FRET analysis…",
            "Cancel",
            0,
            len(config.pairs),
            self,
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        n_pairs = len(config.pairs)
        completed = {"n": 0}

        def progress_cb(pair: FlimFretPair, result: FlimFretPairResult) -> None:
            completed["n"] += 1
            n = completed["n"]
            progress.setValue(n)
            progress.setLabelText(
                f"({n}/{n_pairs}) {pair.name} — {result.status.value}"
            )

        report = self._orchestrator(
            config,
            progress_callback=progress_cb,
            cancel_check=lambda: progress.wasCanceled(),
            run_log=log,
        )
        progress.setValue(n_pairs)

        # Write the combined CSV via the atomic helper.
        csv_path = run_folder / "flim_fret_results.csv"
        rows = [row for result in report.results for row in result.rows]
        df = pd.DataFrame(rows, columns=_CSV_COLUMNS)
        try:
            write_atomic(
                csv_path,
                lambda tmp: df.to_csv(
                    tmp,
                    index=False,
                    float_format="%.6g",
                    na_rep="",
                    encoding="utf-8",
                    lineterminator="\n",
                ),
            )
        except OSError as exc:
            log.log(phase="flim_fret", event="csv_write_failed", error=str(exc))
            QMessageBox.critical(
                self,
                "CSV write failed",
                f"Could not write {csv_path}: {exc}\n\n"
                f"Per-pair events were captured in {log.path}.",
            )
            return

        # Store result for callers; show summary.
        self._workflow_config = config
        self._last_report = report
        self._last_run_folder = run_folder
        self._save_qsettings_paths()
        self._show_summary(report, csv_path)
        self.accept()

    def _show_summary(
        self, report: FlimFretReport, csv_path: Path
    ) -> None:
        counts: dict[FlimFretStatus, int] = {}
        for r in report.results:
            counts[r.status] = counts.get(r.status, 0) + 1
        lines = ["FLIM-FRET run complete.", ""]
        for status in FlimFretStatus:
            n = counts.get(status, 0)
            if n:
                lines.append(f"  {status.value}: {n}")
        lines.append("")
        lines.append(f"CSV: {csv_path}")
        QMessageBox.information(self, "FLIM-FRET", "\n".join(lines))


# Column order matches plan R7 exactly.
_CSV_COLUMNS = [
    "pair_name",
    "donor_dataset",
    "da_dataset",
    "cell_id",
    "donor_mean_lifetime",
    "da_mean_lifetime",
    "fret_efficiency",
    "n_pixels_donor",
    "n_pixels_da",
    "n_cells_donor_reference",
    "n_da_cells_skipped",
]


# ── Configure sub-dialog ───────────────────────────────────


class _ConfigurePairDialog(QDialog):
    """Per-pair layer selection.

    Two columns (Donor / Donor+Acceptor). Each column has dropdowns for
    mask, phasor, and lifetime layers — plus segmentation when single-cell
    mode is on. Dropdowns are unfiltered: the user can pick any
    ``/masks/`` entry for both the mask and the phasor field; helper text
    nudges toward the ``_mask`` / ``_phasor`` suffix convention but does
    not enforce it.
    """

    def __init__(
        self,
        parent: QWidget,
        *,
        donor_path: Path,
        da_path: Path,
        single_cell: bool,
        initial: _PairConfig,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Configure pair: {donor_path.name} ↔ {da_path.name}")
        self.setMinimumWidth(720)
        cap_to_screen(self)

        self._single_cell = single_cell
        self._initial = initial

        # Read live layer lists once.
        try:
            donor_store = DatasetStore(donor_path)
            self._donor_masks = donor_store.list_masks()
            self._donor_lifetimes = list_lifetime_channel_names(donor_store)
            self._donor_labels = donor_store.list_labels()
        except Exception as exc:  # noqa: BLE001
            self._donor_masks = []
            self._donor_lifetimes = []
            self._donor_labels = []
            QMessageBox.warning(
                self, "Donor read failed", f"Could not read donor layers: {exc}"
            )
        try:
            da_store = DatasetStore(da_path)
            self._da_masks = da_store.list_masks()
            self._da_lifetimes = list_lifetime_channel_names(da_store)
            self._da_labels = da_store.list_labels()
        except Exception as exc:  # noqa: BLE001
            self._da_masks = []
            self._da_lifetimes = []
            self._da_labels = []
            QMessageBox.warning(
                self, "DA read failed", f"Could not read DA layers: {exc}"
            )

        # Build the form.
        outer = QVBoxLayout(self)
        content = QWidget()
        layout = QHBoxLayout(content)

        self._donor_widgets = self._build_side(
            "Donor", self._donor_masks, self._donor_lifetimes, self._donor_labels
        )
        self._da_widgets = self._build_side(
            "Donor + Acceptor",
            self._da_masks,
            self._da_lifetimes,
            self._da_labels,
        )
        layout.addWidget(self._donor_widgets["box"], 1)
        layout.addWidget(self._da_widgets["box"], 1)

        outer.addWidget(wrap_in_scroll(content), 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._ok_btn = buttons.button(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._preload_from_initial()
        self._wire_signals_for_validation()
        self._update_ok_enabled()

    def _build_side(
        self,
        title: str,
        masks: list[str],
        lifetimes: list[str],
        labels: list[str],
    ) -> dict[str, Any]:
        box = QGroupBox(title)
        form = QFormLayout(box)

        mask_combo = QComboBox()
        for m in masks:
            mask_combo.addItem(m)
        form.addRow("Mask layer:", mask_combo)
        mask_hint = QLabel("Select a layer whose name ends in '_mask'.")
        mask_hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", mask_hint)

        phasor_combo = QComboBox()
        for m in masks:  # same source list, helper text differs
            phasor_combo.addItem(m)
        form.addRow("Phasor mask:", phasor_combo)
        phasor_hint = QLabel("Select a layer whose name ends in '_phasor'.")
        phasor_hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", phasor_hint)

        lifetime_combo = QComboBox()
        for ln in lifetimes:
            lifetime_combo.addItem(ln)
        form.addRow("Lifetime channel:", lifetime_combo)
        lifetime_hint = QLabel(
            "Select a derived /intensity channel ending in '_lifetime'."
        )
        lifetime_hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", lifetime_hint)

        seg_combo: QComboBox | None = None
        if self._single_cell:
            seg_combo = QComboBox()
            for n in labels:
                seg_combo.addItem(n)
            form.addRow("Segmentation:", seg_combo)
            seg_hint = QLabel("Select a segmentation layer from /labels/.")
            seg_hint.setStyleSheet("color: gray; font-size: 11px;")
            form.addRow("", seg_hint)

        return {
            "box": box,
            "mask": mask_combo,
            "phasor": phasor_combo,
            "lifetime": lifetime_combo,
            "segmentation": seg_combo,
        }

    def _preload_from_initial(self) -> None:
        def _restore(combo: QComboBox, value: str, fallback_suffix: str) -> None:
            if value:
                idx = combo.findText(value)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                    return
            # Fall back to the first item ending in the expected suffix.
            for i in range(combo.count()):
                if combo.itemText(i).endswith(fallback_suffix):
                    combo.setCurrentIndex(i)
                    return

        _restore(self._donor_widgets["mask"], self._initial.donor_mask, "_mask")
        _restore(
            self._donor_widgets["phasor"], self._initial.donor_phasor, "_phasor"
        )
        _restore(
            self._donor_widgets["lifetime"],
            self._initial.donor_lifetime,
            "_lifetime",
        )
        _restore(self._da_widgets["mask"], self._initial.da_mask, "_mask")
        _restore(
            self._da_widgets["phasor"], self._initial.da_phasor, "_phasor"
        )
        _restore(
            self._da_widgets["lifetime"], self._initial.da_lifetime, "_lifetime"
        )
        if self._single_cell:
            ds = self._donor_widgets["segmentation"]
            if ds is not None:
                _restore(ds, self._initial.donor_segmentation, "")
            ads = self._da_widgets["segmentation"]
            if ads is not None:
                _restore(ads, self._initial.da_segmentation, "")

    def _wire_signals_for_validation(self) -> None:
        for side in (self._donor_widgets, self._da_widgets):
            for key in ("mask", "phasor", "lifetime", "segmentation"):
                combo = side.get(key)
                if isinstance(combo, QComboBox):
                    combo.currentIndexChanged.connect(self._update_ok_enabled)

    def _update_ok_enabled(self) -> None:
        if self._ok_btn is None:
            return

        def _has(side: dict[str, Any], key: str) -> bool:
            combo = side.get(key)
            return isinstance(combo, QComboBox) and combo.currentText() != ""

        ok = all(
            _has(side, k)
            for side in (self._donor_widgets, self._da_widgets)
            for k in ("mask", "phasor", "lifetime")
        )
        if self._single_cell:
            ok = ok and all(
                _has(side, "segmentation")
                for side in (self._donor_widgets, self._da_widgets)
            )
        self._ok_btn.setEnabled(ok)

    def apply_to(self, cfg: _PairConfig) -> None:
        """Write the dropdown selections back into ``cfg``."""

        def _text(side: dict[str, Any], key: str) -> str:
            combo = side.get(key)
            return combo.currentText() if isinstance(combo, QComboBox) else ""

        cfg.donor_mask = _text(self._donor_widgets, "mask")
        cfg.donor_phasor = _text(self._donor_widgets, "phasor")
        cfg.donor_lifetime = _text(self._donor_widgets, "lifetime")
        cfg.da_mask = _text(self._da_widgets, "mask")
        cfg.da_phasor = _text(self._da_widgets, "phasor")
        cfg.da_lifetime = _text(self._da_widgets, "lifetime")
        if self._single_cell:
            cfg.donor_segmentation = _text(self._donor_widgets, "segmentation")
            cfg.da_segmentation = _text(self._da_widgets, "segmentation")
