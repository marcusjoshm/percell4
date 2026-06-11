"""Tests for the background-estimator registry (plan U2)."""

from __future__ import annotations

import numpy as np
import pytest

from percell4.domain.analysis._impl._shared import label_and_filter
from percell4.domain.analysis._impl.per_particle_donut import estimate_bg_threshold
from percell4.domain.measure.bg_estimators import (
    BACKGROUND_ESTIMATORS,
    BG_ESTIMATOR_NAMES,
    BackgroundEstimate,
)

# ── Fixtures / builders ───────────────────────────────────────


def _constant_bg(value=50.0, noise=3.0, shape=(80, 80), seed=0):
    """Constant background + Gaussian noise — scalar estimators should zero it."""
    rng = np.random.default_rng(seed)
    return (value + rng.normal(0, noise, size=shape)).astype(float)


def _two_seeds(shape=(60, 60)):
    """``label_and_filter`` tuple for two small square seeds."""
    seed = np.zeros(shape, dtype=np.uint8)
    seed[20:24, 20:24] = 1
    seed[40:44, 40:44] = 1
    return label_and_filter(seed, min_size=1)


# ── Registry / names drift guard ──────────────────────────────


def test_registry_keys_match_names():
    """BACKGROUND_ESTIMATORS keys are exactly BG_ESTIMATOR_NAMES (drift guard)."""
    assert set(BACKGROUND_ESTIMATORS) == set(BG_ESTIMATOR_NAMES)


def test_expected_keys_present():
    expected = {
        "donut-median",
        "donut-mean",
        "gaussian-peak",
        "percentile",
        "mad",
        "stddev",
        "rolling-ball",
        "donut-surface",
    }
    assert set(BACKGROUND_ESTIMATORS) == expected


# ── Constant background → scalar estimators zero it ───────────


@pytest.mark.parametrize("name", ["gaussian-peak", "mad", "stddev", "percentile"])
def test_constant_bg_residual_near_zero(name):
    """Scalar estimators' residual ~ 0 within the group on constant background."""
    img = _constant_bg(value=50.0, noise=3.0)
    gmask = np.ones_like(img, dtype=bool)
    est = BACKGROUND_ESTIMATORS[name](img, gmask, None, {})
    assert not est.is_empty
    # Residual mean should be near zero (within a noise-scale slack).
    assert abs(float(np.nanmean(est.residual[gmask]))) < 1.0


@pytest.mark.parametrize("name", ["gaussian-peak", "mad", "stddev"])
def test_constant_bg_sigma_near_noise(name):
    """gaussian-peak / mad / stddev recover the noise scale (~3.0)."""
    img = _constant_bg(value=50.0, noise=3.0)
    gmask = np.ones_like(img, dtype=bool)
    est = BACKGROUND_ESTIMATORS[name](img, gmask, None, {})
    assert est.sigma is not None
    assert est.sigma == pytest.approx(3.0, abs=1.5)


def test_stddev_is_pulled_up_by_bright_tail_unlike_mad():
    """stddev (non-robust) inflates on a bright-foci tail; MAD stays near the noise.

    Documents why the two are offered as distinct options: on a frame with a
    sparse bright tail the standard deviation rises well above the robust MAD
    scale, so the k·σ contrast margin differs between them.
    """
    rng = np.random.RandomState(0)
    img = 50.0 + rng.normal(0, 3.0, (200, 200))
    img[:10, :10] = 5000.0  # sparse bright foci (1% area)
    gmask = np.ones_like(img, dtype=bool)
    sig_std = BACKGROUND_ESTIMATORS["stddev"](img, gmask, None, {}).sigma
    sig_mad = BACKGROUND_ESTIMATORS["mad"](img, gmask, None, {}).sigma
    assert sig_mad == pytest.approx(3.0, abs=1.0)  # robust to the tail
    assert sig_std > 5 * sig_mad  # non-robust: the bright tail pulls std way up


def test_percentile_sigma_is_none():
    img = _constant_bg()
    est = BACKGROUND_ESTIMATORS["percentile"](img, np.ones_like(img, bool), None, {})
    assert est.sigma is None


def test_percentile_param_p():
    """The ``p`` param selects the percentile used as background."""
    img = np.tile(np.arange(100, dtype=float), (10, 1))  # 0..99 columns
    gmask = np.ones_like(img, dtype=bool)
    est = BACKGROUND_ESTIMATORS["percentile"](img, gmask, None, {"p": 90.0})
    bg = np.percentile(img, 90.0)
    assert np.allclose(est.residual, img - bg)


# ── Gradient / surface background → rolling-ball flattens ─────


def test_rolling_ball_flattens_ramp():
    """Linear-ramp background → rolling-ball residual std << original std."""
    rng = np.random.default_rng(7)
    ramp = np.tile(np.linspace(0, 100, 100), (100, 1)).astype(float)
    img = ramp + rng.normal(0, 1.0, size=(100, 100))
    gmask = np.ones_like(img, dtype=bool)
    est = BACKGROUND_ESTIMATORS["rolling-ball"](img, gmask, None, {"radius": 25})
    assert not est.is_empty
    assert np.nanstd(est.residual) < 0.25 * float(img.std())


def test_rolling_ball_flattens_gaussian_bump():
    """Gaussian-bump background → rolling-ball residual flatter than original."""
    rng = np.random.default_rng(8)
    bump = np.fromfunction(
        lambda y, x: 80 * np.exp(-((y - 50) ** 2 + (x - 50) ** 2) / (2 * 12**2)),
        (100, 100),
    )
    img = bump + rng.normal(0, 1.0, size=(100, 100))
    gmask = np.ones_like(img, dtype=bool)
    est = BACKGROUND_ESTIMATORS["rolling-ball"](img, gmask, None, {"radius": 25})
    assert np.nanstd(est.residual) < float(img.std())


