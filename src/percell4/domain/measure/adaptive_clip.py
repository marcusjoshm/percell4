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

from typing import TYPE_CHECKING

import numpy as np

from percell4.domain.measure.puncta_pipeline import detect_two_pass
from percell4.domain.measure.thresholding import apply_gaussian_smoothing

if TYPE_CHECKING:  # type-only; no runtime import of the workflows layer
    from percell4.workflows.models import PunctaDetectorSettings

# Auto-window calibration. window ~= FACTOR * mean granule diameter, where the
# mean equivalent diameter (2*sqrt(area/pi)) is taken from the Otsu first-pass
# mask. FACTOR is the single tunable constant — calibrate so small (As+Noco)
# granules land near window 15 and matured (WT 90min-wash) granules near 50.
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
