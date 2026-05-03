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
#   v2 = adds top-level schema_version + per-ROI origin / gmm_fit
ROI_JSON_SCHEMA_VERSION: Final[int] = 2


@dataclass
class GMMFit:
    """Eigenstructure + spinbox state for a GMM-origin PhasorROI.

    The ``mean_*`` and ``lambda_*`` fields are constants captured at fit
    time; ``cov_f`` and ``shift`` are the current spinbox values. The
    drag-preserving recompute path uses ``phasor_roi.center`` as the
    anchor (not ``mean_g``/``mean_s``) so a manual drag survives a
    cov_f / shift edit. The "Reset to fit" button is the explicit
    affordance for snapping the center back to the cluster mean.
    """

    mean_g: float
    mean_s: float
    lambda_major: float
    lambda_minor: float
    principal_angle_rad: float
    cov_f: float
    shift: float
    shape: str  # "circle" | "ellipse"
    criterion: str | None  # "BIC" | "AIC" | None (when n was manual)
    sampled_pixels: int  # shared across all ROIs from one GMM run

    @classmethod
    def from_dict(cls, d: dict) -> GMMFit:
        """Tolerant load of a GMMFit JSON sub-dict.

        Raises ``ValueError`` on missing or wrongly-typed required fields
        so the caller (``PhasorROI.from_dict``) can demote a malformed
        ``gmm_fit`` to ``None`` and keep the rest of the ROI loadable.
        """
        try:
            return cls(
                mean_g=float(d["mean_g"]),
                mean_s=float(d["mean_s"]),
                lambda_major=float(d["lambda_major"]),
                lambda_minor=float(d["lambda_minor"]),
                principal_angle_rad=float(d["principal_angle_rad"]),
                cov_f=float(d["cov_f"]),
                shift=float(d["shift"]),
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
            "cov_f": self.cov_f,
            "shift": self.shift,
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
    - preview_mask_ready: emitted when ROI preview mask needs display
    - mask_applied: emitted when user clicks "Apply Visible as Mask"
    The launcher connects these to mediate viewer + HDF5 access.
    """

    # (mask_ndarray, DirectLabelColormap) — for live ROI preview in viewer
    preview_mask_ready = Signal(object, object)
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
        self._colormap_dirty: bool = True
        self._preview_colormap = None

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

        self._save_png_btn = QPushButton("Save Phasor .PNG")
        self._save_png_btn.setToolTip(
            "Save the current phasor plot as a PNG image."
        )
        self._save_png_btn.clicked.connect(self._on_save_png)
        controls.addWidget(self._save_png_btn)

        left_layout.addLayout(controls)

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

        # ROI list
        self._roi_list = QListWidget()
        self._roi_list.currentRowChanged.connect(self._on_roi_list_selection)
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

        # cov_f / shift / Reset to fit — only meaningful for GMM-origin ROIs.
        # Use ``setEnabled`` (not ``setVisible``) so the layout stays stable
        # when the user switches between manual and GMM ROIs.
        cov_row = QHBoxLayout()
        cov_row.addWidget(QLabel("cov_f:"))
        self._cov_f_spin = QDoubleSpinBox()
        self._cov_f_spin.setRange(0.5, 5.0)
        self._cov_f_spin.setSingleStep(0.1)
        self._cov_f_spin.setDecimals(1)
        self._cov_f_spin.setValue(2.0)
        self._cov_f_spin.setEnabled(False)
        self._cov_f_spin.valueChanged.connect(self._on_cov_f_changed)
        cov_row.addWidget(self._cov_f_spin)
        sel_layout.addLayout(cov_row)

        shift_row = QHBoxLayout()
        shift_row.addWidget(QLabel("Shift:"))
        self._shift_spin = QDoubleSpinBox()
        self._shift_spin.setRange(-2.0, 2.0)
        self._shift_spin.setSingleStep(0.1)
        self._shift_spin.setDecimals(1)
        self._shift_spin.setValue(0.0)
        self._shift_spin.setEnabled(False)
        self._shift_spin.valueChanged.connect(self._on_shift_changed)
        shift_row.addWidget(self._shift_spin)
        sel_layout.addLayout(shift_row)

        self._reset_fit_btn = QPushButton("Reset to fit")
        self._reset_fit_btn.setToolTip(
            "Snap the ROI back to the cluster mean. Drag-preserving cov_f / shift "
            "edits keep the user's drag position; this button explicitly returns to "
            "the GMM fit's center."
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
                cov_f=2.0, shift=0.0,
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

        self._colormap_dirty = True
        self._refresh_roi_list()

        n_placed = len(truncated)
        msg = (
            f"GMM placed {n_placed} ROIs "
            f"(criterion={criterion or 'manual'}, sampled {sampled_pixels:,} pixels)"
        )
        if n_dropped > 0:
            msg += f" — truncated {n_dropped} due to 10-ROI cap"
        self._status.showMessage(msg, 0)
        self._preview_timer.start()

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
        self._colormap_dirty = True
        self._refresh_roi_list()
        self._roi_list.setCurrentRow(len(self._roi_widgets) - 1)
        self._preview_timer.start()

    def _on_remove_roi(self) -> None:
        if self._selected_roi_index is None or not self._roi_widgets:
            return
        widget = self._roi_widgets.pop(self._selected_roi_index)
        self._plot.removeItem(widget.roi)
        self._plot.removeItem(widget.curve)
        for i, w in enumerate(self._roi_widgets):
            w.phasor_roi.label = i + 1
            w.cached_mask = None
        self._selected_roi_index = None
        self._colormap_dirty = True
        self._refresh_roi_list()
        self._on_roi_list_selection(self._roi_list.currentRow())
        if not self._roi_widgets:
            self._refresh_histogram()
        self._preview_timer.start()

    def _create_roi_widget(self, phasor_roi: PhasorROI) -> None:
        """Create pyqtgraph ROI + curve for a PhasorROI and add to the list."""
        cx, cy = phasor_roi.center
        rx, ry = phasor_roi.radii
        roi = pg.RectROI(
            [cx - rx, cy - ry], [2 * rx, 2 * ry],
            pen=pg.mkPen(phasor_roi.color, width=1, style=Qt.DashLine),
        )
        roi.setZValue(10)
        self._plot.addItem(roi)

        curve = pg.PlotCurveItem(pen=pg.mkPen(phasor_roi.color, width=2))
        curve.setZValue(10)
        self._plot.addItem(curve)

        widget = _ROIWidget(roi=roi, curve=curve, phasor_roi=phasor_roi)
        self._roi_widgets.append(widget)

        # Connect ROI movement — look up widget by identity, not index,
        # so removal/renumbering doesn't break surviving ROIs
        roi.sigRegionChangeFinished.connect(
            lambda _roi, _w=widget: self._on_roi_moved_widget(_w)
        )
        self._update_ellipse_curve_for(widget)

    def _refresh_roi_list(self) -> None:
        """Rebuild the QListWidget from current _roi_widgets."""
        self._roi_list.blockSignals(True)
        self._roi_list.clear()
        for w in self._roi_widgets:
            vis = "✓" if w.phasor_roi.visible else "✗"
            item = QListWidgetItem(f"[{vis}] {w.phasor_roi.name}")
            self._roi_list.addItem(item)
        self._roi_list.blockSignals(False)
        if self._selected_roi_index is not None and self._selected_roi_index < len(self._roi_widgets):
            self._roi_list.setCurrentRow(self._selected_roi_index)

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
            self._cov_f_spin.setEnabled(False)
            self._shift_spin.setEnabled(False)
            self._reset_fit_btn.setEnabled(False)
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
        self._cov_f_spin.setEnabled(is_gmm)
        self._shift_spin.setEnabled(is_gmm)
        self._reset_fit_btn.setEnabled(is_gmm)
        if is_gmm:
            self._cov_f_spin.blockSignals(True)
            self._cov_f_spin.setValue(roi.gmm_fit.cov_f)
            self._cov_f_spin.blockSignals(False)
            self._shift_spin.blockSignals(True)
            self._shift_spin.setValue(roi.gmm_fit.shift)
            self._shift_spin.blockSignals(False)

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
        self._roi_widgets[self._selected_roi_index].phasor_roi.name = new_name
        self._refresh_roi_list()

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
        self._roi_widgets[self._selected_roi_index].phasor_roi.visible = checked
        self._colormap_dirty = True
        self._refresh_roi_list()
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

    # ── GMM-origin controls (cov_f / shift / Reset to fit) ────

    def _apply_gmm_geometry(
        self,
        widget: _ROIWidget,
        anchor: tuple[float, float] | None,
    ) -> None:
        """Recompute geometry from ``gmm_fit`` and push to the RectROI.

        ``anchor=None`` snaps the center back to the cluster mean (Reset
        to fit). ``anchor=phasor_roi.center`` preserves a manual drag
        across cov_f / shift edits.

        ``RectROI.setPos`` / ``setSize`` programmatic calls are wrapped in
        ``blockSignals`` so the resulting ``sigRegionChangeFinished`` does
        not feed back through ``_on_roi_moved_widget`` and overwrite the
        eigenstructure-derived values with rounded RectROI bbox values.
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
            cov_f=fit.cov_f, shift=fit.shift,
            shape=fit.shape,
            anchor=anchor,
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
        widget.cached_mask = None
        self._preview_timer.start()

    def _on_cov_f_changed(self, value: float) -> None:
        if self._selected_roi_index is None:
            return
        widget = self._roi_widgets[self._selected_roi_index]
        if widget.phasor_roi.gmm_fit is None:
            return
        widget.phasor_roi.gmm_fit.cov_f = float(value)
        # Drag-preserving anchor: keep current center, only update radii
        # via the eigenstructure recompute.
        self._apply_gmm_geometry(widget, anchor=widget.phasor_roi.center)

    def _on_shift_changed(self, value: float) -> None:
        if self._selected_roi_index is None:
            return
        widget = self._roi_widgets[self._selected_roi_index]
        if widget.phasor_roi.gmm_fit is None:
            return
        # Shift is measured against the cluster mean (the eigenstructure
        # baseline); using ``anchor=current center`` would make shifts
        # cumulative on top of any manual drag, which is surprising.
        # Instead, the spinbox value represents the *current* shift from
        # mean — so we re-anchor on the mean before applying.
        widget.phasor_roi.gmm_fit.shift = float(value)
        self._apply_gmm_geometry(widget, anchor=None)

    def _on_reset_fit_clicked(self) -> None:
        if self._selected_roi_index is None:
            return
        widget = self._roi_widgets[self._selected_roi_index]
        if widget.phasor_roi.gmm_fit is None:
            return
        # "Reset to fit": apply current cov_f / shift against the cluster
        # mean. Doesn't touch cov_f or shift values themselves.
        self._apply_gmm_geometry(widget, anchor=None)

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

    def _compute_combined_mask(self) -> np.ndarray:
        """Combine all visible ROIs into a single labeled uint8 mask.

        Uses cached per-ROI boolean masks. Only uncached ROIs recomputed.
        When a cell filter is active, the mask is restricted to pixels
        belonging to filtered cells only.
        """
        from percell4.domain.flim.phasor import phasor_roi_to_mask

        g, s = self._get_active_gs_maps()
        mask = np.zeros(g.shape, dtype=np.uint8)

        for widget in self._roi_widgets:
            if not widget.phasor_roi.visible:
                continue
            if widget.cached_mask is None:
                roi = widget.phasor_roi
                angle_rad = np.radians(roi.angle_deg)
                widget.cached_mask = phasor_roi_to_mask(
                    g, s, center=roi.center, radii=roi.radii,
                    angle_rad=angle_rad,
                )
            mask[widget.cached_mask] = widget.phasor_roi.label

        # Restrict mask to filtered cells when cell filter is active
        filtered_ids = self._session.filter_ids
        if filtered_ids is not None and self._labels is not None:
            cell_mask = np.isin(self._labels, list(filtered_ids))
            mask[~cell_mask] = 0

        # Restrict mask to active mask when the mask filter is engaged.
        # Trigger lazy load via _load_active_mask_flat so the preview
        # path works even if the histogram hasn't rendered yet.
        self._load_active_mask_flat()
        if (
            self._mask_filter_check.isChecked()
            and self._active_mask_array is not None
            and self._active_mask_array.shape == mask.shape
        ):
            mask[self._active_mask_array == 0] = 0

        return mask

    def _update_preview(self) -> None:
        """Compute combined mask and emit preview_mask_ready for the launcher."""
        if self._g_map is None or not self._roi_widgets:
            return

        mask = self._compute_combined_mask()

        # Build colormap only when dirty
        if self._colormap_dirty:
            from napari.utils.colormaps import DirectLabelColormap

            color_dict = {0: "transparent", None: "transparent"}
            for w in self._roi_widgets:
                if w.phasor_roi.visible:
                    color_dict[w.phasor_roi.label] = w.phasor_roi.color
            self._preview_colormap = DirectLabelColormap(color_dict=color_dict)
            self._colormap_dirty = False

        # Emit signal — launcher mediates viewer access
        self.preview_mask_ready.emit(mask, self._preview_colormap)

        # Status bar: pixel counts per ROI via bincount
        max_label = max((w.phasor_roi.label for w in self._roi_widgets
                         if w.phasor_roi.visible), default=0)
        if max_label > 0:
            counts = np.bincount(mask.ravel(), minlength=max_label + 1)
            total = self._total_valid_pixels or 1
            parts = []
            for w in self._roi_widgets:
                if w.phasor_roi.visible:
                    lbl = w.phasor_roi.label
                    pct = counts[lbl] / total * 100
                    parts.append(f"{w.phasor_roi.name}: {counts[lbl]:,} ({pct:.1f}%)")
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
        # Tear down ROI graphics
        for widget in list(self._roi_widgets):
            self._plot.removeItem(widget.roi)
            self._plot.removeItem(widget.curve)
        self._roi_widgets.clear()
        self._selected_roi_index = None
        self._colormap_dirty = True
        self._preview_colormap = None
        self._refresh_roi_list()
        self._on_roi_list_selection(-1)  # clears Selected ROI panel widgets

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
        g_flat = g_display.ravel()
        s_flat = s_display.ravel()

        mask_flat = self._load_active_mask_flat()
        # Surface a status message when the mask is configured but
        # bypassed (shape mismatch / read failure). The checkbox stays
        # checked so the user can fix the mismatch without re-toggling.
        mask_bypassed = (
            self._mask_filter_check.isChecked()
            and self._session.active_mask
            and mask_flat is None
            and self._g_map is not None
        )

        intensity_flat = self._intensity.ravel() if self._intensity is not None else None
        valid = compute_valid_phasor_pixels(
            g_flat, s_flat,
            labels_flat=self._labels_flat,
            filter_ids=self._session.filter_ids,
            mask_flat=mask_flat,
            intensity_flat=intensity_flat,
            intensity_threshold=self._intensity_threshold,
            ref_circle_center=self._ref_circle_center,
            ref_circle_radius=self._ref_circle_radius,
        )

        g_flat = g_flat[valid]
        s_flat = s_flat[valid]

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

        Each visible ROI becomes its own binary mask named after the ROI.
        The launcher saves each to HDF5 and adds each as a napari layer.
        """
        if self._g_map is None or not self._roi_widgets:
            self._status.showMessage("No phasor data or ROIs", 3000)
            return

        from percell4.domain.flim.phasor import phasor_roi_to_mask

        roi_masks: list[tuple[str, np.ndarray, str]] = []
        for w in self._roi_widgets:
            if not w.phasor_roi.visible:
                continue
            if w.cached_mask is None:
                roi = w.phasor_roi
                w.cached_mask = phasor_roi_to_mask(
                    self._g_map, self._s_map,
                    center=roi.center, radii=roi.radii,
                    angle_rad=np.radians(roi.angle_deg),
                )
            binary = np.zeros(self._g_map.shape, dtype=np.uint8)
            binary[w.cached_mask] = 1
            roi_masks.append((w.phasor_roi.name, binary, w.phasor_roi.color))

        if not roi_masks:
            self._status.showMessage("No visible ROIs to apply", 3000)
            return

        self.mask_applied.emit(roi_masks)
        names = ", ".join(name for name, _, _ in roi_masks)
        self._status.showMessage(f"Applied {len(roi_masks)} mask(s): {names}", 5000)

    # ── Save plot as PNG ──────────────────────────────────────

    def _on_save_png(self) -> None:
        """Save the current phasor plot widget as a PNG image."""
        if self._g_map is None:
            self._status.showMessage("No phasor data to save", 3000)
            return

        default_name = "phasor.png"
        handle = self._session.dataset
        if handle is not None:
            stem = handle.path.stem
            channel = self._session.active_channel
            default_name = f"{stem}_{channel}_phasor.png" if channel else f"{stem}_phasor.png"

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Phasor PNG", default_name, "PNG Image (*.png)"
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path = f"{path}.png"

        pixmap = self._plot.grab()
        if not pixmap.save(path, "PNG"):
            QMessageBox.warning(
                self, "Save Error", f"Failed to save phasor PNG to:\n{path}"
            )
            return
        self._status.showMessage(f"Saved phasor PNG: {path}", 4000)

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

        # Clear existing ROIs
        for w in self._roi_widgets:
            self._plot.removeItem(w.roi)
            self._plot.removeItem(w.curve)
        self._roi_widgets.clear()

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

        self._colormap_dirty = True
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
        self._save_geometry()
        self.hide()
        event.ignore()

    def _save_geometry(self) -> None:
        QSettings("LeeLabPerCell4", "PerCell4").setValue(
            "phasor_plot/geometry", self.saveGeometry()
        )

    def _restore_geometry(self) -> None:
        geom = QSettings("LeeLabPerCell4", "PerCell4").value("phasor_plot/geometry")
        if geom:
            self.restoreGeometry(geom)
