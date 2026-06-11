"""Registry of auto-window-size finders for Adaptive Local Clipping (plan U1).

Each finder estimates the local-background window (in px) for the ``adaptive``
detector from an already-smoothed image. The window controls
``threshold_local``'s Gaussian-blur scale (``sigma = (window-1)/6``): it must be
several times the focus size so the local background is *not* pulled up by the
focus it sits beneath. The current production heuristic (Otsu mean diameter) is
the ``otsu-mean`` baseline here; the bake-off (plan) adds and scores candidates.

Uniform signature for every entry of :data:`WINDOW_FINDERS`::

    finder(image: np.ndarray,
           params: dict,
           *,
           cp_mask: np.ndarray | None = None,
           pixel_size_um: float | None = None,
           detect_at_window) -> float   # RAW px estimate

Contract
--------
* **Finders return a RAW window estimate (float).** The odd/clamped contract is
  applied in exactly one place — :func:`percell4.domain.measure.adaptive_clip.
  auto_window` — which does ``_make_odd(clamp(round(raw), lo, hi))``. Callers
  that need an odd, in-range window must go through ``auto_window``; a bare
  finder return is unclamped.
* **Never raise on degenerate input.** A constant / empty / signal-free image
  returns ``0.0`` so the orchestrator floors it to ``_make_odd(lo)``.
* **Pure: numpy / scipy / skimage only** (lazy skimage imports inside finders).
  Finders never import :mod:`percell4.domain.measure.adaptive_clip` (that would
  cycle); outcome-driven finders instead use the injected ``detect_at_window``
  closure to run detection at a candidate window. Pure size/scale/frequency
  finders ignore ``detect_at_window``.

Registry keys must equal :data:`WINDOW_FINDER_NAMES` (asserted at import).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from percell4.domain.measure.window_finder_names import WINDOW_FINDER_NAMES

__all__ = ["WINDOW_FINDERS", "WINDOW_FINDER_NAMES"]

# Otsu-mask components smaller than this (px area) are treated as noise and
# excluded from the size statistic (mirrors ``adaptive_clip.AUTO_WINDOW_NOISE_FLOOR_PX``).
_NOISE_FLOOR_PX = 3


def _otsu_mean(
    image: np.ndarray,
    params: dict,
    *,
    cp_mask: np.ndarray | None = None,
    pixel_size_um: float | None = None,
    detect_at_window: Callable[[int], np.ndarray] | None = None,
) -> float:
    """Baseline: ``factor * mean equivalent-diameter`` of a whole-frame Otsu mask.

    The current production heuristic, ported verbatim so the bake-off has a
    faithful baseline to beat. It is broken on black-background images (Otsu
    over-segments and the mean is speck-dominated) — that is *why* it is the
    baseline. Returns the raw window (``auto_window`` clamps + odds); returns
    ``0.0`` on a constant/empty/signal-free image so the orchestrator floors it.

    Numeric parity with the legacy
    :func:`percell4.domain.measure.adaptive_clip.estimate_adaptive_window`
    (``otsu_first_pass`` -> mean diameter) is pinned by a characterization test.
    """
    from skimage import measure
    from skimage.filters import threshold_otsu as sk_threshold_otsu

    factor = float(params.get("factor", 2.0))
    sm = np.asarray(image, dtype=np.float32)
    finite = sm[np.isfinite(sm)]
    if finite.size == 0 or float(finite.min()) == float(finite.max()):
        return 0.0
    thr = float(sk_threshold_otsu(finite))
    mask = sm > thr
    if not mask.any():
        return 0.0
    labels = measure.label(mask)
    areas = np.array([p.area for p in measure.regionprops(labels)], dtype=float)
    areas = areas[areas >= _NOISE_FLOOR_PX]
    if areas.size == 0:
        return 0.0
    diameters = 2.0 * np.sqrt(areas / np.pi)
    return factor * float(diameters.mean())


def _granule_size(
    image: np.ndarray,
    params: dict,
    *,
    cp_mask: np.ndarray | None = None,
    pixel_size_um: float | None = None,
    detect_at_window: Callable[[int], np.ndarray] | None = None,
) -> float:
    """``c * robust granule diameter``, isolating granules without whole-frame Otsu.

    Fixes all three failure modes of ``otsu-mean``: (1) isolate granules with a
    Gaussian **high-pass** (``image - gaussian(image, hp_sigma)``) + a robust
    ``k * MAD`` threshold instead of whole-frame Otsu — the high-pass removes the
    black outside-cell region and dilute gradients, so it does not over-segment;
    (2) restrict to in-cell pixels when ``cp_mask`` is given (harness-supplied;
    the interactive GUI passes ``None`` and still works whole-frame because the
    high-pass is background-invariant); (3) size by an **area-weighted** (or
    ``p90``) equivalent-diameter so large granules drive the window, not the
    speck median. ``window = c * diameter`` (``c`` ~ 4–5, the local-background
    window wants to be several times the focus). Returns ``0.0`` on degenerate
    input (orchestrator floors it).

    ``params``: ``c`` (default 4.5), ``k`` (default 3.0), ``hp_sigma`` (default
    12.0), ``stat`` (``"area_weighted"`` | ``"p90"``).
    """
    from scipy.ndimage import gaussian_filter
    from skimage import measure

    c = float(params.get("c", 4.5))
    k = float(params.get("k", 3.0))
    hp_sigma = float(params.get("hp_sigma", 12.0))
    stat = str(params.get("stat", "area_weighted"))

    sm = np.asarray(image, dtype=np.float32)
    finite = np.isfinite(sm)
    sel = ((np.asarray(cp_mask) > 0) & finite) if cp_mask is not None else finite
    if not sel.any():
        return 0.0

    # NaN-safe fill (gaussian_filter is not NaN-safe); the high-pass removes a
    # constant fill anyway, so the in-sel median is a neutral choice.
    fill = float(np.median(sm[sel]))
    filled = np.where(finite, sm, fill).astype(np.float32)
    hp = filled - gaussian_filter(filled, hp_sigma, mode="reflect")

    vals = hp[sel]
    med = float(np.median(vals))
    mad = float(np.median(np.abs(vals - med)))
    sigma = 1.4826 * mad
    if sigma <= 0:
        return 0.0
    granules = sel & (hp > med + k * sigma)
    if not granules.any():
        return 0.0

    labels = measure.label(granules)
    areas = np.array([p.area for p in measure.regionprops(labels)], dtype=float)
    areas = areas[areas >= _NOISE_FLOOR_PX]
    if areas.size == 0:
        return 0.0
    diameters = 2.0 * np.sqrt(areas / np.pi)
    if stat == "p90":
        diam = float(np.percentile(diameters, 90))
    else:  # area-weighted: big granules count more
        diam = float((diameters * areas).sum() / areas.sum())
    return c * diam


def _mask_diameter(mask: np.ndarray, *, stat: str = "area_weighted") -> float:
    """Robust equivalent-diameter of a binary mask's components (>= noise floor)."""
    from skimage import measure

    labels = measure.label(np.asarray(mask) > 0)
    areas = np.array([p.area for p in measure.regionprops(labels)], dtype=float)
    areas = areas[areas >= _NOISE_FLOOR_PX]
    if areas.size == 0:
        return 0.0
    diameters = 2.0 * np.sqrt(areas / np.pi)
    if stat == "p90":
        return float(np.percentile(diameters, 90))
    return float((diameters * areas).sum() / areas.sum())  # area-weighted


