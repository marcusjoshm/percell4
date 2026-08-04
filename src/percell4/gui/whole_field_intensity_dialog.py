"""Whole-field decapping-sensor intensity analysis dialog.

Concrete per-analysis ``QDialog`` for the ``WholeFieldIntensity`` analysis,
mirroring :class:`PerParticleDonutDialog`: required role combos, an
optional-masks section, a parameter section with a preset combo + lock,
requires-gating, and bg-value spins that enable only when their mode is
``"manual"``. The four intermediate masks are plain optional combos; the
``intermediate_assemblies`` checkbox is requires-gated on all four being
mapped (the framework-consistent way to express "needs these masks").
Mutually-exclusive option combinations are enforced by the module's
``run()`` ValueError guards (the dialog's Start can still dispatch them; the
batch runner reports the failed item).

This dialog is an **Action**. Side effect on import: sets
``WholeFieldIntensity.dialog_class = WholeFieldIntensityDialog``.
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
from percell4.application.analysis.modules.whole_field_intensity import (
    WholeFieldIntensity,
)
from percell4.domain.analysis import BoolParam
from percell4.gui._dialog_utils import (
    cap_to_screen,
    center_on_screen,
    detach_window,
    wrap_in_scroll,
)
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
from percell4.io.paths import scan_files
from percell4.store import DatasetStore

logger = logging.getLogger(__name__)

_QSETTINGS_OUTPUT_KEY = "analysis/whole_field_intensity/output_parent"
_NO_PRESET = "No preset"

# Optional mask/label roles shown as always-enabled combos.
_OPTIONAL_ROLES = (
    "cp_mask", "mng_mask", "interaction_mask", "sir_mask",
    "dcp2_mask_2", "interaction_mask_2",
)
# bg-mode choice param -> the manual-value IntParam it gates.
_BG_VALUE_PARAMS = {"mng_bg_mode": "mng_bg_value",
                    "halo_bg_mode": "halo_bg_value"}


class WholeFieldIntensityDialog(QDialog):
    """Modal dialog for the whole-field decapping-sensor analysis."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        orchestrator: Callable[..., BatchAnalysisReport] = batch_run_analysis,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(WholeFieldIntensity.display_name)
        self.setMinimumWidth(720)
        self.resize(840, 760)
        cap_to_screen(self)
        detach_window(self)
        center_on_screen(self)

        self._host = parent
        self._orchestrator = orchestrator

        self._h5_paths: list[Path] = []
        self._last_report: BatchAnalysisReport | None = None
        self._cancelled: bool = False

        self._dataset_list: QListWidget | None = None
        # role -> combo (required + optional).
        self._role_combos: dict[str, QComboBox] = {}
        # role -> its row label, so preset-hidden rows can be hidden whole.
        self._role_labels: dict[str, QLabel] = {}
        self._param_widgets: dict[str, QWidget] = {}
        self._param_getters: dict[str, Callable[[], Any]] = {}
        self._param_setters: dict[str, Callable[[Any], None]] = {}
        self._preset_combo: QComboBox | None = None
        self._preset_lock_label: QLabel | None = None
        self._output_labels: dict[str, QLabel] = {}
        self._output_parent_line: Any = None
        self._start_btn: QPushButton | None = None
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
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(cancel_btn)
        outer.addLayout(btn_row)

    def _build_header(self) -> QWidget:
        box = QWidget(self)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel(WholeFieldIntensity.display_name, self)
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        desc = QLabel(WholeFieldIntensity.description, self)
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

        for role in ("condensate_mask", "dilute_mask", "halo", "mng"):
            decl = WholeFieldIntensity.required_inputs[role]
            row, label, combo = build_layer_combo(role, decl.desc, self)
            combo.activated.connect(lambda _i: self._refresh_state())
            self._role_combos[role] = combo
            self._role_labels[role] = label
            layout.addLayout(row)

        opt_box = QGroupBox("Optional masks", self)
        opt_layout = QVBoxLayout(opt_box)
        for role in _OPTIONAL_ROLES:
            decl = WholeFieldIntensity.optional_inputs[role]
            row, label, combo = build_layer_combo(role, decl.desc, self)
            combo.activated.connect(lambda _i: self._refresh_state())
            self._role_combos[role] = combo
            self._role_labels[role] = label
            opt_layout.addLayout(row)
        layout.addWidget(opt_box)
        return box

    def _build_section_params(self) -> QGroupBox:
        box = QGroupBox("3. Parameters")
        outer = QVBoxLayout(box)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:", self))
        self._preset_combo = build_preset_combo(
            WholeFieldIntensity.presets, self, no_preset_label=_NO_PRESET
        )
        self._preset_combo.activated.connect(self._on_preset_changed)
        preset_row.addWidget(self._preset_combo, 1)
        self._preset_lock_label = QLabel("", self)
        self._preset_lock_label.setStyleSheet("color: #f4d35e;")
        preset_row.addWidget(self._preset_lock_label)
        outer.addLayout(preset_row)

        form = QFormLayout()
        for name, decl in WholeFieldIntensity.parameters.items():
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
        for name in WholeFieldIntensity.outputs:
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
        if paths:
            self._add_paths([Path(p) for p in paths])

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Add folder of .h5", ""
        )
        if not folder:
            return
        candidates = scan_files(folder, "*.h5", "*.hdf5")
        if candidates:
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
            if not resolved.is_file() or resolved in existing:
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
        self._refresh_preset_lock()
        self._refresh_hidden_roles()
        self._refresh_requires_gating()
        self._refresh_bg_value_enabled()
        self._refresh_channel_cell_mean_enabled()
        self._refresh_outputs_panel()
        self._refresh_start_button()

    def _gather_layer_inventory(self) -> dict[str, list[str]]:
        inventory: dict[str, list[str]] = {
            "intensity": [], "mask": [], "label": []
        }
        if not self._h5_paths:
            return inventory
        i_sets: list[set[str]] = []
        m_sets: list[set[str]] = []
        l_sets: list[set[str]] = []
        for path in self._h5_paths:
            try:
                store = DatasetStore(path)
                meta = store.metadata
                channel_names = list(meta.get("channel_names") or [])
                decay_names = list(store.list_groups("decay"))
                i_sets.append(set(channel_names) | set(decay_names))
                m_sets.append(set(store.list_masks()))
                l_sets.append(set(store.list_labels()))
            except Exception:  # noqa: BLE001
                logger.exception("layer inventory read failed for %s", path)
                return {"intensity": [], "mask": [], "label": []}
        if i_sets:
            inventory["intensity"] = sorted(set.intersection(*i_sets))
            inventory["mask"] = sorted(set.intersection(*m_sets))
            inventory["label"] = sorted(set.intersection(*l_sets))
        return inventory

    def _refresh_combos(self, inventory: dict[str, list[str]]) -> None:
        all_roles = dict(WholeFieldIntensity.required_inputs)
        all_roles.update(WholeFieldIntensity.optional_inputs)
        for role, combo in self._role_combos.items():
            kind = all_roles[role].kind
            populate_layer_combo(combo, inventory.get(kind, []))

    def _refresh_preset_lock(self) -> None:
        assert self._preset_combo is not None
        assert self._preset_lock_label is not None
        preset_chosen = self._preset_combo.currentText() != _NO_PRESET
        editable = set(WholeFieldIntensity.preset_editable_params)
        for name, widget in self._param_widgets.items():
            decl = WholeFieldIntensity.parameters[name]
            # A preset locks its science params, but "mode" params it declares
            # editable (e.g. single_cell) stay clickable — their final
            # enabled state is then refined by the requires/halo gating below.
            if preset_chosen and name not in editable:
                widget.setEnabled(False)
                widget.setToolTip("Preset locked")
            else:
                widget.setEnabled(True)
                widget.setToolTip(decl.desc or "")
        self._preset_lock_label.setText(
            "🔒 Preset locked (mode params stay editable)"
            if preset_chosen else ""
        )

    def _refresh_hidden_roles(self) -> None:
        """Hide the rows of roles the active preset declares hidden.

        Hides the whole row (label + combo) — both widgets ``setVisible``
        ``False`` so the row collapses while ``wrap_in_scroll`` keeps the
        section scroll-safe. The combo VALUE is **not** cleared; hidden
        roles are excluded at :meth:`_resolve_layer_map`, so switching to a
        non-hiding preset shows the row again with the prior selection
        intact (restore-not-clear).
        """
        hidden = set(self._preset_hidden_roles())
        for role, combo in self._role_combos.items():
            visible = role not in hidden
            combo.setVisible(visible)
            label = self._role_labels.get(role)
            if label is not None:
                label.setVisible(visible)

    def _refresh_requires_gating(self) -> None:
        assert self._preset_combo is not None
        preset_chosen = self._preset_combo.currentText() != _NO_PRESET
        editable = set(WholeFieldIntensity.preset_editable_params)
        layer_map = self._resolve_layer_map()
        for pname, decl in WholeFieldIntensity.parameters.items():
            if not isinstance(decl, BoolParam) or not decl.requires:
                continue
            # Under a preset, only the editable "mode" params are gated here;
            # everything else is already locked by _refresh_preset_lock.
            if preset_chosen and pname not in editable:
                continue
            widget = self._param_widgets[pname]
            missing = [req for req in decl.requires if req not in layer_map]
            if missing:
                widget.setEnabled(False)
                widget.setToolTip(f"Requires {missing[0]!r} to be assigned")
                self._param_setters[pname](False)
            else:
                widget.setEnabled(True)
                widget.setToolTip(decl.desc or "")

    def _refresh_bg_value_enabled(self) -> None:
        """Enable a manual-bg-value spin only when its mode == 'manual'.

        Skipped under a preset lock (everything is already disabled).
        """
        assert self._preset_combo is not None
        if self._preset_combo.currentText() != _NO_PRESET:
            return
        for mode_param, value_param in _BG_VALUE_PARAMS.items():
            is_manual = self._param_getters[mode_param]() == "manual"
            self._param_widgets[value_param].setEnabled(is_manual)

    def _refresh_channel_cell_mean_enabled(self) -> None:
        """Grey the channel_cell_mean checkbox unless single_cell is on.

        UX-only gating (the core no-ops it outside single-cell mode and it
        carries NO ``requires=cp_mask`` so a stray True never raises). When
        single_cell is off, the checkbox is unchecked so a disabled-but-checked
        state can't leak into the run. ``channel_cell_mean`` is a preset-editable
        mode param, so this gating applies under a preset too (single_cell may
        be toggled there).
        """
        assert self._preset_combo is not None
        single_cell = bool(self._param_getters["single_cell"]())
        widget = self._param_widgets["channel_cell_mean"]
        widget.setEnabled(single_cell)
        if not single_cell:
            self._param_setters["channel_cell_mean"](False)
        widget.setToolTip(
            WholeFieldIntensity.parameters["channel_cell_mean"].desc or ""
            if single_cell
            else "Enable single_cell (needs cp_mask) to add the whole-cell "
            "channel means (mNG + Halo)."
        )

    def _refresh_outputs_panel(self) -> None:
        from percell4.domain.analysis import GroupState

        layer_map = self._resolve_layer_map()
        params = self._collect_params()
        all_roles = set(WholeFieldIntensity.required_inputs)
        all_roles |= set(WholeFieldIntensity.optional_inputs)
        flags = {f"{r}_supplied": (r in layer_map) for r in all_roles}
        gs = GroupState(**flags)
        for name, decl in WholeFieldIntensity.outputs.items():
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
        self._start_btn.setEnabled(reason is None)
        self._start_btn.setToolTip(reason or "Begin the analysis run.")

    def _start_disabled_reason(self) -> str | None:
        if not self._h5_paths:
            return "Add at least one dataset to begin."
        layer_map = self._resolve_layer_map()
        for role in ("condensate_mask", "dilute_mask", "halo", "mng"):
            if role not in layer_map:
                return f"Assign the required role {role!r}."
        preset = self._active_preset()
        for role in self._preset_required_roles():
            if role not in layer_map:
                return f"Preset {preset!r} requires {role!r}."
        if not self._resolve_output_parent():
            return "Choose an output folder."
        return None

    # ── Selection helpers ───────────────────────────────────────

    def _resolve_layer_map(self) -> dict[str, str]:
        # Exclude preset-hidden roles WITHOUT clearing their combo, so a
        # switch to a non-hiding preset restores the prior selection.
        hidden = set(self._preset_hidden_roles())
        out: dict[str, str] = {}
        for role, combo in self._role_combos.items():
            if role in hidden:
                continue
            text = combo.currentText()
            if text and text != LAYER_SENTINEL:
                out[role] = text
        return out

    def _active_preset(self) -> str | None:
        """The active preset name, or ``None`` when 'No preset' is chosen."""
        if self._preset_combo is None:
            return None
        text = self._preset_combo.currentText()
        return None if text == _NO_PRESET else text

    def _preset_required_roles(self) -> tuple[str, ...]:
        """Roles the active preset requires (empty when no preset)."""
        preset = self._active_preset()
        if preset is None:
            return ()
        mapping = getattr(WholeFieldIntensity, "preset_required_inputs", {})
        return tuple(mapping.get(preset, ()))

    def _preset_hidden_roles(self) -> tuple[str, ...]:
        """Roles the active preset hides (empty when no preset)."""
        preset = self._active_preset()
        if preset is None:
            return ()
        mapping = getattr(WholeFieldIntensity, "preset_hidden_inputs", {})
        return tuple(mapping.get(preset, ()))

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

    def _on_preset_changed(self, _idx: int) -> None:
        assert self._preset_combo is not None
        preset = self._preset_combo.currentText()
        if preset != _NO_PRESET:
            for name, value in WholeFieldIntensity.presets.get(
                preset, {}
            ).items():
                if name in self._param_setters:
                    self._param_setters[name](value)
        self._refresh_state()

    # ── Run mechanics ───────────────────────────────────────────

    def _on_start_clicked(self) -> None:
        assert self._start_btn is not None
        layer_map = self._resolve_layer_map()
        params = self._collect_params()
        preset = (self._preset_combo.currentText()
                  if self._preset_combo else _NO_PRESET)
        if preset == _NO_PRESET:
            preset = None
        output_parent = self._resolve_output_parent()
        if output_parent is None:
            return

        persist_output_parent(_QSETTINGS_OUTPUT_KEY, str(output_parent))
        self._start_btn.setEnabled(False)

        n_total = len(self._h5_paths)
        progress = QProgressDialog(
            "Whole-field intensity analysis", "Cancel", 0, n_total, self
        )
        # Non-modal: the loop pumps events itself and the form is disabled.
        progress.setWindowModality(Qt.NonModal)
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

        # Under a preset, pass only the editable "mode" params (single_cell,
        # channel_cell_mean) as an overlay — resolve_params merges them onto the
        # preset, keeping the preset's science values authoritative and the
        # preset name in the run's provenance.
        if preset is not None:
            editable = set(WholeFieldIntensity.preset_editable_params)
            runner_params = {k: v for k, v in params.items() if k in editable}
        else:
            runner_params = params

        try:
            report = self._orchestrator(
                "whole_field_intensity",
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
            logger.exception("whole-field intensity analysis raised")
            progress.close()
            QMessageBox.critical(
                self, "Whole-field intensity analysis",
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
        lines = [
            f"Succeeded: {report.succeeded_count}",
            f"Failed: {report.failed_count}",
            f"Run folder: {report.run_folder}",
        ]
        if report.cancelled:
            lines.append("Run was cancelled before completing all datasets.")
        QMessageBox.information(
            self, "Whole-field intensity analysis", "\n".join(lines)
        )


# Late-binding: attach the dialog class so the Scripts tab can open it.
WholeFieldIntensity.dialog_class = WholeFieldIntensityDialog
