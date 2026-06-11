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


def run_adaptive_detection(image, gaussian_sigma, settings, auto_window):
    """Worker body: optionally Otsu-estimate the window, then detect.

    Returns ``(mask uint8, window_used int)``. When ``auto_window`` is True the
    window is estimated from an Otsu first-pass (mean granule size) and the
    settings are rebuilt with it; otherwise the settings' window is used as-is.
    Pure (no Qt) so it is unit-testable and worker-safe.
    """
    from percell4.domain.measure.adaptive_clip import (
        detect_adaptive_whole_frame,
        estimate_adaptive_window,
        otsu_first_pass,
    )
    from percell4.domain.measure.thresholding import apply_gaussian_smoothing
    from percell4.workflows.models import PunctaDetectorSettings

    window_used = int(dict(settings.detector_params).get("window_px", 15))
    if auto_window:
        smoothed = apply_gaussian_smoothing(np.asarray(image, dtype=np.float32), gaussian_sigma)
        window_used = estimate_adaptive_window(otsu_first_pass(smoothed))
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


def run_adaptive_detection_stack(image, gaussian_sigma, settings, auto_window):
    """Worker body for a time-lapse ``(T, H, W)`` channel: detect each frame.

    Loops over the leading time axis, runs :func:`run_adaptive_detection` on each
    frame, and stacks the per-frame masks into ``(T, H, W)``. The auto window is
    estimated per frame (contract D3), so frames with different intensity stats
    get their own window. Mirrors ``segmentation_panel.run_cellpose_stack``'s
    per-frame dispatch. Returns ``(mask (T,H,W) uint8, windows list[int])``.
    Pure (no Qt) so it is unit-testable and worker-safe.
    """
    image = np.asarray(image)
    frames: list[np.ndarray] = []
    windows: list[int] = []
    for t in range(image.shape[0]):
        mask_t, window_t = run_adaptive_detection(
            image[t], gaussian_sigma, settings, auto_window
        )
        frames.append(np.asarray(mask_t, dtype=np.uint8))
        windows.append(int(window_t))
    return np.stack(frames, axis=0), windows


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

        layout.addStretch()

    # ── helpers ──────────────────────────────────────────────────

    def _show_status(self, msg: str) -> None:
        self._status.setText(msg)
        self._show_status_cb(msg)

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

        image = None
        for layer in viewer_win.viewer.layers:
            if layer.__class__.__name__ == "Image" and layer.name == channel:
                image = np.asarray(layer.data)
                break
        if image is None:
            self._show_status(f"Channel '{channel}' not found in viewer")
            return
        # Time-lapse: a (T,H,W) channel layer is detected per frame (stacked to
        # a (T,H,W) mask), not sliced to the displayed frame. A 2D channel takes
        # the historical single-frame path.
        n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)
        is_timelapse = image.ndim == 3 and n_timepoints > 1

        config = self._settings.current_config()

        # Resolve the particle-size filter to a px area (µm² needs calibration).
        from percell4.domain.measure.adaptive_clip import resolve_min_area_px

        pixel_size_um = None
        try:
            pixel_size_um = store.metadata.get("pixel_size_um")
        except Exception:
            pixel_size_um = None
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
        self._run_btn.setEnabled(False)
        self._settings.set_enabled(False)
        n_frames = image.shape[0] if is_timelapse else 1
        detecting = "Detecting (auto window)..." if config.auto_window else "Detecting..."
        if is_timelapse:
            detecting = f"Detecting across {n_frames} timepoints..."
        self._show_status(detecting)

        from percell4.gui.workers import Worker

        worker_fn = run_adaptive_detection_stack if is_timelapse else run_adaptive_detection
        self._worker = Worker(
            worker_fn, image, config.gaussian_sigma, settings, config.auto_window
        )
        self._worker.finished.connect(self._on_detect_done)
        self._worker.error.connect(self._on_detect_error)
        self._worker.start()

    def _on_detect_error(self, err) -> None:
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

        if self._pending_auto:
            self._settings.set_window_value(window_display)

        if self._pending_auto:
            win_note = (
                f" (auto window {window_display}, per frame)"
                if is_stack
                else f" (auto window {window_display})"
            )
        else:
            win_note = ""
        self._show_status(f"Saved '{name}': {res.n_positive:,} px{win_note}")
