"""JCB 2025 FLIM wavelet filter.

Byte-for-byte port of the Python reference implementation at
``https://github.com/LeeLabBCM/ComplexWaveletFilter`` (the code backing
Fahim, Marcus et al., *J. Cell Biol.* 2025). Kept available so analyses
published against the JCB paper remain reproducible through percell4.

This is NOT the strict Wang et al. 2021 BOE paper algorithm — it differs
in three ways (global σ_g across all levels/bands, simplified shrinkage
dropping the ``(σ_n² − σ_g²)_+`` factor, and ``biort='Legall'``). For the
paper-strict implementation see :mod:`percell4.domain.flim.wavelet.boe`.

Requires the optional ``dtcwt`` package: ``pip install percell4[flim]``.
"""

from __future__ import annotations

import logging

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import uniform_filter

# NumPy-2.0 shim for dtcwt's removed np.asfarray / np.issubsctype.
# Imported (but not used directly) to ensure the monkey-patch runs
# before any dtcwt import triggered inside `_filter_channel`.
from percell4 import _compat  # noqa: F401
from percell4.domain.flim.wavelet._shared import (
    anscombe_forward,
    anscombe_inverse,
    next_pow2,
)

logger = logging.getLogger(__name__)


# ── Noise estimation (vectorized) ──────────────────────────────────────

def calculate_median_values(transformed_data):
    """Mean of per-band median-absolute wavelet coefficients across all
    levels and all 6 bands.

    Note: this is the JCB-repo behaviour, NOT the BOE paper specification.
    The BOE paper uses only level-1 ±45° bands.
    """
    median_values = []
    for level in range(len(transformed_data.highpasses)):
        highpasses = transformed_data.highpasses[level]
        for band in range(highpasses.shape[2]):
            coeffs = highpasses[:, :, band]
            median_absolute = np.median(np.abs(coeffs))
            median_values.append(median_absolute)
    return np.mean(median_values)


def calculate_local_noise_variance(transformed_data, n_levels):
    """Vectorized local noise variance via ``uniform_filter``.

    Window half-width follows the JCB-repo rule: ``N = min(3, n_levels)``
    when ``n_levels > 10``, else ``N = n_levels``. This is NOT the BOE
    paper convention (which uses ``N = 3`` uniformly).
    """
    sigma_n_squared_matrices = []
    window_size = 3 if n_levels > 10 else n_levels
    kernel = 2 * window_size + 1

    for level in range(len(transformed_data.highpasses)):
        highpasses = transformed_data.highpasses[level]
        for band in range(highpasses.shape[2]):
            coeffs = highpasses[:, :, band]
            abs_sq = np.abs(coeffs) ** 2
            snq = uniform_filter(abs_sq.real, size=kernel, mode="constant")
            sigma_n_squared_matrices.append((level, band, snq))

    return sigma_n_squared_matrices


# ── Inter-scale Wiener shrinkage (vectorized) ──────────────────────────

