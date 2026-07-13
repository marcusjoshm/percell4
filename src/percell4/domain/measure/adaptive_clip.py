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
from collections.abc import Mapping
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


def per_cell_sigma(work: np.ndarray, labels: np.ndarray) -> dict[int, float]:
    """Robust per-cell noise scale ``{cell_id -> 1.4826 * MAD(work) in the cell}``.

    ``work`` is the **presmoothed** working image (``apply_gaussian_smoothing``
    output), *not* the raw image — σ is defined on the same buffer the detector
    thresholds, so :func:`detect_adaptive_per_cell` and the CNR measurement in
    :mod:`percell4.domain.measure.cnr_classification` share one definition. Cells
    whose MAD is zero or non-finite are omitted (they cannot define a threshold
    and are skipped during detection).
    """
    from scipy.ndimage import find_objects

    lab = np.asarray(labels)
    sigma: dict[int, float] = {}
    for idx, sl in enumerate(find_objects(lab)):
        if sl is None:
            continue
        cid = idx + 1
        cell = lab[sl] == cid
        if not cell.any():
            continue
        vals = work[sl][cell]
        med = float(np.median(vals))
        s = 1.4826 * float(np.median(np.abs(vals - med)))
        if np.isfinite(s) and s > 0.0:
            sigma[cid] = s
    return sigma


def pooled_sigma(work: np.ndarray, labels: np.ndarray) -> float | None:
    """Robust *global* noise scale ``1.4826 * MAD(work)`` over all in-cell pixels.

    The whole-selection counterpart to :func:`per_cell_sigma`: one σ pooled from
    every labelled (``labels > 0``), finite pixel of the **presmoothed** working
    image — the noise floor for the "global" Adaptive Local Clipping mode, where a
    single σ thresholds every cell instead of each cell's own. Returns ``None``
    when the pooled MAD is zero or non-finite (no threshold can be defined); the
    caller then produces an empty mask, mirroring the per-cell skip.
    """
    w = np.asarray(work)
    lab = np.asarray(labels)
    vals = w[(lab > 0) & np.isfinite(w)]
    if vals.size == 0:
        return None
    med = float(np.median(vals))
    s = 1.4826 * float(np.median(np.abs(vals - med)))
    return s if (np.isfinite(s) and s > 0.0) else None


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
    global_sigma: bool = False,
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
      datasets); pass ``global_sigma=True`` to instead pool one σ over all cell
      pixels and threshold every cell against it,
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
    window_px, min_spot_px = window_min_spot_for_particle(
        d_min_um, pixel_size_um, factor=factor
    )
    return detect_adaptive_per_cell(
        image,
        labels,
        window_px=window_px,
        min_spot_px=min_spot_px,
        k=k,
        presmooth_sigma_px=presmooth_sigma_px,
        global_sigma=global_sigma,
    )


