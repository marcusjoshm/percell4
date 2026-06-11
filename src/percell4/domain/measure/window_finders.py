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


WINDOW_FINDERS: dict[str, Callable[..., float]] = {
    "otsu-mean": _otsu_mean,
}

# Drift guard: registry keys are exactly the single-source-of-truth tuple.
assert set(WINDOW_FINDERS) == set(WINDOW_FINDER_NAMES), (
    f"WINDOW_FINDERS keys drifted from WINDOW_FINDER_NAMES: "
    f"{set(WINDOW_FINDERS) ^ set(WINDOW_FINDER_NAMES)}"
)
