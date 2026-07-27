"""Adaptive Local Clipping panel — interactive two-pass auto-extraction.

Exposes the production `adaptive` puncta detector in the Analysis tab via the
single auto-extraction (two-pass) mode. A **Creator**: the Run button runs the
per-cell auto-extraction on the active channel (off the active segmentation),
writes a new ``/masks/<name>`` layer, auto-selects it, and shows it in napari.
Heavy compute runs in a :class:`~percell4.gui.workers.Worker`. Reads
``session.active_channel`` directly; the Run button writes only ``active_mask``
(via ``AcceptPunctaMask``).
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


def run_adaptive_auto_extract(
    image,
    labels,
    smallest_particle_px,
    presmooth_sigma_px,
    min_spot_px,
):
    """Worker body for the auto-extraction routine (per-cell).

    The two-pass :func:`percell4.domain.measure.auto_extraction.auto_extract` — a
    fine pass at k=1 (window = ``3 × smallest_particle_px``) plus, when the
    LoG-measured largest particle exceeds it, a coarse pass (window =
    ``FILL_FACTOR × largest``, k = the noise-symmetry floor), OR-unioned with
    hole-filling. ``min_spot_px`` filters the result. The coarse window ratio and
    false-positive rate are the eye-validated module constants ``FILL_FACTOR`` /
    ``FDR`` and are not parameterised here.

    ``smallest_particle_px`` may be ``None``, in which case ``auto_extract``
    LoG-measures it; the panel always supplies a value, but the helper stays
    tolerant so it remains a reusable pure surface. Returns ``(mask uint8,
    report)`` where ``report`` is the :class:`AutoExtractReport`. Pure (no Qt) so
    it is worker-safe.
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
    image,
    labels,
    smallest_particle_px,
    presmooth_sigma_px,
    min_spot_px,
):
    """Worker body for a time-lapse ``(T,H,W)`` channel: auto-extract each frame.

    Loops the leading time axis and runs :func:`run_adaptive_auto_extract` on each
    frame independently — so every timepoint gets its OWN largest-particle sizing,
    coarse window and noise floor (the multi-time-point 'treat each frame as its own
    image' behaviour). Stacks the per-frame masks into ``(T,H,W)``. A frame with no
    detectable particles yields an empty plane rather than aborting the whole run
    (R9: the dissolved end of a washout); reachable only when
    ``smallest_particle_px is None``, since a supplied diameter needs no
    measurement. Returns ``(mask (T,H,W) uint8, reports list[AutoExtractReport |
    None])``; a frame that degraded to empty has a ``None`` report. Pure (no Qt) so
    it is unit-testable and worker-safe.
    """
    from percell4.domain.measure.auto_extraction import NoParticlesFoundError

    image = np.asarray(image)
    labels = np.asarray(labels)
    frames: list[np.ndarray] = []
    reports: list = []
    for t in range(image.shape[0]):
        try:
            mask_t, report_t = run_adaptive_auto_extract(
                image[t],
                labels[t],
                smallest_particle_px,
                presmooth_sigma_px,
                min_spot_px,
            )
        except NoParticlesFoundError:
            # No particle to size this frame — a recoverable empty frame, not a failed
            # run (R9). Only reachable with an auto-detected smallest; genuine errors
            # propagate to the worker's error signal.
            mask_t = np.zeros(labels[t].shape, dtype=np.uint8)
            report_t = None
        frames.append(np.asarray(mask_t, dtype=np.uint8))
        reports.append(report_t)
    return np.stack(frames, axis=0), reports


