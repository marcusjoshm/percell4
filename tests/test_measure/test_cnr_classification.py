"""Tests for CNR-based subpopulation classification (U3).

Fixtures place a grid of small foci inside one big cell over a noisy background,
each focus's added intensity controlling its CNR. This lets us build a continuum
(no gap -> one population), two well-separated CNR clusters (a gap -> two
populations), and overlapping clusters (no gap -> guided/forced needed).
"""

from __future__ import annotations

import numpy as np
import pytest
from skimage.draw import disk

from percell4.domain.measure.cnr_classification import (
    ClassificationResult,
    StackClassificationResult,
    assign_segments,
    classify_by_cnr,
    classify_by_cnr_stack,
    measure_cnr,
    segment_label_image,
    segment_masks_from_label_image,
    to_dataframe,
)

SHAPE = (360, 360)
_FOCUS_R = 3


def _grid_centers(n_side: int, margin: int = 30, step: int | None = None):
    """``n_side``² focus centers on a regular grid inside the frame."""
    if step is None:
        step = (SHAPE[0] - 2 * margin) // (n_side - 1)
    return [
        (margin + i * step, margin + j * step)
        for i in range(n_side)
        for j in range(n_side)
    ]

def _make_foci(levels, *, noise_std=5.0, baseline=100.0, seed=0, one_outside=False):
    """Build (image, mask, labels) with one focus per entry in ``levels``.

    Each focus is a radius-3 disc with ``baseline + level`` added intensity over a
    noisy ``baseline`` inside a single whole-frame cell. ``one_outside`` drops the
    last focus outside the cell (host sigma unresolvable -> cnr nan).
    """
    rng = np.random.RandomState(seed)
    img = rng.normal(baseline, noise_std, SHAPE).astype(np.float32)
    labels = np.ones(SHAPE, dtype=np.int32)  # one cell = whole frame
    mask = np.zeros(SHAPE, dtype=np.uint8)
    centers = _grid_centers(int(round(len(levels) ** 0.5)))
    centers = centers[: len(levels)]
    for k, ((cy, cx), lvl) in enumerate(zip(centers, levels)):
        rr, cc = disk((cy, cx), _FOCUS_R, shape=SHAPE)
        img[rr, cc] = baseline + float(lvl)
        mask[rr, cc] = 1
        if one_outside and k == len(levels) - 1:
            labels[rr, cc] = 0  # focus sits outside any cell
    return img, mask, labels


# --------------------------------------------------------------------------- #
# measure_cnr
# --------------------------------------------------------------------------- #
def test_measure_cnr_basic_fields_and_value():
    """Each focus yields a record; CNR ~ (interior-background)/sigma_cell > 0."""
    img, mask, labels = _make_foci([120.0] * 9)
    recs = measure_cnr(img, mask, labels)
    assert len(recs) == 9
    for r in recs:
        assert r["cell"] == 1
        assert r["area"] > 0
        assert r["diameter"] > 0
        assert np.isfinite(r["cnr"]) and r["cnr"] > 0
        # CNR is the contrast in per-cell sigma units.
        assert r["cnr"] == pytest.approx(r["contrast"] / r["sigma"], rel=1e-5)


def test_measure_cnr_focus_outside_cell_is_nan():
    """A focus with no host cell gets cnr = nan (dropped by the classifier)."""
    img, mask, labels = _make_foci([120.0] * 9, one_outside=True)
    recs = measure_cnr(img, mask, labels)
    nan_recs = [r for r in recs if not np.isfinite(r["cnr"])]
    assert len(nan_recs) == 1
    assert nan_recs[0]["cell"] == 0


# --------------------------------------------------------------------------- #
# discover mode
# --------------------------------------------------------------------------- #
def test_discover_single_population_on_continuum():
    """A unimodal CNR spread -> one population (no invented structure)."""
    rng = np.random.RandomState(3)
    levels = np.clip(rng.normal(150.0, 45.0, 64), 20.0, None)
    img, mask, labels = _make_foci(levels)
    res = classify_by_cnr(img, mask, labels)  # discover
    assert isinstance(res, ClassificationResult)
    assert res.n_subpopulations == 1
    assert res.split_axis is None
    assert "single population" in res.report["decision"]
    assert res.report["candidate_cnr_threshold"] > 0  # still offered


