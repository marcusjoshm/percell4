"""Tests for phasor computation."""

from __future__ import annotations

import numpy as np
import pytest

from percell4.flim.phasor import (
    compute_phasor,
    measure_phasor_per_cell,
    phasor_roi_to_mask,
    phasor_to_lifetime,
)


def _make_single_exponential(tau_ns: float, n_bins: int = 256, freq_mhz: float = 80.0):
    """Create a synthetic single-exponential decay at known lifetime.

    A single exponential with lifetime tau at harmonic 1 should produce
    a phasor point on the universal semicircle at:
        G = 1 / (1 + (omega*tau)^2)
        S = omega*tau / (1 + (omega*tau)^2)
    """
    t = np.arange(n_bins, dtype=np.float64)
    period_bins = n_bins  # one full period
    decay = np.exp(-t / (tau_ns * freq_mhz * n_bins / 1000.0))
    return decay


def test_phasor_single_pixel():
    """Single pixel with known decay → G, S on semicircle."""
    n_bins = 256
    t = np.arange(n_bins, dtype=np.float64)
    # Single exponential decay
    tau_bins = 30.0  # decay constant in bin units
    decay = np.exp(-t / tau_bins) * 1000  # scale up for photon counts
    decay_stack = decay.reshape(1, 1, n_bins).astype(np.uint16)

    g, s = compute_phasor(decay_stack, harmonic=1)

    assert g.shape == (1, 1)
    assert s.shape == (1, 1)
    assert g.dtype == np.float32

    # Should be on or near the universal semicircle
    g_val = float(g[0, 0])
    s_val = float(s[0, 0])

    # Verify: (g - 0.5)^2 + s^2 ≈ 0.25 (semicircle equation)
    dist_sq = (g_val - 0.5) ** 2 + s_val ** 2
    assert abs(dist_sq - 0.25) < 0.05, f"Point ({g_val:.3f}, {s_val:.3f}) not on semicircle"


def test_phasor_zero_photon_pixels():
    """Zero-photon pixels should be NaN."""
    decay_stack = np.zeros((5, 5, 64), dtype=np.uint16)
    # Only center pixel has counts
    decay_stack[2, 2, :] = 10

    g, s = compute_phasor(decay_stack)

    # Center pixel should be finite
    assert np.isfinite(g[2, 2])
    assert np.isfinite(s[2, 2])

    # Corner pixel (zero photons) should be NaN
    assert np.isnan(g[0, 0])
    assert np.isnan(s[0, 0])


def test_phasor_shape():
    """Output shape matches spatial dimensions of input."""
    decay = np.random.rand(10, 20, 128).astype(np.float32) * 100
    g, s = compute_phasor(decay)
    assert g.shape == (10, 20)
    assert s.shape == (10, 20)


def test_phasor_s_positive():
    """S should be positive for a standard exponential decay."""
    t = np.arange(128, dtype=np.float64)
    decay = (np.exp(-t / 20.0) * 500).astype(np.uint16)
    stack = decay.reshape(1, 1, 128)

    g, s = compute_phasor(stack)
    assert float(s[0, 0]) > 0, "S should be positive for standard decay"


# ── Lifetime ──────────────────────────────────────────────────


def test_lifetime_positive():
    """Lifetime should be positive for valid phasor values."""
    g = np.array([[0.5]], dtype=np.float32)
    s = np.array([[0.3]], dtype=np.float32)

    tau = phasor_to_lifetime(g, s, frequency_mhz=80.0)
    assert tau.shape == (1, 1)
    assert float(tau[0, 0]) > 0


def test_lifetime_nan_for_zero_g():
    """Lifetime should be NaN when G is zero (division by zero)."""
    g = np.array([[0.0]], dtype=np.float32)
    s = np.array([[0.3]], dtype=np.float32)

    tau = phasor_to_lifetime(g, s, frequency_mhz=80.0)
    assert np.isnan(tau[0, 0])


# ── ROI to mask ───────────────────────────────────────────────


def test_roi_to_mask_basic():
    """Ellipse ROI produces a boolean mask."""
    g = np.array([[0.5, 0.1], [0.9, 0.5]], dtype=np.float32)
    s = np.array([[0.3, 0.1], [0.1, 0.3]], dtype=np.float32)

    mask = phasor_roi_to_mask(g, s, center=(0.5, 0.3), radii=(0.2, 0.2))

    assert mask.dtype == bool
    assert mask.shape == (2, 2)
    assert mask[0, 0]  # (0.5, 0.3) is at center
    assert not mask[0, 1]  # (0.1, 0.1) is far from center


def test_roi_excludes_nan():
    """NaN pixels are excluded from the ROI mask."""
    g = np.array([[np.nan, 0.5]], dtype=np.float32)
    s = np.array([[0.3, 0.3]], dtype=np.float32)

    mask = phasor_roi_to_mask(g, s, center=(0.5, 0.3), radii=(0.5, 0.5))
    assert not mask[0, 0]  # NaN excluded
    assert mask[0, 1]  # valid pixel inside


def test_roi_zero_radius():
    """Zero radius produces all-False mask."""
    g = np.ones((3, 3), dtype=np.float32)
    s = np.ones((3, 3), dtype=np.float32)
    mask = phasor_roi_to_mask(g, s, center=(1.0, 1.0), radii=(0.0, 0.0))
    assert not mask.any()


# ── Per-cell phasor metrics ───────────────────────────────────


def test_per_cell_metrics():
    """Per-cell phasor metrics for 2 cells."""
    g = np.full((20, 20), 0.4, dtype=np.float32)
    s = np.full((20, 20), 0.3, dtype=np.float32)
    g[10:20, :] = 0.6
    s[10:20, :] = 0.2

    labels = np.zeros((20, 20), dtype=np.int32)
    labels[0:10, 0:10] = 1
    labels[10:20, 10:20] = 2

    result = measure_phasor_per_cell(g, s, labels)

    assert len(result["label"]) == 2
    assert abs(result["g_mean"][0] - 0.4) < 0.01
    assert abs(result["s_mean"][0] - 0.3) < 0.01
    assert abs(result["g_mean"][1] - 0.6) < 0.01
    assert result["n_valid_pixels"][0] == 100
    assert result["phasor_spread"][0] < 1e-5  # uniform values → near-zero spread


def test_per_cell_empty_labels():
    """Empty labels returns empty arrays."""
    g = np.ones((5, 5), dtype=np.float32)
    s = np.ones((5, 5), dtype=np.float32)
    labels = np.zeros((5, 5), dtype=np.int32)

    result = measure_phasor_per_cell(g, s, labels)
    assert len(result["label"]) == 0
