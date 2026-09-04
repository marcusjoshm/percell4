"""Hybrid wavelet filter: JCB skeleton + JCB σ_g + BOE shrinkage.

A reverse-engineering probe for the Leica LAS X post-phasor wavelet
filter. Combines:

- **From JCB** (``jcb.py``): ``next_pow2`` padding, filter_level-dependent
  local-noise window (``N = min(3, flevel)`` when flevel>10 else flevel),
  serial per-channel execution, index-math NN upsample, and the
  JCB σ_g estimator (mean of per-band medians across *all* levels and
  bands, divided by 0.6745 → global scalar σ_g).
- **From BOE** (``boe.py``): full Sendur-Selesnick BiShrink shrinkage
  with the ``(σ_n² − σ_g²)_+`` gate::

      factor = max(0, 1 − √3·σ_g² / √(R²·(σ_n² − σ_g²)_+))
      Φ_l'   = factor · Φ_l

The point is to isolate whether JCB's strong level-scaling comes from
its σ_g estimator or from its gate-free shrinkage formula. The prior
revision of this file used BOE σ_g + BOE shrinkage and matched BOE's
saturation almost exactly — so the BOE BiShrink gate is the suspected
source of BOE-style saturation, not the σ_g estimator. This revision
flips the σ_g side to test that hypothesis directly.
"""

from __future__ import annotations

import logging

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import uniform_filter

from percell4 import _compat  # noqa: F401
from percell4.domain.flim.wavelet._shared import (
    anscombe_forward,
    anscombe_inverse,
    next_pow2,
)

logger = logging.getLogger(__name__)


# ── JCB σ_g estimator (global scalar, mean of medians over all bands) ──

def estimate_sigma_g_jcb(coeffs) -> float:
    """JCB-repo global σ_g: mean of ``median(|Φ|)`` across every level
    and every band, divided by ``0.6745`` (MAD-to-stddev ratio for a
    zero-mean Gaussian).

    Mirrors ``percell4.domain.flim.wavelet.jcb.calculate_median_values``
    but inlined here so the hybrid stays a self-contained probe —
    mutating the estimator without touching JCB proper is a common sweep.
    """
    medians = []
    for level in range(len(coeffs.highpasses)):
        hp = coeffs.highpasses[level]
        for band in range(hp.shape[2]):
            medians.append(np.median(np.abs(hp[:, :, band])))
    return float(np.mean(medians) / 0.6745)


# ── JCB-style local noise variance (filter-level-dependent window) ─────

def local_noise_variance_jcb(coeffs, n_levels: int):
    """JCB-repo local noise variance. Returns list of (level, band, σ_n²).

    Window half-width: ``N = 3`` when ``n_levels > 10`` else ``N = n_levels``.
    ``mode='constant'`` matches the JCB reference (vs. BOE's ``'reflect'``).
    """
    out = []
    window_size = 3 if n_levels > 10 else n_levels
    kernel = 2 * window_size + 1
    for level in range(len(coeffs.highpasses)):
        highpasses = coeffs.highpasses[level]
        for band in range(highpasses.shape[2]):
            abs_sq = np.abs(highpasses[:, :, band]) ** 2
            snq = uniform_filter(abs_sq.real, size=kernel, mode="constant")
            out.append((level, band, snq))
    return out


# ── BOE-style Sendur-Selesnick BiShrink, JCB-style upsampling ──────────

def bishrink_hybrid(coeffs, sigma_g: float, sigma_n_sq_list) -> None:
    """Sendur-Selesnick BiShrink with JCB's index-math parent upsample.

    Same shrinkage formula as :func:`percell4.domain.flim.wavelet.boe.
    bishrink`, but the parent is upsampled with JCB's clamp-indexed
    gather (tolerant of non-exact 2× shape ratios) rather than
    ``np.repeat×2`` + crop.

    Coarsest level left unshrunk (mean-lifetime carrier).
    """
    sigma_g_sq = sigma_g ** 2
    numer = np.sqrt(3.0) * sigma_g_sq

    max_level = len(coeffs.highpasses) - 1
    for level in range(max_level):
        hp_l = coeffs.highpasses[level]
        hp_parent = coeffs.highpasses[level + 1]
        h_l, w_l = hp_l.shape[:2]
        h_next, w_next = hp_parent.shape[:2]
        y_idx = np.minimum(np.arange(h_l) // 2, h_next - 1)
        x_idx = np.minimum(np.arange(w_l) // 2, w_next - 1)

        for band in range(hp_l.shape[2]):
            phi_l = hp_l[:, :, band]
            phi_parent = hp_parent[:, :, band][np.ix_(y_idx, x_idx)]

            r_sq = np.abs(phi_l) ** 2 + np.abs(phi_parent) ** 2

            _, _, sigma_n_sq = sigma_n_sq_list[level * 6 + band]
            # JCB returns σ_n² on the full band grid already — no resample
            # needed because uniform_filter preserves shape.
            d = np.maximum(sigma_n_sq - sigma_g_sq, 0.0)
            denom = np.sqrt(r_sq * d)

            factor = np.where(
                denom > 0,
                np.maximum(1.0 - numer / np.where(denom > 0, denom, 1.0), 0.0),
                0.0,
            )
            hp_l[:, :, band] = factor * phi_l


# ── Per-channel pipeline ───────────────────────────────────────────────

def _filter_channel(data: NDArray, n_levels: int) -> NDArray:
    """JCB pipeline with BOE's σ_g + BOE's shrinkage swapped in."""
    import dtcwt

    h, w = data.shape
    pad_h = next_pow2(h) - h
    pad_w = next_pow2(w) - w
    padded = np.pad(data, ((0, pad_h), (0, pad_w)), mode="reflect")

    transformed = anscombe_forward(padded)

    xfm = dtcwt.Transform2d(biort="legall", qshift="qshift_a")
    coeffs = xfm.forward(transformed, nlevels=n_levels)

    sigma_g = estimate_sigma_g_jcb(coeffs)
    sigma_n_sq_list = local_noise_variance_jcb(coeffs, n_levels)

    bishrink_hybrid(coeffs, sigma_g, sigma_n_sq_list)

    reconstructed = xfm.inverse(coeffs)
    result = anscombe_inverse(reconstructed)
    return result[:h, :w]


# ── Public API ─────────────────────────────────────────────────────────

def denoise_phasor_hybrid(
    g: NDArray,
    s: NDArray,
    intensity: NDArray,
    *,
    filter_level: int = 9,
    omega: float | None = None,
) -> dict[str, NDArray]:
    """Hybrid filter — JCB σ_g + BOE Sendur-Selesnick BiShrink.

    Same signature and return contract as :func:`denoise_phasor_boe` and
    :func:`denoise_phasor_jcb`.
    """
    g = g.astype(np.float64)
    s = s.astype(np.float64)
    intensity = intensity.astype(np.float64)

    g_unfiltered = g.copy()
    s_unfiltered = s.copy()

    f_real = g * intensity
    f_imag = s * intensity

    logger.info(
        "Hybrid filter (JCB σ_g + BOE BiShrink) filter_level=%d",
        filter_level,
    )
    f_real_filtered = _filter_channel(f_real, filter_level)
    f_imag_filtered = _filter_channel(f_imag, filter_level)
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
