"""Pure two-pass per-group puncta detection pipeline.

:func:`detect_two_pass` implements, for ONE intensity group, the headless
two-pass spot detection that replaces per-group whole-cell Otsu:

1. **Per-group isolation** — out-of-group pixels are set to NaN so the
   multiscale detector's ``threshold_rel`` / LoG response normalize *within*
   the group, not across the field (the cross-group class-imbalance fix).
2. **Pass 1 (once)** — a deliberately-permissive ``seed_detector`` runs on a
   bootstrap residual (``smoothed - robust_mu``) to seed background sampling.
3. **Background estimate** — a fallback ladder: the configured estimator, then
   the robust ``gaussian-peak`` rung (never a plain mean over foci-contaminated
   group pixels), then an empty mask.
4. **Signal-presence gate** — short-circuits to an empty mask when nothing in
   the group clears ``k * sigma`` (never accept-all). It only fires when the
   *brightest* in-group residual fails the floor, so it never vetoes a
   detection the active detector would otherwise produce.
5. **Pass 2** — the configured detector on the per-group-isolated residual at
   the (possibly refined) ``scale_range``.
6. **Size filter** — drops connected components outside ``[min_spot_px,
   max_spot_px]``.

Always returns a ``group_label_mask.shape`` ``uint8`` ``{0, 1}`` array — never
``None`` (so the time-lapse ``np.stack`` in the caller cannot crash).

Pure: ``numpy`` / ``scipy`` / ``skimage`` only (via the two registries). No Qt,
napari, h5py, or store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from percell4.domain.analysis._impl._shared import label_and_filter
from percell4.domain.analysis._impl.per_particle_donut import estimate_bg_threshold
from percell4.domain.measure.bg_estimators import BACKGROUND_ESTIMATORS
from percell4.domain.measure.puncta_detectors import DETECTORS

if TYPE_CHECKING:
    # Type-only reference; the pipeline duck-types ``settings`` at runtime so
    # the pure domain layer never imports the workflows layer (no back-edge,
    # no import cycle).
    from percell4.workflows.models import PunctaDetectorSettings

# Bootstrap scale range used on the very first run when no spot_scale_prior is
# locked. Plan U6 refines this per dataset (narrow-only).
DEFAULT_SCALE_RANGE: tuple[float, float] = (1.0, 4.0)

# Minimum finite group pixels for the robust gaussian-peak bootstrap fit.
_MIN_BOOTSTRAP_PX = 10


def _robust_bg(values: np.ndarray) -> tuple[float, float]:
    """Robust background ``mu`` and noise ``sigma`` for the bootstrap pass-1.

    Uses the gaussian-peak fit (robust to a bright-foci tail) when there are
    enough finite pixels; otherwise a median / MAD fallback.
    """
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0, 0.0
    if finite.size < _MIN_BOOTSTRAP_PX:
        med = float(np.median(finite))
        mad = float(np.median(np.abs(finite - med)))
        return med, 1.4826 * mad
    _thr, mu, sigma = estimate_bg_threshold(finite)
    return float(mu), float(sigma)


def _isolate(image: np.ndarray, group_mask: np.ndarray) -> np.ndarray:
    """Per-group isolation: out-of-group pixels -> NaN."""
    iso = image.astype(np.float64, copy=True)
    iso[~group_mask] = np.nan
    return iso


def _params_with_scale(
    params: tuple[tuple[str, object], ...] | dict,
    scale_range: tuple[float, float],
) -> dict:
    """Convert a canonical param tuple to a dict and inject the scale range."""
    out = dict(params)
    out.setdefault("min_sigma", float(scale_range[0]))
    out.setdefault("max_sigma", float(scale_range[1]))
    return out


def _size_filter(
    mask: np.ndarray, min_spot_px: int, max_spot_px: int | None
) -> np.ndarray:
    """Drop connected components outside ``[min_spot_px, max_spot_px]`` area."""
    if min_spot_px <= 1 and max_spot_px is None:
        return mask
    from skimage import measure

    lab = measure.label(mask > 0)
    out = np.zeros_like(mask)
    for prop in measure.regionprops(lab):
        if prop.area < min_spot_px:
            continue
        if max_spot_px is not None and prop.area > max_spot_px:
            continue
        out[lab == prop.label] = 1
    return out


def detect_two_pass(
    smoothed_image: np.ndarray,
    group_label_mask: np.ndarray,
    settings: PunctaDetectorSettings,
    scale_range: tuple[float, float] | None = None,
    seeds: tuple | None = None,
) -> np.ndarray:
    """Two-pass spot detection for one intensity group.

    Parameters
    ----------
    smoothed_image:
        The already-smoothed float channel image (full frame).
    group_label_mask:
        Boolean array selecting this group's cells.
    settings:
        The locked :class:`PunctaDetectorSettings` for the round.
    scale_range:
        The (possibly per-dataset refined) ``(min_sigma, max_sigma)``. When
        ``None``, the locked ``spot_scale_prior`` is used, falling back to
        :data:`DEFAULT_SCALE_RANGE` on cold start.
    seeds:
        Optional pre-computed pass-1 seeds ``(label_mask, binary_mask,
        region_ids, props_by_id)`` from a calibration pre-pass; when supplied,
        pass-1 is not re-run (so pass-1 executes exactly once per group).

    Returns
    -------
    A ``group_label_mask.shape`` ``uint8`` mask with values in ``{0, 1}``.
    """
    shape = group_label_mask.shape
    empty = np.zeros(shape, dtype=np.uint8)
    if not group_label_mask.any():
        return empty

    if scale_range is None:
        scale_range = settings.spot_scale_prior or DEFAULT_SCALE_RANGE

    # PASS 1 (once): permissive seed detector on the bootstrap residual.
    if seeds is None:
        mu_boot, sigma_boot = _robust_bg(smoothed_image[group_label_mask])
        boot_residual = _isolate(smoothed_image - mu_boot, group_label_mask)
        seed_detector = DETECTORS[settings.seed_detector_name]
        seed_params = _params_with_scale(settings.seed_params, scale_range)
        seed_mask = seed_detector(
            boot_residual, group_label_mask, sigma_boot, seed_params
        )
        seeds = label_and_filter(seed_mask, 0)

    # BACKGROUND estimate — fallback ladder: configured -> gaussian-peak -> empty.
    bg_params = dict(settings.detector_params)
    est = BACKGROUND_ESTIMATORS[settings.background_estimator_name](
        smoothed_image, group_label_mask, seeds, bg_params
    )
    if est.is_empty and settings.background_estimator_name != "gaussian-peak":
        est = BACKGROUND_ESTIMATORS["gaussian-peak"](
            smoothed_image, group_label_mask, None, {}
        )
    if est.is_empty:
        return empty

    residual = _isolate(est.residual, group_label_mask)

    # SIGNAL-PRESENCE GATE — never accept-all, never vetoes a real detection.
    # Only short-circuits when the brightest in-group residual fails k*sigma,
    # i.e. nothing stands out at all. Skipped when sigma is unknown (the
    # detector's own criterion then decides).
    if est.sigma is not None and est.sigma > 0:
        params = dict(settings.detector_params)
        k_gate = float(params.get("k_gate", params.get("k", 2.5)))
        grp = residual[group_label_mask]
        grp = grp[np.isfinite(grp)]
        if grp.size == 0 or float(np.max(grp)) <= k_gate * est.sigma:
            return empty

    # PASS 2: configured detector on the per-group-isolated residual.
    detector = DETECTORS[settings.detector_name]
    det_params = _params_with_scale(settings.detector_params, scale_range)
    mask = detector(residual, group_label_mask, est.sigma, det_params)
    mask = _size_filter(mask, settings.min_spot_px, settings.max_spot_px)
    return (mask > 0).astype(np.uint8)
