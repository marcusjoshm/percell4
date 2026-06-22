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
import math
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

# Eye-validated (2026-06-12, four datasets / two condensate types) ratio between
# the local-background window and the smallest particle to detect. A pixel only
# stands out from a Gaussian-blur local background once the blur scale (~window)
# is several times the particle, so window ~= 6 * d_min: stress granules
# (G3BP1) d_min ~= 0.40 um -> 21 px, P-bodies (DDX6) d_min ~= 0.14 um -> 7 px,
# both @ 0.120369 um/px. See the project_adaptive_clip_per_cell_params memory.
PARTICLE_WINDOW_FACTOR = 6.0
# Window never drops below this (px): below ~3 px the local-background blur
# starts subtracting the particle from itself (self-subtraction floor).
PARTICLE_WINDOW_MIN = 3


def _make_odd(n: int) -> int:
    """Nearest odd integer >= ``n`` (forces the local window to be odd)."""
    return int(n) | 1


def _filter_by_area(mask: np.ndarray, min_spot_px: int) -> np.ndarray:
    """Keep only connected components with area ``>= min_spot_px`` (boolean out).

    Vectorized via ``np.bincount`` + a per-label boolean lookup (``O(H*W)``),
    matching :func:`percell4.domain.measure.puncta_pipeline._size_filter`. A
    ``min_spot_px <= 1`` request is a no-op (every component survives), which is
    how the size filter auto-disables for sub-pixel particles.
    """
    m = np.asarray(mask) > 0
    if min_spot_px <= 1:
        return m
    from skimage import measure

    # connectivity=1 (face/4-connectivity) matches skimage.remove_small_objects,
    # the eye-validated size-filter path. The puncta-pipeline _size_filter uses
    # label's 8-connectivity default; here fidelity to the validated mask wins
    # (diagonal-touching specks must NOT merge into a kept component).
    lab = measure.label(m, connectivity=1)
    counts = np.bincount(lab.ravel())
    keep = counts >= min_spot_px
    keep[0] = False  # background is never a kept component
    return keep[lab]


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


def window_min_spot_for_particle(
    d_min_um: float,
    pixel_size_um: float,
    *,
    factor: float = PARTICLE_WINDOW_FACTOR,
) -> tuple[int, int]:
    """Derive the adaptive-clip ``(window_px, min_spot_px)`` from one physical knob.

    ``d_min_um`` is the diameter (µm) of the *smallest* particle to detect — the
    single parameter that distinguishes condensate types (stress granules vs the
    much smaller P-bodies). Both spatial outputs follow from it:

    * ``window_px = odd(round(factor * d_min / pixel))``, floored at
      :data:`PARTICLE_WINDOW_MIN` — the local-background scale, ~``factor`` (6)×
      the particle (a Gaussian-blur background must be several × the particle or
      it subtracts the particle from itself).
    * ``min_spot_px = round(area of one d_min particle) = round(pi*(d_min/2)^2 /
      pixel^2)`` — the size filter. It falls to ``1`` (i.e. **OFF**, every
      component kept) once ``d_min`` reaches the pixel/diffraction scale, so
      diffraction-limited P-bodies are never deleted by a size filter that can't
      tell a sub-pixel spot from a noise pixel anyway.

    Specifying the rule in microns (not pixels) is what lets it transfer across
    magnifications; ``pixel_size_um`` converts per image. Raises on a
    non-positive pixel size.
    """
    if pixel_size_um <= 0:
        raise ValueError(f"pixel_size_um must be > 0 µm/px, got {pixel_size_um}")
    if d_min_um <= 0:
        raise ValueError(f"d_min_um must be > 0 µm, got {d_min_um}")
    window_px = max(
        PARTICLE_WINDOW_MIN,
        _make_odd(round(float(factor) * float(d_min_um) / float(pixel_size_um))),
    )
    min_spot_px = max(
        1, int(round(math.pi * (float(d_min_um) / 2.0) ** 2 / float(pixel_size_um) ** 2))
    )
    return window_px, min_spot_px


