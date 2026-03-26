"""Phasor computation for FLIM data.

Direct cosine/sine transform (not FFT) — computes only the requested
harmonic, lower memory than full FFT. Supports in-memory arrays and
chunked HDF5 datasets for large images.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def compute_phasor(
    decay_stack: NDArray,
    harmonic: int = 1,
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """Compute phasor G and S coordinates from TCSPC decay data.

    Uses normalized DFT: omega = 2π * harmonic / n_bins. This assumes
    the time bins span one full laser period, which is standard for
    Becker & Hickl TCSPC exports.

    Parameters
    ----------
    decay_stack : (H, W, T) array of photon counts per time bin
    harmonic : Fourier harmonic number (1 = fundamental)

    Returns
    -------
    (g_map, s_map) : each shape (H, W) float32.
        Zero-photon pixels are NaN.
    """
    n_bins = decay_stack.shape[-1]
    k = np.arange(n_bins, dtype=np.float64)

    # Normalized DFT omega: assumes n_bins spans one full laser period.
    # This is correct when bin_width = laser_period / n_bins, which is the
    # standard for Becker & Hickl TCSPC (e.g., 132 bins × 0.097 ns = 12.8 ns
    # = 1/78MHz). The frequency_mhz and bin_width_ns parameters are stored
    # for lifetime calculation but NOT needed for the phasor transform itself.
    omega = 2.0 * np.pi * harmonic / n_bins

    cos_vec = np.cos(omega * k)
    sin_vec = np.sin(omega * k)

    # Total photon counts per pixel
    dc = decay_stack.sum(axis=-1, dtype=np.float64)

    # Avoid division by zero
    dc_safe = np.where(dc > 0, dc, 1.0)

    g = np.einsum("...k,k->...", decay_stack.astype(np.float64), cos_vec) / dc_safe
    s = np.einsum("...k,k->...", decay_stack.astype(np.float64), sin_vec) / dc_safe

    # Mark zero-photon pixels as NaN
    zero_mask = dc == 0
    g[zero_mask] = np.nan
    s[zero_mask] = np.nan

    return g.astype(np.float32), s.astype(np.float32)


def compute_phasor_chunked(
    decay_dset,
    harmonic: int = 1,
    chunk_rows: int = 64,
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """Compute phasor from an HDF5 dataset in spatial row-chunks.

    Memory-bounded: only one chunk (~2MB at 64x64x256 uint16) is
    loaded at a time, plus the output G/S maps.

    Parameters
    ----------
    decay_dset : h5py.Dataset with shape (H, W, T)
    harmonic : Fourier harmonic number
    chunk_rows : number of rows to process at a time

    Returns
    -------
    (g_map, s_map) : each shape (H, W) float32.
    """
    h, w = decay_dset.shape[:2]
    n_bins = decay_dset.shape[-1]

    k = np.arange(n_bins, dtype=np.float64)
    omega = 2.0 * np.pi * harmonic / n_bins
    cos_vec = np.cos(omega * k)
    sin_vec = np.sin(omega * k)

    g_map = np.empty((h, w), dtype=np.float32)
    s_map = np.empty((h, w), dtype=np.float32)

    for row_start in range(0, h, chunk_rows):
        row_end = min(row_start + chunk_rows, h)
        chunk = decay_dset[row_start:row_end, :, :].astype(np.float64)

        dc = chunk.sum(axis=-1)
        dc_safe = np.where(dc > 0, dc, 1.0)

        g_chunk = np.einsum("...k,k->...", chunk, cos_vec) / dc_safe
        s_chunk = np.einsum("...k,k->...", chunk, sin_vec) / dc_safe

        zero_mask = dc == 0
        g_chunk[zero_mask] = np.nan
        s_chunk[zero_mask] = np.nan

        g_map[row_start:row_end, :] = g_chunk.astype(np.float32)
        s_map[row_start:row_end, :] = s_chunk.astype(np.float32)

    return g_map, s_map


def phasor_to_lifetime(
    g: NDArray,
    s: NDArray,
    frequency_mhz: float,
) -> NDArray[np.float32]:
    """Convert phasor coordinates to phase lifetime.

    tau_phi = s / (2 * pi * f * g)

    Parameters
    ----------
    g, s : phasor coordinate maps (H, W)
    frequency_mhz : laser repetition frequency in MHz

    Returns
    -------
    Lifetime map (H, W) in nanoseconds. NaN where g <= 0 or input is NaN.
    """
    omega = 2.0 * np.pi * frequency_mhz  # rad/us -> divide result to get ns
    with np.errstate(divide="ignore", invalid="ignore"):
        tau = s / (omega * g)
    # Convert from microseconds to nanoseconds
    tau = tau * 1000.0
    # Clamp unreasonable values
    tau = np.where((tau < 0) | (tau > 50.0) | np.isnan(tau), np.nan, tau)
    return tau.astype(np.float32)


def phasor_roi_to_mask(
    g_map: NDArray,
    s_map: NDArray,
    center: tuple[float, float],
    radii: tuple[float, float],
) -> NDArray[np.bool_]:
    """Convert an ellipse ROI in phasor space to a spatial pixel mask.

    Parameters
    ----------
    g_map, s_map : (H, W) phasor coordinate maps
    center : (center_g, center_s) ellipse center
    radii : (radius_g, radius_s) semi-axes

    Returns
    -------
    Boolean mask (H, W) — True for pixels whose phasor falls inside the ellipse.
    NaN pixels are excluded (False).
    """
    cx, cy = center
    rx, ry = radii

    if rx <= 0 or ry <= 0:
        return np.zeros(g_map.shape, dtype=bool)

    inside = ((g_map - cx) / rx) ** 2 + ((s_map - cy) / ry) ** 2 <= 1.0
    inside &= np.isfinite(g_map) & np.isfinite(s_map)
    return inside


def measure_phasor_per_cell(
    g_map: NDArray,
    s_map: NDArray,
    labels: NDArray[np.int32],
    intensity: NDArray | None = None,
) -> dict[str, NDArray]:
    """Compute per-cell phasor statistics.

    Parameters
    ----------
    g_map, s_map : (H, W) phasor coordinates
    labels : (H, W) cell label array
    intensity : optional (H, W) photon counts for intensity-weighted means

    Returns
    -------
    dict with arrays indexed by cell (excluding background):
        'label', 'g_mean', 's_mean', 'phasor_spread', 'n_valid_pixels'
    """
    from scipy.ndimage import find_objects

    cell_ids = np.unique(labels)
    cell_ids = cell_ids[cell_ids > 0]

    if len(cell_ids) == 0:
        return {
            "label": np.array([], dtype=np.int32),
            "g_mean": np.array([], dtype=np.float32),
            "s_mean": np.array([], dtype=np.float32),
            "phasor_spread": np.array([], dtype=np.float32),
            "n_valid_pixels": np.array([], dtype=np.int32),
        }

    slices = find_objects(labels)
    n = len(cell_ids)
    out_labels = np.empty(n, dtype=np.int32)
    out_g = np.empty(n, dtype=np.float32)
    out_s = np.empty(n, dtype=np.float32)
    out_spread = np.empty(n, dtype=np.float32)
    out_n = np.empty(n, dtype=np.int32)

    for i, cid in enumerate(cell_ids):
        sl = slices[cid - 1]
        if sl is None:
            out_labels[i] = cid
            out_g[i] = np.nan
            out_s[i] = np.nan
            out_spread[i] = np.nan
            out_n[i] = 0
            continue

        cell_mask = labels[sl] == cid
        g_cell = g_map[sl][cell_mask]
        s_cell = s_map[sl][cell_mask]
        valid = np.isfinite(g_cell) & np.isfinite(s_cell)

        n_valid = int(valid.sum())
        out_labels[i] = cid
        out_n[i] = n_valid

        if n_valid == 0:
            out_g[i] = np.nan
            out_s[i] = np.nan
            out_spread[i] = np.nan
            continue

        g_valid = g_cell[valid]
        s_valid = s_cell[valid]

        if intensity is not None:
            # Intensity-weighted mean
            w = intensity[sl][cell_mask][valid].astype(np.float64)
            w_sum = w.sum()
            if w_sum > 0:
                out_g[i] = float((g_valid * w).sum() / w_sum)
                out_s[i] = float((s_valid * w).sum() / w_sum)
            else:
                out_g[i] = float(np.nanmean(g_valid))
                out_s[i] = float(np.nanmean(s_valid))
        else:
            out_g[i] = float(np.nanmean(g_valid))
            out_s[i] = float(np.nanmean(s_valid))

        # Phasor spread: RMS distance from mean in phasor space
        var_g = float(np.nanvar(g_valid))
        var_s = float(np.nanvar(s_valid))
        out_spread[i] = float(np.sqrt(var_g + var_s))

    return {
        "label": out_labels,
        "g_mean": out_g,
        "s_mean": out_s,
        "phasor_spread": out_spread,
        "n_valid_pixels": out_n,
    }
