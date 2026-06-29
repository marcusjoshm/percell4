"""Whole-field decapping-sensor intensity analysis — pure core.

Single source of truth for the whole-field (aggregate, not per-particle)
quantification of a Halo (decapping-sensor) channel vs an mNG
normalization channel across masked compartments. Shared by:

* The repo-root CLI ``whole_field_analysis.py`` (thin I/O wrapper).
* The registered framework analysis ``WholeFieldIntensity`` (U7).

For each image set it computes mean/integrated intensities and
enrichment ratios in a P-body compartment vs a dilute-cytoplasm
compartment (and, in the v4/v5 three-region modes, an additional
"intermediate assemblies" compartment). Both channels are
background-subtracted per field; an optional single-cell mode emits one
row per cell.

``run_one_image_set`` is the public entry point and performs no file
I/O: the caller passes pre-loaded numpy arrays. Numeric behavior is
preserved verbatim from the original CLI; the only nan-safe ratio helper
is shared with the other analyses
(:func:`percell4.domain.analysis._impl._shared.nan_safe_ratio`).

The ``save_processed`` troubleshooting output and the cross-dataset
``SiR_mean`` Halo background of preset v1 are out of scope for the
framework path; the CLI may still compute a per-dataset Halo background
externally and pass it via ``halo_bg_override``.

References:
* Plan: ``docs/plans/2026-05-28-001-feat-incorporate-whole-field-multichannel-analyses-plan.md``
  (unit U6).
* Pattern: ``src/percell4/domain/analysis/_impl/per_particle_donut.py``.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import numpy as np
from scipy import stats
from skimage import measure

from percell4.domain.analysis._impl._shared import nan_safe_ratio

_BG_KEYWORDS = (
    'mean', 'median', 'mode', 'top_quintile', 'top_quartile', 'top_decile',
    'mng-nan', 'mng-nan-median', 'mng-nan-mode', 'mng-nan-top_quintile',
    'mng-nan-top_quartile', 'mng-nan-top_decile', 'mng-nan-max', 'SiR_mean',
)


def parse_bg_mode(value: Any) -> Any:
    """Return ``value`` if it is a known bg keyword, else coerce to ``int``.

    Mirrors the CLI's argparse ``type=parse_bg_mode``: a keyword string
    stays a string; anything else must parse as an integer (a manual flat
    background, where ``0`` means "no subtraction").
    """
    if value in _BG_KEYWORDS:
        return value
    return int(value)


def filter_mask_by_size(mask_img: np.ndarray, min_size: int) -> np.ndarray:
    """Label connected components and drop those with ``area <= min_size``."""
    binary = mask_img > 0
    labeled = measure.label(binary)
    if min_size <= 0:
        return binary
    filtered = np.zeros_like(binary)
    for prop in measure.regionprops(labeled):
        if prop.area > min_size:
            filtered[labeled == prop.label] = True
    return filtered


def compute_bg_value(
    image: np.ndarray,
    dilute_mask: np.ndarray,
    bg_mode: Any,
    exclude_zeros: bool = False,
    exclude_ones: bool = False,
) -> int:
    """Compute (or return) the background value for a channel.

    A manual integer ``bg_mode`` is returned unrounded (including 0 = no
    subtraction); keyword modes are computed over the dilute-mask pixels
    and rounded up with ``math.ceil`` (``mode`` uses ``int(scipy mode)``).
    """
    if isinstance(bg_mode, int):
        return bg_mode
    pixels = image[dilute_mask]
    if exclude_ones:
        pixels = pixels[pixels > 1]
    elif exclude_zeros:
        pixels = pixels[pixels != 0]
    pixels = pixels[~np.isnan(pixels)]
    if len(pixels) == 0:
        return 0
    if bg_mode == 'mean':
        raw = np.mean(pixels)
    elif bg_mode == 'median':
        raw = np.median(pixels)
    elif bg_mode == 'mode':
        result = stats.mode(pixels.astype(int), keepdims=False)
        raw = float(result.mode)
    elif bg_mode == 'top_quintile':
        raw = np.percentile(pixels, 80)
    elif bg_mode == 'top_quartile':
        raw = np.percentile(pixels, 75)
    elif bg_mode == 'top_decile':
        raw = np.percentile(pixels, 90)
    else:
        raw = np.mean(pixels)
    return math.ceil(raw)


def _measure_region(mng_sub, halo_sub, mng_valid, pbody_mask, dilute_mask,
                    compute_percent=False, flim_mask_img=None,
                    mng_mask_img=None, sir_filter=None):
    """Two-region (P-body vs dilute) mNG/Halo metrics for one field/cell."""
    mng_pbody_pixels = mng_sub[pbody_mask]
    mng_dilute_pixels = mng_sub[dilute_mask]

    def _mean(p):
        return (np.nanmean(p) if p.size > 0 and np.any(~np.isnan(p))
                else np.nan)

    def _sum(p):
        return (np.nansum(p) if p.size > 0 and np.any(~np.isnan(p))
                else np.nan)

    mng_pbody_mean = _mean(mng_pbody_pixels)
    mng_pbody_integ = _sum(mng_pbody_pixels)
    mng_dilute_mean = _mean(mng_dilute_pixels)
    mng_dilute_integ = _sum(mng_dilute_pixels)

    halo_pbody_valid = pbody_mask & mng_valid
    halo_dilute_valid = dilute_mask & mng_valid

    if sir_filter is not None:
        halo_pbody_valid = halo_pbody_valid & sir_filter
        halo_dilute_valid = halo_dilute_valid & sir_filter

    halo_pbody_pixels = halo_sub[halo_pbody_valid]
    halo_dilute_pixels = halo_sub[halo_dilute_valid]

    halo_pbody_mean = _mean(halo_pbody_pixels)
    halo_pbody_integ = _sum(halo_pbody_pixels)
    halo_dilute_mean = _mean(halo_dilute_pixels)
    halo_dilute_integ = _sum(halo_dilute_pixels)

    result = {
        'pbody_area_px': int(pbody_mask.sum()),
        'dilute_area_px': int(dilute_mask.sum()),
        'mNG_pbody_mean': mng_pbody_mean,
        'mNG_dilute_mean': mng_dilute_mean,
        'mNG_pbody_integ': mng_pbody_integ,
        'mNG_dilute_integ': mng_dilute_integ,
        'halo_pbody_mean': halo_pbody_mean,
        'halo_dilute_mean': halo_dilute_mean,
        'halo_pbody_integ': halo_pbody_integ,
        'halo_dilute_integ': halo_dilute_integ,
        'mNG_pbody_over_dilute': nan_safe_ratio(mng_pbody_mean,
                                                mng_dilute_mean),
        'halo_pbody_over_dilute': nan_safe_ratio(halo_pbody_mean,
                                                 halo_dilute_mean),
        'halo_over_mNG_pbody': nan_safe_ratio(halo_pbody_mean,
                                              mng_pbody_mean),
        'halo_over_mNG_dilute': nan_safe_ratio(halo_dilute_mean,
                                               mng_dilute_mean),
    }

    if compute_percent:
        overlap_mask = np.ones(halo_sub.shape, dtype=bool)
        if flim_mask_img is not None:
            overlap_mask &= flim_mask_img > 0
        if mng_mask_img is not None:
            overlap_mask &= mng_mask_img > 0

        dcp2_mask = (mng_mask_img > 0 if mng_mask_img is not None
                     else np.ones(halo_sub.shape, dtype=bool))

        dcp2_in_pbody = int((pbody_mask & dcp2_mask).sum())
        if dcp2_in_pbody > 0:
            pbody_overlap = int((pbody_mask & overlap_mask).sum())
            result['pct_halo_in_mNG_pbody'] = (
                (pbody_overlap / dcp2_in_pbody) * 100
            )
        else:
            result['pct_halo_in_mNG_pbody'] = np.nan

        dcp2_in_dilute = int((dilute_mask & dcp2_mask).sum())
        if dcp2_in_dilute > 0:
            dilute_overlap = int((dilute_mask & overlap_mask).sum())
            result['pct_halo_in_mNG_dilute'] = (
                (dilute_overlap / dcp2_in_dilute) * 100
            )
        else:
            result['pct_halo_in_mNG_dilute'] = np.nan

    return result


def _measure_v4_regions(mng_sub, halo_sub, mng_valid,
                        pbody_mask, dilute_mask,
                        dcp2_mask, dcp2_mask_2,
                        interaction_mask, interaction_mask_2,
                        compute_percent=False,
                        cell_region=None,
                        zero_fill_mode=False):
    """Three-region (P-body / intermediate / dilute) measurement.

    v4 (``zero_fill_mode=False``) excludes pixels outside the inner
    interaction mask from the Halo mean; v5 (``zero_fill_mode=True``)
    zeros and includes them. See the original CLI docstring for the exact
    region definitions, preserved verbatim here.
    """
    pbody_mng_region = pbody_mask
    pbody_halo_region = pbody_mask & mng_valid
    halo_for_pbody = halo_sub

    interm_mng_region = dcp2_mask_2

    if zero_fill_mode:
        interm_halo_region = dcp2_mask_2 & mng_valid
        halo_for_interm = np.where(interaction_mask_2, halo_sub, 0.0)

        dilute_mng_region = dilute_mask & ~dcp2_mask_2
        dilute_halo_region = dilute_mask & ~dcp2_mask_2 & mng_valid
        halo_for_dilute = np.where(
            interaction_mask & ~interaction_mask_2, halo_sub, 0.0
        )
    else:
        interm_halo_region = dcp2_mask_2 & interaction_mask_2 & mng_valid
        halo_for_interm = halo_sub

        dilute_mng_region = dilute_mask & dcp2_mask & ~dcp2_mask_2
        dilute_halo_region = (
            dilute_mask & interaction_mask & ~interaction_mask_2 & mng_valid
        )
        halo_for_dilute = halo_sub

    if cell_region is not None:
        pbody_mng_region = pbody_mng_region & cell_region
        pbody_halo_region = pbody_halo_region & cell_region
        interm_mng_region = interm_mng_region & cell_region
        interm_halo_region = interm_halo_region & cell_region
        dilute_mng_region = dilute_mng_region & cell_region
        dilute_halo_region = dilute_halo_region & cell_region

    def _stats(arr, region):
        pixels = arr[region]
        if pixels.size > 0 and np.any(~np.isnan(pixels)):
            return np.nanmean(pixels), np.nansum(pixels)
        return np.nan, np.nan

    mng_pbody_mean, mng_pbody_integ = _stats(mng_sub, pbody_mng_region)
    mng_interm_mean, mng_interm_integ = _stats(mng_sub, interm_mng_region)
    mng_dilute_mean, mng_dilute_integ = _stats(mng_sub, dilute_mng_region)
    halo_pbody_mean, halo_pbody_integ = _stats(halo_for_pbody,
                                               pbody_halo_region)
    halo_interm_mean, halo_interm_integ = _stats(halo_for_interm,
                                                 interm_halo_region)
    halo_dilute_mean, halo_dilute_integ = _stats(halo_for_dilute,
                                                 dilute_halo_region)

    result = {
        'pbody_area_px': int(pbody_mng_region.sum()),
        'intermediate_area_px': int(interm_mng_region.sum()),
        'dilute_area_px': int(dilute_mng_region.sum()),
        'mNG_pbody_mean': mng_pbody_mean,
        'mNG_intermediate_mean': mng_interm_mean,
        'mNG_dilute_mean': mng_dilute_mean,
        'mNG_pbody_integ': mng_pbody_integ,
        'mNG_intermediate_integ': mng_interm_integ,
        'mNG_dilute_integ': mng_dilute_integ,
        'halo_pbody_mean': halo_pbody_mean,
        'halo_intermediate_mean': halo_interm_mean,
        'halo_dilute_mean': halo_dilute_mean,
        'halo_pbody_integ': halo_pbody_integ,
        'halo_intermediate_integ': halo_interm_integ,
        'halo_dilute_integ': halo_dilute_integ,
        'mNG_pbody_over_dilute': nan_safe_ratio(mng_pbody_mean,
                                                mng_dilute_mean),
        'mNG_intermediate_over_dilute': nan_safe_ratio(mng_interm_mean,
                                                       mng_dilute_mean),
        'halo_pbody_over_dilute': nan_safe_ratio(halo_pbody_mean,
                                                 halo_dilute_mean),
        'halo_intermediate_over_dilute': nan_safe_ratio(halo_interm_mean,
                                                        halo_dilute_mean),
        'halo_over_mNG_pbody': nan_safe_ratio(halo_pbody_mean,
                                              mng_pbody_mean),
        'halo_over_mNG_intermediate': nan_safe_ratio(halo_interm_mean,
                                                     mng_interm_mean),
        'halo_over_mNG_dilute': nan_safe_ratio(halo_dilute_mean,
                                               mng_dilute_mean),
    }

    if compute_percent:
        pbody_perc_denom = pbody_mask & dcp2_mask
        pbody_perc_num = pbody_perc_denom & interaction_mask

        interm_perc_denom = dcp2_mask_2
        interm_perc_num = interm_perc_denom & interaction_mask_2

        dilute_perc_denom = dilute_mask & dcp2_mask & ~dcp2_mask_2
        dilute_perc_num = (
            dilute_perc_denom & interaction_mask & ~interaction_mask_2
        )

        if cell_region is not None:
            pbody_perc_denom = pbody_perc_denom & cell_region
            pbody_perc_num = pbody_perc_num & cell_region
            interm_perc_denom = interm_perc_denom & cell_region
            interm_perc_num = interm_perc_num & cell_region
            dilute_perc_denom = dilute_perc_denom & cell_region
            dilute_perc_num = dilute_perc_num & cell_region

        pbody_d = int(pbody_perc_denom.sum())
        result['pct_halo_in_mNG_pbody'] = (
            (int(pbody_perc_num.sum()) / pbody_d) * 100
            if pbody_d > 0 else np.nan
        )
        interm_d = int(interm_perc_denom.sum())
        result['pct_halo_in_mNG_intermediate'] = (
            (int(interm_perc_num.sum()) / interm_d) * 100
            if interm_d > 0 else np.nan
        )
        dilute_d = int(dilute_perc_denom.sum())
        result['pct_halo_in_mNG_dilute'] = (
            (int(dilute_perc_num.sum()) / dilute_d) * 100
            if dilute_d > 0 else np.nan
        )

    return result


def run_one_image_set(
    *,
    pbody_mask: np.ndarray,
    dilute_mask: np.ndarray,
    halo: np.ndarray,
    mng: np.ndarray,
    cp_mask: np.ndarray | None = None,
    dcp2_mask: np.ndarray | None = None,
    interaction_mask: np.ndarray | None = None,
    sir_mask: np.ndarray | None = None,
    dcp2_mask_2: np.ndarray | None = None,
    interaction_mask_2: np.ndarray | None = None,
    mng_bg_mode: Any,
    halo_bg_mode: Any,
    min_size: int,
    exclude_halo_zero: bool = True,
    exclude_halo_one: bool = False,
    sir_subtract_mode: str | None = None,
    flim_filter_mode: str | None = None,
    mng_in_flim: bool = False,
    mng_filter_mode: str | None = None,
    compute_percent: bool = False,
    single_cell: bool = False,
    sir_filter: bool = False,
    intermediate_assemblies: bool = False,
    intermediate_zero_fill: bool = False,
    halo_cell_mean: bool = False,
    halo_bg_override: int | None = None,
    set_label: str = "",
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Analyze one image set; return ``{"rows": list[dict]}``.

    Pure function: no file I/O. The caller (CLI or framework) reads the
    TIFFs / ``.h5`` layers and passes pre-loaded numpy arrays; ``halo``
    and ``mng`` are coerced to float64 and copied before mutation so the
    caller's arrays are untouched.

    Optional mask arrays (``cp_mask``, ``dcp2_mask``, ``interaction_mask``,
    ``sir_mask``, ``dcp2_mask_2``, ``interaction_mask_2``) must be supplied
    when the corresponding mode/param that consumes them is active; the
    caller (CLI ``_check_channels`` / framework ``run()`` guards) is
    responsible for that. Returns one row for the whole field, or one row
    per non-zero cell id when ``single_cell``.
    """
    halo_img = halo.astype(np.float64, copy=True)
    mng_img = mng.astype(np.float64, copy=True)

    # --- SiR subtract: applied to raw Halo before all other filters ---
    if sir_subtract_mode is not None and sir_mask is not None:
        sir_inside = sir_mask > 0
        if sir_subtract_mode == 'zero':
            halo_img[sir_inside] = 0.0
        elif sir_subtract_mode == 'NaN':
            halo_img[sir_inside] = np.nan
        if log is not None:
            log(f"  SiR subtract applied: Halo where SiR_mask > 0 set to "
                f"{sir_subtract_mode}")

    # --- FLIM filter: applied to raw Halo before background subtraction ---
    if flim_filter_mode is not None and interaction_mask is not None:
        flim_outside = interaction_mask == 0
        if flim_filter_mode == 'zero':
            halo_img[flim_outside] = 0.0
        elif flim_filter_mode == 'NaN':
            halo_img[flim_outside] = np.nan
        if log is not None:
            log(f"  FLIM filter applied: Halo outside interaction_mask set "
                f"to {flim_filter_mode}")

    # --- mNG filter: applied to mNG before background subtraction ---
    if mng_filter_mode is not None and dcp2_mask is not None:
        mng_outside = dcp2_mask == 0
        if mng_filter_mode == 'zero':
            mng_img[mng_outside] = 0.0
        elif mng_filter_mode == 'NaN':
            mng_img[mng_outside] = np.nan
        if log is not None:
            log(f"  mNG filter applied: mNG outside Dcp2_mask set to "
                f"{mng_filter_mode}")

    # --- mNG-in-FLIM: NaN both channels outside (interaction & Dcp2) ---
    if mng_in_flim and interaction_mask is not None and dcp2_mask is not None:
        outside_both = (interaction_mask == 0) | (dcp2_mask == 0)
        mng_img[outside_both] = np.nan
        halo_img[outside_both] = np.nan
        if log is not None:
            log("  mNG-in-FLIM: mNG and Halo set to NaN outside "
                "(interaction_mask & Dcp2_mask)")

    pbody_mask_f = filter_mask_by_size(pbody_mask, min_size)
    dilute_mask_b = dilute_mask > 0

    sir_filter_region = None
    if sir_filter and sir_mask is not None:
        sir_filter_region = sir_mask > 0
        if log is not None:
            log(f"  SiR filter applied: Halo measured only within SiR_mask "
                f"({int(sir_filter_region.sum())} px)")

    # --- mNG background subtraction (Halo 'mng-nan' modes depend on it) ---
    mng_bg = compute_bg_value(mng_img, dilute_mask_b, mng_bg_mode)
    mng_sub = mng_img - mng_bg
    mng_sub[mng_sub <= 0] = np.nan

    # --- Halo background subtraction ---
    if halo_bg_override is not None:
        halo_bg = halo_bg_override
    elif isinstance(halo_bg_mode, str) and halo_bg_mode.startswith('mng-nan'):
        mng_nan_in_dilute = dilute_mask_b & np.isnan(mng_sub)
        halo_bg_pixels = halo_img[mng_nan_in_dilute]
        if exclude_halo_one:
            halo_bg_pixels = halo_bg_pixels[halo_bg_pixels > 1]
        elif exclude_halo_zero:
            halo_bg_pixels = halo_bg_pixels[halo_bg_pixels != 0]
        if len(halo_bg_pixels) > 0:
            if halo_bg_mode == 'mng-nan':
                halo_bg = math.ceil(np.mean(halo_bg_pixels))
            elif halo_bg_mode == 'mng-nan-median':
                halo_bg = math.ceil(np.median(halo_bg_pixels))
            elif halo_bg_mode == 'mng-nan-mode':
                result = stats.mode(halo_bg_pixels.astype(int),
                                    keepdims=False)
                halo_bg = int(result.mode)
            elif halo_bg_mode == 'mng-nan-top_quintile':
                halo_bg = math.ceil(np.percentile(halo_bg_pixels, 80))
            elif halo_bg_mode == 'mng-nan-top_quartile':
                halo_bg = math.ceil(np.percentile(halo_bg_pixels, 75))
            elif halo_bg_mode == 'mng-nan-top_decile':
                halo_bg = math.ceil(np.percentile(halo_bg_pixels, 90))
            elif halo_bg_mode == 'mng-nan-max':
                halo_bg = math.ceil(np.max(halo_bg_pixels))
        else:
            halo_bg = 0
            if log is not None:
                log("  Warning: no NaN mNG pixels in dilute mask; Halo "
                    "background set to 0 (no subtraction)")
    else:
        halo_bg = compute_bg_value(
            halo_img, dilute_mask_b, halo_bg_mode,
            exclude_zeros=exclude_halo_zero, exclude_ones=exclude_halo_one,
        )

    halo_sub = halo_img - halo_bg
    halo_sub = np.maximum(halo_sub, 0)

    mng_valid = ~np.isnan(mng_sub)

    if log is not None:
        log(f"  P-body mask area: {pbody_mask_f.sum()} px | Dilute mask "
            f"area: {dilute_mask_b.sum()} px")
        log(f"  mNG bg: {mng_bg} | Halo bg: {halo_bg}")

    # --- v4/v5: three-region mode ---
    if intermediate_assemblies:
        dcp2_full = dcp2_mask > 0
        interaction_full = interaction_mask > 0
        dcp2_inner = dcp2_mask_2 > 0
        interaction_inner = interaction_mask_2 > 0

        if single_cell and cp_mask is not None:
            return {"rows": _v4_single_cell(
                mng_sub, halo_sub, mng_valid, pbody_mask_f, dilute_mask_b,
                dcp2_full, dcp2_inner, interaction_full, interaction_inner,
                cp_mask, compute_percent, intermediate_zero_fill,
                mng_bg, halo_bg, log, halo_cell_mean,
            )}

        result = _measure_v4_regions(
            mng_sub, halo_sub, mng_valid, pbody_mask_f, dilute_mask_b,
            dcp2_full, dcp2_inner, interaction_full, interaction_inner,
            compute_percent=compute_percent,
            zero_fill_mode=intermediate_zero_fill,
        )
        result['mng_bg_value'] = mng_bg
        result['halo_bg_value'] = halo_bg
        return {"rows": [result]}

    # --- Single-cell two-region mode ---
    if single_cell and cp_mask is not None:
        return {"rows": _two_region_single_cell(
            mng_sub, halo_sub, mng_valid, pbody_mask_f, dilute_mask_b,
            cp_mask, compute_percent, interaction_mask, dcp2_mask,
            sir_filter_region, mng_bg, halo_bg, log, halo_cell_mean,
        )}

    # --- Whole-field two-region ---
    result = _measure_region(
        mng_sub, halo_sub, mng_valid, pbody_mask_f, dilute_mask_b,
        compute_percent, interaction_mask, dcp2_mask,
        sir_filter=sir_filter_region,
    )
    result['mng_bg_value'] = mng_bg
    result['halo_bg_value'] = halo_bg
    return {"rows": [result]}


