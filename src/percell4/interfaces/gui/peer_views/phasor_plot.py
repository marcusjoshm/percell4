"""Phasor plot window — 2D histogram density of FLIM phasor coordinates.

Supports multiple named, colored ROI ellipses. Each ROI represents a
distinct lifetime population. All visible ROIs combine into a single
integer-labeled mask for downstream measurement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final

import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import QRectF, QSettings, QTimer, Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from percell4.application.session import Event, Session
from percell4.domain.flim.phasor_display import (
    compute_valid_phasor_pixels,
    mask_shape_matches,
)

COLOR_CYCLE: Final[tuple[str, ...]] = (
    "#3498db", "#e74c3c", "#2ecc71", "#f39c12",
    "#9b59b6", "#1abc9c", "#e67e22", "#34495e",
)


# JSON schema version for the saved ROI file.
#   v1 = pre-GMM (origin field absent; loaded as origin="manual"; gmm_fit ignored)
#   v2 = adds top-level schema_version + per-ROI origin / gmm_fit (single cov_f, single shift)
#   v3 = splits cov_f into stretch_parallel / stretch_perpendicular and shift into
#        shift_parallel / shift_perpendicular for independent per-axis control;
#        v2 files are migrated on load (cov_f -> both stretch axes; shift -> parallel).
ROI_JSON_SCHEMA_VERSION: Final[int] = 3


@dataclass
class GMMFit:
    """Eigenstructure + spinbox state for a GMM-origin PhasorROI.

    The ``mean_*`` and ``lambda_*`` fields are constants captured at fit
    time. The four axis coefficients (``stretch_parallel``,
    ``stretch_perpendicular``, ``shift_parallel``,
    ``shift_perpendicular``) are the current spinbox values; together
    they describe an exact placement relative to the cluster mean.

    GMM ROIs are non-draggable; the spinboxes are the exclusive way to
    move and scale them. There is no drag-preserving anchor.
    """

    mean_g: float
    mean_s: float
    lambda_major: float
    lambda_minor: float
    principal_angle_rad: float
    stretch_parallel: float
    stretch_perpendicular: float
    shift_parallel: float
    shift_perpendicular: float
    shape: str  # "circle" | "ellipse"
    criterion: str | None  # "BIC" | "AIC" | None (when n was manual)
    sampled_pixels: int  # shared across all ROIs from one GMM run

    @classmethod
    def from_dict(cls, d: dict) -> GMMFit:
        """Tolerant load of a GMMFit JSON sub-dict.

        Migrates v2 fields silently: a v2 dict carries ``cov_f`` (which
        we treat as a uniform stretch on both axes) and ``shift`` (which
        we treat as ``shift_parallel``); the perpendicular versions
        default to 0 / cov_f respectively. v3 dicts carry all four
        explicitly. Raises ``ValueError`` on missing required fields so
        the caller can demote a malformed ``gmm_fit`` to ``None``.
        """
        try:
            # v3 first; fall back to v2 keys if missing.
            if "stretch_parallel" in d:
                stretch_parallel = float(d["stretch_parallel"])
                stretch_perpendicular = float(d["stretch_perpendicular"])
            else:
                # v2 → v3 migration: single cov_f maps to both stretch axes.
                cov_f = float(d["cov_f"])
                stretch_parallel = cov_f
                stretch_perpendicular = cov_f

            if "shift_parallel" in d:
                shift_parallel = float(d["shift_parallel"])
                shift_perpendicular = float(d["shift_perpendicular"])
            else:
                shift_parallel = float(d["shift"])
                shift_perpendicular = 0.0

            return cls(
                mean_g=float(d["mean_g"]),
                mean_s=float(d["mean_s"]),
                lambda_major=float(d["lambda_major"]),
                lambda_minor=float(d["lambda_minor"]),
                principal_angle_rad=float(d["principal_angle_rad"]),
                stretch_parallel=stretch_parallel,
                stretch_perpendicular=stretch_perpendicular,
                shift_parallel=shift_parallel,
                shift_perpendicular=shift_perpendicular,
                shape=str(d["shape"]),
                criterion=(None if d.get("criterion") is None
                           else str(d["criterion"])),
                sampled_pixels=int(d.get("sampled_pixels", 0)),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"Invalid gmm_fit data: {e}") from e

    def to_dict(self) -> dict:
        return {
            "mean_g": self.mean_g,
            "mean_s": self.mean_s,
            "lambda_major": self.lambda_major,
            "lambda_minor": self.lambda_minor,
            "principal_angle_rad": self.principal_angle_rad,
            "stretch_parallel": self.stretch_parallel,
            "stretch_perpendicular": self.stretch_perpendicular,
            "shift_parallel": self.shift_parallel,
            "shift_perpendicular": self.shift_perpendicular,
            "shape": self.shape,
            "criterion": self.criterion,
            "sampled_pixels": self.sampled_pixels,
        }


@dataclass
class PhasorROI:
    """Single phasor ROI definition."""

    name: str
    center: tuple[float, float]
    radii: tuple[float, float]
    angle_deg: float
    label: int
    color: str
    visible: bool = True
    origin: str = "manual"  # "manual" | "gmm"
    gmm_fit: GMMFit | None = None  # present iff origin == "gmm"

    @classmethod
    def from_dict(cls, d: dict, label: int, default_color: str) -> PhasorROI:
        """Create from JSON dict with validation.

        ``origin`` defaults to ``"manual"`` for v1 JSON files (no field).
        A malformed ``gmm_fit`` is logged and demoted to ``None`` rather
        than failing the whole ROI — same defensive policy as the
        existing per-ROI try/except in ``_on_load_rois``.
        """
        try:
            center = tuple(float(x) for x in d["center"])
            radii = tuple(float(x) for x in d["radii"])
            if len(center) != 2 or len(radii) != 2:
                raise ValueError("center and radii must be 2-element sequences")

            origin = str(d.get("origin", "manual"))
            gmm_fit_data = d.get("gmm_fit")
            gmm_fit: GMMFit | None = None
            if isinstance(gmm_fit_data, dict):
                try:
                    gmm_fit = GMMFit.from_dict(gmm_fit_data)
                except ValueError:
                    # Malformed gmm_fit — keep the ROI but drop the fit
                    # so the rest of the file still loads. The user sees
                    # the ROI as a manual-style pin without cov_f/shift.
                    gmm_fit = None

            return cls(
                name=str(d["name"]),
                center=center,
                radii=radii,
                angle_deg=float(d.get("angle_deg", 0)),
                label=label,
                color=str(d.get("color", default_color)),
                origin=origin,
                gmm_fit=gmm_fit,
            )
        except (KeyError, TypeError) as e:
            raise ValueError(f"Invalid ROI data: {e}") from e

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "name": self.name,
            "center": list(self.center),
            "radii": list(self.radii),
            "angle_deg": self.angle_deg,
            "color": self.color,
            "origin": self.origin,
            "gmm_fit": self.gmm_fit.to_dict() if self.gmm_fit is not None else None,
        }


@dataclass
class _ROIWidget:
    """GUI objects for one phasor ROI."""

    roi: pg.RectROI
    curve: pg.PlotCurveItem
    phasor_roi: PhasorROI
    cached_mask: np.ndarray | None = None


class PhasorPlotWindow(QMainWindow):
    """Phasor plot window with multi-ROI support.

    Multiple named, colored elliptical ROIs can be placed on the phasor
    histogram. All visible ROIs combine into a single labeled mask.

    Communication with the viewer is decoupled via signals:
    - preview_roi_upserted: emitted when an ROI's preview needs creation/update
    - preview_roi_removed: emitted when an ROI's preview should be removed
    - preview_all_cleared: emitted when every preview should be removed
    - mask_applied: emitted when user clicks "Apply Visible as Mask"
    The launcher connects these to mediate viewer + HDF5 access.
    """

    # Per-ROI preview signals. One napari layer per ROI named
    # ``_phasor_roi_preview_<name>``. The launcher creates/updates the
    # layer on upsert (carrying ``visible`` to control layer.visible
    # without flipping layer existence) and removes it on remove. A
    # rename emits remove(old_name) followed by upsert(new_name, ...).
    # (roi_name, binary_mask_uint8, hex_color, visible)
    preview_roi_upserted = Signal(str, object, str, bool)
    preview_roi_removed = Signal(str)
    preview_all_cleared = Signal()
    # list[tuple[str, ndarray, str]] — per-ROI (name, binary_mask, hex_color)
    mask_applied = Signal(object)

    def __init__(
        self,
        session: Session,
        get_repo: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__()
        self._session = session
        self._get_repo = get_repo
        self.setWindowTitle("PerCell4 — Phasor Plot")
        self.resize(850, 600)

        self._g_map: np.ndarray | None = None
        self._s_map: np.ndarray | None = None
        self._intensity: np.ndarray | None = None
        self._g_map_unfiltered: np.ndarray | None = None
        self._s_map_unfiltered: np.ndarray | None = None
        self._labels: np.ndarray | None = None
        self._labels_flat: np.ndarray | None = None
        self._total_valid_pixels: int = 0

        # Active-mask filter state. Loaded lazily when the user enables
        # the checkbox so we don't pay HDF5-read cost when the filter
        # isn't engaged. Cleared when active_mask changes.
        self._active_mask_array: np.ndarray | None = None
        self._active_mask_flat: np.ndarray | None = None

        # FlimPanel-driven filter state (U5). All four are GUI-local —
        # neither stored on Session nor derived from Session events.
        self._intensity_threshold: float = 0.0
        self._ref_circle_tau_ns: float | None = None
        self._ref_circle_radius: float | None = None
        self._ref_circle_center: tuple[float, float] | None = None

        self._roi_widgets: list[_ROIWidget] = []
        self._selected_roi_index: int | None = None

        # Manual exclusion bitmap for "Clear within ROI" feature.
        # Pixels marked True are subtracted from the visible histogram and
        # from any "Apply Visible as Mask" output. Lazy-allocated on first
        # Clear; reset to None whenever set_phasor_data installs a new
        # (g, s) frame (the bitmap is bound to the frame, not to abstract
        # pixel indices) and on explicit Reset.
        self._cleared_mask: np.ndarray | None = None

        self._build_ui()
        self._restore_geometry()

        # Debounced preview + filter timers
        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(100)
        self._preview_timer.timeout.connect(self._update_preview)

        self._filter_timer = QTimer()
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._refresh_histogram)
        self._unsubs = [
            self._session.subscribe(Event.FILTER_CHANGED, self._on_filter_changed),
            self._session.subscribe(
                Event.ACTIVE_MASK_CHANGED, self._on_active_mask_changed
            ),
            self._session.subscribe(Event.DATASET_CHANGED, self._on_dataset_changed),
            self._session.subscribe(
                Event.ACTIVE_CHANNEL_CHANGED, self._on_active_channel_changed,
            ),
        ]
        # Sync checkbox state for whatever mask is already active when the
        # window is created (e.g., re-opening the phasor plot after a
        # mask was set elsewhere).
        self._on_active_mask_changed()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # Left: plot + controls
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Controls row
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Harmonic:"))
        self._harmonic_combo = QComboBox()
        self._harmonic_combo.addItems(["1", "2", "3"])
        controls.addWidget(self._harmonic_combo)

        controls.addSpacing(16)
        self._filtered_check = QCheckBox("Filtered")
        # Checkbox styling inherited from global theme
        self._filtered_check.setEnabled(False)
        self._filtered_check.toggled.connect(self._on_filtered_toggled)
        controls.addWidget(self._filtered_check)

        controls.addSpacing(16)
        self._mask_filter_check = QCheckBox("Filter by active mask")
        self._mask_filter_check.setEnabled(False)
        self._mask_filter_check.setToolTip(
            "Restrict the phasor histogram to pixels in the active mask. "
            "Composes with the cell-selection filter via boolean AND."
        )
        self._mask_filter_check.toggled.connect(self._on_mask_filter_toggled)
        controls.addWidget(self._mask_filter_check)

        controls.addStretch()

        self._save_svg_btn = QPushButton("Save Phasor .SVG")
        self._save_svg_btn.setToolTip(
            "Save the current phasor plot — including any ROIs — as a vector "
            "SVG. Each histogram, ROI ellipse, handle, tick, and label is a "
            "separate object editable in Illustrator / Inkscape / Affinity."
        )
        self._save_svg_btn.clicked.connect(self._on_save_svg)
        controls.addWidget(self._save_svg_btn)

        left_layout.addLayout(controls)

        # Selected-ROI banner — shows the selected ROI's name above the
        # plot, color-matched to the ROI. Empty when no ROI is selected.
        self._selected_banner = QLabel("")
        self._selected_banner.setStyleSheet(
            "font-weight: bold; padding: 4px 8px; min-height: 18px;"
        )
        left_layout.addWidget(self._selected_banner)

        # Plot
        self._plot = pg.PlotWidget()
        from percell4.gui import theme

        self._plot.setBackground(theme.BACKGROUND)
        self._plot.setAspectLocked(False)
        self._plot.setLabel("bottom", "G")
        self._plot.setLabel("left", "S")
        self._plot.setXRange(-0.005, 1.005, padding=0)
        self._plot.setYRange(0, 0.7, padding=0)
        self._plot.disableAutoRange()
        self._plot.getAxis("bottom").enableAutoSIPrefix(False)
        self._plot.getAxis("left").enableAutoSIPrefix(False)
        left_layout.addWidget(self._plot)

        # Histogram image
        self._hist_item = None

        # Universal circle
        theta = np.linspace(0, np.pi, 200)
        semi_g = 0.5 + 0.5 * np.cos(theta)
        semi_s = 0.5 * np.sin(theta)
        self._semicircle = pg.PlotCurveItem(
            semi_g, semi_s, pen=pg.mkPen(theme.TEXT_LABEL, width=2),
        )
        self._semicircle.setZValue(10)
        self._plot.addItem(self._semicircle)

        # Reference-circle filter overlay (FlimPanel-driven). Hidden until
        # set_phasor_filters resolves a (G_c, S_c) and a radius. Z-value 9
        # so it sits above the histogram but below the ROI ellipses.
        self._ref_circle_curve = pg.PlotCurveItem(
            pen=pg.mkPen("#ffd166", width=2, style=Qt.DashLine),
        )
        self._ref_circle_curve.setZValue(9)
        self._ref_circle_curve.setVisible(False)
        self._plot.addItem(self._ref_circle_curve)

        # Cluster-center scatter — one point per GMM-origin ROI at its
        # stored (mean_g, mean_s). Color-matched to the ROI. Updates on
        # place_gmm_rois / Remove ROI / dataset reset / cov_f-shift slot
        # via _update_cluster_center_marker.
        self._cluster_center_scatter = pg.ScatterPlotItem(
            pen=None, symbol="+", size=12,
        )
        self._cluster_center_scatter.setZValue(11)  # above ROIs
        self._plot.addItem(self._cluster_center_scatter)

        main_layout.addWidget(left, stretch=3)

        # Right: ROI panel
        right = QWidget()
        right.setMaximumWidth(220)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)

        # Add/Remove buttons
        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add ROI")
        btn_add.clicked.connect(self._on_add_roi)
        btn_row.addWidget(btn_add)
        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self._on_remove_roi)
        btn_row.addWidget(btn_remove)
        right_layout.addLayout(btn_row)

        # ROI list. Each row carries a real Qt checkbox (Qt.ItemIsUserCheckable)
        # so the user can toggle ROI visibility without first selecting the
        # row and using the panel "Visible" checkbox.
        self._roi_list = QListWidget()
        self._roi_list.currentRowChanged.connect(self._on_roi_list_selection)
        self._roi_list.itemChanged.connect(self._on_roi_list_item_changed)
        right_layout.addWidget(self._roi_list)

        # Selected ROI controls
        sel_group = QGroupBox("Selected ROI")
        sel_layout = QVBoxLayout(sel_group)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.editingFinished.connect(self._on_name_edited)
        name_row.addWidget(self._name_edit)
        sel_layout.addLayout(name_row)

        angle_row = QHBoxLayout()
        angle_row.addWidget(QLabel("Angle:"))
        self._angle_spin = QSpinBox()
        self._angle_spin.setRange(-90, 90)
        self._angle_spin.setValue(0)
        self._angle_spin.setSuffix("°")
        self._angle_spin.valueChanged.connect(self._on_angle_changed)
        angle_row.addWidget(self._angle_spin)
        sel_layout.addLayout(angle_row)

        # Per-axis stretch + shift coefficients for GMM-origin ROIs.
        # ``setEnabled`` (not ``setVisible``) preserves layout stability
        # when switching between manual and GMM selections.
        # Stretch is a multiplier on √λ; shift is a multiplier on √λ
        # (positive parallel = away from origin along major axis).
        stretch_par_row = QHBoxLayout()
        stretch_par_row.addWidget(QLabel("Stretch ∥:"))
        self._stretch_parallel_spin = QDoubleSpinBox()
        self._stretch_parallel_spin.setRange(0.1, 10.0)
        self._stretch_parallel_spin.setSingleStep(0.1)
        self._stretch_parallel_spin.setDecimals(2)
        self._stretch_parallel_spin.setValue(2.0)
        self._stretch_parallel_spin.setEnabled(False)
        self._stretch_parallel_spin.setToolTip(
            "Multiplier on √λ_major — controls ROI extent along the cluster's "
            "major eigenaxis. cov_f coefficient for the parallel direction."
        )
        self._stretch_parallel_spin.valueChanged.connect(self._on_gmm_param_changed)
        stretch_par_row.addWidget(self._stretch_parallel_spin)
        sel_layout.addLayout(stretch_par_row)

        stretch_perp_row = QHBoxLayout()
        stretch_perp_row.addWidget(QLabel("Stretch ⊥:"))
        self._stretch_perpendicular_spin = QDoubleSpinBox()
        self._stretch_perpendicular_spin.setRange(0.1, 10.0)
        self._stretch_perpendicular_spin.setSingleStep(0.1)
        self._stretch_perpendicular_spin.setDecimals(2)
        self._stretch_perpendicular_spin.setValue(2.0)
        self._stretch_perpendicular_spin.setEnabled(False)
        self._stretch_perpendicular_spin.setToolTip(
            "Multiplier on √λ_minor — controls ROI extent along the cluster's "
            "minor eigenaxis. cov_f coefficient for the perpendicular direction."
        )
        self._stretch_perpendicular_spin.valueChanged.connect(self._on_gmm_param_changed)
        stretch_perp_row.addWidget(self._stretch_perpendicular_spin)
        sel_layout.addLayout(stretch_perp_row)

        shift_par_row = QHBoxLayout()
        shift_par_row.addWidget(QLabel("Shift ∥:"))
        self._shift_parallel_spin = QDoubleSpinBox()
        self._shift_parallel_spin.setRange(-5.0, 5.0)
        self._shift_parallel_spin.setSingleStep(0.1)
        self._shift_parallel_spin.setDecimals(2)
        self._shift_parallel_spin.setValue(0.0)
        self._shift_parallel_spin.setEnabled(False)
        self._shift_parallel_spin.setToolTip(
            "Translation along the major eigenvector by shift × √λ_major. "
            "Positive = away from origin along the major direction."
        )
        self._shift_parallel_spin.valueChanged.connect(self._on_gmm_param_changed)
        shift_par_row.addWidget(self._shift_parallel_spin)
        sel_layout.addLayout(shift_par_row)

        shift_perp_row = QHBoxLayout()
        shift_perp_row.addWidget(QLabel("Shift ⊥:"))
        self._shift_perpendicular_spin = QDoubleSpinBox()
        self._shift_perpendicular_spin.setRange(-5.0, 5.0)
        self._shift_perpendicular_spin.setSingleStep(0.1)
        self._shift_perpendicular_spin.setDecimals(2)
        self._shift_perpendicular_spin.setValue(0.0)
        self._shift_perpendicular_spin.setEnabled(False)
        self._shift_perpendicular_spin.setToolTip(
            "Translation along the minor eigenvector by shift × √λ_minor. "
            "Positive = perpendicular to the major axis (90° counter-clockwise)."
        )
        self._shift_perpendicular_spin.valueChanged.connect(self._on_gmm_param_changed)
        shift_perp_row.addWidget(self._shift_perpendicular_spin)
        sel_layout.addLayout(shift_perp_row)

        self._reset_fit_btn = QPushButton("Reset to fit")
        self._reset_fit_btn.setToolTip(
            "Reset stretch (parallel + perpendicular) to 2.0 and shift to 0; "
            "snaps the ROI center back to the cluster mean."
        )
        self._reset_fit_btn.setEnabled(False)
        self._reset_fit_btn.clicked.connect(self._on_reset_fit_clicked)
        sel_layout.addWidget(self._reset_fit_btn)

        self._vis_check = QCheckBox("Visible")
        self._vis_check.setChecked(True)
        self._vis_check.toggled.connect(self._on_visibility_toggled)
        sel_layout.addWidget(self._vis_check)

        right_layout.addWidget(sel_group)

        # Apply + Save/Load buttons
        btn_apply = QPushButton("Apply Visible as Mask")
        btn_apply.clicked.connect(self._on_apply_mask)
        right_layout.addWidget(btn_apply)

        io_row = QHBoxLayout()
        btn_save = QPushButton("Save ROIs...")
        btn_save.clicked.connect(self._on_save_rois)
        io_row.addWidget(btn_save)
        btn_load = QPushButton("Load ROIs...")
        btn_load.clicked.connect(self._on_load_rois)
        io_row.addWidget(btn_load)
        right_layout.addLayout(io_row)

        main_layout.addWidget(right, stretch=1)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("No phasor data loaded")

    # ── Public API (used by FlimPanel) ────────────────────────

    def set_phasor_filters(
        self,
        *,
        intensity_threshold: float,
        ref_circle_tau_ns: float | None,
        ref_circle_radius: float | None,
    ) -> None:
        """Push the FlimPanel-driven filter values into the phasor plot.

        Resolves ``(G_c, S_c)`` immediately from harmonic + freq + tau,
        invalidates per-ROI caches, refreshes the reference-circle
        overlay (clipped to the visible viewport), and starts the
        debounced histogram refresh.

        When ``flim_frequency_mhz`` is missing from the dataset metadata,
        the reference-circle filter degrades silently: the overlay is
        hidden, ``_ref_circle_center`` stays None, and the histogram
        path uses the remaining filters. A status-bar message tells the
        user the filter is not applied.
        """
        from percell4.domain.flim.phasor import universal_circle_gs

        self._intensity_threshold = float(intensity_threshold)
        self._ref_circle_tau_ns = ref_circle_tau_ns
        self._ref_circle_radius = ref_circle_radius

        # Resolve the universal-circle anchor when both inputs are present
        # and the dataset has a frequency. Otherwise leave the center at
        # None so compute_valid_phasor_pixels skips the ref-circle filter.
        self._ref_circle_center = None
        ref_active_attempt = (
            ref_circle_tau_ns is not None and ref_circle_radius is not None
        )
        freq_mhz = None
        if self._session.dataset is not None:
            freq_mhz = self._session.dataset.metadata.get("flim_frequency_mhz")

        if ref_active_attempt and freq_mhz is None:
            self._status.showMessage(
                "Reference circle requires flim_frequency_mhz — filter not applied",
                5000,
            )
        elif ref_active_attempt and freq_mhz is not None:
            try:
                harmonic = int(self._harmonic_combo.currentText())
            except (ValueError, AttributeError):
                harmonic = 1
            self._ref_circle_center = universal_circle_gs(
                harmonic, float(ref_circle_tau_ns), float(freq_mhz),
            )

        self._update_ref_circle_overlay()

        # Multi-vector cache invalidation (per
        # percell4-selection-filtering-multi-roi-patterns.md Pattern 5):
        # every per-ROI cached mask + the active-mask flat cache.
        for w in self._roi_widgets:
            w.cached_mask = None
        self._active_mask_array = None
        self._active_mask_flat = None

        self._filter_timer.start()

    def place_gmm_rois(
        self,
        geometries: list,  # list[PhasorROIGeometry] from RunPhasorGMM
        *,
        shape: str,
        criterion: str | None,
        sampled_pixels: int,
    ) -> None:
        """Append GMM-origin ROIs to the existing list.

        Honors the 10-ROI cap by truncating the input rather than
        clobbering existing entries; reports the truncation count in the
        status message. Color cycle continues from
        ``len(_roi_widgets) + i`` so GMM ROIs don't collide visually with
        manual ROIs already in the list.

        Each ROI is constructed with ``cov_f=2.0`` and ``shift=0.0`` —
        matching the geometries that came back from the use case (which
        was called with the FlimPanel's spinbox values; the resulting
        ``center``/``radii``/``angle_deg`` are consistent with those
        defaults). The Selected-ROI panel's spinboxes pick up the same
        values when this ROI is selected.
        """
        if self._g_map is None:
            self._status.showMessage(
                "Phasor data missing — GMM result discarded", 5000,
            )
            return

        existing = len(self._roi_widgets)
        available = 10 - existing
        if available <= 0:
            self._status.showMessage(
                "ROI list full (10 max) — remove some before running GMM",
                5000,
            )
            return

        truncated = list(geometries)[:available]
        n_dropped = len(geometries) - len(truncated)

        for i, geo in enumerate(truncated):
            global_idx = existing + i
            color = COLOR_CYCLE[global_idx % len(COLOR_CYCLE)]
            fit = GMMFit(
                mean_g=geo.mean_g, mean_s=geo.mean_s,
                lambda_major=geo.lambda_major, lambda_minor=geo.lambda_minor,
                principal_angle_rad=geo.principal_angle_rad,
                stretch_parallel=2.0, stretch_perpendicular=2.0,
                shift_parallel=0.0, shift_perpendicular=0.0,
                shape=shape,
                criterion=criterion,
                sampled_pixels=int(sampled_pixels),
            )
            phasor_roi = PhasorROI(
                name=self._make_unique_name(f"GMM_{geo.label}"),
                center=geo.center, radii=geo.radii, angle_deg=geo.angle_deg,
                label=global_idx + 1, color=color,
                origin="gmm", gmm_fit=fit,
            )
            self._create_roi_widget(phasor_roi)

        self._refresh_roi_list()
        self._update_cluster_center_marker()

        n_placed = len(truncated)
        msg = (
            f"GMM placed {n_placed} ROIs "
            f"(criterion={criterion or 'manual'}, sampled {sampled_pixels:,} pixels)"
        )
        if n_dropped > 0:
            msg += f" — truncated {n_dropped} due to 10-ROI cap"
        self._status.showMessage(msg, 0)
        self._preview_timer.start()

    def _refresh_selected_roi_highlight(self) -> None:
        """Recolor only the selected ROI's dashed bounding-box rectangle.

        - Selected: RectROI dashed black, width 1.
        - Non-selected: RectROI dashed in the ROI's color, width 1.
        - The ellipse curve always renders in the ROI's color (never
          recolored on selection) so the user can still tie list
          entries to plot shapes by color.

        Banner above the plot reflects the selected ROI's original
        color regardless.
        """
        for i, w in enumerate(self._roi_widgets):
            color = w.phasor_roi.color
            if i == self._selected_roi_index:
                w.roi.setPen(pg.mkPen("black", width=1, style=Qt.DashLine))
            else:
                w.roi.setPen(pg.mkPen(color, width=1, style=Qt.DashLine))
            # Ellipse curve is the load-bearing color cue; never recolored.
            w.curve.setPen(pg.mkPen(color, width=2))

        if (
            self._selected_roi_index is None
            or self._selected_roi_index >= len(self._roi_widgets)
        ):
            self._selected_banner.setText("")
            return
        roi = self._roi_widgets[self._selected_roi_index].phasor_roi
        self._selected_banner.setText(f"Selected: {roi.name}")
        self._selected_banner.setStyleSheet(
            f"font-weight: bold; padding: 4px 8px; min-height: 18px; color: {roi.color};"
        )

    def _update_cluster_center_marker(self) -> None:
        """Render one + marker per GMM-origin ROI at its stored cluster mean.

        Called whenever the ROI list changes (place_gmm_rois, remove,
        dataset reset) and whenever spinbox changes propagate via
        _apply_gmm_geometry. The marker tracks the GMM fit's stored
        ``(mean_g, mean_s)`` — independent of where the user has shifted
        the ROI to.
        """
        spots = []
        for w in self._roi_widgets:
            roi = w.phasor_roi
            if roi.origin != "gmm" or roi.gmm_fit is None or not roi.visible:
                continue
            spots.append({
                "pos": (roi.gmm_fit.mean_g, roi.gmm_fit.mean_s),
                "brush": pg.mkBrush(roi.color),
                "pen": pg.mkPen(roi.color, width=2),
            })
        self._cluster_center_scatter.setData(spots)

    def _update_ref_circle_overlay(self) -> None:
        """Redraw or hide the reference-circle PlotCurveItem.

        Clips circle points to the visible Y range ``[0, 0.7]`` so a
        large radius doesn't smear off-screen pyqtgraph clipping
        artifacts. The filter still applies to all matching pixels in
        the full-resolution path; only the overlay is clipped.
        """
        if self._ref_circle_center is None or self._ref_circle_radius is None:
            self._ref_circle_curve.setVisible(False)
            return
        g_c, s_c = self._ref_circle_center
        r = float(self._ref_circle_radius)
        theta = np.linspace(0, 2 * np.pi, 200)
        gs = g_c + r * np.cos(theta)
        ss = s_c + r * np.sin(theta)
        # Clip to viewport
        in_view = (ss >= 0.0) & (ss <= 0.7)
        if not in_view.any():
            self._ref_circle_curve.setVisible(False)
            return
        self._ref_circle_curve.setData(gs[in_view], ss[in_view])
        self._ref_circle_curve.setVisible(True)

    # ── ROI Management ────────────────────────────────────────

    def get_visible_roi_names(self) -> dict[int, str]:
        """Public API: return {mask_label: roi_name} for all visible ROIs."""
        return {
            w.phasor_roi.label: w.phasor_roi.name
            for w in self._roi_widgets
            if w.phasor_roi.visible
        }

    def _on_add_roi(self) -> None:
        n = len(self._roi_widgets)
        if n >= 10:
            self._status.showMessage("Maximum 10 ROIs", 3000)
            return
        color = COLOR_CYCLE[n % len(COLOR_CYCLE)]
        phasor_roi = PhasorROI(
            name=f"ROI_{n + 1}",
            center=(0.35 + n * 0.05, 0.35),
            radii=(0.10, 0.08),
            angle_deg=0,
            label=n + 1,
            color=color,
        )
        self._create_roi_widget(phasor_roi)
        self._refresh_roi_list()
        self._roi_list.setCurrentRow(len(self._roi_widgets) - 1)
        self._preview_timer.start()

    def _on_remove_roi(self) -> None:
        if self._selected_roi_index is None or not self._roi_widgets:
            return
        widget = self._roi_widgets.pop(self._selected_roi_index)
        removed_name = widget.phasor_roi.name
        self._plot.removeItem(widget.roi)
        self._plot.removeItem(widget.curve)
        for i, w in enumerate(self._roi_widgets):
            w.phasor_roi.label = i + 1
            w.cached_mask = None
        self._selected_roi_index = None
        self._refresh_roi_list()
        self._on_roi_list_selection(self._roi_list.currentRow())
        self._update_cluster_center_marker()
        if not self._roi_widgets:
            self._refresh_histogram()
        # Drop the napari preview layer for the removed ROI before the
        # debounced preview re-emits the survivors.
        self.preview_roi_removed.emit(removed_name)
        self._preview_timer.start()

    def _create_roi_widget(self, phasor_roi: PhasorROI) -> None:
        """Create pyqtgraph ROI + curve for a PhasorROI and add to the list.

        For GMM-origin ROIs the RectROI is non-interactive (no drag, no
        resize handles) — placement is exclusively driven by the four
        axis coefficients in the Selected-ROI panel. Manual ROIs keep
        the standard drag/resize affordances.
        """
        cx, cy = phasor_roi.center
        rx, ry = phasor_roi.radii
        is_gmm = phasor_roi.origin == "gmm"
        roi = pg.RectROI(
            [cx - rx, cy - ry], [2 * rx, 2 * ry],
            pen=pg.mkPen(phasor_roi.color, width=1, style=Qt.DashLine),
        )
        roi.setZValue(10)
        if is_gmm:
            # Disable mouse drag and strip resize handles so the GMM
            # ROI is fully driven by the four axis spinboxes — no manual
            # drag/resize affordances. ``translatable`` is a settable
            # attribute on pyqtgraph's ROI; iterate over a copy of the
            # handles list because removeHandle mutates it in place.
            roi.translatable = False
            for h in list(roi.handles):
                roi.removeHandle(h["item"])
        self._plot.addItem(roi)

        curve = pg.PlotCurveItem(pen=pg.mkPen(phasor_roi.color, width=2))
        curve.setZValue(10)
        self._plot.addItem(curve)

        widget = _ROIWidget(roi=roi, curve=curve, phasor_roi=phasor_roi)
        self._roi_widgets.append(widget)

        # Connect ROI movement — look up widget by identity, not index,
        # so removal/renumbering doesn't break surviving ROIs. (Won't
        # fire for GMM ROIs since they're non-interactive, but the
        # connection is harmless and simplifies the code.)
        roi.sigRegionChangeFinished.connect(
            lambda _roi, _w=widget: self._on_roi_moved_widget(_w)
        )
        self._update_ellipse_curve_for(widget)

    def _refresh_roi_list(self) -> None:
        """Rebuild the QListWidget from current _roi_widgets.

        Each row uses a real Qt checkbox (Qt.ItemIsUserCheckable +
        setCheckState) so toggling visibility is a single click; the
        previous render used a static "✓"/"✗" glyph that looked
        clickable but was just text.
        """
        self._roi_list.blockSignals(True)
        self._roi_list.clear()
        for w in self._roi_widgets:
            item = QListWidgetItem(w.phasor_roi.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if w.phasor_roi.visible else Qt.Unchecked
            )
            self._roi_list.addItem(item)
        self._roi_list.blockSignals(False)
        if self._selected_roi_index is not None and self._selected_roi_index < len(self._roi_widgets):
            self._roi_list.setCurrentRow(self._selected_roi_index)

    def _on_roi_list_item_changed(self, item: QListWidgetItem) -> None:
        """Handle visibility-checkbox toggles from rows in the ROI list."""
        row = self._roi_list.row(item)
        if row < 0 or row >= len(self._roi_widgets):
            return
        checked = item.checkState() == Qt.Checked
        self._set_roi_visibility(row, checked)

    def _on_roi_list_selection(self, row: int) -> None:
        """User selected a different ROI in the list."""
        if row < 0 or row >= len(self._roi_widgets):
            self._selected_roi_index = None
            self._name_edit.blockSignals(True)
            self._name_edit.setText("")
            self._name_edit.blockSignals(False)
            self._angle_spin.blockSignals(True)
            self._angle_spin.setValue(0)
            self._angle_spin.blockSignals(False)
            self._vis_check.blockSignals(True)
            self._vis_check.setChecked(False)
            self._vis_check.blockSignals(False)
            for spin in (
                self._stretch_parallel_spin,
                self._stretch_perpendicular_spin,
                self._shift_parallel_spin,
                self._shift_perpendicular_spin,
            ):
                spin.setEnabled(False)
            self._reset_fit_btn.setEnabled(False)
            self._refresh_selected_roi_highlight()
            return
        self._selected_roi_index = row
        roi = self._roi_widgets[row].phasor_roi
        self._name_edit.blockSignals(True)
        self._name_edit.setText(roi.name)
        self._name_edit.blockSignals(False)
        self._angle_spin.blockSignals(True)
        self._angle_spin.setValue(int(roi.angle_deg))
        self._angle_spin.blockSignals(False)
        self._vis_check.blockSignals(True)
        self._vis_check.setChecked(roi.visible)
        self._vis_check.blockSignals(False)

        # GMM-only controls. Use setEnabled (not setVisible) so the layout
        # stays put when the user switches between manual and GMM ROIs.
        is_gmm = roi.origin == "gmm" and roi.gmm_fit is not None
        for spin in (
            self._stretch_parallel_spin,
            self._stretch_perpendicular_spin,
            self._shift_parallel_spin,
            self._shift_perpendicular_spin,
        ):
            spin.setEnabled(is_gmm)
        self._reset_fit_btn.setEnabled(is_gmm)
        if is_gmm:
            for spin, value in (
                (self._stretch_parallel_spin, roi.gmm_fit.stretch_parallel),
                (self._stretch_perpendicular_spin, roi.gmm_fit.stretch_perpendicular),
                (self._shift_parallel_spin, roi.gmm_fit.shift_parallel),
                (self._shift_perpendicular_spin, roi.gmm_fit.shift_perpendicular),
            ):
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)

        self._refresh_selected_roi_highlight()

    def _on_name_edited(self) -> None:
        if self._selected_roi_index is None:
            return
        new_name = self._name_edit.text().strip()
        if not new_name:
            return
        # Enforce unique names
        existing = {w.phasor_roi.name for i, w in enumerate(self._roi_widgets)
                     if i != self._selected_roi_index}
        if new_name in existing:
            new_name = f"{new_name}_2"
            self._name_edit.setText(new_name)
        roi = self._roi_widgets[self._selected_roi_index].phasor_roi
        old_name = roi.name
        if new_name == old_name:
            return
        roi.name = new_name
        self._refresh_roi_list()
        # Banner reflects the new name immediately
        self._refresh_selected_roi_highlight()
        # Drop the old napari preview layer; the next debounced preview
        # tick will upsert the new name.
        self.preview_roi_removed.emit(old_name)
        self._preview_timer.start()

    def _on_angle_changed(self, value: int) -> None:
        if self._selected_roi_index is None:
            return
        widget = self._roi_widgets[self._selected_roi_index]
        widget.phasor_roi.angle_deg = float(value)
        self._update_ellipse_curve_for(widget)
        widget.cached_mask = None
        self._preview_timer.start()

    def _on_visibility_toggled(self, checked: bool) -> None:
        if self._selected_roi_index is None:
            return
        self._set_roi_visibility(self._selected_roi_index, checked)

    def _set_roi_visibility(self, index: int, checked: bool) -> None:
        """Single source of truth for flipping an ROI's visible flag.

        Called from both the Selected-ROI panel checkbox and the per-row
        check state in the ROI list. Refreshes the list (which keeps the
        two checkboxes in sync) and pokes the preview timer.
        """
        if index < 0 or index >= len(self._roi_widgets):
            return
        roi = self._roi_widgets[index].phasor_roi
        if roi.visible == checked:
            return
        roi.visible = checked
        # Mirror state into the panel checkbox (the click may have come
        # from the list, not the panel).
        if index == self._selected_roi_index:
            self._vis_check.blockSignals(True)
            self._vis_check.setChecked(checked)
            self._vis_check.blockSignals(False)
        self._refresh_roi_list()
        self._update_cluster_center_marker()
        self._preview_timer.start()

    def _on_roi_moved_widget(self, widget: _ROIWidget) -> None:
        """Recompute only the changed ROI's cached mask."""
        if widget not in self._roi_widgets:
            return  # widget was removed
        pos = widget.roi.pos()
        size = widget.roi.size()
        widget.phasor_roi.center = (
            pos.x() + abs(size.x()) / 2,
            pos.y() + abs(size.y()) / 2,
        )
        widget.phasor_roi.radii = (abs(size.x()) / 2, abs(size.y()) / 2)
        self._update_ellipse_curve_for(widget)
        widget.cached_mask = None
        self._preview_timer.start()

    # ── GMM-origin controls (stretch ∥ / ⊥, shift ∥ / ⊥, Reset to fit) ──

    def _apply_gmm_geometry(self, widget: _ROIWidget) -> None:
        """Recompute geometry from ``gmm_fit`` and push to the RectROI.

        Always recomputes from the cluster mean using the four stored
        axis coefficients — there is no anchor mode. GMM ROIs are
        non-draggable, so the spinboxes are the single source of truth.

        ``RectROI.setPos`` / ``setSize`` are wrapped in ``blockSignals``
        so the programmatic update does not feed back through
        ``_on_roi_moved_widget`` and round-trip the eigenstructure-
        derived values through the RectROI bbox quantization.
        """
        from percell4.domain.flim.phasor import gmm_to_phasor_roi_geometry

        fit = widget.phasor_roi.gmm_fit
        if fit is None:
            return

        center, radii, angle_deg = gmm_to_phasor_roi_geometry(
            mean=(fit.mean_g, fit.mean_s),
            lambda_major=fit.lambda_major,
            lambda_minor=fit.lambda_minor,
            principal_angle_rad=fit.principal_angle_rad,
            stretch_parallel=fit.stretch_parallel,
            stretch_perpendicular=fit.stretch_perpendicular,
            shift_parallel=fit.shift_parallel,
            shift_perpendicular=fit.shift_perpendicular,
            shape=fit.shape,
        )
        widget.phasor_roi.center = center
        widget.phasor_roi.radii = radii
        widget.phasor_roi.angle_deg = angle_deg

        cx, cy = center
        rx, ry = radii
        widget.roi.blockSignals(True)
        try:
            widget.roi.setPos((cx - rx, cy - ry))
            widget.roi.setSize((2 * rx, 2 * ry))
        finally:
            widget.roi.blockSignals(False)

        # Angle spinbox follows the GMM-derived value (may differ between
        # circle=0 and ellipse=θ shapes); keep it in sync but don't fire
        # _on_angle_changed.
        self._angle_spin.blockSignals(True)
        self._angle_spin.setValue(int(round(angle_deg)))
        self._angle_spin.blockSignals(False)

        self._update_ellipse_curve_for(widget)
        self._update_cluster_center_marker()
        widget.cached_mask = None
        self._preview_timer.start()

    def _on_gmm_param_changed(self, _value: float) -> None:
        """Single slot for all four GMM-axis spinboxes.

        Reads the four current spinbox values into ``gmm_fit`` then
        recomputes the ROI geometry from the stored eigenstructure +
        cluster mean. Ignores changes when no GMM ROI is selected.
        """
        if self._selected_roi_index is None:
            return
        widget = self._roi_widgets[self._selected_roi_index]
        if widget.phasor_roi.gmm_fit is None:
            return
        fit = widget.phasor_roi.gmm_fit
        fit.stretch_parallel = float(self._stretch_parallel_spin.value())
        fit.stretch_perpendicular = float(self._stretch_perpendicular_spin.value())
        fit.shift_parallel = float(self._shift_parallel_spin.value())
        fit.shift_perpendicular = float(self._shift_perpendicular_spin.value())
        self._apply_gmm_geometry(widget)

    def _on_reset_fit_clicked(self) -> None:
        """Reset all four axis coefficients to defaults (stretch=2.0, shift=0)."""
        if self._selected_roi_index is None:
            return
        widget = self._roi_widgets[self._selected_roi_index]
        if widget.phasor_roi.gmm_fit is None:
            return
        fit = widget.phasor_roi.gmm_fit
        fit.stretch_parallel = 2.0
        fit.stretch_perpendicular = 2.0
        fit.shift_parallel = 0.0
        fit.shift_perpendicular = 0.0
        # Update spinboxes without firing _on_gmm_param_changed for each
        for spin, value in (
            (self._stretch_parallel_spin, 2.0),
            (self._stretch_perpendicular_spin, 2.0),
            (self._shift_parallel_spin, 0.0),
            (self._shift_perpendicular_spin, 0.0),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self._apply_gmm_geometry(widget)

    # ── Unique-name helper ────────────────────────────────────

    def _make_unique_name(self, base: str) -> str:
        """Return a name unused by any existing ROI, appending _2/_3/... if needed."""
        existing = {w.phasor_roi.name for w in self._roi_widgets}
        if base not in existing:
            return base
        i = 2
        while f"{base}_{i}" in existing:
            i += 1
        return f"{base}_{i}"

    # ── Ellipse drawing ───────────────────────────────────────

    def _update_ellipse_curve_for(self, widget: _ROIWidget) -> None:
        """Redraw the ellipse curve for a specific ROI widget."""
        roi = widget.phasor_roi
        cx, cy = roi.center
        rx, ry = roi.radii
        angle_rad = np.radians(roi.angle_deg)

        if rx < 1e-6 or ry < 1e-6:
            widget.curve.setData([], [])
            return

        theta = np.linspace(0, 2 * np.pi, 200)
        ex = rx * np.cos(theta)
        ey = ry * np.sin(theta)
        cos_a = np.cos(angle_rad)
        sin_a = np.sin(angle_rad)
        ex_rot = cx + ex * cos_a - ey * sin_a
        ey_rot = cy + ex * sin_a + ey * cos_a
        widget.curve.setData(ex_rot, ey_rot)

    # ── Combined mask ─────────────────────────────────────────

    def _get_active_gs_maps(self) -> tuple[np.ndarray, np.ndarray]:
        """Return filtered or unfiltered G/S maps based on checkbox."""
        use_filtered = self._filtered_check.isChecked()
        if not use_filtered and self._g_map_unfiltered is not None:
            return self._g_map_unfiltered, self._s_map_unfiltered
        return self._g_map, self._s_map

    def _compute_visible_valid_2d(self) -> np.ndarray | None:
        """Return the 2D boolean mask of pixels visible in the histogram.

        AND-composes every filter that the visible phasor histogram
        applies (validity, cell selection, active mask, intensity
        threshold, reference circle, manual cleared regions) so callers
        can intersect it with a per-ROI membership mask to produce
        "literally what is visible" as a binary mask.
        """
        if self._g_map is None or self._s_map is None:
            return None

        g, s = self._get_active_gs_maps()
        mask_flat = self._load_active_mask_flat()
        intensity_flat = (
            self._intensity.ravel() if self._intensity is not None else None
        )

        cleared_flat: np.ndarray | None = None
        if self._cleared_mask is not None:
            if self._cleared_mask.size == g.size:
                cleared_flat = self._cleared_mask.ravel()
            else:
                # Defense-in-depth: set_phasor_data resets _cleared_mask
                # whenever a new (g, s) frame lands, so a size mismatch
                # here means a code path mutated G/S without going through
                # set_phasor_data. Bypass the filter and surface a sticky
                # status message — the 8-second timeout outlives the
                # per-ROI count messages that overwrite the status bar
                # on every refresh.
                self._status.showMessage(
                    "Cleared mask shape mismatch — Clear-within-ROI filter not applied",
                    8000,
                )

        valid_flat = compute_valid_phasor_pixels(
            g.ravel(), s.ravel(),
            labels_flat=self._labels_flat,
            filter_ids=self._session.filter_ids,
            mask_flat=mask_flat,
            intensity_flat=intensity_flat,
            intensity_threshold=self._intensity_threshold,
            ref_circle_center=self._ref_circle_center,
            ref_circle_radius=self._ref_circle_radius,
            cleared_mask_flat=cleared_flat,
        )
        return valid_flat.reshape(g.shape)

    def _apply_clear_to_roi(self, widget: _ROIWidget) -> bool:
        """OR a ROI's inside-mask into ``_cleared_mask``.

        Returns True if any pixels were cleared, False if the ROI's
        inside-mask is empty (e.g. the ellipse fell entirely on NaN
        pixels). The False return signals to the caller that the ROI
        should NOT be consumed — there's nothing to show for the
        operation.

        Lazy-allocates ``_cleared_mask`` on first non-empty Clear, so
        the invariant ``_cleared_mask is not None ⇒ at least one pixel
        cleared`` holds. That invariant is what lets the Reset button's
        enable rule be a cheap ``is not None`` check instead of an
        O(H*W) ``.any()`` reduction.
        """
        from percell4.domain.flim.phasor import phasor_roi_to_mask

        g, s = self._get_active_gs_maps()
        if g is None or s is None:
            return False

        roi = widget.phasor_roi
        roi_inside = phasor_roi_to_mask(
            g, s, center=roi.center, radii=roi.radii,
            angle_rad=np.radians(roi.angle_deg),
        )

        if not roi_inside.any():
            self._status.showMessage(
                "ROI has no pixels in the active region — nothing to clear",
                4000,
            )
            return False

        if self._cleared_mask is None:
            self._cleared_mask = np.zeros(g.shape, dtype=bool)
        elif self._cleared_mask.shape != g.shape:
            # Defensive: should not happen because set_phasor_data resets
            # the bitmap whenever the frame changes. If it does, reset
            # cleanly rather than crashing.
            self._status.showMessage(
                "Cleared mask shape mismatch — resetting before Clear",
                8000,
            )
            self._cleared_mask = np.zeros(g.shape, dtype=bool)

        self._cleared_mask |= roi_inside
        return True

    def _reset_cleared_mask(self) -> None:
        """Discard all cleared pixels; refresh synchronously.

        Synchronous (no debounce) because Reset is a discrete user
        action — there's no slider-drag scenario that benefits from
        coalescing.
        """
        if self._cleared_mask is None:
            return
        self._cleared_mask = None
        self._refresh_histogram()
        self._update_preview()

    def _compute_filtered_binary(self, widget: _ROIWidget) -> np.ndarray:
        """Build the (H, W) uint8 binary mask for one ROI.

        Result equals (ROI membership) AND (every filter the visible
        histogram applies), so the preview and the saved binary mask
        match the rendered phasor pixel-for-pixel.
        """
        from percell4.domain.flim.phasor import phasor_roi_to_mask

        g, s = self._get_active_gs_maps()
        if widget.cached_mask is None:
            roi = widget.phasor_roi
            widget.cached_mask = phasor_roi_to_mask(
                g, s, center=roi.center, radii=roi.radii,
                angle_rad=np.radians(roi.angle_deg),
            )

        visible = self._compute_visible_valid_2d()
        binary = np.zeros(g.shape, dtype=np.uint8)
        keep = widget.cached_mask & visible if visible is not None else widget.cached_mask
        binary[keep] = 1
        return binary

    def _update_preview(self) -> None:
        """Emit one preview_roi_upserted per ROI; update status with counts."""
        if self._g_map is None or not self._roi_widgets:
            return

        total = self._total_valid_pixels or 1
        parts: list[str] = []
        for widget in self._roi_widgets:
            roi = widget.phasor_roi
            binary = self._compute_filtered_binary(widget)
            self.preview_roi_upserted.emit(roi.name, binary, roi.color, roi.visible)
            if roi.visible:
                count = int(binary.sum())
                parts.append(f"{roi.name}: {count:,} ({count / total * 100:.1f}%)")

        if parts:
            self._status.showMessage(" | ".join(parts))

    # ── Data ──────────────────────────────────────────────────

    def set_phasor_data(
        self,
        g_map: np.ndarray,
        s_map: np.ndarray,
        intensity: np.ndarray | None = None,
        g_unfiltered: np.ndarray | None = None,
        s_unfiltered: np.ndarray | None = None,
        labels: np.ndarray | None = None,
    ) -> None:
        """Set phasor data and refresh the histogram."""
        self._g_map = g_map
        self._s_map = s_map
        self._intensity = intensity
        self._labels = labels
        self._labels_flat = labels.ravel() if labels is not None else None
        self._total_valid_pixels = int(
            (np.isfinite(g_map) & (g_map != 0)).sum()
        )

        # Invalidate all ROI mask caches
        for w in self._roi_widgets:
            w.cached_mask = None

        # Invalidate active-mask filter cache. Each compute_phasor call
        # produces a new (g, s) frame; the cached mask flat may have
        # been loaded against an earlier frame whose spatial alignment
        # differs even when shapes match (rotation/flip applied to
        # /decay between computes, channel switch, dataset switch).
        # Forcing a re-read on next refresh keeps mask alignment correct.
        # Same rationale applies to the Clear-within-ROI bitmap below:
        # cleared pixels are bound to the frame they were drawn against.
        self._cleared_mask = None
        self._active_mask_array = None
        self._active_mask_flat = None

        if g_unfiltered is not None:
            self._g_map_unfiltered = g_unfiltered
            self._s_map_unfiltered = s_unfiltered
            self._filtered_check.setEnabled(True)
            self._filtered_check.setChecked(True)
        else:
            self._g_map_unfiltered = None
            self._s_map_unfiltered = None
            self._filtered_check.setEnabled(False)
            self._filtered_check.setChecked(False)

        self._refresh_histogram()

    def _on_filtered_toggled(self, checked: bool) -> None:
        # Invalidate all caches when switching filtered/unfiltered
        for w in self._roi_widgets:
            w.cached_mask = None
        self._refresh_histogram()

    def _on_filter_changed(self) -> None:
        """Handle filter changes — debounced histogram refresh."""
        self._filter_timer.start()

    def _on_dataset_changed(self) -> None:
        """Reset per-dataset caches and ROIs when the active dataset switches.

        ROIs are spatial regions on a specific dataset's phasor coordinate
        space; carrying them across loads has no user-validated semantics
        (per the dataset-lifecycle plan's OQ-1). All per-dataset caches
        invalidate; checkboxes reset; the histogram re-derives from the
        next compute_phasor call.
        """
        # Tear down ROI graphics + every napari preview layer
        for widget in list(self._roi_widgets):
            self._plot.removeItem(widget.roi)
            self._plot.removeItem(widget.curve)
        self._roi_widgets.clear()
        self._selected_roi_index = None
        self.preview_all_cleared.emit()
        self._refresh_roi_list()
        self._on_roi_list_selection(-1)  # clears Selected ROI panel widgets
        self._update_cluster_center_marker()

        # Invalidate per-dataset coordinate maps and intensity caches
        self._g_map = None
        self._s_map = None
        self._g_map_unfiltered = None
        self._s_map_unfiltered = None
        self._intensity = None
        self._labels = None
        self._labels_flat = None
        self._total_valid_pixels = 0
        self._active_mask_array = None
        self._active_mask_flat = None

        # Reset FlimPanel-driven filter state — values were tied to the
        # previous dataset's metadata (frequency for ref-circle).
        self._intensity_threshold = 0.0
        self._ref_circle_tau_ns = None
        self._ref_circle_radius = None
        self._ref_circle_center = None
        self._update_ref_circle_overlay()

        # Reset checkbox states. _on_active_mask_changed will re-enable
        # the mask-filter checkbox if the new dataset auto-selected a mask.
        self._filtered_check.blockSignals(True)
        self._filtered_check.setChecked(False)
        self._filtered_check.setEnabled(False)
        self._filtered_check.blockSignals(False)
        self._mask_filter_check.blockSignals(True)
        self._mask_filter_check.setChecked(False)
        self._mask_filter_check.blockSignals(False)
        self._mask_filter_check.setEnabled(False)

        # Clear the histogram and reset the status bar to the no-data state
        if self._hist_item is not None:
            self._plot.removeItem(self._hist_item)
            self._hist_item = None
        self._status.showMessage("No phasor computed")

        # Re-derive checkbox state from current session.active_mask. When
        # both the old and new dataset have the same mask name (e.g.,
        # "SG_mask" in both), Session.set_dataset suppresses the
        # ACTIVE_MASK_CHANGED event because prev_mask == new_mask. But the
        # underlying mask data is from a different dataset, so the
        # checkbox must still re-enable.
        self._on_active_mask_changed()

    def _on_active_mask_changed(self) -> None:
        """Update mask-filter checkbox enabled state when active_mask flips.

        Drops any cached mask array so the next refresh re-loads from the
        repo. Does not auto-engage the filter — the user must opt in via
        the checkbox to avoid the feedback loop with phasor ROI's
        "Apply Visible as Mask" (which itself sets active_mask).
        """
        active = self._session.active_mask
        self._active_mask_array = None
        self._active_mask_flat = None
        if not active:
            self._mask_filter_check.blockSignals(True)
            self._mask_filter_check.setChecked(False)
            self._mask_filter_check.blockSignals(False)
            self._mask_filter_check.setEnabled(False)
        else:
            self._mask_filter_check.setEnabled(True)
        # If the filter is currently engaged, refresh against the new mask
        if self._mask_filter_check.isChecked():
            self._refresh_histogram()

    def _on_mask_filter_toggled(self, checked: bool) -> None:
        """User toggled the 'Filter by active mask' checkbox."""
        # Invalidate ROI caches so the combined mask reflects the new filter
        for w in self._roi_widgets:
            w.cached_mask = None
        self._refresh_histogram()
        self._preview_timer.start()

    def _load_active_mask_flat(self) -> np.ndarray | None:
        """Load and cache the active mask as a flat array, or return None.

        Reads /masks/<active_mask> from the repo on demand. Returns None
        when there is no active mask, no repo, no dataset, the mask
        cannot be read, or the mask shape does not match the phasor
        maps. Caches the loaded array so repeated histogram refreshes
        don't re-read HDF5.
        """
        if not self._mask_filter_check.isChecked():
            return None
        if self._g_map is None:
            return None

        if self._active_mask_flat is not None:
            return self._active_mask_flat

        active = self._session.active_mask
        if not active or self._get_repo is None:
            return None
        handle = self._session.dataset
        if handle is None:
            return None

        try:
            repo = self._get_repo()
            mask = repo.read_mask(handle, active)
        except (KeyError, OSError, ValueError):
            return None

        if not mask_shape_matches(mask, self._g_map):
            self._active_mask_array = None
            self._active_mask_flat = None
            return None

        self._active_mask_array = mask
        self._active_mask_flat = mask.ravel()
        return self._active_mask_flat

    def _refresh_histogram(self) -> None:
        """Render intensity-weighted 2D histogram."""
        if self._g_map is None or self._s_map is None:
            return

        g_display, s_display = self._get_active_gs_maps()
        g_flat_full = g_display.ravel()
        s_flat_full = s_display.ravel()

        # Surface a status message when the mask is configured but
        # bypassed (shape mismatch / read failure). Computed inline
        # because _compute_visible_valid_2d does not expose this.
        mask_bypassed = (
            self._mask_filter_check.isChecked()
            and self._session.active_mask
            and self._load_active_mask_flat() is None
            and self._g_map is not None
        )

        # Delegate the AND filter chain to _compute_visible_valid_2d so
        # this render path picks up every filter (cell selection, active
        # mask, intensity, ref circle, cleared mask) without duplicating
        # the call. Single integration point — mirrors the per-ROI Apply
        # path in _compute_filtered_binary.
        valid_2d = self._compute_visible_valid_2d()
        if valid_2d is None:
            return
        valid = valid_2d.ravel()

        g_flat = g_flat_full[valid]
        s_flat = s_flat_full[valid]

        if len(g_flat) == 0:
            if mask_bypassed:
                self._status.showMessage(
                    "No valid phasor data — mask shape mismatch, filter not applied"
                )
            else:
                self._status.showMessage("No valid phasor data")
            return

        if self._intensity is not None:
            weights = self._intensity.ravel()[valid]
        else:
            weights = np.ones(len(g_flat))

        g_range = (-0.005, 1.005)
        s_range = (0.0, 0.7)

        hist, g_edges, s_edges = np.histogram2d(
            g_flat, s_flat,
            bins=300,
            range=[g_range, s_range],
            weights=weights,
        )

        hist_display = np.log1p(hist)

        if self._hist_item is not None:
            self._plot.removeItem(self._hist_item)

        self._hist_item = pg.ImageItem()
        self._plot.addItem(self._hist_item)

        cmap = pg.colormap.get("nipy_spectral", source="matplotlib")
        if cmap is None:
            cmap = pg.colormap.get("viridis")
        self._hist_item.setImage(hist_display)
        self._hist_item.setColorMap(cmap)

        self._hist_item.setRect(
            QRectF(g_range[0], s_range[0],
                   g_range[1] - g_range[0],
                   s_range[1] - s_range[0])
        )

        self._semicircle.setZValue(10)
        for w in self._roi_widgets:
            w.roi.setZValue(10)
            w.curve.setZValue(10)

        self._plot.setXRange(*g_range, padding=0)
        self._plot.setYRange(*s_range, padding=0)
        self._plot.getAxis("bottom").enableAutoSIPrefix(False)
        self._plot.getAxis("left").enableAutoSIPrefix(False)

        n_pixels = len(g_flat)
        if mask_bypassed:
            self._status.showMessage(
                f"Phasor: {n_pixels:,} valid pixels — mask shape mismatch, filter not applied"
            )
        else:
            self._status.showMessage(f"Phasor: {n_pixels:,} valid pixels")

    # ── Apply mask ────────────────────────────────────────────

    def _on_apply_mask(self) -> None:
        """Emit mask_applied with per-ROI binary masks.

        Each visible ROI becomes its own binary mask whose pixels are
        exactly those visible in the current phasor histogram and inside
        the ROI — i.e. the same intersection the napari preview shows.
        The launcher saves each to HDF5 and adds each as a napari layer.
        """
        if self._g_map is None or not self._roi_widgets:
            self._status.showMessage("No phasor data or ROIs", 3000)
            return

        roi_masks: list[tuple[str, np.ndarray, str]] = []
        for w in self._roi_widgets:
            if not w.phasor_roi.visible:
                continue
            binary = self._compute_filtered_binary(w)
            roi_masks.append((w.phasor_roi.name, binary, w.phasor_roi.color))

        if not roi_masks:
            self._status.showMessage("No visible ROIs to apply", 3000)
            return

        self.mask_applied.emit(roi_masks)
        names = ", ".join(name for name, _, _ in roi_masks)
        self._status.showMessage(f"Applied {len(roi_masks)} mask(s): {names}", 5000)

    # ── Save plot as SVG ──────────────────────────────────────

    def _on_save_svg(self) -> None:
        """Save the current phasor plot as a vector SVG.

        Uses pyqtgraph's SVGExporter so every histogram cell, ROI ellipse,
        ROI handle, axis tick, and label is a separate, editable vector
        object. Open in Illustrator / Inkscape / Affinity to delete the
        rectangular ROI bounding handles for figure-ready output.
        """
        if self._g_map is None:
            self._status.showMessage("No phasor data to save", 3000)
            return

        default_name = "phasor.svg"
        handle = self._session.dataset
        if handle is not None:
            stem = handle.path.stem
            channel = self._session.active_channel
            default_name = f"{stem}_{channel}_phasor.svg" if channel else f"{stem}_phasor.svg"

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Phasor SVG", default_name, "SVG (*.svg)"
        )
        if not path:
            return
        if not path.lower().endswith(".svg"):
            path = f"{path}.svg"

        from pyqtgraph.exporters import SVGExporter

        try:
            exporter = SVGExporter(self._plot.plotItem)
            exporter.export(path)
        except Exception as e:
            QMessageBox.warning(
                self, "Save Error", f"Failed to save phasor SVG to:\n{path}\n\n{e}"
            )
            return
        self._status.showMessage(f"Saved phasor SVG: {path}", 4000)

    # ── Save / Load ROIs ──────────────────────────────────────

    def _on_save_rois(self) -> None:
        if not self._roi_widgets:
            self._status.showMessage("No ROIs to save", 3000)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save ROIs", "", "JSON Files (*.json)"
        )
        if not path:
            return
        data = {
            "schema_version": ROI_JSON_SCHEMA_VERSION,
            "rois": [w.phasor_roi.to_dict() for w in self._roi_widgets],
        }
        Path(path).write_text(json.dumps(data, indent=2))
        self._status.showMessage(f"Saved {len(self._roi_widgets)} ROIs", 3000)

    def _on_load_rois(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load ROIs", "", "JSON Files (*.json)"
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text())
            rois_data = data["rois"]
            if not isinstance(rois_data, list):
                raise ValueError("'rois' must be a list")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            QMessageBox.warning(self, "Load Error", f"Invalid ROI file:\n{e}")
            return

        # Schema-version warning. v1 (no field) and v2 load fully; v>2 may
        # carry fields this build doesn't know about — warn the user that
        # those fields will be lost on the next Save.
        loaded_version = int(data.get("schema_version", 1))
        if loaded_version > ROI_JSON_SCHEMA_VERSION:
            QMessageBox.information(
                self, "Newer ROI file",
                f"This ROI file was written with schema_version={loaded_version}; "
                f"this build understands up to {ROI_JSON_SCHEMA_VERSION}. "
                "Some fields may be lost if you save it again.",
            )

        # Clear existing ROIs (and any napari preview layers from them)
        for w in self._roi_widgets:
            self._plot.removeItem(w.roi)
            self._plot.removeItem(w.curve)
        self._roi_widgets.clear()
        self.preview_all_cleared.emit()

        # Create from JSON — labels derived from position
        for i, roi_data in enumerate(rois_data):
            try:
                phasor_roi = PhasorROI.from_dict(
                    roi_data,
                    label=i + 1,
                    default_color=COLOR_CYCLE[i % len(COLOR_CYCLE)],
                )
            except ValueError as e:
                QMessageBox.warning(self, "Load Error", f"ROI {i}: {e}")
                continue
            self._create_roi_widget(phasor_roi)

        self._selected_roi_index = None
        self._refresh_roi_list()
        if self._roi_widgets:
            self._roi_list.setCurrentRow(0)
        self._preview_timer.start()
        self._status.showMessage(f"Loaded {len(self._roi_widgets)} ROIs", 3000)

    # ── Lifecycle ─────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        # Stop debounced timers before unsubscribing so a queued slot
        # doesn't fire after teardown (origin: napari-modal-tool-overlay
        # pattern). Both timers are single-shot, so .stop() is safe even
        # when no callback is pending.
        for timer in (self._filter_timer, self._preview_timer):
            try:
                timer.stop()
            except RuntimeError:
                pass  # timer already destroyed
        for unsub in getattr(self, '_unsubs', []):
            try:
                unsub()
            except ValueError:
                pass  # already unsubscribed
        # Phasor preview layers belong to the phasor window — hide the
        # window, hide the previews. Reopening the window re-emits via
        # showEvent → _preview_timer.
        self.preview_all_cleared.emit()
        self._save_geometry()
        self.hide()
        event.ignore()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Auto-load cached phasor for the active channel if no compute is
        # in flight. Guard with `_g_map is None` so an in-progress compute
        # is not clobbered (the existing FlimPanel path writes _g_map via
        # set_phasor_data once the compute finishes; this guard keeps the
        # auto-load from racing against it).
        if self._g_map is None:
            self._try_auto_load_cached()
        # Re-render preview layers when the window is reopened, since
        # closeEvent cleared them.
        if self._roi_widgets and self._g_map is not None:
            self._preview_timer.start()

    def _on_active_channel_changed(self) -> None:
        """Hydrate the phasor window when the user switches to a new channel.

        Per Decision #15 (planning review): switching to an uncached
        channel clears the histogram (consistent with per-channel
        caching — the user expects to see the new channel's data, not
        the previous one).
        """
        if not self.isVisible():
            return
        self._try_auto_load_cached()

    def _try_auto_load_cached(self) -> None:
        """Read /phasor/<active_channel> via LoadCachedPhasor; populate window if cached.

        Reads the active channel from ``self._session.active_channel``
        (current session truth — matches the existing subscription
        pattern, NOT the event payload, per Decision #9).

        On NoCachedPhasorError: clears any prior channel's display so
        switching to an uncached channel doesn't leave stale data
        showing (Decision #15).
        """
        from percell4.application.use_cases.load_cached_phasor import (
            LoadCachedPhasor,
        )
        from percell4.domain.errors import NoCachedPhasorError, NoDatasetError

        if self._get_repo is None or self._session.dataset is None:
            return
        active_channel = self._session.active_channel
        if not active_channel:
            return

        try:
            cached = LoadCachedPhasor(self._get_repo(), self._session).execute(
                active_channel,
            )
        except (NoCachedPhasorError, NoDatasetError):
            # Clear prior channel's display so the user sees the new
            # channel's empty state, not stale data from a different
            # channel. _on_dataset_changed already does this for the
            # dataset case; the channel-switch case is handled here.
            if self._g_map is not None:
                self._clear_phasor_display()
            return

        # Cache hit — choose call shape based on whether wavelet is cached.
        if cached.g_filtered is not None and cached.s_filtered is not None:
            # Mirror apply_wavelet's call: filtered as displayed g/s,
            # raw as the unfiltered toggle source.
            self.set_phasor_data(
                cached.g_filtered, cached.s_filtered,
                intensity=cached.intensity,
                g_unfiltered=cached.g_map, s_unfiltered=cached.s_map,
                labels=None,  # cell filter degraded; user re-clicks Compute to re-engage
            )
        else:
            # Mirror compute_phasor's call: raw only, no unfiltered counterpart.
            self.set_phasor_data(
                cached.g_map, cached.s_map,
                intensity=cached.intensity,
                labels=None,
            )
        self._status.showMessage(
            f"Auto-loaded cached phasor (channel: {active_channel})"
        )

    def _clear_phasor_display(self) -> None:
        """Clear the displayed phasor data without touching ROIs or signals.

        Used when switching to an uncached channel so the previous
        channel's histogram doesn't linger. Mirrors the relevant subset
        of _on_dataset_changed without the ROI teardown.
        """
        self._g_map = None
        self._s_map = None
        self._g_map_unfiltered = None
        self._s_map_unfiltered = None
        self._intensity = None
        self._labels = None
        self._labels_flat = None
        self._total_valid_pixels = 0
        if self._hist_item is not None:
            self._plot.removeItem(self._hist_item)
            self._hist_item = None
        self._status.showMessage("No phasor cached for this channel")

    def _save_geometry(self) -> None:
        QSettings("LeeLabPerCell4", "PerCell4").setValue(
            "phasor_plot/geometry", self.saveGeometry()
        )

    def _restore_geometry(self) -> None:
        geom = QSettings("LeeLabPerCell4", "PerCell4").value("phasor_plot/geometry")
        if geom:
            self.restoreGeometry(geom)
