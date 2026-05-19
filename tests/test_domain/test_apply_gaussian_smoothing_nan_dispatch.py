"""U2 regression tests for apply_gaussian_smoothing's NaN-aware dispatch.

The dilute-phase workflow's per-round working buffer contains NaN
where prior rounds have subtracted condensed-phase pixels. Every
consumer of apply_gaussian_smoothing must handle that input
correctly without poisoning the entire smoothed image.

Strategy: clean inputs MUST take the existing scipy fast path
unchanged (bit-equivalent legacy behavior; pinned to guard against
silent drift). NaN inputs MUST be routed through the U1 NaN-safe
helper so finite pixels stay finite even when neighbors are NaN.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from percell4.domain.measure.thresholding import apply_gaussian_smoothing


def test_clean_image_is_bit_equivalent_to_scipy_fast_path():
    """A NaN-free image must take the scipy fast path so legacy
    consumers see zero behavioral change from U2."""
    rng = np.random.default_rng(seed=42)
    image = rng.standard_normal((32, 32)).astype(np.float32)

    direct = gaussian_filter(image.astype(np.float32), sigma=1.5)
    dispatched = apply_gaussian_smoothing(image, sigma=1.5)

    np.testing.assert_array_equal(dispatched, direct)


def test_nan_image_routes_through_nan_safe_helper():
    """A NaN-containing input must hit the U1 helper, not scipy.
    The smoking gun: scipy poisons the entire image with NaN when
    even one NaN is present; the U1 helper does not."""
    image = np.ones((16, 16), dtype=np.float32)
    image[8, 8] = np.nan

    dispatched = apply_gaussian_smoothing(image, sigma=1.0)

    assert np.isfinite(dispatched).all(), (
        "A single NaN in a 16x16 image must not poison the entire "
        "smoothed output — that would be the scipy fast path leaking "
        "through, which is the bug U2 prevents."
    )


def test_sigma_zero_returns_input_unchanged_even_for_nan():
    """sigma=0 short-circuits before either dispatch branch — input
    is returned as-is (NaN preserved exactly)."""
    image = np.ones((4, 4), dtype=np.float32)
    image[1, 1] = np.nan

    out = apply_gaussian_smoothing(image, sigma=0)
    # Identity short-circuit returns the same object.
    assert out is image


def test_sigma_none_returns_input_unchanged():
    """sigma=None short-circuits before either dispatch branch."""
    image = np.ones((4, 4), dtype=np.float32)
    out = apply_gaussian_smoothing(image, sigma=None)
    assert out is image


def test_dispatch_calls_helper_for_nan_input(monkeypatch):
    """Explicit dispatch assertion: prove which branch fires by
    patching both helpers and observing which got the call."""
    helper_calls = []
    scipy_calls = []

    def fake_helper(image, *, sigma, **kwargs):
        helper_calls.append(sigma)
        return image.astype(np.float32)

    def fake_scipy(arr, sigma):
        scipy_calls.append(sigma)
        return arr

    monkeypatch.setattr(
        "percell4.domain.image.gaussian.nan_safe_gaussian_filter",
        fake_helper,
    )
    monkeypatch.setattr(
        "scipy.ndimage.gaussian_filter",
        fake_scipy,
    )

    # Clean image -> scipy
    clean = np.ones((4, 4), dtype=np.float32)
    apply_gaussian_smoothing(clean, sigma=1.0)
    assert scipy_calls == [1.0]
    assert helper_calls == []

    # NaN image -> helper
    nany = clean.copy()
    nany[2, 2] = np.nan
    apply_gaussian_smoothing(nany, sigma=2.0)
    assert helper_calls == [2.0]
    assert scipy_calls == [1.0]  # unchanged


def test_dispatch_uses_isnan_not_isfinite_for_detection():
    """The dispatch predicate is np.isnan(image).any() — +inf and
    -inf go through the scipy fast path (they're finite-NaN
    distinction matters: scipy handles infinities fine, only NaN
    is the poisoning class). This is a pin against drift toward
    `not np.isfinite(...)` which would needlessly route infinity
    inputs through the slower NaN-safe path."""
    image = np.ones((8, 8), dtype=np.float32)
    image[0, 0] = np.inf  # NOT a NaN

    # Should go through scipy without raising.
    out = apply_gaussian_smoothing(image, sigma=1.0)
    assert np.allclose(
        out,
        gaussian_filter(image.astype(np.float32), sigma=1.0),
        equal_nan=True,
    )
