"""Interactive CNR histogram segmenter (manual divider-based subpopulations).

A pop-up window showing a histogram of per-focus CNR with draggable vertical
**divider** lines. The user adds/removes any number of dividers to partition the
CNR axis into ``N+1`` segments; a **live multi-value labels overlay** in napari
recolors as the dividers move. A **linear/log CNR axis** checkbox toggles the
histogram scale (divider CNR values are preserved across the toggle), and a
status line shows the **current divider CNR value(s)** plus per-segment foci
counts live as they are dragged. On **Save**, each non-empty CNR segment is
written as its own ``{0,1}`` binary mask (``<base>_seg1 … <base>_segN``) via the
Creator path (:class:`~percell4.application.use_cases.accept_puncta_mask.AcceptPunctaMask`).

Sibling to the automatic "Classify Mask by CNR" modes (CNR threshold /
Auto Two Groups) — this
one gives the user direct control. Both consume :func:`measure_cnr`. The window
does only fast lookup-table work; the heavy CNR measurement runs off-thread in
the panel before the window opens.

Time-lapse: the window is **shape-agnostic**. When the panel passes a ``(T,H,W)``
``component_labels`` (globally-unique ids across frames) with the foci of all
timepoints pooled into ``records`` (see ``run_cnr_measure_stack``), the histogram is
built from every timepoint's foci at once, the divider threshold(s) apply to all frames
equally, and the live preview + saved ``<base>_segN`` masks come out ``(T,H,W)`` — no
window-side branching needed (``segment_label_image`` / ``segment_masks_from_label_image``
/ ``add_labels`` / ``add_mask`` all operate per-element on any rank).

Pattern: mirrors :mod:`percell4.gui.threshold_qc`'s separate-window + live
``add_labels`` preview + debounced update.
"""

from __future__ import annotations

import logging

import numpy as np
from qtpy.QtCore import QTimer
from qtpy.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.domain.measure.cnr_classification import (
    assign_segments,
    segment_label_image,
    segment_masks_from_label_image,
)
from percell4.gui import theme
from percell4.gui._resource_name_prompt import prompt_for_resource_name

logger = logging.getLogger(__name__)

PREVIEW_LAYER_NAME = "CNR segments (preview)"
_DEBOUNCE_MS = 50
_HIST_BINS = 60


def _segment_palette(n: int) -> dict:
    """A {None+0: transparent, 1..n: distinct RGBA} color dict for the overlay.

    The ``None`` key is napari's default color for unmapped labels (avoids a
    "color_dict did not provide a default color" warning).
    """
    color_dict: dict = {
        None: (0.0, 0.0, 0.0, 0.0),
        0: (0.0, 0.0, 0.0, 0.0),
    }
    try:
        from matplotlib import colormaps

        cmap = colormaps["tab10"] if n <= 10 else colormaps["nipy_spectral"]
        for i in range(1, n + 1):
            frac = (i - 1) / max(1, (10 if n <= 10 else n) - 1)
            r, g, b, _ = cmap(frac)
            color_dict[i] = (float(r), float(g), float(b), 1.0)
    except Exception:  # pragma: no cover - matplotlib always present here
        # Deterministic fallback palette.
        base = [(1, 0, 0), (0, 1, 0), (0, 0.5, 1), (1, 1, 0), (1, 0, 1), (0, 1, 1)]
        for i in range(1, n + 1):
            r, g, b = base[(i - 1) % len(base)]
            color_dict[i] = (float(r), float(g), float(b), 1.0)
    return color_dict