# ── Double-k pin (gaussian-peak) ──────────────────────────────


def test_gaussian_peak_double_k_pin():
    """gaussian-peak sigma == estimate_bg_threshold sigma; residual uses mu, not threshold."""
    rng = np.random.default_rng(42)
    img = (100.0 + rng.normal(0, 5.0, size=(100, 100))).astype(float)
    gmask = np.ones_like(img, dtype=bool)

    vals = img[gmask & np.isfinite(img)]
    threshold, mu, sigma = estimate_bg_threshold(vals, k_sigma=2.5)

    est = BACKGROUND_ESTIMATORS["gaussian-peak"](img, gmask, None, {"k_sigma": 2.5})

    # sigma is taken verbatim from the fit.
    assert est.sigma == float(sigma)
    # residual = image - mu, NEVER image - threshold (avoid double-k).
    assert np.allclose(est.residual, img - mu)
    assert not np.allclose(est.residual, img - threshold)


def test_gaussian_peak_too_few_pixels_is_empty():
    """A tiny group → gaussian-peak cannot fit → is_empty, no raise."""
    img = _constant_bg(shape=(80, 80))
    gmask = np.zeros_like(img, dtype=bool)
    gmask[0, 0] = True  # single pixel
    est = BACKGROUND_ESTIMATORS["gaussian-peak"](img, gmask, None, {})
    assert est.is_empty


# ── Donut estimators ──────────────────────────────────────────


@pytest.mark.parametrize("name", ["donut-median", "donut-mean"])
def test_donut_constant_bg_residual_zero(name):
    """Donut rings over constant background → residual exactly 0 (float, no rounding)."""
    seeds = _two_seeds()
    img = np.full((60, 60), 30.0, dtype=float)
    est = BACKGROUND_ESTIMATORS[name](img, np.ones((60, 60), bool), seeds, {})
    assert not est.is_empty
    assert np.allclose(est.residual, 0.0)


@pytest.mark.parametrize("name", ["donut-median", "donut-mean"])
def test_donut_none_seeds_is_empty(name):
    """seeds=None → donut estimators return is_empty=True (no raise)."""
    img = np.full((60, 60), 30.0, dtype=float)
    est = BACKGROUND_ESTIMATORS[name](img, np.ones((60, 60), bool), None, {})
    assert est.is_empty


@pytest.mark.parametrize("name", ["donut-median", "donut-mean"])
def test_donut_empty_seeds_is_empty(name):
    """Empty region_ids → donut estimators return is_empty=True (no raise)."""
    empty_seeds = (
        np.zeros((60, 60), dtype=int),
        np.zeros((60, 60), dtype=bool),
        np.array([], dtype=int),
        {},
    )
    img = np.full((60, 60), 30.0, dtype=float)
    est = BACKGROUND_ESTIMATORS[name](img, np.ones((60, 60), bool), empty_seeds, {})
    assert est.is_empty


def test_donut_uses_float_not_int():
    """DECLARED FORK: donut bg is the float median, not int(round(...))."""
    seeds = _two_seeds()
    # Background ring at a non-integer value so int-rounding would differ.
    img = np.full((60, 60), 30.4, dtype=float)
    est = BACKGROUND_ESTIMATORS["donut-median"](img, np.ones((60, 60), bool), seeds, {})
    # If int-rounding leaked in, residual would be 30.4 - 30 = 0.4, not ~0.
    assert np.allclose(est.residual, 0.0)


# ── All-NaN group ─────────────────────────────────────────────


@pytest.mark.parametrize("name", ["gaussian-peak", "mad", "stddev", "percentile", "rolling-ball"])
def test_all_nan_group_is_empty_no_propagation(name):
    """All-NaN group → defined is_empty result; no NaN crash, residual finite."""
    img = np.full((40, 40), np.nan, dtype=float)
    gmask = np.ones_like(img, dtype=bool)
    est = BACKGROUND_ESTIMATORS[name](img, gmask, None, {})
    assert est.is_empty
    assert est.residual.shape == img.shape
    assert not np.isnan(est.residual).any()


@pytest.mark.parametrize("name", ["donut-median", "donut-mean"])
def test_all_nan_group_donut_is_empty(name):
    """All-NaN image with seeds → no finite ring pixels → is_empty."""
    seeds = _two_seeds()
    img = np.full((60, 60), np.nan, dtype=float)
    est = BACKGROUND_ESTIMATORS[name](img, np.ones((60, 60), bool), seeds, {})
    assert est.is_empty


# ── donut-surface stub ────────────────────────────────────────


def test_donut_surface_not_implemented():
    img = _constant_bg()
    with pytest.raises(NotImplementedError):
        BACKGROUND_ESTIMATORS["donut-surface"](img, np.ones_like(img, bool), None, {})


# ── Contract: shape & sigma type ──────────────────────────────


@pytest.mark.parametrize(
    "name",
    ["donut-median", "donut-mean", "gaussian-peak", "percentile", "mad", "rolling-ball"],
)
def test_residual_shape_and_sigma_type(name):
    """Every (non-stub) estimator returns residual.shape == image.shape; sigma float|None."""
    img = _constant_bg(shape=(70, 65))
    gmask = np.ones_like(img, dtype=bool)
    seeds = _two_seeds(shape=(70, 65))
    est = BACKGROUND_ESTIMATORS[name](img, gmask, seeds, {})
    assert isinstance(est, BackgroundEstimate)
    assert est.residual.shape == img.shape
    assert est.sigma is None or isinstance(est.sigma, float)
    assert isinstance(est.is_empty, bool)
