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


# ── granule-size finder ───────────────────────────────────────────────────


def _black_bg_image(centers, radius, *, cell_box, dilute=50.0, fg=220.0, shape=(256, 256), seed=0):
    """A frame that is mostly black (0) outside a dilute 'cell' box with granules."""
    rng = np.random.default_rng(seed)
    img = np.zeros(shape, dtype=np.float32)
    (r0, r1, c0, c1) = cell_box
    img[r0:r1, c0:c1] = dilute + rng.normal(0.0, 2.0, size=(r1 - r0, c1 - c0)).astype(np.float32)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), radius, shape=shape)
        img[rr, cc] = fg
    return img.astype(np.float32)


def test_granule_size_is_registered():
    assert "granule-size" in WINDOW_FINDERS


def test_granule_size_positive_and_scales_with_size():
    small = WINDOW_FINDERS["granule-size"](_blobs_image([(60, 60), (60, 140)], radius=5), {})
    large = WINDOW_FINDERS["granule-size"](_blobs_image([(80, 80), (80, 160)], radius=14), {})
    assert small > 0.0
    assert large > small  # bigger granules -> bigger window estimate


def test_granule_size_c_scales_linearly():
    img = _blobs_image([(60, 60), (60, 140)], radius=8)
    a = WINDOW_FINDERS["granule-size"](img, {"c": 4.0})
    b = WINDOW_FINDERS["granule-size"](img, {"c": 8.0})
    assert b == 2.0 * a


def test_granule_size_window_is_several_times_granule_diameter():
    # radius-8 granules => diameter ~16; with c=4.5 the window should land well
    # above the granule diameter (the whole point: window ~ 4-5x the focus).
    img = _blobs_image([(70, 70), (70, 150), (150, 110)], radius=8)
    raw = WINDOW_FINDERS["granule-size"](img, {"c": 4.5})
    assert raw > 3.0 * 16  # comfortably several times the ~16px granule diameter


def test_granule_size_robust_on_black_background_whole_frame():
    """With cp_mask=None on a mostly-black frame, granule-size does NOT collapse.

    This is the failure mode that broke otsu-mean (whole-frame Otsu over-
    segments the black border). granule-size uses a background-invariant
    high-pass, so it returns a granule-scale window even whole-frame.
    """
    img = _black_bg_image(
        [(120, 120), (120, 150), (150, 135)], radius=9, cell_box=(90, 180, 90, 180)
    )
    raw = WINDOW_FINDERS["granule-size"](img, {"c": 4.5})
    assert raw > 30.0  # a real granule-scale window, not a collapsed floor


def test_granule_size_cp_mask_ignores_out_of_cell_junk():
    cell_box = (90, 180, 90, 180)
    centers = [(120, 120), (120, 150)]
    clean = _black_bg_image(centers, radius=8, cell_box=cell_box)
    junky = clean.copy()
    rr, cc = disk((20, 20), 10, shape=clean.shape)  # bright junk far outside the cell
    junky[rr, cc] = 300.0
    cp = np.zeros(clean.shape, dtype=np.uint8)
    cp[90:180, 90:180] = 1
    w_clean = WINDOW_FINDERS["granule-size"](clean, {}, cp_mask=cp)
    w_junky = WINDOW_FINDERS["granule-size"](junky, {}, cp_mask=cp)
    assert w_clean == w_junky  # out-of-cell junk does not affect the in-cell estimate


def test_granule_size_constant_image_returns_zero():
    assert WINDOW_FINDERS["granule-size"](np.full((64, 64), 12.0, dtype=np.float32), {}) == 0.0


def test_granule_size_all_nan_returns_zero_no_raise():
    assert WINDOW_FINDERS["granule-size"](np.full((40, 40), np.nan, dtype=np.float32), {}) == 0.0


def test_granule_size_beats_otsu_mean_floor_intuition():
    """On a black-background granule frame, granule-size yields a usable window
    where the speck-prone otsu-mean is far less reliable — sanity that the new
    finder is in the granule regime."""
    img = _black_bg_image([(120, 120), (120, 150), (150, 135)], radius=10, cell_box=(90, 180, 90, 180))
    gs = WINDOW_FINDERS["granule-size"](img, {"c": 4.5})
    assert gs > 0.0
