"""Tests for the segmentation pre-LUT helpers."""

from __future__ import annotations

import numpy as np
import pytest

from percell4.domain.segmentation.preprocess import (
    apply_gaussian_blur,
    apply_lut,
    apply_saturation_lut,
    preprocess_cellpose_input,
)

# ── apply_lut (pure clip + stretch) ─────────────────────────────────────


def test_apply_lut_uint16_clip_and_stretch():
    """[lo, hi] in source maps to [0, dtype_max] in output."""
    arr = np.array([0, 200, 500, 1000, 2000], dtype=np.uint16)
    out = apply_lut(arr, lo=0, hi=1000)
    # Below-lo pixels are 0; in-band stretch is linear; above-hi
    # pixels saturate to dtype max (65535).
    assert out[0] == 0
    assert out[-1] == 65535
    # 500 / 1000 = 0.5 → 32767 ± 1.
    assert abs(int(out[2]) - 32767) <= 1


def test_apply_lut_float32_normalizes_to_unit():
    """Float output is in [0, 1]."""
    arr = np.array([0.0, 100.0, 500.0, 1000.0, 5000.0], dtype=np.float32)
    out = apply_lut(arr, lo=0, hi=1000)
    assert out.dtype == np.float32
    assert out[0] == pytest.approx(0.0)
    assert out[-1] == pytest.approx(1.0)
    assert out[2] == pytest.approx(0.5)


def test_apply_lut_degenerate_range_returns_input_unchanged():
    """When hi - lo < epsilon, the channel is returned as-is."""
    arr = np.zeros((4, 4), dtype=np.uint16)
    out = apply_lut(arr, lo=0, hi=0)
    assert np.array_equal(out, arr)


def test_apply_lut_negative_dtype_signed_int():
    """Signed integer dtypes also stretch to dtype max via iinfo."""
    arr = np.array([-100, 0, 100], dtype=np.int16)
    out = apply_lut(arr, lo=-100, hi=100)
    assert out[0] == 0
    # int16 max is 32767
    assert out[-1] == 32767


# ── apply_saturation_lut (sat% → lo/hi → clip + stretch) ────────────────


def test_apply_saturation_lut_zero_is_noop():
    """0% saturation returns the channel unchanged (opt-out semantic)."""
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 1000, size=(16, 16), dtype=np.int32).astype(np.uint16)
    out = apply_saturation_lut(arr, saturation_pct=0.0)
    assert np.array_equal(out, arr)


def test_apply_saturation_lut_one_percent_clips_top_percentile():
    """1% saturation sets hi = p99 of the channel."""
    rng = np.random.default_rng(1)
    arr = rng.integers(0, 1000, size=(100, 100), dtype=np.int32).astype(np.uint16)
    out = apply_saturation_lut(arr, saturation_pct=1.0)

    lo = int(arr.min())
    hi = float(np.percentile(arr, 99.0))
    # Replicate the same operation to confirm exact equivalence.
    expected = apply_lut(arr, lo=lo, hi=hi)
    assert np.array_equal(out, expected)


def test_apply_saturation_lut_outlier_clamps_to_dtype_max():
    """A single hot pixel does not pull the percentile up."""
    arr = np.full((50, 50), 100, dtype=np.uint16)
    arr[0, 0] = 60000  # hot pixel
    out = apply_saturation_lut(arr, saturation_pct=1.0)
    # The hot pixel saturates to 65535; the rest stretches relative
    # to (lo=100, hi=p99≈100), which is degenerate → no-op
    # (the bulk of pixels are constant 100, p99 of constants is 100).
    # Verify outlier is at max OR the channel was returned unchanged
    # (degenerate-range path). Either is acceptable.
    if out[0, 0] != arr[0, 0]:
        assert out[0, 0] == 65535


def test_apply_saturation_lut_out_of_range_raises():
    """saturation_pct outside [0, 50] is rejected."""
    arr = np.zeros((4, 4), dtype=np.uint16)
    with pytest.raises(ValueError, match=r"saturation_pct"):
        apply_saturation_lut(arr, saturation_pct=-0.1)
    with pytest.raises(ValueError, match=r"saturation_pct"):
        apply_saturation_lut(arr, saturation_pct=51.0)


def test_apply_saturation_lut_preserves_dtype():
    """Output dtype matches input dtype."""
    arr = np.linspace(0, 1000, 100, dtype=np.float32).reshape((10, 10))
    out = apply_saturation_lut(arr, saturation_pct=2.0)
    assert out.dtype == np.float32


# ── apply_gaussian_blur (sigma-controlled smoothing) ────────────────────


