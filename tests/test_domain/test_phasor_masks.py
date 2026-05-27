"""Tests for the phasor ellipse-fit + dual-threshold mask helper.

Covers the U1 scenarios from
``docs/plans/2026-05-27-001-feat-phasor-masks-workflow-plan.md``:

- Happy path: synthetic Gaussian blob, flat intensity → fit recovers
  center; mask_a coverage > mask_b coverage when ``t_mask_a < t_mask_b``.
- Happy path: two populations, ellipse centers on high-intensity one.
- Edge case: ``t_mask_a == t_mask_b`` → identical masks.
- Edge case: ``t_mask_a == 0`` → mask_a coverage equals the unfiltered
  ellipse mask.
- Edge case: empty fit subset → ``ValueError`` propagates from
  ``single_component_fit_phasor``.
- Edge case: NaN pixels excluded from fit and resulting masks.
- Error path: single-pixel fit → degeneracy guard raises with the
  expected message substring.
- Error path: rank-deficient (collinear) fit → degeneracy guard raises.
- Integration: outputs are ``uint8`` with shape matching ``g_map`` and
  max value 1.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.domain.segmentation.phasor_masks import (
    PhasorEllipseMasksResult,
    fit_phasor_ellipse_and_apply_masks,
)


# ── helpers ─────────────────────────────────────────────────────────────


def _gaussian_blob(
    shape: tuple[int, int],
    center: tuple[float, float],
    sigma: tuple[float, float],
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate (g_map, s_map) with values drawn from a 2D Gaussian.

    Each pixel's (g, s) value is an independent draw; this gives us a
    "blob" in phasor coordinates that ``single_component_fit_phasor``
    can recover.
    """
    h, w = shape
    g = rng.normal(loc=center[0], scale=sigma[0], size=(h, w)).astype(np.float32)
    s = rng.normal(loc=center[1], scale=sigma[1], size=(h, w)).astype(np.float32)
    return g, s


# ── happy paths ─────────────────────────────────────────────────────────


def test_happy_path_recovers_center_and_thresholds_order_coverage():
    """Gaussian blob in (g, s), flat intensity → fit recovers center;
    mask_a (lower threshold) has >= coverage than mask_b (higher)."""
    rng = np.random.default_rng(seed=0)
    shape = (40, 40)
    true_center = (0.5, 0.3)
    g_map, s_map = _gaussian_blob(shape, true_center, (0.02, 0.02), rng)
    # Flat intensity above the fit threshold everywhere.
    intensity = np.full(shape, 100.0, dtype=np.float32)

    result = fit_phasor_ellipse_and_apply_masks(
        g_map, s_map, intensity,
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=50.0,
    )

    assert isinstance(result, PhasorEllipseMasksResult)
    # Fitted ellipse center should land within a few sigma of the truth.
    cx, cy = result.geometry[0]
    assert abs(cx - true_center[0]) < 0.01
    assert abs(cy - true_center[1]) < 0.01

    # mask_a uses the more permissive threshold and must cover at least
    # as many pixels as mask_b.
    assert int(result.mask_a.sum()) >= int(result.mask_b.sum())
    # And at this configuration we expect both to be non-trivial.
    assert int(result.mask_a.sum()) > 0
    # Dtypes.
    assert result.mask_a.dtype == np.uint8
    assert result.mask_b.dtype == np.uint8


