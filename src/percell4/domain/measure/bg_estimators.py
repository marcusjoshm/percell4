"""Background-estimator registry for headless puncta thresholding.

One of the two orthogonal axes of the puncta detector (the other is
:mod:`percell4.domain.measure.puncta_detectors`). Every estimator takes the
already-smoothed *float* channel image plus the boolean ``group_mask`` of the
intensity group it is correcting, and returns a typed
:class:`BackgroundEstimate` — the background-corrected *residual* image, an
optional noise scale ``sigma`` for a downstream ``k * sigma`` gate, and an
``is_empty`` flag that drives the pipeline's fallback ladder when the estimator
could not place any samples.

Uniform signature for every entry of :data:`BACKGROUND_ESTIMATORS`::

    estimator(image: np.ndarray,
              group_mask: np.ndarray,
              seeds,
              params: dict) -> BackgroundEstimate

``seeds`` is either ``None`` or the 4-tuple
``(label_mask, binary_mask, region_ids, props_by_id)`` produced by
:func:`percell4.domain.analysis._impl._shared.label_and_filter` on the pass-1
seed detection; only the donut estimators consult it. Estimators operate on
finite pixels only and never propagate NaN.

Sanctioned reuse edge: this module imports geometry/fit helpers from
``percell4.domain.analysis._impl`` (``label_and_filter``,
``region_and_donut_masks``, ``estimate_bg_threshold``). The reverse direction —
``domain/analysis`` importing ``domain/measure`` — is forbidden by an
import-linter contract in ``pyproject.toml`` to keep the edge one-way.

Pure: ``numpy`` / ``scipy`` / ``skimage`` only — no Qt, napari, h5py, or store.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from skimage.restoration import rolling_ball

from percell4.domain.analysis._impl._shared import region_and_donut_masks
from percell4.domain.analysis._impl.per_particle_donut import estimate_bg_threshold
from percell4.domain.measure.puncta_names import BG_ESTIMATOR_NAMES

__all__ = [
    "BackgroundEstimate",
    "BACKGROUND_ESTIMATORS",
    "BG_ESTIMATOR_NAMES",
]

# Minimum finite group pixels for the gaussian-peak fit to be meaningful.
_MIN_GAUSSIAN_PEAK_PX = 10


@dataclass(frozen=True)
class BackgroundEstimate:
    """Result of a background estimator.

    Attributes
    ----------
    residual:
        The background-corrected image (``smoothed - background``), same shape
        as the input ``image``. Out-of-group pixels are not touched here; the
        caller isolates the group before detection.
    sigma:
        Noise scale for a downstream ``k * sigma`` gate, or ``None`` when the
        estimator has no natural noise estimate.
    is_empty:
        ``True`` when the estimator could not place any samples — this drives
        the pipeline's fallback ladder (e.g. drop to ``gaussian-peak``).
    """

    residual: np.ndarray
    sigma: float | None
    is_empty: bool


def _finite_group_values(image: np.ndarray, group_mask: np.ndarray) -> np.ndarray:
    """Finite pixel values of ``image`` inside ``group_mask`` (flat array)."""
    sel = group_mask & np.isfinite(image)
    return image[sel]


def _zero_residual_like(image: np.ndarray) -> np.ndarray:
    """All-zero residual matching ``image`` shape/dtype (empty-estimate body)."""
    return np.zeros_like(image, dtype=float)


# ---------------------------------------------------------------------------
# Scalar / robust estimators
# ---------------------------------------------------------------------------


def _gaussian_peak(
    image: np.ndarray, group_mask: np.ndarray, seeds, params: dict
) -> BackgroundEstimate:
    """Robust background-peak estimate via ``estimate_bg_threshold``.

    Uses only the fitted ``mu`` and ``sigma`` — never the returned
    ``threshold``. ``k`` is applied exactly once, later, in the detector gate;
    forwarding the baked-in ``k_sigma`` threshold here would double-apply ``k``.
    """
    vals = _finite_group_values(image, group_mask)
    if vals.size < _MIN_GAUSSIAN_PEAK_PX:
        return BackgroundEstimate(_zero_residual_like(image), None, True)

    k_sigma = float(params.get("k_sigma", 2.5))
    _threshold, mu, sigma = estimate_bg_threshold(vals, k_sigma=k_sigma)
    residual = image - mu
    return BackgroundEstimate(residual, float(sigma), False)


def _mad(
    image: np.ndarray, group_mask: np.ndarray, seeds, params: dict
) -> BackgroundEstimate:
    """Median background, MAD-derived robust noise scale (1.4826 * MAD)."""
    vals = _finite_group_values(image, group_mask)
    if vals.size == 0:
        return BackgroundEstimate(_zero_residual_like(image), None, True)

    bg = float(np.median(vals))
    mad = float(np.median(np.abs(vals - bg)))
    sigma = 1.4826 * mad
    residual = image - bg
    return BackgroundEstimate(residual, sigma, False)


def _percentile(
    image: np.ndarray, group_mask: np.ndarray, seeds, params: dict
) -> BackgroundEstimate:
    """Background = the ``p``-th percentile of the group (default median)."""
    vals = _finite_group_values(image, group_mask)
    if vals.size == 0:
        return BackgroundEstimate(_zero_residual_like(image), None, True)

    p = float(params.get("p", 50.0))
    bg = float(np.percentile(vals, p))
    residual = image - bg
    return BackgroundEstimate(residual, None, False)


# ---------------------------------------------------------------------------
# Donut estimators (seed-driven)
# ---------------------------------------------------------------------------


def _donut_aggregate(
    image: np.ndarray,
    group_mask: np.ndarray,
    seeds,
    params: dict,
    aggregator: Callable[[np.ndarray], float],
) -> BackgroundEstimate:
    """Aggregate donut-ring pixel values across all pass-1 seed regions.

    DECLARED FORK of ``analyze_regions``' donut branch in
    ``per_particle_donut.py:226-229``: this operates on the FLOAT image (no
    ``int(np.round(...))`` rounding and no Cap-specific ``exclude_cap_zero``).
    Only the geometry helper ``region_and_donut_masks`` is reused byte-for-byte.

    The pass-1 seed binary is passed as the ``binary_mask`` argument so the
    rings exclude seed pixels (the helper subtracts ``binary_mask`` from the
    EDT band). When ``seeds`` is ``None``/empty or no ring pixel is finite, the
    estimate is empty (drives the pipeline fallback ladder).
    """
    if seeds is None:
        return BackgroundEstimate(_zero_residual_like(image), None, True)

    label_mask, binary_mask, region_ids, props_by_id = seeds
    if region_ids is None or len(region_ids) == 0:
        return BackgroundEstimate(_zero_residual_like(image), None, True)

    buffer_px = int(params.get("buffer_px", 4))
    donut_px = int(params.get("donut_px", 5))

    ring_values: list[np.ndarray] = []
    for region_id in region_ids:
        _region_mask, donut_mask = region_and_donut_masks(
            label_mask, binary_mask, int(region_id), props_by_id,
            buffer_px, donut_px,
        )
        ring = image[donut_mask]
        ring = ring[np.isfinite(ring)]
        if ring.size > 0:
            ring_values.append(ring)

    if len(ring_values) == 0:
        return BackgroundEstimate(_zero_residual_like(image), None, True)

    pooled = np.concatenate(ring_values)
    bg = float(aggregator(pooled))
    residual = image - bg
    return BackgroundEstimate(residual, None, False)


def _donut_median(
    image: np.ndarray, group_mask: np.ndarray, seeds, params: dict
) -> BackgroundEstimate:
    """Median of all pass-1 donut-ring pixels → scalar background."""
    return _donut_aggregate(image, group_mask, seeds, params, np.median)


def _donut_mean(
    image: np.ndarray, group_mask: np.ndarray, seeds, params: dict
) -> BackgroundEstimate:
    """Mean of all pass-1 donut-ring pixels → scalar background."""
    return _donut_aggregate(image, group_mask, seeds, params, np.mean)


# ---------------------------------------------------------------------------
# Surface estimators
# ---------------------------------------------------------------------------


def _rolling_ball(
    image: np.ndarray, group_mask: np.ndarray, seeds, params: dict
) -> BackgroundEstimate:
    """Rolling-ball background surface (skimage), NaN-filled before estimation.

    NaN poisons the morphological surface; fill NaN on a copy with the group
    background (median of finite group pixels, else 0), run ``rolling_ball``
    with ``nansafe=True``, then ``residual = image - surface`` (the original
    NaNs propagate back into the residual, which is the caller's isolation
    signal).
    """
    vals = _finite_group_values(image, group_mask)
    if vals.size == 0:
        return BackgroundEstimate(_zero_residual_like(image), None, True)

    fill_value = float(np.median(vals))
    filled = np.array(image, dtype=float, copy=True)
    filled[~np.isfinite(filled)] = fill_value

    radius = float(params.get("radius", 15))
    surface = rolling_ball(filled, radius=radius, nansafe=True)
    residual = image - surface
    return BackgroundEstimate(residual, None, False)


def _donut_surface(
    image: np.ndarray, group_mask: np.ndarray, seeds, params: dict
) -> BackgroundEstimate:
    """Smooth background surface fit through pass-1 donut-ring samples (STUB).

    Intended implementation (deferred by the plan): collect donut-ring pixel
    coordinates/values across all pass-1 seed regions, fit a smooth surface
    with ``scipy.interpolate.RBFInterpolator(kernel="thin_plate_spline",
    smoothing>0, neighbors=k)``, evaluate it over the group bbox to produce a
    per-pixel background surface, then ``residual = image - surface``.

    A conditioning gate guards the fit: the estimate degrades to
    ``is_empty=True`` not merely on too-few samples but when the RBF system is
    ill-conditioned or the ring samples do not spatially span the group
    (residual / condition-number / coverage checks), so the pipeline falls
    through to the robust ``gaussian-peak`` rung rather than extrapolating a
    garbage surface. Ships as a stub until the harness shows the
    ``gaussian-peak`` fallback rung is insufficient for a qualifying lock.
    """
    raise NotImplementedError(
        "donut-surface RBF background estimator is deferred (plan U2); the "
        "fallback ladder drops to the gaussian-peak rung while it is a stub."
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


BACKGROUND_ESTIMATORS: dict[str, Callable[..., BackgroundEstimate]] = {
    "donut-median": _donut_median,
    "donut-mean": _donut_mean,
    "gaussian-peak": _gaussian_peak,
    "percentile": _percentile,
    "mad": _mad,
    "rolling-ball": _rolling_ball,
    "donut-surface": _donut_surface,
}

# Drift guard: the registry keys are the single source of truth's name tuple.
assert set(BACKGROUND_ESTIMATORS) == set(BG_ESTIMATOR_NAMES), (
    "BACKGROUND_ESTIMATORS keys drifted from BG_ESTIMATOR_NAMES: "
    f"{set(BACKGROUND_ESTIMATORS) ^ set(BG_ESTIMATOR_NAMES)}"
)
