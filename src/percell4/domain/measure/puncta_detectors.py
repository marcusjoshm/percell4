"""Detector registry for headless puncta thresholding (plan U3).

One of the two orthogonal axes of the puncta detector (the other is
:mod:`percell4.domain.measure.bg_estimators`). Every detector turns a
background-corrected *residual* image into a binary spot mask. The registry
mirrors the flat-``dict`` *idiom* of
:data:`percell4.domain.measure.thresholding.THRESHOLD_METHODS`, but **not** its
``(mask, value)`` 2-tuple return — detectors return only a mask.

Uniform signature for every entry of :data:`DETECTORS`::

    detector(residual: np.ndarray,
             group_mask: np.ndarray,
             sigma: float | None,
             params: dict) -> np.ndarray   # uint8, values strictly in {0, 1}

Contract obeyed by every detector
---------------------------------
* **Per-group isolation is the caller's job.** The ``residual`` handed in is
  already isolated to one intensity group: out-of-group pixels are ``NaN`` (on
  a tight bbox crop). This is what makes ``blob_log``/``blob_dog``
  ``threshold_rel`` and the LoG response normalize *within* the group rather
  than across the whole field — the cross-group class-imbalance fix the
  per-group design exists for.
* **NaN safety.** scikit-image morphology/convolution ops are NOT NaN-safe and
  silently corrupt every pixel whose footprint touches a ``NaN``. Detectors
  therefore (a) fill ``NaN`` before any morphology/convolution (the residual is
  background-subtracted, so ``0`` is the natural fill), then (b) restrict the
  final mask to ``group_mask & np.isfinite(residual)``.
* **Output is ``{0, 1}`` ``uint8`` — NEVER 255.** A 255-valued mask breaks
  napari's ``DirectLabelColormap`` downstream.
* **Determinism.** No RNG anywhere; identical inputs yield bit-identical masks.
* **Never raise on degenerate input.** A constant, signal-free, or empty-group
  residual returns an all-zero mask, never an exception (the ``atrous-wavelet``
  stub is the sole exception — it is never selected as a default).

Pure: ``numpy`` / ``scipy`` / ``skimage`` only — no Qt, napari, h5py, or store.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
from skimage.draw import disk as draw_disk
from skimage.feature import blob_dog, blob_log
from skimage.filters import threshold_otsu as _sk_threshold_otsu
from skimage.morphology import disk, h_maxima, white_tophat

from percell4.domain.image.gaussian import nan_safe_gaussian_filter
from percell4.domain.measure.atrous import atrous_wavelet
from percell4.domain.measure.puncta_names import DETECTOR_NAMES

__all__ = ["DETECTORS", "DETECTOR_NAMES"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _finite_in_group(residual: np.ndarray, group_mask: np.ndarray) -> np.ndarray:
    """Boolean mask of pixels that are both in-group and finite."""
    return group_mask & np.isfinite(residual)


def _zero_mask(residual: np.ndarray) -> np.ndarray:
    """All-zero ``{0, 1}`` uint8 mask matching ``residual`` shape."""
    return np.zeros(residual.shape, dtype=np.uint8)


def _restrict(mask: np.ndarray, residual: np.ndarray, group_mask: np.ndarray) -> np.ndarray:
    """Restrict ``mask`` to in-group finite pixels; return ``{0, 1}`` uint8.

    The single choke point that enforces the dtype/value/isolation invariants
    for every detector: the result is ``1`` only where the detector fired AND
    the pixel is inside the group AND the residual is finite (i.e. not a caller
    NaN-isolation pixel).
    """
    out = (np.asarray(mask) > 0) & _finite_in_group(residual, group_mask)
    return out.astype(np.uint8)


def _fill_nan(residual: np.ndarray, fill: float = 0.0) -> np.ndarray:
    """Copy of ``residual`` with non-finite pixels replaced by ``fill``.

    The residual is background-subtracted, so ``0`` is the natural neutral fill
    for morphology/convolution; the final mask is restricted back to finite
    in-group pixels afterwards (fill-then-restrict).
    """
    filled = np.array(residual, dtype=float, copy=True)
    filled[~np.isfinite(filled)] = fill
    return filled


def _robust_sigma(residual: np.ndarray, group_mask: np.ndarray) -> float:
    """Robust in-group noise scale: ``1.4826 * MAD`` of finite group residual.

    Used as a fallback when the background estimator supplied no ``sigma``.
    Returns ``0.0`` for an empty/constant group (callers guard on that).
    """
    vals = residual[_finite_in_group(residual, group_mask)]
    if vals.size == 0:
        return 0.0
    med = float(np.median(vals))
    mad = float(np.median(np.abs(vals - med)))
    return 1.4826 * mad


def _paint_blobs(blobs: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Paint ``(y, x, sigma)`` blobs to filled disks (radius ``ceil(sigma*sqrt2)``).

    Each blob's radius is ``max(1, ceil(sigma * sqrt(2)))`` — the standard
    LoG/DoG blob-radius relation — and the disk is clipped to the image bounds
    via ``skimage.draw.disk(..., shape=shape)``. Returns a boolean mask.
    """
    painted = np.zeros(shape, dtype=bool)
    for y, x, blob_sigma in blobs:
        radius = max(1, int(math.ceil(float(blob_sigma) * math.sqrt(2.0))))
        rr, cc = draw_disk((float(y), float(x)), radius, shape=shape)
        painted[rr, cc] = True
    return painted


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def _otsu(
    residual: np.ndarray, group_mask: np.ndarray, sigma: float | None, params: dict
) -> np.ndarray:
    """Baseline global Otsu on the finite in-group residual.

    Kept for regression comparison against the multiscale default — Otsu over a
    whole group is the very behavior the puncta work exists to replace, so it
    is expected to miss dim foci. Guards the constant/empty case (a single
    intensity level makes ``threshold_otsu`` raise): returns all-zero instead.
    """
    sel = _finite_in_group(residual, group_mask)
    vals = residual[sel]
    if vals.size == 0 or float(vals.min()) == float(vals.max()):
        return _zero_mask(residual)
    thr = float(_sk_threshold_otsu(vals))
    mask = sel & (residual > thr)
    return _restrict(mask, residual, group_mask)


