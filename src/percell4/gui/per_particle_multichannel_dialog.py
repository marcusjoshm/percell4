"""Per-particle multi-channel analysis dialog.

Concrete per-analysis ``QDialog`` for the ``PerParticleMultichannel``
analysis. Hand-laid layout reusing :mod:`percell4.gui.analysis_widgets`
factories, mirroring :class:`PerParticleDonutDialog` but with a **dynamic
"Add channel" list** (1–8 measurement channels) in place of the fixed
branch rows. Each channel row's combo maps to a ``channel_N`` role; the
chosen layer name flows through ``layer_map`` to name the output columns.

This dialog is an **Action**: it reads no session selection state and
only pushes ``session.refresh_resource_lists`` at end-of-run.

Side effect on import: sets
``PerParticleMultichannel.dialog_class = PerParticleMultichannelDialog``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.application.analysis import (
    BatchAnalysisItemResult,
    BatchAnalysisReport,
    batch_run_analysis,
)
from percell4.application.analysis.modules.per_particle_multichannel import (
    _MAX_CHANNELS,
    PerParticleMultichannel,
)
from percell4.domain.analysis import BoolParam
from percell4.gui._dialog_utils import cap_to_screen, wrap_in_scroll
from percell4.gui.analysis_widgets import (
    LAYER_SENTINEL,
    build_dataset_picker,
    build_layer_combo,
    build_output_parent_picker,
    build_param_widget,
    persist_output_parent,
    populate_layer_combo,
)
from percell4.store import DatasetStore

logger = logging.getLogger(__name__)

_QSETTINGS_OUTPUT_KEY = "analysis/per_particle_multichannel/output_parent"


class PerParticleMultichannelDialog(QDialog):
    """Modal dialog for the per-particle multi-channel analysis."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        orchestrator: Callable[..., BatchAnalysisReport] = batch_run_analysis,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(PerParticleMultichannel.display_name)
        self.setMinimumWidth(720)
        self.resize(820, 720)
        cap_to_screen(self)

        self._host = parent
        self._orchestrator = orchestrator

        # State.
        self._h5_paths: list[Path] = []
        self._last_report: BatchAnalysisReport | None = None
        self._cancelled: bool = False

        # Widgets — set in _build_ui.
        self._dataset_list: QListWidget | None = None
        self._mask_combo: QComboBox | None = None
        # Dynamic channel rows: each entry is (row_widget, combo, label).
        self._channel_rows: list[tuple[QWidget, QComboBox, QLabel]] = []
        self._channel_layout: QVBoxLayout | None = None
        self._add_channel_btn: QPushButton | None = None
        self._channel_status: QLabel | None = None
        self._cp_mask_combo: QComboBox | None = None
        # Param widgets + getters/setters keyed by param name.
        self._param_widgets: dict[str, QWidget] = {}
        self._param_getters: dict[str, Callable[[], Any]] = {}
        self._param_setters: dict[str, Callable[[Any], None]] = {}
        # Output panel labels.
        self._output_labels: dict[str, QLabel] = {}
        # Output parent picker.
        self._output_parent_line: Any = None
        # Run controls.
        self._start_btn: QPushButton | None = None
        self._cancel_btn: QPushButton | None = None
        self._status_label: QLabel | None = None

        self._build_ui()
        self._add_channel_row()  # start with one channel slot (channel_1)
        self._refresh_state()

    # ── Public surface ──────────────────────────────────────────

    @property
    def last_report(self) -> BatchAnalysisReport | None:
        return self._last_report

    @property
    def last_run_folder(self) -> Path | None:
        return self._last_report.run_folder if self._last_report else None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    # ── UI construction ─────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setSpacing(10)

        body.addWidget(self._build_header())
        body.addWidget(self._build_section_datasets())
        body.addWidget(self._build_section_layer_map())
        body.addWidget(self._build_section_params())
        body.addWidget(self._build_section_outputs())
        body.addWidget(self._build_section_output_parent())
        body.addStretch()

        outer.addWidget(wrap_in_scroll(content), 1)

        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #c0392b;")
        outer.addWidget(self._status_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._start_btn = QPushButton("Start", self)
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._cancel_btn)
        outer.addLayout(btn_row)

    def _build_header(self) -> QWidget:
        box = QWidget(self)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel(PerParticleMultichannel.display_name, self)
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        desc = QLabel(PerParticleMultichannel.description, self)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaaaaa;")
        layout.addWidget(title)
        layout.addWidget(desc)
        return box

    def _build_section_datasets(self) -> QGroupBox:
        box = QGroupBox("1. Datasets")
        layout = QVBoxLayout(box)

        self._dataset_list, add_files_btn, add_folder_btn = (
            build_dataset_picker(self)
        )
        layout.addWidget(self._dataset_list)

        add_files_btn.clicked.connect(self._on_add_files)
        add_folder_btn.clicked.connect(self._on_add_folder)

        btn_row = QHBoxLayout()
        btn_row.addWidget(add_files_btn)
        btn_row.addWidget(add_folder_btn)
        clear_btn = QPushButton("Clear", self)
        clear_btn.clicked.connect(self._on_clear_datasets)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return box

    def _build_section_layer_map(self) -> QGroupBox:
        box = QGroupBox("2. Layer map")
        layout = QVBoxLayout(box)

        # Required particle mask row.
        mask_role = PerParticleMultichannel.required_inputs["mask"]
        row, _, combo = build_layer_combo("mask", mask_role.desc, self)
        self._mask_combo = combo
        combo.activated.connect(lambda _i: self._refresh_state())
        layout.addLayout(row)

        # Dynamic measurement-channel list (1–8).
        ch_box = QGroupBox(f"Measurement channels (1–{_MAX_CHANNELS})", self)
        ch_outer = QVBoxLayout(ch_box)
        self._channel_layout = QVBoxLayout()
        ch_outer.addLayout(self._channel_layout)

        ctl_row = QHBoxLayout()
        self._add_channel_btn = QPushButton("Add channel", self)
        self._add_channel_btn.clicked.connect(self._add_channel_row)
        ctl_row.addWidget(self._add_channel_btn)
        self._channel_status = QLabel("", self)
        self._channel_status.setStyleSheet("color: #aaaaaa;")
        ctl_row.addWidget(self._channel_status)
        ctl_row.addStretch()
        ch_outer.addLayout(ctl_row)
        layout.addWidget(ch_box)

        # Optional cp_mask.
        cp_role = PerParticleMultichannel.optional_inputs["cp_mask"]
        cp_row, _, cp_combo = build_layer_combo("cp_mask", cp_role.desc, self)
        cp_combo.activated.connect(lambda _i: self._refresh_state())
        self._cp_mask_combo = cp_combo
        layout.addLayout(cp_row)
        return box

    def _build_section_params(self) -> QGroupBox:
        box = QGroupBox("3. Parameters")
        outer = QVBoxLayout(box)

        form = QFormLayout()
        for name, decl in PerParticleMultichannel.parameters.items():
            widget, getter, setter = build_param_widget(decl, self)
            self._param_widgets[name] = widget
            self._param_getters[name] = getter
            self._param_setters[name] = setter
            for signal_name in ("valueChanged", "toggled", "activated"):
                sig = getattr(widget, signal_name, None)
                if sig is not None:
                    sig.connect(lambda *_a: self._refresh_state())
                    break
            form.addRow(name, widget)
        outer.addLayout(form)
        return box

    def _build_section_outputs(self) -> QGroupBox:
        box = QGroupBox("4. Outputs (will be produced)")
        layout = QVBoxLayout(box)
        for name in PerParticleMultichannel.outputs:
            label = QLabel(name, self)
            layout.addWidget(label)
            self._output_labels[name] = label
        return box

    def _build_section_output_parent(self) -> QGroupBox:
        box = QGroupBox("5. Output folder")
        layout = QVBoxLayout(box)
        row, line, _btn = build_output_parent_picker(
            _QSETTINGS_OUTPUT_KEY, self
        )
        line.textChanged.connect(lambda _t: self._refresh_state())
        self._output_parent_line = line
        layout.addLayout(row)
        return box

    # ── Dynamic channel rows ────────────────────────────────────

    def _add_channel_row(self) -> None:
        assert self._channel_layout is not None
        if len(self._channel_rows) >= _MAX_CHANNELS:
            return
        row_widget = QWidget(self)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel("", self)
        label.setMinimumWidth(90)
        combo = QComboBox(self)
        combo.addItem(LAYER_SENTINEL)
        combo.activated.connect(lambda _i: self._refresh_state())
        remove_btn = QPushButton("✕", self)
        remove_btn.setFixedWidth(28)
        remove_btn.clicked.connect(
            lambda _checked=False, c=combo: self._remove_channel_row(c)
        )
        row_layout.addWidget(label)
        row_layout.addWidget(combo, 1)
        row_layout.addWidget(remove_btn)
        self._channel_layout.addWidget(row_widget)
        self._channel_rows.append((row_widget, combo, label))
        self._renumber_channel_rows()
        self._refresh_state()

    def _remove_channel_row(self, combo: QComboBox) -> None:
        # Always keep at least one channel slot (channel_1 is required).
        if len(self._channel_rows) <= 1:
            return
        for i, (row_widget, c, _label) in enumerate(self._channel_rows):
            if c is combo:
                self._channel_rows.pop(i)
                row_widget.setParent(None)
                row_widget.deleteLater()
                break
        self._renumber_channel_rows()
        self._refresh_state()

    def _renumber_channel_rows(self) -> None:
        """Relabel rows Channel 1..N; disable the remove button when only one
        row remains so the user can't drop below the required channel_1."""
        only_one = len(self._channel_rows) <= 1
        for i, (row_widget, _combo, label) in enumerate(self._channel_rows):
            label.setText(f"Channel {i + 1}")
            btn = row_widget.findChild(QPushButton)
            if btn is not None:
                btn.setEnabled(not only_one)

    # ── Dataset add/remove ──────────────────────────────────────

    def _on_add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add .h5 datasets", "", "HDF5 files (*.h5 *.hdf5)"
        )
        if not paths:
            return
        self._add_paths([Path(p) for p in paths])

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Add folder of .h5", ""
        )
        if not folder:
            return
        candidates = sorted(Path(folder).glob("*.h5")) + sorted(
            Path(folder).glob("*.hdf5")
        )
        if not candidates:
            return
        self._add_paths(candidates)

    def _on_clear_datasets(self) -> None:
        self._h5_paths.clear()
        self._refresh_dataset_list()
        self._refresh_state()

    def _add_paths(self, paths: list[Path]) -> None:
        existing = set(self._h5_paths)
        for p in paths:
            try:
                resolved = p.resolve()
            except OSError:
                resolved = p
            if not resolved.is_file():
                continue
            if resolved in existing:
                continue
            self._h5_paths.append(resolved)
            existing.add(resolved)
        self._refresh_dataset_list()
        self._refresh_state()

    def _refresh_dataset_list(self) -> None:
        assert self._dataset_list is not None
        self._dataset_list.clear()
        for p in self._h5_paths:
            self._dataset_list.addItem(str(p))

    # ── Refresh cascade ─────────────────────────────────────────

    def _refresh_state(self) -> None:
        inventory = self._gather_layer_inventory()
        self._refresh_combos(inventory)
        self._refresh_channel_controls()
        self._refresh_requires_gating()
        self._refresh_outputs_panel()
        self._refresh_start_button()

    def _gather_layer_inventory(self) -> dict[str, list[str]]:
        inventory: dict[str, list[str]] = {
            "intensity": [],
            "mask": [],
            "label": [],
        }
        if not self._h5_paths:
            return inventory
        intensity_sets: list[set[str]] = []
        mask_sets: list[set[str]] = []
        label_sets: list[set[str]] = []
        for path in self._h5_paths:
            try:
                store = DatasetStore(path)
                meta = store.metadata
                channel_names = list(meta.get("channel_names") or [])
                decay_names = list(store.list_groups("decay"))
                intensity_sets.append(set(channel_names) | set(decay_names))
                mask_sets.append(set(store.list_masks()))
                label_sets.append(set(store.list_labels()))
            except Exception:  # noqa: BLE001
                logger.exception("layer inventory read failed for %s", path)
                return {"intensity": [], "mask": [], "label": []}
        if intensity_sets:
            inventory["intensity"] = sorted(set.intersection(*intensity_sets))
            inventory["mask"] = sorted(set.intersection(*mask_sets))
            inventory["label"] = sorted(set.intersection(*label_sets))
        return inventory

    def _refresh_combos(self, inventory: dict[str, list[str]]) -> None:
        if self._mask_combo is not None:
            populate_layer_combo(self._mask_combo, inventory["mask"])
        for _row, combo, _label in self._channel_rows:
            populate_layer_combo(combo, inventory["intensity"])
        if self._cp_mask_combo is not None:
            populate_layer_combo(self._cp_mask_combo, inventory["label"])

    def _refresh_channel_controls(self) -> None:
        if self._add_channel_btn is not None:
            self._add_channel_btn.setEnabled(
                len(self._channel_rows) < _MAX_CHANNELS
            )
        if self._channel_status is not None:
            self._channel_status.setText(
                f"{len(self._channel_rows)}/{_MAX_CHANNELS} channels"
            )

    def _refresh_requires_gating(self) -> None:
        layer_map = self._resolve_layer_map()
        for pname, decl in PerParticleMultichannel.parameters.items():
            if not isinstance(decl, BoolParam) or not decl.requires:
                continue
            widget = self._param_widgets[pname]
            missing = [req for req in decl.requires if req not in layer_map]
            if missing:
                widget.setEnabled(False)
                widget.setToolTip(f"Requires {missing[0]!r} to be assigned")
                if pname in self._param_setters:
                    self._param_setters[pname](False)
            else:
                widget.setEnabled(True)
                widget.setToolTip(decl.desc or "")

    def _refresh_outputs_panel(self) -> None:
        from percell4.domain.analysis import GroupState

        layer_map = self._resolve_layer_map()
        params = self._collect_params()

        all_roles = set(PerParticleMultichannel.required_inputs)
        all_roles |= set(PerParticleMultichannel.optional_inputs)
        flags = {f"{r}_supplied": (r in layer_map) for r in all_roles}
        gs = GroupState(**flags)

        for name, decl in PerParticleMultichannel.outputs.items():
            label = self._output_labels[name]
            produced = True
            if decl.produced_when is not None:
                try:
                    produced = bool(decl.produced_when(gs, params))
                except Exception:  # noqa: BLE001
                    produced = False
            if produced:
                label.setStyleSheet("color: #d0d0d0;")
                label.setText(f"✔ {name}")
            else:
                label.setStyleSheet(
                    "color: #555555; text-decoration: line-through;"
                )
                label.setText(f"  {name}")

    def _refresh_start_button(self) -> None:
        assert self._start_btn is not None
        reason = self._start_disabled_reason()
        if reason is None:
            self._start_btn.setEnabled(True)
            self._start_btn.setToolTip("Begin the analysis run.")
        else:
            self._start_btn.setEnabled(False)
            self._start_btn.setToolTip(reason)

    def _start_disabled_reason(self) -> str | None:
        if not self._h5_paths:
            return "Add at least one dataset to begin."
        if (
            self._mask_combo is None
            or self._mask_combo.currentText() == LAYER_SENTINEL
        ):
            return "Assign the particle mask."
        layer_map = self._resolve_layer_map()
        if "channel_1" not in layer_map:
            return "Assign at least one measurement channel."
        if not self._resolve_output_parent():
            return "Choose an output folder."
        return None

    # ── Selection helpers ───────────────────────────────────────

    def _resolve_layer_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if (
            self._mask_combo is not None
            and self._mask_combo.currentText() != LAYER_SENTINEL
        ):
            out["mask"] = self._mask_combo.currentText()
        for i, (_row, combo, _label) in enumerate(self._channel_rows):
            text = combo.currentText()
            if text and text != LAYER_SENTINEL:
                out[f"channel_{i + 1}"] = text
        if self._cp_mask_combo is not None:
            text = self._cp_mask_combo.currentText()
            if text and text != LAYER_SENTINEL:
                out["cp_mask"] = text
        return out

    def _collect_params(self) -> dict[str, Any]:
        return {name: getter() for name, getter in self._param_getters.items()}

    def _resolve_output_parent(self) -> Path | None:
        if self._output_parent_line is None:
            return None
        text = self._output_parent_line.text().strip()
        if not text:
            return None
        p = Path(text)
        return p if p.exists() and p.is_dir() else None

    # ── Run mechanics ───────────────────────────────────────────

    def _on_start_clicked(self) -> None:
        assert self._start_btn is not None
        layer_map = self._resolve_layer_map()
        params = self._collect_params()
        output_parent = self._resolve_output_parent()
        if output_parent is None:
            return

        persist_output_parent(_QSETTINGS_OUTPUT_KEY, str(output_parent))
        self._start_btn.setEnabled(False)

        n_total = len(self._h5_paths)
        progress = QProgressDialog(
            "Per-particle multi-channel analysis", "Cancel", 0, n_total, self
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def on_progress(
            item: BatchAnalysisItemResult, idx: int, total: int
        ) -> None:
            progress.setValue(idx + 1)
            progress.setLabelText(
                f"Dataset {idx + 1}/{total}: {item.h5_path.name} — "
                f"{item.status}"
            )
            QApplication.processEvents()

        def cancel_check() -> bool:
            return bool(progress.wasCanceled())

        try:
            report = self._orchestrator(
                "per_particle_multichannel",
                self._h5_paths,
                lambda _p: layer_map,
                output_parent=output_parent,
                params=params,
                preset=None,
                progress_callback=on_progress,
                cancel_check=cancel_check,
                log=print,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("per-particle multichannel analysis raised")
            progress.close()
            QMessageBox.critical(
                self,
                "Per-particle multi-channel analysis",
                f"The analysis raised an unexpected error:\n\n{exc}",
            )
            self._start_btn.setEnabled(True)
            return

        cancelled = bool(progress.wasCanceled())
        progress.setValue(n_total)
        progress.close()

        self._last_report = report
        self._cancelled = cancelled or report.cancelled

        self._maybe_refresh_session([Path(p) for p in self._h5_paths])
        self._show_summary(report)
        self.accept()

    def _maybe_refresh_session(self, processed_paths: list[Path]) -> None:
        host = self._host
        if host is None:
            return
        session = getattr(host, "session", None)
        active = (
            getattr(session, "active_dataset_path", None) if session else None
        )
        if active is None:
            return
        try:
            active_resolved = Path(active).resolve()
        except OSError:
            return
        if active_resolved in {p.resolve() for p in processed_paths}:
            refresh = getattr(session, "refresh_resource_lists", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:  # noqa: BLE001
                    logger.exception("session refresh after analysis failed")

    def _show_summary(self, report: BatchAnalysisReport) -> None:
        succeeded = report.succeeded_count
        failed = report.failed_count
        lines = [
            f"Succeeded: {succeeded}",
            f"Failed: {failed}",
            f"Run folder: {report.run_folder}",
        ]
        if report.cancelled:
            lines.append("Run was cancelled before completing all datasets.")
        QMessageBox.information(
            self, "Per-particle multi-channel analysis", "\n".join(lines)
        )


# Late-binding: attach the dialog class so the Scripts tab can open it.
PerParticleMultichannel.dialog_class = PerParticleMultichannelDialog
