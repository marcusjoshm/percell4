"""Interactive "Segment by metric" module (analysis tab).

Splits a saved mask's particles into two populations (``<base>_low`` /
``<base>_high``) by a user-selected per-particle metric — edge-skirt ratio,
size, intensity, or CNR — with a live histogram + draggable threshold. A
generalization of "Segment by CNR (interactive)": the histogram window is the
shared :class:`~percell4.gui.cnr_segmenter.MetricSegmenterWindow`; the metric is
measured off-thread by :func:`run_metric_measure` (or ``_stack`` for a
``(T,H,W)`` channel) via
:func:`percell4.domain.measure.metric_segmentation.measure_metric_per_particle`.

Substrate: the sharpness/size/intensity metrics are measured over the per-cell
particle substrate (matching ``particles.csv``); CNR uses the global substrate
(same as the standalone CNR button). See the emitter module for details.

State ownership: this is a **Creator** — only the window's Save writes
``session.active_mask`` (via ``AcceptPunctaMask``); the metric picker, the
source-mask combo, and the window's divider/axis controls are Actions that write
no session field.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
from qtpy.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.domain.measure.metric_segmentation import SEGMENT_METRICS
from percell4.gui import theme
from percell4.model import CellDataModel

# Friendly picker labels -> emitter metric keys (a subset validated against
# SEGMENT_METRICS at construction). The two weak sharpness metrics
# (boundary_gradient / laplacian_variance) are intentionally not offered.
_METRIC_CHOICES: list[tuple[str, str]] = [
    ("Edge-skirt ratio (focus)", "edge_skirt_ratio"),
    ("Area / size", "area"),
    ("Mean intensity", "mean_intensity"),
    ("Max intensity", "max_intensity"),
    ("Integrated intensity", "integrated_intensity"),
    ("CNR (contrast)", "cnr"),
]


def run_metric_measure(image, feature_mask, labels, metric, pixel_size_um):
    """Worker body: measure ``metric`` per particle for a single frame.

    Returns ``(records, component_labels, excluded, metric)``. Pure (no Qt /
    store) so it is worker-safe.
    """
    from percell4.domain.measure.metric_segmentation import (
        measure_metric_per_particle,
    )

    records, comp, excluded = measure_metric_per_particle(
        image, feature_mask, labels, metric, pixel_size_um=pixel_size_um
    )
    return records, comp, excluded, metric


def run_metric_measure_stack(image, feature_mask, labels, metric, pixel_size_um):
    """Worker body: time-lapse ``(T,H,W)`` variant (pooled histogram)."""
    from percell4.domain.measure.metric_segmentation import (
        measure_metric_per_particle_stack,
    )

    records, comp, excluded = measure_metric_per_particle_stack(
        image, feature_mask, labels, metric, pixel_size_um=pixel_size_um
    )
    return records, comp, excluded, metric


class MetricSegmenterPanel(QWidget):
    """Source-mask + metric picker + launch button for the metric segmenter."""

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
        self._measure_worker = None
        self._window = None
        self._pending_source: str | None = None
        # Guard: only offer metrics the emitter actually supports.
        self._choices = [(lbl, key) for lbl, key in _METRIC_CHOICES if key in SEGMENT_METRICS]
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel(
            "Split a saved mask's particles into two populations\n"
            "(low / high) by a per-particle metric, with a live\n"
            "histogram + draggable threshold."
        ))

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Source mask:"))
        self._source = QComboBox()
        self._source.setToolTip("The saved /masks/<name> whose particles to split.")
        src_row.addWidget(self._source)
        layout.addLayout(src_row)

        metric_row = QHBoxLayout()
        metric_row.addWidget(QLabel("Metric:"))
        self._metric = QComboBox()
        self._metric.addItems([lbl for lbl, _ in self._choices])
        self._metric.setToolTip(
            "Metric to histogram + threshold. Edge-skirt ratio is the validated "
            "focus axis (low = in-focus). Size/intensity/CNR also available."
        )
        # Wire the picker so a mid-session change is not a silent no-op: any open
        # window (in the previous metric's units) is closed so its stale divider
        # can never be reinterpreted against a different axis.
        self._metric.currentIndexChanged.connect(self._on_metric_changed)
        metric_row.addWidget(self._metric)
        layout.addLayout(metric_row)

        self._segment_btn = QPushButton("Segment by metric (interactive)")
        self._segment_btn.setStyleSheet(
            f"QPushButton {{ background-color: {theme.ACTION_GREEN}; color: white;"
            f" padding: 8px; font-weight: bold; border-radius: 4px; }}"
            f" QPushButton:hover {{ background-color: {theme.ACTION_GREEN_HOVER}; }}"
        )
        self._segment_btn.clicked.connect(self._on_segment)
        layout.addWidget(self._segment_btn)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(self._status)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._refresh_masks()

    def _refresh_masks(self) -> None:
        store = self._get_store()
        names = (
            store.list_masks()
            if store is not None and hasattr(store, "list_masks")
            else []
        )
        current = self._source.currentText()
        self._source.blockSignals(True)
        self._source.clear()
        self._source.addItems([str(n) for n in (names or [])])
        if current:
            idx = self._source.findText(current)
            if idx >= 0:
                self._source.setCurrentIndex(idx)
        self._source.blockSignals(False)

    def _selected_metric(self) -> str:
        idx = self._metric.currentIndex()
        return self._choices[idx][1] if 0 <= idx < len(self._choices) else "edge_skirt_ratio"

    def _selected_metric_label(self) -> str:
        idx = self._metric.currentIndex()
        return self._choices[idx][0] if 0 <= idx < len(self._choices) else "metric"

    # ── helpers ──────────────────────────────────────────────────

    def _show_status(self, msg: str) -> None:
        self._status.setText(msg)
        self._show_status_cb(msg)

    def _find_layer_data(self, viewer_win, kind: str, name: str | None):
        if not name or viewer_win is None or viewer_win.viewer is None:
            return None
        for layer in viewer_win.viewer.layers:
            if layer.__class__.__name__ == kind and layer.name == name:
                return np.asarray(layer.data)
        return None

    def _pixel_size_um(self, store):
        try:
            px = float(store.metadata.get("pixel_size_um"))
        except (TypeError, ValueError, AttributeError):
            return None
        return px if np.isfinite(px) and px > 0 else None

    def _resolve_inputs(self):
        """Shared pre-flight: active channel image, active segmentation, and the
        selected source mask (read from the store), all shape-checked. Returns
        ``(image, labels, feature_mask, source_mask)`` or ``None`` after a status.
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

        seg = self.data_model.session.active_segmentation
        if not seg:
            self._show_status("Segment-by-metric needs an active segmentation")
            return None
        labels = self._find_layer_data(viewer_win, "Labels", seg)
        if labels is None:
            self._show_status(f"Segmentation '{seg}' not found in viewer")
            return None
        if labels.shape != image.shape:
            self._show_status("Segmentation and channel shapes differ")
            return None

        source = self._source.currentText() if self._source.count() else ""
        if not source:
            self._show_status("Select a source mask")
            return None
        try:
            feature_mask = store.read_mask(source)
        except Exception as e:  # noqa: BLE001 — surface any read failure
            self._show_status(f"Could not read mask '{source}': {e}")
            return None
        if np.asarray(feature_mask).shape != image.shape:
            self._show_status("Source mask and channel shapes differ")
            return None
        return image, labels, feature_mask, source

    # ── metric change ────────────────────────────────────────────

    def _on_metric_changed(self, _index: int) -> None:
        # Close any open window (its divider is in the old metric's units).
        if self._window is not None:
            try:
                self._window.close()
            except Exception:  # noqa: BLE001 — window may already be gone
                pass
            self._window = None
        self._show_status(
            f"Metric set to {self._selected_metric_label()} — click "
            f"'Segment by metric' to measure."
        )

    # ── run ──────────────────────────────────────────────────────

    def _on_segment(self) -> None:
        resolved = self._resolve_inputs()
        if resolved is None:
            return
        image, labels, feature_mask, source = resolved
        metric = self._selected_metric()
        px = self._pixel_size_um(self._get_store())

        self._pending_source = source
        self._segment_btn.setEnabled(False)
        is_timelapse = np.asarray(image).ndim == 3
        worker_fn = run_metric_measure_stack if is_timelapse else run_metric_measure
        tl = f" across {np.asarray(image).shape[0]} timepoints" if is_timelapse else ""
        self._show_status(
            f"Measuring {self._selected_metric_label()} for '{source}'{tl}..."
        )

        from percell4.gui.workers import Worker

        self._measure_worker = Worker(worker_fn, image, feature_mask, labels, metric, px)
        self._measure_worker.finished.connect(self._on_measure_done)
        self._measure_worker.error.connect(self._on_measure_error)
        self._measure_worker.start()

    def _on_measure_error(self, err) -> None:
        self._segment_btn.setEnabled(True)
        self._show_status(f"Metric measurement error: {err.exc_type}: {err.message}")

    def _on_measure_done(self, result) -> None:
        records, component_labels, excluded, metric = result
        self._segment_btn.setEnabled(True)

        if not records:
            # Channel/mask guard (R9): no measurable particles. If some were
            # excluded, the metric is (mostly) NaN for the active channel — likely
            # the source mask was detected on a different channel.
            if excluded:
                self._show_status(
                    f"'{self._selected_metric_label()}' is NaN for every particle on "
                    f"the active channel ({excluded} excluded). Is the active channel "
                    f"the one this mask was detected on?"
                )
            else:
                self._show_status("No particles in the source mask to segment")
            return

        from percell4.gui.cnr_segmenter import MetricSegmenterWindow

        source = self._pending_source or "mask"
        label = self._selected_metric_label()
        self._window = MetricSegmenterWindow(
            records=records,
            component_labels=component_labels,
            source_mask=source,
            get_viewer_window=self._get_viewer_window,
            get_store=self._get_store,
            get_repo=self._get_repo,
            session=self.data_model.session,
            show_status=self._show_status_cb,
            value_key="value",
            metric_label=label,
            naming="lowhigh",
            single_divider=True,
            require_positive=False,
            excluded_count=int(excluded),
            preview_layer_name=f"{metric} segments (preview)",
            active_segment=2,  # auto-select the _high population (mechanical)
            save_default_suffix=f"_{metric}",
        )
        self._window.show()
        self._window.destroyed.connect(lambda *_: self._refresh_masks())
        note = f", {excluded} excluded" if excluded else ""
        self._show_status(
            f"{label} segmenter open for '{source}' ({len(records)} particles{note})"
        )
