"""Adaptive Local Clipping panel — interactive whole-frame `adaptive` detection.

Exposes the production `adaptive` puncta detector in the Analysis tab. A
**Creator**: the Run button detects puncta on the active channel (optionally
auto-sizing the local window from an Otsu first-pass), writes a new
``/masks/<name>`` layer, auto-selects it, and shows it in napari. Heavy compute
runs in a :class:`~percell4.gui.workers.Worker`. Reads ``session.active_channel``
directly; the Run button writes only ``active_mask`` (via ``AcceptPunctaMask``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from percell4.gui._adaptive_clip_settings import AdaptiveClipSettingsWidget
from percell4.gui._cnr_classify_settings import CnrClassifySettingsWidget
from percell4.gui._resource_name_prompt import prompt_for_resource_name
from percell4.model import CellDataModel

logger = logging.getLogger(__name__)


def run_adaptive_detection(image, gaussian_sigma, settings, auto_window, window_method="otsu-mean"):
    """Worker body: optionally estimate the window via the finder registry, then detect.

    Returns ``(mask uint8, window_used int)``. When ``auto_window`` is True the
    window is estimated by ``adaptive_clip.auto_window`` using the named
    ``window_method`` (a ``WINDOW_FINDERS`` registry key, e.g. ``granule-size``)
    and the settings are rebuilt with it; otherwise the settings' window is used
    as-is. Pure (no Qt) so it is unit-testable and worker-safe.
    """
    from percell4.domain.measure.adaptive_clip import (
        auto_window as compute_auto_window,
        detect_adaptive_whole_frame,
    )
    from percell4.workflows.models import PunctaDetectorSettings

    window_used = int(dict(settings.detector_params).get("window_px", 15))
    if auto_window:
        window_used = compute_auto_window(image, gaussian_sigma, settings, method=window_method)
        params = dict(settings.detector_params)
        params["window_px"] = window_used
        settings = PunctaDetectorSettings(
            detector_name=settings.detector_name,
            seed_detector_name=settings.seed_detector_name,
            background_estimator_name=settings.background_estimator_name,
            detector_params=params,
            seed_params=settings.seed_params,
            min_spot_px=settings.min_spot_px,
            max_spot_px=settings.max_spot_px,
            spot_scale_prior=settings.spot_scale_prior,
        )
    mask = detect_adaptive_whole_frame(image, gaussian_sigma, settings)
    return mask, window_used


def run_adaptive_detection_stack(image, gaussian_sigma, settings, auto_window, window_method="otsu-mean"):
    """Worker body for a time-lapse ``(T, H, W)`` channel: detect each frame.

    Loops over the leading time axis, runs :func:`run_adaptive_detection` on each
    frame, and stacks the per-frame masks into ``(T, H, W)``. The auto window is
    estimated per frame (contract D3) with the named ``window_method``, so frames
    with different intensity stats get their own window. Mirrors
    ``segmentation_panel.run_cellpose_stack``'s per-frame dispatch. Returns
    ``(mask (T,H,W) uint8, windows list[int])``. Pure (no Qt) so it is
    unit-testable and worker-safe.
    """
    image = np.asarray(image)
    frames: list[np.ndarray] = []
    windows: list[int] = []
    for t in range(image.shape[0]):
        mask_t, window_t = run_adaptive_detection(
            image[t], gaussian_sigma, settings, auto_window, window_method
        )
        frames.append(np.asarray(mask_t, dtype=np.uint8))
        windows.append(int(window_t))
    return np.stack(frames, axis=0), windows


def run_adaptive_detection_by_particle_size(
    image, labels, pixel_size_um, d_min_um, k, presmooth_sigma_px
):
    """Worker body for the one-knob particle-size detector (per-cell).

    Derives the window from ``d_min`` (returned for the status note) and runs the
    eye-validated per-cell adaptive clip: window + size filter follow ``d_min``,
    the noise floor is each cell's own robust MAD, and ``k`` is the caller's
    sensitivity setting (defaults to 1 in the panel; raise to be conservative).
    Returns ``(mask uint8, window_used int)``. Pure (no Qt) so it is worker-safe
    and unit-testable.
    """
    from percell4.domain.measure.adaptive_clip import (
        detect_adaptive_by_particle_size,
        window_min_spot_for_particle,
    )

    window_used, _ = window_min_spot_for_particle(d_min_um, pixel_size_um)
    mask = detect_adaptive_by_particle_size(
        image,
        labels,
        pixel_size_um,
        d_min_um,
        k=k,
        presmooth_sigma_px=presmooth_sigma_px,
    )
    return mask, window_used


def run_adaptive_detection_per_cell(image, labels, window_px, min_spot_px, k, presmooth_sigma_px):
    """Worker body for manual mode off a segmentation (per-cell, explicit window).

    Thresholds each Cellpose cell against its own robust MAD σ at the manual
    window + k (the segmentation-aware sibling of the whole-frame manual path).
    Returns ``(mask uint8, window_used int)``. Pure (no Qt) so it is worker-safe.
    """
    from percell4.domain.measure.adaptive_clip import detect_adaptive_per_cell

    mask = detect_adaptive_per_cell(
        image,
        labels,
        window_px=int(window_px),
        min_spot_px=int(min_spot_px),
        k=k,
        presmooth_sigma_px=presmooth_sigma_px,
    )
    return mask, int(window_px)


def run_adaptive_detection_multiscale(
    image, labels, start_window_px, max_particle_px, k, presmooth_sigma_px,
    force_passes=None, min_spot_px=1,
):
    """Worker body for the multi-scale routine (per-cell, doubling windows OR-combined).

    Runs the per-cell adaptive clip at a doubling window sequence (from
    ``start_window_px`` until past ``max_particle_px``, or exactly ``force_passes``
    passes when set) and unions the masks, so particles across a wide size range are
    all captured. ``min_spot_px`` is the Min-particle-size filter applied once to the
    combined mask. Returns ``(mask uint8, largest_window_used int)``. Pure (no Qt) so
    it is worker-safe.
    """
    from percell4.domain.measure.adaptive_clip import detect_adaptive_multiscale

    mask, windows = detect_adaptive_multiscale(
        image,
        labels,
        start_window_px=start_window_px,
        max_particle_px=max_particle_px,
        k=k,
        presmooth_sigma_px=presmooth_sigma_px,
        min_spot_px=min_spot_px,
        force_passes=force_passes,
    )
    return mask, int(windows[-1]) if windows else 0


def run_adaptive_auto_extract(
    image, labels, smallest_particle_px, presmooth_sigma_px, min_spot_px
):
    """Worker body for the two-pass auto-extraction routine (per-cell).

    Runs :func:`percell4.domain.measure.auto_extraction.auto_extract`: a fine pass
    at k=1 (window from ``smallest_particle_px`` — measured by LoG when it is
    ``None``, else ``3 ×`` it) plus, when the LoG-measured largest particle exceeds
    it, a coarse pass (window = 3 × largest, k = the noise-symmetry floor),
    OR-unioned with hole-filling. ``min_spot_px`` filters the union. Returns
    ``(mask uint8, report)`` where ``report`` is the :class:`AutoExtractReport`.
    Pure (no Qt) so it is worker-safe.
    """
    from percell4.domain.measure.auto_extraction import auto_extract

    mask, report = auto_extract(
        image,
        labels,
        smallest_particle_px=smallest_particle_px,
        presmooth_sigma_px=presmooth_sigma_px,
        min_spot_px=min_spot_px,
    )
    return mask, report


def run_adaptive_auto_extract_stack(
    image, labels, smallest_particle_px, presmooth_sigma_px, min_spot_px
):
    """Worker body for a time-lapse ``(T,H,W)`` channel: auto-extract each frame.

    Loops the leading time axis and runs :func:`run_adaptive_auto_extract` on each
    frame independently — so every timepoint gets its OWN largest-particle sizing,
    coarse window and noise floor (the multi-time-point 'treat each frame as its own
    image' behaviour). Stacks the per-frame masks into ``(T,H,W)``. A frame with no
    detectable particles in auto-detect mode (``smallest_particle_px is None``) yields
    an empty plane rather than aborting the whole run (R9: the dissolved end of a
    washout). Returns ``(mask (T,H,W) uint8, reports list[AutoExtractReport | None])``;
    a frame that degraded to empty has a ``None`` report. Mirrors
    ``run_adaptive_detection_stack``'s per-frame dispatch. Pure (no Qt) so it is
    unit-testable and worker-safe.
    """
    from percell4.domain.measure.auto_extraction import auto_extract

    image = np.asarray(image)
    labels = np.asarray(labels)
    frames: list[np.ndarray] = []
    reports: list = []
    for t in range(image.shape[0]):
        try:
            mask_t, report_t = auto_extract(
                image[t],
                labels[t],
                smallest_particle_px=smallest_particle_px,
                presmooth_sigma_px=presmooth_sigma_px,
                min_spot_px=min_spot_px,
            )
        except ValueError as e:
            # Auto-detect found no particles to size this frame — a recoverable empty
            # frame, not a failed run (R9). A supplied smallest never hits this.
            if smallest_particle_px is None and "no blobs" in str(e):
                mask_t = np.zeros(labels[t].shape, dtype=np.uint8)
                report_t = None
            else:
                raise
        frames.append(np.asarray(mask_t, dtype=np.uint8))
        reports.append(report_t)
    return np.stack(frames, axis=0), reports


def run_cnr_classification(image, feature_mask, labels, *, mode, threshold):
    """Worker body for CNR subpopulation classification (per-cell, pure).

    Maps the GUI ``mode`` to :func:`classify_by_cnr`: ``"discover"`` → defaults,
    ``"guided"`` → ``threshold=…``, ``"forced"`` → ``n_populations=2``. Splits the
    result's ``labels_image`` (0=bg / 1=low-CNR / 2=high-CNR) into one ``{0,1}``
    ``uint8`` mask per population and returns
    ``(pop_masks: list[(suffix, mask)], components: list[dict], report: dict)``.
    A single-population result yields one mask with suffix ``""`` (the base name).
    Pure (no Qt / store) so it is worker-safe and unit-testable.
    """
    from percell4.domain.measure.cnr_classification import classify_by_cnr

    if mode == "guided":
        res = classify_by_cnr(image, feature_mask, labels, threshold=float(threshold))
    elif mode == "forced":
        res = classify_by_cnr(image, feature_mask, labels, n_populations=2)
    else:  # discover
        res = classify_by_cnr(image, feature_mask, labels)

    lab = np.asarray(res.labels_image)
    if res.n_subpopulations >= 2:
        pop_masks = [
            ("_low", (lab == 1).astype(np.uint8)),
            ("_high", (lab == 2).astype(np.uint8)),
        ]
    else:
        # One population: all classified foci in a single mask under the base name.
        pop_masks = [("", (lab > 0).astype(np.uint8))]
    # Drop any empty population mask (e.g. a degenerate split) so we never persist
    # a blank resource.
    pop_masks = [(suffix, m) for suffix, m in pop_masks if int(m.sum()) > 0]
    return pop_masks, res.components, res.report


def run_cnr_measure(image, feature_mask, labels):
    """Worker body for the interactive segmenter: measure per-focus CNR.

    Returns ``(records, component_labels)`` where ``records`` are the per-focus
    :func:`measure_cnr` dicts and ``component_labels`` is the labelled feature
    mask (same ``scipy.ndimage.label`` call, so each record's ``label`` indexes
    it). Pure (no Qt / store) so it is worker-safe.
    """
    from scipy.ndimage import label

    from percell4.domain.measure.cnr_classification import measure_cnr

    records = measure_cnr(image, feature_mask, labels)
    component_labels, _ = label(np.asarray(feature_mask) > 0)
    return records, component_labels


class AdaptiveClipPanel(QWidget):
    """Interactive Adaptive Local Clipping panel (Creator)."""

    def __init__(
        self,
        data_model: CellDataModel,
        *,
        get_repo: Callable[[], Any | None] = lambda: None,
        get_store: Callable[[], Any | None] = lambda: None,
        get_viewer_window: Callable[[], Any | None] = lambda: None,
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.data_model = data_model
        self._get_repo = get_repo
        self._get_store = get_store
        self._get_viewer_window = get_viewer_window
        self._show_status_cb = show_status
        self._worker = None
        self._pending_name: str | None = None
        self._pending_auto = False
        self._pending_particle = False
        self._pending_d_min = 0.0
        # CNR classification (separate Action path — its own worker + pending state
        # so it never collides with the detection run's _pending_* flags).
        self._cnr_worker = None
        self._pending_classify_base: str | None = None
        # Interactive CNR segmenter (separate worker + held window reference).
        self._measure_worker = None
        self._cnr_segmenter = None
        # Auto-extraction: whether the last run auto-detected the smallest Ø
        # (drives the readout back-fill in _on_auto_extract_done).
        self._pending_ae_auto = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignTop)

        self._settings = AdaptiveClipSettingsWidget(self)
        layout.addWidget(self._settings)

        from percell4.gui import theme

        self._run_btn = QPushButton("Run Adaptive Clipping")
        self._run_btn.setStyleSheet(
            f"QPushButton {{ background-color: {theme.ACTION_GREEN}; color: white;"
            f" padding: 8px; font-weight: bold; border-radius: 4px; }}"
            f" QPushButton:hover {{ background-color: {theme.ACTION_GREEN_HOVER}; }}"
        )
        self._run_btn.clicked.connect(self._on_run)
        layout.addWidget(self._run_btn)

        self._status = QLabel("Ready")
        self._status.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-style: italic;")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        # ── CNR subpopulation classification (a separate Action on a saved mask) ──
        cnr_heading = QLabel("CNR Subpopulation Classification")
        cnr_heading.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(cnr_heading)

        self._cnr_settings = CnrClassifySettingsWidget(self)
        layout.addWidget(self._cnr_settings)

        self._classify_btn = QPushButton("Classify Mask by CNR")
        self._classify_btn.setStyleSheet(
            f"QPushButton {{ background-color: {theme.ACTION_GREEN}; color: white;"
            f" padding: 8px; font-weight: bold; border-radius: 4px; }}"
            f" QPushButton:hover {{ background-color: {theme.ACTION_GREEN_HOVER}; }}"
        )
        self._classify_btn.clicked.connect(self._on_classify)
        layout.addWidget(self._classify_btn)

        self._segment_btn = QPushButton("Segment by CNR (interactive)")
        self._segment_btn.setToolTip(
            "Open a CNR histogram with draggable dividers and a live napari "
            "preview; save any number of CNR segments as masks."
        )
        self._segment_btn.clicked.connect(self._on_segment_cnr)
        layout.addWidget(self._segment_btn)

        layout.addStretch()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Refresh the CNR source-mask list when the panel becomes visible."""
        super().showEvent(event)
        self._refresh_cnr_masks()

    def _refresh_cnr_masks(self) -> None:
        """Repopulate the CNR source-mask combo from the store's current masks."""
        store = self._get_store()
        names = (
            store.list_masks()
            if store is not None and hasattr(store, "list_masks")
            else []
        )
        self._cnr_settings.set_mask_choices(names)

    # ── helpers ──────────────────────────────────────────────────

    def _show_status(self, msg: str) -> None:
        self._status.setText(msg)
        self._show_status_cb(msg)

    def _find_layer_data(self, viewer_win, kind: str, name: str | None):
        """Data array of the first ``kind`` ("Image"/"Labels") layer named ``name``.

        Returns ``None`` when ``name`` is empty, the viewer is gone, or no such
        layer exists. Matches on ``__class__.__name__`` so the tests' mock layers
        resolve the same way real napari layers do.
        """
        if not name or viewer_win is None or viewer_win.viewer is None:
            return None
        for layer in viewer_win.viewer.layers:
            if layer.__class__.__name__ == kind and layer.name == name:
                return np.asarray(layer.data)
        return None

    def _pixel_size_um(self, store):
        """The dataset's pixel size (µm/px) as a positive float, or ``None``.

        Coerces the stored value and rejects missing / non-numeric / non-finite /
        non-positive metadata (e.g. an externally-edited HDF5), so callers can
        treat a non-``None`` return as a usable µm/px without re-validating.
        """
        try:
            px = float(store.metadata.get("pixel_size_um"))
        except (TypeError, ValueError, AttributeError):
            return None
        if not np.isfinite(px) or px <= 0:
            return None
        return px

    # ── debug (terminal) ─────────────────────────────────────────

    def _print_settings_debug(self, config) -> None:
        """Print every Adaptive Local Clipping setting to the terminal (debug)."""
        print(
            "\n===== Adaptive Local Clipping run =====\n"
            f"  auto_window       : {config.auto_window}\n"
            f"  window_method     : {config.window_method}\n"
            f"  particle_mode     : {config.particle_mode}\n"
            f"  multiscale_mode   : {config.multiscale_mode}\n"
            f"  window            : {config.window_value} {config.window_unit}\n"
            f"  k                 : {config.k}\n"
            f"  gaussian_sigma    : {config.gaussian_sigma}\n"
            f"  noise_estimator   : {config.noise_estimator}\n"
            f"  size_percentile   : {config.particle_percentile} %\n"
            f"  detected Ø (µm)   : {config.d_min_um}\n"
            f"  size_cutoff (px)  : {config.size_cutoff_px}  auto_start={config.ms_auto_start}"
            f"  iterations={config.ms_iterations or 'auto'}\n"
            f"  min particle size : {config.min_size_value} {config.min_size_unit}",
            flush=True,
        )

    def _print_otsu_report(self, report) -> None:
        """Print the Otsu first-pass diagnostics for an Otsu particle-size run (debug)."""
        print(
            f"  [otsu first-pass] scope={report.scope}\n"
            f"    Otsu threshold               : {report.otsu_threshold:.4f}\n"
            f"    particle size (p{report.percentile:g})       : {report.diameter_px:.3f} px"
            f" = {report.d_min_um:.4f} µm  (n_components={report.n_components})\n"
            f"    threshold-area mean intensity: {report.area_mean:.4f}\n"
            f"    mean − threshold             : {report.mean_minus_threshold:.4f}\n"
            f"    threshold-area max intensity : {report.area_max:.4f}\n"
            f"    threshold-area min intensity : {report.area_min:.4f}",
            flush=True,
        )

    # ── run (Creator) ────────────────────────────────────────────

    def _on_run(self) -> None:
        viewer_win = self._get_viewer_window()
        if viewer_win is None or viewer_win.viewer is None:
            self._show_status("Open a dataset in the viewer first")
            return
        store = self._get_store()
        if store is None:
            self._show_status("No dataset loaded")
            return

        channel = self.data_model.session.active_channel
        if not channel:
            self._show_status("Select a channel in the Session window first")
            return

        image = self._find_layer_data(viewer_win, "Image", channel)
        if image is None:
            self._show_status(f"Channel '{channel}' not found in viewer")
            return
        # Time-lapse: a (T,H,W) channel layer is detected per frame (stacked to
        # a (T,H,W) mask), not sliced to the displayed frame. A 2D channel takes
        # the historical single-frame path.
        n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)
        is_timelapse = image.ndim == 3 and n_timepoints > 1

        config = self._settings.current_config()
        self._print_settings_debug(config)

        # "Otsu detect particle size" runs the per-cell detector off a fresh Otsu
        # measurement (needs labels + a known pixel size).
        if config.particle_mode:
            self._run_particle_mode(config, image, is_timelapse, store, viewer_win)
            return

        # Multi-scale: per-cell Otsu size assessment -> doubling windows OR-combined.
        if config.multiscale_mode:
            self._run_multiscale_mode(config, image, is_timelapse, store, viewer_win)
            return

        # Auto extraction: two-pass (fine + LoG-sized coarse) per-cell union.
        if config.auto_extract_mode:
            self._run_auto_extract_mode(config, image, is_timelapse, store, viewer_win)
            return

        # Manual mode (Auto off): window in px/µm, per-cell off the active
        # segmentation (whole-frame fallback when none is active).
        if not config.auto_window:
            self._run_manual_mode(config, image, is_timelapse, store, viewer_win)
            return

        # Auto-window finder modes (granule-size / otsu-mean): whole-frame.
        # Resolve the particle-size filter to a px area (µm² needs calibration).
        from percell4.domain.measure.adaptive_clip import resolve_min_area_px

        pixel_size_um = self._pixel_size_um(store)
        try:
            min_spot_px = resolve_min_area_px(
                config.min_size_value, config.min_size_unit, pixel_size_um
            )
        except ValueError as e:
            self._show_status(str(e))
            return

        existing = store.list_masks() if hasattr(store, "list_masks") else []
        mask_name = prompt_for_resource_name(
            self,
            title="Save Adaptive Clipping Mask",
            label="Mask name:",
            default="adaptive",
            existing_names=existing,
        )
        if mask_name is None:
            return

        from percell4.workflows.models import PunctaDetectorSettings

        settings = PunctaDetectorSettings(
            detector_name="adaptive",
            seed_detector_name="otsu",
            background_estimator_name=config.noise_estimator,
            # window_px is overwritten by the finder (auto_window is always True here).
            detector_params={"window_px": 15, "k": config.k},
            min_spot_px=max(1, int(min_spot_px)),
            spot_scale_prior=(1.0, 4.0),
        )

        self._pending_name = mask_name
        self._pending_auto = config.auto_window
        self._pending_particle = False  # this is the auto-finder whole-frame path
        self._run_btn.setEnabled(False)
        self._settings.set_enabled(False)
        n_frames = image.shape[0] if is_timelapse else 1
        detecting = (
            f"Detecting (auto window: {config.window_method})..."
            if config.auto_window
            else "Detecting..."
        )
        if is_timelapse:
            detecting = f"Detecting across {n_frames} timepoints..."
        self._show_status(detecting)

        from percell4.gui.workers import Worker

        worker_fn = run_adaptive_detection_stack if is_timelapse else run_adaptive_detection
        self._worker = Worker(
            worker_fn, image, config.gaussian_sigma, settings, config.auto_window, config.window_method
        )
        self._worker.finished.connect(self._on_detect_done)
        self._worker.error.connect(self._on_detect_error)
        self._worker.start()

    def _run_manual_mode(self, config, image, is_timelapse, store, viewer_win) -> None:
        """Creator path for manual mode (Auto off): explicit window, off the segmentation.

        The window is the manual value resolved to an odd px count (px as-is, or
        µm via the dataset pixel size). Detection goes per-cell against the active
        segmentation's own noise floor when one is present (and single-frame);
        otherwise it falls back to whole-frame detection.
        """
        from percell4.domain.measure.adaptive_clip import resolve_min_area_px, resolve_window_px

        pixel_size_um = self._pixel_size_um(store)
        try:
            window_px = resolve_window_px(config.window_value, config.window_unit, pixel_size_um)
            min_spot_px = max(
                1, resolve_min_area_px(config.min_size_value, config.min_size_unit, pixel_size_um)
            )
        except ValueError as e:
            self._show_status(str(e))
            return

        # Go off the active segmentation (per-cell) when present + single-frame;
        # otherwise fall back to whole-frame detection.
        seg = self.data_model.session.active_segmentation
        labels = self._find_layer_data(viewer_win, "Labels", seg) if seg else None
        per_cell = labels is not None and not is_timelapse and labels.shape == image.shape

        existing = store.list_masks() if hasattr(store, "list_masks") else []
        mask_name = prompt_for_resource_name(
            self,
            title="Save Adaptive Clipping Mask",
            label="Mask name:",
            default="adaptive",
            existing_names=existing,
        )
        if mask_name is None:
            return

        self._pending_name = mask_name
        self._pending_auto = False
        self._pending_particle = False
        self._run_btn.setEnabled(False)
        self._settings.set_enabled(False)
        if per_cell:
            scope = "per-cell"
        else:
            scope = "whole-frame, per frame" if is_timelapse else "whole-frame"
        self._show_status(f"Detecting (window {window_px} px, {scope})...")
        print(
            f"  [manual] window {window_px} px"
            f" (from {config.window_value:g} {config.window_unit}), scope={scope}",
            flush=True,
        )

        from percell4.gui.workers import Worker

        if per_cell:
            self._worker = Worker(
                run_adaptive_detection_per_cell,
                image,
                labels,
                window_px,
                min_spot_px,
                float(config.k),
                config.gaussian_sigma,
            )
        else:
            from percell4.workflows.models import PunctaDetectorSettings

            settings = PunctaDetectorSettings(
                detector_name="adaptive",
                seed_detector_name="otsu",
                background_estimator_name=config.noise_estimator,
                detector_params={"window_px": window_px, "k": config.k},
                min_spot_px=min_spot_px,
                spot_scale_prior=(1.0, 4.0),
            )
            worker_fn = run_adaptive_detection_stack if is_timelapse else run_adaptive_detection
            self._worker = Worker(
                worker_fn, image, config.gaussian_sigma, settings, False, config.window_method
            )
        self._worker.finished.connect(self._on_detect_done)
        self._worker.error.connect(self._on_detect_error)
        self._worker.start()

    def _run_multiscale_mode(self, config, image, is_timelapse, store, viewer_win) -> None:
        """Creator path for the multi-scale routine (per-cell, doubling windows).

        Per-cell Otsu measures the particle-size range on the current image; the
        starting window is ½ × mean particle (or the manual Window field); the
        per-cell detector runs at a doubling window sequence until past the largest
        particle, and the masks are OR-combined. Per-cell ⇒ requires an active
        segmentation, single-frame.
        """
        if is_timelapse:
            self._show_status("Multi-scale mode supports single-frame channels only")
            return
        seg = self.data_model.session.active_segmentation
        if not seg:
            self._show_status("Multi-scale mode needs an active segmentation")
            return
        labels = self._find_layer_data(viewer_win, "Labels", seg)
        if labels is None:
            self._show_status(f"Segmentation '{seg}' not found in viewer")
            return
        if labels.shape != image.shape:
            self._show_status("Segmentation and channel shapes differ")
            return

        from percell4.domain.measure.adaptive_clip import (
            assess_particle_sizes_per_cell,
            multiscale_windows,
            resolve_min_area_px,
            resolve_window_px,
        )

        # Min-particle-size filter applied to the OR-combined output (µm² needs calibration).
        try:
            min_spot_px = max(
                1,
                resolve_min_area_px(
                    config.min_size_value, config.min_size_unit, self._pixel_size_um(store)
                ),
            )
        except ValueError as e:
            self._show_status(str(e))
            return

        # First pass: per-cell Otsu size assessment on the current image.
        report = assess_particle_sizes_per_cell(
            image, labels, config.gaussian_sigma, cutoff_px=float(config.size_cutoff_px)
        )
        if report is None:
            self._show_status("Multi-scale: Otsu found no particles to size from")
            return

        # Starting window: ½ × mean particle (auto) or the manual Window field.
        if config.ms_auto_start:
            start_window_px = max(3, int(round(0.5 * report.mean_px)) | 1)
        else:
            try:
                start_window_px = resolve_window_px(
                    config.window_value, config.window_unit, self._pixel_size_um(store)
                )
            except ValueError as e:
                self._show_status(str(e))
                return

        force_passes = config.ms_iterations if config.ms_iterations > 0 else None
        windows = multiscale_windows(
            start_window_px, report.largest_px, force_passes=force_passes
        )
        stop_note = (
            f"{len(windows)} forced passes"
            if force_passes
            else "stop > largest"
        )
        # Debug: raw Otsu min/max (calibration), post-cutoff stats, and the windows.
        print(
            f"  [multi-scale] Otsu raw particle Ø: min {report.raw_min_px:.2f} px, "
            f"max {report.raw_max_px:.2f} px  (n_raw={report.n_raw})\n"
            f"    post-cutoff (>= {config.size_cutoff_px:g} px): smallest "
            f"{report.smallest_px:.2f}, mean {report.mean_px:.2f}, largest "
            f"{report.largest_px:.2f}, range {report.range_px:.2f}  (n={report.n_particles})\n"
            f"    start window {start_window_px} px "
            f"({'½×mean auto' if config.ms_auto_start else 'manual'}); "
            f"windows {windows} ({stop_note}); min particle {min_spot_px} px² (output filter)",
            flush=True,
        )

        existing = store.list_masks() if hasattr(store, "list_masks") else []
        mask_name = prompt_for_resource_name(
            self,
            title="Save Adaptive Clipping Mask",
            label="Mask name:",
            default="adaptive",
            existing_names=existing,
        )
        if mask_name is None:
            return

        self._pending_name = mask_name
        self._pending_auto = False
        self._pending_particle = False
        self._run_btn.setEnabled(False)
        self._settings.set_enabled(False)
        self._show_status(
            f"Detecting (multi-scale, {len(windows)} windows {windows[0]}–{windows[-1]} px)..."
        )

        from percell4.gui.workers import Worker

        self._worker = Worker(
            run_adaptive_detection_multiscale,
            image,
            labels,
            start_window_px,
            report.largest_px,
            float(config.k),
            config.gaussian_sigma,
            force_passes,
            min_spot_px,
        )
        self._worker.finished.connect(self._on_detect_done)
        self._worker.error.connect(self._on_detect_error)
        self._worker.start()

    def _run_auto_extract_mode(self, config, image, is_timelapse, store, viewer_win) -> None:
        """Creator path for the two-pass auto-extraction routine (per-cell).

        By default the smallest particle (fine window) is **auto-detected** from
        the image (LoG) inside the worker, so it adapts per dataset; turning off
        "Auto-detect smallest" lets the user supply the optical-resolution Ø (px,
        or µm via the dataset pixel size). The largest is always measured by LoG
        and the coarse k by the noise-symmetry floor. Per-cell ⇒ requires an
        active segmentation. A time-lapse ``(T,H,W)`` channel is auto-extracted per
        frame (each frame sized independently) and saved as one ``(T,H,W)`` mask.
        """
        seg = self.data_model.session.active_segmentation
        if not seg:
            self._show_status("Auto extraction needs an active segmentation")
            return
        labels = self._find_layer_data(viewer_win, "Labels", seg)
        if labels is None:
            self._show_status(f"Segmentation '{seg}' not found in viewer")
            return
        if labels.shape != image.shape:
            self._show_status("Segmentation and channel shapes differ")
            return

        from percell4.domain.measure.adaptive_clip import resolve_min_area_px

        pixel_size_um = self._pixel_size_um(store)
        # Smallest particle: auto-detected (None) by default, or the manual
        # optical-resolution override resolved to px (px as-is, or µm via pixel size).
        if config.auto_extract_smallest_auto:
            smallest_px = None
        else:
            if config.smallest_particle_unit == "um":
                if not pixel_size_um or float(pixel_size_um) <= 0:
                    self._show_status(
                        "µm smallest-particle size needs a known pixel size; switch "
                        "the unit to px or re-import with TIFF resolution metadata."
                    )
                    return
                smallest_px = float(config.smallest_particle_value) / float(pixel_size_um)
            else:
                smallest_px = float(config.smallest_particle_value)
            if smallest_px <= 0:
                self._show_status("Smallest particle Ø must be > 0")
                return

        try:
            min_spot_px = max(
                1, resolve_min_area_px(config.min_size_value, config.min_size_unit, pixel_size_um)
            )
        except ValueError as e:
            self._show_status(str(e))
            return

        existing = store.list_masks() if hasattr(store, "list_masks") else []
        mask_name = prompt_for_resource_name(
            self,
            title="Save Adaptive Clipping Mask",
            label="Mask name:",
            default="adaptive",
            existing_names=existing,
        )
        if mask_name is None:
            return

        self._pending_name = mask_name
        self._pending_auto = False
        self._pending_particle = False
        # Remember whether to back-fill the smallest-Ø readout after the run.
        self._pending_ae_auto = config.auto_extract_smallest_auto
        self._run_btn.setEnabled(False)
        self._settings.set_enabled(False)
        smallest_note = (
            "auto-detect (LoG)"
            if smallest_px is None
            else f"{smallest_px:.2f} px (from {config.smallest_particle_value:g} "
            f"{config.smallest_particle_unit})"
        )
        print(
            f"  [auto-extract] smallest particle: {smallest_note}; "
            f"min particle {min_spot_px} px² (union filter)",
            flush=True,
        )
        self._show_status(f"Detecting (auto extraction, smallest {smallest_note})...")

        from percell4.gui.workers import Worker

        # Time-lapse: auto-extract each frame independently and stack to (T,H,W);
        # single-frame: the 2D worker. Both share the same call signature.
        worker_fn = (
            run_adaptive_auto_extract_stack if is_timelapse else run_adaptive_auto_extract
        )
        self._worker = Worker(
            worker_fn,
            image,
            labels,
            smallest_px,
            config.gaussian_sigma,
            min_spot_px,
        )
        self._worker.finished.connect(self._on_auto_extract_done)
        self._worker.error.connect(self._on_detect_error)
        self._worker.start()

    def _print_auto_extract_report(self, report) -> None:
        """Print the two-pass auto-extraction passes/sizes to the terminal (debug)."""
        print(
            f"  [auto-extract] smallest: {report.smallest_source}\n"
            f"    passes {report.passes} "
            f"(fine window {report.fine_window}; "
            f"largest Ø {report.largest_particle_px} px; "
            f"second pass {report.second_pass_used})\n"
            f"    presmooth σ {report.presmooth_sigma_px}; n_cells {report.n_cells}; "
            f"components {report.n_components}; area {report.area_px} px",
            flush=True,
        )

    def _on_auto_extract_done(self, result) -> None:
        """Finished handler for auto-extraction: print the report(s), then Creator-save.

        The time-lapse stack worker returns ``(mask (T,H,W), reports list)`` (a per-frame
        report, ``None`` for a frame that degraded to empty); the single-frame worker
        returns ``(mask 2D, report)``. Both route to the shared ``(T,H,W)``-aware save.
        """
        mask, report = result
        is_stack = isinstance(report, (list, tuple))
        reports = list(report) if is_stack else [report]
        valid = [r for r in reports if r is not None]
        for r in valid:
            self._print_auto_extract_report(r)
        # When the smallest was auto-detected, surface the first frame's value in the
        # (readout) smallest-Ø field so the user sees it adapt per dataset.
        if getattr(self, "_pending_ae_auto", False) and valid:
            self._settings.set_smallest_value(valid[0].smallest_diameter_px)
        self._pending_ae_auto = False
        # Reuse the standard Creator save (no window write-back; the pending flags were
        # cleared so no auto/particle note is fabricated). For a stack, pass the per-frame
        # fine-window list so _on_detect_done's is_stack handling applies.
        if is_stack:
            windows = [(r.fine_window if r is not None else 0) for r in reports]
            self._on_detect_done((mask, windows))
        else:
            self._on_detect_done((mask, report.fine_window))

    def _run_particle_mode(self, config, image, is_timelapse, store, viewer_win) -> None:
        """Creator path for the one-knob particle-size detector (per-cell).

        The smallest-particle Ø is re-measured FRESH from the current image's Otsu
        first-pass at each run and drives the window (the d_min field is a readout
        only); the run never reuses a cached value. Requires a known pixel size
        (the window is physical) and an active segmentation (σ is per-cell).
        Restricted to single-frame channels — the per-cell loop expects 2D image +
        2D labels.
        """
        if is_timelapse:
            self._show_status("Particle-size mode supports single-frame channels only")
            return

        pixel_size_um = self._pixel_size_um(store)
        if not pixel_size_um or float(pixel_size_um) <= 0:
            self._show_status(
                "Particle-size mode needs a known pixel size (µm/px) on this dataset"
            )
            return

        seg = self.data_model.session.active_segmentation
        if not seg:
            self._show_status("Particle-size mode needs an active segmentation")
            return
        labels = self._find_layer_data(viewer_win, "Labels", seg)
        if labels is None:
            self._show_status(f"Segmentation '{seg}' not found in viewer")
            return
        if labels.shape != image.shape:
            self._show_status("Segmentation and channel shapes differ")
            return

        # Re-detect the smallest particle FRESH on this dataset (the d_min field is
        # a readout, not an input): the window is sized from the current image's
        # Otsu first-pass, never a cached/stale value.
        from percell4.domain.measure.adaptive_clip import otsu_smallest_particle

        try:
            report = otsu_smallest_particle(
                image,
                config.gaussian_sigma,
                float(pixel_size_um),
                cp_mask=labels > 0,
                percentile=config.particle_percentile,
            )
        except Exception as e:  # noqa: BLE001 — surface any detection failure
            self._show_status(f"Otsu first-pass failed: {e}")
            return
        if report is None:
            self._show_status(
                "Otsu first-pass found no particle to size the window — "
                "check the channel / segmentation"
            )
            return
        d_min_um = report.d_min_um
        self._settings.set_d_min_um(d_min_um)  # surface the value the run will use
        self._print_otsu_report(report)

        existing = store.list_masks() if hasattr(store, "list_masks") else []
        mask_name = prompt_for_resource_name(
            self,
            title="Save Adaptive Clipping Mask",
            label="Mask name:",
            default="adaptive",
            existing_names=existing,
        )
        if mask_name is None:
            return

        self._pending_name = mask_name
        self._pending_auto = False
        self._pending_particle = True
        self._pending_d_min = float(d_min_um)
        self._run_btn.setEnabled(False)
        self._settings.set_enabled(False)
        self._show_status(
            f"Detecting (smallest particle {d_min_um:g} µm, per-cell)..."
        )

        from percell4.gui.workers import Worker

        self._worker = Worker(
            run_adaptive_detection_by_particle_size,
            image,
            labels,
            float(pixel_size_um),
            float(d_min_um),
            float(config.k),  # sensitivity knob (defaults to 1; raise to be conservative)
            config.gaussian_sigma,
        )
        self._worker.finished.connect(self._on_detect_done)
        self._worker.error.connect(self._on_detect_error)
        self._worker.start()

    def _on_detect_error(self, err) -> None:
        # Clear the pending flags so a failed run cannot mislabel the NEXT run
        # (e.g. a failed per-cell run leaving _pending_particle set, which would
        # then overwrite the manual Window spinbox + fabricate a per-cell note).
        self._pending_auto = False
        self._pending_particle = False
        self._run_btn.setEnabled(True)
        self._settings.set_enabled(True)
        self._show_status(f"Detection error: {err.exc_type}: {err.message}")

    def _on_detect_done(self, result) -> None:
        mask, window_used = result
        self._run_btn.setEnabled(True)
        self._settings.set_enabled(True)

        # The time-lapse stack worker returns a per-frame list of windows;
        # the single-frame worker returns one int. Normalize for the spinbox
        # (show the first frame's auto window) and the status note.
        is_stack = isinstance(window_used, (list, tuple))
        window_display = (window_used[0] if window_used else 0) if is_stack else window_used

        name = self._pending_name or "adaptive"
        try:
            from percell4.application.use_cases.accept_puncta_mask import AcceptPunctaMask

            repo = self._get_repo()
            uc = AcceptPunctaMask(repo, self.data_model.session)
            res = uc.execute(mask, name)
        except Exception as e:  # noqa: BLE001 — surface any persist failure to the user
            self._show_status(f"Failed to save mask: {e}")
            return

        viewer_win = self._get_viewer_window()
        if viewer_win is not None:
            viewer_win.add_mask(np.asarray(mask, dtype=np.uint8), name=name)

        # Surface the window that was used (auto-estimated, or derived from d_min).
        if self._pending_auto or self._pending_particle:
            self._settings.set_window_value(window_display)

        if self._pending_particle:
            win_note = (
                f" (Ø {self._pending_d_min:g} µm → window {window_display} px, per-cell)"
            )
        elif self._pending_auto:
            win_note = (
                f" (auto window {window_display}, per frame)"
                if is_stack
                else f" (auto window {window_display})"
            )
        else:
            win_note = ""
        self._pending_particle = False
        # A new mask is now available as a CNR classification source.
        self._refresh_cnr_masks()
        self._show_status(f"Saved '{name}': {res.n_positive:,} px{win_note}")

    # ── CNR subpopulation classification (Action) ─────────────────

    def _print_cnr_settings_debug(self, cfg) -> None:
        """Print the CNR-classification settings to the terminal (debug)."""
        print(
            "\n===== CNR subpopulation classification =====\n"
            f"  source_mask : {cfg.source_mask}\n"
            f"  mode        : {cfg.mode}\n"
            f"  threshold   : {cfg.threshold}  (guided only)",
            flush=True,
        )

    def _print_cnr_report(self, report) -> None:
        """Print the classification decision trail to the terminal (debug)."""
        dip = report.get("dip_cnr", {})
        pct = report.get("cnr_percentiles", {})
        print(
            f"  decision    : {report.get('decision')}\n"
            f"  foci        : {report.get('n_components_valid')} valid / "
            f"{report.get('n_components_total')} total\n"
            f"  dip (logCNR): method={dip.get('method')} p={dip.get('pvalue')} "
            f"bimodal={dip.get('bimodal')} reliable={dip.get('reliable')}\n"
            f"  CNR pct     : {pct}\n"
            f"  candidate th: {report.get('candidate_cnr_threshold')}\n"
            f"  mode        : {report.get('mode')}\n"
            f"  group sizes : {report.get('group_sizes')} "
            f"(smaller frac {report.get('smaller_group_fraction')})\n"
            f"  warnings    : {report.get('warnings')}",
            flush=True,
        )

    def _resolve_cnr_inputs(self):
        """Shared pre-flight for the CNR tools (classify + interactive segmenter).

        Validates an open dataset/viewer, an active channel image, single-frame
        (per-cell σ), an active segmentation matching the channel, and a selected
        source mask read from the store. Returns ``(image, labels, feature_mask,
        cfg)`` or ``None`` after setting a status message on any failure.
        """
        viewer_win = self._get_viewer_window()
        if viewer_win is None or viewer_win.viewer is None:
            self._show_status("Open a dataset in the viewer first")
            return None
        store = self._get_store()
        if store is None:
            self._show_status("No dataset loaded")
            return None

        channel = self.data_model.session.active_channel
        if not channel:
            self._show_status("Select a channel in the Session window first")
            return None
        image = self._find_layer_data(viewer_win, "Image", channel)
        if image is None:
            self._show_status(f"Channel '{channel}' not found in viewer")
            return None

        # Per-cell σ ⇒ single-frame only (mirrors the per-cell detector modes).
        n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)
        if image.ndim == 3 and n_timepoints > 1:
            self._show_status("CNR tools support single-frame channels only")
            return None

        seg = self.data_model.session.active_segmentation
        if not seg:
            self._show_status("CNR tools need an active segmentation")
            return None
        labels = self._find_layer_data(viewer_win, "Labels", seg)
        if labels is None:
            self._show_status(f"Segmentation '{seg}' not found in viewer")
            return None
        if labels.shape != image.shape:
            self._show_status("Segmentation and channel shapes differ")
            return None

        cfg = self._cnr_settings.current_config()
        if not cfg.source_mask:
            self._show_status("Select a source mask")
            return None
        # Read the saved feature mask from the store (a /masks/<name> renders as a
        # napari Labels layer, so _find_layer_data has no apt 'mask' kind).
        try:
            feature_mask = store.read_mask(cfg.source_mask)
        except Exception as e:  # noqa: BLE001 — surface any read failure
            self._show_status(f"Could not read mask '{cfg.source_mask}': {e}")
            return None
        if np.asarray(feature_mask).shape != image.shape:
            self._show_status("Source mask and channel shapes differ")
            return None
        return image, labels, feature_mask, cfg

    def _on_classify(self) -> None:
        resolved = self._resolve_cnr_inputs()
        if resolved is None:
            return
        image, labels, feature_mask, cfg = resolved
        store = self._get_store()

        existing = store.list_masks() if hasattr(store, "list_masks") else []
        base_name = prompt_for_resource_name(
            self,
            title="Save CNR Populations",
            label="Base mask name:",
            default=f"{cfg.source_mask}_cnr",
            existing_names=existing,
        )
        if base_name is None:
            return

        self._pending_classify_base = base_name
        self._classify_btn.setEnabled(False)
        self._run_btn.setEnabled(False)
        self._settings.set_enabled(False)
        self._cnr_settings.set_enabled(False)
        self._print_cnr_settings_debug(cfg)
        self._show_status(
            f"Classifying '{cfg.source_mask}' by CNR ({cfg.mode})..."
        )

        from percell4.gui.workers import Worker

        self._cnr_worker = Worker(
            run_cnr_classification,
            image,
            feature_mask,
            labels,
            mode=cfg.mode,
            threshold=cfg.threshold,
        )
        self._cnr_worker.finished.connect(self._on_classify_done)
        self._cnr_worker.error.connect(self._on_classify_error)
        self._cnr_worker.start()

    def _unlock_after_classify(self) -> None:
        self._classify_btn.setEnabled(True)
        self._run_btn.setEnabled(True)
        self._settings.set_enabled(True)
        self._cnr_settings.set_enabled(True)

    def _on_classify_error(self, err) -> None:
        self._pending_classify_base = None
        self._unlock_after_classify()
        self._show_status(f"CNR classification error: {err.exc_type}: {err.message}")

    def _on_classify_done(self, result) -> None:
        pop_masks, components, report = result
        self._unlock_after_classify()
        self._print_cnr_report(report)

        base = self._pending_classify_base or "cnr"
        self._pending_classify_base = None

        if not pop_masks:
            self._show_status(
                f"CNR classification: no populations to save "
                f"({report.get('decision', '')})"
            )
            return

        # Creator: persist each population as its own {0,1} mask (store-before-layer
        # per AcceptPunctaMask; the panel owns the viewer add).
        from percell4.application.use_cases.accept_puncta_mask import AcceptPunctaMask

        viewer_win = self._get_viewer_window()
        written: list[tuple[str, int]] = []
        try:
            uc = AcceptPunctaMask(self._get_repo(), self.data_model.session)
            for suffix, m in pop_masks:
                name = f"{base}{suffix}"
                res = uc.execute(m, name)
                if viewer_win is not None:
                    viewer_win.add_mask(np.asarray(m, dtype=np.uint8), name=name)
                written.append((name, res.n_positive))
        except Exception as e:  # noqa: BLE001 — surface any persist failure
            self._show_status(f"Failed to save population mask: {e}")
            return

        # Per-focus CNR table -> its own /classification/<base> group (store, not
        # the repo port — write_dataframe lives on DatasetStore).
        table_note = ""
        store = self._get_store()
        try:
            import pandas as pd

            df = pd.DataFrame(components)
            if store is not None and not df.empty:
                store.write_dataframe(f"/classification/{base}", df)
                table_note = f"; table /classification/{base} ({len(df)} foci)"
        except Exception as e:  # noqa: BLE001 — table is secondary; report but don't fail the masks
            table_note = f"; table write failed: {e}"

        self._refresh_cnr_masks()
        summary = ", ".join(f"'{n}' {npos:,} px" for n, npos in written)
        self._show_status(f"CNR populations saved: {summary}{table_note}")

    # ── Interactive CNR segmenter (Action) ────────────────────────

    def _on_segment_cnr(self) -> None:
        """Open the interactive CNR histogram segmenter for the selected mask.

        Shares the CNR source-mask selector + pre-flight with the auto classifier;
        measures per-focus CNR off-thread, then opens :class:`CnrSegmenterWindow`.
        """
        resolved = self._resolve_cnr_inputs()
        if resolved is None:
            return
        image, labels, feature_mask, cfg = resolved

        self._pending_segment_source = cfg.source_mask
        self._segment_btn.setEnabled(False)
        self._show_status(f"Measuring CNR for '{cfg.source_mask}'...")

        from percell4.gui.workers import Worker

        self._measure_worker = Worker(run_cnr_measure, image, feature_mask, labels)
        self._measure_worker.finished.connect(self._on_measure_done)
        self._measure_worker.error.connect(self._on_measure_error)
        self._measure_worker.start()

    def _on_measure_error(self, err) -> None:
        self._segment_btn.setEnabled(True)
        self._show_status(f"CNR measurement error: {err.exc_type}: {err.message}")

    def _on_measure_done(self, result) -> None:
        records, component_labels = result
        self._segment_btn.setEnabled(True)
        valid = [r for r in records if np.isfinite(r.get("cnr", float("nan"))) and r["cnr"] > 0]
        if not valid:
            self._show_status("No foci with a measurable CNR to segment")
            return

        from percell4.gui.cnr_segmenter import CnrSegmenterWindow

        source = getattr(self, "_pending_segment_source", "adaptive")
        self._cnr_segmenter = CnrSegmenterWindow(
            records=records,
            component_labels=component_labels,
            source_mask=source,
            get_viewer_window=self._get_viewer_window,
            get_store=self._get_store,
            get_repo=self._get_repo,
            session=self.data_model.session,
            show_status=self._show_status_cb,
        )
        self._cnr_segmenter.show()
        # New segment masks become available as CNR sources once saved; refresh
        # the source list when the segmenter window closes.
        self._cnr_segmenter.destroyed.connect(lambda *_: self._refresh_cnr_masks())
        self._show_status(f"CNR segmenter open for '{source}' ({len(valid)} foci)")
