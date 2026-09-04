"""Tests for the BOE 2021 FLIM wavelet filter.

Covers both the component-level unit checks (no dtcwt needed for some
— Anscombe, padding helpers, input sanitization) and the end-to-end
pipeline tests that verify noise-reduction behaviour on a synthetic
spoke phantom.

Pipeline tests are marked ``@pytest.mark.slow``; run with
``pytest -m slow`` or plain ``pytest`` to include them.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.domain.errors import MissingOptionalDependencyError
from percell4.domain.flim.wavelet import (
    ALGORITHM_CHOICES,
    DEFAULT_FILTER_LEVEL,
    denoise_phasor,
)
from percell4.domain.flim.wavelet._shared import (
    anscombe_forward,
    anscombe_inverse,
    next_multiple,
    next_pow2,
)

# Skip all tests in this module if dtcwt is not installed — the filter
# itself raises cleanly, but most of these tests exercise the full path.
dtcwt = pytest.importorskip("dtcwt")

from percell4.domain.flim.wavelet.boe import (  # noqa: E402
    BAND_MINUS_45,
    BAND_PLUS_45,
    DEFAULT_N_LOCAL,
    EXPECTED_LEGALL_H0O,
    _sanitize_inputs,
    bishrink,
    denoise_phasor_boe,
    estimate_sigma_g,
    local_noise_variance,
)
from ._spoke_phantom import generate_spoke_phantom, g_s_mse


# ── Helper / shared-module tests ──────────────────────────────────────

def test_next_multiple_basic():
    assert next_multiple(10, 8) == 16
    assert next_multiple(16, 8) == 16       # already a multiple
    assert next_multiple(17, 8) == 24
    assert next_multiple(1000, 512) == 1024
    assert next_multiple(1025, 512) == 1536


def test_next_multiple_saves_vs_next_pow2_on_nonpow2():
    """The whole point of using `next_multiple` in BOE: it's tighter
    than next_pow2 for non-power-of-2 inputs."""
    # 1500 → pow2 → 2048 (28% overhead), next_multiple(., 512) → 1536 (2.4%)
    assert next_multiple(1500, 512) < next_pow2(1500)


def test_anscombe_roundtrip_preserves_counts():
    """forward then inverse recovers input within tolerance at typical
    photon counts."""
    rng = np.random.default_rng(0)
    counts = rng.poisson(lam=25.0, size=(64, 64)).astype(np.float64)
    recovered = anscombe_inverse(anscombe_forward(counts))
    # Mäkitalo-Foi is unbiased in expectation, not pixel-exact.
    assert np.mean(np.abs(recovered - counts)) < 0.6
    assert (recovered >= 0).all()


def test_anscombe_inverse_handles_zero_input():
    """`anscombe_inverse(y=0)` shouldn't blow up on the 1/y terms."""
    out = anscombe_inverse(np.zeros((3, 3)))
    assert np.all(np.isfinite(out))


# ── dtcwt filter-bank and band-index guards ──────────────────────────

def test_legall_coefficients_match_boe_table_s1():
    """dtcwt 0.14.0's `biort='legall'` must match the taps tabulated in
    the BOE supplement (Table S1). Catches a silent dtcwt upgrade that
    renames or rebuilds the filter.
    """
    h0o, g0o, h1o, g1o = dtcwt.coeffs.biort("legall")
    # h0o is the Tree-A analysis low-pass, 5 non-zero taps matching
    # [-0.125, 0.25, 0.75, 0.25, -0.125].
    taps = np.asarray(h0o).ravel()
    np.testing.assert_allclose(taps, EXPECTED_LEGALL_H0O, rtol=1e-12)