def test_discover_two_populations_on_gap():
    """Two well-separated CNR clusters -> a gap -> two populations."""
    levels = np.concatenate([np.full(32, 30.0), np.full(32, 400.0)])
    img, mask, labels = _make_foci(levels, noise_std=5.0)
    res = classify_by_cnr(img, mask, labels)  # discover
    assert res.n_subpopulations == 2
    assert res.report["dip_cnr"]["bimodal"] is True
    assert res.report["dip_cnr"]["reliable"] is True  # diptest present
    assert set(np.unique(res.labels_image)) <= {0, 1, 2}
    assert {1, 2} <= set(np.unique(res.labels_image))


def test_discover_too_few_foci_returns_single():
    """Below MIN_COMPONENTS, discover returns one population and says so."""
    levels = np.concatenate([np.full(5, 30.0), np.full(5, 400.0)])  # only 10
    img, mask, labels = _make_foci(levels)
    res = classify_by_cnr(img, mask, labels)
    assert res.n_subpopulations == 1
    assert "need >=" in res.report["decision"]


def test_discover_not_fooled_by_textured_cell():
    """A continuum with extra in-cell texture must not produce a spurious split."""
    rng = np.random.RandomState(11)
    levels = np.clip(rng.normal(150.0, 45.0, 64), 20.0, None)
    img, mask, labels = _make_foci(levels)
    # Add coarse texture (a low-frequency ripple) across the cell — inflates MAD
    # but does not create a second CNR mode.
    yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    img = img + 8.0 * np.sin(yy / 9.0).astype(np.float32) * np.cos(xx / 11.0).astype(np.float32)
    res = classify_by_cnr(img, mask, labels)
    assert res.n_subpopulations == 1


# --------------------------------------------------------------------------- #
# guided mode
# --------------------------------------------------------------------------- #
def test_guided_splits_at_threshold():
    """Guided mode splits at the supplied CNR threshold (no gap needed)."""
    rng = np.random.RandomState(5)
    levels = np.clip(rng.normal(150.0, 45.0, 64), 20.0, None)
    img, mask, labels = _make_foci(levels)
    base = classify_by_cnr(img, mask, labels)  # discover -> 1 population
    assert base.n_subpopulations == 1
    thr = base.report["candidate_cnr_threshold"]
    res = classify_by_cnr(img, mask, labels, threshold=thr)
    assert res.n_subpopulations == 2
    assert res.threshold == pytest.approx(thr, rel=1e-6)
    assert sum(res.report["group_sizes"]) == res.report["n_components_valid"]


def test_guided_rejects_tiny_smaller_group():
    """A threshold that isolates < MIN_FRACTION of foci -> split rejected."""
    rng = np.random.RandomState(6)
    levels = np.clip(rng.normal(150.0, 30.0, 64), 20.0, None)
    img, mask, labels = _make_foci(levels)
    recs = [r for r in measure_cnr(img, mask, labels) if np.isfinite(r["cnr"]) and r["cnr"] > 0]
    cnrs = sorted(r["cnr"] for r in recs)
    thr = cnrs[-1] + 1.0  # above every focus -> smaller group is 0%
    res = classify_by_cnr(img, mask, labels, threshold=thr)
    assert res.n_subpopulations == 1
    assert "rejected" in res.report["decision"]


# --------------------------------------------------------------------------- #
# forced mode
# --------------------------------------------------------------------------- #
def test_forced_splits_continuum_with_low_confidence_warning():
    """Forced n_populations=2 always splits, warning when there is no gap."""
    rng = np.random.RandomState(8)
    levels = np.clip(rng.normal(150.0, 45.0, 64), 20.0, None)
    img, mask, labels = _make_foci(levels)
    res = classify_by_cnr(img, mask, labels, n_populations=2)
    assert res.n_subpopulations == 2
    assert any("low confidence" in w for w in res.report["warnings"])


# --------------------------------------------------------------------------- #
# to_dataframe
# --------------------------------------------------------------------------- #
def test_to_dataframe_has_subpopulation_column():
    levels = np.concatenate([np.full(32, 30.0), np.full(32, 400.0)])
    img, mask, labels = _make_foci(levels)
    res = classify_by_cnr(img, mask, labels)
    df = to_dataframe(res)
    assert len(df) == res.report["n_components_total"]
    assert "subpopulation" in df.columns
    assert "cnr" in df.columns