def _bg_k_sigma(
    residual: np.ndarray, group_mask: np.ndarray, sigma: float | None, params: dict
) -> np.ndarray:
    """Threshold the residual at ``k * sigma`` (``k`` default 2.5).

    ``sigma`` comes from the :class:`BackgroundEstimate`; when it is ``None``
    (the estimator had no natural noise scale) fall back to a robust in-group
    ``1.4826 * MAD`` sigma.
    """
    k = float(params.get("k", 2.5))
    if sigma is None:
        sigma = _robust_sigma(residual, group_mask)
    if sigma <= 0:
        return _zero_mask(residual)
    mask = _finite_in_group(residual, group_mask) & (residual > k * float(sigma))
    return _restrict(mask, residual, group_mask)


def _white_tophat(
    residual: np.ndarray, group_mask: np.ndarray, sigma: float | None, params: dict
) -> np.ndarray:
    """White top-hat (``disk(r)``) then threshold at ``k * MAD`` of the response.

    ``r`` (default 5) is the structuring-element radius; ``k`` (default 2.5)
    scales the robust MAD of the in-group top-hat response. The residual is
    NaN-filled with ``0`` before the morphology (white top-hat is not
    NaN-safe), and the mask is restricted to finite in-group pixels after.
    """
    r = int(params.get("r", 5))
    k = float(params.get("k", 2.5))

    filled = _fill_nan(residual, fill=0.0)
    th = white_tophat(filled, disk(r))

    sel = _finite_in_group(residual, group_mask)
    th_vals = th[sel]
    if th_vals.size == 0:
        return _zero_mask(residual)
    med = float(np.median(th_vals))
    mad = float(np.median(np.abs(th_vals - med)))
    if mad > 0:
        thr = k * 1.4826 * mad
    else:
        # Degenerate-MAD case: on a (near-)noise-free residual the top-hat
        # response is mostly exact zeros, so the robust scale collapses to 0.
        # Any strictly-positive response is then real bump signal — threshold
        # just above the median rather than returning an empty mask, so a clean
        # bright spot is not silently dropped.
        thr = med
    mask = sel & (th > thr)
    return _restrict(mask, residual, group_mask)


