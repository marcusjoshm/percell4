"""Unit tests for the GMM helpers in ``domain/flim/phasor.py``.

Covers ``universal_circle_gs``, ``gmm_eigenstructure``,
``gmm_to_phasor_roi_geometry``, and ``gmm_fit_phasor``. Pure numpy /
sklearn — no Qt, no h5py, no GUI.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from percell4.domain.flim.phasor import (
    GMMFitResult,
    gmm_eigenstructure,
    gmm_fit_phasor,
    gmm_to_phasor_roi_geometry,
    universal_circle_gs,
)


# ── universal_circle_gs ───────────────────────────────────────────────


def test_universal_circle_gs_lies_on_semicircle():
    g_c, s_c = universal_circle_gs(harmonic=1, tau_ns=2.5, freq_mhz=80.0)
    # Universal semicircle: (G - 0.5)^2 + S^2 = 0.25
    assert (g_c - 0.5) ** 2 + s_c ** 2 == pytest.approx(0.25, abs=1e-9)


def test_universal_circle_gs_zero_lifetime_is_dc():
    g_c, s_c = universal_circle_gs(harmonic=1, tau_ns=0.0, freq_mhz=80.0)
    assert g_c == pytest.approx(1.0, abs=1e-12)
    assert s_c == pytest.approx(0.0, abs=1e-12)


def test_universal_circle_gs_infinite_lifetime_is_origin():
    # As τ → ∞, both G and S → 0. At τ=1e6 ns with f=80MHz, S ≈ 1/(ωτ) ≈ 2e-6,
    # so use a tolerance loose enough to confirm "approaches origin"
    # without overconstraining the asymptote.
    g_c, s_c = universal_circle_gs(harmonic=1, tau_ns=1e6, freq_mhz=80.0)
    assert g_c == pytest.approx(0.0, abs=1e-9)
    assert s_c == pytest.approx(0.0, abs=1e-4)


def test_universal_circle_gs_negative_tau_raises():
    with pytest.raises(ValueError, match="tau_ns must be >= 0"):
        universal_circle_gs(harmonic=1, tau_ns=-1.0, freq_mhz=80.0)


# ── gmm_eigenstructure ────────────────────────────────────────────────


def test_gmm_eigenstructure_known_covariance():
    # Eigenvalues of [[4, 1], [1, 1]]: λ ≈ 4.302, 0.697
    cov = np.array([[4.0, 1.0], [1.0, 1.0]])
    lambda_major, lambda_minor, angle = gmm_eigenstructure(cov)
    assert lambda_major == pytest.approx(4.302775637732, abs=1e-6)
    assert lambda_minor == pytest.approx(0.697224362267, abs=1e-6)
    # Angle is bounded by atan2 → [-π, π]
    assert -np.pi <= angle <= np.pi


def test_gmm_eigenstructure_singular_covariance_clamps():
    # Rank-1 covariance: one eigenvalue is exactly 0.
    cov = np.array([[1.0, 1.0], [1.0, 1.0]])
    lambda_major, lambda_minor, _ = gmm_eigenstructure(cov)
    # Larger eigenvalue ~= 2; smaller is clamped (was 0).
    assert lambda_major == pytest.approx(2.0, abs=1e-9)
    assert lambda_minor > 0  # clamped, not zero


# ── gmm_to_phasor_roi_geometry ────────────────────────────────────────


def test_geometry_ellipse_no_shift_no_anchor():
    mean = (0.4, 0.3)
    center, radii, angle_deg = gmm_to_phasor_roi_geometry(
        mean=mean, lambda_major=4.0, lambda_minor=1.0,
        principal_angle_rad=0.0,
        cov_f=2.0, shift=0.0, shape="ellipse",
    )
    assert center == pytest.approx(mean, abs=1e-12)
    # 2 × √4 = 4 ; 2 × √1 = 2
    assert radii[0] == pytest.approx(4.0, abs=1e-12)
    assert radii[1] == pytest.approx(2.0, abs=1e-12)
    assert angle_deg == pytest.approx(0.0, abs=1e-12)


def test_geometry_ellipse_with_shift_displaces_along_principal_axis():
    mean = (0.4, 0.3)
    angle_rad = np.radians(45.0)
    center, radii, _ = gmm_to_phasor_roi_geometry(
        mean=mean, lambda_major=4.0, lambda_minor=1.0,
        principal_angle_rad=angle_rad,
        cov_f=2.0, shift=0.5, shape="ellipse",
    )
    # Δ = 0.5 × √4 × (cos 45°, sin 45°) = (0.707, 0.707)
    expected_dx = 0.5 * 2.0 * np.cos(angle_rad)
    expected_dy = 0.5 * 2.0 * np.sin(angle_rad)
    assert center[0] == pytest.approx(mean[0] + expected_dx, abs=1e-9)
    assert center[1] == pytest.approx(mean[1] + expected_dy, abs=1e-9)
    assert radii[0] == pytest.approx(4.0, abs=1e-12)
    assert radii[1] == pytest.approx(2.0, abs=1e-12)


def test_geometry_drag_preservation_uses_anchor_not_mean():
    """Spinbox-edit path: anchor is the user's dragged center."""
    mean = (0.4, 0.3)
    dragged = (0.5, 0.35)
    angle_rad = np.radians(30.0)
    center, _, _ = gmm_to_phasor_roi_geometry(
        mean=mean, lambda_major=4.0, lambda_minor=1.0,
        principal_angle_rad=angle_rad,
        cov_f=2.0, shift=0.5, shape="ellipse",
        anchor=dragged,
    )
    # Δ added to anchor (dragged), NOT mean
    expected_dx = 0.5 * 2.0 * np.cos(angle_rad)
    expected_dy = 0.5 * 2.0 * np.sin(angle_rad)
    assert center[0] == pytest.approx(dragged[0] + expected_dx, abs=1e-9)
    assert center[1] == pytest.approx(dragged[1] + expected_dy, abs=1e-9)