def detect_adaptive_by_particle_size(
    image: np.ndarray,
    labels: np.ndarray,
    pixel_size_um: float,
    d_min_um: float,
    *,
    k: float = 1.0,
    presmooth_sigma_px: float = 1.0,
    factor: float = PARTICLE_WINDOW_FACTOR,
) -> np.ndarray:
    """Per-cell adaptive local clip driven by one physical knob, ``d_min_um``.

    The "just works" puncta detector: tell it the smallest particle you want
    (µm) and it thresholds every Cellpose cell against *its own* noise floor.
    ``d_min_um`` sets the local-background ``window`` and the size filter via
    :func:`window_min_spot_for_particle`; everything else is fixed —

    * **presmooth** ``sigma=1 px`` (noise suppression; a *fixed pixel* scale, not
      physical, since pixel/shot noise does not scale with magnification),
    * **background** = Gaussian local mean at ``sigma=(window-1)/6`` (the skimage
      ``threshold_local('gaussian')`` / ``adaptive``-detector convention),
    * **noise floor** = robust ``1.4826*MAD`` of the smoothed image, computed
      **per cell** — the per-cell σ is what lets one ``k`` hold across cells whose
      intensity scale varies many-fold (observed 3× within a field, 40× across
      datasets),
    * **k = 1**.

    The local background (hence ``diff = work - background``) is computed over the
    **whole frame once**; only σ is per-cell. Computing it per cell-crop instead
    would distort the background within a window of each cell's bounding box —
    exactly at the cell-edge granules. A pixel is foreground iff
    ``diff > k*σ_cell`` inside its cell; components smaller than ``min_spot_px``
    are dropped (a no-op for sub-pixel ``d_min``).

    Eye-validated (2026-06-12) across four datasets / two condensate types:
    stress granules (G3BP1) at ``d_min≈0.40 µm`` (window 21 px, size filter on)
    and P-bodies (DDX6) at ``d_min≈0.14 µm`` (window 7 px, size filter off).
    Returns a whole-frame ``{0, 1}`` ``uint8`` mask.
    """
    from scipy.ndimage import find_objects, gaussian_filter

    img = np.asarray(image, dtype=np.float32)
    lab = np.asarray(labels)
    window_px, min_spot_px = window_min_spot_for_particle(
        d_min_um, pixel_size_um, factor=factor
    )

    work = apply_gaussian_smoothing(img, presmooth_sigma_px)
    # Local background = Gaussian mean at the threshold_local('gaussian') sigma.
    # The constant global offset cancels in `diff`, so the per-cell comparison
    # reduces to image > local_background + k*sigma (see adaptive_local_clip.ijm).
    diff = work - gaussian_filter(work, (window_px - 1) / 6.0)

    out = np.zeros(img.shape, dtype=bool)
    for idx, sl in enumerate(find_objects(lab)):
        if sl is None:
            continue
        cell = lab[sl] == (idx + 1)
        if not cell.any():
            continue
        vals = work[sl][cell]
        med = float(np.median(vals))
        sigma = 1.4826 * float(np.median(np.abs(vals - med)))
        if not np.isfinite(sigma) or sigma <= 0.0:
            continue
        out[sl] |= (diff[sl] > float(k) * sigma) & cell

    return _filter_by_area(out, min_spot_px).astype(np.uint8)


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


@dataclasses.dataclass(frozen=True)
class OtsuSmallestReport:
    """Diagnostics from the Otsu first-pass that seeds the per-cell ``d_min``.

    All intensities are in the units of the **smoothed** working image (which is
    what the Otsu threshold is defined on, so ``mean_minus_threshold`` is a like
    comparison). The "threshold area" is the Otsu foreground (pixels ``> threshold``
    inside the selection), before the noise-floor size filter.
    """

    otsu_threshold: float          # Otsu threshold on the smoothed selected pixels
    smallest_diameter_px: float    # smallest surviving component's equiv. diameter
    d_min_um: float                # smallest_diameter_px * pixel_size_um
    n_components: int              # components surviving the noise floor
    area_mean: float               # mean intensity of the threshold area (sm > thr)
    area_max: float                # max intensity in the threshold area
    area_min: float                # min intensity in the threshold area
    scope: str                     # "in-cell" | "whole-frame"

    @property
    def mean_minus_threshold(self) -> float:
        """Mean intensity of the threshold area minus the Otsu threshold value."""
        return self.area_mean - self.otsu_threshold


