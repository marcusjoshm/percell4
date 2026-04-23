"""Siemens-star phasor phantom for FLIM wavelet filter tests.

Synthesises a ground-truth (G, S, intensity) triple with a radial
spoke pattern so tests can measure whether a filter actually reduces
noise without erasing high-spatial-frequency structure. Spoke count
increases toward the image center — mirroring BOE 2021 supp Fig. S2.

Not a full TCSPC simulator. Phasor noise is modelled directly as
additive Gaussian with scale ``1/√I_noisy``. This captures the
photon-limited regime well enough for a regression-style MSE test;
for reproducing the paper's MSE-vs-spatial-frequency curves we'd
upgrade to a TCSPC-based generator (Phase 3 deliverable).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class SpokePhantom:
    """Ground-truth + noisy phasor triples produced by
    :func:`generate_spoke_phantom`.

    Attributes
    ----------
    g_true, s_true : (H, W) float64
        Noise-free phasor coordinates.
    intensity_true : (H, W) float64
        Per-pixel expected photon counts.
    g_noisy, s_noisy : (H, W) float64
        Phasor coordinates with photon-limited Gaussian noise. Contain
        NaN at pixels where ``intensity_noisy`` rolled zero (matches the
        convention upstream ComputePhasor uses).
    intensity_noisy : (H, W) float64
        Poisson-sampled photon counts.
    """

    g_true: NDArray
    s_true: NDArray
    intensity_true: NDArray
    g_noisy: NDArray
    s_noisy: NDArray
    intensity_noisy: NDArray


def generate_spoke_phantom(
    *,
    shape: tuple[int, int] = (256, 256),
    n_spokes_edge: int = 8,
    n_spokes_center: int = 40,
    tau_fast_ns: float = 1.5,
    tau_slow_ns: float = 2.5,
    freq_mhz: float = 80.0,
    photons_per_pixel: float = 25.0,
    noise_scale: float = 0.35,
    seed: int = 0,
) -> SpokePhantom:
    """Generate a Siemens-star phasor ground truth + noisy observation.

    Parameters
    ----------
    shape : (H, W)
        Image size. Must be square for the spoke pattern to look clean.
    n_spokes_edge, n_spokes_center : int
        Spoke counts at the periphery and center. Interpolates linearly
        in-between, so spatial frequency rises toward the middle.
    tau_fast_ns, tau_slow_ns : float
        Two lifetimes assigned to the "light" and "dark" regions of the
        spoke mask.
    freq_mhz : float
        Laser repetition rate in MHz. Drives ω.
    photons_per_pixel : float
        Expected Poisson rate for ``intensity_true``.
    noise_scale : float
        Gaussian noise scale in G/S per pixel. The per-pixel noise is
        ``noise_scale / √max(I_noisy, 1)`` — qualitatively matches
        photon-limited phasor scatter without modelling TCSPC.
    seed : int
        RNG seed; output is deterministic under a fixed seed.
    """
    rng = np.random.default_rng(seed)
    h, w = shape
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    y, x = np.mgrid[0:h, 0:w].astype(np.float64)
    dx, dy = x - cx, y - cy
    r = np.hypot(dx, dy)
    r_max = min(cx, cy)
    r_norm = np.clip(r / r_max, 0.0, 1.0)
    theta = np.arctan2(dy, dx)

    # Spoke count rises toward the center (higher spatial frequency).
    spokes = n_spokes_edge + (n_spokes_center - n_spokes_edge) * (1.0 - r_norm)
    mask = np.cos(spokes * theta) > 0.0
    mask &= r_norm < 1.0  # discard the corners

    # Per-pixel lifetime.
    tau = np.where(mask, tau_fast_ns, tau_slow_ns)

    # Analytic single-exponential phasor:
    #   G(ω, τ) = 1 / (1 + (ωτ)²)
    #   S(ω, τ) = ωτ / (1 + (ωτ)²)
    omega_rad_per_ns = 2.0 * np.pi * freq_mhz * 1e-3
    wt = omega_rad_per_ns * tau
    g_true = 1.0 / (1.0 + wt ** 2)
    s_true = wt / (1.0 + wt ** 2)

    # Expected intensity is uniform (a deliberately simple model —
    # the point of this phantom is to stress the G/S channels).
    intensity_true = np.full_like(g_true, photons_per_pixel)
    intensity_noisy = rng.poisson(intensity_true).astype(np.float64)

    # Photon-limited Gaussian noise on G and S.
    safe_i = np.sqrt(np.maximum(intensity_noisy, 1.0))
    g_noisy = g_true + rng.normal(scale=noise_scale, size=g_true.shape) / safe_i
    s_noisy = s_true + rng.normal(scale=noise_scale, size=s_true.shape) / safe_i

    # Matches upstream ComputePhasor convention: zero-intensity pixels
    # yield NaN phasor coords (the filter's input sanitization must
    # handle this).
    zero = intensity_noisy == 0
    g_noisy = np.where(zero, np.nan, g_noisy)
    s_noisy = np.where(zero, np.nan, s_noisy)

    return SpokePhantom(
        g_true=g_true,
        s_true=s_true,
        intensity_true=intensity_true,
        g_noisy=g_noisy,
        s_noisy=s_noisy,
        intensity_noisy=intensity_noisy,
    )


def g_s_mse(g_filt: NDArray, s_filt: NDArray,
            g_true: NDArray, s_true: NDArray,
            intensity: NDArray) -> float:
    """Mean squared error of a filtered G/S map against ground truth,
    weighted by intensity (matches the paper's weighting where low-
    photon pixels are thresholded out)."""
    weight = intensity > 0
    if not weight.any():
        return float("inf")
    dg = (g_filt[weight] - g_true[weight]) ** 2
    ds = (s_filt[weight] - s_true[weight]) ** 2
    return float(np.mean(dg + ds))
