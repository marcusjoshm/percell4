"""Pure scoring math for the puncta-detection validation harness (plan U5).

This module is the pure-domain heart of the dev-time validation harness: it
turns a detected mask + a set of hand-labeled ground-truth (GT) centroids into
matched recall / precision counts, and accumulates those counts across fields
into a micro-averaged score.

Two-phase location-matched protocol (ISBI-style distance gate), designed so a
*merged* detection that covers several touching granules is credited fairly:

1. **Phase 1 — footprint credit.** Each GT point whose rounded ``(y, x)`` lands
   inside a detected component's footprint is credited as a recall TP and its
   covering component is marked "covers >= 1 GT". That GT leaves the pool.
   A single component covering ``k`` GT therefore yields ``k`` recall TPs.
2. **Phase 2 — bipartite on the remainder.** Components that do not yet cover
   any GT are matched to the *remaining unclaimed* GT via
   ``scipy.optimize.linear_sum_assignment`` on their centroid-distance matrix;
   any assigned pair with distance ``> tol`` is dropped. A surviving pair
   credits a recall TP and marks its component as covering a GT.

**Precision is counted at the detected-component level, recall at the GT level.**
A component covering ``k`` GT contributes ``k`` toward recall but exactly **one**
unit to the precision denominator (a TP-component). A component covering *no* GT
is one FP-component. This is the regression-critical asymmetry: flooding a cell
with a few giant blobs cannot inflate precision toward 1.0, because the giant
blobs still count as only a handful of precision units.

Pure: ``numpy`` / ``scipy`` / ``skimage`` only. No Qt, napari, h5py, or store.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


def mask_to_centroids(mask: NDArray, *, close_radius: int = 0) -> NDArray:
    """Return the ``(N, 2)`` array of ``(y, x)`` float centroids of ``mask > 0``.

    Reuses ``skimage.measure.regionprops(label(mask > 0))`` so the centroid is
    byte-identical to the particle-analysis path
    (``percell4.domain.measure.particle``); this is deliberately not hand-rolled.

    When ``close_radius > 0`` a morphological closing with a disk of that radius
    is applied to ``mask > 0`` *before* labeling, so fragments of one granule
    within the spot scale merge into a single connected component (one centroid)
    rather than several.
    """
    from skimage import measure, morphology

    binary = np.asarray(mask) > 0
    if close_radius > 0:
        binary = morphology.closing(binary, morphology.disk(close_radius))
    labeled = measure.label(binary)
    props = measure.regionprops(labeled)
    if not props:
        return np.empty((0, 2), dtype=np.float64)
    return np.array([p.centroid for p in props], dtype=np.float64)


@dataclass(frozen=True)
class MatchCounts:
    """Per-field matched counts from :func:`match_detections`.

    Recall is GT-level; precision is component-level (see module docstring).

    Attributes
    ----------
    tp_recall:
        GT points matched to a detection (recall numerator).
    fn:
        GT points left unmatched (recall denominator = ``tp_recall + fn``).
    tp_components:
        Detected components covering >= 1 GT (precision numerator).
    fp_components:
        Detected components covering no GT (precision denominator =
        ``tp_components + fp_components``).
    """

    tp_recall: int
    fn: int
    tp_components: int
    fp_components: int


def match_detections(det_labels: NDArray, gt_points: NDArray, tol: float) -> MatchCounts:
    """Match a labeled detection mask against GT centroids (two-phase protocol).

    Parameters
    ----------
    det_labels:
        Integer label image (``0`` = background, each connected component a
        distinct positive id). The caller passes
        ``skimage.measure.label(closed_mask)``.
    gt_points:
        ``(M, 2)`` array of ground-truth ``(y, x)`` centroids.
    tol:
        Centroid-distance gate for the phase-2 bipartite match. The boundary is
        inclusive (``distance <= tol`` matches).

    Returns
    -------
    A :class:`MatchCounts`.
    """
    det_labels = np.asarray(det_labels)
    gt = np.asarray(gt_points, dtype=np.float64).reshape(-1, 2)
    n_gt = gt.shape[0]

    component_ids = [int(c) for c in np.unique(det_labels) if c != 0]
    covers_gt: dict[int, bool] = {cid: False for cid in component_ids}

    h, w = det_labels.shape

    # ── Phase 1: footprint credit ──────────────────────────────────────
    gt_claimed = np.zeros(n_gt, dtype=bool)
    tp_recall = 0
    for i in range(n_gt):
        yy = int(round(float(gt[i, 0])))
        xx = int(round(float(gt[i, 1])))
        if not (0 <= yy < h and 0 <= xx < w):
            continue
        cid = int(det_labels[yy, xx])
        if cid != 0:
            tp_recall += 1
            gt_claimed[i] = True
            covers_gt[cid] = True

    # ── Phase 2: bipartite on the remainder ────────────────────────────
    # Only components that don't yet cover any GT, against unclaimed GT.
    remaining_ids = [cid for cid in component_ids if not covers_gt[cid]]
    remaining_gt_idx = np.flatnonzero(~gt_claimed)

    if remaining_ids and remaining_gt_idx.size > 0:
        cent = _component_centroids(det_labels, remaining_ids)
        gt_rem = gt[remaining_gt_idx]
        # Distance matrix: rows = remaining components, cols = remaining GT.
        diff = cent[:, None, :] - gt_rem[None, :, :]
        cost = np.sqrt((diff**2).sum(axis=2))
        from scipy.optimize import linear_sum_assignment

        row_ind, col_ind = linear_sum_assignment(cost)
        for r, c in zip(row_ind, col_ind):
            if cost[r, c] <= tol:
                tp_recall += 1
                covers_gt[remaining_ids[r]] = True

    # ── Tally ──────────────────────────────────────────────────────────
    fn = n_gt - tp_recall
    tp_components = sum(1 for cid in component_ids if covers_gt[cid])
    fp_components = sum(1 for cid in component_ids if not covers_gt[cid])
    return MatchCounts(
        tp_recall=tp_recall,
        fn=fn,
        tp_components=tp_components,
        fp_components=fp_components,
    )


def _component_centroids(det_labels: NDArray, ids: list[int]) -> NDArray:
    """``(len(ids), 2)`` array of ``(y, x)`` centroids for the given label ids."""
    out = np.empty((len(ids), 2), dtype=np.float64)
    for k, cid in enumerate(ids):
        ys, xs = np.nonzero(det_labels == cid)
        out[k, 0] = ys.mean()
        out[k, 1] = xs.mean()
    return out


@dataclass(frozen=True)
class MicroScore:
    """Micro-averaged score from summed :class:`MatchCounts` across fields."""

    tp_recall: int
    fn: int
    tp_components: int
    fp_components: int

    @property
    def recall(self) -> float:
        denom = self.tp_recall + self.fn
        return self.tp_recall / denom if denom > 0 else 0.0

    @property
    def precision(self) -> float:
        denom = self.tp_components + self.fp_components
        return self.tp_components / denom if denom > 0 else 0.0

    def f_beta(self, beta: float = 2.0) -> float:
        """Weighted harmonic mean of precision and recall (recall-weighted)."""
        p = self.precision
        r = self.recall
        b2 = beta * beta
        denom = b2 * p + r
        if denom <= 0.0:
            return 0.0
        return (1.0 + b2) * p * r / denom


def accumulate(counts: list[MatchCounts]) -> MicroScore:
    """Sum a list of :class:`MatchCounts` into micro totals (a :class:`MicroScore`)."""
    return MicroScore(
        tp_recall=sum(c.tp_recall for c in counts),
        fn=sum(c.fn for c in counts),
        tp_components=sum(c.tp_components for c in counts),
        fp_components=sum(c.fp_components for c in counts),
    )


# ── Window-size oracle ─────────────────────────────────────────────────────
#
# Turns "the mask looks right" into a number for the auto-window bake-off: the
# *ideal* window for a labeled image is the one whose adaptive mask best matches
# the hand-drawn SG mask. IoU is the primary score, but a SG mask drawn with a
# different boundary convention than the detector's tight ``k*sigma`` edges can
# make ``argmax IoU`` track boundary tightness rather than granule capture — so
# a recall (coverage) curve is returned alongside, for the harness/human guard.
# Detection is never reimplemented here: ``sweep_ideal_window`` calls the
# production ``detect_two_pass`` at each window, reusing a cached, window-
# independent pass-1 ``seeds`` and the round's fixed ``k`` from ``settings``.


def mask_iou(a: NDArray, b: NDArray) -> float:
    """Intersection-over-union of two boolean masks (binarized ``> 0``).

    Returns ``0.0`` on an empty union (no divide-by-zero). Any positive encoding
    (``{0,1}`` or ``{0,255}``) yields the same value.
    """
    am = np.asarray(a) > 0
    bm = np.asarray(b) > 0
    union = int(np.logical_or(am, bm).sum())
    if union == 0:
        return 0.0
    return int(np.logical_and(am, bm).sum()) / union


def mask_recall(mask: NDArray, sg_mask: NDArray) -> float:
    """Fraction of the SG-mask area covered by ``mask`` (recall @ coverage).

    Unlike IoU this ignores over-coverage, so comparing the two curves reveals
    when a higher-IoU window is just boundary-matching rather than capturing
    more granules. Returns ``0.0`` for an empty SG mask.
    """
    g = np.asarray(sg_mask) > 0
    g_area = int(g.sum())
    if g_area == 0:
        return 0.0
    return int(np.logical_and(np.asarray(mask) > 0, g).sum()) / g_area


@dataclass(frozen=True)
class WindowOracle:
    """Result of an SG-mask window sweep.

    ``ideal_window`` is the IoU-argmax window. ``iou_curve`` / ``recall_curve``
    (aligned with ``windows``) are returned so the harness/human can guard
    against an IoU peak that merely matches the SG mask's boundary convention:
    a broad/flat IoU curve, or a recall curve that peaks at a different window,
    is the signal to fall back to recall rather than trust tight-boundary IoU.
    """

    ideal_window: int
    windows: tuple[int, ...]
    iou_curve: tuple[float, ...]
    recall_curve: tuple[float, ...]


def sweep_ideal_window(
    image: NDArray,
    gaussian_sigma: float | None,
    settings,
    sg_mask: NDArray,
    window_grid,
) -> WindowOracle:
    """``argmax_w IoU(adaptive_mask(image, window=w), sg_mask)`` over a grid.

    ``settings`` is a duck-typed ``PunctaDetectorSettings`` whose fixed ``k`` /
    background estimator define the detector; only ``window_px`` is varied. Runs
    the production ``detect_two_pass`` per window with a cached window-
    independent pass-1 ``seeds`` (the cost lever is the grid coarseness, not the
    cache — the per-window ``threshold_local`` + size filter still run). Returns
    both the IoU and recall curves for the boundary guard.
    """
    import dataclasses

    from percell4.domain.measure.puncta_pipeline import (
        DEFAULT_SCALE_RANGE,
        compute_seeds,
        detect_two_pass,
    )
    from percell4.domain.measure.thresholding import apply_gaussian_smoothing

    sm = apply_gaussian_smoothing(np.asarray(image, dtype=np.float32), gaussian_sigma)
    group = np.ones(sm.shape, dtype=bool)
    g = np.asarray(sg_mask) > 0
    scale_range = settings.spot_scale_prior or DEFAULT_SCALE_RANGE

    seeds = None
    windows: list[int] = []
    ious: list[float] = []
    recalls: list[float] = []
    for raw_w in window_grid:
        w = int(raw_w) | 1  # the detector forces odd; sweep odd values
        if seeds is None:  # window-independent — computed once
            seeds = compute_seeds(sm, group, settings, scale_range)
        params = dict(settings.detector_params)
        params["window_px"] = w
        s = dataclasses.replace(settings, detector_params=params)
        mask = detect_two_pass(sm, group, s, seeds=seeds)
        windows.append(w)
        ious.append(mask_iou(mask, g))
        recalls.append(mask_recall(mask, g))

    if not windows:
        return WindowOracle(0, (), (), ())
    best = int(np.argmax(ious))
    return WindowOracle(windows[best], tuple(windows), tuple(ious), tuple(recalls))


@dataclass(frozen=True)
class FinderScore:
    """One finder's score against the oracle on one labeled field."""

    method: str
    auto_window: int
    ideal_window: int
    window_error: int
    iou: float
    recall: float
    k: float
    in_sample: bool


def score_finder(
    method: str,
    auto_window: int,
    oracle: WindowOracle,
    finder_mask: NDArray,
    sg_mask: NDArray,
    *,
    k: float,
    in_sample: bool,
) -> FinderScore:
    """Score a finder: ``|auto - ideal|`` window error + the finder mask's IoU/recall.

    ``in_sample`` records whether this field was also used to calibrate the
    finder's multiplier — the harness must never report an in-sample score as
    "validated"; ``k`` is the pinned value the whole sweep ran at.
    """
    return FinderScore(
        method=method,
        auto_window=int(auto_window),
        ideal_window=int(oracle.ideal_window),
        window_error=abs(int(auto_window) - int(oracle.ideal_window)),
        iou=mask_iou(finder_mask, sg_mask),
        recall=mask_recall(finder_mask, sg_mask),
        k=float(k),
        in_sample=bool(in_sample),
    )
