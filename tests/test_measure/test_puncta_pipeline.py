"""Tests for the pure two-pass puncta detection pipeline (plan U4)."""

from __future__ import annotations

import numpy as np

from percell4.domain.measure import puncta_pipeline
from percell4.domain.measure.puncta_pipeline import (
    DEFAULT_SCALE_RANGE,
    calibrate_scale_range,
    compute_seeds,
    detect_two_pass,
    seed_sigmas,
)
from percell4.workflows.models import PunctaDetectorSettings

H = W = 64


def _field(spots, *, bg=100.0, ramp=0.0, noise=2.0, seed=0):
    """Synthetic float frame: flat (or ramped) background + Gaussian spots."""
    rng = np.random.RandomState(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    img = np.full((H, W), bg) + rng.normal(0, noise, (H, W))
    if ramp:
        img += ramp * xx
    for y, x, amp, s in spots:
        img += amp * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * s * s))
    return img


def _group_mask(y0=8, y1=56, x0=8, x1=56):
    m = np.zeros((H, W), bool)
    m[y0:y1, x0:x1] = True
    return m


def _log_settings(**overrides):
    defaults = dict(
        detector_name="log",
        background_estimator_name="gaussian-peak",
        detector_params={"threshold_rel": 0.05},
        min_spot_px=2,
    )
    defaults.update(overrides)
    return PunctaDetectorSettings(**defaults)


def _recovered(mask, centers, tol=2):
    return sum(
        1 for y, x, *_ in centers if mask[y - tol : y + tol + 1, x - tol : x + tol + 1].any()
    )


def test_recovers_mixed_size_and_dim_foci():
    centers = [(20, 20, 200, 2.0), (20, 44, 60, 1.2), (44, 20, 150, 2.5), (44, 44, 40, 1.0)]
    img = _field(centers)
    mask = detect_two_pass(img, _group_mask(), _log_settings())
    assert _recovered(mask, centers) == 4  # incl. the dim amp-40 / amp-60 foci


def test_output_is_binary_uint8_and_never_none():
    img = _field([(32, 32, 150, 2.0)])
    mask = detect_two_pass(img, _group_mask(), _log_settings())
    assert mask is not None
    assert mask.dtype == np.uint8
    assert set(np.unique(mask).tolist()) <= {0, 1}
    assert mask.shape == (H, W)


def test_empty_group_returns_all_zero_never_none():
    img = _field([(32, 32, 150, 2.0)])
    mask = detect_two_pass(img, np.zeros((H, W), bool), _log_settings())
    assert mask is not None
    assert mask.shape == (H, W)
    assert mask.sum() == 0


def test_signal_free_group_is_not_accept_all():
    # A sigma-aware detector on a flat noisy group must not flood the cell.
    flat = _field([], noise=2.0)
    gm = _group_mask()
    settings = PunctaDetectorSettings(
        detector_name="bg-k-sigma",
        background_estimator_name="gaussian-peak",
        detector_params={"k": 4.0},
        min_spot_px=2,
    )
    mask = detect_two_pass(flat, gm, settings)
    # Must be nowhere near accept-all (the whole group lit up).
    assert mask.sum() < 0.02 * int(gm.sum())


def test_over_capture_haze_is_subtracted():
    # A bright diffuse haze plus discrete foci: pass-2 background subtraction
    # should keep the foci while not lighting the whole hazy region.
    centers = [(24, 24, 180, 2.0), (40, 40, 160, 2.0)]
    img = _field(centers, bg=100.0, ramp=3.0)  # strong gradient "haze"
    mask = detect_two_pass(img, _group_mask(), _log_settings())
    assert _recovered(mask, centers) == 2
    assert mask.sum() < 0.25 * int(_group_mask().sum())  # not flooding the haze


def test_pass1_runs_once_when_seeds_supplied(monkeypatch):
    img = _field([(32, 32, 150, 2.0)])
    gm = _group_mask()
    settings = _log_settings(seed_detector_name="log")

    calls = {"n": 0}
    real = puncta_pipeline.DETECTORS["log"]

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setitem(puncta_pipeline.DETECTORS, "log", counting)

    # seeds=None: pass-1 seed detector ("log") + pass-2 detector ("log") = 2 calls.
    calls["n"] = 0
    detect_two_pass(img, gm, settings, seeds=None)
    with_seed_pass = calls["n"]

    # Pre-supplied seeds: pass-1 is skipped, so "log" is called once (pass-2 only).
    from percell4.domain.analysis._impl._shared import label_and_filter

    seeds = label_and_filter(np.zeros((H, W), np.uint8), 0)
    calls["n"] = 0
    detect_two_pass(img, gm, settings, seeds=seeds)
    assert calls["n"] == with_seed_pass - 1