# ── Outcome-driven finders (no particle size measured to set the window) ────


def _sweep_knee(
    image: np.ndarray,
    params: dict,
    *,
    cp_mask: np.ndarray | None = None,
    pixel_size_um: float | None = None,
    detect_at_window: Callable[[int], np.ndarray] | None = None,
) -> float:
    """The window where the detection result saturates — no size, no multiplier.

    Runs the production detector (injected ``detect_at_window``) at an ascending
    ladder of windows and tracks a detection metric — total foreground ``area``
    (default) or component ``count``. As the window grows the detector captures
    granules better until the metric saturates; the **knee** (the point of
    greatest deviation from the straight chord joining the first and last grid
    points) is the smallest window past which detection stops improving. The
    window is read straight off detection behavior. ``params``: ``grid_lo`` (21),
    ``grid_hi`` (201), ``n`` (10), ``metric`` (``"area"`` | ``"count"``).
    """
    if detect_at_window is None:
        return 0.0
    lo = int(params.get("grid_lo", 21))
    hi = int(params.get("grid_hi", 201))
    n = max(2, int(params.get("n", 10)))
    metric = str(params.get("metric", "area"))

    windows = sorted({int(w) | 1 for w in np.linspace(lo, hi, n)})
    vals: list[float] = []
    for w in windows:
        mask = np.asarray(detect_at_window(int(w))) > 0
        if metric == "count":
            from skimage import measure

            vals.append(float(measure.label(mask).max()))
        else:
            vals.append(float(np.count_nonzero(mask)))

    v = np.array(vals, dtype=float)
    x = np.array(windows, dtype=float)
    if v.max() <= v.min():  # flat / empty across the grid -> no knee
        return 0.0
    # Knee = point of greatest deviation from the first->last chord (normalized),
    # robust whether the metric saturates upward (area) or downward (count).
    xn = (x - x[0]) / (x[-1] - x[0] + 1e-12)
    vn = (v - v.min()) / (v.max() - v.min() + 1e-12)
    chord = vn[0] + (vn[-1] - vn[0]) * xn
    knee_idx = int(np.argmax(np.abs(vn - chord)))
    return float(windows[knee_idx])