def detect_adaptive_per_cell(
    image: np.ndarray,
    labels: np.ndarray,
    *,
    window_px: int,
    min_spot_px: int,
    k: float | Mapping[int, float] = 1.0,
    presmooth_sigma_px: float = 1.0,
    fill_holes: bool = False,
    global_sigma: bool = False,
) -> np.ndarray:
    """Per-cell adaptive local clip with an explicit window + size filter.

    The shared core of the per-cell detectors (the manual segmentation path and
    :func:`detect_adaptive_by_particle_size`, which derives ``window_px`` /
    ``min_spot_px`` from a physical ``d_min`` and delegates here). Presmooths at
    ``presmooth_sigma_px``, subtracts a whole-frame Gaussian local-mean background
    at ``sigma=(window_px-1)/6`` (computed once — a per-cell-crop background would
    distort the cell-edge granules), then thresholds every Cellpose cell against
    *its own* robust ``1.4826*MAD`` σ at ``k``. The per-cell σ is what lets one
    ``k`` hold across cells whose intensity scale varies many-fold.

    ``k`` is either a scalar (one k for every cell) or a ``{cell_id -> k}`` mapping
    for a genuinely per-cell threshold (e.g. the two-pass auto-extraction coarse
    pass, where the noise-symmetry floor is estimated per cell). With a mapping, a
    cell absent from it is **not** thresholded this pass (it has no defined k) —
    callers build the mapping over the same finite-σ cells this function thresholds,
    so in practice every thresholded cell is present. When ``global_sigma`` is True,
    a single σ pooled over all in-cell pixels (:func:`pooled_sigma`) thresholds
    every cell instead of each cell's own robust MAD — the "global" mode, for when
    a shared noise floor at one ``k`` is wanted rather than a per-cell one (a flat
    or non-finite pool then omits every cell → empty mask). When ``fill_holes`` is True,
    enclosed interior holes are filled per pass (a large particle under-windowed
    into a ring is closed solid) **before** the size filter; default False leaves
    the mask as detected. Components smaller than ``min_spot_px`` are dropped (a
    no-op when ``min_spot_px <= 1``). Returns a whole-frame ``{0, 1}`` ``uint8`` mask.
    """
    from scipy.ndimage import binary_fill_holes, find_objects, gaussian_filter

    img = np.asarray(image, dtype=np.float32)
    lab = np.asarray(labels)
    k_is_map = isinstance(k, Mapping)

    work = apply_gaussian_smoothing(img, presmooth_sigma_px)
    # Local background = Gaussian mean at the threshold_local('gaussian') sigma.
    # The constant global offset cancels in `diff`, so the per-cell comparison
    # reduces to image > local_background + k*sigma (see adaptive_local_clip.ijm).
    diff = work - gaussian_filter(work, (window_px - 1) / 6.0)

    # Noise floor: one σ pooled over all cell pixels (global mode), else each
    # cell's own robust MAD. A None global σ (flat/empty pool) omits every cell,
    # exactly as a per-cell None does.
    global_s = pooled_sigma(work, lab) if global_sigma else None
    sigmas = {} if global_sigma else per_cell_sigma(work, lab)
    out = np.zeros(img.shape, dtype=bool)
    for idx, sl in enumerate(find_objects(lab)):
        if sl is None:
            continue
        cid = idx + 1
        s = global_s if global_sigma else sigmas.get(cid)
        if s is None:  # zero/non-finite MAD: cell omitted (cannot threshold)
            continue
        if k_is_map:
            kc = k.get(cid)
            if kc is None:  # no per-cell k for this cell -> not thresholded this pass
                continue
            kc = float(kc)
        else:
            kc = float(k)
        cell = lab[sl] == cid
        out[sl] |= (diff[sl] > kc * s) & cell

    if fill_holes:
        out = binary_fill_holes(out)
    return _filter_by_area(out, min_spot_px).astype(np.uint8)


def multiscale_windows(
    start_window_px: float,
    max_particle_px: float,
    *,
    max_passes: int = 16,
    force_passes: int | None = None,
) -> list[int]:
    """The doubling window sequence for the multi-scale routine.

    Returns odd px windows ``[W0, 2·W0, 4·W0, …]`` starting at ``start_window_px``
    (forced odd, floored at :data:`PARTICLE_WINDOW_MIN`).

    * Default (``force_passes`` is ``None``): stop **after** the first window that
      exceeds ``max_particle_px`` (so the largest particle is bracketed);
      ``max_passes`` is a backstop against a degenerate ``max_particle_px``.
    * ``force_passes = N`` (> 0): return **exactly** ``N`` windows regardless of
      ``max_particle_px`` — the manual iteration count, for testing the doubling.
    """
    windows: list[int] = []
    w = max(PARTICLE_WINDOW_MIN, _make_odd(int(round(float(start_window_px)))))
    if force_passes is not None and int(force_passes) > 0:
        for _ in range(int(force_passes)):
            windows.append(w)
            w = _make_odd(w * 2)
        return windows
    for _ in range(max(1, int(max_passes))):
        windows.append(w)
        if w > float(max_particle_px):
            break
        w = _make_odd(w * 2)
    return windows


