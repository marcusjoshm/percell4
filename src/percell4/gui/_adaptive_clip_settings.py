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

from percell4.domain.measure.window_finder_names import WINDOW_FINDER_NAMES

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

# Auto-window method dropdown labels -> codes. Only shown when "Auto adaptive
# window size" is on. The first two name whole-frame WINDOW_FINDERS (the default
# `granule-size` isolates the granules and sizes the window to them; `otsu-mean`
# is the legacy baseline; bake-off candidates land here with their registry
# entries). The third, "Otsu detect smallest particle size", is *not* a finder —
# it switches the run to the per-cell d_min engine (see _OTSU_SMALLEST_CODE).
_WINDOW_METHOD_LABELS = (
    "Granule size",
    "Otsu mean (baseline)",
    "Otsu detect smallest particle size",
)
_WINDOW_METHOD_CODES = {
    "Granule size": "granule-size",
    "Otsu mean (baseline)": "otsu-mean",
    "Otsu detect smallest particle size": "otsu-smallest",
}

# Selecting this method does not pick a whole-frame window-finder: an Otsu
# first-pass measures the smallest particle, auto-fills the d_min knob, and the
# run uses the per-cell detect_adaptive_by_particle_size engine (window + size
# filter derived from d_min). It is therefore exempt from the finder drift guard.
_OTSU_SMALLEST_CODE = "otsu-smallest"

# Drift guard: every dropdown code that names a whole-frame finder must be a real
# registered finder (the per-cell engine switch above is exempt).
assert set(_WINDOW_METHOD_CODES.values()) - {_OTSU_SMALLEST_CODE} <= set(
    WINDOW_FINDER_NAMES
), (
    "window-method dropdown codes drifted from WINDOW_FINDER_NAMES: "
    f"{set(_WINDOW_METHOD_CODES.values()) - {_OTSU_SMALLEST_CODE} - set(WINDOW_FINDER_NAMES)}"
)


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
    # Which window-finder estimates the window when ``auto_window`` is True
    # (a WINDOW_FINDERS registry name). Ignored when ``auto_window`` is False or
    # ``particle_mode`` is True. Default ``granule-size`` (granule-isolating).
    window_method: str = "granule-size"
    # Particle-size mode. True when ``auto_window`` is on AND ``window_method`` is
    # the "Otsu detect smallest particle size" code — the run then derives the
    # window + size filter from ``d_min_um`` (smallest particle Ø, µm, auto-filled
    # from an Otsu first-pass) and uses the per-cell detector with a robust
    # per-cell MAD σ. ``k`` is still honored (the sensitivity knob, default 1.0);
    # ``window_px`` / ``min_size_*`` / ``noise_estimator`` are ignored in that mode.
    particle_mode: bool = False
    d_min_um: float = 0.40


