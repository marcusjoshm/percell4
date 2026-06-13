"""Settings widget for the Adaptive Local Clipping module.

A reusable form (window / k / Gaussian σ / particle-size filter + unit / auto
window) that snapshots into a frozen :class:`AdaptiveClipConfig`. Owns only
Action-shaped per-run knobs — channel / segmentation remain Session-owned
Selectors. Mirrors ``gui/_grouped_threshold_settings.py`` (frozen
``current_config()`` + aggregated ``config_changed`` signal).
"""

from __future__ import annotations

from dataclasses import dataclass

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# Unit dropdown labels -> internal codes used by resolve_min_area_px.
_UNIT_LABELS = ("px²", "µm²")
_UNIT_CODES = {"px²": "px", "µm²": "um2"}

# Noise (sigma) estimate dropdown labels -> BACKGROUND_ESTIMATORS registry names.
# MAD is the default (matches the ImageJ reference macro and is robust to the
# black-background histogram spike that collapses the gaussian-peak fit on a
# whole frame). gaussian-peak stays available for parity with the per-cell
# pipeline default.
_NOISE_LABELS = ("MAD (robust)", "stddev", "gaussian-peak")
_NOISE_CODES = {"MAD (robust)": "mad", "stddev": "stddev", "gaussian-peak": "gaussian-peak"}


@dataclass(frozen=True)
class AdaptiveClipConfig:
    """Immutable snapshot of the adaptive-clip settings widget.

    ``window_px`` is always odd (the local window must be odd). ``min_size_unit``
    is the internal code (``"px"`` / ``"um2"``). When ``auto_window`` is True the
    ``window_px`` value is ignored by the run (it is estimated from an Otsu
    first-pass).
    """

    window_px: int
    k: float
    gaussian_sigma: float
    min_size_value: float
    min_size_unit: str
    auto_window: bool
    noise_estimator: str = "mad"
    # Particle-size mode. When ``particle_mode`` is True the run derives the
    # window + size filter from ``d_min_um`` (smallest particle Ø, µm) and uses
    # the per-cell detector with a robust per-cell MAD σ. ``k`` is still honored
    # (the sensitivity knob, default 1.0); ``window_px`` / ``min_size_*`` /
    # ``auto_window`` / ``noise_estimator`` are ignored in that mode.
    particle_mode: bool = False
    d_min_um: float = 0.40


