"""DTCWT-based wavelet filtering for FLIM phasor data.

Faithful match to the reference ``ComplexWaveletFilter.py`` (LeeLabBCM):
Anscombe → DTCWT (``biort='Legall'``, ``qshift='qshift_a'``) → inter-scale
Wiener-like shrinkage → inverse DTCWT → inverse Anscombe, followed by the
reference's phasor recovery (divide by filtered intensity, ``nan_to_num``,
threshold by *unfiltered* intensity, clip to ``[-0.1, 1.1]``). The math is
vectorized with numpy/scipy for ~100x speedup over the reference's nested
Python loops, but produces output identical to the reference to float
precision (verified against ``dataset_CWFlevels=9.npz``: G ~1e-8, S ~1e-5).

Three details are load-bearing for that identity and must not drift from
the reference: the Anscombe clamp order (``2√(max(data,0)+3/8)``), the
*unclamped* inverse Anscombe (clamping is deferred to ``nan_to_num`` +
clip in :func:`denoise_phasor`), and the ``Legall`` biorthogonal basis.

Requires the optional ``dtcwt`` package: ``pip install dtcwt>=0.14.0``
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import uniform_filter

# Maximum wavelet decomposition depth offered to users. The GUI spinbox and
# the batch CLIs (percell4-batch-phasor / batch_compute_phasor) share this one
# ceiling. It is NOT a DTCWT hard limit — dtcwt pads internally and accepts far
# more — but a generous, sane cap. Real microscopy images saturate their
# meaningful scales (~log2 of the image dimension) well below this, while it
# leaves ample headroom above the common default of 9. Verified crash-free and
# finite up to 35+ on images down to 64x64; raise further if a workflow needs it.
MAX_FILTER_LEVEL = 30

# ── Transforms ─────────────────────────────────────────────────


def anscombe_transform(data):
    """Anscombe transform to stabilize Poisson noise variance.

    Clamps ``data`` to non-negative *before* adding 3/8, matching
    ``ComplexWaveletFilter.anscombe_transform`` exactly. (Adding 3/8 first
    and clamping after differs only for ``data < -3/8`` — negative
    Fourier coordinates ``G*I`` at noisy pixels — but that difference is
    enough to perturb the filtered phasor by ~0.02, so the order matters.)
    """
    return 2 * np.sqrt(np.maximum(data, 0) + (3 / 8))


def reverse_anscombe_transform(y):
    """Inverse Anscombe transform (sixth-order rational approximation).

    Faithful to ``ComplexWaveletFilter.reverse_anscombe_transform``: no
    clamping of ``y`` and no flooring of the result. Small or non-positive
    reconstructed values therefore yield inf/NaN here, exactly as in the
    reference; :func:`denoise_phasor` sweeps them up with ``nan_to_num`` +
    clip during phasor recovery (mirroring the reference's
    ``process_files``). ``errstate`` only silences the divide/invalid
    warnings — it does not alter the produced values.
    """
    y = np.asarray(y, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (
            (y**2 / 4)
            + (np.sqrt(3 / 2) * (1 / y) / 4)
            - (11 / (8 * y**2))
            + (np.sqrt(5 / 2) * (1 / y**3) / 8)
            - (1 / (8 * y**4))
        )


# ── Noise estimation (vectorized) ─────────────────────────────


def calculate_median_values(transformed_data):
    """Calculate median absolute values of wavelet coefficients."""
    median_values = []
    for level in range(len(transformed_data.highpasses)):
        highpasses = transformed_data.highpasses[level]
        for band in range(highpasses.shape[2]):
            coeffs = highpasses[:, :, band]
            median_absolute = np.median(np.abs(coeffs))
            median_values.append(median_absolute)
    return np.mean(median_values)


def calculate_local_noise_variance(transformed_data, n_levels):
    """Calculate local noise variance using vectorized uniform filter.

    Replaces the nested Python loop with scipy.ndimage.uniform_filter
    for ~100x speedup. Mathematically equivalent: computes mean of
    |coeffs|^2 in a (2*ws+1) x (2*ws+1) window around each pixel.
    """
    sigma_n_squared_matrices = []
    window_size = 3 if n_levels > 10 else n_levels
    kernel = 2 * window_size + 1  # convert radius to diameter for uniform_filter

    for level in range(len(transformed_data.highpasses)):
        highpasses = transformed_data.highpasses[level]
        for band in range(highpasses.shape[2]):
            coeffs = highpasses[:, :, band]
            # uniform_filter computes the mean over the kernel — same as
            # the original nested loop: mean(|window|^2)
            abs_sq = np.abs(coeffs) ** 2
            snq = uniform_filter(abs_sq.real, size=kernel, mode="constant")
            sigma_n_squared_matrices.append((level, band, snq))

    return sigma_n_squared_matrices


# ── Inter-scale Wiener shrinkage (vectorized) ──────────────────


def compute_phi_prime(mandrill_t, sigma_g_squared, sigma_n_squared_matrices):
    """Vectorized inter-scale Wiener shrinkage.

    Replaces the nested Python loop with array operations.
    For each level and band:
    1. Compute |phi_l|^2 (current level magnitude squared)
    2. Upsample |phi_{l+1}|^2 from the coarser level (nearest-neighbor 2x)
    3. phi_squared_sum = |phi_l|^2 + |phi_{l+1}_upsampled|^2
    4. factor = max(0, 1 - local_term / sqrt(phi_squared_sum + local_term))
    5. phi_prime = factor * phi_l
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

            # |phi_l|^2
            phi_l_sq = np.abs(phi_l_b) ** 2

            # Upsample |phi_{l+1}|^2 to match phi_l dimensions
            # nearest-neighbor 2x upsampling (each pixel maps to 2x2 block)
            h_l, w_l = phi_l_b.shape
            h_next, w_next = phi_l_plus_1_b.shape
            phi_next_sq = np.abs(phi_l_plus_1_b) ** 2

            # Create upsampled version via index mapping
            y_idx = np.minimum(np.arange(h_l) // 2, h_next - 1)
            x_idx = np.minimum(np.arange(w_l) // 2, w_next - 1)
            phi_next_upsampled = phi_next_sq[np.ix_(y_idx, x_idx)]

            # phi_squared_sum = |phi_l|^2 + |phi_{l+1}|^2 (upsampled)
            phi_squared_sum = phi_l_sq + phi_next_upsampled

            # Handle sigma_n_squared size mismatch
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

            # Compute shrinkage factor (vectorized)
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

    Mirrors ``ComplexWaveletFilter.process_files``' per-channel filtering
    with vectorized numpy operations:
    Anscombe → DTCWT (``biort='Legall'``) → inter-scale Wiener shrinkage →
    inverse DTCWT → inverse Anscombe. The basis matters: with the
    reference Anscombe transforms in place, ``near_sym_a`` leaves a ~1e-3
    residual versus the reference output while ``Legall`` matches it to
    float precision.
    """
    import dtcwt

    # Pad to power-of-2 dimensions for DTCWT
    h, w = data.shape
    pad_h = _next_pow2(h) - h
    pad_w = _next_pow2(w) - w
    padded = np.pad(data, ((0, pad_h), (0, pad_w)), mode="reflect")

    # Anscombe transform
    transformed = anscombe_transform(padded)

    # Forward DTCWT — Legall/qshift_a to match ComplexWaveletFilter.py
    xfm = dtcwt.Transform2d(biort="Legall", qshift="qshift_a")
    coeffs = xfm.forward(transformed, nlevels=n_levels)

    # Noise estimation
    median_vals = calculate_median_values(coeffs)
    sigma_g_squared = median_vals / 0.6745

    # Local noise variance (vectorized with uniform_filter)
    sigma_n_squared = calculate_local_noise_variance(coeffs, n_levels)

    # Inter-scale Wiener shrinkage (vectorized)
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

    Uses the reference ComplexWaveletFilter's inter-scale Wiener
    shrinkage algorithm and phasor recovery, vectorized with numpy for
    fast execution on large stitched datasets.

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

    # Step 2-5: Filter each channel
    print("  Filtering Freal...")
    f_real_filtered = _filter_channel(f_real, filter_level)
    print("  Filtering Fimag...")
    f_imag_filtered = _filter_channel(f_imag, filter_level)
    print("  Filtering intensity...")
    intensity_filtered = _filter_channel(intensity, filter_level)

    # Step 6: Recover filtered phasor — faithful to
    # ComplexWaveletFilter.process_files. Raw-divide by the *filtered*
    # intensity (inf/NaN from a non-positive filtered intensity is swept
    # up by nan_to_num below, exactly as the reference does), then
    # threshold by the *unfiltered* intensity and clip to the phasor
    # display range [-0.1, 1.1].
    with np.errstate(divide="ignore", invalid="ignore"):
        g_filtered = f_real_filtered / intensity_filtered
        s_filtered = f_imag_filtered / intensity_filtered
    g_filtered = np.nan_to_num(g_filtered)
    s_filtered = np.nan_to_num(s_filtered)
    thr = intensity > 0
    g_filtered = np.clip(g_filtered * thr, -0.1, 1.1)
    s_filtered = np.clip(s_filtered * thr, -0.1, 1.1)

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