def otsu_smallest_particle(
    image: np.ndarray,
    gaussian_sigma: float | None,
    pixel_size_um: float,
    *,
    cp_mask: np.ndarray | None = None,
    noise_floor_px: int = AUTO_WINDOW_NOISE_FLOOR_PX,
) -> OtsuSmallestReport | None:
    """Otsu first-pass measuring the smallest particle (+ diagnostics).

    Smooths ``image`` (``gaussian_sigma``), runs the Otsu first pass over the
    finite (and, when ``cp_mask`` is given, in-cell) pixels, labels the
    foreground, drops sub-``noise_floor_px`` specks, and reports the **minimum**
    equivalent diameter (``2*sqrt(area/pi)``) — in px and µm — alongside the Otsu
    threshold and the threshold-area intensity stats. This is the inverse of
    :func:`window_min_spot_for_particle`: instead of the user eyeballing the
    smallest particle, the Otsu pass measures it so the per-cell ``d_min`` knob
    (and hence the window + size filter it derives) can be auto-populated.

    Restricting to ``cp_mask`` (the union of Cellpose cells) is the robust path:
    whole-frame Otsu over-segments the black outside-cell region (the documented
    ``otsu-mean`` failure mode), which would let an out-of-cell noise blob set an
    absurdly small diameter. Out-of-cell pixels are NaN-filled **before** smoothing
    (``apply_gaussian_smoothing`` then takes its NaN-safe normalized-convolution
    path), so they cannot bleed across the cell boundary into in-cell values; the
    threshold and mask are then computed strictly inside the original selection.

    Returns ``None`` on a degenerate pass (constant/empty selection, or no
    component survives the noise floor). Raises :class:`ValueError` on a
    non-positive ``pixel_size_um`` (the knob is physical — mirrors
    :func:`window_min_spot_for_particle`).
    """
    if pixel_size_um is None or float(pixel_size_um) <= 0:
        raise ValueError(f"pixel_size_um must be > 0 µm/px, got {pixel_size_um}")
    from skimage import measure
    from skimage.filters import threshold_otsu as sk_threshold_otsu

    raw = np.asarray(image, dtype=np.float32)
    base_finite = np.isfinite(raw)
    base_sel = ((np.asarray(cp_mask) > 0) & base_finite) if cp_mask is not None else base_finite
    if not base_sel.any():
        return None
    # NaN-fill outside the selection before smoothing so out-of-cell pixels do not
    # bleed inward across the boundary; re-clamp to base_sel after so in-cell
    # values cannot bleed outward via the NaN-safe normalized convolution.
    work = np.where(base_sel, raw, np.nan).astype(np.float32)
    sm = apply_gaussian_smoothing(work, gaussian_sigma)
    sel = base_sel & np.isfinite(sm)
    vals = sm[sel]
    if vals.size == 0:
        return None
    vmin, vmax = float(vals.min()), float(vals.max())
    # Near-constant selection is degenerate: Otsu on it is meaningless and would
    # split float32 smoothing noise into a spurious cell-sized "particle". The
    # tolerance sits far above that epsilon (~1e-7 relative) yet orders of
    # magnitude below any real contrast, so a genuinely flat cell returns None.
    if (vmax - vmin) <= 1e-6 * (abs(vmax) + 1.0):
        return None
    thr = float(sk_threshold_otsu(vals))
    mask = sel & (sm > thr)
    if not mask.any():
        return None
    # connectivity=1 (4-conn) matches _filter_by_area, the size filter the per-cell
    # engine this seeds applies — so "one particle" is counted the same both ways
    # (a diagonal speck must not seed a d_min the engine would then reject).
    labels = measure.label(mask, connectivity=1)
    areas = np.array([p.area for p in measure.regionprops(labels)], dtype=float)
    areas = areas[areas >= noise_floor_px]
    if areas.size == 0:
        return None
    smallest_px = float((2.0 * np.sqrt(areas / np.pi)).min())
    area_vals = sm[mask]  # intensities of the Otsu threshold area (above threshold)
    return OtsuSmallestReport(
        otsu_threshold=thr,
        smallest_diameter_px=smallest_px,
        d_min_um=smallest_px * float(pixel_size_um),
        n_components=int(areas.size),
        area_mean=float(area_vals.mean()),
        area_max=float(area_vals.max()),
        area_min=float(area_vals.min()),
        scope="in-cell" if cp_mask is not None else "whole-frame",
    )


def detect_smallest_particle_um(
    image: np.ndarray,
    gaussian_sigma: float | None,
    pixel_size_um: float,
    *,
    cp_mask: np.ndarray | None = None,
    noise_floor_px: int = AUTO_WINDOW_NOISE_FLOOR_PX,
) -> float | None:
    """Smallest particle diameter (µm) from an Otsu first-pass — seeds ``d_min``.

    Thin wrapper over :func:`otsu_smallest_particle` returning only its
    ``d_min_um`` (the value the auto-fill writes into the spinbox); ``None`` on a
    degenerate pass. See that function for the full contract and diagnostics.
    """
    report = otsu_smallest_particle(
        image, gaussian_sigma, pixel_size_um, cp_mask=cp_mask, noise_floor_px=noise_floor_px
    )
    return report.d_min_um if report is not None else None


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
