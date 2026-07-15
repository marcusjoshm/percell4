"""Settings widget for the Adaptive Local Clipping module.

A reusable form for the single auto-extraction (two-pass) detection mode: an
"Auto-detect smallest (LoG)" toggle, the smallest-particle Ø (the manual
optical-resolution override when auto-detect is off), the Gaussian σ presmooth,
and the min-particle-size filter. Snapshots into a frozen
:class:`AdaptiveClipConfig`. Owns only Action-shaped per-run knobs —
channel / segmentation remain Session-owned Selectors. Mirrors
``gui/_grouped_threshold_settings.py`` (frozen ``current_config()`` +
aggregated ``config_changed`` signal).
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
    are the optical-resolution Ø the auto-extraction run uses when
    ``auto_extract_smallest_auto`` is False; when True the smallest particle / fine
    window is measured from the image (LoG) and the field becomes a readout.
    ``min_size_unit`` is the internal code (``"px"`` / ``"um2"``) for the union
    size filter.
    """

    gaussian_sigma: float
    min_size_value: float
    min_size_unit: str
    smallest_particle_value: float
    smallest_particle_unit: str
    auto_extract_smallest_auto: bool
    largest_only: bool = False


class AdaptiveClipSettingsWidget(QWidget):
    """Auto-extraction (two-pass) settings form as one reusable widget.

    Emits :attr:`config_changed` whenever any child widget's user-edit signal
    fires. The "Auto-detect smallest (LoG)" checkbox is the mode's one toggle:
    when on (default) the smallest particle / fine window is measured from the
    image per run (the smallest-Ø field is a disabled readout); when off, the
    field is the manual optical-resolution override. Gaussian σ and the
    min-particle-size filter are always live.
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

        # ── Largest particle only (single pass) ──
        # When on, the run does ONE coarse pass sized to the largest particle and
        # skips the fine/small-window pass entirely, so the smallest-particle
        # controls below are meaningless and are disabled.
        self._largest_only = QCheckBox("Largest particle only (single pass)")
        self._largest_only.setChecked(False)
        self._largest_only.setToolTip(
            "Run a single coarse pass sized to the largest particle (measured by "
            "Laplacian-of-Gaussian), skipping the fine/small-window pass. Use it "
            "when only the large features matter and the fine pass would only add "
            "small-scale junk. The smallest-particle settings are ignored in this mode."
        )
        self._largest_only.toggled.connect(self._on_largest_only_toggled)
        layout.addWidget(self._largest_only)

        # ── Auto-detect smallest particle (LoG) ──
        # When on (default), the fine window is measured from the image each run
        # (the smallest-Ø field becomes a readout). When off, the field is the
        # manual optical-resolution override (a true diameter, ×3 → fine window).
        self._ae_smallest_auto = QCheckBox("Auto-detect smallest (LoG)")
        self._ae_smallest_auto.setChecked(True)
        self._ae_smallest_auto.setToolTip(
            "When on, the smallest particle (fine window) is measured from the "
            "current image by a Laplacian-of-Gaussian each run, so it adapts per "
            "dataset. Turn off to enter your optical resolution limit in the field "
            "below."
        )
        self._ae_smallest_auto.toggled.connect(self._on_ae_smallest_auto_toggled)
        layout.addWidget(self._ae_smallest_auto)

        # ── Smallest particle Ø (value + px/µm unit) ──
        # When Auto-detect is off this is the manual optical-resolution override
        # (fine window = 3 × this); when on it is a readout the run fills. px, or
        # µm via the dataset pixel size.
        sp_row = QHBoxLayout()
        sp_row.addWidget(QLabel("Smallest particle Ø:"))
        self._smallest = QDoubleSpinBox()
        self._smallest.setRange(0.02, 1000.0)
        self._smallest.setDecimals(2)
        self._smallest.setSingleStep(1.0)
        self._smallest.setValue(3.0)
        self._smallest.setToolTip(
            "The smallest particle diameter to resolve — your optical resolution "
            "limit (it cannot be measured reliably, so you supply it). The fine "
            "window is 3× this; the largest particle is measured automatically (LoG)."
        )
        sp_row.addWidget(self._smallest)
        self._smallest_unit = QComboBox()
        self._smallest_unit.addItems(list(_WINDOW_UNIT_LABELS))
        self._smallest_unit.setToolTip(
            "Smallest-particle unit. px is a pixel diameter; µm converts to pixels "
            "via the dataset pixel size (needs a known µm/px)."
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
        sig_row.addWidget(self._sigma)
        layout.addLayout(sig_row)

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

        # Initial gating: auto-detect on by default -> the smallest-Ø field is a
        # disabled readout.
        self._apply_mode_gating()

    def _connect_change_signals(self) -> None:
        # Signal-to-signal forwarding: Qt drops the extra arg the child signals
        # carry (value / index / bool), so config_changed (0-arg) re-emits cleanly.
        self._sigma.valueChanged.connect(self.config_changed)
        self._min_size.valueChanged.connect(self.config_changed)
        self._unit.currentIndexChanged.connect(self.config_changed)
        self._smallest.valueChanged.connect(self.config_changed)
        self._smallest_unit.currentIndexChanged.connect(self.config_changed)
        self._ae_smallest_auto.toggled.connect(self.config_changed)
        self._largest_only.toggled.connect(self.config_changed)

    # ── Slots ─────────────────────────────────────────────────────

    def _on_ae_smallest_auto_toggled(self, _checked: bool) -> None:
        self._apply_mode_gating()  # toggles the smallest-Ø field

    def _on_largest_only_toggled(self, _checked: bool) -> None:
        self._apply_mode_gating()  # disables the smallest-particle controls when on

    def _apply_mode_gating(self) -> None:
        """Enable/disable the smallest-particle controls for the current mode.

        In largest-only mode there is no fine pass, so the whole smallest-particle
        group — the auto-detect toggle, the smallest-Ø field, and its unit — is
        disabled. Otherwise the auto-detect toggle is live and the smallest-Ø field
        + unit are the manual optical-resolution override, live only when
        "Auto-detect smallest" is off. Gaussian σ and the min-particle-size filter
        are always live.
        """
        largest_only = self._largest_only.isChecked()
        self._ae_smallest_auto.setEnabled(not largest_only)
        manual_smallest = (not largest_only) and (not self._ae_smallest_auto.isChecked())
        self._smallest.setEnabled(manual_smallest)
        self._smallest_unit.setEnabled(manual_smallest)

    # ── Public API ────────────────────────────────────────────────

    def current_config(self) -> AdaptiveClipConfig:
        """Snapshot the live widget state into a frozen dataclass."""
        return AdaptiveClipConfig(
            gaussian_sigma=float(self._sigma.value()),
            min_size_value=float(self._min_size.value()),
            min_size_unit=_UNIT_CODES[self._unit.currentText()],
            smallest_particle_value=float(self._smallest.value()),
            smallest_particle_unit=_WINDOW_UNIT_CODES[self._smallest_unit.currentText()],
            auto_extract_smallest_auto=self._ae_smallest_auto.isChecked(),
            largest_only=self._largest_only.isChecked(),
        )

    def set_smallest_value(self, diameter_px: float) -> None:
        """Display the auto-detected smallest Ø (px) in the readout spinbox.

        Used by the host to surface the LoG-measured smallest particle after an
        auto-extraction run so the user sees the value adapt per dataset. Always a
        pixel diameter, so the unit is forced to px. The field is a readout while
        Auto-detect is on, so there is no feedback loop.
        """
        self._smallest_unit.setCurrentText("px")
        self._smallest.setValue(float(diameter_px))

    def set_enabled(self, enabled: bool) -> None:
        """Lock/unlock all widgets during a run (preserves the auto-detect gating)."""
        for widget in (
            self._largest_only,
            self._ae_smallest_auto,
            self._smallest,
            self._smallest_unit,
            self._sigma,
            self._min_size,
            self._unit,
        ):
            widget.setEnabled(enabled)
        if enabled:
            # Re-apply the mode gating on top of the unlock.
            self._apply_mode_gating()