def _particle_counts_per_cell(pbody_mask: np.ndarray,
                              cp_mask_img: np.ndarray) -> dict[int, int]:
    """Count P-body particles per cell by majority-pixel ownership.

    Kept verbatim from the CLI (``np.unique`` + ``argmax`` + skip
    background-majority) — deliberately NOT the shared bincount-based
    ``assign_particles_to_cells`` used by donut/multichannel.
    """
    pbody_labels = measure.label(pbody_mask)
    counts: dict[int, int] = defaultdict(int)
    for prop in measure.regionprops(pbody_labels):
        ys, xs = prop.coords[:, 0], prop.coords[:, 1]
        cell_values = cp_mask_img[ys, xs]
        values, value_counts = np.unique(cell_values, return_counts=True)
        majority_cell = int(values[np.argmax(value_counts)])
        if majority_cell != 0:
            counts[majority_cell] += 1
    return counts


def _cell_nanmean(channel_sub: np.ndarray, cell_region: np.ndarray) -> float:
    """Float-safe whole-cell mean of a bg-subtracted channel over a cell.

    Mirrors the always-on ``mNG_cell_mean`` guard: returns ``np.nan`` for an
    empty cell or an all-NaN region (e.g. Halo NaN'd by a FLIM/SiR filter)
    rather than emitting a RuntimeWarning.
    """
    pixels = channel_sub[cell_region]
    if pixels.size > 0 and np.any(~np.isnan(pixels)):
        return float(np.nanmean(pixels))
    return np.nan