def _fixed_point(
    image: np.ndarray,
    params: dict,
    *,
    cp_mask: np.ndarray | None = None,
    pixel_size_um: float | None = None,
    detect_at_window: Callable[[int], np.ndarray] | None = None,
) -> float:
    """Self-consistent window where the detection and its granule scale agree.

    Iterate: detect at the current window, measure the detected granule diameter,
    set ``window = c * diameter``, repeat until the window stops moving (or a
    cap). Unlike ``granule-size`` (which measures size once at a *fixed*
    isolation scale — chicken-and-egg), the granules are defined by the detector
    AT the current window each round, so it self-corrects toward a fixed point.
    It carries a consistency ratio ``c`` (the window/granule ratio the local
    background needs), but converges without a hand-picked answer. Monotonic-
    merge failure mode: a too-large window merges granules, inflating the
    measured diameter and driving the window up — a run that does not converge
    and lands pinned near the clamp ceiling is a non-result, not a score.
    ``params``: ``c`` (5.0), ``seed`` (31), ``max_iter`` (8), ``tol`` (4 px).
    """
    if detect_at_window is None:
        return 0.0
    c = float(params.get("c", 5.0))
    w = int(params.get("seed", 31)) | 1
    max_iter = int(params.get("max_iter", 8))
    tol = float(params.get("tol", 4.0))
    stat = str(params.get("stat", "area_weighted"))

    for _ in range(max_iter):
        diam = _mask_diameter(detect_at_window(w), stat=stat)
        if diam <= 0:
            return 0.0
        new_w = int(round(c * diam)) | 1
        if abs(new_w - w) <= tol:
            return float(new_w)  # converged
        w = new_w
    return float(w)  # did not converge within the cap -> last estimate (non-result)


# ── Texture / frequency finder (no segmentation, no counting) ──────────────


def _downsample(image: np.ndarray, max_dim: int) -> tuple[np.ndarray, float]:
    """Block-mean downsample so the largest dim <= ``max_dim``; return (small, factor).

    A feature of size ``D`` in the original is ``D / factor`` in the result, so a
    scale measured on the downsampled image is multiplied by ``factor`` to return
    to original pixels. Keeps FFT / morphology tractable on 33 MP frames.
    """
    sm = np.asarray(image, dtype=np.float32)
    if max(sm.shape) <= max_dim:
        return sm, 1.0
    from skimage.transform import downscale_local_mean

    factor = int(np.ceil(max(sm.shape) / float(max_dim)))
    return np.asarray(downscale_local_mean(sm, (factor, factor)), dtype=np.float32), float(factor)


