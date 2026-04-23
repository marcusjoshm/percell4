"""Siemens-star TCSPC phantom mirroring BOE 2021 supp Fig. S2.

Generates a bi-exponential FLIM dataset with a radial spoke pattern —
spoke count increases toward the image center so spatial frequency rises
along a radial axis. Emits both a single-frame (photon-starved) and
500-frame (ground-truth) TCSPC stack so benchmarks can measure MSE vs.
a realistic noise-free reference.

Hard-coded to the paper's parameters:

- 512×512 pixels, 256 time bins
- 80 MHz laser repetition → 12.5 ns laser period
- 3 µs dwell, 0.1 photons/pulse → ~24 photons/pixel/frame
- Two lifetimes: 1.5 ns (fast, "lit" spokes) and 2.5 ns (slow, "dark")

The only knob is ``seed`` (deterministic output for tests and for paper
reproduction). If future benchmarks need different parameters, add them
here rather than making :func:`generate_spoke_tcspc` configurable — the
whole point of this phantom is that it's a faithful reference.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


# ── Paper parameters (BOE 2021 supp Fig. S2) ─────────────────────────

SHAPE: tuple[int, int] = (512, 512)
N_BINS: int = 256
FREQ_MHZ: float = 80.0
DWELL_US: float = 3.0
PHOTONS_PER_PULSE: float = 0.1
TAU_FAST_NS: float = 1.5
TAU_SLOW_NS: float = 2.5
N_GROUND_TRUTH_FRAMES: int = 500
N_SPOKES_EDGE: int = 8
N_SPOKES_CENTER: int = 40

_T_LASER_NS: float = 1000.0 / FREQ_MHZ  # 12.5 ns
_N_PULSES_PER_FRAME: int = int(DWELL_US * FREQ_MHZ)  # 240
_MEAN_PHOTONS_PER_FRAME: float = PHOTONS_PER_PULSE * _N_PULSES_PER_FRAME
_OMEGA_RAD_PER_NS: float = 2.0 * np.pi * FREQ_MHZ * 1e-3


@dataclass(frozen=True)
class SpokePhantom:
    """A synthetic spoke-pattern FLIM dataset with known ground truth.

    Attributes
    ----------
    tcspc_single : (H, W, T) uint32
        Single-frame TCSPC histogram — the "photon-starved" test input.
    tcspc_reference : (H, W, T) uint32
        Many-frame accumulation — the high-SNR reference against which
        denoising is scored.
    g_true, s_true : (H, W) float64
        Analytic noise-free phasor coordinates per pixel. Useful as a
        secondary reference (no simulation noise, unlike
        ``tcspc_reference``).
    intensity_true : (H, W) float64
        Expected photon count per pixel for a single frame.
    tau_map : (H, W) float64
        Per-pixel lifetime (ns). 1.5 where the spoke mask is True,
        2.5 elsewhere.
    freq_mhz : float
        Laser repetition rate (mirror of :data:`FREQ_MHZ`).
    n_reference_frames : int
        Frames accumulated into ``tcspc_reference``.
    """

    tcspc_single: NDArray
    tcspc_reference: NDArray
    g_true: NDArray
    s_true: NDArray
    intensity_true: NDArray
    tau_map: NDArray
    freq_mhz: float
    n_reference_frames: int


def generate_spoke_tcspc(
    seed: int = 0,
    *,
    shape: tuple[int, int] = SHAPE,
    n_bins: int = N_BINS,
    n_reference_frames: int = N_GROUND_TRUTH_FRAMES,
) -> SpokePhantom:
    """Build the synthetic phantom.

    ``seed`` controls the RNG; output is deterministic under a fixed
    seed across NumPy versions. The optional ``shape``, ``n_bins``, and
    ``n_reference_frames`` kwargs exist only so perf tests can shrink
    the phantom — the default values are the paper's values and
    production benchmarks should use them unchanged.
    """
    rng = np.random.default_rng(seed)
    h, w = shape

    # ── Spoke mask (chirp: spokes count rises toward center) ──
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    y, x = np.mgrid[0:h, 0:w].astype(np.float64)
    dx, dy = x - cx, y - cy
    r = np.hypot(dx, dy)
    r_max = min(cx, cy)
    r_norm = np.clip(r / r_max, 0.0, 1.0)
    theta = np.arctan2(dy, dx)
    spokes = N_SPOKES_EDGE + (N_SPOKES_CENTER - N_SPOKES_EDGE) * (1.0 - r_norm)
    mask = (np.cos(spokes * theta) > 0.0) & (r_norm < 1.0)

    # ── Per-pixel lifetime + analytic phasor ground truth ──
    tau = np.where(mask, TAU_FAST_NS, TAU_SLOW_NS)
    wt = _OMEGA_RAD_PER_NS * tau
    g_true = 1.0 / (1.0 + wt ** 2)
    s_true = wt / (1.0 + wt ** 2)
    intensity_true = np.full((h, w), _MEAN_PHOTONS_PER_FRAME)

    # ── Per-pixel decay PMF (bin probabilities given τ) ──
    # Bin centers at t = (i + 0.5) · bin_width. Truncate at T_laser
    # (no IRF, no re-excitation — good enough for this benchmark).
    bin_width = _T_LASER_NS / n_bins
    t_bin = (np.arange(n_bins, dtype=np.float64) + 0.5) * bin_width
    pmf_fast = np.exp(-t_bin / TAU_FAST_NS)
    pmf_fast /= pmf_fast.sum()
    pmf_slow = np.exp(-t_bin / TAU_SLOW_NS)
    pmf_slow /= pmf_slow.sum()

    # Per-pixel PMF assembled by the mask.
    pmf_per_pixel = np.where(mask[..., None], pmf_fast, pmf_slow)  # (H, W, T)

    # ── Poisson-thinned TCSPC sampling ──
    # Total photons per pixel per frame is Poisson(λ_total) where
    # λ_total = mean_photons_per_frame. By Poisson thinning, bin counts
    # are independent Poisson(λ_total · pmf[bin]). This lets us sample
    # the whole (H, W, T) stack in one vectorised rng.poisson call
    # instead of iterating pixels.
    def _sample(n_frames: int) -> NDArray:
        lam = (_MEAN_PHOTONS_PER_FRAME * n_frames) * pmf_per_pixel
        return rng.poisson(lam).astype(np.uint32)

    tcspc_single = _sample(1)
    tcspc_reference = _sample(n_reference_frames)

    return SpokePhantom(
        tcspc_single=tcspc_single,
        tcspc_reference=tcspc_reference,
        g_true=g_true,
        s_true=s_true,
        intensity_true=intensity_true,
        tau_map=tau,
        freq_mhz=FREQ_MHZ,
        n_reference_frames=n_reference_frames,
    )