def detect_adaptive_multiscale(
    image: np.ndarray,
    labels: np.ndarray,
    *,
    start_window_px: float,
    max_particle_px: float,
    k: float = 1.0,
    presmooth_sigma_px: float = 1.0,
    min_spot_px: int = 1,
    max_passes: int = 16,
    force_passes: int | None = None,
) -> tuple[np.ndarray, list[int]]:
    """Multi-scale per-cell adaptive clip: OR-union over a doubling window sequence.

    Runs :func:`detect_adaptive_per_cell` at each window from
    :func:`multiscale_windows` and OR-combines the masks (a pixel is foreground if
    *any* pass marks it), so particles across a wide size range are all captured:
    each window cleanly detects particles a few × smaller than it, and the union
    spans the smallest start window up to past the largest particle.
    ``force_passes = N`` runs exactly ``N`` doubling passes instead (manual
    iteration count). ``min_spot_px`` is the size filter applied **once to the
    OR-combined mask** (a particle may be fully formed only in the union, so each
    pass detects unfiltered and the filter runs last); ``<= 1`` keeps everything.
    Returns ``(combined {0,1} uint8 mask, windows_used)``.
    """
    windows = multiscale_windows(
        start_window_px, max_particle_px, max_passes=max_passes, force_passes=force_passes
    )
    combined: np.ndarray | None = None
    for w in windows:
        m = detect_adaptive_per_cell(
            image,
            labels,
            window_px=w,
            min_spot_px=1,  # no per-pass filter; the union is filtered once below
            k=k,
            presmooth_sigma_px=presmooth_sigma_px,
        )
        combined = m.astype(bool) if combined is None else (combined | m.astype(bool))
    if combined is None:
        combined = np.zeros(np.asarray(image).shape, dtype=bool)
    combined = _filter_by_area(combined, int(min_spot_px))
    return combined.astype(np.uint8), windows


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
    inside the selection), before the noise-floor size filter. ``diameter_px`` is
    the ``percentile``-th equivalent diameter of the surviving components (a low
    percentile is a robust stand-in for "smallest"; ``percentile=0`` is the
    absolute minimum).
    """

    otsu_threshold: float          # Otsu threshold on the smoothed selected pixels
    diameter_px: float             # the percentile-th component equiv. diameter
    d_min_um: float                # diameter_px * pixel_size_um
    percentile: float              # the diameter percentile used (0 = absolute min)
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
    percentile: float = 0.0,
) -> OtsuSmallestReport | None:
    """Otsu first-pass measuring a low-percentile particle size (+ diagnostics).

    Smooths ``image`` (``gaussian_sigma``), runs the Otsu first pass over the
    finite (and, when ``cp_mask`` is given, in-cell) pixels, labels the
    foreground, drops sub-``noise_floor_px`` specks, and reports the
    ``percentile``-th equivalent diameter (``2*sqrt(area/pi)``) of the survivors —
    in px and µm — alongside the Otsu threshold and the threshold-area intensity
    stats. ``percentile=0`` gives the absolute smallest; a low percentile (e.g.
    10) is robust to a single tiny fragment dragging the size down. This is the
    inverse of :func:`window_min_spot_for_particle`: the Otsu pass measures the
    particle size so the per-cell ``d_min`` knob (and the window + size filter it
    derives) can be set from the image instead of eyeballed.

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
    pct = float(min(100.0, max(0.0, percentile)))
    diameter_px = float(np.percentile(2.0 * np.sqrt(areas / np.pi), pct))
    area_vals = sm[mask]  # intensities of the Otsu threshold area (above threshold)
    return OtsuSmallestReport(
        otsu_threshold=thr,
        diameter_px=diameter_px,
        d_min_um=diameter_px * float(pixel_size_um),
        percentile=pct,
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


@dataclasses.dataclass(frozen=True)
class ParticleSizeReport:
    """Per-cell-Otsu particle-size assessment (px diameters) for the multi-scale routine.

    ``raw_min_px`` / ``raw_max_px`` are over ALL detected particles (above the
    noise floor, before the cutoff) — always surfaced for calibration. The other
    stats are over particles ``>= cutoff_px`` (the cutoff is a stats floor); if the
    cutoff excludes everything, they fall back to the full set so the routine still
    runs (and the raw min/max reveal that the cutoff is too high).
    """

    raw_min_px: float
    raw_max_px: float
    smallest_px: float   # smallest particle >= cutoff
    mean_px: float       # mean diameter >= cutoff (drives the ½×mean start window)
    largest_px: float    # largest particle >= cutoff (the doubling stop target)
    n_particles: int     # particles >= cutoff (or all, on cutoff fallback)
    n_raw: int           # all particles above the noise floor

    @property
    def range_px(self) -> float:
        """Spread of the (post-cutoff) particle sizes (largest − smallest)."""
        return self.largest_px - self.smallest_px


def assess_particle_sizes_per_cell(
    image: np.ndarray,
    labels: np.ndarray,
    gaussian_sigma: float | None,
    *,
    cutoff_px: float = 0.0,
    noise_floor_px: int = AUTO_WINDOW_NOISE_FLOOR_PX,
) -> ParticleSizeReport | None:
    """First-pass size assessment: per-cell Otsu, pooled component diameters.

    Smooths the image, then for each Cellpose cell runs Otsu on **that cell's**
    pixels (so dim and bright cells each get an apt threshold), labels the in-cell
    foreground, and pools every surviving component's equivalent diameter
    (``2*sqrt(area/pi)``, area ``>= noise_floor_px``) across cells. Returns a
    :class:`ParticleSizeReport`; ``None`` only when no particle is found at all.
    Particles below ``cutoff_px`` are excluded from the (non-raw) stats.
    """
    from scipy.ndimage import find_objects
    from skimage import measure
    from skimage.filters import threshold_otsu as sk_threshold_otsu

    sm = apply_gaussian_smoothing(np.asarray(image, dtype=np.float32), gaussian_sigma)
    lab = np.asarray(labels)
    diameters: list[float] = []
    for idx, sl in enumerate(find_objects(lab)):
        if sl is None:
            continue
        cell = lab[sl] == (idx + 1)
        if not cell.any():
            continue
        vals = sm[sl][cell]
        finite = vals[np.isfinite(vals)]
        if finite.size == 0:
            continue
        vmin, vmax = float(finite.min()), float(finite.max())
        # Near-constant cell is degenerate — same relative-tolerance guard as
        # otsu_smallest_particle. An exact-equality check would admit a float32-
        # epsilon gradient and Otsu would carve a spurious cell-sized "particle",
        # inflating raw_max/mean and the multi-scale window sequence.
        if (vmax - vmin) <= 1e-6 * (abs(vmax) + 1.0):
            continue
        thr = float(sk_threshold_otsu(finite))
        cell_fg = cell & (sm[sl] > thr)
        if not cell_fg.any():
            continue
        comp = measure.label(cell_fg, connectivity=1)
        for p in measure.regionprops(comp):
            if p.area >= noise_floor_px:
                diameters.append(2.0 * math.sqrt(p.area / math.pi))

    if not diameters:
        return None
    diam = np.asarray(diameters, dtype=float)
    kept = diam[diam >= float(cutoff_px)]
    if kept.size == 0:  # cutoff above everything -> fall back so the run proceeds
        kept = diam
    return ParticleSizeReport(
        raw_min_px=float(diam.min()),
        raw_max_px=float(diam.max()),
        smallest_px=float(kept.min()),
        mean_px=float(kept.mean()),
        largest_px=float(kept.max()),
        n_particles=int(kept.size),
        n_raw=int(diam.size),
    )


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


def resolve_window_px(value: float, unit: str, pixel_size_um: float | None) -> int:
    """Convert a manual window value+unit into an odd pixel window (floored at 3).

    ``unit`` is ``"px"`` (the value is a pixel window) or ``"um"`` (a physical
    window in µm, converted via a positive ``pixel_size_um`` or :class:`ValueError`).
    The result is forced odd (the local window must be odd) and floored at
    :data:`PARTICLE_WINDOW_MIN` (below which the local-background blur subtracts the
    particle from itself).
    """
    v = float(value)
    if unit == "px":
        px = v
    elif unit == "um":
        if not pixel_size_um or float(pixel_size_um) <= 0:
            raise ValueError(
                "µm window requires a known pixel size; switch the unit to px or "
                "re-import the dataset with TIFF resolution metadata."
            )
        px = v / float(pixel_size_um)
    else:
        raise ValueError(f"unknown window unit: {unit!r}")
    return max(PARTICLE_WINDOW_MIN, _make_odd(int(round(px))))