def _radial_profile(arr: np.ndarray) -> np.ndarray:
    """Mean of ``arr`` as a function of integer radius from its center."""
    h, w = arr.shape
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    yy, xx = np.ogrid[:h, :w]
    r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2).astype(int)
    counts = np.bincount(r.ravel())
    sums = np.bincount(r.ravel(), weights=arr.ravel())
    return sums / np.maximum(counts, 1)


def _autocorr(
    image: np.ndarray,
    params: dict,
    *,
    cp_mask: np.ndarray | None = None,
    pixel_size_um: float | None = None,
    detect_at_window: Callable[[int], np.ndarray] | None = None,
) -> float:
    """Characteristic scale from image texture — no segmentation, no counting.

    High-pass the (downsampled) image to drop the large-scale background, then
    read its dominant granule scale from the frequency domain: ``mode="autocorr"``
    (default) takes the half-width-at-half-max of the radial autocorrelation
    (≈ granule radius); ``mode="spectrum"`` takes the radial power-spectrum peak
    wavelength. ``window = c * scale`` — a completely different *signal* from
    Otsu/measuring, though it still maps a scale to a window via ``c``. ``params``:
    ``c`` (6.0), ``mode``, ``hp_sigma`` (25.0), ``max_dim`` (1024).
    """
    from scipy.ndimage import gaussian_filter

    c = float(params.get("c", 6.0))
    mode = str(params.get("mode", "autocorr"))
    hp_sigma = float(params.get("hp_sigma", 25.0))

    small, factor = _downsample(image, int(params.get("max_dim", 1024)))
    finite = np.isfinite(small)
    if not finite.any():
        return 0.0
    fin_vals = small[finite]
    if float(fin_vals.min()) == float(fin_vals.max()):
        return 0.0
    filled = np.where(finite, small, float(np.median(fin_vals))).astype(np.float32)
    hp = filled - gaussian_filter(filled, max(1.0, hp_sigma / factor), mode="reflect")
    hp = hp - float(hp.mean())
    if not np.any(hp):
        return 0.0

    if mode == "spectrum":
        ps = np.fft.fftshift(np.abs(np.fft.fft2(hp)) ** 2)
        prof = _radial_profile(ps)
        if prof.size < 3:
            return 0.0
        prof[0] = 0.0  # kill DC
        k_peak = int(np.argmax(prof))
        if k_peak <= 0:
            return 0.0
        wavelength_ds = float(min(hp.shape)) / float(k_peak)  # px per cycle
        return c * wavelength_ds * factor

    # autocorr HWHM
    ac = np.fft.fftshift(np.fft.irfft2(np.abs(np.fft.rfft2(hp)) ** 2, s=hp.shape))
    peak = float(ac.max())
    if peak <= 0:
        return 0.0
    prof = _radial_profile(ac) / peak
    below = np.where(prof < 0.5)[0]
    if below.size == 0 or below[0] <= 0:
        return 0.0
    hwhm_ds = float(below[0])
    return c * (2.0 * hwhm_ds) * factor  # diameter ~ 2*HWHM, back to original px


WINDOW_FINDERS: dict[str, Callable[..., float]] = {
    "otsu-mean": _otsu_mean,
    "granule-size": _granule_size,
    "sweep-knee": _sweep_knee,
    "fixed-point": _fixed_point,
    "autocorr": _autocorr,
}

# Drift guard: registry keys are exactly the single-source-of-truth tuple.
assert set(WINDOW_FINDERS) == set(WINDOW_FINDER_NAMES), (
    f"WINDOW_FINDERS keys drifted from WINDOW_FINDER_NAMES: "
    f"{set(WINDOW_FINDERS) ^ set(WINDOW_FINDER_NAMES)}"
)
