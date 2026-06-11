"""Tests for the window-finder registry + finders (plan U1+)."""

from __future__ import annotations

import numpy as np
from skimage.draw import disk

from percell4.domain.measure.window_finder_names import WINDOW_FINDER_NAMES
from percell4.domain.measure.window_finders import WINDOW_FINDERS


def _blobs_image(centers, radius, *, bg=10.0, fg=200.0, shape=(200, 200), seed=0):
    rng = np.random.default_rng(seed)
    img = bg + rng.normal(0.0, 1.5, size=shape).astype(np.float32)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), radius, shape=shape)
        img[rr, cc] = fg
    return img.astype(np.float32)


# ── registry contract ────────────────────────────────────────────────────


def test_registry_keys_match_names():
    """WINDOW_FINDERS keys are exactly WINDOW_FINDER_NAMES (drift guard)."""
    assert set(WINDOW_FINDERS) == set(WINDOW_FINDER_NAMES)


def test_otsu_mean_is_registered():
    assert "otsu-mean" in WINDOW_FINDERS


# ── otsu-mean baseline finder (raw, unclamped) ───────────────────────────


def test_otsu_mean_returns_positive_raw_on_blobs():
    img = _blobs_image([(50, 50), (50, 150), (150, 100)], radius=7)
    raw = WINDOW_FINDERS["otsu-mean"](img, {})
    assert raw > 0.0


def test_otsu_mean_scales_with_granule_size():
    small = WINDOW_FINDERS["otsu-mean"](_blobs_image([(50, 50), (50, 150)], radius=4), {})
    large = WINDOW_FINDERS["otsu-mean"](_blobs_image([(80, 80), (80, 200)], radius=16), {})
    assert large > small


def test_otsu_mean_factor_param_scales_linearly():
    img = _blobs_image([(50, 50), (50, 150)], radius=8)
    a = WINDOW_FINDERS["otsu-mean"](img, {"factor": 2.0})
    b = WINDOW_FINDERS["otsu-mean"](img, {"factor": 4.0})
    assert b == 2.0 * a


def test_otsu_mean_constant_image_returns_zero():
    img = np.full((64, 64), 12.0, dtype=np.float32)
    assert WINDOW_FINDERS["otsu-mean"](img, {}) == 0.0


def test_otsu_mean_all_nan_returns_zero_no_raise():
    img = np.full((40, 40), np.nan, dtype=np.float32)
    assert WINDOW_FINDERS["otsu-mean"](img, {}) == 0.0


def test_otsu_mean_only_subfloor_specks_returns_zero():
    img = np.full((50, 50), 10.0, dtype=np.float32)
    img[10, 10] = img[20, 30] = 200.0  # single-pixel specks (< noise floor)
    assert WINDOW_FINDERS["otsu-mean"](img, {}) == 0.0