class AdaptiveClipSettingsWidget(QWidget):
    """Window / k / σ / particle-size / auto-window form as one reusable widget.

    Emits :attr:`config_changed` whenever any child widget's user-edit signal
    fires. Checking "Auto adaptive window size" disables the window spinbox (it
    is computed at run time) and enables the method dropdown; unchecking reverses
    both. The "Otsu detect smallest particle size" method makes the d_min field a
    disabled **readout**: the host measures the smallest particle from an Otsu
    first-pass on the *current* image at each run and surfaces it via
    :meth:`set_d_min_um`.
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

        # ── Auto-window method (only used when "Auto adaptive window size" is on) ──
        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Auto window method:"))
        self._window_method = QComboBox()
        self._window_method.addItems(list(_WINDOW_METHOD_LABELS))
        self._window_method.setToolTip(
            "How the window is found when Auto is on. Granule size isolates the "
            "granules and sizes the window to them; Otsu mean (baseline) is the "
            "legacy first-pass estimate; Otsu detect smallest particle size runs "
            "an Otsu first-pass to measure the smallest particle, auto-fills the "
            "Ø knob below, and runs the per-cell detector driven by it."
        )
        self._window_method.setEnabled(False)  # off until Auto is checked
        method_row.addWidget(self._window_method)
        layout.addLayout(method_row)

        # ── Smallest particle Ø (the one-knob per-cell detector) ──
        # Editable only under the "Otsu detect smallest particle size" method,
        # which auto-fills it from an Otsu first-pass (the user may then tweak it).
        dmin_row = QHBoxLayout()
        dmin_row.addWidget(QLabel("Smallest particle Ø (µm):"))
        self._d_min = QDoubleSpinBox()
        self._d_min.setRange(0.02, 50.0)
        self._d_min.setDecimals(3)
        self._d_min.setSingleStep(0.05)
        self._d_min.setValue(0.40)
        self._d_min.setEnabled(False)  # off until the per-cell method is selected
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
        # The method dropdown drives gating + the Otsu auto-detect request; connect
        # the behavior slot here (after _build_ui's addItems, so construction-time
        # index changes do not fire it) before the config_changed forward.
        self._window_method.currentIndexChanged.connect(self._on_window_method_changed)
        self._window_method.currentIndexChanged.connect(self.config_changed)
        self._auto.toggled.connect(self.config_changed)
        self._d_min.valueChanged.connect(self.config_changed)

    # ── Slots ─────────────────────────────────────────────────────

    def _on_auto_toggled(self, checked: bool) -> None:
        self._apply_mode_gating()
        # Checking Auto while the per-cell method is the remembered selection
        # enters particle mode -> adopt its validated default.
        if checked and self._is_particle_mode():
            self._adopt_particle_defaults()

    def _on_window_method_changed(self, _index: int) -> None:
        self._apply_mode_gating()
        # Switching the active method to the per-cell engine.
        if self._is_particle_mode():
            self._adopt_particle_defaults()

    def _adopt_particle_defaults(self) -> None:
        """Adopt the validated one-knob default (k=1) on entering particle mode.

        ``k`` stays editable so the user can raise it to be conservative (fewer
        false positives, at the cost of missing dim sub-threshold particles). The
        smallest-particle Ø is measured fresh by the host at each run (the d_min
        field is a readout), so there is nothing to seed here beyond ``k``.
        """
        self._k.setValue(1.0)

    def _method_code(self) -> str:
        """The selected auto-window method's internal code."""
        return _WINDOW_METHOD_CODES[self._window_method.currentText()]

    def _is_particle_mode(self) -> bool:
        """True when the active mode is the per-cell d_min engine.

        Requires Auto on (the dropdown is the auto-window method picker) and the
        "Otsu detect smallest particle size" method selected.
        """
        return self._auto.isChecked() and self._method_code() == _OTSU_SMALLEST_CODE

    def _apply_mode_gating(self) -> None:
        """Enable/disable fields for the active mode.

        The per-cell (particle) mode derives the spatial scale from ``d_min``, so
        the size filter and the noise estimate go disabled. ``d_min`` itself is a
        disabled **readout** (the host re-measures it fresh at each run), never an
        input. ``k`` stays live in every mode (the sensitivity knob), as do
        Gaussian σ and Auto. The manual window is live only when Auto is off; the
        method dropdown is live only when Auto is on.
        """
        auto = self._auto.isChecked()
        particle = self._is_particle_mode()
        self._d_min.setEnabled(False)  # readout: filled by the host at run time
        for w in (self._min_size, self._unit, self._noise):
            w.setEnabled(not particle)
        self._k.setEnabled(True)
        self._window.setEnabled(not auto)
        self._window_method.setEnabled(auto)

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
            window_method=self._method_code(),
            particle_mode=self._is_particle_mode(),
            d_min_um=float(self._d_min.value()),
        )

    def set_d_min_um(self, d_min_um: float) -> None:
        """Display an (Otsu-detected) smallest-particle Ø in the d_min spinbox.

        Used by the host to surface the auto-detected diameter. Setting it
        programmatically forwards ``config_changed`` (a value did change) but does
        not re-request Otsu detection — only the method dropdown does that — so
        there is no detect/auto-fill feedback loop. The spinbox clamps the value
        to its range ``[0.02, 50.0]`` µm.
        """
        self._d_min.setValue(float(d_min_um))

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
            self._window_method,
            self._d_min,
            self._window,
        ):
            widget.setEnabled(enabled)
        if enabled:
            # Re-apply particle-mode / auto-window gating on top of the unlock.
            self._apply_mode_gating()