def test_geometry_circle_shape_uses_lambda_minor_and_zero_angle():
    center, radii, angle_deg = gmm_to_phasor_roi_geometry(
        mean=(0.4, 0.3), lambda_major=4.0, lambda_minor=1.0,
        principal_angle_rad=np.radians(45.0),
        cov_f=2.0, shift=0.0, shape="circle",
    )
    assert center == pytest.approx((0.4, 0.3), abs=1e-12)
    # Both radii from √λ_minor (= 1) × cov_f
    assert radii[0] == pytest.approx(2.0, abs=1e-12)
    assert radii[1] == pytest.approx(2.0, abs=1e-12)
    # Circle ignores rotation
    assert angle_deg == 0.0


def test_geometry_invalid_shape_raises():
    with pytest.raises(ValueError, match="shape must be"):
        gmm_to_phasor_roi_geometry(
            mean=(0, 0), lambda_major=1.0, lambda_minor=1.0,
            principal_angle_rad=0.0, cov_f=1.0, shift=0.0,
            shape="square",
        )


# ── gmm_fit_phasor ────────────────────────────────────────────────────


def _make_two_cluster_data(rng_seed: int = 7) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(rng_seed)
    n_per = 1500
    cov_a = np.array([[0.0008, 0.0], [0.0, 0.0005]])
    cov_b = np.array([[0.0006, 0.0], [0.0, 0.0004]])
    cluster_a = rng.multivariate_normal([0.30, 0.40], cov_a, size=n_per)
    cluster_b = rng.multivariate_normal([0.55, 0.42], cov_b, size=n_per)
    pts = np.vstack([cluster_a, cluster_b])
    g = pts[:, 0]
    s = pts[:, 1]
    intensity = np.ones_like(g)
    return g, s, intensity


def test_gmm_fit_phasor_recovers_two_cluster_means():
    g, s, intensity = _make_two_cluster_data()
    result = gmm_fit_phasor(
        g, s, intensity,
        n_components=2, criterion=None,
    )
    assert isinstance(result, GMMFitResult)
    assert result.chosen_n == 2
    # Sort means by G to align with truth ordering
    means = result.means[result.means[:, 0].argsort()]
    assert means[0][0] == pytest.approx(0.30, abs=0.02)
    assert means[1][0] == pytest.approx(0.55, abs=0.02)


def test_gmm_fit_phasor_bic_picks_n_equals_2():
    g, s, intensity = _make_two_cluster_data()
    result = gmm_fit_phasor(
        g, s, intensity,
        n_components=None, criterion="BIC",
        n_min=2, n_max=4,
    )
    assert result.chosen_n == 2
    assert result.criterion_value is not None