# --------------------------------------------------------------------------- #
# diptest dependency smoke (U1)
# --------------------------------------------------------------------------- #
def test_diptest_present_and_gap_test_reliable():
    """diptest is installed (U1) so the gap test is reported reliable."""
    import diptest

    sample = np.concatenate(
        [
            np.random.RandomState(0).normal(0.0, 1.0, 200),
            np.random.RandomState(1).normal(8.0, 1.0, 200),
        ]
    )
    _, p = diptest.diptest(sample)
    assert p < 0.05  # a real gap


# --------------------------------------------------------------------------- #
# manual divider segmentation helpers (SEG-U1)
# --------------------------------------------------------------------------- #
def test_assign_segments_two_dividers():
    cnr = np.array([1.0, 4.0, 6.0, 9.0, 20.0])
    seg = assign_segments(cnr, [5.0, 10.0])  # 3 segments
    # <=5 -> 1, (5,10] -> 2, >10 -> 3
    assert list(seg) == [1, 1, 2, 2, 3]


def test_assign_segments_empty_dividers_all_one():
    cnr = np.array([1.0, 50.0, 999.0])
    assert list(assign_segments(cnr, [])) == [1, 1, 1]


def test_assign_segments_unsorted_dividers():
    cnr = np.array([2.0, 7.0, 12.0])
    a = assign_segments(cnr, [10.0, 5.0])  # given unsorted
    b = assign_segments(cnr, [5.0, 10.0])
    assert list(a) == list(b) == [1, 2, 3]


def test_segment_label_image_maps_components():
    # component-label image: 0 bg, comp 1 (top-left), comp 2 (bottom-right)
    comp = np.zeros((6, 6), dtype=np.int32)
    comp[0:2, 0:2] = 1
    comp[4:6, 4:6] = 2
    focus_labels = np.array([1, 2])
    focus_cnr = np.array([3.0, 20.0])  # one low, one high
    seg_img = segment_label_image(comp, focus_labels, focus_cnr, [10.0])
    assert set(np.unique(seg_img)) == {0, 1, 2}
    assert seg_img[0, 0] == 1   # comp 1 (cnr 3 <= 10) -> seg 1
    assert seg_img[5, 5] == 2   # comp 2 (cnr 20 > 10) -> seg 2
    assert seg_img[3, 3] == 0   # background unchanged


def test_segment_label_image_invalid_focus_stays_zero():
    comp = np.zeros((4, 4), dtype=np.int32)
    comp[0:2, 0:2] = 1
    # focus id 1 present but a stray id 99 (outside the component range) ignored
    seg_img = segment_label_image(comp, np.array([1, 99]), np.array([5.0, 5.0]), [])
    assert seg_img[0, 0] == 1
    assert set(np.unique(seg_img)) == {0, 1}


def test_segment_masks_from_label_image():
    seg_img = np.array([[0, 1], [2, 2]], dtype=np.int32)
    masks = segment_masks_from_label_image(seg_img, 3)
    assert len(masks) == 3
    assert masks[0].dtype == np.uint8
    assert masks[0].sum() == 1   # one pixel in seg 1
    assert masks[1].sum() == 2   # two pixels in seg 2
    assert masks[2].sum() == 0   # seg 3 empty -> all-zero mask


# --------------------------------------------------------------------------- #
# U2: classify_by_cnr_stack — per-frame time-lapse classification (R4, R5, R6)
# --------------------------------------------------------------------------- #
_SPLIT_LEVELS = np.concatenate([np.full(32, 30.0), np.full(32, 400.0)])  # two CNR clusters


def _guided_threshold():
    """A CNR threshold sitting between the two _SPLIT_LEVELS clusters."""
    img, mask, labels = _make_foci(_SPLIT_LEVELS, noise_std=5.0)
    return float(classify_by_cnr(img, mask, labels).report["candidate_cnr_threshold"])


def _stack(frames):
    """Stack a list of (img, mask, labels) into ((T,H,W), (T,H,W), (T,H,W))."""
    img = np.stack([f[0] for f in frames], axis=0)
    mask = np.stack([f[1] for f in frames], axis=0)
    labels = np.stack([f[2] for f in frames], axis=0)
    return img, mask, labels


