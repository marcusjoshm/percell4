"""Settings widget for the Iterative Otsu Thresholding module.

A reusable form (scope / guard-ring radius / max iterations / Gaussian σ + a
toggleable, parameterised stopping criterion per row) that snapshots into a
frozen :class:`IterativeOtsuConfig`. Owns only Action-shaped per-run knobs —
channel / segmentation remain Session-owned Selectors. Mirrors
``gui/_adaptive_clip_settings.py`` (frozen ``current_config()`` + aggregated
``config_changed`` signal).

The ``groups`` scope needs the measure+grouper pipeline, so the GUI offers only
``per-cell`` and ``whole-field`` (both label-only); ``groups`` is available via
``percell4-batch-threshold --strategy iterative-otsu``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# Scope dropdown: (display label, internal code).
_SCOPE_OPTIONS = (("Per cell", "per-cell"), ("Whole field", "whole-field"))

# Stopping criteria, in display order. Each row is one checkbox + one param spin.
# (criterion, param_key, kind, lo, hi, step, default, checked_by_default, label)
# Defaults are a starting point for experimentation, not a tuned recommendation;
# they track the fixture-derived defaults in domain/measure/iterative_otsu.py.
_CRITERIA: tuple[tuple[str, str, str, float, float, float, float, bool, str], ...] = (
    ("bg-floor", "k", "float", 0.0, 10.0, 0.25, 2.0, True, "Background floor (thr ≤ bg + k·σ)"),
    ("positive-fraction-high", "max_frac", "float", 0.0, 1.0, 0.05, 0.5, True,
     "Positive-fraction high (≥)"),
    ("separability", "min_eta", "float", 0.0, 1.0, 0.05, 0.8, False, "Otsu separability η (<)"),
    ("min-positive", "min_px", "int", 0, 100_000, 1, 5, False, "Min positive px (<)"),
    ("diminishing-returns", "min_gain_frac", "float", 0.0, 1.0, 0.01, 0.02, False,
     "Diminishing returns (gain frac <)"),
    ("peak-prominence", "k", "float", 0.0, 10.0, 0.25, 3.0, False,
     "Peak prominence (max−bg < k·σ)"),
    ("min-area-components", "min_area_px", "int", 0, 100_000, 1, 3, False,
     "Min particle area px (<)"),
)


@dataclass(frozen=True)
class IterativeOtsuConfig:
    """Immutable snapshot of the iterative-Otsu settings widget.

    ``stop_params`` carries dotted-namespaced ``("<criterion>.<param>", value)``
    pairs for the *checked* criteria only — the exact shape
    :class:`~percell4.workflows.models.IterativeOtsuSettings` expects.
    """

    scope: str
    dilation_radius_px: int
    max_rounds: int
    gaussian_sigma: float
    stop_criteria: tuple[str, ...]
    stop_params: tuple[tuple[str, Any], ...]
    stop_combine: str
    # When set, the peel runs exactly this many iterations per unit and the
    # stop criteria are blocked. ``None`` keeps the criteria-driven mode.
    fixed_iterations: int | None = None


class IterativeOtsuSettingsWidget(QWidget):
    """Scope / dilation / max-iterations / σ + a stopping-criteria group.

    Emits :attr:`config_changed` whenever any child widget's user-edit signal
    fires. Each criterion's parameter spinbox is enabled only while its checkbox
    is checked.
    """

    config_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # criterion -> (checkbox, spinbox, param_key)
        self._rows: dict[str, tuple[QCheckBox, QSpinBox | QDoubleSpinBox, str]] = {}
        self._build_ui()
        self._connect_change_signals()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ── Scope ──
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Scope:"))
        self._scope = QComboBox()
        for label, code in _SCOPE_OPTIONS:
            self._scope.addItem(label, code)
        scope_row.addWidget(self._scope)
        scope_row.addStretch()
        layout.addLayout(scope_row)

        # ── Guard-ring radius ──
        dil_row = QHBoxLayout()
        dil_row.addWidget(QLabel("Guard-ring radius (px):"))
        self._dilation = QSpinBox()
        self._dilation.setRange(1, 50)
        self._dilation.setValue(5)
        dil_row.addWidget(self._dilation)
        layout.addLayout(dil_row)

        # ── Max iterations (relabelled "Iterations:" in fixed-count mode) ──
        mr_row = QHBoxLayout()
        self._mr_label = QLabel("Max iterations:")
        mr_row.addWidget(self._mr_label)
        self._max_rounds = QSpinBox()
        self._max_rounds.setRange(1, 100)
        self._max_rounds.setValue(10)
        mr_row.addWidget(self._max_rounds)
        layout.addLayout(mr_row)

        # ── Fixed-count mode: blocks the stopping criteria ──
        fixed_row = QHBoxLayout()
        self._fixed_mode = QCheckBox("Fixed iteration count (ignore stopping criteria)")
        fixed_row.addWidget(self._fixed_mode)
        fixed_row.addStretch()
        layout.addLayout(fixed_row)

        # ── Gaussian sigma ──
        sig_row = QHBoxLayout()
        sig_row.addWidget(QLabel("Gaussian σ:"))
        self._sigma = QDoubleSpinBox()
        self._sigma.setRange(0.0, 20.0)
        self._sigma.setSingleStep(0.5)
        self._sigma.setValue(1.0)
        self._sigma.setSpecialValueText("None")
        sig_row.addWidget(self._sigma)
        layout.addLayout(sig_row)

        # ── Stopping criteria ──
        crit_group = QGroupBox("Stopping criteria")
        self._crit_group = crit_group
        crit_layout = QVBoxLayout(crit_group)

        combine_row = QHBoxLayout()
        combine_row.addWidget(QLabel("Stop a cell when"))
        self._combine = QComboBox()
        self._combine.addItems(["any", "all"])
        combine_row.addWidget(self._combine)
        combine_row.addWidget(QLabel("checked criteria fire"))
        combine_row.addStretch()
        crit_layout.addLayout(combine_row)

        for crit, key, kind, lo, hi, step, default, checked, label in _CRITERIA:
            row = QHBoxLayout()
            cb = QCheckBox(label)
            cb.setChecked(checked)
            row.addWidget(cb)
            row.addStretch()
            if kind == "float":
                spin: QSpinBox | QDoubleSpinBox = QDoubleSpinBox()
                spin.setRange(float(lo), float(hi))
                spin.setSingleStep(float(step))
                spin.setValue(float(default))
            else:
                spin = QSpinBox()
                spin.setRange(int(lo), int(hi))
                spin.setSingleStep(int(step))
                spin.setValue(int(default))
            spin.setEnabled(checked)
            cb.toggled.connect(spin.setEnabled)
            row.addWidget(spin)
            crit_layout.addLayout(row)
            self._rows[crit] = (cb, spin, key)

        layout.addWidget(crit_group)

    def _connect_change_signals(self) -> None:
        self._scope.currentIndexChanged.connect(self.config_changed)
        self._dilation.valueChanged.connect(self.config_changed)
        self._max_rounds.valueChanged.connect(self.config_changed)
        self._sigma.valueChanged.connect(self.config_changed)
        self._combine.currentIndexChanged.connect(self.config_changed)
        self._fixed_mode.toggled.connect(self._on_fixed_toggled)
        self._fixed_mode.toggled.connect(self.config_changed)
        for cb, spin, _ in self._rows.values():
            cb.toggled.connect(self.config_changed)
            spin.valueChanged.connect(self.config_changed)

    def _on_fixed_toggled(self, checked: bool) -> None:
        """Fixed-count mode blocks the criteria: grey the group and relabel."""
        self._crit_group.setEnabled(not checked)
        self._mr_label.setText("Iterations:" if checked else "Max iterations:")

    # ── Public API ────────────────────────────────────────────────

    def current_config(self) -> IterativeOtsuConfig:
        """Snapshot the live widget state into a frozen dataclass."""
        criteria: list[str] = []
        params: list[tuple[str, Any]] = []
        for crit, (cb, spin, key) in self._rows.items():
            if cb.isChecked():
                criteria.append(crit)
                params.append((f"{crit}.{key}", spin.value()))
        fixed = int(self._max_rounds.value()) if self._fixed_mode.isChecked() else None
        return IterativeOtsuConfig(
            scope=self._scope.currentData(),
            dilation_radius_px=int(self._dilation.value()),
            max_rounds=int(self._max_rounds.value()),
            gaussian_sigma=float(self._sigma.value()),
            stop_criteria=tuple(criteria),
            stop_params=tuple(params),
            stop_combine=self._combine.currentText(),
            fixed_iterations=fixed,
        )

    def set_enabled(self, enabled: bool) -> None:
        """Lock/unlock all widgets during a run (preserves each criterion's gate)."""
        fixed = self._fixed_mode.isChecked()
        for w in (self._scope, self._dilation, self._max_rounds, self._sigma, self._fixed_mode):
            w.setEnabled(enabled)
        # The criteria group (incl. combine) stays greyed in fixed-count mode.
        self._crit_group.setEnabled(enabled and not fixed)
        for cb, spin, _ in self._rows.values():
            cb.setEnabled(enabled and not fixed)
            spin.setEnabled(enabled and not fixed and cb.isChecked())
