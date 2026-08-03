"""Shared pure-domain helpers for per-particle / donut-style analyses.

Extracted from :mod:`per_particle_donut` so sibling analyses
(``per_particle_multichannel``, ``whole_field_intensity``) reuse one
implementation of the labeling, donut geometry, particle→cell assignment,
and small numeric guards instead of duplicating them.

Pure: ``numpy`` / ``scipy`` / ``skimage`` only — no file I/O, no Qt.

Note: ``whole_field_intensity`` deliberately keeps its own
``np.unique``-based particle→cell assignment (different tie-break and
background-majority handling) and does NOT use
:func:`assign_particles_to_cells` here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.ndimage import distance_transform_edt
from skimage import measure


def label_and_filter(
    mask_img: np.ndarray, min_size: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Label a binary mask and return the regions surviving the size filter.

    Returns ``(label_mask, binary_mask, region_ids, props_by_id)`` where
    ``region_ids`` keeps only connected components with ``area > min_size``
    (strict ``>``; the filter is skipped when ``min_size <= 0``).
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
    return label_mask, binary_mask, region_ids, props_by_id


def region_and_donut_masks(
    label_mask: np.ndarray,
    binary_mask: np.ndarray,
    region_id: int,
    props_by_id: dict,
    buffer_px: int,
    donut_px: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Full-image ``(region_mask, donut_mask)`` boolean arrays for one region.

    The donut ring is the set of pixels at Euclidean distance in
    ``(buffer_px, buffer_px + donut_px]`` from the region, excluding any
    pixel that belongs to the binary mask. Computed on a padded bounding-box
    crop for speed, then mapped back to full-image coordinates.
    """
    h, w = binary_mask.shape
    pad = buffer_px + donut_px + 1
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
    return region_mask, donut_mask


def donut_for_region_mask(
    region_mask: np.ndarray,
    binary_mask: np.ndarray,
    bbox: tuple[int, int, int, int],
    buffer_px: int,
    donut_px: int,
) -> np.ndarray:
    """Full-image donut-ring boolean for an arbitrary region given as a mask.

    Sibling of :func:`region_and_donut_masks` that takes the region as a
    pre-built boolean ``region_mask`` (plus its full-image ``bbox`` as
    ``(min_row, min_col, max_row, max_col)``) instead of a label id in a
    global label image. Used by the per-cell labeling path, where a
    particle is a connected component of ``cell ∩ mask`` rather than a
    component of the whole-image mask.

    The ring is the set of pixels at Euclidean distance in
    ``(buffer_px, buffer_px + donut_px]`` from the region, excluding any
    pixel that belongs to ``binary_mask`` (the full particle mask, so the
    dilute ring never includes another particle's pixels). Computed on a
    padded bounding-box crop for speed, then mapped back to full-image
    coordinates.
    """
    h, w = binary_mask.shape
    pad = buffer_px + donut_px + 1
    r0 = max(bbox[0] - pad, 0)
    c0 = max(bbox[1] - pad, 0)
    r1 = min(bbox[2] + pad, h)
    c1 = min(bbox[3] + pad, w)

    crop_region = region_mask[r0:r1, c0:c1]
    crop_binary = binary_mask[r0:r1, c0:c1]

    dist_from_this = distance_transform_edt(~crop_region)
    in_donut_range = (
        (dist_from_this > buffer_px)
        & (dist_from_this <= buffer_px + donut_px)
    )
    crop_donut = in_donut_range & ~crop_binary

    donut_mask = np.zeros_like(binary_mask)
    donut_mask[r0:r1, c0:c1] = crop_donut
    return donut_mask


def assign_particles_to_cells(
    mask_img: np.ndarray, cp_mask_img: np.ndarray, min_size: int
) -> dict[int, int]:
    """Map particle label ID → cell ID by majority of non-zero cp pixels.

    Particles whose pixels overlap no cell (all-zero cp values) are omitted
    from the mapping; the caller decides the fallback (donut drops them,
    multichannel maps them to cell 0 via ``.get(pid, 0)``).
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


def nan_safe_ratio(a: float, b: float) -> float:
    """``a / b`` when both are finite and ``b != 0``, else ``np.nan``."""
    if pd.notna(a) and pd.notna(b) and b != 0:
        return a / b
    return np.nan


def weighted_mean(df: pd.DataFrame, col: str, weights_col: str) -> float:
    """Area-weighted mean of ``df[col]`` weighted by ``df[weights_col]``.

    Rows with a NaN value, NaN weight, or non-positive weight are dropped.
    Returns ``np.nan`` when no row is valid.
    """
    weights = df[weights_col].values.astype(float)
    values = df[col].values.astype(float)
    valid = ~(np.isnan(values) | np.isnan(weights)) & (weights > 0)
    if not np.any(valid):
        return np.nan
    return float(
        (values[valid] * weights[valid]).sum() / weights[valid].sum()
    )


def nanmean_or_nan(arr: np.ndarray) -> float:
    """``np.nanmean(arr)`` unless every element is NaN (then ``np.nan``)."""
    return np.nanmean(arr) if np.any(~np.isnan(arr)) else np.nan


def nansum_or_nan(arr: np.ndarray) -> float:
    """``np.nansum(arr)`` unless every element is NaN (then ``np.nan``)."""
    return np.nansum(arr) if np.any(~np.isnan(arr)) else np.nan
