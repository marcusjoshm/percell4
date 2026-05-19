"""Shared grouped-thresholding settings widget.

Single source of truth for the metric / algorithm / GMM-or-K-means options
/ Gaussian-σ widget block consumed by:

* ``gui/grouped_seg_panel.py`` (single-pass Grouped Threshold workflow)
* ``gui/workflows/dilute_phase/panel.py`` (iterative dilute-phase peeling)

The widget owns only Action-shaped state (per-run knobs); Selector-shaped
fields (channel / segmentation / mask) remain on ``SessionWindow`` per the
2026-05-12/2026-05-14 sibling-dialog vs. session-consolidation rulings.
Construction mirrors the original block at ``grouped_seg_panel.py:67-134``
so the user-visible layout stays bit-identical.
"""

from __future__ import annotations

from dataclasses import dataclass

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from percell4.domain.measure.metrics import BUILTIN_METRICS


@dataclass(frozen=True)
class GroupedThresholdConfig:
    """Immutable snapshot of the settings widget's current values.

    ``algorithm`` is the user-facing label (``"GMM"`` or ``"K-means"``);
    ``gmm_criterion`` is lowercased to match
    :func:`percell4.domain.measure.grouper.group_cells_gmm`'s contract.
    """

    metric: str
    algorithm: str
    gmm_criterion: str
    gmm_max_components: int
    kmeans_n_clusters: int
    sigma: float


class GroupedThresholdSettingsWidget(QWidget):
    """Metric / algorithm / GMM-or-K-means options / σ as one reusable form.

    Emits :attr:`config_changed` whenever any contained widget's user-edit
    signal fires, so consumers can drive Start-button enable state without
    re-wiring every child signal individually.
    """

    config_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._connect_change_signals()

    # ──────────────────────────────────────────────────────────────
    # Construction — mirrors grouped_seg_panel.py:67-134 verbatim at
    # the time of extraction so the visual surface stays identical.
    # ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ── Metric selector ──
        metric_row = QHBoxLayout()
        metric_row.addWidget(QLabel("Metric:"))
        self._metric_combo = QComboBox()
        self._metric_combo.addItems(list(BUILTIN_METRICS.keys()))
        self._metric_combo.setCurrentText("mean_intensity")
        metric_row.addWidget(self._metric_combo)
        layout.addLayout(metric_row)

        # ── Algorithm selector ──
        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel("Algorithm:"))
        self._algo_combo = QComboBox()
        self._algo_combo.addItems(["GMM", "K-means"])
        self._algo_combo.currentTextChanged.connect(self._on_algorithm_changed)
        algo_row.addWidget(self._algo_combo)
        layout.addLayout(algo_row)

        # ── GMM Options ──
        self._gmm_group = QGroupBox("GMM Options")
        gmm_layout = QVBoxLayout(self._gmm_group)

        crit_row = QHBoxLayout()
        crit_row.addWidget(QLabel("Criterion:"))
        self._criterion_combo = QComboBox()
        self._criterion_combo.addItems(["BIC", "Silhouette"])
        crit_row.addWidget(self._criterion_combo)
        gmm_layout.addLayout(crit_row)

        max_row = QHBoxLayout()
        max_row.addWidget(QLabel("Max components:"))
        self._max_components = QSpinBox()
        self._max_components.setRange(2, 20)
        self._max_components.setValue(10)
        max_row.addWidget(self._max_components)
        gmm_layout.addLayout(max_row)

        layout.addWidget(self._gmm_group)

        # ── K-means Options ──
        self._kmeans_group = QGroupBox("K-means Options")
        km_layout = QVBoxLayout(self._kmeans_group)

        k_row = QHBoxLayout()
        k_row.addWidget(QLabel("Number of groups:"))
        self._n_clusters = QSpinBox()
        self._n_clusters.setRange(2, 20)
        self._n_clusters.setValue(3)
        k_row.addWidget(self._n_clusters)
        km_layout.addLayout(k_row)

        layout.addWidget(self._kmeans_group)
        self._kmeans_group.setVisible(False)

        # ── Threshold Options ──
        self._thresh_group = QGroupBox("Threshold Options")
        thresh_layout = QVBoxLayout(self._thresh_group)

        sigma_row = QHBoxLayout()
        sigma_row.addWidget(QLabel("Gaussian σ:"))
        self._sigma = QDoubleSpinBox()
        self._sigma.setRange(0.0, 20.0)
        self._sigma.setValue(0.0)
        self._sigma.setSingleStep(0.5)
        sigma_row.addWidget(self._sigma)
        thresh_layout.addLayout(sigma_row)

        layout.addWidget(self._thresh_group)

    def _connect_change_signals(self) -> None:
        """Wire every child widget's user-edit signal to ``config_changed``.

        Per ``docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md``:
        downstream consumers (e.g. the dilute-phase panel's Start button)
        rely on this single aggregated signal to re-validate.
        """
        self._metric_combo.currentIndexChanged.connect(self.config_changed.emit)
        self._algo_combo.currentIndexChanged.connect(self.config_changed.emit)
        self._criterion_combo.currentIndexChanged.connect(self.config_changed.emit)
        self._max_components.valueChanged.connect(self.config_changed.emit)
        self._n_clusters.valueChanged.connect(self.config_changed.emit)
        self._sigma.valueChanged.connect(self.config_changed.emit)

    # ──────────────────────────────────────────────────────────────
    # Slots
    # ──────────────────────────────────────────────────────────────

    def _on_algorithm_changed(self, text: str) -> None:
        self._gmm_group.setVisible(text == "GMM")
        self._kmeans_group.setVisible(text == "K-means")

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    def current_config(self) -> GroupedThresholdConfig:
        """Snapshot the live widget state into a frozen dataclass."""
        return GroupedThresholdConfig(
            metric=self._metric_combo.currentText(),
            algorithm=self._algo_combo.currentText(),
            gmm_criterion=self._criterion_combo.currentText().lower(),
            gmm_max_components=self._max_components.value(),
            kmeans_n_clusters=self._n_clusters.value(),
            sigma=self._sigma.value(),
        )

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable every contained widget without changing visibility.

        ``QWidget.setEnabled`` would propagate to hidden children too, but
        this explicit per-widget loop keeps the visible/hidden state of
        the algorithm-options groups intact while still locking edits.
        """
        for widget in (
            self._metric_combo,
            self._algo_combo,
            self._criterion_combo,
            self._max_components,
            self._n_clusters,
            self._sigma,
            self._gmm_group,
            self._kmeans_group,
            self._thresh_group,
        ):
            widget.setEnabled(enabled)
