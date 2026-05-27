"""Automated phasor-masks workflow dialog.

Modal ``QDialog`` that drives the Automated Phasor-Masks Workflow on the
main thread:

- The user queues one or more ``.h5`` datasets, picks one or more channels
  (auto-filtered to channels that are (a) present in every queued dataset,
  (b) have ``/decay/<ch>`` on disk in every dataset, and (c) do **not**
  cause a collision with ``<channel><suffix>`` on any dataset), tweaks the
  three intensity thresholds and two mask-name suffixes, and clicks Start.
- Start disables the form, opens a ``Qt.WindowModal`` ``QProgressDialog``,
  and runs :func:`batch_fit_phasor_masks` synchronously. The progress
  callback updates the dialog label per dataset and
  ``QApplication.processEvents()`` lets cancel clicks register between
  items.
- After the loop, the dialog emits **one** conditional
  ``session.refresh_resource_lists(mask_names=...)`` if and only if the
  currently-loaded dataset (via ``Path.resolve()``) is among the processed
  paths, builds a summary ``QMessageBox``, and accepts.

This dialog mirrors :class:`percell4.gui.flim_fret_dialog.FlimFretDialog`
in shape — self-driving, no ``BaseWorkflowRunner``, no ``WorkflowConfig``,
no ``RunMetadata``. It is an **Action** in the GUI-element-classification
taxonomy: it reads no session state for selection purposes, and only writes
session state via the single end-of-run ``refresh_resource_lists`` push.

See ``docs/plans/2026-05-27-001-feat-phasor-masks-workflow-plan.md`` (U3).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qtpy.QtCore import QSettings, Qt
from qtpy.QtWidgets import (
    QApplication,
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
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.application.use_cases.batch_compute_phasor import (
    BatchPhasorItemResult,
    BatchPhasorReport,
)
from percell4.application.use_cases.batch_fit_phasor_masks import (
    batch_fit_phasor_masks,
)
from percell4.gui._dialog_utils import cap_to_screen, wrap_in_scroll
from percell4.store import DatasetStore
from percell4.workflows.channels import intersect_channels

logger = logging.getLogger(__name__)

# QSettings keys — same org/app convention as the FLIM-FRET dialog.
_QSETTINGS_ORG = "LeeLabPerCell4"
_QSETTINGS_APP = "PerCell4"

_QS_T_FIT = "phasor_masks/t_fit"
_QS_T_MASK_A = "phasor_masks/t_mask_a"
_QS_T_MASK_B = "phasor_masks/t_mask_b"
_QS_SUFFIX_A = "phasor_masks/suffix_a"
_QS_SUFFIX_B = "phasor_masks/suffix_b"

# Hard-coded defaults; surfaced when QSettings has no prior value.
_DEFAULT_T_FIT = 10.0
_DEFAULT_T_MASK_A = 0.0
_DEFAULT_T_MASK_B = 5.0
_DEFAULT_SUFFIX_A = "_phasor_1"
_DEFAULT_SUFFIX_B = "_phasor_5"


@dataclass
class _PendingDataset:
    """One queued ``.h5`` dataset.

    ``channel_names`` is the union of /metadata.channel_names; the picker
    independently intersects this with ``store.list_groups("decay")`` to
    arrive at the channels eligible for fitting.
    """

    h5_path: Path
    channel_names: list[str]
    decay_channels: list[str]


def _normalize_channel_name(name: Any) -> str:
    """Decode bytes → str; otherwise stringify.

    Mirrors ``WorkflowConfigDialog._read_h5_channels`` (lines 1037-1051):
    h5py returns numpy arrays of bytes for sequence attrs on some platforms,
    and ``np.bytes_`` instances on others. Decode utf-8 with replace; fall
    back to ``str()`` for anything else.
    """
    if isinstance(name, bytes):
        return name.decode("utf-8", errors="replace")
    return str(name)


class PhasorMasksDialog(QDialog):
    """Self-driving modal dialog for the automated phasor-masks workflow.

    Optional ``orchestrator`` parameter exists for testing — production
    callers don't pass it. The default is the canonical use case
    :func:`batch_fit_phasor_masks`.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        orchestrator: Callable[..., BatchPhasorReport] = batch_fit_phasor_masks,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Automated phasor-masks workflow")
        self.setMinimumWidth(680)
        self.resize(760, 640)
        cap_to_screen(self)

        # Host (LauncherWindow) is the parent; cache for end-of-run refresh.
        self._host = parent
        self._orchestrator = orchestrator

        # Pure-Python state.
        self._pending_datasets: list[_PendingDataset] = []
        self._eligible_channels: list[str] = []
        self._collision_reasons: list[str] = []
        self._last_report: BatchPhasorReport | None = None
        self._last_overwrites: list[tuple[Path, str]] = []
        self._cancelled: bool = False

        # Widgets — assigned in _build_ui.
        self._dataset_list: QListWidget | None = None
        self._channel_list: QListWidget | None = None
        self._channel_status: QLabel | None = None
        self._t_fit_spin: QDoubleSpinBox | None = None
        self._t_mask_a_spin: QDoubleSpinBox | None = None
        self._t_mask_b_spin: QDoubleSpinBox | None = None
        self._suffix_a_edit: QLineEdit | None = None
        self._suffix_b_edit: QLineEdit | None = None
        self._start_btn: QPushButton | None = None
        self._cancel_btn: QPushButton | None = None
        # Form controls that get bulk-disabled during the run.
        self._form_controls: list[QWidget] = []

        self._build_ui()
        self._restore_qsettings()
        # Initial pass: nothing queued, picker empty, Start disabled.
        self._refresh_channel_picker()
        self._update_start_enabled()

    # ── Public API ────────────────────────────────────────

    @property
    def last_report(self) -> BatchPhasorReport | None:
        """The most recent :class:`BatchPhasorReport`, or None if the
        dialog was rejected without running."""
        return self._last_report

    @property
    def cancelled(self) -> bool:
        """True if the user pressed Cancel on the QProgressDialog
        mid-run."""
        return self._cancelled

    # ── UI construction ──────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setSpacing(10)

        body.addWidget(self._build_section_datasets())
        body.addWidget(self._build_section_channels())
        body.addWidget(self._build_section_params())
        body.addStretch()

        outer.addWidget(wrap_in_scroll(content), 1)

        # Pinned button row outside the scroll area.
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._start_btn = QPushButton("Start")
        self._start_btn.setToolTip(
            "Phasor maps cached from prior runs are reused as-is. To "
            "recompute, run `python -m percell4.interfaces.cli.batch_phasor "
            "--overwrite` first."
        )
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._cancel_btn)
        outer.addLayout(btn_row)

    def _build_section_datasets(self) -> QGroupBox:
        box = QGroupBox("1. Datasets")
        layout = QVBoxLayout(box)

        self._dataset_list = QListWidget()
        self._dataset_list.setSelectionMode(QListWidget.ExtendedSelection)
        self._dataset_list.setMinimumHeight(120)
        layout.addWidget(self._dataset_list)

        btn_row = QHBoxLayout()
        add_files_btn = QPushButton("Add .h5 files…")
        add_files_btn.clicked.connect(self._on_add_h5_files)
        add_folder_btn = QPushButton("Add folder of .h5…")
        add_folder_btn.clicked.connect(self._on_add_h5_folder)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._on_remove_selected)
        btn_row.addWidget(add_files_btn)
        btn_row.addWidget(add_folder_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._form_controls.extend([add_files_btn, add_folder_btn, remove_btn])
        return box

    def _build_section_channels(self) -> QGroupBox:
        box = QGroupBox("2. Channels")
        layout = QVBoxLayout(box)

        self._channel_list = QListWidget()
        self._channel_list.setMinimumHeight(100)
        # Use itemChanged for checkbox-driven multi-select.
        self._channel_list.itemChanged.connect(self._on_channel_item_changed)
        layout.addWidget(self._channel_list)

        self._channel_status = QLabel("Add datasets to populate the channel picker.")
        self._channel_status.setWordWrap(True)
        self._channel_status.setStyleSheet("color: #888;")
        layout.addWidget(self._channel_status)

        self._form_controls.append(self._channel_list)
        return box

    def _build_section_params(self) -> QGroupBox:
        box = QGroupBox("3. Phasor mask parameters")
        form = QFormLayout(box)

        self._t_fit_spin = QDoubleSpinBox()
        self._t_fit_spin.setRange(0.0, 65535.0)
        self._t_fit_spin.setSingleStep(1.0)
        self._t_fit_spin.setValue(_DEFAULT_T_FIT)
        self._t_fit_spin.setToolTip(
            "Only pixels with intensity ≥ this value are used to fit "
            "the ellipse. Higher = cleaner ellipse on noisier data."
        )
        self._t_fit_spin.valueChanged.connect(self._on_editable_changed)
        form.addRow("Fit threshold (intensity):", self._t_fit_spin)

        self._t_mask_a_spin = QDoubleSpinBox()
        self._t_mask_a_spin.setRange(0.0, 65535.0)
        self._t_mask_a_spin.setSingleStep(1.0)
        self._t_mask_a_spin.setValue(_DEFAULT_T_MASK_A)
        self._t_mask_a_spin.setToolTip(
            "Intensity threshold for the first output mask. Lower = larger "
            "coverage."
        )
        self._t_mask_a_spin.valueChanged.connect(self._on_editable_changed)
        form.addRow("Mask A threshold (permissive):", self._t_mask_a_spin)

        self._t_mask_b_spin = QDoubleSpinBox()
        self._t_mask_b_spin.setRange(0.0, 65535.0)
        self._t_mask_b_spin.setSingleStep(1.0)
        self._t_mask_b_spin.setValue(_DEFAULT_T_MASK_B)
        self._t_mask_b_spin.setToolTip(
            "Intensity threshold for the second output mask. Higher = "
            "tighter, higher-confidence coverage."
        )
        self._t_mask_b_spin.valueChanged.connect(self._on_editable_changed)
        form.addRow("Mask B threshold (conservative):", self._t_mask_b_spin)

        self._suffix_a_edit = QLineEdit(_DEFAULT_SUFFIX_A)
        self._suffix_a_edit.setToolTip(
            "Mask written as `<channel>{suffix}` in the dataset's /masks/ "
            "group."
        )
        self._suffix_a_edit.textChanged.connect(self._on_editable_changed)
        form.addRow("Mask A suffix:", self._suffix_a_edit)

        self._suffix_b_edit = QLineEdit(_DEFAULT_SUFFIX_B)
        self._suffix_b_edit.setToolTip(
            "Mask written as `<channel>{suffix}` in the dataset's /masks/ "
            "group."
        )
        self._suffix_b_edit.textChanged.connect(self._on_editable_changed)
        form.addRow("Mask B suffix:", self._suffix_b_edit)

        self._form_controls.extend([
            self._t_fit_spin,
            self._t_mask_a_spin,
            self._t_mask_b_spin,
            self._suffix_a_edit,
            self._suffix_b_edit,
        ])
        return box

    # ── QSettings persistence ────────────────────────────

    def _restore_qsettings(self) -> None:
        qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        assert self._t_fit_spin is not None
        assert self._t_mask_a_spin is not None
        assert self._t_mask_b_spin is not None
        assert self._suffix_a_edit is not None
        assert self._suffix_b_edit is not None

        self._t_fit_spin.setValue(
            float(qs.value(_QS_T_FIT, _DEFAULT_T_FIT, type=float))
        )
        self._t_mask_a_spin.setValue(
            float(qs.value(_QS_T_MASK_A, _DEFAULT_T_MASK_A, type=float))
        )
        self._t_mask_b_spin.setValue(
            float(qs.value(_QS_T_MASK_B, _DEFAULT_T_MASK_B, type=float))
        )
        self._suffix_a_edit.setText(
            str(qs.value(_QS_SUFFIX_A, _DEFAULT_SUFFIX_A, type=str))
        )
        self._suffix_b_edit.setText(
            str(qs.value(_QS_SUFFIX_B, _DEFAULT_SUFFIX_B, type=str))
        )

    def _save_qsettings(self) -> None:
        qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        assert self._t_fit_spin is not None
        assert self._t_mask_a_spin is not None
        assert self._t_mask_b_spin is not None
        assert self._suffix_a_edit is not None
        assert self._suffix_b_edit is not None
        qs.setValue(_QS_T_FIT, self._t_fit_spin.value())
        qs.setValue(_QS_T_MASK_A, self._t_mask_a_spin.value())
        qs.setValue(_QS_T_MASK_B, self._t_mask_b_spin.value())
        qs.setValue(_QS_SUFFIX_A, self._suffix_a_edit.text())
        qs.setValue(_QS_SUFFIX_B, self._suffix_b_edit.text())

    # ── Dataset queue handlers ───────────────────────────

    def _on_add_h5_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add .h5 datasets",
            "",
            "HDF5 files (*.h5 *.hdf5)",
        )
        if not paths:
            return
        self._add_h5_paths([Path(p) for p in paths])

    def _on_add_h5_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Add folder of .h5 datasets", ""
        )
        if not folder:
            return
        folder_path = Path(folder)
        h5_files = sorted(folder_path.glob("*.h5")) + sorted(
            folder_path.glob("*.hdf5")
        )
        if not h5_files:
            return
        self._add_h5_paths(h5_files)

    def _on_remove_selected(self) -> None:
        assert self._dataset_list is not None
        rows = sorted(
            {self._dataset_list.row(it) for it in self._dataset_list.selectedItems()},
            reverse=True,
        )
        for r in rows:
            if 0 <= r < len(self._pending_datasets):
                del self._pending_datasets[r]
        self._refresh_dataset_list()
        self._refresh_channel_picker()
        self._update_start_enabled()

    def _add_h5_paths(self, paths: list[Path]) -> None:
        """Add datasets to the queue (deduped by resolved path)."""
        assert self._dataset_list is not None
        existing = {pd.h5_path for pd in self._pending_datasets}
        for path in paths:
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if not resolved.is_file():
                continue
            if resolved in existing:
                continue
            try:
                store = DatasetStore(resolved)
                meta = store.metadata
                raw = meta.get("channel_names", []) or []
                channel_names = [_normalize_channel_name(n) for n in raw]
                decay_channels = [
                    _normalize_channel_name(n) for n in store.list_groups("decay")
                ]
            except Exception:
                logger.exception(
                    "phasor-masks dialog: failed to read %s", resolved
                )
                continue
            self._pending_datasets.append(
                _PendingDataset(
                    h5_path=resolved,
                    channel_names=channel_names,
                    decay_channels=decay_channels,
                )
            )
            existing.add(resolved)

        self._refresh_dataset_list()
        self._refresh_channel_picker()
        self._update_start_enabled()

    def _refresh_dataset_list(self) -> None:
        assert self._dataset_list is not None
        self._dataset_list.clear()
        for pd in self._pending_datasets:
            QListWidgetItem(str(pd.h5_path), self._dataset_list)

    # ── Channel picker refresh ───────────────────────────

    def _refresh_channel_picker(self) -> None:
        """Recompute the eligible-channels list and rebuild the picker.

        Algorithm:
          1. For each queued dataset, gather
             ``set(channel_names) ∩ set(decay_channels)``.
          2. Intersect across datasets via :func:`intersect_channels`.
          3. **Collision pre-filter:** drop any channel ``ch`` for which
             ``f"{ch}{suffix_a}"`` or ``f"{ch}{suffix_b}"`` exists in any
             dataset's ``channel_names`` (the add-mask name-collision learning).
          4. Surface any non-empty exclusion reasons in
             ``self._channel_status``.

        Preserves prior selection where possible: any channel that survives
        the rebuild stays checked.
        """
        assert self._channel_list is not None
        assert self._channel_status is not None

        # Capture prior checked set so we can restore it.
        prior_checked: set[str] = set()
        for i in range(self._channel_list.count()):
            item = self._channel_list.item(i)
            if item.checkState() == Qt.Checked:
                prior_checked.add(item.text())

        # Block itemChanged so the rebuild doesn't fire the slot N times.
        self._channel_list.blockSignals(True)
        try:
            self._channel_list.clear()

            if not self._pending_datasets:
                self._eligible_channels = []
                self._collision_reasons = []
                self._channel_status.setText(
                    "Add datasets to populate the channel picker."
                )
                return

            sources: list[tuple[str, list[str]]] = []
            for pd in self._pending_datasets:
                with_decay = sorted(
                    set(pd.channel_names) & set(pd.decay_channels)
                )
                sources.append((str(pd.h5_path), with_decay))

            intersection, _outliers = intersect_channels(sources)

            # Collision pre-filter.
            suffix_a = self._suffix_a_edit.text() if self._suffix_a_edit else ""
            suffix_b = self._suffix_b_edit.text() if self._suffix_b_edit else ""
            collision_reasons: list[str] = []
            eligible: list[str] = []
            for ch in intersection:
                collision_with: str | None = None
                for pd in self._pending_datasets:
                    names = set(pd.channel_names)
                    if suffix_a and f"{ch}{suffix_a}" in names:
                        collision_with = f"{ch}{suffix_a}"
                        break
                    if suffix_b and f"{ch}{suffix_b}" in names:
                        collision_with = f"{ch}{suffix_b}"
                        break
                if collision_with is not None:
                    collision_reasons.append(
                        f"Excluded '{ch}': would overwrite channel "
                        f"'{collision_with}'"
                    )
                else:
                    eligible.append(ch)

            self._eligible_channels = eligible
            self._collision_reasons = collision_reasons

            for ch in eligible:
                item = QListWidgetItem(ch)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.Checked if ch in prior_checked else Qt.Unchecked
                )
                self._channel_list.addItem(item)

            if not eligible and not collision_reasons:
                self._channel_status.setText(
                    "No channels are present (with decay) in every selected "
                    "dataset."
                )
            elif not eligible and collision_reasons:
                self._channel_status.setText(
                    "No channels remain after collision filter:\n"
                    + "\n".join(collision_reasons)
                )
            elif collision_reasons:
                self._channel_status.setText(
                    "\n".join(collision_reasons)
                )
            else:
                self._channel_status.setText(
                    f"{len(eligible)} channel(s) eligible. "
                    "Check one or more to include in the run."
                )
        finally:
            self._channel_list.blockSignals(False)

    # ── Signal slots ────────────────────────────────────

    def _on_editable_changed(self, *_args: Any) -> None:
        """Slot for every editable widget's user-edit signal.

        Connected from threshold spinboxes (`valueChanged`) and suffix
        line edits (`textChanged`). Suffix changes re-run the collision
        pre-filter; threshold changes only affect Start-enabled gating but
        we run both for symmetry and resilience (per the
        ``qt-wire-user-edit-signals`` learning).
        """
        self._refresh_channel_picker()
        self._update_start_enabled()

    def _on_channel_item_changed(self, _item: QListWidgetItem) -> None:
        self._update_start_enabled()

    # ── Start-enabled gating ─────────────────────────────

    def _selected_channels(self) -> list[str]:
        assert self._channel_list is not None
        out: list[str] = []
        for i in range(self._channel_list.count()):
            item = self._channel_list.item(i)
            if item.checkState() == Qt.Checked:
                out.append(item.text())
        return out

    def _update_start_enabled(self) -> None:
        if self._start_btn is None:
            return
        assert self._suffix_a_edit is not None
        assert self._suffix_b_edit is not None
        suffix_a = self._suffix_a_edit.text()
        suffix_b = self._suffix_b_edit.text()
        enabled = (
            bool(self._pending_datasets)
            and bool(self._selected_channels())
            and bool(suffix_a)
            and bool(suffix_b)
            and suffix_a != suffix_b
        )
        self._start_btn.setEnabled(enabled)

    # ── Run mechanics ────────────────────────────────────

    def _on_start_clicked(self) -> None:
        """Capture frozen params, run the use case with a progress dialog,
        emit a single conditional refresh, show a summary, accept.

        Mirrors :meth:`FlimFretDialog._on_start_clicked` (line 674).
        """
        assert self._t_fit_spin is not None
        assert self._t_mask_a_spin is not None
        assert self._t_mask_b_spin is not None
        assert self._suffix_a_edit is not None
        assert self._suffix_b_edit is not None

        dataset_paths = [pd.h5_path for pd in self._pending_datasets]
        channels = tuple(self._selected_channels())
        t_fit = float(self._t_fit_spin.value())
        t_mask_a = float(self._t_mask_a_spin.value())
        t_mask_b = float(self._t_mask_b_spin.value())
        suffix_a = self._suffix_a_edit.text()
        suffix_b = self._suffix_b_edit.text()

        # Disable the form controls during the run; the QProgressDialog has
        # its own Cancel button.
        for w in self._form_controls:
            w.setEnabled(False)
        if self._start_btn is not None:
            self._start_btn.setEnabled(False)

        # Probe pre-existing masks per dataset for overwrite tracking.
        pre_existing: dict[Path, set[str]] = {}
        for pd in self._pending_datasets:
            try:
                store = DatasetStore(pd.h5_path)
                pre_existing[pd.h5_path] = set(store.list_groups("masks"))
            except Exception:
                pre_existing[pd.h5_path] = set()

        n_total = len(dataset_paths)
        progress = QProgressDialog(
            "Phasor-masks workflow",
            "Cancel",
            0,
            n_total,
            self,
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        overwrites: list[tuple[Path, str]] = []
        completed = {"n": 0}

        def progress_cb(item: BatchPhasorItemResult) -> None:
            completed["n"] += 1
            n = completed["n"]
            progress.setValue(n)
            progress.setLabelText(
                f"Dataset {n}/{n_total}: {item.h5_path.name} — {item.status}"
            )
            # Track overwrites: any mask name that pre-existed AND was
            # rewritten by this item.
            pre = pre_existing.get(item.h5_path, set())
            for ch in item.processed:
                name_a = f"{ch}{suffix_a}"
                name_b = f"{ch}{suffix_b}"
                if name_a in pre:
                    overwrites.append((item.h5_path, name_a))
                if name_b in pre:
                    overwrites.append((item.h5_path, name_b))
            QApplication.processEvents()

        def cancel_check() -> bool:
            return bool(progress.wasCanceled())

        try:
            report = self._orchestrator(
                dataset_paths,
                channels=channels,
                t_fit=t_fit,
                t_mask_a=t_mask_a,
                t_mask_b=t_mask_b,
                suffix_a=suffix_a,
                suffix_b=suffix_b,
                ensure_phasor=True,
                progress_callback=progress_cb,
                cancel_check=cancel_check,
            )
        except Exception as exc:  # noqa: BLE001 — surface as a critical box
            logger.exception("phasor-masks workflow raised out of orchestrator")
            progress.close()
            QMessageBox.critical(
                self,
                "Phasor-masks workflow",
                f"The workflow raised an unexpected error:\n\n{exc}",
            )
            for w in self._form_controls:
                w.setEnabled(True)
            self._update_start_enabled()
            return

        # Capture cancel state BEFORE `setValue(maximum)` — Qt's
        # QProgressDialog implicitly resets `wasCanceled` when the value
        # hits the maximum.
        cancelled = bool(progress.wasCanceled())
        progress.setValue(n_total)
        progress.close()

        self._last_report = report
        self._last_overwrites = overwrites
        self._cancelled = cancelled
        self._save_qsettings()

        # Conditional, one-shot session refresh.
        self._maybe_refresh_session(dataset_paths)

        # Summary message box (icon depends on outcome).
        self._show_summary(report, overwrites, cancelled)

        # Accept on completion — failures are reported, not abortive.
        self.accept()

    def _maybe_refresh_session(self, dataset_paths: list[Path]) -> None:
        """Emit one ``session.refresh_resource_lists`` if the active
        dataset is among the processed paths.

        Uses ``Path.resolve()`` on both sides to handle ``./`` prefixes,
        symlinks, and case-insensitive filesystem quirks.
        """
        host = self._host
        if host is None or not hasattr(host, "get_session"):
            return
        try:
            session = host.get_session()
        except Exception:
            logger.exception("phasor-masks dialog: host.get_session raised")
            return
        if session is None:
            return
        dataset = getattr(session, "dataset", None)
        if dataset is None:
            return
        active_path = getattr(dataset, "path", None)
        if active_path is None:
            return
        try:
            active_resolved = Path(active_path).resolve()
        except OSError:
            active_resolved = Path(active_path)
        processed_resolved: list[Path] = []
        for p in dataset_paths:
            try:
                processed_resolved.append(p.resolve())
            except OSError:
                processed_resolved.append(p)
        if active_resolved not in processed_resolved:
            return
        try:
            store = DatasetStore(active_resolved)
            mask_names = store.list_groups("masks")
        except Exception:
            logger.exception(
                "phasor-masks dialog: re-listing masks for refresh failed"
            )
            return
        try:
            session.refresh_resource_lists(mask_names=mask_names)
        except Exception:
            logger.exception(
                "phasor-masks dialog: session.refresh_resource_lists raised"
            )

    def _show_summary(
        self,
        report: BatchPhasorReport,
        overwrites: list[tuple[Path, str]],
        cancelled: bool,
    ) -> None:
        n_total = len(report.items)
        n_succeeded = sum(1 for it in report.items if it.status == "succeeded")
        n_partial = sum(1 for it in report.items if it.status == "partial")
        n_failed = sum(1 for it in report.items if it.status == "failed")
        n_skipped = sum(
            1 for it in report.items if it.status == "skipped_no_changes"
        )

        if cancelled:
            text = (
                f"Cancelled after {n_total} dataset(s): {n_succeeded} "
                f"succeeded, {n_partial} partial, {n_failed} failed."
            )
        else:
            text = (
                f"Processed {n_total} dataset(s): {n_succeeded} succeeded, "
                f"{n_partial} partial, {n_failed} failed."
            )

        # Detailed text: per-dataset breakdown + overwrite list.
        detail_lines: list[str] = []
        for it in report.items:
            line = f"{it.h5_path.name}: {it.status}"
            if it.processed:
                line += f" — processed: {', '.join(it.processed)}"
            if it.skipped:
                line += " — skipped: " + ", ".join(
                    f"{k}={v}" for k, v in it.skipped.items()
                )
            if it.errors:
                line += " — errors: " + ", ".join(
                    f"{k}={v}" for k, v in it.errors.items()
                )
            if it.error:
                line += f" — dataset error: {it.error}"
            detail_lines.append(line)

        if overwrites:
            ds_count = len({p for p, _ in overwrites})
            detail_lines.append("")
            detail_lines.append(
                f"Overwrote {len(overwrites)} existing mask(s) in "
                f"{ds_count} dataset(s):"
            )
            for p, name in overwrites:
                detail_lines.append(f"  {p.name}: {name}")

        msg = QMessageBox(self)
        msg.setWindowTitle("Phasor-masks workflow")
        # Warning icon when anything went sideways; Information when clean.
        if n_partial > 0 or n_failed > 0 or cancelled:
            msg.setIcon(QMessageBox.Warning)
        else:
            msg.setIcon(QMessageBox.Information)
        msg.setText(text)
        if detail_lines:
            msg.setDetailedText("\n".join(detail_lines))
        # Unused locals (n_skipped) is intentionally kept for grep/future use.
        _ = n_skipped
        msg.exec_()
