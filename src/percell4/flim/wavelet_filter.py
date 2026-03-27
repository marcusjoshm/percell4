"""DTCWT-based wavelet filtering for FLIM phasor data.

Exact port of flimfret/src/python/modules/wavelet_filter.py logic.
Uses inter-scale Wiener-like shrinkage with multi-scale coefficient
interaction. Slower than simplified soft thresholding but produces
the correct tight arc structure on the phasor plot.

Requires the optional ``dtcwt`` package: ``pip install dtcwt>=0.14.0``
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

# ── Transforms (exact copy from flimfret) ─────────────────────


def anscombe_transform(data):
    """Anscombe transform to stabilize Poisson noise variance."""
    return 2 * np.sqrt(np.maximum(data + (3 / 8), 0))


def reverse_anscombe_transform(y):
    """Inverse Anscombe transform (sixth-order rational approximation)."""
    y = np.asarray(y, dtype=np.float64)
    y = np.maximum(y, 1e-6)
    inverse = (
        (y**2 / 4)
        + (np.sqrt(3 / 2) * (1 / y) / 4)
        - (11 / (8 * y**2))
        + (np.sqrt(5 / 2) * (1 / y**3) / 8)
        - (1 / (8 * y**4))
    )
    return np.maximum(inverse, 0)


# ── Noise estimation (exact copy from flimfret) ───────────────


def calculate_median_values(transformed_data):
    """Calculate median absolute values of wavelet coefficients."""
    median_values = []
    for level in range(len(transformed_data.highpasses)):
        highpasses = transformed_data.highpasses[level]
        for band in range(highpasses.shape[2]):
            coeffs = highpasses[:, :, band]
            median_absolute = np.median(np.abs(coeffs.flatten()))
            median_values.append(median_absolute)
    return np.mean(median_values)


def calculate_local_noise_variance(transformed_data, n_levels):
    """Calculate local noise variance for each coefficient.

    Uses a sliding window (size=3) to estimate local variance
    at each position in each level and band.
    """
    sigma_n_squared_matrices = []
    window_size = 3 if n_levels > 10 else n_levels

    def _local_noise_variance(coeffs, ws):
        sigma_n_squared = np.zeros_like(coeffs, dtype=float)
        height, width = coeffs.shape
        for x in range(width):
            for y in range(height):
                x_min = max(0, x - ws)
                x_max = min(width, x + ws + 1)
                y_min = max(0, y - ws)
                y_max = min(height, y + ws + 1)
                window = coeffs[y_min:y_max, x_min:x_max]
                sigma_n_squared[y, x] = np.mean(np.abs(window) ** 2)
        return sigma_n_squared

    num_levels = len(transformed_data.highpasses)
    for level in range(num_levels):
        highpasses = transformed_data.highpasses[level]
        for band in range(highpasses.shape[2]):
            coeffs = highpasses[:, :, band]
            snq = _local_noise_variance(coeffs, window_size)
            sigma_n_squared_matrices.append((level, band, snq))

    return sigma_n_squared_matrices


# ── Inter-scale Wiener shrinkage (exact copy from flimfret) ────


def compute_phi_prime(mandrill_t, sigma_g_squared, sigma_n_squared_matrices):
    """Compute modified wavelet coefficients using inter-scale Wiener shrinkage.

    For each coefficient, computes a shrinkage factor based on:
    - The coefficient magnitude at the current level
    - The corresponding coefficient at the next coarser level
    - The local noise variance estimate
    - The global signal variance estimate

    This produces much tighter denoising than simple soft thresholding.
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
            phi_prime = np.zeros_like(phi_l_b, dtype=complex)

            # Account for size mismatch due to downsampling
            if sigma_n_squared.shape != phi_l_b.shape:
                downsample_factor = max(
                    1, phi_l_b.shape[0] // sigma_n_squared.shape[0]
                )
            else:
                downsample_factor = 1

            for x in range(phi_l_b.shape[1]):
                for y in range(phi_l_b.shape[0]):
                    x_half = x // 2
                    y_half = y // 2

                    x_ds = min(
                        x // downsample_factor, sigma_n_squared.shape[1] - 1
                    )
                    y_ds = min(
                        y // downsample_factor, sigma_n_squared.shape[0] - 1
                    )

                    if (
                        y_half >= highpasses_l_plus_1.shape[0]
                        or x_half >= highpasses_l_plus_1.shape[1]
                    ):
                        continue

                    phi_squared_sum = np.abs(phi_l_b[y, x]) ** 2
                    if (
                        y_half < phi_l_plus_1_b.shape[0]
                        and x_half < phi_l_plus_1_b.shape[1]
                    ):
                        phi_squared_sum += (
                            np.abs(phi_l_plus_1_b[y_half, x_half]) ** 2
                        )

                    if (
                        sigma_n_squared[y_ds, x_ds] > 0
                        and phi_squared_sum > 0
                    ):
                        denominator = np.sqrt(phi_squared_sum + local_term)
                        factor = 1 - local_term / denominator
                    else:
                        factor = 0

                    factor = max(factor, 0)
                    phi_prime[y, x] = factor * phi_l_b[y, x]

            level_coefficients.append(phi_prime)
        updated_coefficients.append(level_coefficients)

    return updated_coefficients


