"""BOE 2021 FLIM wavelet filter — strict replication of Wang et al.

Implements the algorithm specified in Wang, Hecht, Ossato, Tille, Fraser,
Junge (2021), *Biomed. Opt. Express* 12(6), 3463, DOI 10.1364/BOE.420953,
including the equations and filter-bank tables of its supplement:

- DTCWT with LeGall 5/3 (level 1) + Kingsbury Q-shift 10 (higher levels).
- Global noise estimator: ``σ_g = median(|Φ(level 1, ±45°)|) / 0.6745``.
- Local noise variance: mean of ``|Φ|²`` in a ``(2·N + 1)²`` neighborhood
  (N = 3 per Sendur & Selesnick), computed with ``mode='reflect'``.
- Inter-scale BiShrink (Sendur & Selesnick 2002/2003)::

      factor = max(0, 1 − √3·σ_g² / √((|Φ_l|² + |Φ_parent|²)·(σ_n² − σ_g²)_+))
      Φ_l'   = factor · Φ_l

- Anscombe forward / Mäkitalo-Foi inverse (unbiased at low photon counts).
- Three channels (F_real, F_imag, I) run concurrently via
  :class:`concurrent.futures.ThreadPoolExecutor` — dtcwt releases the GIL
  in its FFT core.

For the simpler JCB-repo variant (global σ_g over all bands, shrinkage
without the ``(σ_n² − σ_g²)_+`` term) see :mod:`.jcb`.

Requires the optional ``dtcwt`` package: ``pip install percell4[flim]``.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

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
    next_multiple,
)

logger = logging.getLogger(__name__)


# ── dtcwt band index → orientation ────────────────────────────────────

# dtcwt's 2D transform returns 6 complex highpass bands per level at
# orientations {+15°, +45°, +75°, −75°, −45°, −15°}. These two are the
# diagonal-detail bands the BOE paper uses for σ_g estimation.
# Verified against dtcwt's q2c function and pytorch-wavelets docs;
# protected by a pytest assertion in tests/test_flim/test_wavelet_boe.py
# so a silent reordering in a future dtcwt release is caught.
BAND_PLUS_45: int = 1
BAND_MINUS_45: int = 4

# Sendur & Selesnick (2003) local-variance window half-width.
# Full window is (2·N + 1)² → 7×7 by default.
DEFAULT_N_LOCAL: int = 3

# Tree-A analysis taps expected from `biort='legall'` (BOE Table S1).
# Used by the coefficient verification test.
EXPECTED_LEGALL_H0O = np.array([-0.125, 0.25, 0.75, 0.25, -0.125])


# ── Input sanitization ────────────────────────────────────────────────

def _sanitize_inputs(
    g: NDArray, s: NDArray, intensity: NDArray, filter_level: int,
) -> tuple[NDArray, NDArray, NDArray]:
    """Zero out NaN/Inf, clamp negative intensity, check minimum size.

    Upstream ComputePhasor sets G/S = NaN wherever intensity is zero
    (division by zero). Unchecked, NaN propagates through the Anscombe
    transform and DTCWT into the entire filtered output. Required
    sanitization, not optional.
    """
    g = np.nan_to_num(g.astype(np.float64, copy=False),
                      nan=0.0, posinf=0.0, neginf=0.0)
    s = np.nan_to_num(s.astype(np.float64, copy=False),
                      nan=0.0, posinf=0.0, neginf=0.0)
    intensity = np.nan_to_num(intensity.astype(np.float64, copy=False),
                              nan=0.0, posinf=0.0, neginf=0.0)
    intensity = np.maximum(intensity, 0.0)

    h, w = g.shape
    required = 2 ** filter_level
    if min(h, w) < required:
        raise ValueError(
            f"Image {h}×{w} is too small for filter_level={filter_level}: "
            f"each dimension must be ≥ 2**{filter_level} = {required}."
        )
    return g, s, intensity


# ── Noise estimation ──────────────────────────────────────────────────

def estimate_sigma_g(coeffs) -> float:
    """Global noise standard-deviation estimate σ_g.

    MAD/0.6745 applied to the level-1 ±45° diagonal bands only. First-
    level HH coefficients are dominated by noise, and the robust MAD/
    0.6745 ratio converts the median absolute deviation of a zero-mean
    Gaussian to its stddev.
    """
    lvl1 = coeffs.highpasses[0]  # (H/2, W/2, 6) complex
    mad_plus = np.median(np.abs(lvl1[:, :, BAND_PLUS_45]))
    mad_minus = np.median(np.abs(lvl1[:, :, BAND_MINUS_45]))
    return float(np.median([mad_plus, mad_minus]) / 0.6745)


def local_noise_variance(
    coeffs, n_local: int = DEFAULT_N_LOCAL,
) -> list[NDArray]:
    """Local noise variance σ_n²(l, b, x, y) for every (level, band).

    Mean of ``|Φ|²`` over a ``(2·n_local + 1)²`` neighborhood with
    ``mode='reflect'`` so edge windows fold inward rather than get
    dragged toward zero. Returns a flat list indexed by
    ``level * 6 + band``.
    """
    kernel = 2 * n_local + 1
    out: list[NDArray] = []
    for level in range(len(coeffs.highpasses)):
        highpasses = coeffs.highpasses[level]
        for band in range(highpasses.shape[2]):
            abs_sq = np.abs(highpasses[:, :, band]) ** 2
            out.append(uniform_filter(abs_sq, size=kernel, mode="reflect"))
    return out


# ── Inter-scale BiShrink (vectorized) ─────────────────────────────────

def _nn_upsample_2x(parent: NDArray, target_shape: tuple[int, int]) -> NDArray:
    """Nearest-neighbor 2× upsample, cropped to ``target_shape``.

    Canonical Sendur-Selesnick BiShrink expands the parent subband by
    Kronecker product with a 2×2 ones kernel. ``np.repeat × 2`` achieves
    the same thing and is readable.
    """
    expanded = np.repeat(np.repeat(parent, 2, axis=0), 2, axis=1)
    h, w = target_shape
    return expanded[:h, :w]


def bishrink(
    coeffs,
    sigma_g: float,
    sigma_n_sq_list: list[NDArray],
) -> None:
    """Apply Sendur-Selesnick bivariate shrinkage in place.

    For each fine level ``l ∈ {0, ..., L-2}`` and each band ``b``::

        Φ_parent    = NN-upsample of Φ(l+1, b)
        R²          = |Φ_l|² + |Φ_parent|²
        D           = max(σ_n²(l, b, x, y) − σ_g², 0)
        denom       = √(R² · D)
        factor      = max(0, 1 − √3·σ_g² / denom)   where denom > 0
                    = 0                             otherwise
        Φ_l'        = factor · Φ_l

    The coarsest level ``L-1`` is left unshrunk — it is DC-dominated and
    carries the mean-lifetime signal in phasor data, so shrinking it
    costs more than it gains.
    """
    sigma_g_sq = sigma_g ** 2
    numer = np.sqrt(3.0) * sigma_g_sq

    max_level = len(coeffs.highpasses) - 1
    for level in range(max_level):
        hp_l = coeffs.highpasses[level]
        hp_parent = coeffs.highpasses[level + 1]
        h_l, w_l = hp_l.shape[:2]

        for band in range(hp_l.shape[2]):
            phi_l = hp_l[:, :, band]
            phi_parent = _nn_upsample_2x(hp_parent[:, :, band], (h_l, w_l))

            r_sq = np.abs(phi_l) ** 2 + np.abs(phi_parent) ** 2

            sigma_n_sq = sigma_n_sq_list[level * 6 + band]
            d = np.maximum(sigma_n_sq - sigma_g_sq, 0.0)

            # denom = sqrt(r²·d). Fuses the two sqrts into one pass
            # (half the memory traffic of sqrt(r²)*sqrt(d)).
            denom = np.sqrt(r_sq * d)

            factor = np.where(
                denom > 0,
                np.maximum(1.0 - numer / np.where(denom > 0, denom, 1.0), 0.0),
                0.0,
            )
            hp_l[:, :, band] = factor * phi_l


# ── Per-channel pipeline ──────────────────────────────────────────────

def _filter_channel(
    data: NDArray, n_levels: int, n_local: int = DEFAULT_N_LOCAL,
) -> NDArray:
    """Pad → Anscombe → DTCWT → BiShrink → iDTCWT → iAnscombe → crop.

    Pads to the next multiple of ``2**n_levels`` (not the next power of
    two) to save memory and compute on non-pow2 inputs. dtcwt itself
    will further pad odd dimensions by duplicating the last row/column
    and emit ``logging.warn`` — the caller must crop back to the
    original shape after inverse.
    """
    import dtcwt

    h, w = data.shape
    h_pad = next_multiple(h, 2 ** n_levels)
    w_pad = next_multiple(w, 2 ** n_levels)
    padded = np.pad(data, ((0, h_pad - h), (0, w_pad - w)), mode="reflect")

    transformed = anscombe_forward(padded)

    xfm = dtcwt.Transform2d(biort="legall", qshift="qshift_a")
    coeffs = xfm.forward(transformed, nlevels=n_levels)

    sigma_g = estimate_sigma_g(coeffs)
    sigma_n_sq_list = local_noise_variance(coeffs, n_local=n_local)

    bishrink(coeffs, sigma_g, sigma_n_sq_list)

    reconstructed = xfm.inverse(coeffs)

    # dtcwt may return a slightly different shape than `padded` if it
    # auto-padded internally for odd-at-any-level dimensions. Crop back
    # to the explicit pad first, then to the original input shape.
    reconstructed = reconstructed[:h_pad, :w_pad]
    result = anscombe_inverse(reconstructed)
    return result[:h, :w]


# ── Public API ────────────────────────────────────────────────────────

def denoise_phasor_boe(
    g: NDArray,
    s: NDArray,
    intensity: NDArray,
    *,
    filter_level: int = 9,
    omega: float | None = None,
) -> dict[str, NDArray]:
    """Strict BOE 2021 FLIM wavelet filter.

    Parameters
    ----------
    g : (H, W) array
        Unfiltered G phasor coordinate map. NaN/Inf are zeroed upstream.
    s : (H, W) array
        Unfiltered S phasor coordinate map.
    intensity : (H, W) array
        Total photon counts per pixel. Negative values are clamped to 0.
    filter_level : int, optional
        DTCWT decomposition depth (default 9). Each dimension of ``g``
        must be at least ``2 ** filter_level``.
    omega : float, optional
        Angular frequency in rad/ns. When provided, a filtered lifetime
        map is returned (bounded to [0, 50] ns, NaN elsewhere).

    Returns
    -------
    dict[str, NDArray]
        Keys ``G``, ``S``, ``T`` (filtered, float32), ``GU``, ``SU``,
        ``TU`` (unfiltered copies, float32), ``filter_level``.

    Raises
    ------
    ValueError
        If ``min(H, W) < 2**filter_level``.
    """
    g_unfiltered = g.astype(np.float64, copy=True)
    s_unfiltered = s.astype(np.float64, copy=True)

    g, s, intensity = _sanitize_inputs(g, s, intensity, filter_level)

    # Step 1: rescale to Fourier coefficients (linearity lets us go
    # back through G·I, S·I rather than re-running the Fourier sum over
    # TCSPC bins).
    f_real = g * intensity
    f_imag = s * intensity

    # Step 2–7: run the three channels concurrently. dtcwt releases the
    # GIL in its FFT inner loops, so the thread pool gives a real
    # speedup on multi-core hosts (~2.5× for typical sizes).
    logger.info("BOE filter: Freal | Fimag | intensity (3 threads)...")
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_real = pool.submit(_filter_channel, f_real, filter_level)
        fut_imag = pool.submit(_filter_channel, f_imag, filter_level)
        fut_int = pool.submit(_filter_channel, intensity, filter_level)
        f_real_filtered = fut_real.result()
        f_imag_filtered = fut_imag.result()
        intensity_filtered = fut_int.result()

    # Step 8: recover filtered phasor.
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
            # Use sanitized unfiltered G/S (zeros where intensity was 0)
            # for the unfiltered lifetime. The pre-sanitization originals
            # would divide-by-zero instead.
            t_unfiltered = s / (omega * np.where(g != 0, g, 1.0))
            t_unfiltered = np.where(g == 0, np.nan, t_unfiltered)
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
