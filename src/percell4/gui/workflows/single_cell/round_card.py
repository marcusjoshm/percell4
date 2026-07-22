"""One thresholding round as a self-contained card widget.

The single-cell workflow config dialog edits its thresholding rounds as a
vertical list of these cards (replacing the old wide `QTableWidget`). Each card
is a full-width form that shows only the fields for its selected Method — a
Grouped-Otsu sub-group (Algorithm / GMM max / K-means K) or an Adaptive Local
Clipping sub-group (Smallest Particle Diameter + unit / CNR split / CNR threshold
/ GMM 2-pop) — plus the always-visible shared fields (Name, Channel, Metric,
Method, Smoothing σ, Min. Particle Area + unit) and per-card ▲/▼/✕ controls.

The card exposes the same per-round value dict the dialog's table rows used
(`to_dict()` / `from_dict()`), **minus** the σ-clipping-only `k` / `global_sigma`
keys — the σ-clipping method is not offered here. This keeps the dialog's
downstream `ThresholdingRound` construction unchanged. `from_dict` tolerates a
stale `k` / `global_sigma` in an incoming dict (it ignores them).

Only two methods are offered: Grouped Otsu (the default) and Adaptive Local
Clipping (two-pass) — the same detector the GUI Analysis panel runs. Field labels
use the user-facing vocabulary standardised across the app (Smallest Particle
Diameter, Min. Particle Area), not the terse column headers.
"""

from __future__ import annotations

import re
from typing import Any

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.domain.measure.metrics import BUILTIN_METRICS
from percell4.workflows.models import ThresholdAlgorithm

# Method dropdown labels. "Grouped Otsu" is the default (the legacy per-group
# path). "Adaptive Local Clipping (two-pass)" is the per-cell two-pass extractor —
# the SAME algorithm the GUI "Adaptive Local Clipping" panel runs (its only mode),
# so it is the method to pick when reproducing a single-dataset GUI run in batch.
# The VALUES are display text only; routing/serialisation key off the sentinel
# dataclass the dialog builds, so relabeling here does not touch run_config.json.
METHOD_GROUPED = "Grouped Otsu"
METHOD_AUTO_EXTRACT = "Adaptive Local Thresholding"

# Round-name validation: letters/digits/_/-, non-numeric start, max 40 chars.
ROUND_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,39}$")

_INVALID_NAME_STYLE = "QLineEdit { background-color: #5b2a2a; }"


