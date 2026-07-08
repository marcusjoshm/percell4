"""Tests for src/percell4/domain/image/gaussian.py.

Covers normalized-convolution NaN-aware Gaussian filtering:
- equivalence to scipy on clean inputs,
- finite-output guarantee at pixels with finite kernel-footprint
  neighbors,
- no NaN bleed beyond all-NaN-footprint pixels,
- sigma=0 identity (NaN preserved exactly),
- all-NaN degenerate input (no warnings),
- dtype/shape contract on small and large grids.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from percell4.domain.image import nan_safe_gaussian_filter

# ---------------------------------------------------------------------------
# Happy path: clean input matches scipy
# ---------------------------------------------------------------------------

def test_clean_input_matches_scipy_gaussian_filter():
    rng = np.random.default_rng(0)
    image = rng.normal(loc=10.0, scale=2.0, size=(32, 32)).astype(np.float32)

    out = nan_safe_gaussian_filter(image, sigma=1.5)
    expected = gaussian_filter(image.astype(np.float32), sigma=1.5)

    assert out.dtype == np.float32
    assert out.shape == image.shape
    assert np.allclose(out, expected, atol=1e-5)


def test_clean_input_larger_image_matches_scipy():
    rng = np.random.default_rng(1)
    image = rng.normal(loc=5.0, scale=1.0, size=(128, 128)).astype(np.float32)

    out = nan_safe_gaussian_filter(image, sigma=2.0)
    expected = gaussian_filter(image.astype(np.float32), sigma=2.0)

    assert out.dtype == np.float32
    assert np.allclose(out, expected, atol=1e-5)


# ---------------------------------------------------------------------------
# Isolated NaN pixels
# ---------------------------------------------------------------------------

def test_isolated_nans_yield_fully_finite_output_for_reasonable_sigma():
    rng = np.random.default_rng(2)
    image = rng.normal(loc=10.0, scale=1.0, size=(64, 64)).astype(np.float32)
    # Punch isolated NaN pixels deep in the interior (well clear of edges).
    image[10, 10] = np.nan
    image[30, 40] = np.nan
    image[50, 20] = np.nan

    out = nan_safe_gaussian_filter(image, sigma=1.5)

    # With sigma >= 1, every isolated NaN pixel's kernel footprint contains
    # many finite neighbors, so the output must be fully finite.
    assert np.all(np.isfinite(out))
    assert out.dtype == np.float32


# ---------------------------------------------------------------------------
# Half-NaN image: clean blending on the boundary, no NaN bleed beyond
# pixels whose entire footprint is NaN.
# ---------------------------------------------------------------------------

def test_half_nan_image_no_bleed_beyond_all_nan_footprint():
    image = np.ones((64, 64), dtype=np.float32) * 5.0
    # Make the left half NaN, right half finite.
    image[:, :32] = np.nan

    sigma = 1.5
    out = nan_safe_gaussian_filter(image, sigma=sigma)

    # Build the "all-NaN footprint" mask analytically: smooth the
    # finite-indicator with the same kernel; pixels where smoothed weight
    # is zero (within floating tolerance) are the all-NaN-footprint ones.
    finite_mask = np.isfinite(image).astype(np.float32)
    smoothed_weight = gaussian_filter(finite_mask, sigma=sigma)
    all_nan_footprint = smoothed_weight <= 0.0

    # Pixels with any finite neighbor must be finite in the output.
    assert np.all(np.isfinite(out[~all_nan_footprint]))
    # Pixels with no finite neighbor must remain NaN.
    assert np.all(np.isnan(out[all_nan_footprint]))

    # On the right (finite) half well away from the boundary, value
    # should be close to the constant 5.0.
    assert np.allclose(out[:, 60:], 5.0, atol=1e-3)


# ---------------------------------------------------------------------------
# sigma = 0 identity
# ---------------------------------------------------------------------------

def test_sigma_zero_returns_input_preserving_nan():
    image = np.array(
        [[1.0, np.nan, 3.0],
         [4.0, 5.0, np.nan],
         [np.nan, 8.0, 9.0]],
        dtype=np.float32,
    )

    out = nan_safe_gaussian_filter(image, sigma=0.0)

    assert out.dtype == np.float32
    assert out.shape == image.shape
    np.testing.assert_array_equal(out, image)  # equal_nan default for floats
    # Be explicit about NaN-equality semantics:
    assert np.array_equal(out, image, equal_nan=True)


# ---------------------------------------------------------------------------
# All-NaN degenerate input
# ---------------------------------------------------------------------------

def test_all_nan_input_returns_all_nan_without_warnings():
    image = np.full((16, 16), np.nan, dtype=np.float32)

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning becomes an error
        out = nan_safe_gaussian_filter(image, sigma=1.5)

    assert out.shape == image.shape
    assert out.dtype == np.float32
    assert np.all(np.isnan(out))


# ---------------------------------------------------------------------------
# Shape + dtype contract on small and large grids
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("shape", [(8, 8), (128, 128)])
def test_shape_and_dtype_contract(shape):
    rng = np.random.default_rng(3)
    image = rng.normal(size=shape).astype(np.float64)  # input as float64
    # Sprinkle a few NaN to exercise the NaN branch on both shapes.
    image[0, 0] = np.nan
    image[shape[0] // 2, shape[1] // 2] = np.nan

    out = nan_safe_gaussian_filter(image, sigma=1.0)

    assert out.shape == shape
    assert out.dtype == np.float32
