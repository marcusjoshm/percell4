"""Iterative Otsu Thresholding panel — interactive per-cell / whole-field peeling.

Exposes the iterative-Otsu method in the Analysis tab. A **Creator**: the Run
button peels the active channel within the active segmentation (per cell or over
the whole field), writes a new ``/masks/<name>`` layer, auto-selects it, and
shows it in napari. Heavy compute runs in a :class:`~percell4.gui.workers.Worker`.
Reads ``session.active_channel`` + ``session.active_segmentation`` directly; the
Run button writes only ``active_mask`` (via ``AcceptPunctaMask``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from percell4.gui._iterative_otsu_settings import IterativeOtsuSettingsWidget
from percell4.gui._resource_name_prompt import prompt_for_resource_name
from percell4.model import CellDataModel

logger = logging.getLogger(__name__)


def run_iterative_otsu(image, labels, gaussian_sigma, settings):
    """Worker body: smooth once, build unit masks per scope, peel.

    Returns ``(mask uint8, IterativeOtsuReport)``. Unit masks come from
    ``labels`` per ``settings.scope`` (``per-cell`` -> one unit per label;
    ``whole-field`` -> a single all-cells unit). Pure (no Qt) so it is
    unit-testable and worker-safe.
    """
    from percell4.domain.measure.iterative_otsu import IterativeOtsuReport, peel
    from percell4.domain.measure.thresholding import apply_gaussian_smoothing

    img = np.asarray(image, dtype=np.float32)
    smoothed = apply_gaussian_smoothing(img, gaussian_sigma)
    lbl = np.asarray(labels)

    if settings.scope == "whole-field":
        field = lbl > 0
        units = [field] if field.any() else []
    else:  # per-cell
        units = [lbl == i for i in np.unique(lbl) if int(i) != 0]

    if not units:
        return np.zeros(img.shape, dtype=np.uint8), IterativeOtsuReport(0, 0, 0, {}, 0)
    return peel(smoothed, units, settings)


class IterativeOtsuPanel(QWidget):
    """Interactive Iterative Otsu Thresholding panel (Creator)."""

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
        self._pending_max_rounds = 0
        self._pending_fixed: int | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignTop)

        self._settings = IterativeOtsuSettingsWidget(self)
        layout.addWidget(self._settings)

        from percell4.gui import theme

        self._run_btn = QPushButton("Run Iterative Otsu")
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

        session = self.data_model.session
        channel = session.active_channel
        if not channel:
            self._show_status("Select a channel in the Session window first")
            return

        # Segmentation guards — distinguish "none exists" from "none selected".
        seg_names = store.list_labels() if hasattr(store, "list_labels") else []
        if not seg_names:
            self._show_status("No segmentation found — run Cellpose (Segment tab) first")
            return
        seg = session.active_segmentation
        if not seg:
            self._show_status("Select a segmentation in the Session window first")
            return

        image = None
        for layer in viewer_win.viewer.layers:
            if layer.__class__.__name__ == "Image" and layer.name == channel:
                image = np.asarray(layer.data)
                break
        if image is None:
            self._show_status(f"Channel '{channel}' not found in viewer")
            return

        timepoint = None
        if image.ndim == 3:  # time-lapse: peel the currently-displayed frame
            timepoint = int(viewer_win.viewer.dims.current_step[0])
            image = image[timepoint]

        try:
            labels = store.read_labels(seg, timepoint=timepoint)
        except Exception as e:  # noqa: BLE001 — surface a read failure to the user
            self._show_status(f"Could not read segmentation '{seg}': {e}")
            return

        config = self._settings.current_config()
        if config.fixed_iterations is None and not config.stop_criteria:
            self._show_status("Enable at least one stopping criterion")
            return

        from percell4.workflows.models import IterativeOtsuSettings

        try:
            settings = IterativeOtsuSettings(
                scope=config.scope,
                dilation_radius_px=config.dilation_radius_px,
                max_rounds=config.max_rounds,
                stop_criteria=config.stop_criteria,
                stop_params=config.stop_params,
                stop_combine=config.stop_combine,
                fixed_iterations=config.fixed_iterations,
            )
        except ValueError as e:
            self._show_status(str(e))
            return

        existing = store.list_masks() if hasattr(store, "list_masks") else []
        mask_name = prompt_for_resource_name(
            self,
            title="Save Iterative Otsu Mask",
            label="Mask name:",
            default="iterative_otsu",
            existing_names=existing,
        )
        if mask_name is None:
            return

        self._pending_name = mask_name
        self._pending_fixed = config.fixed_iterations
        self._pending_max_rounds = (
            config.fixed_iterations if config.fixed_iterations is not None else config.max_rounds
        )
        self._run_btn.setEnabled(False)
        self._settings.set_enabled(False)
        frame_note = f" (frame {timepoint})" if timepoint is not None else ""
        if config.fixed_iterations is not None:
            count_note = f"exactly {config.fixed_iterations} iterations/cell (criteria off)"
        else:
            count_note = f"up to {config.max_rounds} iterations/cell"
        self._show_status(f"Running iterative Otsu — {count_note}…{frame_note}")

        from percell4.gui.workers import Worker

        self._worker = Worker(run_iterative_otsu, image, labels, config.gaussian_sigma, settings)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_detect_error)
        self._worker.start()

    def _on_detect_error(self, err) -> None:
        self._run_btn.setEnabled(True)
        self._settings.set_enabled(True)
        self._show_status(f"Error: {err.exc_type}: {err.message}")

    def _on_done(self, result) -> None:
        mask, report = result
        self._run_btn.setEnabled(True)
        self._settings.set_enabled(True)

        name = self._pending_name or "iterative_otsu"
        try:
            from percell4.application.use_cases.accept_puncta_mask import AcceptPunctaMask

            repo = self._get_repo()
            AcceptPunctaMask(repo, self.data_model.session).execute(mask, name)
        except Exception as e:  # noqa: BLE001 — surface any persist failure to the user
            self._show_status(f"Failed to save mask: {e}")
            return

        viewer_win = self._get_viewer_window()
        if viewer_win is not None:
            viewer_win.add_mask(np.asarray(mask, dtype=np.uint8), name=name)

        if report.n_positive == 0:
            self._show_status(
                f"Saved '{name}': no foreground detected ({report.n_iterations_run} iters)"
            )
        elif self._pending_fixed is not None:
            self._show_status(
                f"Saved '{name}': {report.n_positive:,} px, {report.n_iterations_run} iters "
                f"(fixed {self._pending_fixed}/cell, criteria off)"
            )
        else:
            self._show_status(
                f"Saved '{name}': {report.n_positive:,} px, {report.n_iterations_run} iters, "
                f"{report.units_hit_max_rounds}/{report.units_total} units hit the "
                f"{self._pending_max_rounds}-cap"
            )