def run_cnr_classification(image, feature_mask, labels, *, mode, threshold):
    """Worker body for CNR subpopulation classification (per-cell, pure).

    Maps the ``mode`` to :func:`classify_by_cnr`: ``"guided"`` → ``threshold=…``,
    ``"forced"`` → ``n_populations=2``, ``"discover"`` → defaults. The mapping is
    total: any other value (notably the GUI-only ``"interactive"``) raises, so a
    routing slip can never silently persist a discover-mode result. Splits the
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
    elif mode == "discover":
        res = classify_by_cnr(image, feature_mask, labels)
    else:
        raise ValueError(f"unknown CNR classification mode {mode!r}")

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


def run_cnr_classification_stack(image, feature_mask, labels, *, mode, threshold):
    """Worker body for time-lapse ``(T,H,W)`` CNR classification (per frame, pure).

    Classifies each timepoint independently via
    :func:`percell4.domain.measure.cnr_classification.classify_by_cnr_stack` — guided
    applies the **one shared** ``threshold`` to every frame. Returns the SAME
    ``(pop_masks: list[(suffix, mask)], components: list[dict], report: dict)`` contract as
    :func:`run_cnr_classification`, but the masks are ``(T,H,W)``:
    ``[("_low", low), ("_high", high)]`` when any frame splits, else ``[("", all-foci)]``
    (single population everywhere). The panel has no producing ALC round, so CNR presmooth
    stays at the 1px default (matching the panel detector default). Pure (no Qt / store).
    """
    from percell4.domain.measure.cnr_classification import classify_by_cnr_stack

    if mode not in ("guided", "forced", "discover"):
        raise ValueError(f"unknown CNR classification mode {mode!r}")
    thr = float(threshold) if (mode == "guided" and threshold is not None) else None
    res = classify_by_cnr_stack(image, feature_mask, labels, mode=mode, threshold=thr)
    n_frames = len(res.per_frame)
    n_split = sum(1 for f in res.per_frame if f["n_subpopulations"] >= 2)
    if n_split > 0:
        pop_masks = [("_low", res.low_stack), ("_high", res.high_stack)]
    else:
        # Single population in every frame: all foci in one mask under the base name.
        pop_masks = [("", res.low_stack)]
    pop_masks = [(suffix, m) for suffix, m in pop_masks if int(m.sum()) > 0]
    components = res.table.to_dict("records")  # res.table is always a DataFrame
    report = {
        "decision": f"time-lapse CNR: {n_split}/{n_frames} timepoint(s) split",
        "mode": mode,
        "n_components_total": len(components),
        "n_timepoints": n_frames,
    }
    return pop_masks, components, report


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


def run_cnr_measure_stack(image, feature_mask, labels):
    """Worker body for the interactive segmenter on a time-lapse ``(T,H,W)`` channel.

    Measures per-focus CNR for **every frame** and POOLS the foci into one flat record
    list, offsetting each frame's component ids so they are globally unique across the
    stack. Returns ``(records, component_labels)`` where ``component_labels`` is a
    ``(T,H,W)`` label image whose ids match the pooled records' ``label`` field — so the
    shape-agnostic :class:`~percell4.gui.cnr_segmenter.CnrSegmenterWindow` builds ONE
    histogram from all timepoints and applies the divider threshold(s) to every frame
    (its segment image + saved masks come out ``(T,H,W)``). Each record also carries its
    ``timepoint``. Mirrors :func:`run_cnr_measure`'s per-frame ``measure_cnr`` + matching
    ``scipy.ndimage.label`` so the record/label alignment that the window relies on holds
    globally. Pure (no Qt / store) so it is worker-safe and unit-testable.
    """
    from scipy.ndimage import label

    from percell4.domain.measure.cnr_classification import measure_cnr

    image = np.asarray(image)
    feature_mask = np.asarray(feature_mask)
    labels = np.asarray(labels)
    records: list[dict] = []
    comp_frames: list[np.ndarray] = []
    offset = 0
    for t in range(image.shape[0]):
        recs = measure_cnr(image[t], feature_mask[t], labels[t])
        comp_t, n_t = label(feature_mask[t] > 0)  # scipy returns int32
        comp_t[comp_t > 0] += offset  # globally-unique ids across frames (total ids ≪ 2³¹)
        for r in recs:
            r = dict(r)
            r["timepoint"] = int(t)
            if int(r.get("label", 0)) > 0:
                r["label"] = int(r["label"]) + offset
            records.append(r)
        comp_frames.append(comp_t)
        offset += int(n_t)
    component_labels = np.stack(comp_frames, axis=0)
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
        # CNR classification (separate Action path — its own worker + pending state
        # so it never collides with the detection run's _pending_* flags).
        self._cnr_worker = None
        self._pending_classify_base: str | None = None
        # Interactive CNR segmenter (separate worker + held window reference).
        self._measure_worker = None
        self._cnr_segmenter = None
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
        # The label is fixed, but what the click DOES depends on the mode, so the
        # tooltip tracks it (Interactive opens a window and saves nothing yet).
        self._cnr_settings.config_changed.connect(self._update_classify_tooltip)
        self._update_classify_tooltip()

        layout.addStretch()

    def _update_classify_tooltip(self) -> None:
        """Keep the green button's tooltip in step with the selected mode."""
        mode = self._cnr_settings.current_config().mode
        if mode == "interactive":
            tip = (
                "Opens a histogram of every particle's contrast with draggable "
                "dividers and a live preview. Nothing is saved until you save "
                "from that window."
            )
        elif mode == "forced":
            tip = (
                "Splits the source mask into exactly two populations and saves "
                "them as new masks."
            )
        else:
            tip = (
                "Splits the source mask at the CNR threshold above and saves the "
                "populations as new masks."
            )
        self._classify_btn.setToolTip(tip)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Refresh the CNR source-mask list when the panel becomes visible."""
        super().showEvent(event)
        self._refresh_cnr_masks()

    def _on_cnr_segmenter_destroyed(self, *_args) -> None:
        """Slot for the CNR segmenter window's ``destroyed`` signal.

        Exists as a named method rather than a lambda so Qt ties the connection
        to this panel's lifetime and drops it when the panel is destroyed.
        """
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
        """Print the Adaptive Local Clipping (auto-extraction) settings (debug)."""
        print(
            "\n===== Adaptive Local Clipping run =====\n"
            f"  gaussian_sigma             : {config.gaussian_sigma}\n"
            f"  smallest particle diameter : {config.smallest_particle_value} "
            f"{config.smallest_particle_unit}\n"
            f"  min particle area          : {config.min_size_value} {config.min_size_unit}",
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
        # Time-lapse: a (T,H,W) channel layer is auto-extracted per frame (stacked
        # to a (T,H,W) mask), not sliced to the displayed frame. A 2D channel takes
        # the historical single-frame path.
        n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)
        is_timelapse = image.ndim == 3 and n_timepoints > 1

        config = self._settings.current_config()
        self._print_settings_debug(config)

        # Auto extraction (two-pass) is the only detection mode.
        self._run_auto_extract_mode(config, image, is_timelapse, store, viewer_win)

    def _run_auto_extract_mode(self, config, image, is_timelapse, store, viewer_win) -> None:
        """Creator path for the two-pass auto-extraction routine (per-cell).

        The user supplies the smallest particle diameter (px, or µm via the
        dataset pixel size) and the fine window is ``3 ×`` it; the largest is
        measured by LoG and the coarse k by the noise-symmetry floor. Per-cell ⇒
        requires an active segmentation. A time-lapse ``(T,H,W)`` channel is
        auto-extracted per frame (each frame sized independently) and saved as one
        ``(T,H,W)`` mask.
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
        # Resolve the user-supplied smallest particle diameter to px (px as-is, or
        # µm via the dataset pixel size). It is always supplied — the form has no
        # auto-detect mode.
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
            self._show_status("Smallest particle diameter must be > 0")
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
        self._run_btn.setEnabled(False)
        self._settings.set_enabled(False)
        smallest_note = (
            f"{smallest_px:.2f} px (from {config.smallest_particle_value:g} "
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
        """Print the auto-extraction passes/sizes to the terminal (debug)."""
        if report.coarse_k_mean is not None:
            coarse_k = (
                f"coarse k (per-cell): mean {report.coarse_k_mean:.2f} "
                f"[{report.coarse_k_min:.2f}–{report.coarse_k_max:.2f}] "
                f"over {report.coarse_k_n} cells"
            )
        else:
            coarse_k = "coarse k (per-cell): n/a (single pass)"
        print(
            f"  [auto-extract] smallest: {report.smallest_source}\n"
            f"    passes {report.passes} "
            f"(fine window {report.fine_window}; "
            f"largest Ø {report.largest_particle_px} px; "
            f"second pass {report.second_pass_used})\n"
            f"    {coarse_k}\n"
            f"    presmooth σ {report.presmooth_sigma_px}; n_cells {report.n_cells}; "
            f"components {report.n_components}; area {report.area_px} px²",
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
        # Reuse the standard Creator save. The detected window is no longer surfaced
        # in the UI (the form has no window field), so it is not threaded through.
        self._on_detect_done(mask)

    def _on_detect_error(self, err) -> None:
        self._run_btn.setEnabled(True)
        self._settings.set_enabled(True)
        self._show_status(f"Detection error: {err.exc_type}: {err.message}")

    def _on_detect_done(self, mask) -> None:
        """Creator-save the detected mask (shared by the auto-extraction handler)."""
        self._run_btn.setEnabled(True)
        self._settings.set_enabled(True)

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

        # A new mask is now available as a CNR classification source.
        self._refresh_cnr_masks()
        self._show_status(f"Saved '{name}': {res.n_positive:,} px")

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

    def _resolve_cnr_inputs(self, *, allow_timelapse: bool = False):
        """Shared pre-flight for the CNR tools (classify + interactive segmenter).

        Validates an open dataset/viewer, an active channel image, an active
        segmentation matching the channel, and a selected source mask read from the
        store. Returns ``(image, labels, feature_mask, cfg)`` or ``None`` after setting a
        status message on any failure.

        ``allow_timelapse`` lets a caller accept a ``(T,H,W)`` channel — both CNR tools
        pass it: the classify Action splits each frame at the one shared threshold, and
        the interactive segmenter pools the foci of all frames into one histogram and
        applies the divider(s) per frame (``run_cnr_measure_stack``). It defaults to
        ``False`` (single-frame only) for any future caller that has not opted in.
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

        # Per-cell σ is per frame. The classify Action handles a (T,H,W) channel per
        # frame (allow_timelapse=True); the interactive segmenter stays single-frame.
        n_timepoints = int(store.metadata.get("n_timepoints", 1) or 1)
        if image.ndim == 3 and n_timepoints > 1 and not allow_timelapse:
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

    def _lock_for_cnr(self) -> None:
        """Disable exactly the four widgets :meth:`_unlock_after_classify` enables.

        Both CNR paths (classify and interactive) use this, so the lock and unlock
        sets are symmetric. An asymmetric pair would let a finishing measure worker
        re-enable Run mid-detection, after which a second run would reassign
        ``self._worker`` while the first QThread is still alive.
        """
        self._classify_btn.setEnabled(False)
        self._run_btn.setEnabled(False)
        self._settings.set_enabled(False)
        self._cnr_settings.set_enabled(False)

    def _on_classify(self) -> None:
        """Green-button handler for all three CNR modes.

        The pre-flight is shared and runs exactly once per click; the mode then
        selects between the classifier (prompt → worker → save) and the
        interactive histogram segmenter (no prompt, saves from its own window).
        """
        resolved = self._resolve_cnr_inputs(allow_timelapse=True)
        if resolved is None:
            return
        image, labels, feature_mask, cfg = resolved
        if cfg.mode == "interactive":
            self._start_cnr_segmenter(image, labels, feature_mask, cfg)
            return
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
        self._lock_for_cnr()
        self._print_cnr_settings_debug(cfg)
        self._show_status(
            f"Classifying '{cfg.source_mask}' by CNR ({cfg.mode})..."
        )

        from percell4.gui.workers import Worker

        # Time-lapse (T,H,W) channel: classify each frame independently and save (T,H,W)
        # population masks; single-frame: the 2D worker. Same (pop_masks, components,
        # report) contract, so _on_classify_done handles both unchanged.
        worker_fn = (
            run_cnr_classification_stack
            if np.asarray(image).ndim == 3
            else run_cnr_classification
        )
        self._cnr_worker = Worker(
            worker_fn,
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

    def _start_cnr_segmenter(self, image, labels, feature_mask, cfg) -> None:
        """Interactive-mode path: measure per-focus CNR, then open the segmenter.

        Takes the inputs already resolved by :meth:`_on_classify`'s shared
        pre-flight, so the validation runs exactly once per click. For a time-lapse
        ``(T,H,W)`` channel the foci of ALL timepoints are pooled into one histogram
        and the divider threshold(s) apply to every frame equally.
        """
        self._pending_segment_source = cfg.source_mask
        self._lock_for_cnr()
        # Time-lapse: pool foci across frames (one histogram); single-frame: the 2D
        # worker. Both return (records, component_labels) for the shape-agnostic window.
        is_timelapse = np.asarray(image).ndim == 3
        worker_fn = run_cnr_measure_stack if is_timelapse else run_cnr_measure
        tl_note = f" across {np.asarray(image).shape[0]} timepoints" if is_timelapse else ""
        self._show_status(f"Measuring CNR for '{cfg.source_mask}'{tl_note}...")

        from percell4.gui.workers import Worker

        self._measure_worker = Worker(worker_fn, image, feature_mask, labels)
        self._measure_worker.finished.connect(self._on_measure_done)
        self._measure_worker.error.connect(self._on_measure_error)
        self._measure_worker.start()

    def _on_measure_error(self, err) -> None:
        self._unlock_after_classify()
        self._show_status(f"CNR measurement error: {err.exc_type}: {err.message}")

    def _on_measure_done(self, result) -> None:
        records, component_labels = result
        self._unlock_after_classify()
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
        #
        # Bound method, NOT a lambda. Qt breaks a connection automatically when
        # the receiver QObject is destroyed, but only when it can identify a
        # receiver — a lambda has none, so the connection outlived this panel
        # and `destroyed` fired into a half-torn-down widget tree. Reading the
        # combo then hit a freed C++ object: RuntimeError under PySide, a hard
        # segfault under PyQt5 (which is what CI runs), taking the whole test
        # process down at whatever test happened to be executing when the old
        # panel was garbage-collected.
        self._cnr_segmenter.destroyed.connect(self._on_cnr_segmenter_destroyed)
        self._show_status(f"CNR segmenter open for '{source}' ({len(valid)} foci)")
