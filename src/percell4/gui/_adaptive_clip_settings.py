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

# Size-filter unit dropdown labels -> internal codes used by resolve_min_area_px.
_UNIT_LABELS = ("px²", "µm²")
_UNIT_CODES = {"px²": "px", "µm²": "um2"}

# Manual-window unit dropdown labels -> codes used by resolve_window_px.
_WINDOW_UNIT_LABELS = ("px", "µm")
_WINDOW_UNIT_CODES = {"px": "px", "µm": "um"}

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
# entries). The third, "Otsu detect particle size", is *not* a finder — it
# switches the run to the per-cell d_min engine (see _OTSU_SMALLEST_CODE).
_WINDOW_METHOD_LABELS = (
    "Granule size",
    "Otsu mean (baseline)",
    "Otsu detect particle size",
    "Multi-scale (particle range)",
    "Auto extraction (two-pass)",
)
_WINDOW_METHOD_CODES = {
    "Granule size": "granule-size",
    "Otsu mean (baseline)": "otsu-mean",
    "Otsu detect particle size": "otsu-smallest",
    "Multi-scale (particle range)": "multiscale",
    "Auto extraction (two-pass)": "auto-extract",
}

# These two methods are per-cell *engine switches*, not whole-frame window-finders,
# so they are exempt from the finder drift guard:
#  - otsu-smallest: an Otsu first-pass sizes the d_min readout, then the per-cell
#    detect_adaptive_by_particle_size runs.
#  - multiscale: a per-cell Otsu measures the particle-size range, then the per-cell
#    detector runs at a doubling window sequence whose masks are OR-combined.
_OTSU_SMALLEST_CODE = "otsu-smallest"
_MULTISCALE_CODE = "multiscale"
_AUTO_EXTRACT_CODE = "auto-extract"
_ENGINE_SWITCH_CODES = {_OTSU_SMALLEST_CODE, _MULTISCALE_CODE, _AUTO_EXTRACT_CODE}

# Drift guard: every dropdown code that names a whole-frame finder must be a real
# registered finder (the per-cell engine switches above are exempt).
assert set(_WINDOW_METHOD_CODES.values()) - _ENGINE_SWITCH_CODES <= set(
    WINDOW_FINDER_NAMES
), (
    "window-method dropdown codes drifted from WINDOW_FINDER_NAMES: "
    f"{set(_WINDOW_METHOD_CODES.values()) - _ENGINE_SWITCH_CODES - set(WINDOW_FINDER_NAMES)}"
)


