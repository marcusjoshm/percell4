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


def match_detections(
    det_labels: NDArray, gt_points: NDArray, tol: float
) -> MatchCounts:
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
