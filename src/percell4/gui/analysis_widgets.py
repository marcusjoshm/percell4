"""Shared widget factories for analysis dialogs.

Pure-Qt helpers — no domain imports, no ``Analysis`` import — so domain
remains GUI-free. Each concrete per-analysis dialog (e.g.
:class:`PerParticleDonutDialog`) composes these factories into its
hand-laid layout. Per the plan, the framework does NOT generate a
dialog from the schema; the factories absorb only the repeated
layout boilerplate.

Factory inventory:

- :func:`build_dataset_picker` — list + add-files / add-folder buttons.
- :func:`build_layer_combo` — labeled QComboBox row for one role.
- :func:`populate_layer_combo` — fill a combo with the intersection of
  available layer names across selected datasets, with a leading
  sentinel item for "no selection / skip".
- :func:`build_param_widget` — dispatch ``IntParam`` / ``FloatParam`` /
  ``BoolParam`` / ``ChoiceParam`` to a Qt widget + getter/setter pair.
- :func:`build_preset_combo` — preset picker with "No preset" sentinel.
- :func:`build_output_parent_picker` — line edit + Browse button bound
  to ``QSettings`` for persistence.

These are tested independently of any concrete analysis dialog so the
factory contracts are pinned cleanly.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from qtpy.QtCore import QSettings
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QWidget,
)

# Sentinel item shown as the first entry in every layer combo. Selecting
# it means "this role is not supplied" — the dialog should treat it as
# "absent from the resolved layer_map."
LAYER_SENTINEL = "—"


# ── Dataset picker ─────────────────────────────────────────────


def build_dataset_picker(
    parent: QWidget,
) -> tuple[QListWidget, QPushButton, QPushButton]:
    """Return ``(QListWidget, add_files_button, add_folder_button)``.

    The caller wires the buttons' ``clicked`` signals to file/folder
    dialogs and owns the list of resolved paths. The factory does NOT
    open any file dialogs itself.
    """
    list_widget = QListWidget(parent)
    list_widget.setSelectionMode(QListWidget.NoSelection)
    list_widget.setMinimumHeight(120)

    add_files_btn = QPushButton("Add .h5 files…", parent)
    add_folder_btn = QPushButton("Add folder of .h5…", parent)

    return list_widget, add_files_btn, add_folder_btn


# ── Layer combo row ────────────────────────────────────────────


def build_layer_combo(
    role_name: str,
    role_desc: str,
    parent: QWidget,
) -> tuple[QHBoxLayout, QLabel, QComboBox]:
    """One labeled combo row for a single role."""
    row = QHBoxLayout()
    label_text = role_name if not role_desc else f"{role_name} — {role_desc}"
    label = QLabel(label_text, parent)
    label.setMinimumWidth(220)
    combo = QComboBox(parent)
    combo.addItem(LAYER_SENTINEL)
    row.addWidget(label)
    row.addWidget(combo, 1)
    return row, label, combo


def populate_layer_combo(
    combo: QComboBox,
    available_names: list[str],
    *,
    sentinel: str = LAYER_SENTINEL,
) -> bool:
    """Replace the combo's items with ``[sentinel, *sorted(available_names)]``.

    Preserves the prior selection if it appears in ``available_names``.
    Returns ``True`` when the prior selection was preserved, ``False``
    when it was snapped back to the sentinel (either because there was
    no prior selection, or because it no longer exists in the new list).
    Callers can use the return value to surface a stale-selection
    warning to the user.
    """
    prior = combo.currentText()
    sorted_names = sorted(set(available_names))

    combo.blockSignals(True)
    try:
        combo.clear()
        combo.addItem(sentinel)
        for name in sorted_names:
            combo.addItem(name)

        if prior and prior in sorted_names:
            idx = combo.findText(prior)
            if idx >= 0:
                combo.setCurrentIndex(idx)
                return True
        combo.setCurrentIndex(0)
        return False
    finally:
        combo.blockSignals(False)


# ── Parameter widget dispatch ──────────────────────────────────


def build_param_widget(
    param_decl: Any,
    parent: QWidget,
) -> tuple[QWidget, Callable[[], Any], Callable[[Any], None]]:
    """Build a Qt widget for a parameter declaration.

    Returns ``(widget, getter, setter)`` where ``getter()`` reads the
    user's current value and ``setter(value)`` writes a value
    *programmatically* (no user-edit signals fire on `setter` paths —
    this is the qt-wire-user-edit-signals contract).
    """
    # Import declarations lazily to keep this module domain-free at
    # module load — the dispatch uses ``type(param_decl).__name__``
    # so we don't need direct ``isinstance`` against the domain types.
    name = type(param_decl).__name__

    if name == "IntParam":
        return _build_int_param(param_decl, parent)
    if name == "FloatParam":
        return _build_float_param(param_decl, parent)
    if name == "BoolParam":
        return _build_bool_param(param_decl, parent)
    if name == "ChoiceParam":
        return _build_choice_param(param_decl, parent)
    raise ValueError(
        f"build_param_widget: unsupported declaration {name!r} "
        f"(expected IntParam/FloatParam/BoolParam/ChoiceParam)"
    )


def _build_int_param(
    decl: Any, parent: QWidget
) -> tuple[QSpinBox, Callable[[], int], Callable[[int], None]]:
    w = QSpinBox(parent)
    w.setMinimum(decl.min if decl.min is not None else -10_000_000)
    w.setMaximum(decl.max if decl.max is not None else 10_000_000)
    w.setValue(int(decl.default))
    if decl.desc:
        w.setToolTip(decl.desc)
    return w, lambda: int(w.value()), lambda v: _block_set(w, "setValue", int(v))


def _build_float_param(
    decl: Any, parent: QWidget
) -> tuple[QDoubleSpinBox, Callable[[], float], Callable[[float], None]]:
    w = QDoubleSpinBox(parent)
    w.setDecimals(4)
    w.setMinimum(decl.min if decl.min is not None else -1e9)
    w.setMaximum(decl.max if decl.max is not None else 1e9)
    w.setSingleStep(0.1)
    w.setValue(float(decl.default))
    if decl.desc:
        w.setToolTip(decl.desc)
    return (
        w,
        lambda: float(w.value()),
        lambda v: _block_set(w, "setValue", float(v)),
    )


def _build_bool_param(
    decl: Any, parent: QWidget
) -> tuple[QCheckBox, Callable[[], bool], Callable[[bool], None]]:
    w = QCheckBox(parent)
    w.setChecked(bool(decl.default))
    if decl.desc:
        w.setToolTip(decl.desc)
    return (
        w,
        lambda: bool(w.isChecked()),
        lambda v: _block_set(w, "setChecked", bool(v)),
    )


def _build_choice_param(
    decl: Any, parent: QWidget
) -> tuple[QComboBox, Callable[[], str], Callable[[str], None]]:
    w = QComboBox(parent)
    for choice in decl.choices:
        w.addItem(str(choice))
    default_idx = max(0, w.findText(str(decl.default)))
    w.setCurrentIndex(default_idx)
    if decl.desc:
        w.setToolTip(decl.desc)

    def setter(v: str) -> None:
        idx = w.findText(str(v))
        if idx >= 0:
            _block_set(w, "setCurrentIndex", idx)

    return w, lambda: w.currentText(), setter


def _block_set(widget: QWidget, method_name: str, value: Any) -> None:
    """Call a setter on a widget while blocking its signals.

    Used by the getter/setter closures so programmatic value updates do
    not fire user-edit signals — per the qt-wire-user-edit-signals
    learning, only user actions should cascade the dialog's
    ``_refresh_state``.
    """
    widget.blockSignals(True)
    try:
        getattr(widget, method_name)(value)
    finally:
        widget.blockSignals(False)


# ── Preset combo ───────────────────────────────────────────────


def build_preset_combo(
    presets: dict[str, dict[str, Any]],
    parent: QWidget,
    *,
    no_preset_label: str = "No preset",
) -> QComboBox:
    """Build a preset picker with ``no_preset_label`` as item 0."""
    combo = QComboBox(parent)
    combo.addItem(no_preset_label)
    for name in sorted(presets):
        combo.addItem(name)
        idx = combo.count() - 1
        combo.setItemData(idx, _format_preset_tooltip(presets[name]), 3)  # Qt.ToolTipRole == 3
    return combo


def _format_preset_tooltip(values: dict[str, Any]) -> str:
    lines = [f"{k}: {v}" for k, v in sorted(values.items())]
    return "\n".join(lines)


# ── Output-parent picker ───────────────────────────────────────


def build_output_parent_picker(
    qsettings_key: str,
    parent: QWidget,
    *,
    qsettings_org: str = "LeeLabPerCell4",
    qsettings_app: str = "PerCell4",
) -> tuple[QHBoxLayout, QLineEdit, QPushButton]:
    """LineEdit + Browse… for an output directory, persisted in QSettings."""
    line = QLineEdit(parent)
    qs = QSettings(qsettings_org, qsettings_app)
    prior = qs.value(qsettings_key, "", type=str)
    if prior:
        line.setText(str(prior))

    btn = QPushButton("Browse…", parent)

    def on_browse() -> None:
        start = line.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            parent, "Choose output folder", start
        )
        if chosen:
            line.setText(chosen)

    btn.clicked.connect(on_browse)

    row = QHBoxLayout()
    row.addWidget(line, 1)
    row.addWidget(btn)
    return row, line, btn


def persist_output_parent(
    qsettings_key: str,
    value: str,
    *,
    qsettings_org: str = "LeeLabPerCell4",
    qsettings_app: str = "PerCell4",
) -> None:
    qs = QSettings(qsettings_org, qsettings_app)
    qs.setValue(qsettings_key, value)