class RoundCard(QFrame):
    """One thresholding round as a card. Owns its widgets; exposes a value dict.

    Signals:
      - ``name_changed`` / ``channel_changed`` / ``method_changed`` — host wires
        these to the column-picker refresh and name validation.
      - ``move_up_requested`` / ``move_down_requested`` / ``remove_requested`` —
        the per-card ▲ / ▼ / ✕ controls; the host reorders/removes.
    """

    name_changed = Signal()
    channel_changed = Signal()
    method_changed = Signal()
    move_up_requested = Signal(object)
    move_down_requested = Signal(object)
    remove_requested = Signal(object)

    def __init__(self, index: int, channels: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self._build_ui(index, channels)
        self._wire_signals()
        self._apply_method_visibility()

    # ── construction ──────────────────────────────────────────────

    def _build_ui(self, index: int, channels: list[str]) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)

        # ── Header: "N · <name>" + ▲ ▼ ✕ ──
        header = QHBoxLayout()
        self._header_label = QLabel()
        self._header_label.setStyleSheet("font-weight: bold;")
        header.addWidget(self._header_label)
        header.addStretch()
        self._up_btn = QPushButton("↑")
        self._up_btn.setFixedWidth(30)
        self._up_btn.setToolTip("Move this round up")
        self._down_btn = QPushButton("↓")
        self._down_btn.setFixedWidth(30)
        self._down_btn.setToolTip("Move this round down")
        self._remove_btn = QPushButton("×")
        self._remove_btn.setFixedWidth(30)
        self._remove_btn.setToolTip("Remove this round")
        for b in (self._up_btn, self._down_btn, self._remove_btn):
            header.addWidget(b)
        outer.addLayout(header)

        # ── Shared identity row: Name / Channel ──
        ident = QGridLayout()
        ident.setHorizontalSpacing(8)
        ident.addWidget(QLabel("Name:"), 0, 0)
        self._name = QLineEdit(f"round_{index + 1}")
        self._name.setToolTip(
            "Round name — letters, digits, _ or -, up to 40 chars, "
            "not starting with a digit. Must be unique across rounds."
        )
        ident.addWidget(self._name, 0, 1)
        ident.addWidget(QLabel("Channel:"), 0, 2)
        self._channel = QComboBox()
        if channels:
            self._channel.addItems(channels)
        else:
            self._channel.addItem("(add datasets first)")
            self._channel.setEnabled(False)
        ident.addWidget(self._channel, 0, 3)
        ident.setColumnStretch(1, 1)
        ident.setColumnStretch(3, 1)
        outer.addLayout(ident)

        # ── Method row ──
        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Method:"))
        self._method = QComboBox()
        self._method.addItems([METHOD_GROUPED, METHOD_AUTO_EXTRACT])
        self._method.setToolTip(
            "Grouped Otsu: per-intensity-group Otsu (uses Algorithm / GMM / "
            "K-means).\n"
            "Adaptive Local Clipping (two-pass): the SAME detector the GUI "
            "'Adaptive Local Clipping' panel runs — a per-cell two-pass extractor "
            "driven by the smallest particle diameter (0 = auto-detect). Pick this "
            "to reproduce a single-dataset GUI run in batch; the Min. Particle "
            "Area = the panel's Min particle size. σ is the detector presmooth; "
            "µm units need a pixel size."
        )
        method_row.addWidget(self._method)
        method_row.addStretch()
        outer.addLayout(method_row)

        # ── Metric (Grouped Otsu only) ──
        # The per-cell ALC method thresholds each cell independently and ignores
        # intensity grouping, so Metric is meaningful only for Grouped Otsu; it is
        # shown/hidden with the method.
        self._metric_row = QFrame()
        metric_layout = QHBoxLayout(self._metric_row)
        metric_layout.setContentsMargins(0, 0, 0, 0)
        metric_layout.addWidget(QLabel("Metric:"))
        self._metric = QComboBox()
        self._metric.addItems(sorted(BUILTIN_METRICS.keys()))
        self._metric.setCurrentText("median_intensity")
        self._metric.setToolTip(
            "The per-cell metric to group cells by before per-group Otsu. "
            "Grouped Otsu only."
        )
        metric_layout.addWidget(self._metric)
        metric_layout.addStretch()
        outer.addWidget(self._metric_row)

        # ── Grouped Otsu sub-group ──
        self._grouped_box = QFrame()
        gbox = QHBoxLayout(self._grouped_box)
        gbox.setContentsMargins(0, 0, 0, 0)
        gbox.addWidget(QLabel("Algorithm:"))
        self._algorithm = QComboBox()
        self._algorithm.addItems(
            [ThresholdAlgorithm.GMM.value, ThresholdAlgorithm.KMEANS.value]
        )
        gbox.addWidget(self._algorithm)
        gbox.addWidget(QLabel("GMM max:"))
        self._gmm_max = QSpinBox()
        self._gmm_max.setRange(2, 20)
        self._gmm_max.setValue(10)
        gbox.addWidget(self._gmm_max)
        gbox.addWidget(QLabel("K-means K:"))
        self._kmeans_k = QSpinBox()
        self._kmeans_k.setRange(2, 20)
        self._kmeans_k.setValue(3)
        gbox.addWidget(self._kmeans_k)
        gbox.addStretch()
        outer.addWidget(self._grouped_box)

        # ── Adaptive Local Clipping sub-group ──
        self._alc_box = QFrame()
        abox = QVBoxLayout(self._alc_box)
        abox.setContentsMargins(0, 0, 0, 0)
        abox.setSpacing(4)
        # Smallest particle diameter + unit.
        smallest_row = QHBoxLayout()
        smallest_row.addWidget(QLabel("Smallest Particle Diameter:"))
        self._d_min = QDoubleSpinBox()
        self._d_min.setRange(0.0, 10000.0)  # floor set per-method by _apply_dmin_floor
        self._d_min.setDecimals(3)
        self._d_min.setSingleStep(0.05)
        self._d_min.setValue(2.0)
        self._d_min.setToolTip(
            "Smallest particle diameter to detect, in the unit at right (µm uses "
            "the dataset pixel size; px is used directly). 0 = auto-detect the "
            "smallest particle."
        )
        smallest_row.addWidget(self._d_min)
        self._size_unit = QComboBox()
        self._size_unit.addItem("µm", userData="um")
        self._size_unit.addItem("px", userData="px")
        self._size_unit.setCurrentText("px")  # default: 2 px smallest particle
        self._size_unit.setToolTip(
            "Unit for the diameter. µm resolves to pixels per dataset via each "
            "dataset's pixel size; px applies the value directly — pick px for "
            "datasets that have no pixel size metadata."
        )
        smallest_row.addWidget(self._size_unit)
        smallest_row.addStretch()
        abox.addLayout(smallest_row)
        # CNR split / threshold / GMM 2-pop.
        cnr_row = QHBoxLayout()
        self._cnr_on = QCheckBox("CNR split")
        self._cnr_on.setToolTip(
            "Split the produced foci into 1–2 populations by contrast-to-noise "
            "ratio at the CNR threshold (guided). Writes <round>_low / "
            "<round>_high masks + a /classification/<round> table. "
            "Single-timepoint datasets only."
        )
        cnr_row.addWidget(self._cnr_on)
        cnr_row.addWidget(QLabel("thr:"))
        self._cnr_threshold = QDoubleSpinBox()
        self._cnr_threshold.setRange(0.01, 1000.0)
        self._cnr_threshold.setDecimals(2)
        self._cnr_threshold.setSingleStep(0.5)
        self._cnr_threshold.setValue(5.0)
        cnr_row.addWidget(self._cnr_threshold)
        self._cnr_forced = QCheckBox("GMM 2-pop")
        self._cnr_forced.setToolTip(
            "Forced always-2 subpopulation classification (GMM two-group split). "
            "OVERRIDES the CNR threshold: the boundary is placed by a data-driven "
            "GaussianMixture two-group fit instead of the guided CNR value. "
            "Enabled only when CNR split is on."
        )
        cnr_row.addWidget(self._cnr_forced)
        cnr_row.addStretch()
        abox.addLayout(cnr_row)
        outer.addWidget(self._alc_box)

        # ── Shared trailing row: σ / Min. Particle Area + unit ──
        shared = QHBoxLayout()
        shared.addWidget(QLabel("Smoothing σ:"))
        self._sigma = QDoubleSpinBox()
        self._sigma.setRange(0.0, 20.0)
        self._sigma.setSingleStep(0.1)
        self._sigma.setValue(0.0)
        self._sigma.setToolTip(
            "Grouped Otsu: pre-threshold Gaussian smoothing.\n"
            "Adaptive Local Clipping: the detector's per-cell presmooth (Gaussian "
            "blur σ, px). Seeded to the validated 1.0 when you pick the ALC method."
        )
        shared.addWidget(self._sigma)
        shared.addSpacing(12)
        shared.addWidget(QLabel("Min. Particle Area:"))
        self._min_size = QDoubleSpinBox()
        self._min_size.setRange(0.0, 1_000_000.0)
        self._min_size.setDecimals(2)
        self._min_size.setSingleStep(1.0)
        self._min_size.setValue(3.0)
        self._min_size.setToolTip(
            "Drop connected components below this area from the round's mask "
            "(any method). 0 = keep every particle. The unit is set at right; "
            "µm² resolves via the dataset pixel size."
        )
        shared.addWidget(self._min_size)
        self._min_size_unit = QComboBox()
        self._min_size_unit.addItem("px²", userData="px")
        self._min_size_unit.addItem("µm²", userData="um2")
        self._min_size_unit.setCurrentIndex(0)  # px² — no pixel size required
        self._min_size_unit.setToolTip(
            "Unit for Min. Particle Area. px² applies the value directly; µm² "
            "resolves to pixels per dataset via each dataset's pixel size — pick "
            "px² for datasets that have no pixel size metadata."
        )
        shared.addWidget(self._min_size_unit)
        shared.addStretch()
        outer.addLayout(shared)

        self.set_index(index)

    def _wire_signals(self) -> None:
        self._name.textChanged.connect(self._on_name_changed)
        self._channel.currentTextChanged.connect(lambda _t: self.channel_changed.emit())
        self._method.currentTextChanged.connect(self._on_method_changed)
        self._algorithm.currentTextChanged.connect(lambda _t: self._apply_algo_enabled())
        self._cnr_on.stateChanged.connect(lambda _s: self._apply_cnr_enabled())
        self._cnr_forced.stateChanged.connect(lambda _s: self._apply_cnr_enabled())
        self._up_btn.clicked.connect(lambda: self.move_up_requested.emit(self))
        self._down_btn.clicked.connect(lambda: self.move_down_requested.emit(self))
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))

    # ── slots ─────────────────────────────────────────────────────

    def _on_name_changed(self, _text: str) -> None:
        self._apply_name_validity()
        self._update_header()
        self.name_changed.emit()

    def _on_method_changed(self, _text: str) -> None:
        # Seed the ALC presmooth default (σ = 1.0) when entering an ALC method
        # from σ = 0, so the detector presmooth is the validated value rather than
        # the grouped-Otsu 0 (which collapses detection). Programmatic from_dict
        # writes call _apply_method_visibility directly and skip this seed.
        if self.is_alc() and self._sigma.value() == 0.0:
            self._sigma.setValue(1.0)
        self._apply_method_visibility()
        self.method_changed.emit()

    # ── visibility / enablement ──────────────────────────────────

    def _apply_method_visibility(self) -> None:
        """Show only the selected method's sub-group; the other is hidden but keeps
        its values. σ and Min. Particle Area are always visible (shared)."""
        alc = self.is_alc()
        self._metric_row.setVisible(not alc)
        self._grouped_box.setVisible(not alc)
        self._alc_box.setVisible(alc)
        self._apply_dmin_floor()
        self._apply_algo_enabled()
        self._apply_cnr_enabled()

    def _apply_dmin_floor(self) -> None:
        """Auto-extract allows 0 (auto-detect); a positive floor otherwise."""
        floor = 0.0 if self.is_auto_extract() else 0.02
        if self._d_min.minimum() != floor:
            v = self._d_min.value()
            self._d_min.setMinimum(floor)
            # setMinimum can clamp the value up; restore if it was a valid 0.
            if v == 0.0 and floor == 0.0:
                self._d_min.setValue(0.0)

    def _apply_algo_enabled(self) -> None:
        """Algorithm gates GMM max vs K-means K (Grouped Otsu only)."""
        is_gmm = self._algorithm.currentText() == ThresholdAlgorithm.GMM.value
        self._gmm_max.setEnabled(is_gmm)
        self._kmeans_k.setEnabled(not is_gmm)

    def _apply_cnr_enabled(self) -> None:
        """CNR split is valid only on the per-cell ALC method; the threshold and GMM
        2-pop follow the split checkbox. On Grouped Otsu the whole CNR group is
        cleared and disabled so a meaningless split can never ride along into the
        config (the group is also hidden). Mirrors the old table gating."""
        if not self.is_alc():
            if self._cnr_on.isChecked():
                self._cnr_on.setChecked(False)
            if self._cnr_forced.isChecked():
                self._cnr_forced.setChecked(False)
            self._cnr_on.setEnabled(False)
            self._cnr_forced.setEnabled(False)
            self._cnr_threshold.setEnabled(False)
            return
        self._cnr_on.setEnabled(True)
        checked = self._cnr_on.isChecked()
        if not checked and self._cnr_forced.isChecked():
            self._cnr_forced.setChecked(False)
        self._cnr_forced.setEnabled(checked)
        is_forced = self._cnr_forced.isChecked()
        self._cnr_threshold.setEnabled(checked and not is_forced)

    def _apply_name_validity(self) -> None:
        if ROUND_NAME_RE.match(self._name.text()):
            self._name.setStyleSheet("")
            self._name.setToolTip(
                "Round name — letters, digits, _ or -, up to 40 chars, "
                "not starting with a digit. Must be unique across rounds."
            )
        else:
            self._name.setStyleSheet(_INVALID_NAME_STYLE)
            self._name.setToolTip(
                f"Round name must match {ROUND_NAME_RE.pattern} "
                "(letters/digits/_/-, max 40 chars, non-numeric start)"
            )

    def _update_header(self) -> None:
        self._header_label.setText(f"{self._index + 1} · {self._name.text()}")

    # ── public API ────────────────────────────────────────────────

    def set_index(self, index: int) -> None:
        """Set this card's 1-based position label (does not change the name)."""
        self._index = index
        self._update_header()

    def set_channels(self, channels: list[str]) -> None:
        """Repopulate the Channel combo from the intersection, keeping the pick."""
        current = self._channel.currentText()
        self._channel.blockSignals(True)
        self._channel.clear()
        if channels:
            self._channel.addItems(channels)
            self._channel.setEnabled(True)
            idx = self._channel.findText(current)
            if idx >= 0:
                self._channel.setCurrentIndex(idx)
        else:
            self._channel.addItem("(add datasets first)")
            self._channel.setEnabled(False)
        self._channel.blockSignals(False)

    def set_move_enabled(self, *, up: bool, down: bool) -> None:
        """Disable ▲ on the first card and ▼ on the last so a boundary click is
        visibly inert rather than a silent no-op."""
        self._up_btn.setEnabled(up)
        self._down_btn.setEnabled(down)

    def is_auto_extract(self) -> bool:
        return self._method.currentText() == METHOD_AUTO_EXTRACT

    def is_alc(self) -> bool:
        """Per-cell Adaptive Local Clipping row (the only per-cell method now)."""
        return self.is_auto_extract()

    def name_is_valid(self) -> bool:
        return bool(ROUND_NAME_RE.match(self._name.text()))

    def to_dict(self) -> dict[str, Any]:
        """Snapshot the card's values — the same keys the table rows produced,
        minus the σ-clipping-only ``k`` / ``global_sigma``."""
        return {
            "name": self._name.text(),
            "channel": self._channel.currentText(),
            "metric": self._metric.currentText(),
            "algorithm": self._algorithm.currentText(),
            "method": self._method.currentText(),
            "gmm_max": self._gmm_max.value(),
            "kmeans_k": self._kmeans_k.value(),
            "sigma": self._sigma.value(),
            "d_min_um": self._d_min.value(),
            "size_unit": self._size_unit.currentData(),
            "cnr_classify": self._cnr_on.isChecked(),
            "cnr_threshold": self._cnr_threshold.value(),
            "cnr_forced": self._cnr_forced.isChecked(),
            "min_particle_size": self._min_size.value(),
            "min_particle_size_unit": self._min_size_unit.currentData(),
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        """Load values from a dict (mirrors ``to_dict``). Tolerates a stale
        ``k`` / ``global_sigma`` in the dict (ignored). Programmatic — does NOT
        re-seed σ, so a saved σ (including 0) survives a reorder round-trip."""
        self._name.setText(data.get("name", ""))
        ch_idx = self._channel.findText(data.get("channel", ""))
        if ch_idx >= 0:
            self._channel.setCurrentIndex(ch_idx)
        self._metric.setCurrentText(data.get("metric", "median_intensity"))
        self._algorithm.setCurrentText(
            data.get("algorithm", ThresholdAlgorithm.GMM.value)
        )
        self._method.setCurrentText(data.get("method", METHOD_GROUPED))
        self._gmm_max.setValue(int(data.get("gmm_max", 10)))
        self._kmeans_k.setValue(int(data.get("kmeans_k", 3)))
        self._sigma.setValue(float(data.get("sigma", 0.0)))
        # Set the per-method floor BEFORE the value so a 0 (auto-detect, valid only
        # for auto-extraction) is not clamped up to a positive minimum.
        self._apply_dmin_floor()
        self._d_min.setValue(float(data.get("d_min_um", 0.40)))
        unit_idx = self._size_unit.findData(data.get("size_unit", "um"))
        self._size_unit.setCurrentIndex(unit_idx if unit_idx >= 0 else 0)
        self._cnr_on.setChecked(bool(data.get("cnr_classify", False)))
        self._cnr_threshold.setValue(float(data.get("cnr_threshold", 5.0)))
        self._cnr_forced.setChecked(bool(data.get("cnr_forced", False)))
        self._min_size.setValue(float(data.get("min_particle_size", 0.0)))
        min_unit_idx = self._min_size_unit.findData(
            data.get("min_particle_size_unit", "px")
        )
        self._min_size_unit.setCurrentIndex(min_unit_idx if min_unit_idx >= 0 else 0)
        # Programmatic visibility refresh — skips the σ auto-seed.
        self._apply_method_visibility()
        self._apply_name_validity()
