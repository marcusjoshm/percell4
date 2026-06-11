"""Hand-computed tests for the pure puncta-scoring module (plan U5).

Every TP/FP/FN here is known by construction. Recall is GT-level; precision is
component-level — the asymmetry that stops a few giant blobs from inflating
precision toward 1.0.
"""

from __future__ import annotations

import numpy as np
import pytest
from skimage import measure
from skimage.draw import disk

from percell4.workflows.models import PunctaDetectorSettings

from percell4.domain.measure.puncta_scoring import (
    FinderScore,
    MatchCounts,
    MicroScore,
    WindowOracle,
    accumulate,
    mask_iou,
    mask_recall,
    mask_to_centroids,
    match_detections,
    score_finder,
    sweep_ideal_window,
)


def _label(mask: np.ndarray) -> np.ndarray:
    return measure.label(np.asarray(mask) > 0)


# ── mask_to_centroids ──────────────────────────────────────────────────────


def test_mask_to_centroids_single_blob():
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[8:11, 8:11] = 1  # 3x3 centered at (9, 9)
    cents = mask_to_centroids(mask)
    assert cents.shape == (1, 2)
    assert np.allclose(cents[0], [9.0, 9.0])


def test_mask_to_centroids_empty():
    cents = mask_to_centroids(np.zeros((10, 10), dtype=np.uint8))
    assert cents.shape == (0, 2)


def test_mask_to_centroids_closing_merges_fragments():
    # Two 3x3 granule fragments with a 1px gap -> closing(disk=1) merges them.
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[10:13, 8:11] = 1  # cols 8,9,10
    mask[10:13, 12:15] = 1  # cols 12,13,14 (gap at col 11)
    assert mask_to_centroids(mask, close_radius=0).shape[0] == 2
    assert mask_to_centroids(mask, close_radius=1).shape[0] == 1


# ── match_detections: perfect match ─────────────────────────────────────────


def test_perfect_match_recall_precision_one():
    mask = np.zeros((40, 40), dtype=np.uint8)
    gt = []
    for y, x in [(10, 10), (10, 30), (30, 10), (30, 30)]:
        mask[y - 1 : y + 2, x - 1 : x + 2] = 1
        gt.append((y, x))
    counts = match_detections(_label(mask), np.array(gt, float), tol=2.0)
    assert counts == MatchCounts(tp_recall=4, fn=0, tp_components=4, fp_components=0)
    score = accumulate([counts])
    assert score.recall == 1.0
    assert score.precision == 1.0


# ── Touching foci: one component covers 3 GT ────────────────────────────────


def test_one_component_over_three_gt_recall_three_precision_one():
    # A single elongated component spanning three GT footprints.
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[20, 8:33] = 1  # one connected horizontal bar
    gt = np.array([[20, 10], [20, 20], [20, 30]], float)
    counts = match_detections(_label(mask), gt, tol=2.0)
    # 3 GT credited toward recall, but only ONE precision component.
    assert counts.tp_recall == 3
    assert counts.fn == 0
    assert counts.tp_components == 1
    assert counts.fp_components == 0


# ── Flooding guard: extra empty giant blobs penalize precision ──────────────


def test_flooding_does_not_inflate_precision_past_floor():
    # One giant blob covers all 3 GT (recall via phase 1), but the method
    # also floods 3 extra giant blobs covering NO GT -> 3 FP components.
    mask = np.zeros((80, 80), dtype=np.uint8)
    mask[10:20, 5:60] = 1  # covers the 3 GT
    mask[30:40, 5:25] = 2  # empty giant blob
    mask[50:60, 5:25] = 3  # empty giant blob
    mask[30:40, 40:60] = 4  # empty giant blob
    det_labels = measure.label(mask > 0)
    gt = np.array([[15, 10], [15, 30], [15, 50]], float)
    counts = match_detections(det_labels, gt, tol=2.0)
    assert counts.tp_recall == 3  # recall is perfect via footprint credit
    assert counts.tp_components == 1
    assert counts.fp_components == 3
    score = accumulate([counts])
    # precision = 1 / (1 + 3) = 0.25 -> would FAIL a 0.9 floor.
    assert score.precision == pytest.approx(0.25)
    assert score.precision < 0.9


# ── Fragmentation: 2 fragments of one granule, closed -> 1 TP / 0 FP ────────