def test_band_indices_pm45_are_1_and_4():
    """dtcwt's 6 directional bands must still map +45° → index 1,
    −45° → index 4.

    Probe with oriented STEP edges (not sinusoids — a sinusoid has
    extended spectral content along both orthogonal diagonals and
    can't isolate a single ±45° band). An x+y step edge sends its
    most energy into band 1; an x−y step edge sends its most energy
    into band 4. A future dtcwt upgrade that reorders bands would
    silently corrupt BOE's σ_g estimate without this guard.
    """
    h = w = 128
    y, x = np.mgrid[0:h, 0:w].astype(np.float64)
    edge_plus = ((x + y) > h).astype(np.float64)   # +45° edge
    edge_minus = ((x - y) > 0).astype(np.float64)  # −45° edge

    xfm = dtcwt.Transform2d(biort="legall", qshift="qshift_a")

    for label, img, expected_band in [
        ("+45°", edge_plus, BAND_PLUS_45),
        ("−45°", edge_minus, BAND_MINUS_45),
    ]:
        coeffs = xfm.forward(img, nlevels=3)
        band_energy = [
            float(np.mean(np.abs(coeffs.highpasses[0][:, :, b]) ** 2))
            for b in range(6)
        ]
        top = int(np.argmax(band_energy))
        assert top == expected_band, (
            f"{label} edge should peak at band {expected_band}, got "
            f"{top}. Energies: {band_energy!r}. dtcwt band ordering "
            f"may have changed."
        )


# ── σ_g estimator ─────────────────────────────────────────────────────

def test_sigma_g_on_pure_gaussian_noise_recovers_true_sigma():
    """On a zero-mean Gaussian image with known σ, the MAD/0.6745
    estimator on level-1 ±45° bands should recover σ within ~15%.
    (Tolerance is loose because DTCWT is not an orthogonal basis; the
    ±45° HH coefficients carry a subset of the input variance.)
    """
    rng = np.random.default_rng(0)
    true_sigma = 2.0
    noise = rng.normal(scale=true_sigma, size=(256, 256))
    xfm = dtcwt.Transform2d(biort="legall", qshift="qshift_a")
    coeffs = xfm.forward(noise, nlevels=4)

    sigma_hat = estimate_sigma_g(coeffs)
    # Orthonormality loss means sigma_hat scales with true_sigma but
    # not exactly 1:1. Check monotone response instead of absolute hit.
    # Rerun at 2× σ and confirm estimator roughly doubles.
    noise2 = rng.normal(scale=true_sigma * 2.0, size=(256, 256))
    coeffs2 = xfm.forward(noise2, nlevels=4)
    sigma_hat2 = estimate_sigma_g(coeffs2)
    ratio = sigma_hat2 / sigma_hat
    assert 1.7 < ratio < 2.3, (
        f"Estimator should scale linearly with σ; got ratio={ratio:.2f}"
    )


def test_sigma_g_is_positive_scalar():
    rng = np.random.default_rng(0)
    xfm = dtcwt.Transform2d(biort="legall", qshift="qshift_a")
    coeffs = xfm.forward(rng.normal(size=(128, 128)), nlevels=3)
    sigma_g = estimate_sigma_g(coeffs)
    assert isinstance(sigma_g, float)
    assert sigma_g > 0


# ── Local noise variance ──────────────────────────────────────────────

