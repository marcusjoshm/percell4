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
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignTop)

        self._settings = AdaptiveClipSettingsWidget(self)
        # Selecting the "Otsu detect smallest particle size" method seeds the
        # d_min knob from an Otsu first-pass of the active channel (the widget has
        # no image, so the panel measures it).
        self._settings.otsu_detect_requested.connect(self._on_otsu_detect_requested)
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

        layout.addStretch()

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
            f"  window_px         : {config.window_px}\n"
            f"  k                 : {config.k}\n"
            f"  gaussian_sigma    : {config.gaussian_sigma}\n"
            f"  noise_estimator   : {config.noise_estimator}\n"
            f"  d_min_um (used)   : {config.d_min_um}\n"
            f"  min particle size : {config.min_size_value} {config.min_size_unit}",
            flush=True,
        )

    def _print_otsu_debug(self, image, labels, pixel_size_um: float, config) -> None:
        """Print the Otsu first-pass diagnostics for an Otsu-smallest run (debug).

        Recomputed fresh on the image being detected (not the auto-fill's earlier
        pass), restricted to in-cell pixels exactly as the auto-fill was.
        """
        from percell4.domain.measure.adaptive_clip import otsu_smallest_particle

        frame = image if image.ndim == 2 else image[0]
        lab = labels if labels.ndim == 2 else labels[0]
        cp_mask = (lab > 0) if lab.shape == frame.shape else None
        try:
            r = otsu_smallest_particle(frame, config.gaussian_sigma, pixel_size_um, cp_mask=cp_mask)
        except Exception as e:  # noqa: BLE001 — debug print must never break the run
            print(f"  [otsu first-pass] failed: {e}", flush=True)
            return
        if r is None:
            print("  [otsu first-pass] degenerate (no particle detected)", flush=True)
            return
        print(
            f"  [otsu first-pass] scope={r.scope}\n"
            f"    Otsu threshold               : {r.otsu_threshold:.4f}\n"
            f"    smallest particle            : {r.smallest_diameter_px:.3f} px"
            f" = {r.d_min_um:.4f} µm  (n_components={r.n_components})\n"
            f"    threshold-area mean intensity: {r.area_mean:.4f}\n"
            f"    mean − threshold             : {r.mean_minus_threshold:.4f}\n"
            f"    threshold-area max intensity : {r.area_max:.4f}\n"
            f"    threshold-area min intensity : {r.area_min:.4f}",
            flush=True,
        )

    def _on_otsu_detect_requested(self) -> None:
        """Auto-fill the smallest-particle Ø from an Otsu first-pass of the channel.

        Selecting "Otsu detect smallest particle size" seeds the d_min knob from
        the image so the user does not have to eyeball it. The knob is physical, so
        this needs a known pixel size; it restricts the Otsu pass to the active
        segmentation's cells when one is present (more robust than whole-frame). If
        a prerequisite is missing we leave the current Ø and say why.

        The gates here mirror the per-cell run's (``_run_particle_mode``): single
        frame only, and in-cell scope decided on the full label/image shape match —
        so the readout never promises an "(in-cell)" Ø the run would then refuse.

        Runs synchronously on the GUI thread (unlike the threaded Run path): the
        intended in-cell case is bounded (tens of ms). A large whole-frame channel
        with no active segmentation briefly blocks the event loop on selection.
        """
        viewer_win = self._get_viewer_window()
        store = self._get_store()
        if viewer_win is None or viewer_win.viewer is None or store is None:
            self._show_status("Open a dataset to auto-detect the smallest particle")
            return

        channel = self.data_model.session.active_channel
        image = self._find_layer_data(viewer_win, "Image", channel)
        if image is None:
            self._show_status("Select a channel to auto-detect the smallest particle")
            return

        pixel_size_um = self._pixel_size_um(store)
        if not pixel_size_um:
            self._show_status("Auto-detect needs a known pixel size (µm/px) on this dataset")
            return

        # Mirror the per-cell run gates so the readout never promises a value the
        # run will refuse: per-cell mode is single-frame only.
        n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)
        if image.ndim == 3 and n_timepoints > 1:
            self._show_status("Per-cell mode supports single-frame channels only")
            return

        frame = image if image.ndim == 2 else image[0]
        labels = self._find_layer_data(
            viewer_win, "Labels", self.data_model.session.active_segmentation
        )
        # In-cell scope is decided on the FULL shape match the run requires
        # (labels.shape == image.shape), not a per-frame slice.
        cp_mask = None
        if labels is not None and labels.shape == image.shape:
            cp_mask = (labels if labels.ndim == 2 else labels[0]) > 0

        from percell4.domain.measure.adaptive_clip import detect_smallest_particle_um

        cfg = self._settings.current_config()
        try:
            d_um = detect_smallest_particle_um(
                frame, cfg.gaussian_sigma, float(pixel_size_um), cp_mask=cp_mask
            )
        except Exception as e:  # noqa: BLE001 — surface any detection failure
            self._show_status(f"Auto-detect failed: {e}")
            return
        if d_um is None:
            self._show_status("Otsu first-pass found no particles; keeping current Ø")
            return

        self._settings.set_d_min_um(d_um)
        scope = "in-cell" if cp_mask is not None else "whole-frame"
        self._show_status(
            f"Otsu smallest particle ≈ {d_um:.3f} µm ({scope}) — tweak Ø if needed"
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

        # One-knob particle-size mode runs the per-cell detector (needs labels +
        # a known pixel size); the manual path below stays whole-frame.
        if config.particle_mode:
            self._run_particle_mode(config, image, is_timelapse, store, viewer_win)
            return

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
            detector_params={"window_px": config.window_px, "k": config.k},
            min_spot_px=max(1, int(min_spot_px)),
            spot_scale_prior=(1.0, 4.0),
        )

        self._pending_name = mask_name
        self._pending_auto = config.auto_window
        self._pending_particle = False  # this is the whole-frame/manual path
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

    def _run_particle_mode(self, config, image, is_timelapse, store, viewer_win) -> None:
        """Creator path for the one-knob particle-size detector (per-cell).

        Requires a known pixel size (the window is physical) and an active
        segmentation (σ is per-cell). Restricted to single-frame channels — the
        per-cell loop expects 2D image + 2D labels.
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
        self._pending_d_min = float(config.d_min_um)
        self._run_btn.setEnabled(False)
        self._settings.set_enabled(False)
        self._show_status(
            f"Detecting (smallest particle {config.d_min_um:g} µm, per-cell)..."
        )
        self._print_otsu_debug(image, labels, float(pixel_size_um), config)

        from percell4.gui.workers import Worker

        self._worker = Worker(
            run_adaptive_detection_by_particle_size,
            image,
            labels,
            float(pixel_size_um),
            float(config.d_min_um),
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
        self._show_status(f"Saved '{name}': {res.n_positive:,} px{win_note}")
