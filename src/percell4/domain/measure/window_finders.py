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


WINDOW_FINDERS: dict[str, Callable[..., float]] = {
    "otsu-mean": _otsu_mean,
    "granule-size": _granule_size,
}

# Drift guard: registry keys are exactly the single-source-of-truth tuple.
assert set(WINDOW_FINDERS) == set(WINDOW_FINDER_NAMES), (
    f"WINDOW_FINDERS keys drifted from WINDOW_FINDER_NAMES: "
    f"{set(WINDOW_FINDERS) ^ set(WINDOW_FINDER_NAMES)}"
)