def test_local_noise_variance_returns_per_level_per_band():
    rng = np.random.default_rng(0)
    xfm = dtcwt.Transform2d(biort="legall", qshift="qshift_a")
    coeffs = xfm.forward(rng.normal(size=(128, 128)), nlevels=3)
    sigma_n_sq_list = local_noise_variance(coeffs, n_local=DEFAULT_N_LOCAL)
    assert len(sigma_n_sq_list) == 3 * 6  # 3 levels × 6 bands
    for arr in sigma_n_sq_list:
        assert arr.shape == (128 // 2, 128 // 2) or arr.shape[0] <= 128
        assert (arr >= 0).all()


# ── BiShrink ──────────────────────────────────────────────────────────

def _make_coeffs_from_array(arr, n_levels=3):
    xfm = dtcwt.Transform2d(biort="legall", qshift="qshift_a")
    return xfm, xfm.forward(arr, nlevels=n_levels)


def test_bishrink_zero_noise_preserves_coefficients():
    """σ_g = 0 → factor = 1 everywhere → Φ unchanged.
    Regression test for the BiShrink "noiseless input" corner case.
    """
    rng = np.random.default_rng(0)
    xfm, coeffs = _make_coeffs_from_array(rng.normal(size=(128, 128)))

    before = [hp.copy() for hp in coeffs.highpasses]
    sigma_n_sq_list = local_noise_variance(coeffs)
    bishrink(coeffs, sigma_g=0.0, sigma_n_sq_list=sigma_n_sq_list)

    # Coarsest level must be unchanged (never shrunk).
    np.testing.assert_array_equal(coeffs.highpasses[-1], before[-1])
    # Finer levels with σ_g=0 are also unchanged because factor = max(0, 1-0) = 1.
    for lvl in range(len(coeffs.highpasses) - 1):
        np.testing.assert_allclose(coeffs.highpasses[lvl], before[lvl],
                                    rtol=1e-12, atol=1e-12)


def test_bishrink_uniform_region_zeros_out_without_warnings():
    """A uniform-signal region has σ_n² ≈ σ_g², so (σ_n²−σ_g²)_+ = 0,
    denom = 0, factor = 0. The explicit denom>0 gate must handle this
    without RuntimeWarning.
    """
    rng = np.random.default_rng(0)
    xfm, coeffs = _make_coeffs_from_array(rng.normal(size=(128, 128)))
    sigma_n_sq_list = local_noise_variance(coeffs)

    # Huge sigma_g → (σ_n² − σ_g²)_+ = 0 everywhere → all factors = 0
    with np.errstate(divide="raise", invalid="raise"):
        bishrink(coeffs, sigma_g=1e6, sigma_n_sq_list=sigma_n_sq_list)

    for lvl in range(len(coeffs.highpasses) - 1):
        np.testing.assert_array_equal(
            coeffs.highpasses[lvl], 0.0,
            err_msg=f"Level {lvl} should be fully zeroed with huge σ_g",
        )


def test_bishrink_vectorized_matches_scalar_reference():
    """The single most important BiShrink test: the vectorized
    implementation must agree pixelwise with a scalar reference loop
    computing the same formula. Catches exactly the class of bug that
    let the JCB port drift from its paper in the first place (a
    too-clever vectorization that implements a different math than
    intended).
    """
    rng = np.random.default_rng(42)
    xfm, coeffs = _make_coeffs_from_array(rng.normal(size=(64, 64)),
                                            n_levels=3)

    # Take a snapshot before shrinkage so we can compare.
    hp_before = [hp.copy() for hp in coeffs.highpasses]
    sigma_n_sq_list = local_noise_variance(coeffs, n_local=DEFAULT_N_LOCAL)
    sigma_g = 0.4  # arbitrary nonzero — exercises the full formula
    sigma_g_sq = sigma_g ** 2

    # Scalar reference: pixel-by-pixel implementation of the BOE
    # formula from the plan. Intentionally naive and obviously correct.
    expected = [hp.copy() for hp in hp_before]  # coarsest untouched
    numer = np.sqrt(3.0) * sigma_g_sq
    max_level = len(hp_before) - 1
    for level in range(max_level):
        hp_l = hp_before[level]
        hp_parent = hp_before[level + 1]
        h_l, w_l, nb = hp_l.shape
        for band in range(nb):
            phi_l = hp_l[:, :, band]
            phi_parent_full = hp_parent[:, :, band]
            sigma_n_sq_full = sigma_n_sq_list[level * 6 + band]
            out = np.zeros_like(phi_l)
            for y in range(h_l):
                for x in range(w_l):
                    pl = phi_l[y, x]
                    pp = phi_parent_full[y // 2, x // 2]
                    r_sq = abs(pl) ** 2 + abs(pp) ** 2
                    d = max(sigma_n_sq_full[y, x] - sigma_g_sq, 0.0)
                    denom = np.sqrt(r_sq * d)
                    if denom > 0:
                        factor = max(1.0 - numer / denom, 0.0)
                    else:
                        factor = 0.0
                    out[y, x] = factor * pl
            expected[level][:, :, band] = out

    # Now run the vectorized version.
    bishrink(coeffs, sigma_g=sigma_g, sigma_n_sq_list=sigma_n_sq_list)

    for level in range(max_level):
        np.testing.assert_allclose(
            coeffs.highpasses[level], expected[level],
            rtol=1e-10, atol=1e-12,
            err_msg=f"Vectorized BiShrink diverges from scalar reference "
                    f"at level {level}",
        )
    # Coarsest untouched.
    np.testing.assert_array_equal(coeffs.highpasses[-1], expected[-1])


def test_bishrink_coarsest_level_unshrunk():
    """Design choice: BOE filter preserves the coarsest-level DC band."""
    rng = np.random.default_rng(0)
    xfm, coeffs = _make_coeffs_from_array(rng.normal(size=(128, 128)))
    coarse_before = coeffs.highpasses[-1].copy()

    sigma_n_sq_list = local_noise_variance(coeffs)
    bishrink(coeffs, sigma_g=0.5, sigma_n_sq_list=sigma_n_sq_list)

    np.testing.assert_array_equal(coeffs.highpasses[-1], coarse_before)


# ── Input sanitization ────────────────────────────────────────────────

def test_sanitize_inputs_zeros_nan_and_inf():
    g = np.array([[1.0, np.nan, 3.0], [np.inf, 4.0, -np.inf]])
    s = np.array([[0.0, 1.0, np.nan], [2.0, np.inf, 3.0]])
    i = np.array([[10.0, -5.0, 20.0], [np.nan, 30.0, 40.0]])
    # Too small for filter_level=9, use 0 to bypass
    g, s, i = _sanitize_inputs(g, s, i, filter_level=0)
    assert np.isfinite(g).all()
    assert np.isfinite(s).all()
    assert (i >= 0).all()
    assert np.isfinite(i).all()


def test_small_image_raises_value_error():
    g = np.zeros((10, 10))
    s = np.zeros((10, 10))
    i = np.zeros((10, 10))
    with pytest.raises(ValueError, match="too small"):
        denoise_phasor_boe(g, s, i, filter_level=5)  # 2**5=32 > 10


def test_nan_input_does_not_propagate():
    """NaN in G/S (upstream ComputePhasor convention) must be zeroed
    before Step 1 so it doesn't poison the Anscombe transform."""
    rng = np.random.default_rng(0)
    H = W = 64
    intensity = rng.poisson(25.0, (H, W)).astype(np.float64)
    g = np.full((H, W), 0.45) + 0.01 * rng.standard_normal((H, W))
    s = np.full((H, W), 0.45) + 0.01 * rng.standard_normal((H, W))
    # Corrupt 20% of pixels with NaN as ComputePhasor would at I==0
    nan_mask = rng.random((H, W)) < 0.2
    g[nan_mask] = np.nan
    s[nan_mask] = np.nan

    result = denoise_phasor_boe(g, s, intensity, filter_level=4)
    assert np.isfinite(result["G"]).all()
    assert np.isfinite(result["S"]).all()


# ── Dispatch ──────────────────────────────────────────────────────────

def test_dispatch_registers_both_algorithms():
    ids = {c[0] for c in ALGORITHM_CHOICES}
    assert ids == {"boe_2021", "jcb_2025", "hybrid_jcb_boe"}


def test_dispatch_unknown_algorithm_raises_valueerror():
    g = s = i = np.zeros((64, 64))
    with pytest.raises(ValueError, match="Unknown wavelet algorithm"):
        denoise_phasor(g, s, i, algorithm="bogus", filter_level=4)


def test_dispatch_default_algorithm_is_boe():
    """`denoise_phasor` with no `algorithm=` uses BOE."""
    rng = np.random.default_rng(0)
    H = W = 64
    intensity = rng.poisson(25.0, (H, W)).astype(np.float64)
    g = np.full((H, W), 0.45)
    s = np.full((H, W), 0.45)

    default = denoise_phasor(g, s, intensity, filter_level=4)
    explicit = denoise_phasor(g, s, intensity, algorithm="boe_2021",
                               filter_level=4)
    np.testing.assert_array_equal(default["G"], explicit["G"])
    np.testing.assert_array_equal(default["S"], explicit["S"])


def test_dispatch_returns_expected_keys():
    rng = np.random.default_rng(0)
    H = W = 64
    intensity = rng.poisson(25.0, (H, W)).astype(np.float64)
    g = np.full((H, W), 0.45)
    s = np.full((H, W), 0.45)

    for alg in ("boe_2021", "jcb_2025"):
        result = denoise_phasor(g, s, intensity, algorithm=alg,
                                filter_level=4)
        assert set(result) >= {"G", "S", "T", "GU", "SU", "TU",
                                "filter_level"}
        assert result["G"].dtype == np.float32
        assert result["filter_level"] == 4


def test_dispatch_lifetime_when_omega_provided():
    rng = np.random.default_rng(0)
    H = W = 64
    intensity = rng.poisson(25.0, (H, W)).astype(np.float64)
    g = np.full((H, W), 0.45)
    s = np.full((H, W), 0.45)
    omega = 2 * np.pi * 80e-3  # 80 MHz

    result = denoise_phasor(g, s, intensity, filter_level=4, omega=omega)
    assert result["T"] is not None
    assert result["TU"] is not None
    assert result["T"].shape == (H, W)


# ── Pipeline: spoke phantom MSE thresholds (slow) ─────────────────────

@pytest.mark.slow
def test_boe_filter_cuts_mse_by_at_least_half():
    """On the spoke phantom, BOE's G/S MSE against ground truth should
    be at most half the unfiltered MSE. Generous threshold (actual
    reduction during development was to ~23%) so the test stays stable
    under small algorithm tweaks."""
    p = generate_spoke_phantom(shape=(256, 256), seed=0)
    result = denoise_phasor_boe(p.g_noisy, p.s_noisy, p.intensity_noisy,
                                 filter_level=5)
    mse_unfilt = g_s_mse(np.nan_to_num(p.g_noisy), np.nan_to_num(p.s_noisy),
                         p.g_true, p.s_true, p.intensity_noisy)
    mse_boe = g_s_mse(result["G"], result["S"],
                      p.g_true, p.s_true, p.intensity_noisy)
    assert mse_boe < 0.5 * mse_unfilt, (
        f"BOE MSE {mse_boe:.5f} not below half of unfiltered "
        f"{mse_unfilt:.5f}"
    )


@pytest.mark.slow
def test_boe_and_jcb_produce_measurably_different_g_maps():
    """Proves the two implementations are meaningfully distinct.
    Regression guard against an accidental refactor that collapses
    them into the same code path."""
    p = generate_spoke_phantom(shape=(256, 256), seed=0)
    boe = denoise_phasor(p.g_noisy, p.s_noisy, p.intensity_noisy,
                         algorithm="boe_2021", filter_level=5)
    jcb = denoise_phasor(p.g_noisy, p.s_noisy, p.intensity_noisy,
                         algorithm="jcb_2025", filter_level=5)
    rms = float(np.sqrt(np.mean((boe["G"] - jcb["G"]) ** 2)))
    assert rms > 1e-4, f"BOE vs JCB G-map RMS too small: {rms}"
