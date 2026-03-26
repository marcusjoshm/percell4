"""DTCWT-based wavelet filtering for FLIM phasor data.

Operates on post-computed G/S phasor maps (not raw decay). Reduces phasor
scatter while preserving spatial structure. Adapted from
leelab/flimfret/src/python/modules/wavelet_filter.py.

Requires the optional ``dtcwt`` package: ``pip install dtcwt>=0.14.0``
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def anscombe_transform(data: NDArray) -> NDArray:
    """Variance-stabilizing Anscombe transform for Poisson data."""
    return 2.0 * np.sqrt(np.maximum(data + 3.0 / 8.0, 0.0))


def inverse_anscombe_transform(data: NDArray) -> NDArray:
    """Inverse Anscombe transform (algebraic approximation).

    Uses the exact unbiased inverse for large values and a sixth-order
    rational approximation for accuracy at low values.
    """
    y = data
    # Algebraic inverse: (y/2)^2 - 3/8
    result = (y / 2.0) ** 2 - 3.0 / 8.0
    # Correction for small values
    result = np.maximum(result, 0.0)
    return result


def denoise_phasor(
    g: NDArray,
    s: NDArray,
    intensity: NDArray,
    filter_level: int = 9,
    omega: float | None = None,
) -> dict[str, NDArray]:
    """Apply DTCWT-based wavelet filtering to FLIM phasor data.

    Denoises G and S phasor maps by:
    1. Rescaling to Fourier coefficients (G*intensity, S*intensity)
    2. Applying Anscombe variance stabilization
    3. Running DTCWT with adaptive thresholding
    4. Inverting to recover filtered phasor coordinates

    Parameters
    ----------
    g : (H, W) G phasor coordinate map
    s : (H, W) S phasor coordinate map
    intensity : (H, W) total photon counts per pixel
    filter_level : DTCWT decomposition depth (default 9)
    omega : angular frequency in rad/ns (for lifetime calculation, optional)

    Returns
    -------
    dict with keys:
        'G' : filtered G map
        'S' : filtered S map
        'T' : filtered lifetime map (if omega provided, else None)
        'GU' : unfiltered G map (copy of input)
        'SU' : unfiltered S map (copy of input)
        'TU' : unfiltered lifetime map (if omega provided, else None)
        'filter_level' : decomposition level used
    """
    try:
        import dtcwt
    except ImportError as e:
        raise ImportError(
            "dtcwt package required for wavelet filtering.\n"
            "Install with: pip install 'percell4[flim]'"
        ) from e

    g = g.astype(np.float64)
    s = s.astype(np.float64)
    intensity = intensity.astype(np.float64)

    # Unfiltered copies
    g_unfiltered = g.copy()
    s_unfiltered = s.copy()

    # Step 1: Rescale to Fourier coefficients
    f_real = g * intensity
    f_imag = s * intensity

    # Step 2-5: Filter each channel (Freal, Fimag, intensity)
    f_real_filtered = _filter_channel(f_real, filter_level, dtcwt)
    f_imag_filtered = _filter_channel(f_imag, filter_level, dtcwt)
    intensity_filtered = _filter_channel(intensity, filter_level, dtcwt)

    # Step 6: Recover filtered phasor
    int_safe = np.where(intensity_filtered > 0, intensity_filtered, 1.0)
    g_filtered = f_real_filtered / int_safe
    s_filtered = f_imag_filtered / int_safe

    # Mark zero-intensity pixels as NaN
    zero_mask = intensity_filtered <= 0
    g_filtered[zero_mask] = np.nan
    s_filtered[zero_mask] = np.nan

    # Lifetime calculation if omega provided
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


def _filter_channel(data: NDArray, n_levels: int, dtcwt_module) -> NDArray:
    """Apply DTCWT denoising to a single 2D channel.

    1. Anscombe transform (variance stabilization)
    2. Forward DTCWT
    3. Estimate noise from MAD of finest-level coefficients
    4. Adaptive thresholding of wavelet coefficients
    5. Inverse DTCWT
    6. Inverse Anscombe transform
    """
    # Pad to power-of-2 dimensions for DTCWT
    h, w = data.shape
    pad_h = _next_pow2(h) - h
    pad_w = _next_pow2(w) - w
    padded = np.pad(data, ((0, pad_h), (0, pad_w)), mode="reflect")

    # Anscombe transform
    transformed = anscombe_transform(padded)

    # Forward DTCWT
    xfm = dtcwt_module.Transform2d(biort="near_sym_a", qshift="qshift_a")
    coeffs = xfm.forward(transformed, nlevels=n_levels)

    # Noise estimation from finest level (MAD)
    finest = coeffs.highpasses[0]
    sigma = np.median(np.abs(finest)) / 0.6745

    # Adaptive thresholding
    if sigma > 0:
        for level_idx in range(len(coeffs.highpasses)):
            hp = coeffs.highpasses[level_idx]
            # Soft thresholding with level-dependent threshold
            threshold = sigma * np.sqrt(2.0 * np.log(hp.size))
            # Scale threshold by level (coarser levels get less aggressive)
            level_scale = 1.0 / (1.0 + level_idx * 0.5)
            t = threshold * level_scale
            # Soft shrinkage
            magnitude = np.abs(hp)
            shrunk = np.maximum(magnitude - t, 0) * np.sign(hp)
            # Where magnitude was below threshold, keep original phase
            coeffs.highpasses[level_idx] = np.where(
                magnitude > t, shrunk, hp * 0.0
            )

    # Inverse DTCWT
    reconstructed = xfm.inverse(coeffs)

    # Inverse Anscombe
    result = inverse_anscombe_transform(reconstructed)

    # Remove padding
    return result[:h, :w]


def _next_pow2(n: int) -> int:
    """Return the smallest power of 2 >= n."""
    p = 1
    while p < n:
        p *= 2
    return p