def _blob_detector(
    residual: np.ndarray,
    group_mask: np.ndarray,
    params: dict,
    *,
    backend: Callable,
) -> np.ndarray:
    """Shared LoG/DoG body: NaN-fill, run ``backend``, paint blobs to disks.

    ``min_sigma``/``max_sigma`` come from ``params`` (the pipeline forwards the
    calibrated scale range; defaults 1.0 / 4.0). ``threshold_rel`` is the recall
    knob (default 0.1) and ``exclude_border=False`` keeps foci near the bbox
    crop edge. ``num_sigma`` (LoG only) defaults to 5.
    """
    min_sigma = float(params.get("min_sigma", 1.0))
    max_sigma = float(params.get("max_sigma", 4.0))
    threshold_rel = float(params.get("threshold_rel", 0.1))

    filled = _fill_nan(residual, fill=0.0)

    # A constant filled image has no blobs and would make threshold_rel divide
    # by a zero dynamic range; short-circuit to all-zero.
    if float(filled.min()) == float(filled.max()):
        return _zero_mask(residual)

    if backend is blob_log:
        num_sigma = int(params.get("num_sigma", 5))
        blobs = blob_log(
            filled,
            min_sigma=min_sigma,
            max_sigma=max_sigma,
            num_sigma=num_sigma,
            threshold_rel=threshold_rel,
            exclude_border=False,
        )
    else:  # blob_dog
        blobs = blob_dog(
            filled,
            min_sigma=min_sigma,
            max_sigma=max_sigma,
            threshold_rel=threshold_rel,
            exclude_border=False,
        )

    if blobs.shape[0] == 0:
        return _zero_mask(residual)

    painted = _paint_blobs(blobs, residual.shape)
    return _restrict(painted, residual, group_mask)


def _log(
    residual: np.ndarray, group_mask: np.ndarray, sigma: float | None, params: dict
) -> np.ndarray:
    """Multiscale Laplacian-of-Gaussian blob detector (the default detector).

    Backed by :func:`skimage.feature.blob_log`. Each returned ``(y, x, sigma)``
    blob is painted to a filled disk of radius ``ceil(sigma * sqrt(2))``.
    """
    return _blob_detector(residual, group_mask, params, backend=blob_log)


def _dog(
    residual: np.ndarray, group_mask: np.ndarray, sigma: float | None, params: dict
) -> np.ndarray:
    """Multiscale Difference-of-Gaussian blob detector.

    Backed by :func:`skimage.feature.blob_dog` (a faster LoG approximation);
    same blob-to-disk painting as :func:`_log`.
    """
    return _blob_detector(residual, group_mask, params, backend=blob_dog)


def _h_maxima(
    residual: np.ndarray, group_mask: np.ndarray, sigma: float | None, params: dict
) -> np.ndarray:
    """``h_maxima`` regional maxima on a LoG-like prefiltered residual.

    The residual is NaN-filled with ``0`` and prefiltered with a NaN-safe
    Gaussian (``prefilter_sigma`` default 1.0) to suppress single-pixel noise
    before the morphological reconstruction; ``h = k * sigma`` (``k`` default
    2.5, ``sigma`` falling back to a robust in-group MAD when ``None``). The
    resulting regional-maxima mask is restricted to in-group finite pixels.
    """
    k = float(params.get("k", 2.5))
    prefilter_sigma = float(params.get("prefilter_sigma", 1.0))
    if sigma is None:
        sigma = _robust_sigma(residual, group_mask)
    if sigma <= 0:
        return _zero_mask(residual)

    filled = _fill_nan(residual, fill=0.0)
    # nan_safe_gaussian_filter on a NaN-filled image is the plain Gaussian, but
    # routing through it keeps the NaN-safe discipline uniform across detectors
    # and tolerates a residual that still carries NaN.
    prefiltered = np.asarray(
        nan_safe_gaussian_filter(filled, sigma=prefilter_sigma), dtype=float
    )
    prefiltered = _fill_nan(prefiltered, fill=0.0)

    h = k * float(sigma)
    if h <= 0:
        return _zero_mask(residual)
    if float(prefiltered.min()) == float(prefiltered.max()):
        return _zero_mask(residual)

    maxima = h_maxima(prefiltered, h)
    return _restrict(maxima, residual, group_mask)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


DETECTORS: dict[str, Callable[..., np.ndarray]] = {
    "otsu": _otsu,
    "bg-k-sigma": _bg_k_sigma,
    "white-tophat": _white_tophat,
    "log": _log,
    "dog": _dog,
    "h-maxima": _h_maxima,
    "atrous-wavelet": atrous_wavelet,
}

# Drift guard: the registry keys are exactly the single-source-of-truth tuple
# (which includes the atrous-wavelet stub registered above).
assert set(DETECTORS) == set(DETECTOR_NAMES), (
    "DETECTORS keys drifted from DETECTOR_NAMES: "
    f"{set(DETECTORS) ^ set(DETECTOR_NAMES)}"
)