def test_classify_stack_guided_splits_every_frame_same_threshold():
    """Guided mode applies ONE shared threshold to every timepoint independently."""
    thr = _guided_threshold()
    img, mask, labels = _stack([_make_foci(_SPLIT_LEVELS, seed=s) for s in (0, 1, 2)])
    res = classify_by_cnr_stack(img, mask, labels, mode="guided", threshold=thr)
    assert isinstance(res, StackClassificationResult)
    assert res.low_stack.shape == img.shape and res.high_stack.shape == img.shape
    assert res.low_stack.dtype == np.uint8 and res.high_stack.dtype == np.uint8
    for fr in res.per_frame:
        assert fr["n_subpopulations"] == 2
        assert fr["low_px"] > 0 and fr["high_px"] > 0
    # per-focus table spans all timepoints and is flagged 2-population per frame
    assert set(res.table["timepoint"].unique()) == {0, 1, 2}
    assert (res.table["n_subpopulations"] == 2).all()


def test_classify_stack_always_exactly_T_frames_with_empty_frame():
    """A frame with no foci is an all-zero plane — the stacks keep exactly T frames
    (so they round-trip a store mask whose leading axis must equal n_timepoints)."""
    thr = _guided_threshold()
    empty = (
        _make_foci(_SPLIT_LEVELS, seed=0)[0],          # real image
        np.zeros(SHAPE, dtype=np.uint8),                # empty feature mask
        _make_foci(_SPLIT_LEVELS, seed=0)[2],          # labels
    )
    img, mask, labels = _stack([_make_foci(_SPLIT_LEVELS, seed=1), empty])
    res = classify_by_cnr_stack(img, mask, labels, mode="guided", threshold=thr)
    assert res.low_stack.shape[0] == 2 and res.high_stack.shape[0] == 2
    # frame 1 (empty) contributes all-zero planes, no rows, no raise
    assert res.low_stack[1].sum() == 0 and res.high_stack[1].sum() == 0
    assert res.per_frame[1]["low_px"] == 0 and res.per_frame[1]["high_px"] == 0
    assert (res.table["timepoint"] == 1).sum() == 0


def test_classify_stack_single_population_frame_flagged_not_dim():
    """A frame whose bright group falls below MIN_FRACTION collapses to one population:
    all its foci land in `low` (unclassified, not 'dim') and it is flagged
    n_subpopulations==1 so a _low time-course can exclude it (ADV2)."""
    thr = _guided_threshold()
    flip = np.concatenate([np.full(63, 30.0), np.full(1, 400.0)])  # smaller group < 2%
    img, mask, labels = _stack(
        [_make_foci(_SPLIT_LEVELS, seed=0), _make_foci(flip, seed=1)]
    )
    res = classify_by_cnr_stack(img, mask, labels, mode="guided", threshold=thr)
    assert res.per_frame[0]["n_subpopulations"] == 2          # early frame splits
    assert res.per_frame[1]["n_subpopulations"] == 1          # late frame does not
    assert res.per_frame[1]["high_px"] == 0                   # bright focus dumped into low
    assert res.per_frame[1]["low_px"] > 0
    # the table marks the non-splitting frame so downstream can flag it
    f1 = res.table[res.table["timepoint"] == 1]
    assert (f1["n_subpopulations"] == 1).all()


def test_classify_stack_discover_mode_per_frame():
    """Discover mode (no threshold) splits each frame on its own CNR gap."""
    img, mask, labels = _stack([_make_foci(_SPLIT_LEVELS, seed=s) for s in (0, 1)])
    res = classify_by_cnr_stack(img, mask, labels, mode="discover")
    for fr in res.per_frame:
        assert fr["n_subpopulations"] == 2


def test_classify_stack_guided_requires_threshold():
    img, mask, labels = _stack([_make_foci(_SPLIT_LEVELS, seed=0)])
    with pytest.raises(ValueError, match="guided mode requires a threshold"):
        classify_by_cnr_stack(img, mask, labels, mode="guided", threshold=None)


def test_classify_stack_shape_mismatch_raises():
    img, mask, labels = _stack([_make_foci(_SPLIT_LEVELS, seed=0)])
    with pytest.raises(ValueError, match="share shape"):
        classify_by_cnr_stack(img, mask, labels[:, :10, :10], mode="discover")
