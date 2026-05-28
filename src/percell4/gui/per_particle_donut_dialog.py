"""Per-particle donut analysis dialog.

Concrete per-analysis ``QDialog`` for the ``PerParticleDonut``
analysis. Hand-laid layout (no schema-driven widget generation):
header + skip-able P-body / SG branches + optional cp_mask row +
parameter section + preset combo with lock affordance + outputs
panel + output-parent picker + Start / Cancel.

Reuses :mod:`percell4.gui.analysis_widgets` factories for the
repeated layout primitives (dataset picker, layer combos, param
widgets, preset combo, output-parent picker).

This dialog is an **Action** in the GUI-element classification
taxonomy: it reads no session state for selection purposes, and only
writes session state via the end-of-run
``session.refresh_resource_lists`` push if the parent has one.

Side effect on import: sets
``PerParticleDonut.dialog_class = PerParticleDonutDialog``, so U8's
Scripts-tab wiring can read it generically.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
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
from percell4.application.analysis.modules.per_particle_donut import (
    PerParticleDonut,
)
from percell4.domain.analysis import BoolParam
from percell4.gui._dialog_utils import cap_to_screen, wrap_in_scroll
from percell4.gui.analysis_widgets import (
    LAYER_SENTINEL,
    build_dataset_picker,
    build_layer_combo,
    build_output_parent_picker,
    build_param_widget,
    build_preset_combo,
    persist_output_parent,
    populate_layer_combo,
)
from percell4.store import DatasetStore

logger = logging.getLogger(__name__)

_QSETTINGS_OUTPUT_KEY = "analysis/per_particle_donut/output_parent"
_NO_PRESET = "No preset"

# Branch declaration: maps the user-facing branch label to the role
# names that branch is responsible for (matches PerParticleDonut's
# input_groups).
_BRANCHES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("pbody", "P-body branch", ("pbody_mask", "pnorm")),
    ("sg", "SG branch", ("sg_mask", "sgnorm")),
)


class PerParticleDonutDialog(QDialog):
    """Modal dialog for the per-particle donut analysis."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        orchestrator: Callable[..., BatchAnalysisReport] = batch_run_analysis,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(PerParticleDonut.display_name)
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
        self._cap_combo: QComboBox | None = None
        # Branch widgets keyed by group name.
        self._branch_skip: dict[str, QCheckBox] = {}
        self._branch_combos: dict[str, dict[str, QComboBox]] = {
            "pbody": {},
            "sg": {},
        }
        self._cp_mask_combo: QComboBox | None = None
        # Param widgets + getters/setters keyed by param name.
        self._param_widgets: dict[str, QWidget] = {}
        self._param_getters: dict[str, Callable[[], Any]] = {}
        self._param_setters: dict[str, Callable[[Any], None]] = {}
        # Preset combo + lock affordance label.
        self._preset_combo: QComboBox | None = None
        self._preset_lock_label: QLabel | None = None
        # Output panel labels (one per output name).
        self._output_labels: dict[str, QLabel] = {}
        # Output parent picker.
        self._output_parent_line: Any = None
        # Run controls.
        self._start_btn: QPushButton | None = None
        self._cancel_btn: QPushButton | None = None
        self._status_label: QLabel | None = None

        self._build_ui()
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
        title = QLabel(PerParticleDonut.display_name, self)
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        desc = QLabel(PerParticleDonut.description, self)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaaaaa;")
        layout.addWidget(title)
        layout.addWidget(desc)
        return box

    def _build_section_datasets(self) -> QGroupBox:
        box = QGroupBox("1. Datasets")
        layout = QVBoxLayout(box)

        self._dataset_list, add_files_btn, add_folder_btn = build_dataset_picker(self)
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

        # Required Cap row.
        cap_role = PerParticleDonut.required_inputs["cap"]
        row, _, combo = build_layer_combo("Cap", cap_role.desc, self)
        self._cap_combo = combo
        combo.activated.connect(lambda _i: self._refresh_state())
        layout.addLayout(row)

        # Group branches.
        for group_name, branch_label, role_names in _BRANCHES:
            sub = QGroupBox(branch_label, self)
            sub_layout = QVBoxLayout(sub)

            skip_box = QCheckBox(f"Skip {branch_label.lower()}", self)
            skip_box.toggled.connect(self._refresh_state)
            sub_layout.addWidget(skip_box)
            self._branch_skip[group_name] = skip_box

            for role in role_names:
                role_decl = PerParticleDonut.input_groups[group_name][role]
                role_row, _, role_combo = build_layer_combo(
                    role, role_decl.desc, self
                )
                role_combo.activated.connect(lambda _i: self._refresh_state())
                self._branch_combos[group_name][role] = role_combo
                sub_layout.addLayout(role_row)
            layout.addWidget(sub)

        # Optional cp_mask.
        cp_role = PerParticleDonut.optional_inputs["cp_mask"]
        cp_row, _, cp_combo = build_layer_combo("cp_mask", cp_role.desc, self)
        cp_combo.activated.connect(lambda _i: self._refresh_state())
        self._cp_mask_combo = cp_combo
        layout.addLayout(cp_row)
        return box

    def _build_section_params(self) -> QGroupBox:
        box = QGroupBox("3. Parameters")
        outer = QVBoxLayout(box)

        # Preset row.
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:", self))
        self._preset_combo = build_preset_combo(
            PerParticleDonut.presets, self, no_preset_label=_NO_PRESET
        )
        self._preset_combo.activated.connect(self._on_preset_changed)
        preset_row.addWidget(self._preset_combo, 1)
        self._preset_lock_label = QLabel("", self)
        self._preset_lock_label.setStyleSheet("color: #f4d35e;")
        preset_row.addWidget(self._preset_lock_label)
        outer.addLayout(preset_row)

        form = QFormLayout()
        for name, decl in PerParticleDonut.parameters.items():
            widget, getter, setter = build_param_widget(decl, self)
            self._param_widgets[name] = widget
            self._param_getters[name] = getter
            self._param_setters[name] = setter
            # Hook user-edit signal.
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
        for name in PerParticleDonut.outputs:
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

    # ── Dataset add/remove ──────────────────────────────────────

    def _on_add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add .h5 datasets", "", "HDF5 files (*.h5 *.hdf5)"
        )
        if not paths:
            return
        self._add_paths([Path(p) for p in paths])

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add folder of .h5", "")
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
        """Recompute all derived UI state from the current inputs."""
        layer_inventory = self._gather_layer_inventory()
        stale_messages = self._refresh_combos(layer_inventory)
        self._refresh_branch_enabled_state()
        self._refresh_preset_lock()
        self._refresh_requires_gating()
        self._refresh_outputs_panel()
        self._refresh_start_button()
        self._update_status_label(stale_messages)

    def _gather_layer_inventory(self) -> dict[str, list[str]]:
        """Return ``{kind: [names]}`` available across ALL selected datasets.

        Empty list when no datasets are selected.
        """
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
                # If any dataset fails, intersection collapses to empty.
                return {"intensity": [], "mask": [], "label": []}
        if intensity_sets:
            inventory["intensity"] = sorted(set.intersection(*intensity_sets))
            inventory["mask"] = sorted(set.intersection(*mask_sets))
            inventory["label"] = sorted(set.intersection(*label_sets))
        return inventory

    def _refresh_combos(
        self, inventory: dict[str, list[str]]
    ) -> list[str]:
        """Repopulate every role combo against the intersection. Returns
        list of stale-selection warnings (one per role whose previous
        selection no longer exists)."""
        warnings: list[str] = []
        # Cap (intensity).
        if self._cap_combo is not None:
            ok = populate_layer_combo(self._cap_combo, inventory["intensity"])
            if not ok and self._h5_paths and self._cap_combo.currentText() == LAYER_SENTINEL:
                # Only warn if the user previously had a selection and lost it.
                # populate_layer_combo returns False both for "no prior" and
                # "prior gone." Distinguishing requires tracking — skip the
                # warning for the initial paint by checking inventory non-empty.
                pass

        for group_name, _label, role_names in _BRANCHES:
            for role in role_names:
                combo = self._branch_combos[group_name][role]
                role_decl = PerParticleDonut.input_groups[group_name][role]
                kind = role_decl.kind
                populate_layer_combo(combo, inventory.get(kind, []))

        if self._cp_mask_combo is not None:
            populate_layer_combo(
                self._cp_mask_combo, inventory.get("label", [])
            )
        return warnings

    def _refresh_branch_enabled_state(self) -> None:
        """Skipping a branch disables its role combos."""
        for group_name, _label, role_names in _BRANCHES:
            skipped = self._branch_skip[group_name].isChecked()
            for role in role_names:
                combo = self._branch_combos[group_name][role]
                combo.setEnabled(not skipped)

    def _refresh_preset_lock(self) -> None:
        """When a preset is selected, disable param widgets."""
        assert self._preset_combo is not None
        assert self._preset_lock_label is not None
        preset_chosen = self._preset_combo.currentText() != _NO_PRESET
        for name, widget in self._param_widgets.items():
            decl = PerParticleDonut.parameters[name]
            # Preset locks ALL widgets except the requires-gated ones, which
            # are independently disabled by _refresh_requires_gating().
            if preset_chosen:
                widget.setEnabled(False)
                widget.setToolTip("Preset locked")
            else:
                # Restore tooltip to declaration's desc; requires-gating may
                # re-disable below.
                widget.setEnabled(True)
                widget.setToolTip(decl.desc or "")
        self._preset_lock_label.setText("🔒 Preset locked" if preset_chosen else "")

    def _refresh_requires_gating(self) -> None:
        """BoolParam.requires controls widget-enabled when no preset is
        chosen. (Preset lock takes precedence.)"""
        assert self._preset_combo is not None
        if self._preset_combo.currentText() != _NO_PRESET:
            return  # preset lock already disabled everything
        layer_map = self._resolve_layer_map()
        groups_satisfied = self._compute_groups_satisfied()
        for pname, decl in PerParticleDonut.parameters.items():
            if not isinstance(decl, BoolParam) or not decl.requires:
                continue
            widget = self._param_widgets[pname]
            missing = []
            for req in decl.requires:
                if req in layer_map:
                    continue
                if groups_satisfied.get(req, False):
                    continue
                missing.append(req)
            if missing:
                widget.setEnabled(False)
                widget.setToolTip(f"Requires {missing[0]!r} to be assigned")
                # Force value to False so requires-gate at run time can't fire.
                if pname in self._param_setters:
                    self._param_setters[pname](False)
            else:
                widget.setEnabled(True)
                widget.setToolTip(decl.desc or "")

    def _refresh_outputs_panel(self) -> None:
        """Update each output label's styling based on produced_when."""
        from percell4.domain.analysis import GroupState

        groups_satisfied = self._compute_groups_satisfied()
        layer_map = self._resolve_layer_map()
        params = self._collect_params(include_defaults=True)

        # Build a GroupState with both group satisfaction and role-supplied
        # flags, mirroring run_analysis's contract.
        flags: dict[str, bool] = {}
        for gname in PerParticleDonut.input_groups:
            flags[f"{gname}_satisfied"] = groups_satisfied.get(gname, False)
        all_roles = set(PerParticleDonut.required_inputs)
        all_roles |= set(PerParticleDonut.optional_inputs)
        for g in PerParticleDonut.input_groups.values():
            all_roles |= set(g)
        for r in all_roles:
            flags[f"{r}_supplied"] = r in layer_map
        gs = GroupState(**flags)

        for name, decl in PerParticleDonut.outputs.items():
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
                label.setStyleSheet("color: #555555; text-decoration: line-through;")
                label.setText(f"  {name}")

    def _refresh_start_button(self) -> None:
        assert self._start_btn is not None
        # Compute each gate independently so the tooltip can name the
        # actual cause.
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
        if self._cap_combo is None or self._cap_combo.currentText() == LAYER_SENTINEL:
            return "Assign the Cap channel."
        groups_satisfied = self._compute_groups_satisfied()
        if not any(groups_satisfied.values()):
            return (
                "Assign at least one full branch — P-body (pbody_mask + pnorm) "
                "or SG (sg_mask + sgnorm)."
            )
        if not self._resolve_output_parent():
            return "Choose an output folder."
        return None

    def _update_status_label(self, messages: list[str]) -> None:
        if self._status_label is None:
            return
        self._status_label.setText("\n".join(messages))

    # ── Selection helpers ───────────────────────────────────────

    def _resolve_layer_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self._cap_combo is not None and self._cap_combo.currentText() != LAYER_SENTINEL:
            out["cap"] = self._cap_combo.currentText()
        for group_name, _label, role_names in _BRANCHES:
            if self._branch_skip[group_name].isChecked():
                continue
            for role in role_names:
                combo = self._branch_combos[group_name][role]
                text = combo.currentText()
                if text and text != LAYER_SENTINEL:
                    out[role] = text
        if self._cp_mask_combo is not None:
            text = self._cp_mask_combo.currentText()
            if text and text != LAYER_SENTINEL:
                out["cp_mask"] = text
        return out

    def _compute_groups_satisfied(self) -> dict[str, bool]:
        layer_map = self._resolve_layer_map()
        out: dict[str, bool] = {}
        for group_name, _label, role_names in _BRANCHES:
            if self._branch_skip[group_name].isChecked():
                out[group_name] = False
                continue
            out[group_name] = all(r in layer_map for r in role_names)
        return out

    def _collect_params(self, *, include_defaults: bool = False) -> dict[str, Any]:
        """Snapshot the parameter values from the widgets.

        ``include_defaults`` is True when the snapshot is for the outputs-panel
        prediction (we want to see the active widget values even if a preset
        will be sent at run time).
        """
        return {name: getter() for name, getter in self._param_getters.items()}

    def _resolve_output_parent(self) -> Path | None:
        if self._output_parent_line is None:
            return None
        text = self._output_parent_line.text().strip()
        if not text:
            return None
        p = Path(text)
        return p if p.exists() and p.is_dir() else None

    def _on_preset_changed(self, _idx: int) -> None:
        assert self._preset_combo is not None
        preset = self._preset_combo.currentText()
        if preset == _NO_PRESET:
            # No-op — the preset combo's "No preset" path doesn't push values
            # back to the widgets. The user keeps whatever they edited.
            pass
        else:
            values = PerParticleDonut.presets.get(preset, {})
            for name, value in values.items():
                if name in self._param_setters:
                    self._param_setters[name](value)
        self._refresh_state()

    # ── Run mechanics ───────────────────────────────────────────

    def _on_start_clicked(self) -> None:
        assert self._start_btn is not None
        layer_map = self._resolve_layer_map()
        params = self._collect_params()
        preset = self._preset_combo.currentText() if self._preset_combo else _NO_PRESET
        if preset == _NO_PRESET:
            preset = None
        output_parent = self._resolve_output_parent()
        if output_parent is None:
            return  # Start was incorrectly enabled

        # Persist the chosen output parent.
        persist_output_parent(_QSETTINGS_OUTPUT_KEY, str(output_parent))

        # Disable controls during the run.
        self._start_btn.setEnabled(False)

        n_total = len(self._h5_paths)
        progress = QProgressDialog(
            "Per-particle donut analysis", "Cancel", 0, n_total, self
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def on_progress(
            item: BatchAnalysisItemResult, idx: int, total: int
        ) -> None:
            progress.setValue(idx + 1)
            progress.setLabelText(
                f"Dataset {idx + 1}/{total}: {item.h5_path.name} — {item.status}"
            )
            QApplication.processEvents()

        def cancel_check() -> bool:
            return bool(progress.wasCanceled())

        # If a preset is in use, the runner expects params=None.
        runner_params = None if preset is not None else params

        try:
            report = self._orchestrator(
                "per_particle_donut",
                self._h5_paths,
                lambda _p: layer_map,
                output_parent=output_parent,
                params=runner_params,
                preset=preset,
                progress_callback=on_progress,
                cancel_check=cancel_check,
                log=print,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("per-particle donut analysis raised")
            progress.close()
            QMessageBox.critical(
                self,
                "Per-particle donut analysis",
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
        active = getattr(session, "active_dataset_path", None) if session else None
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
        total = len(report.items)
        if report.cancelled:
            icon = QMessageBox.Information
            title = "Analysis cancelled"
            text = (
                f"Cancelled after {total} of {len(self._h5_paths)} datasets.\n"
                f"Succeeded: {succeeded}, Failed: {failed}.\n"
                f"Run folder: {report.run_folder}"
            )
        elif failed > 0:
            icon = QMessageBox.Warning
            title = "Analysis completed with failures"
            text = (
                f"Succeeded: {succeeded}, Failed: {failed}.\n"
                f"Run folder: {report.run_folder}\n\n"
                + "\n".join(
                    f"FAILED: {i.h5_path.name}: {i.error}"
                    for i in report.items
                    if i.status == "failed"
                )
            )
        else:
            icon = QMessageBox.Information
            title = "Analysis complete"
            text = (
                f"{succeeded} datasets succeeded.\n"
                f"Run folder: {report.run_folder}"
            )
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.exec_()


# Late binding: register this dialog on the PerParticleDonut class so the
# Scripts tab (U8) can read it generically via ``cls.dialog_class``.
PerParticleDonut.dialog_class = PerParticleDonutDialog