def test_refined_scale_range_threads_to_detector(monkeypatch):
    img = _field([(32, 32, 150, 2.0)])
    gm = _group_mask()
    settings = _log_settings(seed_detector_name="bg-k-sigma")  # avoid log in pass-1

    seen = {}

    def capturing(residual, group_mask, sigma, params):
        seen.update(params)
        return np.zeros(residual.shape, np.uint8)

    monkeypatch.setitem(puncta_pipeline.DETECTORS, "log", capturing)
    detect_two_pass(img, gm, settings, scale_range=(2.0, 7.0))
    assert seen.get("min_sigma") == 2.0
    assert seen.get("max_sigma") == 7.0


def test_scale_range_defaults_to_prior_then_constant(monkeypatch):
    img = _field([(32, 32, 150, 2.0)])
    gm = _group_mask()
    seen = {}

    def capturing(residual, group_mask, sigma, params):
        seen.update(params)
        return np.zeros(residual.shape, np.uint8)

    monkeypatch.setitem(puncta_pipeline.DETECTORS, "log", capturing)
    # No scale_range arg, no prior -> DEFAULT_SCALE_RANGE.
    detect_two_pass(img, gm, _log_settings(seed_detector_name="bg-k-sigma"))
    assert (seen["min_sigma"], seen["max_sigma"]) == DEFAULT_SCALE_RANGE

    # Locked prior wins when no explicit scale_range is passed.
    seen.clear()
    detect_two_pass(
        img,
        gm,
        _log_settings(seed_detector_name="bg-k-sigma", spot_scale_prior=(1.5, 5.0)),
    )
    assert (seen["min_sigma"], seen["max_sigma"]) == (1.5, 5.0)


def test_under_capture_fallback_uses_robust_background(monkeypatch):
    # Force the configured donut estimator to find no seeds (is_empty) so the
    # fallback ladder drops to gaussian-peak; a dim focus must still survive
    # (the rung-2 background must not be inflated by the focus).
    centers = [(32, 32, 50, 1.2)]  # single dim focus
    img = _field(centers)
    gm = _group_mask()
    settings = _log_settings(background_estimator_name="donut-median")

    # donut-median with empty seeds returns is_empty=True -> ladder -> gaussian-peak.
    from percell4.domain.measure.bg_estimators import BACKGROUND_ESTIMATORS

    real_gp = BACKGROUND_ESTIMATORS["gaussian-peak"]
    used = {"gp": 0}

    def spy_gp(*a, **k):
        used["gp"] += 1
        return real_gp(*a, **k)

    monkeypatch.setitem(BACKGROUND_ESTIMATORS, "gaussian-peak", spy_gp)
    # Empty seeds tuple forces donut-median to is_empty.
    from percell4.domain.analysis._impl._shared import label_and_filter

    empty_seeds = label_and_filter(np.zeros((H, W), np.uint8), 0)
    mask = detect_two_pass(img, gm, settings, seeds=empty_seeds)
    assert used["gp"] >= 1  # fell back to gaussian-peak rung
    assert _recovered(mask, centers) == 1  # dim focus still recovered


# ── Spot-scale calibration (U6) ──────────────────────────────


def test_calibrate_cold_start_uses_default():
    # No prior + no seeds -> the bootstrap default range.
    refined, clamped = calibrate_scale_range([], prior=None)
    assert refined == DEFAULT_SCALE_RANGE and clamped is False


def test_calibrate_sparse_seeds_retains_prior():
    prior = (1.0, 4.0)
    refined, clamped = calibrate_scale_range([2.0, 2.1], prior=prior, n_calib=5)
    assert refined == prior  # < n_calib -> prior retained
    assert clamped is False