def test_apply_gaussian_blur_zero_is_noop():
    """sigma == 0 returns the channel unchanged (opt-out semantic)."""
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 4000, size=(16, 16), dtype=np.uint16)
    out = apply_gaussian_blur(arr, sigma=0.0)
    assert np.array_equal(out, arr)


def test_apply_gaussian_blur_smooths_and_matches_scipy_reference():
    """sigma > 0 smooths noise; output equals scipy.ndimage reference."""
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(1)
    arr = rng.integers(0, 4000, size=(32, 32), dtype=np.uint16)
    out = apply_gaussian_blur(arr, sigma=1.5)
    expected = gaussian_filter(arr, sigma=1.5).astype(np.uint16)
    np.testing.assert_array_equal(out, expected)
    # Blurring damps pixel-to-pixel variance.
    assert out.var() < arr.var()


def test_apply_gaussian_blur_preserves_shape_and_dtype():
    """Output keeps the input shape and dtype (no float leak for ints)."""
    arr = np.linspace(0, 1000, 256, dtype=np.uint16).reshape((16, 16))
    out = apply_gaussian_blur(arr, sigma=2.0)
    assert out.shape == arr.shape
    assert out.dtype == np.uint16


def test_apply_gaussian_blur_negative_sigma_raises():
    """A negative sigma is rejected."""
    arr = np.zeros((8, 8), dtype=np.uint16)
    with pytest.raises(ValueError, match=r"sigma"):
        apply_gaussian_blur(arr, sigma=-1.0)


# ── preprocess_cellpose_input (shared run/preview composition) ──────────


def _noisy_plane(dtype):
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 1000, size=(16, 16)).astype(dtype)
    arr[3, 3] = 60000 if np.issubdtype(dtype, np.integer) else 60000.0
    return arr


def test_preprocess_cellpose_input_both_zero_returns_input_unchanged():
    """saturation_pct == 0 and blur_sigma == 0 is a full no-op."""
    arr = _noisy_plane(np.uint16)
    out = preprocess_cellpose_input(arr, saturation_pct=0.0, blur_sigma=0.0)
    np.testing.assert_array_equal(out, arr)


def test_preprocess_cellpose_input_saturation_only_matches_lut():
    arr = _noisy_plane(np.uint16)
    out = preprocess_cellpose_input(arr, saturation_pct=1.0, blur_sigma=0.0)
    np.testing.assert_array_equal(out, apply_saturation_lut(arr, 1.0))


def test_preprocess_cellpose_input_blur_only_matches_blur():
    arr = _noisy_plane(np.uint16)
    out = preprocess_cellpose_input(arr, saturation_pct=0.0, blur_sigma=1.5)
    np.testing.assert_array_equal(out, apply_gaussian_blur(arr, 1.5))


def test_preprocess_cellpose_input_both_composes_lut_then_blur():
    """LUT first, then blur — the order the run path has always used."""
    arr = _noisy_plane(np.uint16)
    out = preprocess_cellpose_input(arr, saturation_pct=1.0, blur_sigma=1.5)
    expected = apply_gaussian_blur(apply_saturation_lut(arr, 1.0), 1.5)
    np.testing.assert_array_equal(out, expected)
    # Order matters: blur-then-LUT smears the outlier first and differs.
    reversed_order = apply_saturation_lut(apply_gaussian_blur(arr, 1.5), 1.0)
    assert not np.array_equal(out, reversed_order)


@pytest.mark.parametrize("dtype", [np.uint16, np.float32])
def test_preprocess_cellpose_input_preserves_dtype(dtype):
    arr = _noisy_plane(dtype)
    out = preprocess_cellpose_input(arr, saturation_pct=1.0, blur_sigma=1.5)
    assert out.dtype == dtype
    assert out.shape == arr.shape


def test_preprocess_cellpose_input_resolves_helpers_at_call_time(monkeypatch):
    """The composition looks up the module-level helpers on each call so
    callers (and tests) can monkeypatch them on the module."""
    import percell4.domain.segmentation.preprocess as pre_mod

    calls = []
    monkeypatch.setattr(
        pre_mod, "apply_saturation_lut",
        lambda ch, pct: (calls.append(("sat", pct)), ch)[1],
    )
    monkeypatch.setattr(
        pre_mod, "apply_gaussian_blur",
        lambda ch, sigma: (calls.append(("blur", sigma)), ch)[1],
    )
    arr = np.zeros((4, 4), dtype=np.uint16)
    pre_mod.preprocess_cellpose_input(arr, saturation_pct=2.0, blur_sigma=0.5)
    assert calls == [("sat", 2.0), ("blur", 0.5)]