def _two_region_single_cell(mng_sub, halo_sub, mng_valid, pbody_mask,
                            dilute_mask, cp_mask_img, compute_percent,
                            interaction_mask, dcp2_mask, sir_filter_region,
                            mng_bg, halo_bg, log, halo_cell_mean=False):
    cell_ids = np.unique(cp_mask_img)
    cell_ids = cell_ids[cell_ids != 0]
    counts = _particle_counts_per_cell(pbody_mask, cp_mask_img)

    results = []
    for cell_id in cell_ids:
        cell_region = cp_mask_img == cell_id
        cell_pbody = pbody_mask & cell_region
        cell_dilute = dilute_mask & cell_region
        metrics = _measure_region(
            mng_sub, halo_sub, mng_valid, cell_pbody, cell_dilute,
            compute_percent, interaction_mask, dcp2_mask,
            sir_filter=sir_filter_region,
        )
        mng_cell_pixels = mng_sub[cell_region]
        mng_cell_mean = (
            np.nanmean(mng_cell_pixels)
            if mng_cell_pixels.size > 0 and np.any(~np.isnan(mng_cell_pixels))
            else np.nan
        )
        metrics['cell_id'] = int(cell_id)
        metrics['cell_area_px'] = int(cell_region.sum())
        metrics['particle_count'] = counts.get(int(cell_id), 0)
        metrics['mNG_cell_mean'] = mng_cell_mean
        if halo_cell_mean:
            metrics['Halo_cell_mean'] = _cell_nanmean(halo_sub, cell_region)
        metrics['mng_bg_value'] = mng_bg
        metrics['halo_bg_value'] = halo_bg
        results.append(metrics)
    if log is not None:
        log(f"  Single-cell: {len(cell_ids)} cells analyzed")
    return results