def test_fragmentation_closed_is_one_tp_zero_fp():
    # Two 3x3 granule fragments with a 1px gap, straddling one GT at the gap.
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[14:17, 11:14] = 1  # cols 11,12,13
    mask[14:17, 15:18] = 1  # cols 15,16,17 (gap at col 14)
    gt = np.array([[15, 14]], float)  # the gap pixel -> in neither raw footprint
    # Without closing: 2 separate components; GT lands in the gap (no footprint
    # credit) -> phase 2 matches one, the other is an FP.
    raw = match_detections(measure.label(mask > 0), gt, tol=2.0)
    assert raw.fp_components == 1
    # With closing (mask_to_centroids-style): the caller closes first.
    from skimage import morphology

    closed = morphology.closing(mask > 0, morphology.disk(1))
    counts = match_detections(measure.label(closed), gt, tol=2.0)
    assert counts.tp_recall == 1
    assert counts.fn == 0
    assert counts.tp_components == 1
    assert counts.fp_components == 0


# ── Boundary: distance exactly at tol matches; just beyond -> fn + fp ───────


def test_boundary_pair_exactly_at_tol_matches():
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[20, 20] = 1  # single pixel, centroid (20, 20)
    gt = np.array([[20, 23]], float)  # distance exactly 3.0
    counts = match_detections(_label(mask), gt, tol=3.0)
    assert counts.tp_recall == 1
    assert counts.fp_components == 0


def test_boundary_pair_just_beyond_tol_is_fn_and_fp():
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[20, 20] = 1
    gt = np.array([[20, 24]], float)  # distance 4.0 > tol 3.0
    counts = match_detections(_label(mask), gt, tol=3.0)
    assert counts.tp_recall == 0
    assert counts.fn == 1
    assert counts.fp_components == 1


# ── Phase-2 dedup: a phase-1-credited GT is not re-matched in phase 2 ───────


def test_phase1_credited_gt_not_double_counted():
    # GT0 lands inside component A (phase-1 credit). A leftover detection B
    # also sits within tol of GT0. GT0 must be counted once, not twice.
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[20, 20] = 1  # component A: GT0 lands on it (footprint credit)
    mask[20, 22] = 2  # component B: a separate detection 2px away
    det_labels = measure.label(mask > 0)
    gt = np.array([[20, 20]], float)  # exactly on A; within 2px of B too
    counts = match_detections(det_labels, gt, tol=2.0)
    # GT counted once. A covers it (TP-component). B covers nothing (FP).
    assert counts.tp_recall == 1
    assert counts.fn == 0
    assert counts.tp_components == 1
    assert counts.fp_components == 1


# ── f_beta math on known counts ─────────────────────────────────────────────


def test_f_beta_known_counts():
    # recall = 8 / (8 + 2) = 0.8 ; precision = 6 / (6 + 2) = 0.75
    score = MicroScore(tp_recall=8, fn=2, tp_components=6, fp_components=2)
    assert score.recall == pytest.approx(0.8)
    assert score.precision == pytest.approx(0.75)
    # F2 = (1 + 4) * p * r / (4 * p + r)
    p, r = 0.75, 0.8
    expected = 5 * p * r / (4 * p + r)
    assert score.f_beta(2.0) == pytest.approx(expected)
    # F1 = 2pr / (p + r)
    expected_f1 = 2 * p * r / (p + r)
    assert score.f_beta(1.0) == pytest.approx(expected_f1)


def test_zero_denominators_guarded():
    empty = MicroScore(tp_recall=0, fn=0, tp_components=0, fp_components=0)
    assert empty.recall == 0.0
    assert empty.precision == 0.0
    assert empty.f_beta(2.0) == 0.0


def test_accumulate_sums_across_fields():
    a = MatchCounts(tp_recall=3, fn=1, tp_components=3, fp_components=0)
    b = MatchCounts(tp_recall=2, fn=2, tp_components=2, fp_components=1)
    score = accumulate([a, b])
    assert score.tp_recall == 5
    assert score.fn == 3
    assert score.tp_components == 5
    assert score.fp_components == 1


# ── Window-size oracle (U6) ───────────────────────────────────────────────


def _disk_image(centers, radius, *, dilute=50.0, fg=220.0, shape=(160, 160), seed=0):
    rng = np.random.default_rng(seed)
    img = dilute + rng.normal(0.0, 2.0, size=shape).astype(np.float32)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), radius, shape=shape)
        img[rr, cc] = fg
    return img.astype(np.float32)


