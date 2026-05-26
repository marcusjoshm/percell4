"""Phasor computation for FLIM data.

Direct cosine/sine transform (not FFT) — computes only the requested
harmonic, lower memory than full FFT. Supports in-memory arrays and
chunked HDF5 datasets for large images.

Also hosts the pure helpers backing GMM-based phasor segmentation:
``universal_circle_gs``, ``gmm_eigenstructure``,
``gmm_to_phasor_roi_geometry``, and ``gmm_fit_phasor``. Sklearn is lazy-
imported inside ``gmm_fit_phasor`` to mirror ``domain/measure/grouper.py``
and keep import time low.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

# Default cap on the number of pixels passed to the GMM fitter — sampling
# beyond this point doesn't measurably improve cluster recovery and the
# memory cost grows linearly. Exposed as a kwarg on ``gmm_fit_phasor`` so
# tests and future tuning can override it.
MAX_GMM_PIXELS = 100_000


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


def median_filter_gs(
    g_map: NDArray,
    s_map: NDArray,
    size: int = 3,
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """Apply a square spatial median filter to phasor G/S maps.

    Thin wrapper over ``scipy.ndimage.median_filter`` parameterized by the
    kernel side-length. At ``size=3`` the result is byte-identical to the
    fixed 3x3 median that ``ComputePhasor`` used to apply unconditionally —
    a ``size=3`` median therefore reproduces the legacy "unfiltered"
    (flimfret-equivalent) output.

    Parameters
    ----------
    g_map, s_map : (H, W) phasor coordinate maps. NaN (zero-photon) pixels
        are passed straight to scipy; NaN propagation matches scipy's
        default ``median_filter`` behavior (no NaN-aware normalization).
    size : odd kernel side-length in pixels (>= 3). The window is
        ``size x size``, i.e. ``size**2`` pixels feed each median.

    Returns
    -------
    (g_filtered, s_filtered) : each shape (H, W) float32.
    """
    if not isinstance(size, (int, np.integer)) or size < 3 or size % 2 == 0:
        raise ValueError(f"size must be an odd integer >= 3, got {size!r}")

    from scipy.ndimage import median_filter

    g_filtered = median_filter(g_map, size=int(size)).astype(np.float32)
    s_filtered = median_filter(s_map, size=int(size)).astype(np.float32)
    return g_filtered, s_filtered


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
    angle_rad: float = 0.0,
) -> NDArray[np.bool_]:
    """Convert a rotated ellipse ROI in phasor space to a spatial pixel mask.

    Parameters
    ----------
    g_map, s_map : (H, W) phasor coordinate maps
    center : (center_g, center_s) ellipse center
    radii : (radius_g, radius_s) semi-axes
    angle_rad : rotation angle in radians (counterclockwise)

    Returns
    -------
    Boolean mask (H, W) — True for pixels whose phasor falls inside the ellipse.
    NaN pixels are excluded (False).
    """
    cx, cy = center
    rx, ry = radii

    if rx <= 0 or ry <= 0:
        return np.zeros(g_map.shape, dtype=bool)

    # Shift to ellipse center
    dg = g_map - cx
    ds = s_map - cy

    if angle_rad != 0.0:
        # Rotate coordinates into the ellipse's principal axes
        cos_a = np.cos(-angle_rad)
        sin_a = np.sin(-angle_rad)
        dg_rot = dg * cos_a - ds * sin_a
        ds_rot = dg * sin_a + ds * cos_a
    else:
        dg_rot = dg
        ds_rot = ds

    inside = (dg_rot / rx) ** 2 + (ds_rot / ry) ** 2 <= 1.0
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


# ── GMM-based phasor segmentation ────────────────────────────────────


@dataclass
class GMMFitResult:
    """Output of ``gmm_fit_phasor`` — what the use case forwards to the GUI.

    ``means`` and ``covariances`` are sklearn's per-component arrays; the
    use case maps them through ``gmm_eigenstructure`` and
    ``gmm_to_phasor_roi_geometry`` before constructing ROI dataclasses.
    """

    means: NDArray[np.float64]
    covariances: NDArray[np.float64]
    chosen_n: int
    criterion_value: float | None
    sampled_pixels: int


def universal_circle_gs(
    harmonic: int, tau_ns: float, freq_mhz: float
) -> tuple[float, float]:
    """Closed-form (G, S) on the universal semicircle for a single lifetime.

    The reference scripts at ``ComplexWaveletFilter/CondensedPhaseGMM.py``
    used ``scipy.minimize`` with a circle constraint to find this point;
    the exact closed form is faster, deterministic, and has no
    convergence failure mode.

    Parameters
    ----------
    harmonic : Fourier harmonic number (1 = fundamental).
    tau_ns : target fluorescence lifetime in nanoseconds (>= 0).
    freq_mhz : laser repetition frequency in megahertz.

    Returns
    -------
    (G_c, S_c) on the universal semicircle.
    """
    if tau_ns < 0:
        raise ValueError(f"tau_ns must be >= 0, got {tau_ns!r}")

    # ω in rad/s × τ in s. freq_mhz × 1e6 → Hz; tau_ns × 1e-9 → s.
    omega_tau = 2.0 * np.pi * harmonic * (freq_mhz * 1e6) * (tau_ns * 1e-9)
    denom = 1.0 + omega_tau * omega_tau
    g_c = 1.0 / denom
    s_c = omega_tau / denom
    return float(g_c), float(s_c)


def gmm_eigenstructure(
    cov_matrix: NDArray[np.floating],
) -> tuple[float, float, float]:
    """Decompose a 2x2 covariance matrix into ROI-ready scalars.

    Returns ``(lambda_major, lambda_minor, principal_angle_rad)``:

    - ``lambda_major`` is the larger eigenvalue (variance along the major
      axis); ``lambda_minor`` the smaller. Both are clamped to a small
      positive floor so downstream radii never collapse to zero on
      singular / near-singular covariance.
    - ``principal_angle_rad`` is the angle of the major eigenvector,
      measured counter-clockwise from the +G axis.
    """
    eigvals, eigvecs = np.linalg.eigh(np.asarray(cov_matrix, dtype=np.float64))
    # eigh returns sorted ascending: [λ_minor, λ_major]; eigvecs columns match.
    lambda_minor = float(eigvals[0])
    lambda_major = float(eigvals[1])

    trace = float(np.trace(cov_matrix))
    floor = max(1e-6 * trace, 1e-9) if trace > 0 else 1e-9
    lambda_minor = max(lambda_minor, floor)
    lambda_major = max(lambda_major, floor)

    major_vec = eigvecs[:, 1]
    principal_angle_rad = float(np.arctan2(major_vec[1], major_vec[0]))
    return lambda_major, lambda_minor, principal_angle_rad


def gmm_to_phasor_roi_geometry(
    mean: tuple[float, float],
    lambda_major: float,
    lambda_minor: float,
    principal_angle_rad: float,
    stretch_parallel: float,
    stretch_perpendicular: float,
    shift_parallel: float,
    shift_perpendicular: float,
    shape: str,
) -> tuple[tuple[float, float], tuple[float, float], float]:
    """Compute (center, radii, angle_deg) for a GMM-derived ROI.

    Four data-determined coefficients drive ROI placement, two for
    translation and two for stretch — each independent along the major
    and minor eigenaxes of the cluster covariance:

    - ``shift_parallel`` translates along the major eigenvector by
      ``shift_parallel × √λ_major``.
    - ``shift_perpendicular`` translates along the minor eigenvector by
      ``shift_perpendicular × √λ_minor``.
    - ``stretch_parallel`` scales the major-axis radius
      (``stretch_parallel × √λ_major``).
    - ``stretch_perpendicular`` scales the minor-axis radius
      (``stretch_perpendicular × √λ_minor``).

    All four are measured relative to the cluster mean — there is no
    "drag-preserving anchor". GMM ROIs are non-draggable in the GUI;
    the spinboxes are the exclusive way to move/scale them.

    For ``shape="ellipse"`` the radii are
    ``(stretch_parallel × √λ_major, stretch_perpendicular × √λ_minor)``
    and the angle is the major-eigenvector angle. For ``shape="circle"``
    the radii degenerate to
    ``(stretch_perpendicular × √λ_minor, stretch_perpendicular × √λ_minor)``
    — a circle inscribed in the cluster's minor extent (matches
    ``Circular_ROI_lifetime.py:169``). The angle is ``0``.
    """
    if shape not in ("ellipse", "circle"):
        raise ValueError(f"shape must be 'ellipse' or 'circle', got {shape!r}")
    if stretch_parallel <= 0 or stretch_perpendicular <= 0:
        raise ValueError(
            f"stretch_parallel and stretch_perpendicular must be > 0, "
            f"got ({stretch_parallel!r}, {stretch_perpendicular!r})"
        )

    sqrt_major = float(np.sqrt(lambda_major))
    sqrt_minor = float(np.sqrt(lambda_minor))

    cos_a = float(np.cos(principal_angle_rad))
    sin_a = float(np.sin(principal_angle_rad))
    # Parallel direction = major eigenvector; perpendicular direction is
    # rotated 90° counter-clockwise (so a positive shift_perpendicular
    # moves the ROI to the "left" of the major axis when looking outward
    # from the cluster center).
    delta_g = (
        shift_parallel * sqrt_major * cos_a
        - shift_perpendicular * sqrt_minor * sin_a
    )
    delta_s = (
        shift_parallel * sqrt_major * sin_a
        + shift_perpendicular * sqrt_minor * cos_a
    )

    mean_g, mean_s = mean
    center = (float(mean_g) + delta_g, float(mean_s) + delta_s)

    if shape == "ellipse":
        radii = (stretch_parallel * sqrt_major, stretch_perpendicular * sqrt_minor)
        angle_deg = float(np.degrees(principal_angle_rad))
    else:  # circle
        radii = (
            stretch_perpendicular * sqrt_minor,
            stretch_perpendicular * sqrt_minor,
        )
        angle_deg = 0.0

    return center, radii, angle_deg


def single_component_fit_phasor(
    g: NDArray[np.floating],
    s: NDArray[np.floating],
    intensity: NDArray[np.floating],
) -> GMMFitResult:
    """Closed-form intensity-weighted mean and covariance.

    For n=1, EM is unnecessary — the maximum-likelihood Gaussian over
    weighted samples has the analytic form

        mu = sum(w_i x_i) / sum(w_i)
        Sigma = sum(w_i (x_i - mu)(x_i - mu)^T) / sum(w_i)

    Computing it directly over all valid pixels avoids both the EM cost
    and the ``replace=False, p=p`` sampling bias used in ``gmm_fit_phasor``.
    Returns a ``GMMFitResult`` shaped identically to a 1-component GMM fit
    so the use case can treat both paths uniformly.

    Falls back to uniform weighting when ``intensity.sum() == 0``.
    """
    g = np.asarray(g, dtype=np.float64)
    s = np.asarray(s, dtype=np.float64)
    intensity = np.asarray(intensity, dtype=np.float64)
    if g.shape != s.shape or g.shape != intensity.shape:
        raise ValueError("g, s, intensity must share shape")
    if g.size == 0:
        raise ValueError("Cannot fit single component on empty input")

    w = intensity if intensity.sum() > 0 else np.ones_like(intensity)
    w_sum = w.sum()
    mean_g = float((w * g).sum() / w_sum)
    mean_s = float((w * s).sum() / w_sum)

    dg = g - mean_g
    ds = s - mean_s
    cov_gg = float((w * dg * dg).sum() / w_sum)
    cov_ss = float((w * ds * ds).sum() / w_sum)
    cov_gs = float((w * dg * ds).sum() / w_sum)

    means = np.array([[mean_g, mean_s]], dtype=np.float64)
    covariances = np.array(
        [[[cov_gg, cov_gs], [cov_gs, cov_ss]]], dtype=np.float64
    )
    return GMMFitResult(
        means=means,
        covariances=covariances,
        chosen_n=1,
        criterion_value=None,
        sampled_pixels=int(g.size),
    )


def gmm_fit_phasor(
    g: NDArray[np.floating],
    s: NDArray[np.floating],
    intensity: NDArray[np.floating],
    n_components: int | None,
    criterion: str | None,
    n_min: int = 2,
    n_max: int = 4,
    max_pixels: int = MAX_GMM_PIXELS,
    random_seed: int = 0,
) -> GMMFitResult:
    """Fit a Gaussian mixture to (g, s) pixels with intensity weighting.

    When ``n_components`` is set, fits exactly that count. When it's
    ``None``, sweeps ``n_min..n_max`` and picks the lowest BIC or AIC per
    ``criterion``. Defaults reflect the FLIM rule "a single Gaussian over
    the whole filtered phasor is never useful as an ROI" (``n_min=2``)
    and the perf-tuned upper bound (``n_max=4``).

    Intensity weighting uses ``np.random.default_rng(seed).choice`` with
    ``p = intensity / intensity.sum()`` instead of the reference
    scripts' ``np.repeat`` — same proportional weighting, bounded
    memory. Subsamples to at most ``max_pixels``. When intensity sums
    to zero (constant or all-zero), falls back to uniform sampling.

    sklearn is lazy-imported inside this function (matches
    ``domain/measure/grouper.py``).
    """
    from sklearn.mixture import GaussianMixture

    if criterion is not None and criterion not in ("BIC", "AIC"):
        raise ValueError(f"criterion must be 'BIC', 'AIC', or None, got {criterion!r}")
    if n_components is not None and n_components < 1:
        raise ValueError(f"n_components must be >= 1, got {n_components!r}")

    g = np.asarray(g, dtype=np.float64)
    s = np.asarray(s, dtype=np.float64)
    intensity = np.asarray(intensity, dtype=np.float64)
    if g.shape != s.shape or g.shape != intensity.shape:
        raise ValueError("g, s, intensity must share shape")

    n_valid = int(g.size)
    floor_n = n_components if n_components is not None else n_min
    if n_valid < floor_n:
        raise ValueError(
            f"Not enough valid pixels for n={floor_n} clusters (have {n_valid})"
        )

    rng = np.random.default_rng(random_seed)
    sample_size = min(n_valid, max_pixels)

    if intensity.sum() > 0:
        p = intensity / intensity.sum()
        idx = rng.choice(n_valid, size=sample_size, replace=False, p=p)
    else:
        # Constant / zero intensity → uniform sampling.
        idx = rng.choice(n_valid, size=sample_size, replace=False)

    samples = np.column_stack([g[idx], s[idx]])

    def _fit_one(n: int) -> GaussianMixture:
        gmm = GaussianMixture(
            n_components=n,
            covariance_type="full",
            random_state=random_seed,
        )
        gmm.fit(samples)
        return gmm

    if n_components is not None:
        best = _fit_one(n_components)
        chosen_n = n_components
        criterion_value: float | None = None
    else:
        if criterion is None:
            raise ValueError("criterion required when n_components is None")
        best_gmm: GaussianMixture | None = None
        best_score = float("inf")
        chosen_n = n_min
        for n in range(n_min, n_max + 1):
            if n_valid < n:
                break
            gmm = _fit_one(n)
            score = gmm.bic(samples) if criterion == "BIC" else gmm.aic(samples)
            if score < best_score:
                best_score = score
                best_gmm = gmm
                chosen_n = n
        if best_gmm is None:
            raise ValueError(
                f"Could not fit any GMM in range n={n_min}..{n_max} "
                f"with {n_valid} valid pixels"
            )
        best = best_gmm
        criterion_value = float(best_score)

    return GMMFitResult(
        means=np.asarray(best.means_, dtype=np.float64),
        covariances=np.asarray(best.covariances_, dtype=np.float64),
        chosen_n=chosen_n,
        criterion_value=criterion_value,
        sampled_pixels=sample_size,
    )
