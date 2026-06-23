"""Fully-automated two-pass total feature extraction for Adaptive Local Clipping.

The parameter-light front-end to total foci extraction. Both windows follow one
rule — ``window = fill_factor × particle diameter`` (the no-hole floor) — applied
to the *smallest* particle for the fine pass and the *largest* for the coarse
pass:

    Pass 1 (fine)   : window = fill_factor × smallest particle Ø (you supply),
                      k = 1                       -> catches the small particles
    Pass 2 (coarse) : window = fill_factor × largest particle Ø (measured by LoG),
                      k = noise-symmetry floor    -> fills the large particles
                                                     without oversampling

The two passes are OR-unioned (with per-pass hole-filling). The coarse pass is
added **only** when its window exceeds the fine window; otherwise a single pass
runs.

Why the smallest is supplied but the largest is measured
--------------------------------------------------------
The **largest** particle is bright and isolated, so a Laplacian-of-Gaussian
operator sizes it reliably from the image (it responds to local curvature, so a
particle on a bright diffuse dilute phase still peaks while the smooth phase does
not — where intensity thresholding / Otsu fails). The **smallest** is confounded
with noise — at small scales a detector returns whatever its scale floor is set
to, not a real size — so it is **not** measured; you supply it, because it is
really your optical resolution limit, which you know and the image does not.

Why the coarse k is raised automatically
----------------------------------------
A large coarse window also admits broad dilute-phase structure, so the coarse k
must rise to reject it. The right value is the **noise-symmetry floor**: after
the band-pass, background + texture is ~symmetric about zero, so the count below
``-k·σ`` estimates the false positives at ``+k·σ``; the floor is the smallest k
whose estimated false rate (negative tail / positive tail) is ≤ ``fdr``.

This module reuses the shared per-cell detector
(:func:`percell4.domain.measure.adaptive_clip.detect_adaptive_per_cell`) and the
shared per-cell σ (:func:`percell4.domain.measure.adaptive_clip.per_cell_sigma`),
so its noise floor matches the detection comparison exactly. Pure domain
(numpy / scipy / skimage; ``blob_log`` imported lazily). 2D / single-channel.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import find_objects, gaussian_filter, label

from percell4.domain.measure.adaptive_clip import (
    PARTICLE_WINDOW_MIN,
    _filter_by_area,
    detect_adaptive_per_cell,
    per_cell_sigma,
)
from percell4.domain.measure.thresholding import apply_gaussian_smoothing

__all__ = [
    "auto_extract",
    "measure_largest_particle_diameter",
    "measure_smallest_particle_diameter",
    "noise_symmetry_floor_k",
    "AutoExtractReport",
]

# Reference (eye-validated) defaults — not user-facing knobs.
FILL_FACTOR = 3.0          # window = FILL_FACTOR × particle diameter (no-hole floor)
FDR = 0.1                  # target false rate for the coarse noise-symmetry floor
SIZE_PERCENTILE = 99.0     # LoG blob-diameter percentile taken as the largest particle
SMALL_PERCENTILE = 5.0     # low LoG percentile taken as the smallest particle (stable)
MIN_SIGMA_SMALL = 0.5      # LoG scale floor for autodetecting the smallest particle
LOG_PRESMOOTH = 1.0        # fixed pre-smoothing for LoG SIZING (decoupled from detection)
MAX_SIGMA = 20.0           # upper LoG scale (caps the largest detectable particle)


def _win(x: float) -> int:
    """Round to an integer window, floored at ``PARTICLE_WINDOW_MIN``.

    Windows need NOT be odd — a window maps to ``sigma_bg = (window-1)/6`` and the
    Gaussian filter accepts any sigma, so e.g. ``3 × 2 = 6`` stays 6.
    """
    return max(PARTICLE_WINDOW_MIN, int(round(float(x))))


@dataclass(frozen=True)
class AutoExtractReport:
    """Run description for :func:`auto_extract` (terminal debug / status)."""

    passes: list[tuple[int, float]]   # [(window, k), …] in run order
    fine_window: int
    largest_particle_px: float | None  # LoG-measured (or None on single pass)
    second_pass_used: bool
    presmooth_sigma_px: float
    n_cells: int
    n_components: int
    area_px: int
    smallest_diameter_px: float = 0.0  # effective smallest Ø (supplied or auto)
    smallest_source: str = ""          # "supplied …" | "auto LoG …"
    extra: dict = field(default_factory=dict)


def _log_diameters(
    image: np.ndarray,
    cell_labels: np.ndarray,
    *,
    min_sigma: float,
    max_sigma: float,
    presmooth_sigma_px: float,
    num_sigma: int = 12,
    threshold_rel: float = 0.1,
) -> np.ndarray:
    """In-cell LoG blob diameters (px, ``2√2·σ``). Requires scikit-image (lazy).

    The LoG response peaks at the scale matching a particle, so blob scale gives
    size. The image is normalised over its in-cell 1–99.9 percentile so the
    relative threshold behaves consistently. Returns an empty array on a
    degenerate/empty selection.
    """
    from skimage.feature import blob_log  # lazy import

    img = apply_gaussian_smoothing(np.asarray(image, dtype=np.float32), presmooth_sigma_px)
    cells = np.asarray(cell_labels)
    inc = cells > 0
    if not inc.any():
        return np.array([])
    finite = img[inc & np.isfinite(img)]
    if finite.size == 0:
        return np.array([])
    lo, hi = np.percentile(finite, [1.0, 99.9])
    if hi <= lo:
        return np.array([])
    imn = np.clip((np.nan_to_num(img, nan=lo) - lo) / (hi - lo), 0.0, 1.0)
    blobs = blob_log(
        imn,
        min_sigma=min_sigma,
        max_sigma=max_sigma,
        num_sigma=num_sigma,
        threshold_rel=threshold_rel,
    )
    return np.array(
        [2.0 * np.sqrt(2.0) * b[2] for b in blobs if cells[int(b[0]), int(b[1])] > 0]
    )


def measure_largest_particle_diameter(
    image: np.ndarray,
    cell_labels: np.ndarray,
    *,
    percentile: float = SIZE_PERCENTILE,
    presmooth_sigma_px: float = LOG_PRESMOOTH,
    max_sigma: float = MAX_SIGMA,
    num_sigma: int = 12,
    threshold_rel: float = 0.1,
) -> float:
    """Largest particle diameter (px), from a high percentile of LoG blob sizes.

    The largest particle is bright and isolated, so its LoG peak is unambiguous
    and this is reliable; the ``percentile`` (default 99th, not the bare maximum)
    guards against a spurious large-scale blob. Returns ``0.0`` if none found.
    """
    d = _log_diameters(
        image, cell_labels, min_sigma=1.0, max_sigma=max_sigma,
        presmooth_sigma_px=presmooth_sigma_px, num_sigma=num_sigma,
        threshold_rel=threshold_rel,
    )
    return float(np.percentile(d, percentile)) if d.size else 0.0


def measure_smallest_particle_diameter(
    image: np.ndarray,
    cell_labels: np.ndarray,
    *,
    min_sigma: float = MIN_SIGMA_SMALL,
    percentile: float = SMALL_PERCENTILE,
    presmooth_sigma_px: float = LOG_PRESMOOTH,
    max_sigma: float = MAX_SIGMA,
    num_sigma: int = 14,
    threshold_rel: float = 0.1,
) -> float:
    """Smallest particle diameter (px), from a low LoG percentile at ``min_sigma``.

    The caller applies the same no-hole rule as the largest end — fine window =
    ``fill_factor × this`` — so this returns the smallest particle *diameter*, not
    a window.

    CAVEAT: unlike the largest, the smallest is partly confounded with noise — at
    small scales the LoG also fires on texture, so the result depends on
    ``min_sigma``, ``presmooth_sigma_px`` and ``percentile`` (a low percentile, not
    the bare minimum, is used for stability). Treat it as an estimate of the
    resolution-limited smallest particle, not a precise physical size. Returns
    ``0.0`` if none found.
    """
    d = _log_diameters(
        image, cell_labels, min_sigma=min_sigma, max_sigma=max_sigma,
        presmooth_sigma_px=presmooth_sigma_px, num_sigma=num_sigma,
        threshold_rel=threshold_rel,
    )
    return float(np.percentile(d, percentile)) if d.size else 0.0


def noise_symmetry_floor_k(
    work: np.ndarray,
    cell_labels: np.ndarray,
    sigma: dict[int, float],
    window: int,
    *,
    fdr: float = FDR,
    k_floor: float = 1.0,
    k_ceiling: float = 15.0,
    step: float = 0.25,
) -> float:
    """Smallest k where the band-passed background is rejected at rate ≤ ``fdr``.

    Operates on precomputed ``work`` (the presmoothed image) and ``sigma`` (the
    per-cell σ from :func:`per_cell_sigma`) so the z-scores match the detection
    comparison exactly. After the band-pass at ``window``, background + texture is
    ~symmetric about zero, so the count below ``-k·σ`` estimates the false
    positives at ``+k·σ``; the floor is the smallest k whose estimated false rate
    (negative tail / positive tail) is ≤ ``fdr`` (with ≥20 positives, so a tiny
    tail does not latch a spuriously low k). Returns ``k_floor`` when no cell
    contributes z-scores, ``k_ceiling`` when the criterion is never met.
    """
    work = np.asarray(work)
    lab = np.asarray(cell_labels)
    diff = work - gaussian_filter(work, (window - 1) / 6.0)

    zs = []
    for idx, sl in enumerate(find_objects(lab)):
        if sl is None:
            continue
        cid = idx + 1
        s = sigma.get(cid)
        if s is None:
            continue
        zs.append(diff[sl][lab[sl] == cid] / s)
    if not zs:
        return float(k_floor)
    z = np.concatenate(zs)

    k = float(k_floor)
    while k <= k_ceiling:
        pos = int((z > k).sum())
        neg = int((z < -k).sum())
        if pos >= 20 and neg <= fdr * pos:
            return float(k)
        k += step
    return float(k_ceiling)


def auto_extract(
    image: np.ndarray,
    cell_labels: np.ndarray,
    *,
    smallest_particle_px: float | None = None,
    fill_factor: float = FILL_FACTOR,
    fdr: float = FDR,
    min_sigma_small: float = MIN_SIGMA_SMALL,
    log_presmooth: float = LOG_PRESMOOTH,
    presmooth_sigma_px: float = 1.0,
    min_spot_px: int = 2,
    size_percentile: float = SIZE_PERCENTILE,
    max_sigma: float = MAX_SIGMA,
    fill_holes: bool = True,
) -> tuple[np.ndarray, AutoExtractReport]:
    """Two-pass automated extraction of all foci. Returns ``(mask uint8, report)``.

    By default **both** ends are measured from the image (LoG): pass
    ``smallest_particle_px=None`` (default) to autodetect the fine window from the
    smallest particle (LoG p5 @ ``min_sigma_small``, used *directly* as the window
    since the LoG size of a small particle already ≈ 3× its true diameter); supply
    a true diameter to set the fine window ``= fill_factor × smallest`` instead.
    The largest particle is always measured by LoG (``size_percentile``) and sets
    the coarse window ``= fill_factor × largest`` at ``k =`` the noise-symmetry
    floor. The coarse pass runs only when its window exceeds the fine window; the
    passes are OR-unioned (with per-pass hole-filling), and ``min_spot_px`` filters
    the union once at the end. Detection is per-cell (the per-cell σ is the basis
    of the method). LoG **sizing** uses the fixed ``log_presmooth`` (decoupled from
    the detection ``presmooth_sigma_px``). Raises :class:`ValueError` when
    smallest autodetection finds no blobs (supply ``smallest_particle_px`` instead).

    The two ends are not equally reliable: the largest is bright/isolated so LoG
    sizes it robustly; the smallest is partly confounded with noise, so the
    autodetected fine window is best read as an estimate of the resolution-limited
    fine window — supplying your known optical resolution is more defensible.
    """
    img = np.asarray(image, dtype=np.float32)
    lab = np.asarray(cell_labels)

    # ---- resolve the FINE window ----
    if smallest_particle_px is not None:
        fine_window = _win(fill_factor * float(smallest_particle_px))
        smallest_diameter_px = float(smallest_particle_px)
        smallest_source = f"supplied Ø {float(smallest_particle_px):g} px"
    else:
        d_small = measure_smallest_particle_diameter(
            img, lab, min_sigma=min_sigma_small,
            presmooth_sigma_px=log_presmooth, max_sigma=max_sigma,
        )
        if d_small <= 0:
            raise ValueError(
                "smallest-particle autodetection found no blobs; supply a smallest "
                "particle Ø (turn off Auto-detect smallest) instead"
            )
        # The fine window follows the same no-hole rule as the coarse pass and the
        # manual path: window = fill_factor × the measured smallest diameter.
        fine_window = _win(fill_factor * d_small)
        smallest_diameter_px = float(d_small)
        smallest_source = (
            f"auto LoG p{SMALL_PERCENTILE:g} @ min_sigma={min_sigma_small:g} "
            f"({d_small:.1f} px → window {fill_factor:g}×{d_small:.1f}={fine_window})"
        )

    # Shared presmoothed buffer + per-cell σ — the noise floor uses these so its
    # z-scores match what detect_adaptive_per_cell thresholds.
    work = apply_gaussian_smoothing(img, presmooth_sigma_px)
    sigma = per_cell_sigma(work, lab)

    # ---- pass 1: fine, k = 1 ----
    mask = detect_adaptive_per_cell(
        img, lab, window_px=fine_window, min_spot_px=1, k=1.0,
        presmooth_sigma_px=presmooth_sigma_px, fill_holes=fill_holes,
    ).astype(bool)
    passes: list[tuple[int, float]] = [(int(fine_window), 1.0)]

    # ---- resolve the coarse window (always LoG-measured, fixed log_presmooth) ----
    largest_px = measure_largest_particle_diameter(
        img, lab, percentile=size_percentile,
        presmooth_sigma_px=log_presmooth, max_sigma=max_sigma,
    )
    coarse_window = _win(fill_factor * largest_px)

    # ---- pass 2: only if the largest particle exceeds what the fine window fills ----
    second = coarse_window > fine_window
    if second:
        k_coarse = noise_symmetry_floor_k(
            work, lab, sigma, coarse_window, fdr=fdr, k_floor=1.0,
        )
        mask |= detect_adaptive_per_cell(
            img, lab, window_px=coarse_window, min_spot_px=1, k=k_coarse,
            presmooth_sigma_px=presmooth_sigma_px, fill_holes=fill_holes,
        ).astype(bool)
        passes.append((int(coarse_window), round(float(k_coarse), 2)))

    mask = _filter_by_area(mask, int(min_spot_px))
    out = mask.astype(np.uint8)

    _, ncomp = label(mask)
    report = AutoExtractReport(
        passes=passes,
        fine_window=int(fine_window),
        largest_particle_px=float(largest_px) if second else (float(largest_px) or None),
        second_pass_used=bool(second),
        presmooth_sigma_px=float(presmooth_sigma_px),
        n_cells=len(sigma),
        n_components=int(ncomp),
        area_px=int(mask.sum()),
        smallest_diameter_px=float(smallest_diameter_px),
        smallest_source=smallest_source,
        extra={"fdr": float(fdr)},
    )
    return out, report