def _v4_single_cell(mng_sub, halo_sub, mng_valid, pbody_mask, dilute_mask,
                    dcp2_full, dcp2_inner, interaction_full,
                    interaction_inner, cp_mask_img, compute_percent,
                    zero_fill_mode, mng_bg, halo_bg, log,
                    halo_cell_mean=False):
    cell_ids = np.unique(cp_mask_img)
    cell_ids = cell_ids[cell_ids != 0]
    counts = _particle_counts_per_cell(pbody_mask, cp_mask_img)

    results = []
    for cell_id in cell_ids:
        cell_region = cp_mask_img == cell_id
        metrics = _measure_v4_regions(
            mng_sub, halo_sub, mng_valid, pbody_mask, dilute_mask,
            dcp2_full, dcp2_inner, interaction_full, interaction_inner,
            compute_percent=compute_percent, cell_region=cell_region,
            zero_fill_mode=zero_fill_mode,
        )
        mng_cell_pixels = mng_sub[cell_region]
        mng_cell_mean = (
            np.nanmean(mng_cell_pixels)
            if mng_cell_pixels.size > 0 and np.any(~np.isnan(mng_cell_pixels))
            else np.nan
        )
        metrics['cell_id'] = int(cell_id)
        metrics['cell_area_px'] = int(cell_region.sum())
        metrics['particle_count'] = counts.get(int(cell_id), 0)
        metrics['mNG_cell_mean'] = mng_cell_mean
        if halo_cell_mean:
            metrics['Halo_cell_mean'] = _cell_nanmean(halo_sub, cell_region)
        metrics['mng_bg_value'] = mng_bg
        metrics['halo_bg_value'] = halo_bg
        results.append(metrics)
    if log is not None:
        log(f"  Single-cell v4: {len(cell_ids)} cells analyzed")
    return results