class AdaptiveClipSettingsWidget(QWidget):
    """Window / k / σ / particle-size / auto-window form as one reusable widget.

    Emits :attr:`config_changed` whenever any child widget's user-edit signal
    fires. Checking "Auto adaptive window size" disables the window spinbox (it
    is computed at run time); unchecking re-enables it.
    """

    config_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._connect_change_signals()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ── Auto window size ──
        self._auto = QCheckBox("Auto adaptive window size")
        self._auto.setToolTip(
            "Estimate the window from an Otsu first-pass (sizes it to the granules)."
        )
        self._auto.toggled.connect(self._on_auto_toggled)
        layout.addWidget(self._auto)

        # ── Particle-size mode (the one-knob "just works" detector) ──
        self._particle = QCheckBox("Detect by smallest particle size")
        self._particle.setToolTip(
            "Drive the detector from ONE physical knob — the smallest particle "
            "diameter (µm) you want to detect. Window and size filter are derived "
            "from it and the noise floor is a robust per-cell MAD (so one setting "
            "transfers across cells/datasets). k stays adjustable (defaults to 1; "
            "raise it to be conservative). Needs an active segmentation and a "
            "known pixel size."
        )
        self._particle.toggled.connect(self._on_particle_toggled)
        layout.addWidget(self._particle)

        dmin_row = QHBoxLayout()
        dmin_row.addWidget(QLabel("Smallest particle Ø (µm):"))
        self._d_min = QDoubleSpinBox()
        self._d_min.setRange(0.02, 50.0)
        self._d_min.setDecimals(3)
        self._d_min.setSingleStep(0.05)
        self._d_min.setValue(0.40)
        self._d_min.setEnabled(False)  # off until particle mode is checked
        dmin_row.addWidget(self._d_min)
        layout.addLayout(dmin_row)

        # ── Adaptive window (px) ──
        win_row = QHBoxLayout()
        win_row.addWidget(QLabel("Window (px):"))
        self._window = QSpinBox()
        self._window.setRange(3, 151)
        self._window.setSingleStep(2)  # keep odd via the arrows
        self._window.setValue(15)
        win_row.addWidget(self._window)
        layout.addLayout(win_row)

        # ── k (sigma multiplier) ──
        k_row = QHBoxLayout()
        k_row.addWidget(QLabel("k (σ multiplier):"))
        self._k = QDoubleSpinBox()
        self._k.setRange(0.0, 20.0)
        self._k.setSingleStep(0.25)
        self._k.setValue(2.25)
        k_row.addWidget(self._k)
        layout.addLayout(k_row)

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

        # ── Noise (sigma) estimate ──
        noise_row = QHBoxLayout()
        noise_row.addWidget(QLabel("Noise (σ) estimate:"))
        self._noise = QComboBox()
        self._noise.addItems(list(_NOISE_LABELS))
        self._noise.setToolTip(
            "How the k·σ contrast margin is scaled. MAD (robust) matches the "
            "ImageJ reference and resists the black-background histogram spike "
            "that collapses gaussian-peak on a whole frame."
        )
        noise_row.addWidget(self._noise)
        layout.addLayout(noise_row)

        # ── Particle-size filter (value + unit) ──
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Min particle size:"))
        self._min_size = QDoubleSpinBox()
        self._min_size.setRange(0.0, 1_000_000.0)
        self._min_size.setDecimals(2)
        self._min_size.setValue(3.0)
        size_row.addWidget(self._min_size)
        self._unit = QComboBox()
        self._unit.addItems(list(_UNIT_LABELS))
        size_row.addWidget(self._unit)
        layout.addLayout(size_row)

    def _connect_change_signals(self) -> None:
        # Signal-to-signal forwarding: Qt drops the extra arg the child signals
        # carry (value / index / bool), so config_changed (0-arg) re-emits cleanly.
        self._window.valueChanged.connect(self.config_changed)
        self._k.valueChanged.connect(self.config_changed)
        self._sigma.valueChanged.connect(self.config_changed)
        self._min_size.valueChanged.connect(self.config_changed)
        self._unit.currentIndexChanged.connect(self.config_changed)
        self._noise.currentIndexChanged.connect(self.config_changed)
        self._auto.toggled.connect(self.config_changed)
        self._particle.toggled.connect(self.config_changed)
        self._d_min.valueChanged.connect(self.config_changed)

    # ── Slots ─────────────────────────────────────────────────────

    def _on_auto_toggled(self, checked: bool) -> None:  # noqa: ARG002
        self._apply_mode_gating()

    def _on_particle_toggled(self, checked: bool) -> None:
        # Adopt the validated one-knob default (k=1) when entering particle mode;
        # it stays editable so the user can raise k to be conservative (fewer
        # false positives, at the cost of missing dim sub-threshold particles).
        if checked:
            self._k.setValue(1.0)
        self._apply_mode_gating()

    def _apply_mode_gating(self) -> None:
        """Enable/disable fields for the active mode.

        Particle-size mode derives the spatial scale from ``d_min``, so window,
        the size filter, the noise estimate and auto-window go disabled. ``k``
        stays live in BOTH modes (the sensitivity knob), as do ``d_min`` and
        Gaussian σ. Outside particle mode the window respects the auto-window
        checkbox exactly as before.
        """
        particle = self._particle.isChecked()
        self._d_min.setEnabled(particle)
        for w in (self._min_size, self._unit, self._noise, self._auto):
            w.setEnabled(not particle)
        self._k.setEnabled(True)
        self._window.setEnabled(not particle and not self._auto.isChecked())

    # ── Public API ────────────────────────────────────────────────

    def current_config(self) -> AdaptiveClipConfig:
        """Snapshot the live widget state into a frozen dataclass (odd window)."""
        return AdaptiveClipConfig(
            window_px=int(self._window.value()) | 1,  # force odd
            k=float(self._k.value()),
            gaussian_sigma=float(self._sigma.value()),
            min_size_value=float(self._min_size.value()),
            min_size_unit=_UNIT_CODES[self._unit.currentText()],
            auto_window=self._auto.isChecked(),
            noise_estimator=_NOISE_CODES[self._noise.currentText()],
            particle_mode=self._particle.isChecked(),
            d_min_um=float(self._d_min.value()),
        )

    def set_window_value(self, window_px: int) -> None:
        """Display an (auto-estimated) window in the spinbox without re-running.

        Used to surface the auto-window result after a run. Setting it
        programmatically does not re-trigger estimation (estimation only runs on
        the panel's Run button), so there is no auto/manual feedback loop.
        """
        self._window.setValue(int(window_px) | 1)

    def set_enabled(self, enabled: bool) -> None:
        """Lock/unlock all widgets during a run (preserves the active-mode gating)."""
        for widget in (
            self._auto,
            self._k,
            self._sigma,
            self._min_size,
            self._unit,
            self._noise,
            self._particle,
            self._d_min,
            self._window,
        ):
            widget.setEnabled(enabled)
        if enabled:
            # Re-apply particle-mode / auto-window gating on top of the unlock.
            self._apply_mode_gating()
