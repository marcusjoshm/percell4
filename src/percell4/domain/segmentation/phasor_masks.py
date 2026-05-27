"""Phasor ellipse fit + dual-threshold spatial masks.

Pure-domain helper backing the Automated Phasor-Masks Workflow
(``docs/plans/2026-05-27-001-feat-phasor-masks-workflow-plan.md``) and the
shared-ROI extension
(``docs/plans/2026-05-27-002-feat-phasor-masks-shared-roi-plan.md``).

This module exposes three public functions:

- :func:`fit_phasor_ellipse` — fit a single-component phasor ellipse to a
  Gaussian blob in ``(g, s)``. Returns :class:`PhasorEllipseFit`.
- :func:`apply_ellipse_masks` — apply a previously-fitted ellipse to a
  (potentially different) phasor map and produce two intensity-thresholded
  spatial masks. Returns :class:`PhasorEllipseMasks`.
- :func:`fit_phasor_ellipse_and_apply_masks` — thin facade that composes
  the two operations on the same dataset, returning the existing
  :class:`PhasorEllipseMasksResult` shape unchanged for backward
  compatibility.

The decomposition is what makes the shared-ROI workflow possible: U2's
interleaved per-source-group loop calls :func:`fit_phasor_ellipse` on the
source dataset, caches the resulting :class:`PhasorEllipseFit`, then calls
:func:`apply_ellipse_masks` on each dependent target with the cached fit.

End-to-end pipeline (composed by the facade and by U2's loop):

1. Build the GMM-fit subset: pixels above ``t_fit`` with finite ``g``/``s``.
2. ``single_component_fit_phasor`` — closed-form intensity-weighted
   mean / covariance for n=1.
3. ``gmm_eigenstructure`` → ``gmm_to_phasor_roi_geometry`` with
   ``stretch_parallel=stretch_perpendicular=2.0`` (the FLIM panel's "fit"
   default) and ``shape="ellipse"``. No anchor parameter exists — the
   GMM mean is the implicit anchor.
4. **Degeneracy guard:** raise ``ValueError("degenerate fit (ellipse has
   zero area)")`` when either ellipse radius collapses to zero. This
   closes the silent "all-zero masks marked succeeded" bug
   (``phasor_roi_to_mask`` returns zeros for non-positive radii by
   design — without the guard, the caller has no signal that anything
   went wrong). Steps 1–4 live in :func:`fit_phasor_ellipse`.
5. ``phasor_roi_to_mask`` → boolean spatial mask of "pixel lies inside
   the ellipse on the phasor plot".
6. AND with ``intensity_map >= t_mask_a/b`` to produce the two output
   masks. Binarize at the boundary via ``(m & i).astype(np.uint8)``
   per the batch-compress-development-lessons learning. Steps 5–6 live
   in :func:`apply_ellipse_masks`.

The caller is responsible for deriving
``intensity_map = decay.sum(axis=-1)`` (the FLIM cross-layer-alignment
rule lives at the call site, not buried here — see
``docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md``).

No imports from ``qtpy``, ``napari``, ``h5py``, or
``percell4.application.session``. Pure-domain isolation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from percell4.domain.flim.phasor import (
    gmm_eigenstructure,
    gmm_to_phasor_roi_geometry,
    phasor_roi_to_mask,
    single_component_fit_phasor,
)


# The FLIM panel default ellipse stretch ("2 sigma" along each
# principal axis). Matches the GUI's GMM "fit" preset.
_DEFAULT_STRETCH = 2.0


@dataclass(frozen=True)
class PhasorEllipseFit:
    """Geometry of a fitted phasor ellipse, plus the size of the fit
    subset.

    Frozen + all-scalar fields makes this hashable-by-equality, which is
    what U2's per-(source_path, channel) ROI cache relies on.

    Attributes
    ----------
    center : ``(g, s)`` ellipse center.
    radii : ``(r_parallel, r_perpendicular)``.
    angle_deg : major-eigenvector angle in degrees.
    sampled_pixels : number of pixels that entered the fit (after the
        intensity + finite-(g, s) cut).
    """

    center: tuple[float, float]
    radii: tuple[float, float]
    angle_deg: float
    sampled_pixels: int


@dataclass
class PhasorEllipseMasks:
    """Output of :func:`apply_ellipse_masks`.

    Intentionally two fields only — no ``geometry`` (the caller already
    supplied it via the ``fit`` argument) and no ``sampled_pixels`` (the
    caller already has it on the source fit). Keeps the apply-only return
    type honest about what it actually produced.

    Not frozen because numpy arrays don't implement ``__hash__``; frozen
    dataclasses default to ``eq=True, frozen=True`` which would attempt
    to synthesise ``__hash__`` and array equality would break it.
    """

    mask_a: NDArray[np.uint8]
    mask_b: NDArray[np.uint8]


@dataclass
class PhasorEllipseMasksResult:
    """Output of :func:`fit_phasor_ellipse_and_apply_masks`.

    Attributes
    ----------
    geometry : ``(center, radii, angle_deg)`` triple from
        :func:`gmm_to_phasor_roi_geometry`. ``center`` is ``(g, s)``,
        ``radii`` is ``(r_parallel, r_perpendicular)``, ``angle_deg``
        is the major-eigenvector angle in degrees.
    mask_a : ``uint8`` spatial mask filtered at ``t_mask_a``. Shape
        matches ``g_map``.
    mask_b : ``uint8`` spatial mask filtered at ``t_mask_b``. Shape
        matches ``g_map``.
    sampled_pixels : number of pixels that entered the fit (after
        the intensity + finite-(g,s) cut).
    """

    geometry: tuple[tuple[float, float], tuple[float, float], float]
    mask_a: NDArray[np.uint8]
    mask_b: NDArray[np.uint8]
    sampled_pixels: int


def fit_phasor_ellipse(
    g_map: NDArray[np.floating],
    s_map: NDArray[np.floating],
    intensity_map: NDArray[np.floating],
    *,
    t_fit: float,
) -> PhasorEllipseFit:
    """Fit a single-component phasor ellipse.

    Steps 1–4 of the combined pipeline: build the fit subset, run the
    closed-form n=1 fit, compute eigenstructure → ROI geometry, and
    guard against degenerate (zero-area) ellipses.

    Parameters
    ----------
    g_map, s_map : (H, W) phasor coordinate maps. NaN pixels are
        excluded from the fit subset.
    intensity_map : (H, W) intensity used to gate the fit subset
        (``intensity_map >= t_fit``). Must share shape with ``g_map``.
    t_fit : intensity threshold for the GMM fit subset.

    Returns
    -------
    :class:`PhasorEllipseFit`

    Raises
    ------
    ValueError
        Propagated from :func:`single_component_fit_phasor` when the
        fit subset is empty ("Cannot fit single component on empty
        input").
    ValueError
        ``"degenerate fit (ellipse has zero area)"`` when the fitted
        ellipse collapses on either axis (single-pixel subset, two
        collinear pixels, or otherwise rank-deficient covariance).
    """
    g = np.asarray(g_map)
    s = np.asarray(s_map)
    intensity = np.asarray(intensity_map)

    if g.shape != s.shape or g.shape != intensity.shape:
        raise ValueError("g_map, s_map, intensity_map must share shape")

    # ── Step 1: fit subset ─────────────────────────────────────────────
    finite = np.isfinite(g) & np.isfinite(s)
    fit_selector = finite & (intensity >= t_fit)

    g_subset = g[fit_selector]
    s_subset = s[fit_selector]
    intensity_subset = intensity[fit_selector]

    # ── Step 2: closed-form n=1 fit (raises on empty input) ────────────
    fit = single_component_fit_phasor(g_subset, s_subset, intensity_subset)

    # ── Step 3: eigenstructure → ROI geometry ──────────────────────────
    mean_g = float(fit.means[0, 0])
    mean_s = float(fit.means[0, 1])
    cov = np.asarray(fit.covariances[0], dtype=np.float64)

    # ── Step 4: degeneracy guard ───────────────────────────────────────
    # The plan calls for raising when ``radii[0] <= 0 or radii[1] <= 0``.
    # In practice ``gmm_eigenstructure`` clamps both eigenvalues to a
    # small positive floor (so callers like the FLIM panel still draw a
    # visible — if tiny — ROI on a degenerate cluster), which means the
    # ``<= 0`` boundary never actually triggers in the live primitive.
    # We therefore detect degeneracy at its source: a singular (or
    # near-singular) source covariance. Both pathological cases the plan
    # enumerates — a single sampled pixel (cov == zeros) and two
    # collinear pixels (rank-deficient cov) — collapse the determinant
    # to numerical zero. The check is conservative: a healthy
    # 2D-Gaussian fit produces det(cov) >> 1e-30.
    #
    # Without this guard, ``phasor_roi_to_mask`` would return a tiny
    # ellipse containing only the originating pixel(s), and the caller
    # would treat the resulting (effectively all-zero) masks as a
    # successful "no pixels match" outcome. See the plan's risk table.
    det_cov = float(np.linalg.det(cov))
    if not np.isfinite(det_cov) or det_cov <= 1e-30:
        raise ValueError("degenerate fit (ellipse has zero area)")

    lambda_major, lambda_minor, principal_angle_rad = gmm_eigenstructure(cov)
    center, radii, angle_deg = gmm_to_phasor_roi_geometry(
        mean=(mean_g, mean_s),
        lambda_major=lambda_major,
        lambda_minor=lambda_minor,
        principal_angle_rad=principal_angle_rad,
        stretch_parallel=_DEFAULT_STRETCH,
        stretch_perpendicular=_DEFAULT_STRETCH,
        shift_parallel=0.0,
        shift_perpendicular=0.0,
        shape="ellipse",
    )

    # Belt-and-suspenders: the plan's literal radii check. If the
    # primitive's floor is ever lowered or removed, the determinant
    # gate above might still admit a borderline case; this catches
    # any remaining zero-area ellipse before it reaches the mask
    # primitive.
    if radii[0] <= 0 or radii[1] <= 0:
        raise ValueError("degenerate fit (ellipse has zero area)")

    return PhasorEllipseFit(
        center=center,
        radii=radii,
        angle_deg=angle_deg,
        sampled_pixels=fit.sampled_pixels,
    )


def apply_ellipse_masks(
    g_map: NDArray[np.floating],
    s_map: NDArray[np.floating],
    intensity_map: NDArray[np.floating],
    fit: PhasorEllipseFit,
    *,
    t_mask_a: float,
    t_mask_b: float,
) -> PhasorEllipseMasks:
    """Apply a fitted phasor ellipse to a (potentially different) phasor
    map and produce two intensity-thresholded spatial masks.

    Steps 5–6 of the combined pipeline: phasor → spatial ellipse-
    membership mask, AND with intensity thresholds, binarize as uint8.
    Trusts the supplied ``fit`` — does not re-validate ellipse
    degeneracy (that is a fit-time concern enforced by
    :func:`fit_phasor_ellipse`).

    The shared-ROI workflow uses this entry point to apply a source
    dataset's fitted ellipse against each dependent target's own phasor
    map: same ROI geometry, different ``(g_map, s_map, intensity_map)``.

    Parameters
    ----------
    g_map, s_map : (H, W) phasor coordinate maps. NaN pixels are
        excluded from both output masks (see ``phasor_roi_to_mask``).
    intensity_map : (H, W) intensity used for per-mask thresholding
        (``>= t_mask_a/b``). Must share shape with ``g_map``.
    fit : the :class:`PhasorEllipseFit` to apply.
    t_mask_a, t_mask_b : intensity thresholds for the two output masks.

    Returns
    -------
    :class:`PhasorEllipseMasks`
    """
    g = np.asarray(g_map)
    s = np.asarray(s_map)
    intensity = np.asarray(intensity_map)

    if g.shape != s.shape or g.shape != intensity.shape:
        raise ValueError("g_map, s_map, intensity_map must share shape")

    # ── Step 5: phasor → spatial ellipse-membership mask ───────────────
    ellipse_mask = phasor_roi_to_mask(
        g, s,
        center=fit.center,
        radii=fit.radii,
        angle_rad=float(np.radians(fit.angle_deg)),
    )

    # ── Step 6: AND with intensity thresholds, binarize as uint8 ───────
    i_a = intensity >= t_mask_a
    i_b = intensity >= t_mask_b

    mask_a = (ellipse_mask & i_a).astype(np.uint8)
    mask_b = (ellipse_mask & i_b).astype(np.uint8)

    return PhasorEllipseMasks(mask_a=mask_a, mask_b=mask_b)


def fit_phasor_ellipse_and_apply_masks(
    g_map: NDArray[np.floating],
    s_map: NDArray[np.floating],
    intensity_map: NDArray[np.floating],
    *,
    t_fit: float,
    t_mask_a: float,
    t_mask_b: float,
) -> PhasorEllipseMasksResult:
    """Fit a single-component phasor ellipse and produce two
    intensity-thresholded spatial masks against the same dataset.

    Thin facade composing :func:`fit_phasor_ellipse` and
    :func:`apply_ellipse_masks`. Existing callers see the unchanged
    :class:`PhasorEllipseMasksResult` shape; new callers (U2's shared-
    ROI loop) call the two underlying functions directly so fit and
    apply can target different datasets.

    Parameters
    ----------
    g_map, s_map : (H, W) phasor coordinate maps. NaN pixels are
        excluded from the fit AND from both output masks (see
        ``phasor_roi_to_mask`` for the latter half).
    intensity_map : (H, W) intensity used for both the fit subset
        (``intensity_map >= t_fit``) and per-mask intensity filtering
        (``>= t_mask_a/b``). Must share shape with ``g_map``.
    t_fit : intensity threshold for the GMM fit subset.
    t_mask_a, t_mask_b : intensity thresholds for the two output masks.

    Returns
    -------
    :class:`PhasorEllipseMasksResult`

    Raises
    ------
    ValueError
        Propagated from :func:`fit_phasor_ellipse` (empty fit subset
        or degenerate ellipse).
    """
    fit = fit_phasor_ellipse(g_map, s_map, intensity_map, t_fit=t_fit)
    masks = apply_ellipse_masks(
        g_map, s_map, intensity_map, fit,
        t_mask_a=t_mask_a,
        t_mask_b=t_mask_b,
    )
    return PhasorEllipseMasksResult(
        geometry=(fit.center, fit.radii, fit.angle_deg),
        mask_a=masks.mask_a,
        mask_b=masks.mask_b,
        sampled_pixels=fit.sampled_pixels,
    )
