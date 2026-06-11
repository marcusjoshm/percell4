"""Whole-frame adaptive local-clipping helpers + auto-window estimation.

Pure domain (``numpy`` / ``scipy`` / ``skimage`` only — no Qt, napari, h5py, or
store). These back the interactive **Adaptive Local Clipping** GUI module:

* :func:`detect_adaptive_whole_frame` runs the validated ``adaptive`` detector
  over a whole frame by handing :func:`detect_two_pass` a single full-frame
  "group" (an all-``True`` mask) — same computation that produced the gallery
  masks, just without per-cell-group isolation.
* :func:`otsu_first_pass` + :func:`estimate_adaptive_window` implement the
  auto-window calibration: the local window is sized to the granules in the
  image from the mean particle size of an Otsu first-pass mask.
* :func:`resolve_min_area_px` converts the GUI particle-size filter (px² or µm²)
  into an integer pixel-area threshold, mirroring
  :func:`percell4.workflows.phases._resolve_min_area_px`.

``settings`` is duck-typed (a :class:`~percell4.workflows.models.PunctaDetectorSettings`)
exactly as :func:`detect_two_pass` does, so the pure domain layer never imports
the workflows layer at runtime.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import numpy as np

from percell4.domain.measure.puncta_pipeline import (
    DEFAULT_SCALE_RANGE,
    compute_seeds,
    detect_two_pass,
)
from percell4.domain.measure.thresholding import apply_gaussian_smoothing
from percell4.domain.measure.window_finders import WINDOW_FINDERS

if TYPE_CHECKING:  # type-only; no runtime import of the workflows layer
    from percell4.workflows.models import PunctaDetectorSettings

# Clamp range and Otsu noise floor for the auto-window estimate. The
# ``otsu-mean`` window-finder (``window ~= FACTOR * mean granule diameter`` over
# the Otsu first-pass mask) is the *legacy baseline* — it under-estimates badly
# on black-background images (whole-frame Otsu over-segments, the mean is
# speck-dominated). The window-finder bake-off supersedes it; ``estimate_adaptive_window``
# is retained as that baseline. ``AUTO_WINDOW_MAX`` may need raising for matured
# granules (decided from the bake-off's oracle curve).
AUTO_WINDOW_FACTOR = 2.0
AUTO_WINDOW_MIN = 11
AUTO_WINDOW_MAX = 151
# Otsu-mask components smaller than this (px area) are treated as noise and
# excluded from the mean-diameter estimate.
AUTO_WINDOW_NOISE_FLOOR_PX = 3


def _make_odd(n: int) -> int:
    """Nearest odd integer >= ``n`` (forces the local window to be odd)."""
    return int(n) | 1


def detect_adaptive_whole_frame(
    image: np.ndarray,
    gaussian_sigma: float | None,
    settings: PunctaDetectorSettings,
) -> np.ndarray:
    """Run the ``adaptive`` detector over the whole frame (one sigma).

    Smooths ``image`` (``gaussian_sigma``), then runs :func:`detect_two_pass`
    with a single full-frame group (all-``True`` mask). Returns the detector's
    ``{0, 1}`` ``uint8`` mask; the size filter is applied inside
    :func:`detect_two_pass` via ``settings.min_spot_px``.
    """
    img = np.asarray(image, dtype=np.float32)
    smoothed = apply_gaussian_smoothing(img, gaussian_sigma)
    group = np.ones(smoothed.shape, dtype=bool)
    return detect_two_pass(smoothed, group, settings)


def _with_window(settings: PunctaDetectorSettings, window_px: int) -> PunctaDetectorSettings:
    """Copy of ``settings`` with ``detector_params['window_px']`` set to ``window_px``.

    Uses :func:`dataclasses.replace` so this pure-domain module never imports
    :class:`PunctaDetectorSettings` at runtime (it is duck-typed); the dataclass
    ``__post_init__`` re-normalizes ``detector_params`` to its canonical tuple.
    """
    params = dict(settings.detector_params)
    params["window_px"] = int(window_px)
    return dataclasses.replace(settings, detector_params=params)


def auto_window(
    image: np.ndarray,
    gaussian_sigma: float | None,
    settings: PunctaDetectorSettings,
    method: str,
    *,
    cp_mask: np.ndarray | None = None,
    pixel_size_um: float | None = None,
    lo: int = AUTO_WINDOW_MIN,
    hi: int = AUTO_WINDOW_MAX,
    params: dict | None = None,
) -> int:
    """Estimate the adaptive-clipping window via the named window-finder.

    Smooths ``image`` (``gaussian_sigma``), runs ``WINDOW_FINDERS[method]`` on
    the smoothed image, then applies the odd/clamped contract — this is the
    **only** place the result is forced odd and clamped to ``[lo, hi]`` (the
    ``round -> clamp -> make_odd`` order matches the legacy
    :func:`estimate_adaptive_window`). Outcome-driven finders receive an injected
    ``detect_at_window(w) -> mask`` closure that runs the production
    :func:`detect_two_pass` at a candidate window, reusing a cached, window-
    independent pass-1 ``seeds`` and the round's fixed ``k`` / background
    estimator from ``settings``. The closure is built lazily, so size/scale/
    frequency finders that never call it pay no pass-1 cost.
    """
    smoothed = apply_gaussian_smoothing(np.asarray(image, dtype=np.float32), gaussian_sigma)
    group = np.ones(smoothed.shape, dtype=bool)
    scale_range = settings.spot_scale_prior or DEFAULT_SCALE_RANGE

    seeds_cache: dict[str, object] = {}

    def detect_at_window(window_px: int) -> np.ndarray:
        if "seeds" not in seeds_cache:
            seeds_cache["seeds"] = compute_seeds(smoothed, group, settings, scale_range)
        return detect_two_pass(
            smoothed, group, _with_window(settings, window_px), seeds=seeds_cache["seeds"]
        )

    raw = WINDOW_FINDERS[method](
        smoothed,
        dict(params or {}),
        cp_mask=cp_mask,
        pixel_size_um=pixel_size_um,
        detect_at_window=detect_at_window,
    )
    window = max(int(lo), min(int(hi), int(round(float(raw)))))
    return _make_odd(window)


def otsu_first_pass(smoothed: np.ndarray) -> np.ndarray:
    """Boolean whole-frame Otsu mask of an already-smoothed image.

    Guards the degenerate constant/empty case (``threshold_otsu`` raises on a
    single intensity level): returns an all-``False`` mask instead.
    """
    from skimage.filters import threshold_otsu as sk_threshold_otsu

    sm = np.asarray(smoothed, dtype=np.float32)
    finite = sm[np.isfinite(sm)]
    if finite.size == 0 or float(finite.min()) == float(finite.max()):
        return np.zeros(sm.shape, dtype=bool)
    thr = float(sk_threshold_otsu(finite))
    return sm > thr


def estimate_adaptive_window(
    otsu_mask: np.ndarray,
    *,
    factor: float = AUTO_WINDOW_FACTOR,
    lo: int = AUTO_WINDOW_MIN,
    hi: int = AUTO_WINDOW_MAX,
    noise_floor_px: int = AUTO_WINDOW_NOISE_FLOOR_PX,
) -> int:
    """Estimate the adaptive window from the mean granule size of an Otsu mask.

    ``window = clamp(make_odd(round(factor * mean_equiv_diameter)), lo, hi)``,
    where the mean equivalent diameter (``2*sqrt(area/pi)``) is computed over the
    Otsu mask's connected components with area ``>= noise_floor_px``. An empty
    mask (or one with only sub-floor specks) returns ``make_odd(lo)``.
    """
    from skimage import measure

    mask = np.asarray(otsu_mask) > 0
    if not mask.any():
        return _make_odd(lo)
    labels = measure.label(mask)
    areas = np.array([p.area for p in measure.regionprops(labels)], dtype=float)
    areas = areas[areas >= noise_floor_px]
    if areas.size == 0:
        return _make_odd(lo)
    diameters = 2.0 * np.sqrt(areas / np.pi)
    window = int(round(factor * float(diameters.mean())))
    window = max(lo, min(hi, window))
    return _make_odd(window)


def resolve_min_area_px(value: float, unit: str, pixel_size_um: float | None) -> int:
    """Convert a particle-size filter value+unit into an integer pixel area.

    ``unit`` is ``"px"`` (area in pixels) or ``"um2"`` (area in µm²). The µm²
    option requires a positive ``pixel_size_um`` or raises :class:`ValueError`
    (no silent default — mirrors the workflow phase behavior).
    """
    v = float(value)
    if unit == "px":
        return int(round(v))
    if unit == "um2":
        if not pixel_size_um or float(pixel_size_um) <= 0:
            raise ValueError(
                "µm² particle-size filter requires a known pixel size; switch "
                "the unit to px² or re-import the dataset with TIFF resolution "
                "metadata."
            )
        return int(round(v / (float(pixel_size_um) ** 2)))
    raise ValueError(f"unknown size-filter unit: {unit!r}")