@dataclass(frozen=True)
class AdaptiveClipConfig:
    """Immutable snapshot of the adaptive-clip settings widget.

    ``window_value`` + ``window_unit`` (``"px"`` / ``"um"``) are the **manual**
    window; the host resolves them to an odd px window via ``resolve_window_px``
    (µm needs a known pixel size). ``min_size_unit`` is the internal code
    (``"px"`` / ``"um2"``). When ``auto_window`` is True the manual window is
    ignored (the window is estimated); the manual path runs per-cell off the
    active segmentation when one is present, else whole-frame.
    """

    window_value: float
    window_unit: str
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
    # the "Otsu detect particle size" code — the run then measures the
    # ``particle_percentile``-th particle diameter from an Otsu first-pass on the
    # current image, derives the window + size filter from it (``d_min_um``), and
    # uses the per-cell detector with a robust per-cell MAD σ. ``k`` is still
    # honored (the sensitivity knob, default 1.0); ``window_px`` / ``min_size_*`` /
    # ``noise_estimator`` are ignored in that mode. ``d_min_um`` is a readout the
    # run fills; ``particle_percentile`` is the editable knob (0 = absolute
    # smallest, which a single fragment can make too small).
    particle_mode: bool = False
    d_min_um: float = 0.40
    particle_percentile: float = 10.0
    # Multi-scale mode. True when ``auto_window`` is on AND ``window_method`` is the
    # "Multi-scale (particle range)" code: a per-cell Otsu first pass measures the
    # particle-size range, then the per-cell detector runs at a doubling window
    # sequence (OR-combined). ``size_cutoff_px`` floors the size *assessment* (a
    # diameter lower limit; does not filter the final mask). ``ms_auto_start`` picks
    # the starting window — ½ × mean particle when True, else the manual
    # ``window_value`` / ``window_unit``. ``k`` is honored (default 1).
    multiscale_mode: bool = False
    size_cutoff_px: float = 0.0
    ms_auto_start: bool = True
    # Manual iteration count for multi-scale: 0 = auto (double until past the
    # largest particle); N > 0 = run exactly N doubling passes (for testing).
    ms_iterations: int = 0
    # Auto-extraction mode. True when ``auto_window`` is on AND ``window_method``
    # is the "Auto extraction (two-pass)" code: a fine pass (window = 3×
    # ``smallest_particle_value`` at k=1) unioned with a coarse pass (window = 3×
    # LoG-measured largest, k = noise-symmetry floor) when the largest particle
    # exceeds the fine window. ``smallest_particle_value`` + ``smallest_particle_unit``
    # (``"px"`` / ``"um"``) are the one required input (the user's resolution
    # limit). ``k`` is ignored here (fine k=1, coarse auto); the Min-particle-size
    # filter applies to the union.
    auto_extract_mode: bool = False
    smallest_particle_value: float = 3.0
    smallest_particle_unit: str = "px"


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
            "legacy first-pass estimate; Otsu detect particle size runs an Otsu "
            "first-pass each run to measure the size-percentile particle, and "
            "runs the per-cell detector driven by it."
        )
        self._window_method.setEnabled(False)  # off until Auto is checked
        method_row.addWidget(self._window_method)
        layout.addLayout(method_row)

        # ── Size percentile (the per-cell detector's one editable knob) ──
        # Used only by the "Otsu detect particle size" method: the run measures
        # this percentile of the detected particle diameters (0 = absolute
        # smallest, which a single fragment can make too small).
        pct_row = QHBoxLayout()
        pct_row.addWidget(QLabel("Size percentile (%):"))
        self._percentile = QDoubleSpinBox()
        self._percentile.setRange(0.0, 100.0)
        self._percentile.setDecimals(1)
        self._percentile.setSingleStep(5.0)
        self._percentile.setValue(10.0)
        self._percentile.setToolTip(
            "Which percentile of the detected particle diameters sizes the window. "
            "0 = the absolute smallest particle; a low percentile (e.g. 10) ignores "
            "a lone tiny fragment. Re-measured fresh on the current image at run."
        )
        self._percentile.setEnabled(False)  # off until the per-cell method is selected
        pct_row.addWidget(self._percentile)
        layout.addLayout(pct_row)

        # ── Detected Ø readout (filled by the run from the size percentile) ──
        dmin_row = QHBoxLayout()
        dmin_row.addWidget(QLabel("Detected Ø (µm):"))
        self._d_min = QDoubleSpinBox()
        self._d_min.setRange(0.02, 50.0)
        self._d_min.setDecimals(3)
        self._d_min.setSingleStep(0.05)
        self._d_min.setValue(0.40)
        self._d_min.setEnabled(False)  # readout: filled by the host at run time
        dmin_row.addWidget(self._d_min)
        layout.addLayout(dmin_row)

        # ── Multi-scale controls (used only by the "Multi-scale (particle range)" method) ──
        # Size cutoff = a diameter lower limit that floors the size assessment
        # (does not filter the final mask). 0 = no floor.
        cut_row = QHBoxLayout()
        cut_row.addWidget(QLabel("Size cutoff Ø (px):"))
        self._cutoff = QDoubleSpinBox()
        self._cutoff.setRange(0.0, 1000.0)
        self._cutoff.setDecimals(1)
        self._cutoff.setSingleStep(1.0)
        self._cutoff.setValue(0.0)
        self._cutoff.setToolTip(
            "Lower size limit (particle diameter, px) for the multi-scale size "
            "assessment: particles below it are ignored when measuring the "
            "smallest/mean/largest. 0 = no floor. Does NOT filter the final mask."
        )
        self._cutoff.setEnabled(False)  # off until the multi-scale method is selected
        cut_row.addWidget(self._cutoff)
        layout.addLayout(cut_row)

        # Auto start window (½ × mean particle) vs the manual Window field below.
        self._ms_auto_start = QCheckBox("Auto start window (½ × mean particle)")
        self._ms_auto_start.setChecked(True)
        self._ms_auto_start.setToolTip(
            "Multi-scale only. When on, the starting window is half the mean "
            "particle diameter (then doubles until past the largest particle). "
            "When off, the Window field below is the manual starting window."
        )
        self._ms_auto_start.setEnabled(False)  # off until the multi-scale method is selected
        self._ms_auto_start.toggled.connect(self._on_ms_auto_start_toggled)
        layout.addWidget(self._ms_auto_start)

        # Manual iteration count (testing): 0 = auto (double until past the largest
        # particle); N = run exactly N doubling passes.
        iter_row = QHBoxLayout()
        iter_row.addWidget(QLabel("Iterations (0 = auto):"))
        self._iterations = QSpinBox()
        self._iterations.setRange(0, 20)
        self._iterations.setValue(0)
        self._iterations.setToolTip(
            "Multi-scale only. 0 = auto: keep doubling the window until it exceeds "
            "the largest particle. N > 0 = run exactly N doubling passes regardless "
            "of particle size (for testing the iteration)."
        )
        self._iterations.setEnabled(False)  # off until the multi-scale method is selected
        iter_row.addWidget(self._iterations)
        layout.addLayout(iter_row)

        # ── Smallest particle Ø (auto-extraction's one required input) ──
        # The fine window = 3 × this; the largest particle is measured (LoG) and
        # the coarse k is automatic. px, or µm via the dataset pixel size.
        sp_row = QHBoxLayout()
        sp_row.addWidget(QLabel("Smallest particle Ø:"))
        self._smallest = QDoubleSpinBox()
        self._smallest.setRange(0.02, 1000.0)
        self._smallest.setDecimals(2)
        self._smallest.setSingleStep(1.0)
        self._smallest.setValue(3.0)
        self._smallest.setToolTip(
            "Auto extraction only. The smallest particle diameter to resolve — "
            "your optical resolution limit (it cannot be measured reliably, so you "
            "supply it). The fine window is 3× this; the largest particle is "
            "measured automatically (LoG)."
        )
        self._smallest.setEnabled(False)  # off until the auto-extract method is selected
        sp_row.addWidget(self._smallest)
        self._smallest_unit = QComboBox()
        self._smallest_unit.addItems(list(_WINDOW_UNIT_LABELS))
        self._smallest_unit.setToolTip(
            "Smallest-particle unit. px is a pixel diameter; µm converts to pixels "
            "via the dataset pixel size (needs a known µm/px)."
        )
        self._smallest_unit.setEnabled(False)
        sp_row.addWidget(self._smallest_unit)
        layout.addLayout(sp_row)

        # ── Manual adaptive window (value + px/µm unit) ──
        # Used when Auto is off, and as the multi-scale starting window when its
        # "Auto start window" is off. The host resolves it to an odd px window
        # (forced odd, floored at 3); µm needs a known pixel size.
        win_row = QHBoxLayout()
        win_row.addWidget(QLabel("Window:"))
        self._window = QDoubleSpinBox()
        self._window.setRange(0.02, 1000.0)
        self._window.setDecimals(2)
        self._window.setSingleStep(1.0)
        self._window.setValue(15.0)
        win_row.addWidget(self._window)
        self._window_unit = QComboBox()
        self._window_unit.addItems(list(_WINDOW_UNIT_LABELS))
        self._window_unit.setToolTip(
            "Window unit. px is the local-window size in pixels; µm converts to "
            "pixels via the dataset pixel size (needs a known µm/px)."
        )
        win_row.addWidget(self._window_unit)
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
        self._window_unit.currentIndexChanged.connect(self.config_changed)
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
        self._percentile.valueChanged.connect(self.config_changed)
        self._d_min.valueChanged.connect(self.config_changed)
        self._cutoff.valueChanged.connect(self.config_changed)
        self._ms_auto_start.toggled.connect(self.config_changed)
        self._iterations.valueChanged.connect(self.config_changed)
        self._smallest.valueChanged.connect(self.config_changed)
        self._smallest_unit.currentIndexChanged.connect(self.config_changed)

    # ── Slots ─────────────────────────────────────────────────────

    def _on_auto_toggled(self, checked: bool) -> None:
        self._apply_mode_gating()
        # Checking Auto while a per-cell engine method is the remembered selection
        # enters that mode -> adopt its validated default.
        if checked and self._active_mode() in ("particle", "multiscale"):
            self._adopt_particle_defaults()

    def _on_window_method_changed(self, _index: int) -> None:
        self._apply_mode_gating()
        # Switching the active method to a per-cell engine.
        if self._active_mode() in ("particle", "multiscale"):
            self._adopt_particle_defaults()

    def _on_ms_auto_start_toggled(self, _checked: bool) -> None:
        self._apply_mode_gating()  # toggles the manual Window field in multi-scale mode

    def _adopt_particle_defaults(self) -> None:
        """Adopt the validated one-knob default (k=1) on entering a per-cell mode.

        ``k`` stays editable so the user can raise it to be conservative. Sizes are
        measured fresh by the host at each run (d_min readout / multi-scale
        assessment), so there is nothing to seed here beyond ``k``.
        """
        self._k.setValue(1.0)

    def _method_code(self) -> str:
        """The selected auto-window method's internal code."""
        return _WINDOW_METHOD_CODES[self._window_method.currentText()]

    def _active_mode(self) -> str:
        """The active run mode.

        One of ``manual`` / ``finder`` / ``particle`` / ``multiscale`` /
        ``auto-extract``. Auto off is always ``manual``. Auto on dispatches on the
        selected method:
        the three per-cell engine switches (``particle`` = Otsu-detect-particle,
        ``multiscale``, ``auto-extract``) or a whole-frame ``finder`` (granule-size
        / otsu-mean).
        """
        if not self._auto.isChecked():
            return "manual"
        code = self._method_code()
        if code == _OTSU_SMALLEST_CODE:
            return "particle"
        if code == _MULTISCALE_CODE:
            return "multiscale"
        if code == _AUTO_EXTRACT_CODE:
            return "auto-extract"
        return "finder"

    def _is_particle_mode(self) -> bool:
        """True when the active mode is the per-cell d_min (Otsu-detect-particle) engine."""
        return self._active_mode() == "particle"

    def _is_multiscale_mode(self) -> bool:
        """True when the active mode is the multi-scale per-cell routine."""
        return self._active_mode() == "multiscale"

    def _is_auto_extract_mode(self) -> bool:
        """True when the active mode is the two-pass auto-extraction routine."""
        return self._active_mode() == "auto-extract"

    def _apply_mode_gating(self) -> None:
        """Enable/disable fields for the active mode.

        - ``finder`` / ``manual``: size filter + noise estimate live; ``d_min`` /
          percentile / multi-scale controls off. The manual window is live only in
          ``manual``; the method dropdown only when Auto is on.
        - ``particle``: percentile live; ``d_min`` is a disabled readout; size
          filter / noise off (per-cell MAD).
        - ``multiscale``: size cutoff + auto-start live; the manual Window is live
          only when auto-start is off; size filter / noise / d_min / percentile off.

        ``k`` stays live in every mode; so do Gaussian σ and Auto.
        """
        mode = self._active_mode()
        auto = self._auto.isChecked()
        self._d_min.setEnabled(False)  # readout: filled by the host at run time
        self._percentile.setEnabled(mode == "particle")
        self._cutoff.setEnabled(mode == "multiscale")
        self._ms_auto_start.setEnabled(mode == "multiscale")
        self._iterations.setEnabled(mode == "multiscale")
        # Smallest particle Ø: the one input of auto-extraction.
        self._smallest.setEnabled(mode == "auto-extract")
        self._smallest_unit.setEnabled(mode == "auto-extract")
        # Min particle size filter: live in manual/finder, multi-scale, AND
        # auto-extract (where it filters the unioned output). The noise estimator
        # is only used by the whole-frame detector (manual/finder); per-cell modes
        # use a fixed MAD σ.
        size_filter = mode in ("manual", "finder", "multiscale", "auto-extract")
        self._min_size.setEnabled(size_filter)
        self._unit.setEnabled(size_filter)
        self._noise.setEnabled(mode in ("manual", "finder"))
        # k is ignored in auto-extract (fine k=1 fixed, coarse k automatic).
        self._k.setEnabled(mode != "auto-extract")
        # Manual window: live in manual mode, or as the multi-scale manual start.
        manual_window = mode == "manual" or (
            mode == "multiscale" and not self._ms_auto_start.isChecked()
        )
        self._window.setEnabled(manual_window)
        self._window_unit.setEnabled(manual_window)
        self._window_method.setEnabled(auto)

    # ── Public API ────────────────────────────────────────────────

    def current_config(self) -> AdaptiveClipConfig:
        """Snapshot the live widget state into a frozen dataclass."""
        return AdaptiveClipConfig(
            window_value=float(self._window.value()),
            window_unit=_WINDOW_UNIT_CODES[self._window_unit.currentText()],
            k=float(self._k.value()),
            gaussian_sigma=float(self._sigma.value()),
            min_size_value=float(self._min_size.value()),
            min_size_unit=_UNIT_CODES[self._unit.currentText()],
            auto_window=self._auto.isChecked(),
            noise_estimator=_NOISE_CODES[self._noise.currentText()],
            window_method=self._method_code(),
            particle_mode=self._is_particle_mode(),
            d_min_um=float(self._d_min.value()),
            particle_percentile=float(self._percentile.value()),
            multiscale_mode=self._is_multiscale_mode(),
            size_cutoff_px=float(self._cutoff.value()),
            ms_auto_start=self._ms_auto_start.isChecked(),
            ms_iterations=int(self._iterations.value()),
            auto_extract_mode=self._is_auto_extract_mode(),
            smallest_particle_value=float(self._smallest.value()),
            smallest_particle_unit=_WINDOW_UNIT_CODES[self._smallest_unit.currentText()],
        )

    def set_d_min_um(self, d_min_um: float) -> None:
        """Display the run's detected Ø in the (readout) d_min spinbox.

        Used by the host to surface the diameter measured at run time. Setting it
        programmatically forwards ``config_changed`` (a value did change); the
        field is a readout (disabled), so there is no edit/detect feedback loop.
        The spinbox clamps the value to its range ``[0.02, 50.0]`` µm.
        """
        self._d_min.setValue(float(d_min_um))

    def set_window_value(self, window_px: int) -> None:
        """Display an (auto-estimated) px window in the spinbox without re-running.

        Used to surface the auto/per-cell window result after a run; it is always a
        pixel count, so the unit is forced to px. Setting it programmatically does
        not re-trigger estimation (estimation only runs on the panel's Run button),
        so there is no auto/manual feedback loop.
        """
        self._window_unit.setCurrentText("px")
        self._window.setValue(float(int(window_px) | 1))

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
            self._percentile,
            self._d_min,
            self._cutoff,
            self._ms_auto_start,
            self._iterations,
            self._smallest,
            self._smallest_unit,
            self._window,
            self._window_unit,
        ):
            widget.setEnabled(enabled)
        if enabled:
            # Re-apply particle-mode / auto-window gating on top of the unlock.
            self._apply_mode_gating()