def update_coefficients(mandrill_t, phi_prime_matrices):
    """Update wavelet coefficients with filtered values."""
    for level, level_matrices in enumerate(phi_prime_matrices):
        for band, phi_prime in enumerate(level_matrices):
            if band < mandrill_t.highpasses[level].shape[2]:
                mandrill_t.highpasses[level][:, :, band] = phi_prime


# ── Main filter function ──────────────────────────────────────


def _next_pow2(n: int) -> int:
    """Return the smallest power of 2 >= n."""
    p = 1
    while p < n:
        p *= 2
    return p


def _filter_channel(data: NDArray, n_levels: int) -> NDArray:
    """Apply DTCWT denoising to a single 2D channel.

    Uses exact flimfret algorithm: Anscombe → DTCWT → inter-scale
    Wiener shrinkage → inverse DTCWT → inverse Anscombe.
    """
    import dtcwt

    # Pad to power-of-2 dimensions for DTCWT
    h, w = data.shape
    pad_h = _next_pow2(h) - h
    pad_w = _next_pow2(w) - w
    padded = np.pad(data, ((0, pad_h), (0, pad_w)), mode="reflect")

    # Anscombe transform
    transformed = anscombe_transform(padded)

    # Forward DTCWT
    xfm = dtcwt.Transform2d(biort="near_sym_a", qshift="qshift_a")
    coeffs = xfm.forward(transformed, nlevels=n_levels)

    # Noise estimation
    median_vals = calculate_median_values(coeffs)
    sigma_g_squared = median_vals / 0.6745

    # Local noise variance (pixel-by-pixel, slow but exact)
    sigma_n_squared = calculate_local_noise_variance(coeffs, n_levels)

    # Inter-scale Wiener shrinkage (pixel-by-pixel, slow but exact)
    phi_prime = compute_phi_prime(coeffs, sigma_g_squared, sigma_n_squared)
    update_coefficients(coeffs, phi_prime)

    # Inverse DTCWT
    reconstructed = xfm.inverse(coeffs)

    # Inverse Anscombe
    result = reverse_anscombe_transform(reconstructed)

    # Remove padding
    return result[:h, :w]


def denoise_phasor(
    g: NDArray,
    s: NDArray,
    intensity: NDArray,
    filter_level: int = 9,
    omega: float | None = None,
) -> dict[str, NDArray]:
    """Apply DTCWT-based wavelet filtering to FLIM phasor data.

    Uses flimfret's exact inter-scale Wiener shrinkage algorithm.
    This is slow (nested Python loops) but produces the correct
    tight arc structure on the phasor plot.

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
    g = g.astype(np.float64)
    s = s.astype(np.float64)
    intensity = intensity.astype(np.float64)

    # Unfiltered copies
    g_unfiltered = g.copy()
    s_unfiltered = s.copy()

    # Step 1: Rescale to Fourier coefficients
    f_real = g * intensity
    f_imag = s * intensity

    # Step 2-5: Filter each channel using flimfret's exact algorithm
    print("  Filtering Freal...")
    f_real_filtered = _filter_channel(f_real, filter_level)
    print("  Filtering Fimag...")
    f_imag_filtered = _filter_channel(f_imag, filter_level)
    print("  Filtering intensity...")
    intensity_filtered = _filter_channel(intensity, filter_level)

    # Step 6: Recover filtered phasor
    int_safe = np.where(intensity_filtered > 0, intensity_filtered, 1.0)
    g_filtered = f_real_filtered / int_safe
    s_filtered = f_imag_filtered / int_safe

    # Mark zero-intensity pixels
    zero_mask = intensity_filtered <= 0
    g_filtered[zero_mask] = 0.0
    s_filtered[zero_mask] = 0.0

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