def test_two_populations_ellipse_centers_on_high_intensity_one():
    """Two distinct blobs at (0.3, 0.2) and (0.7, 0.4); only the
    high-intensity blob's pixels survive the fit threshold."""
    rng = np.random.default_rng(seed=1)
    shape = (40, 40)
    h, w = shape
    g_map = np.empty(shape, dtype=np.float32)
    s_map = np.empty(shape, dtype=np.float32)
    intensity = np.empty(shape, dtype=np.float32)

    # Left half: low-intensity blob at (0.3, 0.2).
    g_map[:, : w // 2] = rng.normal(0.3, 0.01, size=(h, w // 2)).astype(np.float32)
    s_map[:, : w // 2] = rng.normal(0.2, 0.01, size=(h, w // 2)).astype(np.float32)
    intensity[:, : w // 2] = 1.0  # below t_fit

    # Right half: high-intensity blob at (0.7, 0.4).
    g_map[:, w // 2 :] = rng.normal(0.7, 0.01, size=(h, w - w // 2)).astype(np.float32)
    s_map[:, w // 2 :] = rng.normal(0.4, 0.01, size=(h, w - w // 2)).astype(np.float32)
    intensity[:, w // 2 :] = 100.0  # above t_fit

    result = fit_phasor_ellipse_and_apply_masks(
        g_map, s_map, intensity,
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=50.0,
    )

    cx, cy = result.geometry[0]
    # Ellipse centers on the high-intensity blob.
    assert abs(cx - 0.7) < 0.02
    assert abs(cy - 0.4) < 0.02


# ── edge cases ──────────────────────────────────────────────────────────


def test_equal_thresholds_yield_identical_masks():
    rng = np.random.default_rng(seed=2)
    shape = (20, 20)
    g_map, s_map = _gaussian_blob(shape, (0.5, 0.3), (0.02, 0.02), rng)
    intensity = np.full(shape, 100.0, dtype=np.float32)

    result = fit_phasor_ellipse_and_apply_masks(
        g_map, s_map, intensity,
        t_fit=10.0,
        t_mask_a=20.0,
        t_mask_b=20.0,
    )
    np.testing.assert_array_equal(result.mask_a, result.mask_b)


def test_t_mask_a_zero_equals_unfiltered_ellipse_mask():
    """When ``t_mask_a == 0``, mask_a should not be filtered by
    intensity at all — it must equal the raw ellipse-membership mask."""
    rng = np.random.default_rng(seed=3)
    shape = (30, 30)
    g_map, s_map = _gaussian_blob(shape, (0.5, 0.3), (0.02, 0.02), rng)
    # Mixed intensities — some above, some below 0. (>= 0 is everything.)
    intensity = rng.uniform(0.0, 50.0, size=shape).astype(np.float32)

    result = fit_phasor_ellipse_and_apply_masks(
        g_map, s_map, intensity,
        t_fit=0.0,
        t_mask_a=0.0,
        t_mask_b=10.0,
    )

    # Recompute the pure ellipse-membership mask using the result's
    # geometry. mask_a (no intensity filter) should match.
    from percell4.domain.flim.phasor import phasor_roi_to_mask

    center, radii, angle_deg = result.geometry
    ellipse_only = phasor_roi_to_mask(
        g_map, s_map,
        center=center,
        radii=radii,
        angle_rad=np.radians(angle_deg),
    )
    np.testing.assert_array_equal(result.mask_a.astype(bool), ellipse_only)


def test_empty_fit_subset_raises_value_error():
    """Intensity all zeros below t_fit → fit subset is empty;
    ``single_component_fit_phasor`` raises with its native message."""
    rng = np.random.default_rng(seed=4)
    shape = (10, 10)
    g_map, s_map = _gaussian_blob(shape, (0.5, 0.3), (0.02, 0.02), rng)
    intensity = np.zeros(shape, dtype=np.float32)

    with pytest.raises(ValueError, match="Cannot fit single component on empty input"):
        fit_phasor_ellipse_and_apply_masks(
            g_map, s_map, intensity,
            t_fit=10.0,
            t_mask_a=0.0,
            t_mask_b=5.0,
        )


def test_nan_pixels_excluded_from_fit_and_masks():
    """NaN pixels in g/s must be excluded from the fit (so a finite
    fit is still possible) and must be False in the output masks."""
    rng = np.random.default_rng(seed=5)
    shape = (20, 20)
    g_map, s_map = _gaussian_blob(shape, (0.5, 0.3), (0.02, 0.02), rng)
    intensity = np.full(shape, 100.0, dtype=np.float32)

    # Punch a 5x5 hole of NaN in the middle.
    g_map[5:10, 5:10] = np.nan
    s_map[5:10, 5:10] = np.nan

    result = fit_phasor_ellipse_and_apply_masks(
        g_map, s_map, intensity,
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=50.0,
    )

    # The fit still recovers a sensible center.
    cx, cy = result.geometry[0]
    assert abs(cx - 0.5) < 0.02
    assert abs(cy - 0.3) < 0.02

    # NaN pixels must be 0 in both masks.
    assert int(result.mask_a[5:10, 5:10].sum()) == 0
    assert int(result.mask_b[5:10, 5:10].sum()) == 0


# ── degeneracy guards ──────────────────────────────────────────────────


def test_single_pixel_fit_raises_degeneracy_guard():
    """One non-NaN pixel above t_fit → cov is zero → radii collapse to
    zero → U1 raises a ValueError with the documented message."""
    shape = (10, 10)
    g_map = np.full(shape, np.nan, dtype=np.float32)
    s_map = np.full(shape, np.nan, dtype=np.float32)
    intensity = np.zeros(shape, dtype=np.float32)

    # Exactly one pixel survives the (finite-g, finite-s, above-t_fit) cut.
    g_map[3, 3] = 0.5
    s_map[3, 3] = 0.3
    intensity[3, 3] = 100.0

    with pytest.raises(ValueError, match="degenerate fit"):
        fit_phasor_ellipse_and_apply_masks(
            g_map, s_map, intensity,
            t_fit=10.0,
            t_mask_a=0.0,
            t_mask_b=5.0,
        )


def test_collinear_pixels_raise_degeneracy_guard():
    """Two pixels collinear in (G, S) → cov is rank-deficient (one
    eigenvalue zero) → degeneracy guard raises."""
    shape = (10, 10)
    g_map = np.full(shape, np.nan, dtype=np.float32)
    s_map = np.full(shape, np.nan, dtype=np.float32)
    intensity = np.zeros(shape, dtype=np.float32)

    # Two pixels along the (G == S) line — rank-deficient covariance.
    g_map[1, 1] = 0.4
    s_map[1, 1] = 0.4
    intensity[1, 1] = 100.0

    g_map[2, 2] = 0.5
    s_map[2, 2] = 0.5
    intensity[2, 2] = 100.0

    with pytest.raises(ValueError, match="degenerate fit"):
        fit_phasor_ellipse_and_apply_masks(
            g_map, s_map, intensity,
            t_fit=10.0,
            t_mask_a=0.0,
            t_mask_b=5.0,
        )


# ── integration / output shape contracts ──────────────────────────────


def test_output_shape_dtype_and_max_value_contract():
    """``store.write_mask`` boundary contract: outputs are
    ``np.uint8``, share shape with ``g_map``, max value 1."""
    rng = np.random.default_rng(seed=6)
    shape = (16, 24)  # non-square to catch shape-swap bugs
    g_map, s_map = _gaussian_blob(shape, (0.5, 0.3), (0.02, 0.02), rng)
    intensity = np.full(shape, 100.0, dtype=np.float32)

    result = fit_phasor_ellipse_and_apply_masks(
        g_map, s_map, intensity,
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=20.0,
    )

    for mask in (result.mask_a, result.mask_b):
        assert mask.dtype == np.uint8
        assert mask.shape == g_map.shape
        assert int(mask.max()) <= 1
        assert int(mask.min()) >= 0


def test_pure_domain_no_forbidden_imports():
    """Domain isolation: ``phasor_masks`` must not pull in qtpy,
    napari, h5py, or percell4.application.session."""
    import sys

    # The helper module is already imported via the test imports above.
    forbidden = ("qtpy", "napari", "h5py", "percell4.application.session")
    # Sanity: the helper itself must not have grabbed any forbidden
    # symbol into its own module namespace. We don't audit sys.modules
    # globally (other tests may have imported them).
    import percell4.domain.segmentation.phasor_masks as mod

    for name in dir(mod):
        # We only care that no top-level symbol *is* one of the forbidden
        # modules. Transitive imports happen in unrelated tests.
        attr = getattr(mod, name)
        attr_mod = getattr(attr, "__module__", "")
        if isinstance(attr_mod, str):
            assert not attr_mod.startswith("qtpy"), name
            assert not attr_mod.startswith("napari"), name
            assert not attr_mod.startswith("h5py"), name
            assert not attr_mod.startswith(
                "percell4.application.session"
            ), name

    # Also: the source file must not import these by name. Read it as
    # text to be sure.
    src = mod.__file__
    assert src is not None
    with open(src, "r", encoding="utf-8") as f:
        text = f.read()
    for token in ("import qtpy", "from qtpy", "import napari", "from napari",
                  "import h5py", "from h5py",
                  "from percell4.application.session",
                  "import percell4.application.session"):
        assert token not in text, f"forbidden import found: {token}"
