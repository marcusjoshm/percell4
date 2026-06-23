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
    classify_by_cnr,
    measure_cnr,
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