def test_calibrate_narrows_within_prior():
    prior = (1.0, 6.0)
    # Tightly clustered seed sigmas around 2-3 -> candidate narrows within prior.
    sigmas = [2.0, 2.2, 2.5, 2.8, 3.0, 2.3, 2.6]
    refined, clamped = calibrate_scale_range(sigmas, prior=prior)
    assert prior[0] <= refined[0] <= refined[1] <= prior[1]
    assert refined != prior  # genuinely narrowed
    assert clamped is False


def test_calibrate_clamps_when_candidate_exceeds_prior():
    prior = (2.0, 3.0)
    # Big seeds (sigma ~5-8) push the candidate above the prior bracket.
    sigmas = [5.0, 6.0, 7.0, 8.0, 6.5, 5.5]
    refined, clamped = calibrate_scale_range(sigmas, prior=prior)
    assert clamped is True  # candidate fell outside the locked prior
    assert refined[0] >= prior[0] and refined[1] <= prior[1]  # never expands


def test_calibrate_is_deterministic():
    sigmas = [1.5, 2.0, 2.5, 3.0, 3.5, 2.2]
    a, _ = calibrate_scale_range(sigmas, prior=(1.0, 5.0))
    b, _ = calibrate_scale_range(sigmas, prior=(1.0, 5.0))
    assert a == b


def test_compute_seeds_and_sigmas_on_spots():
    centers = [(20, 20, 200, 2.0), (44, 44, 150, 2.5)]
    img = _field(centers)
    gm = _group_mask()
    settings = _log_settings(seed_detector_name="log")
    seeds = compute_seeds(img, gm, settings, (1.0, 4.0))
    sigmas = seed_sigmas(seeds)
    assert len(sigmas) >= 1  # found at least one seed
    assert all(s > 0 for s in sigmas)


# ── size filter (whole-frame perf regression) ────────────────────────────────


def test_size_filter_keeps_and_drops_by_area():
    """Components below ``min_spot_px`` are dropped; those at/above are kept."""
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[2, 2] = 1  # 1-px speck -> dropped (area 1 < 3)
    mask[5:7, 5:7] = 1  # 4-px blob -> kept (area 4 >= 3)
    mask[10:13, 10:13] = 1  # 9-px blob -> kept

    out = puncta_pipeline._size_filter(mask, min_spot_px=3, max_spot_px=None)

    assert out.dtype == np.uint8
    assert set(np.unique(out)).issubset({0, 1})
    assert out[2, 2] == 0  # speck removed
    assert out[5:7, 5:7].sum() == 4  # blob survives intact
    assert out[10:13, 10:13].sum() == 9


def test_size_filter_respects_max_spot_px():
    """``max_spot_px`` drops components larger than the cap."""
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[5:7, 5:7] = 1  # area 4 -> within [3, 5], kept
    mask[10:14, 10:14] = 1  # area 16 -> exceeds 5, dropped

    out = puncta_pipeline._size_filter(mask, min_spot_px=3, max_spot_px=5)

    assert out[5:7, 5:7].sum() == 4
    assert out[10:14, 10:14].sum() == 0


def test_size_filter_all_zero_mask_returns_all_zero():
    out = puncta_pipeline._size_filter(np.zeros((16, 16), dtype=np.uint8), 3, None)
    assert out.dtype == np.uint8
    assert out.sum() == 0


def test_size_filter_scales_to_many_components():
    """Whole-frame regression: thousands of components must filter quickly.

    The old per-region ``out[lab == prop.label] = 1`` loop is ``O(P*H*W)`` and
    takes far longer than this bound on this input; the vectorized filter is
    ``O(H*W)`` and returns near-instantly. The generous ceiling catches a
    regression to the quadratic form without being flaky on slow CI.
    """
    import time

    # ~10k isolated 1-px specks (dropped) on a 1000x1000 frame, plus a handful
    # of larger blobs that survive — the worst case for a per-region rescan.
    mask = np.zeros((1000, 1000), dtype=np.uint8)
    mask[::10, ::10] = 1  # 100x100 == 10_000 single-pixel components
    mask[500:505, 500:505] = 1  # one 25-px blob that must survive

    start = time.perf_counter()
    out = puncta_pipeline._size_filter(mask, min_spot_px=3, max_spot_px=None)
    elapsed = time.perf_counter() - start

    assert out[500:505, 500:505].sum() == 25  # large blob kept
    assert out[0, 0] == 0  # a 1-px speck dropped
    assert elapsed < 5.0, f"_size_filter too slow ({elapsed:.1f}s) — quadratic regression?"
