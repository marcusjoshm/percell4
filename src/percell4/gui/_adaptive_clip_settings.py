"""Settings widget for the Adaptive Local Clipping module.

A reusable form for the auto-extraction (two-pass) detection mode: the
smallest-particle diameter (the optical-resolution limit the fine window is
sized from), the Gaussian σ presmooth, and the minimum particle area filter.
Snapshots into a frozen :class:`AdaptiveClipConfig`. Owns only Action-shaped
per-run knobs — channel / segmentation remain Session-owned Selectors. Mirrors
``gui/_grouped_threshold_settings.py`` (frozen ``current_config()`` +
aggregated ``config_changed`` signal).

The coarse-pass window ratio and false-positive rate are the eye-validated
module constants ``FILL_FACTOR`` and ``FDR`` in
``percell4.domain.measure.auto_extraction`` and are deliberately not exposed:
the validated configuration is the default, and a user-set value would drift
from it silently.
"""

from __future__ import annotations

from dataclasses import dataclass

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

# Size-filter unit dropdown labels -> internal codes used by resolve_min_area_px.
_UNIT_LABELS = ("px²", "µm²")
_UNIT_CODES = {"px²": "px", "µm²": "um2"}

# Smallest-particle unit dropdown labels -> codes used by resolve_window_px.
_WINDOW_UNIT_LABELS = ("px", "µm")
_WINDOW_UNIT_CODES = {"px": "px", "µm": "um"}


@dataclass(frozen=True)
class AdaptiveClipConfig:
    """Immutable snapshot of the adaptive-clip (auto-extraction) settings widget.

    ``smallest_particle_value`` + ``smallest_particle_unit`` (``"px"`` / ``"um"``)
    are the optical-resolution Ø the auto-extraction run sizes its fine window
    from (fine window = 3 × this). ``min_size_unit`` is the internal code
    (``"px"`` / ``"um2"``) for the union size filter.
    """

    gaussian_sigma: float
    min_size_value: float
    min_size_unit: str
    smallest_particle_value: float
    smallest_particle_unit: str


class AdaptiveClipSettingsWidget(QWidget):
    """Auto-extraction (two-pass) settings form as one reusable widget.

    Emits :attr:`config_changed` whenever any child widget's user-edit signal
    fires. Every control is always live — the form has no modes.
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

        # ── Smallest particle diameter (value + px/µm unit) ──
        # The optical-resolution limit the fine pass is sized from (fine window
        # = 3 × this). px, or µm via the dataset pixel size.
        sp_row = QHBoxLayout()
        sp_row.addWidget(QLabel("Smallest Particle Diameter:"))
        self._smallest = QDoubleSpinBox()
        self._smallest.setRange(0.02, 1000.0)
        self._smallest.setDecimals(2)
        self._smallest.setSingleStep(1.0)
        self._smallest.setValue(2.0)
        self._smallest.setToolTip(
            "The diameter of the smallest particle you want to detect — your "
            "optical resolution limit. Detection looks for particles in a "
            "neighbourhood three times this wide; the largest particle is "
            "measured from the image automatically."
        )
        sp_row.addWidget(self._smallest)
        self._smallest_unit = QComboBox()
        self._smallest_unit.addItems(list(_WINDOW_UNIT_LABELS))
        self._smallest_unit.setToolTip(
            "Unit for the diameter above. px is a pixel diameter; µm converts "
            "to pixels using the dataset's pixel size (needs a known µm/px)."
        )
        sp_row.addWidget(self._smallest_unit)
        layout.addLayout(sp_row)

        # ── Gaussian sigma ──
        sig_row = QHBoxLayout()
        sig_row.addWidget(QLabel("Gaussian σ:"))
        self._sigma = QDoubleSpinBox()
        self._sigma.setRange(0.0, 20.0)
        self._sigma.setSingleStep(0.5)
        self._sigma.setValue(1.0)
        self._sigma.setSpecialValueText("None")
        self._sigma.setToolTip(
            "Smooth the image by this much before detecting, to keep single-pixel "
            "noise from registering as particles. Set to None to skip smoothing."
        )
        sig_row.addWidget(self._sigma)
        layout.addLayout(sig_row)

        # ── Minimum particle area filter (value + unit) ──
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Min. Particle Area:"))
        self._min_size = QDoubleSpinBox()
        self._min_size.setRange(0.0, 1_000_000.0)
        self._min_size.setDecimals(2)
        self._min_size.setValue(3.0)
        self._min_size.setToolTip(
            "Discard detected particles smaller than this area. Applied once, "
            "to the finished detection."
        )
        size_row.addWidget(self._min_size)
        self._unit = QComboBox()
        self._unit.addItems(list(_UNIT_LABELS))
        self._unit.setToolTip(
            "Unit for the area above. µm² converts to pixels using the dataset's "
            "pixel size (needs a known µm/px)."
        )
        size_row.addWidget(self._unit)
        layout.addLayout(size_row)

    def _connect_change_signals(self) -> None:
        # Signal-to-signal forwarding: Qt drops the extra arg the child signals
        # carry (value / index), so config_changed (0-arg) re-emits cleanly.
        self._sigma.valueChanged.connect(self.config_changed)
        self._min_size.valueChanged.connect(self.config_changed)
        self._unit.currentIndexChanged.connect(self.config_changed)
        self._smallest.valueChanged.connect(self.config_changed)
        self._smallest_unit.currentIndexChanged.connect(self.config_changed)

    # ── Public API ────────────────────────────────────────────────

    def current_config(self) -> AdaptiveClipConfig:
        """Snapshot the live widget state into a frozen dataclass."""
        return AdaptiveClipConfig(
            gaussian_sigma=float(self._sigma.value()),
            min_size_value=float(self._min_size.value()),
            min_size_unit=_UNIT_CODES[self._unit.currentText()],
            smallest_particle_value=float(self._smallest.value()),
            smallest_particle_unit=_WINDOW_UNIT_CODES[self._smallest_unit.currentText()],
        )

    def set_enabled(self, enabled: bool) -> None:
        """Lock/unlock all widgets during a run."""
        for widget in (
            self._smallest,
            self._smallest_unit,
            self._sigma,
            self._min_size,
            self._unit,
        ):
            widget.setEnabled(enabled)