def _disk_mask(centers, radius, shape=(160, 160)):
    m = np.zeros(shape, dtype=np.uint8)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), radius, shape=shape)
        m[rr, cc] = 1
    return m


def _adaptive_settings(window_px=31, k=3.0):
    return PunctaDetectorSettings(
        detector_name="adaptive",
        seed_detector_name="otsu",
        background_estimator_name="mad",
        detector_params={"window_px": window_px, "k": k},
        min_spot_px=3,
        spot_scale_prior=(1.0, 4.0),
    )


def test_mask_iou_identities():
    a = _disk_mask([(80, 80)], 12)
    assert mask_iou(a, a) == 1.0
    assert mask_iou(a, _disk_mask([(20, 20)], 5)) == 0.0  # disjoint
    assert mask_iou(np.zeros((10, 10)), np.zeros((10, 10))) == 0.0  # empty union guarded


def test_mask_iou_encoding_invariant():
    a = _disk_mask([(80, 80)], 12)
    a255 = (a * 255).astype(np.uint8)
    assert mask_iou(a255, a) == 1.0  # {0,255} vs {0,1} score identically


def test_mask_iou_half_overlap():
    a = np.zeros((10, 20), dtype=np.uint8); a[:, :10] = 1
    b = np.zeros((10, 20), dtype=np.uint8); b[:, 5:15] = 1
    # intersection 50 px, union 150 px
    assert mask_iou(a, b) == pytest.approx(50 / 150)


def test_mask_recall_ignores_over_coverage():
    sg = _disk_mask([(80, 80)], 10)
    exact = _disk_mask([(80, 80)], 10)
    over = _disk_mask([(80, 80)], 20)  # covers all of sg AND more
    assert mask_recall(exact, sg) == 1.0
    assert mask_recall(over, sg) == 1.0  # recall ignores the over-coverage
    assert mask_recall(np.zeros_like(sg), sg) == 0.0
    assert mask_recall(sg, np.zeros_like(sg)) == 0.0  # empty SG guarded


def test_iou_and_recall_rank_windows_differently():
    """The boundary guard's basis: IoU penalizes over-coverage, recall does not.

    An exactly-matching mask and an over-covering mask have identical recall but
    different IoU — so an IoU peak can reflect boundary tightness, not capture.
    """
    sg = _disk_mask([(80, 80)], 10)
    exact = _disk_mask([(80, 80)], 10)
    over = _disk_mask([(80, 80)], 20)
    assert mask_iou(exact, sg) > mask_iou(over, sg)  # IoU prefers the tight match
    assert mask_recall(exact, sg) == mask_recall(over, sg)  # recall cannot tell them apart


def test_sweep_ideal_window_structure_and_peak():
    img = _disk_image([(80, 80), (80, 40), (40, 100)], radius=8)
    sg = _disk_mask([(80, 80), (80, 40), (40, 100)], radius=8)
    grid = [11, 21, 41]
    oracle = sweep_ideal_window(img, 1.0, _adaptive_settings(), sg, grid)
    assert isinstance(oracle, WindowOracle)
    assert len(oracle.windows) == len(grid) == len(oracle.iou_curve) == len(oracle.recall_curve)
    assert all(w % 2 == 1 for w in oracle.windows)  # detector forces odd
    assert max(oracle.iou_curve) > 0.0  # detection overlaps the granules somewhere
    assert oracle.ideal_window == oracle.windows[int(np.argmax(oracle.iou_curve))]
    assert all(0.0 <= r <= 1.0 for r in oracle.recall_curve)


def test_sweep_ideal_window_empty_grid():
    oracle = sweep_ideal_window(_disk_image([(80, 80)], 8), 1.0, _adaptive_settings(), _disk_mask([(80, 80)], 8), [])
    assert oracle == WindowOracle(0, (), (), ())


def test_score_finder_fields():
    sg = _disk_mask([(80, 80)], 10)
    finder_mask = _disk_mask([(80, 80)], 10)
    oracle = WindowOracle(ideal_window=91, windows=(11, 91), iou_curve=(0.1, 0.9), recall_curve=(0.2, 1.0))
    fs = score_finder("granule-size", 71, oracle, finder_mask, sg, k=3.0, in_sample=True)
    assert isinstance(fs, FinderScore)
    assert fs.window_error == abs(71 - 91)
    assert fs.iou == 1.0
    assert fs.recall == 1.0
    assert fs.k == 3.0
    assert fs.in_sample is True
