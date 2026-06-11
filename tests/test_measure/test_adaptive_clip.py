"""Tests for the whole-frame adaptive runner + auto-window estimator (U1)."""

from __future__ import annotations

import numpy as np
import pytest
from skimage.draw import disk

from percell4.domain.measure.adaptive_clip import (
    AUTO_WINDOW_MAX,
    AUTO_WINDOW_MIN,
    auto_window,
    detect_adaptive_whole_frame,
    estimate_adaptive_window,
    otsu_first_pass,
    resolve_min_area_px,
)
from percell4.domain.measure.thresholding import apply_gaussian_smoothing
from percell4.workflows.models import PunctaDetectorSettings


def _adaptive_settings(window_px: int = 15, k: float = 2.25) -> PunctaDetectorSettings:
    return PunctaDetectorSettings(
        detector_name="adaptive",
        seed_detector_name="otsu",
        background_estimator_name="gaussian-peak",
        detector_params={"window_px": window_px, "k": k},
        min_spot_px=3,
        spot_scale_prior=(1.0, 4.0),
    )


def _blobs_image(centers, radius, *, bg=10.0, fg=200.0, shape=(200, 200), seed=0):
    rng = np.random.default_rng(seed)
    img = bg + rng.normal(0.0, 1.5, size=shape).astype(np.float32)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), radius, shape=shape)
        img[rr, cc] = fg
    return img.astype(np.float32)


def _disk_mask(centers, radius, shape=(300, 300)) -> np.ndarray:
    mask = np.zeros(shape, dtype=bool)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), radius, shape=shape)
        mask[rr, cc] = True
    return mask


# ── detect_adaptive_whole_frame ──────────────────────────────────────────

def test_detect_whole_frame_marks_blobs_and_is_binary():
    centers = [(50, 50), (50, 150), (150, 100)]
    img = _blobs_image(centers, radius=6)
    mask = detect_adaptive_whole_frame(img, 1.0, _adaptive_settings())

    assert mask.dtype == np.uint8
    assert set(np.unique(mask)).issubset({0, 1})
    assert mask.shape == img.shape
    # Each blob center is detected; a far background corner is not.
    for cy, cx in centers:
        assert mask[cy, cx] == 1
    assert mask[2, 2] == 0
    # Foreground is a small fraction of the frame (blobs, not the whole image).
    assert mask.mean() < 0.2


def test_detect_whole_frame_constant_image_is_empty():
    img = np.full((64, 64), 12.0, dtype=np.float32)
    mask = detect_adaptive_whole_frame(img, 1.0, _adaptive_settings())
    assert mask.dtype == np.uint8
    assert int(mask.sum()) == 0


# ── otsu_first_pass ──────────────────────────────────────────────────────

def test_otsu_first_pass_marks_bright_regions():
    img = _blobs_image([(40, 40), (40, 90)], radius=8)
    mask = otsu_first_pass(img)
    assert mask.dtype == bool
    assert mask[40, 40] and mask[40, 90]
    assert not mask[2, 2]


def test_otsu_first_pass_constant_returns_empty():
    assert otsu_first_pass(np.full((32, 32), 5.0, dtype=np.float32)).sum() == 0


# ── estimate_adaptive_window ─────────────────────────────────────────────

def test_estimate_window_is_odd_and_in_range():
    win = estimate_adaptive_window(_disk_mask([(60, 60), (60, 180), (180, 120)], radius=8))
    assert win % 2 == 1
    assert AUTO_WINDOW_MIN <= win <= AUTO_WINDOW_MAX


def test_estimate_window_scales_with_granule_size():
    small = estimate_adaptive_window(_disk_mask([(50, 50), (50, 150)], radius=4))
    large = estimate_adaptive_window(_disk_mask([(80, 80), (80, 200)], radius=16))
    assert large > small  # bigger granules -> bigger window
    assert small % 2 == 1 and large % 2 == 1


def test_estimate_window_empty_mask_returns_floor():
    win = estimate_adaptive_window(np.zeros((50, 50), dtype=bool))
    assert win == (AUTO_WINDOW_MIN | 1)


def test_estimate_window_only_noise_returns_floor():
    # A few single-pixel specks below the noise floor -> no usable granules.
    mask = np.zeros((50, 50), dtype=bool)
    mask[10, 10] = mask[20, 30] = True
    assert estimate_adaptive_window(mask, noise_floor_px=3) == (AUTO_WINDOW_MIN | 1)


def test_estimate_window_clamped_to_max():
    # One huge granule would exceed the cap; result is clamped and odd.
    win = estimate_adaptive_window(_disk_mask([(300, 300)], radius=160, shape=(640, 640)))
    assert win == AUTO_WINDOW_MAX
    assert win % 2 == 1


# ── auto_window orchestrator (registry dispatch) ─────────────────────────


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_auto_window_otsu_mean_matches_legacy_estimator(seed):
    """The 'otsu-mean' finder via auto_window reproduces the legacy estimator.

    Characterization parity (registered-analysis Rule 2): the bake-off baseline
    must be byte-identical to today's estimate_adaptive_window(otsu_first_pass)
    so the bake-off measures a faithful baseline, not a re-implementation drift.
    """
    img = _blobs_image([(50, 50), (50, 150), (150, 100)], radius=7, seed=seed)
    gs = 1.0
    smoothed = apply_gaussian_smoothing(img.astype(np.float32), gs)
    legacy = estimate_adaptive_window(otsu_first_pass(smoothed))
    new = auto_window(img, gs, _adaptive_settings(), method="otsu-mean")
    assert new == legacy


def test_auto_window_is_odd_and_in_range():
    img = _blobs_image([(60, 60), (60, 180), (180, 120)], radius=8)
    win = auto_window(img, 1.0, _adaptive_settings(), method="otsu-mean")
    assert win % 2 == 1
    assert AUTO_WINDOW_MIN <= win <= AUTO_WINDOW_MAX


def test_auto_window_constant_image_returns_floor():
    img = np.full((64, 64), 12.0, dtype=np.float32)
    assert auto_window(img, 1.0, _adaptive_settings(), method="otsu-mean") == (AUTO_WINDOW_MIN | 1)


def test_auto_window_respects_clamp_args():
    img = _blobs_image([(50, 50), (50, 150)], radius=8)
    # A tight hi clamps the result; still odd.
    win = auto_window(img, 1.0, _adaptive_settings(), method="otsu-mean", lo=11, hi=21)
    assert win <= 21 and win % 2 == 1


# ── resolve_min_area_px ──────────────────────────────────────────────────

def test_resolve_min_area_px_passthrough():
    assert resolve_min_area_px(9, "px", None) == 9
    assert resolve_min_area_px(9.6, "px", 0.5) == 10  # rounds


def test_resolve_min_area_px_um2_conversion():
    # 1 µm² at 0.5 µm/px -> 1 / 0.25 = 4 px.
    assert resolve_min_area_px(1.0, "um2", 0.5) == 4


def test_resolve_min_area_px_um2_without_calibration_raises():
    with pytest.raises(ValueError):
        resolve_min_area_px(1.0, "um2", None)
    with pytest.raises(ValueError):
        resolve_min_area_px(1.0, "um2", 0.0)


def test_resolve_min_area_px_unknown_unit_raises():
    with pytest.raises(ValueError):
        resolve_min_area_px(1.0, "inches", 0.5)