def test_gmm_fit_phasor_n_min_two_on_unimodal():
    """A unimodal blob still returns chosen_n >= n_min."""
    rng = np.random.default_rng(11)
    pts = rng.multivariate_normal([0.4, 0.3], np.eye(2) * 0.0005, size=2000)
    result = gmm_fit_phasor(
        pts[:, 0], pts[:, 1], np.ones(2000),
        n_components=None, criterion="BIC",
        n_min=2, n_max=4,
    )
    # The fitter does its best to find substructure, but n_min=2 floors it
    assert result.chosen_n >= 2


def test_gmm_fit_phasor_explicit_n_components_one_allowed():
    """Manual override: n=1 is valid even though auto-mode floors at n_min=2."""
    rng = np.random.default_rng(13)
    pts = rng.multivariate_normal([0.4, 0.3], np.eye(2) * 0.0005, size=500)
    result = gmm_fit_phasor(
        pts[:, 0], pts[:, 1], np.ones(500),
        n_components=1, criterion=None,
    )
    assert result.chosen_n == 1
    assert result.means.shape == (1, 2)


def test_gmm_fit_phasor_constant_intensity_falls_back_to_uniform():
    g, s, _ = _make_two_cluster_data()
    intensity_zero = np.zeros_like(g)
    result = gmm_fit_phasor(
        g, s, intensity_zero,
        n_components=2, criterion=None,
    )
    # Still recovers two distinct means via uniform sampling
    assert result.chosen_n == 2
    means = result.means[result.means[:, 0].argsort()]
    assert means[0][0] == pytest.approx(0.30, abs=0.05)
    assert means[1][0] == pytest.approx(0.55, abs=0.05)


def test_gmm_fit_phasor_reproducible_with_seed():
    g, s, intensity = _make_two_cluster_data()
    r1 = gmm_fit_phasor(g, s, intensity, n_components=2, criterion=None, random_seed=0)
    r2 = gmm_fit_phasor(g, s, intensity, n_components=2, criterion=None, random_seed=0)
    np.testing.assert_allclose(r1.means, r2.means)


def test_gmm_fit_phasor_max_pixels_subsample_override():
    g, s, intensity = _make_two_cluster_data()
    result = gmm_fit_phasor(
        g, s, intensity,
        n_components=2, criterion=None,
        max_pixels=200,
    )
    assert result.sampled_pixels == 200


def test_gmm_fit_phasor_too_few_pixels_raises():
    g = np.array([0.3, 0.5])
    s = np.array([0.4, 0.42])
    intensity = np.ones(2)
    with pytest.raises(ValueError, match="Not enough valid pixels"):
        gmm_fit_phasor(
            g, s, intensity,
            n_components=20, criterion=None,
        )


def test_gmm_fit_phasor_invalid_criterion_raises():
    g, s, intensity = _make_two_cluster_data()
    with pytest.raises(ValueError, match="criterion must be"):
        gmm_fit_phasor(
            g, s, intensity,
            n_components=None, criterion="XYZ",
        )


def test_gmm_fit_phasor_zero_n_components_raises():
    g, s, intensity = _make_two_cluster_data()
    with pytest.raises(ValueError, match="n_components must be >= 1"):
        gmm_fit_phasor(
            g, s, intensity,
            n_components=0, criterion=None,
        )


# ── lazy import discipline ────────────────────────────────────────────


def test_phasor_module_does_not_import_sklearn_at_module_load():
    """Importing percell4.domain.flim.phasor must not pull in sklearn.

    Mirrors ``domain/measure/grouper.py``'s lazy-import discipline so
    import time stays low and the import-linter contract for domain/
    is honest about runtime requirements.
    """
    # Drop both modules so the next import is a true cold load.
    sys.modules.pop("percell4.domain.flim.phasor", None)
    # We can't unconditionally drop sklearn — other tests may rely on it.
    sklearn_already_loaded = any(
        name == "sklearn" or name.startswith("sklearn.")
        for name in list(sys.modules)
    )

    import percell4.domain.flim.phasor  # noqa: F401

    if not sklearn_already_loaded:
        loaded = [
            name for name in sys.modules
            if name == "sklearn" or name.startswith("sklearn.")
        ]
        assert not loaded, (
            f"phasor.py module load pulled in sklearn: {loaded}"
        )
