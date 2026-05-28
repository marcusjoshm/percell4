"""Per-particle donut background subtraction — pure-domain implementation.

This module is the single source of truth for the donut math used by:

* The repo-root CLI ``per_particle_analysis.py`` (the thin I/O wrapper).
* The registered framework analysis ``PerParticleDonut`` (U5).

``run_one_image_set`` is the public entry point. Given pre-loaded numpy
arrays (Cap intensity, optional P-body mask + pnorm, optional SG mask
+ sgnorm, optional cp_mask labels) and analysis parameters, it
returns a dict with per-particle rows for P-bodies and/or stress
granules and — when requested — the donut masks themselves.

The function performs no file I/O. The Cap background-subtraction
NaN'ing of low-signal pixels happens inside (controlled by
``no_bgsub`` and ``bgsub_k``) so that the CLI path and the framework
path remain numerically identical.

References:
* Plan: ``docs/plans/2026-05-27-004-feat-analysis-integration-plan.md``
  (U4 extracts this from the CLI; U5 wraps it in an ``Analysis``).
* Pattern: ``src/percell4/domain/segmentation/phasor_masks.py``.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

import numpy as np
import pandas as pd
from scipy.ndimage import distance_transform_edt
from scipy.optimize import curve_fit
from skimage import measure


# ---------------------------------------------------------------------------
# Background-threshold estimation (Cap channel, pre-region analysis)
# ---------------------------------------------------------------------------


def _estimate_bg_hwhm(flat: np.ndarray, k_sigma: float,
                      log: Callable[[str], None] | None = None
                      ) -> tuple[float, float, float]:
    """Fallback background estimator for low-signal images.

    Uses unit-width histogram bins to find the mode, then derives sigma
    from the half-width at half-maximum (HWHM) of the background peak.
    """
    upper = max(np.percentile(flat, 75), 20)
    hist, edges = np.histogram(flat, bins=np.arange(0, upper + 1, 1))
    centers = (edges[:-1] + edges[1:]) / 2

    mode_idx = np.argmax(hist)
    mode_val = centers[mode_idx]
    half_max = hist[mode_idx] / 2.0

    right_side = hist[mode_idx:]
    hwhm_idx = np.argmax(right_side < half_max)
    if hwhm_idx == 0:
        hwhm_idx = len(right_side) - 1
    # HWHM -> sigma: FWHM = 2.355*sigma, so HWHM = 1.177*sigma
    sigma = hwhm_idx / 1.177

    mu = mode_val
    threshold = mu + k_sigma * sigma
    if log is not None:
        log(f"  [bg-fallback HWHM] mode={mu:.1f}, sigma={sigma:.1f}, "
            f"threshold={threshold:.1f}")
    return threshold, mu, sigma


def estimate_bg_threshold(cap_img: np.ndarray, k_sigma: float = 2.5,
                          log: Callable[[str], None] | None = None
                          ) -> tuple[float, float, float]:
    """Estimate a background threshold by fitting a Gaussian to the
    background peak of the Cap image.

    Returns ``(threshold, mu, sigma)`` where pixels with Cap value below
    ``threshold`` are considered background (nucleus, cell borders,
    outside cell). Falls back to a HWHM-based estimator when the image
    is too low-signal for reliable Gaussian fitting.
    """
    flat = cap_img[~np.isnan(cap_img)]
    bins = np.arange(0, np.percentile(flat, 50), 5)
    if len(bins) < 10:
        bins = np.arange(0, np.percentile(flat, 75), 5)

    if len(bins) < 3:
        return _estimate_bg_hwhm(flat, k_sigma, log)

    hist, edges = np.histogram(flat, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2

    mode_idx = np.argmax(hist)
    mode_val = centers[mode_idx]

    def _gaussian(x: np.ndarray, amp: float, mu: float, sigma: float
                  ) -> np.ndarray:
        return amp * np.exp(-(x - mu) ** 2 / (2 * sigma ** 2))

    fit_range = centers <= mode_val * 2.5
    if np.sum(fit_range) < 4:
        return _estimate_bg_hwhm(flat, k_sigma, log)

    try:
        popt, _ = curve_fit(
            _gaussian, centers[fit_range], hist[fit_range],
            p0=[hist[mode_idx], mode_val, mode_val * 0.5],
            maxfev=10000,
        )
    except (TypeError, RuntimeError):
        return _estimate_bg_hwhm(flat, k_sigma, log)

    mu, sigma = popt[1], abs(popt[2])
    threshold = mu + k_sigma * sigma
    return threshold, mu, sigma


# ---------------------------------------------------------------------------
# Per-region analysis (donut background subtraction)
# ---------------------------------------------------------------------------


def analyze_regions(*, mask_img: np.ndarray, cap_img: np.ndarray,
                    norm_img: np.ndarray, buffer_px: int, donut_px: int,
                    bg_mode: str, flat_bg_value: int, min_size: int,
                    exclude_cap_zero: bool = True,
                    region_label: str = 'pbody', norm_label: str = 'pnorm',
                    capture_donut_mask: bool = False,
                    log: Callable[[str], None] | None = None
                    ) -> dict[str, Any]:
    """Analyze one set of regions (P-bodies or SGs) and return per-particle
    measurements plus, optionally, the union donut mask.

    Parameters
    ----------
    mask_img : ndarray
        Binary mask array (P-body_mask or SG_mask). Caller is responsible
        for reading the TIFF and passing the numpy array here.
    cap_img : ndarray
        Cap intensity image (float64), potentially with NaN exclusions
        already applied by the caller (Cap bg subtraction, SG exclusion).
    norm_img : ndarray
        Normalization channel image (float64) — pnorm or sgnorm.
    region_label : str
        Label for output columns — 'pbody' or 'sg'.
    norm_label : str
        Label for normalization channel in output columns — 'pnorm' or
        'sgnorm'.
    capture_donut_mask : bool
        When True the returned dict contains ``donut_mask`` — a uint8
        array (255 in donut pixels, 0 elsewhere). When False the
        returned dict has ``donut_mask=None``.

    Returns
    -------
    dict
        ``{"rows": list[dict], "donut_mask": np.ndarray | None,
           "n_found": int}``. ``n_found`` is the number of regions that
        survived the ``min_size`` filter (useful for the CLI's progress
        prints) — distinct from ``len(rows)`` which excludes regions
        whose donut sum was zero (and hence skipped).
    """
    binary_mask = mask_img > 0
    label_mask = measure.label(binary_mask)
    region_ids = np.unique(label_mask)
    region_ids = region_ids[region_ids != 0]

    props = measure.regionprops(label_mask)
    props_by_id = {p.label: p for p in props}

    if min_size > 0:
        region_ids = np.array(
            [rid for rid in region_ids if props_by_id[rid].area > min_size]
        )

    if log is not None:
        if bg_mode == 'flat':
            bg_label_str = f"flat={flat_bg_value}"
        elif bg_mode == 'donut-mean':
            bg_label_str = "donut-mean-estimated"
        else:
            bg_label_str = "donut-median-estimated"
        log(f"  Found {len(region_ids)} {region_label}s > {min_size} px "
            f"(bg: {bg_label_str})")

    h, w = mask_img.shape
    pad = buffer_px + donut_px + 1

    donut_export = (
        np.zeros((h, w), dtype=np.uint8) if capture_donut_mask else None
    )

    results: list[dict[str, Any]] = []
    for region_id in region_ids:
        bbox = props_by_id[region_id].bbox
        r0 = max(bbox[0] - pad, 0)
        c0 = max(bbox[1] - pad, 0)
        r1 = min(bbox[2] + pad, h)
        c1 = min(bbox[3] + pad, w)

        crop_label = label_mask[r0:r1, c0:c1]
        crop_binary = binary_mask[r0:r1, c0:c1]
        crop_region = crop_label == region_id

        dist_from_this = distance_transform_edt(~crop_region)
        in_donut_range = (
            (dist_from_this > buffer_px)
            & (dist_from_this <= buffer_px + donut_px)
        )
        crop_donut = in_donut_range & ~crop_binary

        region_mask = label_mask == region_id
        donut_mask = np.zeros_like(binary_mask)
        donut_mask[r0:r1, c0:c1] = crop_donut

        if donut_mask.sum() == 0:
            continue

        if donut_export is not None:
            donut_export[donut_mask] = 255

        cap_donut_raw = cap_img[donut_mask]

        cap_donut_for_bg = cap_donut_raw[~np.isnan(cap_donut_raw)]
        if exclude_cap_zero and len(cap_donut_for_bg) > 0:
            nonzero = cap_donut_for_bg[cap_donut_for_bg != 0]
            if len(nonzero) > 0:
                cap_donut_for_bg = nonzero
        if len(cap_donut_for_bg) == 0:
            cap_donut_for_bg = cap_donut_raw[~np.isnan(cap_donut_raw)]
        if len(cap_donut_for_bg) == 0:
            continue

        if bg_mode == 'donut':
            bg_value = int(np.round(np.median(cap_donut_for_bg)))
        elif bg_mode == 'donut-mean':
            bg_value = int(np.round(np.mean(cap_donut_for_bg)))
        else:
            bg_value = flat_bg_value

        cap_region_sub = np.maximum(cap_img[region_mask] - bg_value, 0)
        cap_donut_sub = np.maximum(cap_donut_raw - bg_value, 0)

        cap_region_raw = cap_img[region_mask]
        cap_region_sub[np.isnan(cap_region_raw)] = np.nan
        cap_donut_sub[np.isnan(cap_donut_raw)] = np.nan

        cap_region_raw_mean = (
            np.nanmean(cap_region_raw) if np.any(~np.isnan(cap_region_raw))
            else np.nan
        )
        cap_donut_raw_mean = (
            np.nanmean(cap_donut_raw) if np.any(~np.isnan(cap_donut_raw))
            else np.nan
        )
        cap_region_raw_integ = (
            np.nansum(cap_region_raw) if np.any(~np.isnan(cap_region_raw))
            else np.nan
        )
        cap_donut_raw_integ = (
            np.nansum(cap_donut_raw) if np.any(~np.isnan(cap_donut_raw))
            else np.nan
        )

        cap_region_mean = (
            np.nanmean(cap_region_sub) if np.any(~np.isnan(cap_region_sub))
            else np.nan
        )
        cap_donut_mean = (
            np.nanmean(cap_donut_sub) if np.any(~np.isnan(cap_donut_sub))
            else np.nan
        )
        cap_region_integ = (
            np.nansum(cap_region_sub) if np.any(~np.isnan(cap_region_sub))
            else np.nan
        )
        cap_donut_integ = (
            np.nansum(cap_donut_sub) if np.any(~np.isnan(cap_donut_sub))
            else np.nan
        )
        norm_region_mean = (
            np.mean(norm_img[region_mask]) if region_mask.sum() > 0
            else np.nan
        )
        norm_donut_mean = (
            np.mean(norm_img[donut_mask]) if donut_mask.sum() > 0
            else np.nan
        )
        norm_region_integ = (
            np.sum(norm_img[region_mask]) if region_mask.sum() > 0
            else np.nan
        )
        norm_donut_integ = (
            np.sum(norm_img[donut_mask]) if donut_mask.sum() > 0
            else np.nan
        )

        def _ratio(a: float, b: float) -> float:
            return (a / b if (b and not np.isnan(a) and not np.isnan(b)
                              and b != 0) else np.nan)

        rl = region_label
        nl = norm_label
        results.append({
            f'{rl}_id': int(region_id),
            f'{rl}_area_px': int(region_mask.sum()),
            'donut_area_px': int(donut_mask.sum()),
            'bg_value': bg_value,
            f'cap_{rl}_raw_mean': cap_region_raw_mean,
            'cap_dilute_raw_mean': cap_donut_raw_mean,
            f'cap_{rl}_raw_integ': cap_region_raw_integ,
            'cap_dilute_raw_integ': cap_donut_raw_integ,
            f'cap_{rl}_mean': cap_region_mean,
            'cap_dilute_mean': cap_donut_mean,
            f'cap_{rl}_integ': cap_region_integ,
            'cap_dilute_integ': cap_donut_integ,
            f'{nl}_{rl}_mean': norm_region_mean,
            f'{nl}_dilute_mean': norm_donut_mean,
            f'{nl}_{rl}_integ': norm_region_integ,
            f'{nl}_dilute_integ': norm_donut_integ,
            f'cap_{rl}_over_dilute': _ratio(cap_region_mean, cap_donut_mean),
            f'{nl}_{rl}_over_dilute': _ratio(norm_region_mean,
                                             norm_donut_mean),
            f'cap_over_{nl}_{rl}': _ratio(cap_region_mean, norm_region_mean),
            f'cap_over_{nl}_dilute': _ratio(cap_donut_mean, norm_donut_mean),
        })

    return {
        "rows": results,
        "donut_mask": donut_export,
        "n_found": int(len(region_ids)),
    }


# ---------------------------------------------------------------------------
# Single-cell aggregation
# ---------------------------------------------------------------------------


def assign_particles_to_cells(mask_img: np.ndarray, cp_mask_img: np.ndarray,
                              min_size: int) -> dict[int, int]:
    """Return dict mapping particle label ID → cell ID (majority of pixels).

    Caller passes the binary particle mask array directly (no file I/O
    inside).
    """
    label_mask = measure.label(mask_img > 0)
    props = measure.regionprops(label_mask)

    mapping: dict[int, int] = {}
    for p in props:
        if min_size > 0 and p.area <= min_size:
            continue
        particle_pixels = label_mask == p.label
        cell_values = cp_mask_img[particle_pixels]
        cell_values = cell_values[cell_values != 0]
        if len(cell_values) == 0:
            continue
        mapping[p.label] = int(np.bincount(cell_values).argmax())
    return mapping


def aggregate_by_cell(particle_results: list[dict[str, Any]],
                      particle_to_cell: dict[int, int],
                      cp_mask_img: np.ndarray,
                      region_label: str, norm_label: str
                      ) -> list[dict[str, Any]]:
    """Aggregate per-particle results into per-cell rows.

    Particles are grouped by assigned cell ID. Means are area-weighted,
    integrated intensities are summed, ratios are recomputed from the
    aggregated means. Cells with no particles get NaN for all metrics.
    """
    rl = region_label
    nl = norm_label

    cell_particles: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for result in particle_results:
        pid = result[f'{rl}_id']
        if pid in particle_to_cell:
            cell_particles[particle_to_cell[pid]].append(result)

    all_cell_ids = np.unique(cp_mask_img)
    all_cell_ids = all_cell_ids[all_cell_ids != 0]

    metric_cols = [
        f'cap_{rl}_raw_mean', 'cap_dilute_raw_mean',
        f'cap_{rl}_raw_integ', 'cap_dilute_raw_integ',
        f'cap_{rl}_mean', 'cap_dilute_mean',
        f'cap_{rl}_integ', 'cap_dilute_integ',
        f'{nl}_{rl}_mean', f'{nl}_dilute_mean',
        f'{nl}_{rl}_integ', f'{nl}_dilute_integ',
        f'cap_{rl}_over_dilute', f'{nl}_{rl}_over_dilute',
        f'cap_over_{nl}_{rl}', f'cap_over_{nl}_dilute',
    ]

    aggregated: list[dict[str, Any]] = []
    for cell_id in all_cell_ids:
        cell_id = int(cell_id)
        cell_area = int((cp_mask_img == cell_id).sum())
        particles = cell_particles.get(cell_id, [])

        if not particles:
            row: dict[str, Any] = {
                'cell_id': cell_id,
                'cell_area_px': cell_area,
                f'n_{rl}s': 0,
                f'total_{rl}_area_px': 0,
                'total_donut_area_px': 0,
                'mean_bg_value': np.nan,
            }
            for col in metric_cols:
                row[col] = np.nan
            aggregated.append(row)
            continue

        df_p = pd.DataFrame(particles)
        total_region_area = df_p[f'{rl}_area_px'].sum()
        total_donut_area = df_p['donut_area_px'].sum()

        def _weighted_mean(col: str, weights_col: str) -> float:
            weights = df_p[weights_col].values.astype(float)
            values = df_p[col].values.astype(float)
            valid = ~(np.isnan(values) | np.isnan(weights)) & (weights > 0)
            if not np.any(valid):
                return np.nan
            return float(
                (values[valid] * weights[valid]).sum() / weights[valid].sum()
            )

        def _ratio(a: float, b: float) -> float:
            if pd.notna(a) and pd.notna(b) and b != 0:
                return a / b
            return np.nan

        row = {
            'cell_id': cell_id,
            'cell_area_px': cell_area,
            f'n_{rl}s': len(particles),
            f'total_{rl}_area_px': int(total_region_area),
            'total_donut_area_px': int(total_donut_area),
            'mean_bg_value': float(df_p['bg_value'].mean()),
            f'cap_{rl}_raw_mean':
                _weighted_mean(f'cap_{rl}_raw_mean', f'{rl}_area_px'),
            'cap_dilute_raw_mean':
                _weighted_mean('cap_dilute_raw_mean', 'donut_area_px'),
            f'cap_{rl}_mean':
                _weighted_mean(f'cap_{rl}_mean', f'{rl}_area_px'),
            'cap_dilute_mean':
                _weighted_mean('cap_dilute_mean', 'donut_area_px'),
            f'{nl}_{rl}_mean':
                _weighted_mean(f'{nl}_{rl}_mean', f'{rl}_area_px'),
            f'{nl}_dilute_mean':
                _weighted_mean(f'{nl}_dilute_mean', 'donut_area_px'),
            f'cap_{rl}_raw_integ': float(df_p[f'cap_{rl}_raw_integ'].sum()),
            'cap_dilute_raw_integ': float(df_p['cap_dilute_raw_integ'].sum()),
            f'cap_{rl}_integ': float(df_p[f'cap_{rl}_integ'].sum()),
            'cap_dilute_integ': float(df_p['cap_dilute_integ'].sum()),
            f'{nl}_{rl}_integ': float(df_p[f'{nl}_{rl}_integ'].sum()),
            f'{nl}_dilute_integ': float(df_p[f'{nl}_dilute_integ'].sum()),
        }

        row[f'cap_{rl}_over_dilute'] = _ratio(row[f'cap_{rl}_mean'],
                                              row['cap_dilute_mean'])
        row[f'{nl}_{rl}_over_dilute'] = _ratio(row[f'{nl}_{rl}_mean'],
                                               row[f'{nl}_dilute_mean'])
        row[f'cap_over_{nl}_{rl}'] = _ratio(row[f'cap_{rl}_mean'],
                                            row[f'{nl}_{rl}_mean'])
        row[f'cap_over_{nl}_dilute'] = _ratio(row['cap_dilute_mean'],
                                              row[f'{nl}_dilute_mean'])

        aggregated.append(row)

    return aggregated


# ---------------------------------------------------------------------------
# Top-level entry: one image set
# ---------------------------------------------------------------------------


def run_one_image_set(
    *,
    cap: np.ndarray,
    pbody_mask: np.ndarray | None = None,
    pnorm: np.ndarray | None = None,
    sg_mask: np.ndarray | None = None,
    sgnorm: np.ndarray | None = None,
    cp_mask: np.ndarray | None = None,
    buffer: int,
    donut: int,
    bg_mode: str,
    bg_value: int,
    exclude_cap_zero: bool,
    min_size: int,
    bgsub_k: float,
    no_bgsub: bool,
    single_cell: bool,
    export_donuts: bool,
    set_label: str = "",
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the donut-background-subtraction analysis on one image set.

    Pure function: no file I/O, no directory walks. The caller is
    responsible for reading the TIFFs (CLI) or pulling arrays from the
    ``.h5`` (framework) and writing CSV / donut TIFFs / HDF5 layers.

    Parameters
    ----------
    cap : ndarray
        2D Cap intensity image. Will be coerced to float64 internally so
        the NaN-masking branches work regardless of input dtype.
    pbody_mask, pnorm : ndarray | None
        P-body branch inputs. Both must be present together to enable
        the P-body branch.
    sg_mask, sgnorm : ndarray | None
        SG branch inputs. Both must be present together to enable the
        SG branch.
    cp_mask : ndarray | None
        Per-cell label image. Required when ``single_cell=True``.
    buffer, donut, bg_mode, bg_value, exclude_cap_zero, min_size,
    bgsub_k, no_bgsub, single_cell, export_donuts
        See the CLI's argparse help; semantics are preserved verbatim.
    set_label : str
        Name of the image set, used only in progress messages (the
        ``{group_key}`` in the original CLI output). Ignored when
        ``log`` is ``None``.
    log : callable | None
        Optional sink for streaming progress lines (e.g. ``print``).
        When ``None`` (the default, and the framework path) the function
        is silent and side-effect-free. When provided, it receives the
        same per-step messages the original CLI streamed as it ran.

    Returns
    -------
    dict
        ``{"pbody_rows": list[dict] | None,
           "sg_rows": list[dict] | None,
           "pbody_donut_mask": np.ndarray | None,
           "sg_donut_mask": np.ndarray | None}``.

        Each ``*_rows`` is ``None`` when that branch was not enabled
        (inputs absent). When the branch was enabled but no particles
        survived the ``min_size`` filter, the value is an empty list.

        Each ``*_donut_mask`` is the uint8 union of donut pixels (255
        in donut, 0 elsewhere). Returned only when both that branch is
        enabled AND ``export_donuts=True``; otherwise ``None``.
    """
    has_pbody = pbody_mask is not None and pnorm is not None
    has_sg = sg_mask is not None and sgnorm is not None

    if single_cell and cp_mask is None:
        raise ValueError(
            "run_one_image_set: single_cell=True requires cp_mask"
        )

    # Float-coerce + global Cap background subtraction (NaN-mask low signal).
    cap_img = cap.astype(np.float64, copy=True)
    if not no_bgsub:
        bg_thresh, bg_mu, bg_sig = estimate_bg_threshold(cap_img, bgsub_k, log)
        if log is not None:
            n_masked = int(np.sum(cap_img < bg_thresh))
            pct_masked = n_masked / cap_img.size * 100
            log(f"  Background subtraction for {set_label}: "
                f"mu={bg_mu:.1f}, sigma={bg_sig:.1f}, "
                f"threshold={bg_thresh:.0f} (mu+{bgsub_k}*sigma), "
                f"{n_masked} px ({pct_masked:.1f}%) set to NaN")
        cap_img[cap_img < bg_thresh] = np.nan

    pbody_rows: list[dict[str, Any]] | None = None
    sg_rows: list[dict[str, Any]] | None = None
    pbody_donut_mask: np.ndarray | None = None
    sg_donut_mask: np.ndarray | None = None

    # --- SG analysis (uses bg-subtracted Cap, no SG exclusion) ---
    if has_sg:
        if log is not None:
            log(f"Analyzing SGs: {set_label}")
        sgnorm_img = sgnorm.astype(np.float64, copy=False)
        sg_result = analyze_regions(
            mask_img=sg_mask, cap_img=cap_img, norm_img=sgnorm_img,
            buffer_px=buffer, donut_px=donut, bg_mode=bg_mode,
            flat_bg_value=bg_value, min_size=min_size,
            exclude_cap_zero=exclude_cap_zero,
            region_label='sg', norm_label='sgnorm',
            capture_donut_mask=export_donuts, log=log,
        )
        sg_particle_rows = sg_result["rows"]
        if export_donuts:
            sg_donut_mask = sg_result["donut_mask"]

        if single_cell:
            particle_to_cell = assign_particles_to_cells(
                sg_mask, cp_mask, min_size,
            )
            sg_rows = aggregate_by_cell(
                sg_particle_rows, particle_to_cell, cp_mask,
                'sg', 'sgnorm',
            )
            if log is not None:
                log(f"  Single-cell SG: {len(sg_rows)} cells aggregated")
        else:
            sg_rows = sg_particle_rows

    # --- P-body analysis (SG exclusion first, then uses bg-subtracted Cap) -
    if has_pbody:
        cap_for_pbody = cap_img.copy()
        if has_sg:
            sg_inside = sg_mask > 0
            cap_for_pbody[sg_inside] = np.nan
            if log is not None:
                n_excluded = int(sg_inside.sum())
                log(f"Analyzing P-bodies (SG-excluded): {set_label} "
                    f"({n_excluded} Cap pixels inside SG_mask set to NaN)")
        elif log is not None:
            log(f"Analyzing P-bodies: {set_label}")

        pnorm_img = pnorm.astype(np.float64, copy=False)
        pbody_result = analyze_regions(
            mask_img=pbody_mask, cap_img=cap_for_pbody, norm_img=pnorm_img,
            buffer_px=buffer, donut_px=donut, bg_mode=bg_mode,
            flat_bg_value=bg_value, min_size=min_size,
            exclude_cap_zero=exclude_cap_zero,
            region_label='pbody', norm_label='pnorm',
            capture_donut_mask=export_donuts, log=log,
        )
        pbody_particle_rows = pbody_result["rows"]
        if export_donuts:
            pbody_donut_mask = pbody_result["donut_mask"]

        if single_cell:
            particle_to_cell = assign_particles_to_cells(
                pbody_mask, cp_mask, min_size,
            )
            pbody_rows = aggregate_by_cell(
                pbody_particle_rows, particle_to_cell, cp_mask,
                'pbody', 'pnorm',
            )
            if log is not None:
                log(f"  Single-cell P-body: {len(pbody_rows)} cells "
                    f"aggregated")
        else:
            pbody_rows = pbody_particle_rows

    return {
        "pbody_rows": pbody_rows,
        "sg_rows": sg_rows,
        "pbody_donut_mask": pbody_donut_mask,
        "sg_donut_mask": sg_donut_mask,
    }
