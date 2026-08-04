"""Batch "Dilute phase mask from mask" workflow dialog.

Modal ``QDialog`` that drives :func:`batch_dilute_from_mask` on the main
thread:

- The user queues one or more ``.h5`` datasets, picks one **common mask
  name** and one **common segmentation name** (each offered only when it is
  present in *every* queued dataset), sets an **expansion radius (px)**, and
  enters an **output mask name**. The output name is collision-checked up
  front against every queued dataset's channels / masks / labels.
- Start disables the form, opens a ``Qt.WindowModal`` ``QProgressDialog``,
  and runs :func:`batch_dilute_from_mask` synchronously. The progress
  callback updates the dialog label per dataset and
  ``QApplication.processEvents()`` lets cancel clicks register between
  items.
- After the loop, the dialog emits **one** conditional
  ``session.refresh_resource_lists(mask_names=...)`` if and only if the
  currently-loaded dataset (via ``Path.resolve()``) is among the processed
  paths, builds a per-dataset summary ``QMessageBox``, and accepts.

This dialog mirrors :class:`percell4.gui.phasor_masks_dialog.PhasorMasksDialog`
in shape — self-driving, no ``BaseWorkflowRunner``. It is an **Action** in
the GUI-element-classification taxonomy: dialog pickers are dialog-local
config (no session mutation during configuration), and the only session
touch is the single end-of-run ``refresh_resource_lists`` push (R6/D5).

See ``docs/plans/2026-06-28-002-feat-dilute-phase-mask-from-mask-plan.md``
(U3).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qtpy.QtCore import QSignalBlocker, Qt
from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.application.use_cases.batch_dilute_from_mask import (
    DiluteItemResult,
    DiluteReport,
    batch_dilute_from_mask,
)
from percell4.gui._dialog_utils import (
    cap_to_screen,
    center_on_screen,
    detach_window,
    wrap_in_scroll,
)
from percell4.gui.settings import app_settings
from percell4.io.paths import drop_sidecars, scan_files
from percell4.store import DatasetStore
from percell4.workflows.channels import intersect_channels

logger = logging.getLogger(__name__)

# QSettings keys — same org/app convention as the phasor-masks dialog.

_QS_RADIUS = "dilute_from_mask/radius_px"
_QS_OUTPUT = "dilute_from_mask/output_name"

# Hard-coded defaults; surfaced when QSettings has no prior value.
_DEFAULT_RADIUS = 5
_DEFAULT_OUTPUT = "dilute"

_RADIUS_MIN = 0
_RADIUS_MAX = 50


@dataclass
class _PendingDataset:
    """One queued ``.h5`` dataset.

    ``channel_names`` / ``mask_names`` / ``label_names`` are this file's own
    ``/metadata`` channel names, ``list_groups("masks")``, and
    ``list_groups("labels")`` — read once at add time. The mask / seg pickers
    intersect these *per dataset* (never a shared set); the output-name
    collision check tests against all three namespaces (D7, collision
    learning).
    """

    h5_path: Path
    channel_names: list[str] = field(default_factory=list)
    mask_names: list[str] = field(default_factory=list)
    label_names: list[str] = field(default_factory=list)


class DiluteFromMaskDialog(QDialog):
    """Self-driving modal dialog for the batch dilute-from-mask workflow.

    Optional ``orchestrator`` parameter exists for testing — production
    callers don't pass it. The default is the canonical use case
    :func:`batch_dilute_from_mask`.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        orchestrator: Callable[..., DiluteReport] = batch_dilute_from_mask,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dilute phase mask from mask")
        self.setMinimumWidth(620)
        self.resize(700, 560)
        cap_to_screen(self)
        detach_window(self)
        center_on_screen(self)

        # Host (LauncherWindow) is the parent; cache for end-of-run refresh.
        self._host = parent
        self._orchestrator = orchestrator

        # Pure-Python state.
        self._pending_datasets: list[_PendingDataset] = []
        self._last_report: DiluteReport | None = None
        self._cancelled: bool = False

        # Widgets — assigned in _build_ui.
        self._dataset_list: QListWidget | None = None
        self._mask_combo: QComboBox | None = None
        self._seg_combo: QComboBox | None = None
        self._picker_status: QLabel | None = None
        self._radius_spin: QSpinBox | None = None
        self._output_edit: QLineEdit | None = None
        self._output_status: QLabel | None = None
        self._start_btn: QPushButton | None = None
        self._cancel_btn: QPushButton | None = None
        # Form controls that get bulk-disabled during the run.
        self._form_controls: list[QWidget] = []

        self._build_ui()
        self._restore_qsettings()
        # Initial pass: nothing queued, pickers empty, Start disabled.
        self._refresh_pickers()
        self._update_start_enabled()

    # ── Public API ────────────────────────────────────────

    @property
    def last_report(self) -> DiluteReport | None:
        """The most recent :class:`DiluteReport`, or None if the dialog was
        rejected without running."""
        return self._last_report

    @property
    def cancelled(self) -> bool:
        """True if the user pressed Cancel on the QProgressDialog mid-run."""
        return self._cancelled

    # ── UI construction ──────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setSpacing(10)

        body.addWidget(self._build_section_datasets())
        body.addWidget(self._build_section_params())
        body.addStretch()

        outer.addWidget(wrap_in_scroll(content), 1)

        # Pinned button row outside the scroll area.
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._start_btn = QPushButton("Start")
        self._start_btn.setToolTip(
            "Grow the chosen mask by the radius and invert it within the "
            "chosen segmentation, writing one new mask per dataset."
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
        self._dataset_list.setSelectionMode(QListWidget.NoSelection)
        self._dataset_list.setMinimumHeight(120)
        layout.addWidget(self._dataset_list)

        btn_row = QHBoxLayout()
        add_files_btn = QPushButton("Add .h5 files…")
        add_files_btn.clicked.connect(self._on_add_h5_files)
        add_folder_btn = QPushButton("Add folder of .h5…")
        add_folder_btn.clicked.connect(self._on_add_h5_folder)
        btn_row.addWidget(add_files_btn)
        btn_row.addWidget(add_folder_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._form_controls.extend([add_files_btn, add_folder_btn])
        return box

    def _build_section_params(self) -> QGroupBox:
        box = QGroupBox("2. Dilute mask parameters")
        form = QFormLayout(box)

        self._mask_combo = QComboBox()
        self._mask_combo.setToolTip(
            "Existing condensed-phase mask (present in every queued dataset) "
            "to grow and invert."
        )
        self._mask_combo.currentIndexChanged.connect(self._on_picker_changed)
        form.addRow("Mask name:", self._mask_combo)

        self._seg_combo = QComboBox()
        self._seg_combo.setToolTip(
            "Cell segmentation (present in every queued dataset) that bounds "
            "the inversion."
        )
        self._seg_combo.currentIndexChanged.connect(self._on_picker_changed)
        form.addRow("Segmentation name:", self._seg_combo)

        self._radius_spin = QSpinBox()
        self._radius_spin.setRange(_RADIUS_MIN, _RADIUS_MAX)
        self._radius_spin.setSingleStep(1)
        self._radius_spin.setValue(_DEFAULT_RADIUS)
        self._radius_spin.setToolTip(
            "Disk radius (in pixels) by which the mask is grown before it is "
            "inverted within cells. 0 = no dilation."
        )
        # Label as a RADIUS (D2) to avoid a radius-vs-diameter 2x error.
        form.addRow("Expansion radius (px):", self._radius_spin)

        self._output_edit = QLineEdit(_DEFAULT_OUTPUT)
        self._output_edit.setToolTip(
            "New mask written as `<output_name>` in each dataset's /masks/ "
            "group. Must not collide with an existing channel, mask, or label."
        )
        self._output_edit.textChanged.connect(self._on_output_changed)
        form.addRow("Output mask name:", self._output_edit)

        # Picker status: surfaces "no common mask / segmentation" reasons.
        self._picker_status = QLabel("")
        self._picker_status.setWordWrap(True)
        self._picker_status.setStyleSheet("color: #888;")
        form.addRow("", self._picker_status)

        # Output status: surfaces the collision reason (red) when invalid.
        self._output_status = QLabel("")
        self._output_status.setWordWrap(True)
        self._output_status.setStyleSheet("color: #c0392b;")
        form.addRow("", self._output_status)

        self._form_controls.extend([
            self._mask_combo,
            self._seg_combo,
            self._radius_spin,
            self._output_edit,
        ])
        return box

    # ── QSettings persistence ────────────────────────────

    def _restore_qsettings(self) -> None:
        qs = app_settings()
        assert self._radius_spin is not None
        assert self._output_edit is not None
        self._radius_spin.setValue(
            int(qs.value(_QS_RADIUS, _DEFAULT_RADIUS, type=int))
        )
        self._output_edit.setText(
            str(qs.value(_QS_OUTPUT, _DEFAULT_OUTPUT, type=str))
        )

    def _save_qsettings(self) -> None:
        qs = app_settings()
        assert self._radius_spin is not None
        assert self._output_edit is not None
        qs.setValue(_QS_RADIUS, self._radius_spin.value())
        qs.setValue(_QS_OUTPUT, self._output_edit.text())

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
        self._add_h5_paths(drop_sidecars(paths))

    def _on_add_h5_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Add folder of .h5 datasets", ""
        )
        if not folder:
            return
        h5_files = scan_files(folder, "*.h5", "*.hdf5")
        if not h5_files:
            return
        self._add_h5_paths(h5_files)

    def _on_remove_row(self, row_index: int) -> None:
        """Row-local remove (per-row x button)."""
        if not (0 <= row_index < len(self._pending_datasets)):
            return
        del self._pending_datasets[row_index]
        self._refresh_dataset_list()
        self._refresh_pickers()
        self._update_start_enabled()

    def _add_h5_paths(self, paths: list[Path]) -> None:
        """Add datasets to the queue (deduped by resolved path).

        Per-file discovery opens a :class:`DatasetStore` and records that
        file's channel names, ``/masks`` listing, and ``/labels`` listing.
        A corrupt or unreadable file is silently skipped (with logging),
        mirroring the phasor-masks dialog.
        """
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
                channel_names = list(meta.get("channel_names", []) or [])
                mask_names = list(store.list_groups("masks"))
                label_names = list(store.list_groups("labels"))
            except Exception:
                logger.exception(
                    "dilute-from-mask dialog: failed to read %s", resolved
                )
                continue
            self._pending_datasets.append(
                _PendingDataset(
                    h5_path=resolved,
                    channel_names=channel_names,
                    mask_names=mask_names,
                    label_names=label_names,
                )
            )
            existing.add(resolved)

        self._refresh_dataset_list()
        self._refresh_pickers()
        self._update_start_enabled()

    def _refresh_dataset_list(self) -> None:
        """Rebuild the dataset rows (path label + per-row x button)."""
        assert self._dataset_list is not None
        self._dataset_list.clear()
        for row_index, pd in enumerate(self._pending_datasets):
            item = QListWidgetItem(self._dataset_list)
            row_widget = self._build_dataset_row_widget(pd, row_index)
            item.setSizeHint(row_widget.sizeHint())
            self._dataset_list.setItemWidget(item, row_widget)

    def _build_dataset_row_widget(
        self, pd: _PendingDataset, row_index: int
    ) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)

        path_label = QLabel(str(pd.h5_path))
        path_label.setTextInteractionFlags(Qt.NoTextInteraction)
        path_label.setToolTip(str(pd.h5_path))
        path_label.setWordWrap(False)
        path_label.setTextFormat(Qt.PlainText)
        path_label.setMinimumWidth(0)
        layout.addWidget(path_label, 1)

        remove_btn = QPushButton("×")
        remove_btn.setFixedSize(24, 24)
        remove_btn.setToolTip("Remove this dataset from the queue")
        remove_btn.clicked.connect(
            lambda _checked=False, r=row_index: self._on_remove_row(r)
        )
        layout.addWidget(remove_btn)

        return widget

    # ── Row widget access (test affordances) ─────────────

    def _row_path_label(self, row_index: int) -> QLabel | None:
        assert self._dataset_list is not None
        if not (0 <= row_index < self._dataset_list.count()):
            return None
        item = self._dataset_list.item(row_index)
        widget = self._dataset_list.itemWidget(item)
        if widget is None:
            return None
        for lbl in widget.findChildren(QLabel):
            return lbl
        return None

    def _row_remove_button(self, row_index: int) -> QPushButton | None:
        assert self._dataset_list is not None
        if not (0 <= row_index < self._dataset_list.count()):
            return None
        item = self._dataset_list.item(row_index)
        widget = self._dataset_list.itemWidget(item)
        if widget is None:
            return None
        for btn in widget.findChildren(QPushButton):
            return btn
        return None

    # ── Mask / segmentation pickers ──────────────────────

    def _common_mask_names(self) -> list[str]:
        """Order-preserving intersection of /masks names across all files."""
        if not self._pending_datasets:
            return []
        sources = [
            (str(pd.h5_path), pd.mask_names) for pd in self._pending_datasets
        ]
        intersection, _outliers = intersect_channels(sources)
        return intersection

    def _common_seg_names(self) -> list[str]:
        """Order-preserving intersection of /labels names across all files.

        Kept namespace-distinct from the mask intersection (collision
        learning): /masks and /labels are intersected separately.
        """
        if not self._pending_datasets:
            return []
        sources = [
            (str(pd.h5_path), pd.label_names) for pd in self._pending_datasets
        ]
        intersection, _outliers = intersect_channels(sources)
        return intersection

    def _refresh_pickers(self) -> None:
        """Repopulate the mask + segmentation combos from the intersections.

        Preserves the prior selection when the name survives the rebuild.
        Uses ``QSignalBlocker`` so the rebuild does not recurse into
        ``_update_start_enabled`` (the caller invokes it explicitly).
        """
        assert self._mask_combo is not None
        assert self._seg_combo is not None
        assert self._picker_status is not None

        common_masks = self._common_mask_names()
        common_segs = self._common_seg_names()

        prev_mask = self._mask_combo.currentText()
        prev_seg = self._seg_combo.currentText()

        with QSignalBlocker(self._mask_combo):
            self._mask_combo.clear()
            self._mask_combo.addItems(common_masks)
            if prev_mask in common_masks:
                self._mask_combo.setCurrentText(prev_mask)

        with QSignalBlocker(self._seg_combo):
            self._seg_combo.clear()
            self._seg_combo.addItems(common_segs)
            if prev_seg in common_segs:
                self._seg_combo.setCurrentText(prev_seg)

        # Picker status message.
        if not self._pending_datasets:
            self._picker_status.setText(
                "Add datasets to populate the mask and segmentation pickers."
            )
        elif not common_masks and not common_segs:
            self._picker_status.setText(
                "No mask name and no segmentation is present in every "
                "selected dataset."
            )
        elif not common_masks:
            self._picker_status.setText(
                "No mask name is present in every selected dataset."
            )
        elif not common_segs:
            self._picker_status.setText(
                "No segmentation is present in every selected dataset."
            )
        else:
            self._picker_status.setText(
                f"{len(common_masks)} common mask name(s), "
                f"{len(common_segs)} common segmentation(s)."
            )

    # ── Output-name collision validation ─────────────────

    def _collision_info(self) -> tuple[int, int, str]:
        """``(n_colliding, n_total, status_text)`` for the current output name.

        A dataset "collides" when the output name already exists in it as a
        channel / mask / label (the three namespaces kept distinct, per the
        add-mask collision learning). The use case **skips colliding datasets
        per-dataset**, so a *partial* collision is a non-blocking warning (those
        are skipped, the rest run); only an *all-datasets* collision blocks the
        run (nothing to do). This mirrors the phasor dialog's graceful
        degradation rather than blocking the whole batch on one stale file —
        which would defeat a normal incremental re-run.
        """
        assert self._output_edit is not None
        name = self._output_edit.text().strip()
        total = len(self._pending_datasets)
        if not name or total == 0:
            return 0, total, ""
        n = 0
        first_kind: str | None = None
        for pd in self._pending_datasets:
            if name in pd.channel_names:
                kind = "channel"
            elif name in pd.mask_names:
                kind = "mask"
            elif name in pd.label_names:
                kind = "label"
            else:
                continue
            n += 1
            if first_kind is None:
                first_kind = kind
        if n == 0:
            return 0, total, ""
        if n == total:
            # Every queued dataset collides → nothing to write; block the run
            # and name the conflicting resource kind.
            return n, total, (
                f"'{name}' already exists as a {first_kind} — choose another "
                "output name."
            )
        # Partial collision → the use case skips those datasets and runs the
        # rest; warn but do not block (a normal incremental re-run).
        return n, total, (
            f"'{name}' already exists in {n} of {total} dataset(s); those will "
            "be skipped (the rest run)."
        )

    # ── Signal slots ────────────────────────────────────

    def _on_picker_changed(self, *_args: object) -> None:
        self._update_start_enabled()

    def _on_output_changed(self, *_args: object) -> None:
        self._update_start_enabled()

    # ── Start-enabled gating ─────────────────────────────

    def _update_start_enabled(self) -> None:
        if self._start_btn is None:
            return
        assert self._mask_combo is not None
        assert self._seg_combo is not None
        assert self._output_edit is not None
        assert self._output_status is not None

        has_files = bool(self._pending_datasets)
        mask_name = self._mask_combo.currentText()
        seg_name = self._seg_combo.currentText()
        output_name = self._output_edit.text().strip()
        n_colliding, n_total, status_text = self._collision_info()

        # Surface the collision status inline (a partial collision is a
        # non-blocking warning; an all-datasets collision blocks the run).
        self._output_status.setText(status_text)
        all_collide = n_total > 0 and n_colliding == n_total

        enabled = (
            has_files
            and bool(mask_name)
            and bool(seg_name)
            and bool(output_name)
            and not all_collide
        )
        self._start_btn.setEnabled(enabled)

    # ── Run mechanics ────────────────────────────────────

    def _on_start_clicked(self) -> None:
        """Capture frozen params, run the use case with a progress dialog,
        emit a single conditional refresh, show a summary, accept.

        Mirrors :meth:`PhasorMasksDialog._on_start_clicked`.
        """
        assert self._mask_combo is not None
        assert self._seg_combo is not None
        assert self._radius_spin is not None
        assert self._output_edit is not None

        dataset_paths = [pd.h5_path for pd in self._pending_datasets]
        mask_name = self._mask_combo.currentText()
        segmentation_name = self._seg_combo.currentText()
        radius_px = int(self._radius_spin.value())
        output_name = self._output_edit.text().strip()

        # Disable the form controls during the run; the QProgressDialog has
        # its own Cancel button.
        for w in self._form_controls:
            w.setEnabled(False)
        if self._start_btn is not None:
            self._start_btn.setEnabled(False)

        n_total = len(dataset_paths)
        progress = QProgressDialog(
            "Dilute phase mask from mask",
            "Cancel",
            0,
            n_total,
            self,
        )
        # Non-modal: the loop pumps events itself and the form is disabled.
        progress.setWindowModality(Qt.NonModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        completed = {"n": 0}

        def progress_cb(item: DiluteItemResult) -> None:
            completed["n"] += 1
            n = completed["n"]
            progress.setValue(n)
            progress.setLabelText(
                f"Dataset {n}/{n_total}: {item.h5_path.name} — {item.status}"
            )
            QApplication.processEvents()

        def cancel_check() -> bool:
            return bool(progress.wasCanceled())

        try:
            report = self._orchestrator(
                dataset_paths,
                mask_name=mask_name,
                segmentation_name=segmentation_name,
                radius_px=radius_px,
                output_name=output_name,
                progress_callback=progress_cb,
                cancel_check=cancel_check,
            )
        except Exception as exc:  # noqa: BLE001 — surface as a critical box
            logger.exception(
                "dilute-from-mask workflow raised out of orchestrator"
            )
            progress.close()
            QMessageBox.critical(
                self,
                "Dilute phase mask from mask",
                f"The workflow raised an unexpected error:\n\n{exc}",
            )
            for w in self._form_controls:
                w.setEnabled(True)
            self._update_start_enabled()
            return

        # Capture cancel state BEFORE setValue(maximum) — Qt's
        # QProgressDialog implicitly resets wasCanceled when the value hits
        # the maximum.
        cancelled = bool(progress.wasCanceled())
        progress.setValue(n_total)
        progress.close()

        self._last_report = report
        self._cancelled = cancelled
        self._save_qsettings()

        # Conditional, one-shot session refresh (Action discipline, R6/D5):
        # only datasets that actually had a mask written can change the loaded
        # dataset's resource list — skipped/failed/unreached datasets are
        # excluded so the refresh matches its "among the processed paths" gate.
        processed_paths = [
            it.h5_path for it in report.items if it.status == "processed"
        ]
        self._maybe_refresh_session(processed_paths)

        # Per-dataset summary.
        self._show_summary(report, cancelled)

        # Accept on completion — failures are reported, not abortive.
        self.accept()

    def _maybe_refresh_session(self, dataset_paths: list[Path]) -> None:
        """Emit one ``session.refresh_resource_lists`` if the active dataset
        is among the processed paths.

        Uses ``Path.resolve()`` on both sides to handle ``./`` prefixes,
        symlinks, and case-insensitive filesystem quirks. Mirrors the phasor
        dialog exactly.
        """
        host = self._host
        if host is None or not hasattr(host, "get_session"):
            return
        try:
            session = host.get_session()
        except Exception:
            logger.exception("dilute-from-mask dialog: host.get_session raised")
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
                "dilute-from-mask dialog: re-listing masks for refresh failed"
            )
            return
        try:
            session.refresh_resource_lists(mask_names=mask_names)
        except Exception:
            logger.exception(
                "dilute-from-mask dialog: session.refresh_resource_lists raised"
            )

    def _show_summary(self, report: DiluteReport, cancelled: bool) -> None:
        n_total = len(report.items)
        n_processed = report.total_processed
        n_skipped = report.total_skipped
        n_failed = report.total_failed
        n_empty = sum(
            1
            for it in report.items
            if it.status == "processed" and it.message
        )

        if cancelled:
            text = (
                f"Cancelled after {n_total} dataset(s): {n_processed} "
                f"processed, {n_skipped} skipped, {n_failed} failed."
            )
        else:
            text = (
                f"Processed {n_total} dataset(s): {n_processed} processed, "
                f"{n_skipped} skipped, {n_failed} failed."
            )
        if n_empty:
            text += f" {n_empty} produced an empty mask."

        # Detailed text: per-dataset status + any reason / annotation.
        detail_lines: list[str] = []
        for it in report.items:
            line = f"{it.h5_path.name}: {it.status}"
            if it.message:
                line += f" — {it.message}"
            detail_lines.append(line)

        msg = QMessageBox(self)
        msg.setWindowTitle("Dilute phase mask from mask")
        # Warning icon when anything went sideways or any output was empty.
        if n_failed > 0 or n_skipped > 0 or n_empty > 0 or cancelled:
            msg.setIcon(QMessageBox.Warning)
        else:
            msg.setIcon(QMessageBox.Information)
        msg.setText(text)
        if detail_lines:
            msg.setDetailedText("\n".join(detail_lines))
        msg.exec_()