class CnrSegmenterWindow(QWidget):
    """Histogram + draggable dividers + live napari preview + per-segment save."""

    def __init__(
        self,
        *,
        records: list[dict],
        component_labels: np.ndarray,
        source_mask: str,
        get_viewer_window,
        get_store,
        get_repo,
        session,
        show_status=lambda _m: None,
        parent: QWidget | None = None,
        # ── generalization knobs (defaults reproduce the CNR behaviour) ──
        value_key: str = "cnr",
        metric_label: str = "CNR",
        naming: str = "seg",            # "seg" -> _seg{i}; "lowhigh" -> _low/_high
        single_divider: bool = False,   # metric mode: exactly one divider (2 pops)
        require_positive: bool = True,   # drop value <= 0 (CNR); metric drops NaN only
        excluded_count: int = 0,         # particles the emitter already dropped
        preview_layer_name: str = PREVIEW_LAYER_NAME,
        active_segment: int | None = None,  # which segment auto-selects on Save
        save_default_suffix: str = "_cnrseg",
    ) -> None:
        super().__init__(parent)
        self._get_viewer_window = get_viewer_window
        self._get_store = get_store
        self._get_repo = get_repo
        self._session = session
        self._show_status_cb = show_status
        self._source_mask = source_mask
        self._comp = np.asarray(component_labels)

        self._value_label = metric_label
        self._naming = naming
        self._single_divider = single_divider
        self._preview_layer_name = preview_layer_name
        self._active_segment = active_segment
        self._save_default_suffix = save_default_suffix
        self._excluded_count = int(excluded_count)

        def _ok(v: float) -> bool:
            return bool(np.isfinite(v)) and (v > 0 if require_positive else True)

        valid = [r for r in records if _ok(r.get(value_key, np.nan))]
        # ``self._cnr`` is the generic per-focus value array (kept named for
        # backward compatibility with existing references/tests).
        self._cnr = np.array([float(r[value_key]) for r in valid], dtype=float)
        self._focus_labels = np.array([int(r["label"]) for r in valid], dtype=np.int64)

        self._dividers: list = []          # pg.InfiniteLine instances
        self._plot = None
        self._bars = None                   # the histogram BarGraphItem (rebuilt on toggle)
        self._log_mode = False              # x-axis: linear value (False) or log10(value)
        self._update_pending = False
        self._last_n = 0                    # last segment count (colormap rebuild guard)

        self.setWindowTitle(f"{metric_label} segmenter — {source_mask}")
        self.setMinimumSize(560, 460)
        self.setStyleSheet(
            f"background-color: {theme.BACKGROUND}; color: {theme.TEXT_BRIGHT};"
        )
        self._build_ui()

        # One initial divider at the median (-> 2 segments), then preview.
        if self._cnr.size:
            self._add_divider(float(np.median(self._cnr)))
        self._update_preview()

    # ── UI ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        drag_word = "divider" if self._single_divider else "dividers"
        title = QLabel(
            f"Drag the {drag_word} to set the {self._value_label} "
            f"{'threshold' if self._single_divider else 'segment boundaries'}"
        )
        title.setStyleSheet(f"font-weight: bold; color: {theme.TEXT_BRIGHT};")
        layout.addWidget(title)

        try:
            import pyqtgraph as pg

            self._pg = pg
            plot = pg.PlotWidget()
            plot.setBackground(theme.BACKGROUND)
            plot.getAxis("bottom").enableAutoSIPrefix(False)
            plot.getAxis("left").enableAutoSIPrefix(False)
            plot.setLabel("left", "Foci")
            self._plot = plot
            self._rebuild_histogram()
            layout.addWidget(plot, stretch=1)
        except Exception:  # pragma: no cover - pyqtgraph is present in the GUI env
            self._pg = None
            layout.addWidget(QLabel("(pyqtgraph not available for the histogram)"))

        # Buttons. In single-divider (metric) mode the add/remove-divider
        # affordances are hidden — the tool always produces exactly two
        # populations (low/high).
        btn_row = QHBoxLayout()
        if not self._single_divider:
            add_btn = QPushButton("Add divider")
            add_btn.clicked.connect(lambda: self._add_divider())
            btn_row.addWidget(add_btn)
            rm_btn = QPushButton("Remove divider")
            rm_btn.clicked.connect(self._remove_divider)
            btn_row.addWidget(rm_btn)
        self._log_check = QCheckBox(f"Log {self._value_label} axis")
        self._log_check.setToolTip(
            f"Show the {self._value_label} axis on a log scale (helpful when the "
            f"metric is heavy-tailed). Divider values are preserved across the toggle."
        )
        self._log_check.toggled.connect(self._on_log_toggled)
        btn_row.addWidget(self._log_check)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        action_row = QHBoxLayout()
        action_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        action_row.addWidget(close_btn)
        self._save_btn = QPushButton("Save segments")
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background-color: {theme.ACTION_GREEN}; color: white;"
            f" padding: 6px 14px; font-weight: bold; border-radius: 4px; }}"
            f" QPushButton:hover {{ background-color: {theme.ACTION_GREEN_HOVER}; }}"
        )
        self._save_btn.clicked.connect(self._on_save)
        action_row.addWidget(self._save_btn)
        layout.addLayout(action_row)

    # ── axis (linear CNR <-> log10) ──────────────────────────────

    def _to_plot(self, cnr: float) -> float:
        """CNR value -> plot x-coordinate (identity, or log10 in log mode)."""
        return float(np.log10(cnr)) if self._log_mode else float(cnr)

    def _to_cnr(self, x: float) -> float:
        """Plot x-coordinate -> CNR value (identity, or 10**x in log mode)."""
        return float(10.0**x) if self._log_mode else float(x)

    def _rebuild_histogram(self) -> None:
        """(Re)draw the CNR histogram for the current axis mode."""
        if self._plot is None or self._pg is None:
            return
        if self._bars is not None:
            self._plot.removeItem(self._bars)
            self._bars = None
        label = f"log₁₀({self._value_label})" if self._log_mode else self._value_label
        self._plot.setLabel("bottom", label)
        if not self._cnr.size:
            return
        data = np.log10(self._cnr) if self._log_mode else self._cnr
        counts, edges = np.histogram(data, bins=_HIST_BINS)
        centers = (edges[:-1] + edges[1:]) / 2.0
        width = (edges[1] - edges[0]) * 0.95
        self._bars = self._pg.BarGraphItem(
            x=centers, height=counts, width=width, brush=theme.ACCENT
        )
        self._plot.addItem(self._bars)

    def _on_log_toggled(self, checked: bool) -> None:
        # Preserve each divider's CNR value across the axis change.
        cnrs = [self._to_cnr(float(line.value())) for line in self._dividers]
        self._log_mode = bool(checked)
        self._rebuild_histogram()
        for line, cnr in zip(self._dividers, cnrs):
            line.setValue(float(self._to_plot(cnr)))
        self._update_preview()

    # ── dividers ─────────────────────────────────────────────────

    def _divider_positions(self) -> list[float]:
        """Sorted divider positions in **CNR** units (axis-mode independent)."""
        return sorted(self._to_cnr(float(line.value())) for line in self._dividers)

    def _n_segments(self) -> int:
        return len(self._dividers) + 1

    def _next_divider_pos(self) -> float:
        """Midpoint (CNR) of the widest current segment (or the CNR median if none)."""
        if not self._cnr.size:
            return 0.0
        if not self._dividers:
            return float(np.median(self._cnr))
        edges = [float(self._cnr.min()), *self._divider_positions(), float(self._cnr.max())]
        widest = max(range(len(edges) - 1), key=lambda i: edges[i + 1] - edges[i])
        return (edges[widest] + edges[widest + 1]) / 2.0

    def _add_divider(self, cnr: float | None = None) -> None:
        """Add a divider at ``cnr`` (CNR units; placed at the current axis coord)."""
        if self._plot is None or self._pg is None:
            return
        if cnr is None:
            cnr = self._next_divider_pos()
        line = self._pg.InfiniteLine(
            pos=float(self._to_plot(cnr)),
            angle=90,
            movable=True,
            pen=self._pg.mkPen(theme.TEXT_BRIGHT, width=2),
        )
        line.sigPositionChanged.connect(self._on_divider_moved)
        line.sigPositionChangeFinished.connect(self._update_preview)
        self._plot.addItem(line)
        self._dividers.append(line)
        self._update_preview()

    def _remove_divider(self) -> None:
        if not self._dividers:
            return
        line = self._dividers.pop()
        if self._plot is not None:
            self._plot.removeItem(line)
        self._update_preview()

    def _on_divider_moved(self) -> None:
        """A line is being dragged: update the CNR readout now, the mask debounced."""
        self._update_divider_readout()
        self._schedule_update()

    # ── live preview ─────────────────────────────────────────────

    def _schedule_update(self) -> None:
        if not self._update_pending:
            self._update_pending = True
            QTimer.singleShot(_DEBOUNCE_MS, self._update_preview)

    def _current_segment_image(self) -> np.ndarray:
        return segment_label_image(
            self._comp, self._focus_labels, self._cnr, self._divider_positions()
        )

    def _update_divider_readout(self) -> None:
        """Show the current divider CNR value(s) + per-segment foci counts.

        Cheap (digitize only) so it can run live on every drag, independent of the
        debounced mask rebuild. Divider positions are always reported in CNR units,
        regardless of the linear/log axis.
        """
        positions = self._divider_positions()
        n = self._n_segments()
        if self._focus_labels.size:
            seg = assign_segments(self._cnr, positions)
            counts = [int((seg == i).sum()) for i in range(1, n + 1)]
        else:
            counts = [0] * n
        div_txt = ", ".join(f"{p:.2f}" for p in positions) if positions else "(none)"
        seg_word = "low/high" if (self._single_divider and n == 2) else f"{n} segments"
        excl = (
            f"   |   {self._excluded_count} excluded (NaN)"
            if self._excluded_count
            else ""
        )
        self._status.setText(
            f"Dividers ({self._value_label}): {div_txt}   |   {seg_word}   |   "
            f"foci per segment: " + ", ".join(map(str, counts)) + excl
        )

    def _update_preview(self) -> None:
        self._update_pending = False
        n = self._n_segments()
        seg_img = self._current_segment_image()
        self._push_preview(seg_img, n)
        self._update_divider_readout()

    def _push_preview(self, seg_img: np.ndarray, n: int) -> None:
        viewer_win = self._get_viewer_window()
        if viewer_win is None or getattr(viewer_win, "existing_viewer", None) is None:
            return
        viewer = viewer_win.viewer
        existing = None
        for layer in viewer.layers:
            if getattr(layer, "name", None) == self._preview_layer_name:
                existing = layer
                break
        if existing is not None and n == self._last_n:
            existing.data = seg_img  # in-place fast path (same colormap)
            return
        # Colormap (re)build when the segment count changed, or first add.
        from napari.utils.colormaps import DirectLabelColormap

        cmap = DirectLabelColormap(color_dict=_segment_palette(n))
        if existing is not None:
            viewer.layers.remove(existing)
        viewer_win.add_labels(seg_img, name=self._preview_layer_name, colormap=cmap)
        self._last_n = n

    def _remove_preview_layer(self) -> None:
        viewer_win = self._get_viewer_window()
        if viewer_win is None or getattr(viewer_win, "existing_viewer", None) is None:
            return
        viewer = viewer_win.viewer
        for layer in list(viewer.layers):
            if getattr(layer, "name", None) == self._preview_layer_name:
                viewer.layers.remove(layer)

    # ── save (Creator) ───────────────────────────────────────────

    def _show_status(self, msg: str) -> None:
        self._status.setText(msg)
        self._show_status_cb(msg)

    def _on_save(self) -> None:
        n = self._n_segments()
        seg_img = self._current_segment_image()
        masks = segment_masks_from_label_image(seg_img, n)
        nonempty = [(i + 1, m) for i, m in enumerate(masks) if int(m.sum()) > 0]
        if not nonempty:
            self._show_status("No non-empty segments to save")
            return

        store = self._get_store()
        has_list = store is not None and hasattr(store, "list_masks")
        existing = store.list_masks() if has_list else []
        base = prompt_for_resource_name(
            self,
            title=f"Save {self._value_label} Segments",
            label="Base mask name:",
            default=f"{self._source_mask}{self._save_default_suffix}",
            existing_names=existing,
        )
        if base is None:
            return

        from percell4.application.use_cases.accept_puncta_mask import AcceptPunctaMask

        viewer_win = self._get_viewer_window()
        written: list[tuple[str, int]] = []
        # Auto-select exactly one written mask. When ``active_segment`` is set
        # (metric mode), only that segment selects; otherwise every write selects
        # (CNR mode — last-wins, preserving the original behaviour).
        try:
            uc = AcceptPunctaMask(self._get_repo(), self._session)
            for seg_id, m in nonempty:
                name = self._segment_mask_name(base, seg_id)
                select = True if self._active_segment is None else (
                    seg_id == self._active_segment
                )
                res = uc.execute(m, name, select=select)
                if viewer_win is not None:
                    viewer_win.add_mask(np.asarray(m, dtype=np.uint8), name=name)
                written.append((name, res.n_positive))
        except Exception as e:  # noqa: BLE001 — surface any persist failure
            self._show_status(f"Failed to save segment mask: {e}")
            return

        self._remove_preview_layer()
        summary = ", ".join(f"'{nm}' {npx:,} px" for nm, npx in written)
        self._show_status_cb(f"Saved {len(written)} {self._value_label} segments: {summary}")
        self.close()

    def _segment_mask_name(self, base: str, seg_id: int) -> str:
        """Output mask name for segment ``seg_id`` under the active naming scheme.

        ``lowhigh``: segment 1 -> ``<base>_low``, segment 2 -> ``<base>_high``.
        ``seg`` (CNR default): ``<base>_seg{seg_id}`` for any number of segments.
        """
        if self._naming == "lowhigh":
            return f"{base}_low" if seg_id == 1 else f"{base}_high"
        return f"{base}_seg{seg_id}"

    # ── lifecycle ────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._remove_preview_layer()
        super().closeEvent(event)


# The window is metric-agnostic; ``MetricSegmenterWindow`` is the name the
# Segment-by-Metric tool imports (CNR callers keep using ``CnrSegmenterWindow``).
MetricSegmenterWindow = CnrSegmenterWindow
