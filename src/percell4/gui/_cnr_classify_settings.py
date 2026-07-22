"""Settings widget for the CNR subpopulation-classification step.

A small form — source mask + mode + (guided) CNR threshold — that snapshots into
a frozen :class:`CnrClassifyConfig`. The classification *algorithm* knobs (ring
geometry, dip ``alpha``, ``min_components``, ``min_fraction``, presmooth σ) are
fixed module constants in ``percell4.domain.measure.cnr_classification`` and are
deliberately **not** exposed here: the user has no calibrated intuition for them,
and a user-set presmooth would make CNR inconsistent with the detector's ``k``.

Mirrors ``gui/_adaptive_clip_settings.py`` (frozen ``current_config()`` + an
aggregated ``config_changed`` signal + mode gating). This widget is a pure form;
the host (:class:`~percell4.gui.adaptive_clip_panel.AdaptiveClipPanel`) owns the
source-mask list, the Run action, and the Creator save.
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

# Mode dropdown labels -> internal codes consumed by the panel's dispatch.
# "interactive" is a GUI-only routing value: it selects the histogram segmenter
# and must never reach run_cnr_classification (which would treat an unknown mode
# as discover). The panel's worker bodies raise on it defensively.
_MODE_LABELS = ("CNR threshold", "Auto Two Groups", "Interactive")
_MODE_CODES = {
    "CNR threshold": "guided",
    "Auto Two Groups": "forced",
    "Interactive": "interactive",
}


@dataclass(frozen=True)
class CnrClassifyConfig:
    """Immutable snapshot of the CNR-classification settings widget.

    ``source_mask`` is the ``/masks/<name>`` to classify (empty string when none
    is selected — the host treats that as a pre-flight failure). ``mode`` is one
    of ``"guided"`` / ``"forced"`` / ``"interactive"``; the first two are
    classifier modes, while ``"interactive"`` routes the host to the histogram
    segmenter instead. ``threshold`` is the raw CNR split value used only in
    guided mode (ignored otherwise).
    """

    source_mask: str
    mode: str
    threshold: float


class CnrClassifySettingsWidget(QWidget):
    """Source-mask + mode + (guided) threshold form as one reusable widget.

    Emits :attr:`config_changed` whenever a child widget's user-edit signal
    fires. Selecting "CNR threshold" enables the threshold spinbox; the other two
    modes disable it (it is unused there).
    """

    config_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._connect_change_signals()
        self._apply_mode_gating()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ── Source mask (the already-extracted feature mask to classify) ──
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Source mask:"))
        self._source = QComboBox()
        self._source.setToolTip(
            "The saved feature mask to classify by CNR (e.g. a multi-scale "
            "Adaptive Local Clipping mask). Populations are written as new masks."
        )
        src_row.addWidget(self._source)
        layout.addLayout(src_row)

        # ── Mode ──
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._mode = QComboBox()
        self._mode.addItems(list(_MODE_LABELS))
        self._mode.setToolTip(
            "How to split the source mask into populations.\n\n"
            "CNR threshold — split at the contrast value you set below, for "
            "populations you already know overlap.\n"
            "Auto Two Groups — always split into exactly two, letting the data "
            "place the boundary.\n"
            "Interactive — open a histogram of every particle's contrast and "
            "place the boundaries yourself, with a live preview."
        )
        mode_row.addWidget(self._mode)
        layout.addLayout(mode_row)

        # ── CNR threshold (guided only) ──
        thr_row = QHBoxLayout()
        thr_row.addWidget(QLabel("CNR threshold:"))
        self._threshold = QDoubleSpinBox()
        self._threshold.setRange(0.0, 1_000.0)
        self._threshold.setDecimals(2)
        self._threshold.setSingleStep(0.5)
        self._threshold.setValue(8.0)
        self._threshold.setToolTip(
            "CNR threshold mode only: particles at or above this contrast go "
            "into the higher population. To find a starting value, run "
            "Interactive mode and read it off the histogram — every run also "
            "prints a suggested threshold to the terminal."
        )
        thr_row.addWidget(self._threshold)
        layout.addLayout(thr_row)

    def _connect_change_signals(self) -> None:
        # Signal-to-signal forwarding: Qt drops the extra arg the child signals
        # carry, so config_changed (0-arg) re-emits cleanly.
        self._source.currentIndexChanged.connect(self.config_changed)
        self._mode.currentIndexChanged.connect(self._on_mode_changed)
        self._mode.currentIndexChanged.connect(self.config_changed)
        self._threshold.valueChanged.connect(self.config_changed)

    # ── Slots ─────────────────────────────────────────────────────

    def _on_mode_changed(self, _index: int) -> None:
        self._apply_mode_gating()

    def _mode_code(self) -> str:
        return _MODE_CODES[self._mode.currentText()]

    def _apply_mode_gating(self) -> None:
        """The CNR threshold is live only in "CNR threshold" (guided) mode."""
        self._threshold.setEnabled(self._mode_code() == "guided")

    # ── Public API ────────────────────────────────────────────────

    def current_config(self) -> CnrClassifyConfig:
        """Snapshot the live widget state into a frozen dataclass."""
        return CnrClassifyConfig(
            source_mask=self._source.currentText() if self._source.count() else "",
            mode=self._mode_code(),
            threshold=float(self._threshold.value()),
        )

    def set_mask_choices(self, names) -> None:
        """Repopulate the source-mask combo, preserving the current pick if able.

        Setting items programmatically must not spuriously fire ``config_changed``
        more than the implicit index change, so the combo is repopulated under a
        signal block and the change is emitted once at the end.
        """
        current = self._source.currentText()
        self._source.blockSignals(True)
        self._source.clear()
        self._source.addItems([str(n) for n in (names or [])])
        if current:
            idx = self._source.findText(current)
            if idx >= 0:
                self._source.setCurrentIndex(idx)
        self._source.blockSignals(False)
        self.config_changed.emit()

    def set_enabled(self, enabled: bool) -> None:
        """Lock/unlock all widgets during a run (preserves mode gating)."""
        for widget in (self._source, self._mode, self._threshold):
            widget.setEnabled(enabled)
        if enabled:
            self._apply_mode_gating()