def compute_phi_prime(mandrill_t, sigma_g_squared, sigma_n_squared_matrices):
    """Vectorized inter-scale Wiener-like shrinkage used by JCB 2025.

    Shrinkage factor (simpler than Sendur-Selesnick BiShrink — the
    ``(σ_n² − σ_g²)_+`` term that the paper uses in the denominator is
    dropped here; ``sigma_n_squared`` is read only to gate the factor):

        factor = max(0, 1 − √3·σ_g / √(|Φ_l|² + |Φ_parent|² + √3·σ_g))
    """
    updated_coefficients = []
    max_level = len(mandrill_t.highpasses) - 1
    local_term = np.sqrt(3) * np.sqrt(sigma_g_squared)

    for level in range(max_level):
        highpasses_l = mandrill_t.highpasses[level]
        highpasses_l_plus_1 = mandrill_t.highpasses[level + 1]
        level_coefficients = []

        for band in range(highpasses_l.shape[2]):
            phi_l_b = highpasses_l[:, :, band]
            phi_l_plus_1_b = highpasses_l_plus_1[:, :, band]

            _, _, sigma_n_squared = sigma_n_squared_matrices[level * 6 + band]

            phi_l_sq = np.abs(phi_l_b) ** 2

            h_l, w_l = phi_l_b.shape
            h_next, w_next = phi_l_plus_1_b.shape
            phi_next_sq = np.abs(phi_l_plus_1_b) ** 2

            y_idx = np.minimum(np.arange(h_l) // 2, h_next - 1)
            x_idx = np.minimum(np.arange(w_l) // 2, w_next - 1)
            phi_next_upsampled = phi_next_sq[np.ix_(y_idx, x_idx)]

            phi_squared_sum = phi_l_sq + phi_next_upsampled

            if sigma_n_squared.shape != phi_l_b.shape:
                ds = max(1, phi_l_b.shape[0] // sigma_n_squared.shape[0])
                y_ds = np.minimum(
                    np.arange(h_l) // ds, sigma_n_squared.shape[0] - 1
                )
                x_ds = np.minimum(
                    np.arange(w_l) // ds, sigma_n_squared.shape[1] - 1
                )
                sigma_n_sq = sigma_n_squared[np.ix_(y_ds, x_ds)]
            else:
                sigma_n_sq = sigma_n_squared

            denominator = np.sqrt(phi_squared_sum + local_term)
            factor = np.where(
                (sigma_n_sq > 0) & (phi_squared_sum > 0),
                1.0 - local_term / denominator,
                0.0,
            )
            factor = np.maximum(factor, 0.0)

            phi_prime = factor * phi_l_b
            level_coefficients.append(phi_prime)

        updated_coefficients.append(level_coefficients)

    return updated_coefficients


def update_coefficients(mandrill_t, phi_prime_matrices):
    for level, level_matrices in enumerate(phi_prime_matrices):
        for band, phi_prime in enumerate(level_matrices):
            if band < mandrill_t.highpasses[level].shape[2]:
                mandrill_t.highpasses[level][:, :, band] = phi_prime


# ── Main filter function ──────────────────────────────────────────────

def _filter_channel(data: NDArray, n_levels: int) -> NDArray:
    """JCB pipeline: pad → Anscombe → DTCWT → shrink → iDTCWT → iAnscombe.

    Pads to the next power of 2 (not the next multiple of 2^n_levels) —
    matches the upstream JCB-repo behaviour rather than the tighter
    padding used by :mod:`.boe`.
    """
    import dtcwt

    h, w = data.shape
    pad_h = next_pow2(h) - h
    pad_w = next_pow2(w) - w
    padded = np.pad(data, ((0, pad_h), (0, pad_w)), mode="reflect")

    transformed = anscombe_forward(padded)

    # Uses `biort='legall'` — the LeGall 5/3 filter bank matching the
    # upstream `LeeLabBCM/ComplexWaveletFilter` repo. The previous
    # percell4 port used `biort='near_sym_a'`; restoring `'legall'` here
    # realigns percell4's JCB mode with the code that backs the
    # published JCB 2025 paper. (dtcwt's filename lookup is
    # case-sensitive; lowercase is portable across filesystems.)
    xfm = dtcwt.Transform2d(biort="legall", qshift="qshift_a")
    coeffs = xfm.forward(transformed, nlevels=n_levels)

    median_vals = calculate_median_values(coeffs)
    sigma_g_squared = median_vals / 0.6745

    sigma_n_squared = calculate_local_noise_variance(coeffs, n_levels)

    phi_prime = compute_phi_prime(coeffs, sigma_g_squared, sigma_n_squared)
    update_coefficients(coeffs, phi_prime)

    reconstructed = xfm.inverse(coeffs)
    result = anscombe_inverse(reconstructed)

    return result[:h, :w]


def denoise_phasor_jcb(
    g: NDArray,
    s: NDArray,
    intensity: NDArray,
    *,
    filter_level: int = 9,
    omega: float | None = None,
) -> dict[str, NDArray]:
    """Apply JCB-style DTCWT wavelet denoising to FLIM phasor data.

    Parameters
    ----------
    g : (H, W) array
        Unfiltered G phasor coordinate map.
    s : (H, W) array
        Unfiltered S phasor coordinate map.
    intensity : (H, W) array
        Total photon counts per pixel.
    filter_level : int, optional
        DTCWT decomposition depth (default 9).
    omega : float, optional
        Angular frequency in rad/ns. If supplied, a lifetime map is
        computed and bounded to [0, 50] ns.

    Returns
    -------
    dict[str, NDArray]
        Keys: ``G`` (filtered G), ``S`` (filtered S), ``T`` (filtered
        lifetime or None), ``GU``/``SU``/``TU`` (unfiltered copies),
        ``filter_level``.
    """
    g = g.astype(np.float64)
    s = s.astype(np.float64)
    intensity = intensity.astype(np.float64)

    g_unfiltered = g.copy()
    s_unfiltered = s.copy()

    f_real = g * intensity
    f_imag = s * intensity

    logger.info("JCB filter: Freal...")
    f_real_filtered = _filter_channel(f_real, filter_level)
    logger.info("JCB filter: Fimag...")
    f_imag_filtered = _filter_channel(f_imag, filter_level)
    logger.info("JCB filter: intensity...")
    intensity_filtered = _filter_channel(intensity, filter_level)

    int_safe = np.where(intensity_filtered > 0, intensity_filtered, 1.0)
    g_filtered = f_real_filtered / int_safe
    s_filtered = f_imag_filtered / int_safe

    zero_mask = intensity_filtered <= 0
    g_filtered[zero_mask] = 0.0
    s_filtered[zero_mask] = 0.0

    t_filtered = None
    t_unfiltered = None
    if omega is not None and omega > 0:
        with np.errstate(divide="ignore", invalid="ignore"):
            t_filtered = s_filtered / (omega * g_filtered)
            t_unfiltered = s_unfiltered / (omega * g_unfiltered)
        t_filtered = np.where(
            (t_filtered < 0) | (t_filtered > 50) | np.isnan(t_filtered),
            np.nan,
            t_filtered,
        )
        t_unfiltered = np.where(
            (t_unfiltered < 0) | (t_unfiltered > 50) | np.isnan(t_unfiltered),
            np.nan,
            t_unfiltered,
        )

    return {
        "G": g_filtered.astype(np.float32),
        "S": s_filtered.astype(np.float32),
        "T": t_filtered.astype(np.float32) if t_filtered is not None else None,
        "GU": g_unfiltered.astype(np.float32),
        "SU": s_unfiltered.astype(np.float32),
        "TU": t_unfiltered.astype(np.float32) if t_unfiltered is not None else None,
        "filter_level": filter_level,
    }
