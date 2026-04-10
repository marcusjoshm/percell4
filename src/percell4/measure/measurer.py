"""BBox-optimized per-cell measurement.

Pure function: arrays in, DataFrame out. No store or GUI coupling.
Uses scipy.ndimage.find_objects for O(1) bounding box lookup per cell.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.ndimage import find_objects
from skimage.measure import regionprops

from percell4.measure.metrics import BUILTIN_METRICS

logger = logging.getLogger(__name__)

CORE_COLUMNS = [
    "label", "centroid_y", "centroid_x",
    "bbox_y", "bbox_x", "bbox_h", "bbox_w", "area",
]

# (round_name, inside_mask, outside_mask, inside_area, outside_area) — used by
# the multi-mask single-pass measurer to carry per-round crop state across
# channels without redundant mask arithmetic.
_PerRound = tuple[str, NDArray[np.bool_], NDArray[np.bool_], float, float]


@dataclass
class _CellCrop:
    """Data for one cell's bounding box crop."""

    label: int
    image_crop: NDArray
    cell_mask: NDArray[np.bool_]
    centroid_y: float
    centroid_x: float
    bbox_y: int
    bbox_x: int
    bbox_h: int
    bbox_w: int
    area: float
    sl: tuple  # bounding box slice


def _validate_metrics(metrics: list[str] | None) -> list[str]:
    """Validate and return metric names."""
    if metrics is None:
        return list(BUILTIN_METRICS.keys())
    for name in metrics:
        if name not in BUILTIN_METRICS:
            raise ValueError(f"Unknown metric: {name!r}")
    return metrics


def _iter_cell_crops(
    image: NDArray,
    labels: NDArray[np.int32],
) -> Iterator[_CellCrop]:
    """Iterate cell crops from a label array. Single find_objects + regionprops call.

    Yields _CellCrop for each non-empty cell, providing the image crop,
    boolean cell mask, and core spatial properties.
    """
    slices = find_objects(labels)
    props = regionprops(labels)

    for prop in props:
        label_val = prop.label
        sl = slices[label_val - 1]
        if sl is None:
            continue

        label_crop = labels[sl]
        cell_mask = label_crop == label_val
        if not np.any(cell_mask):
            continue

        cy, cx = prop.centroid
        min_row, min_col, max_row, max_col = prop.bbox

        yield _CellCrop(
            label=int(label_val),
            image_crop=image[sl],
            cell_mask=cell_mask,
            centroid_y=float(cy),
            centroid_x=float(cx),
            bbox_y=int(min_row),
            bbox_x=int(min_col),
            bbox_h=int(max_row - min_row),
            bbox_w=int(max_col - min_col),
            area=float(prop.area),
            sl=sl,
        )


def _core_row(crop: _CellCrop) -> dict:
    """Build the core columns dict from a cell crop."""
    return {
        "label": crop.label,
        "centroid_y": crop.centroid_y,
        "centroid_x": crop.centroid_x,
        "bbox_y": crop.bbox_y,
        "bbox_x": crop.bbox_x,
        "bbox_h": crop.bbox_h,
        "bbox_w": crop.bbox_w,
        "area": crop.area,
    }


def _compute_metrics(
    image_crop: NDArray,
    cell_mask: NDArray[np.bool_],
    metric_names: list[str],
    area: float,
    prefix: str = "",
) -> dict:
    """Compute metrics for a single cell region. Returns {name: value} dict."""
    result = {}
    for name in metric_names:
        key = f"{prefix}{name}" if prefix else name
        if name == "area":
            result[key] = area
        else:
            result[key] = BUILTIN_METRICS[name](image_crop, cell_mask)
    return result


def measure_cells(
    image: NDArray,
    labels: NDArray[np.int32],
    metrics: list[str] | None = None,
    mask: NDArray[np.uint8] | None = None,
) -> pd.DataFrame:
    """Compute per-cell metrics from a single-channel image and label array.

    Parameters
    ----------
    image : (H, W) intensity image
    labels : (H, W) int32 label array (0 = background)
    metrics : list of metric names from BUILTIN_METRICS (None = all)
    mask : optional (H, W) uint8 binary mask for masked measurements.
        When provided, computes mask_inside and mask_outside scopes
        in addition to whole_cell.

    Returns
    -------
    DataFrame with one row per cell. Columns include:
        label, centroid_y, centroid_x, bbox_y, bbox_x, bbox_h, bbox_w, area,
        plus one column per metric.
        If mask provided, additional {metric}_mask_inside and {metric}_mask_outside columns.
    """
    metric_names = _validate_metrics(metrics)

    if labels.max() == 0:
        columns = _build_column_list(metric_names, has_mask=mask is not None)
        return pd.DataFrame(columns=columns)

    rows: list[dict] = []

    for crop in _iter_cell_crops(image, labels):
        row = _core_row(crop)
        row.update(_compute_metrics(
            crop.image_crop, crop.cell_mask, metric_names, crop.area
        ))

        if mask is not None:
            mask_crop = mask[crop.sl]
            mask_bool = mask_crop > 0
            inside = crop.cell_mask & mask_bool
            outside = crop.cell_mask & ~mask_bool

            for name in metric_names:
                if name == "area":
                    row[f"{name}_mask_inside"] = float(np.sum(inside))
                    row[f"{name}_mask_outside"] = float(np.sum(outside))
                else:
                    row[f"{name}_mask_inside"] = BUILTIN_METRICS[name](
                        crop.image_crop, inside
                    )
                    row[f"{name}_mask_outside"] = BUILTIN_METRICS[name](
                        crop.image_crop, outside
                    )

        rows.append(row)

    if not rows:
        columns = _build_column_list(metric_names, has_mask=mask is not None)
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(rows)
    df["label"] = df["label"].astype(np.int32)
    return df


def measure_cells_multi_roi(
    image: NDArray,
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    roi_names: dict[int, str],
    metrics: list[str] | None = None,
) -> pd.DataFrame:
    """Measure per-cell metrics for each ROI label in a multi-label mask.

    Single-pass: calls find_objects() and regionprops() once, then computes
    per-ROI metrics within each cell's bounding box crop.

    Parameters
    ----------
    image : (H, W) single-channel intensity image
    labels : (H, W) int32 label array
    mask : (H, W) uint8 multi-label mask (0=outside, 1..N=ROI labels)
    roi_names : mapping {mask_label_value: roi_name_string}
    metrics : metric names (None = all builtins)

    Returns
    -------
    DataFrame with whole-cell metrics plus {roi_name}_{metric} columns per ROI.
    """
    metric_names = _validate_metrics(metrics)

    if labels.max() == 0:
        return pd.DataFrame()

    rows: list[dict] = []

    for crop in _iter_cell_crops(image, labels):
        row = _core_row(crop)
        row.update(_compute_metrics(
            crop.image_crop, crop.cell_mask, metric_names, crop.area
        ))

        # Per-ROI metrics — computed on the small crop, fast
        mask_crop = mask[crop.sl]
        for label_v, roi_name in roi_names.items():
            roi_cell = crop.cell_mask & (mask_crop == label_v)
            n_pixels = int(roi_cell.sum())
            row[f"{roi_name}_area"] = float(n_pixels)
            if n_pixels > 0:
                row.update(_compute_metrics(
                    crop.image_crop, roi_cell, metric_names, float(n_pixels),
                    prefix=f"{roi_name}_",
                ))
            else:
                for name in metric_names:
                    if name != "area":
                        row[f"{roi_name}_{name}"] = float("nan")

        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["label"] = df["label"].astype(np.int32)
    return df


def _merge_multichannel(
    per_channel_dfs: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Merge per-channel DataFrames: core columns from first, prefixed metrics from all."""
    result_df: pd.DataFrame | None = None
    core_set = set(CORE_COLUMNS)

    for ch_name, ch_df in per_channel_dfs.items():
        if ch_df.empty:
            continue

        metric_cols = [c for c in ch_df.columns if c not in core_set]
        rename_map = {col: f"{ch_name}_{col}" for col in metric_cols}

        if result_df is None:
            result_df = ch_df.rename(columns=rename_map)
        else:
            ch_metrics = ch_df[["label"] + metric_cols].rename(columns=rename_map)
            result_df = result_df.merge(ch_metrics, on="label", how="outer")

    return result_df if result_df is not None else pd.DataFrame()


def measure_multichannel(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    metrics: list[str] | None = None,
    mask: NDArray[np.uint8] | None = None,
) -> pd.DataFrame:
    """Measure multiple channels and merge into a single DataFrame.

    Parameters
    ----------
    images : dict mapping channel name to (H, W) array
    labels : (H, W) int32 label array
    metrics : metric names (None = all builtins)
    mask : optional binary mask

    Returns
    -------
    DataFrame with core columns from the first channel, plus
    {channel}_{metric} columns for each channel.
    """
    if not images:
        raise ValueError("No channel images to measure")

    per_channel = {
        ch_name: measure_cells(image, labels, metrics=metrics, mask=mask)
        for ch_name, image in images.items()
    }
    return _merge_multichannel(per_channel)


def measure_multichannel_multi_roi(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    roi_names: dict[int, str],
    metrics: list[str] | None = None,
) -> pd.DataFrame:
    """Measure multiple channels with a multi-label mask.

    Parameters
    ----------
    images : dict mapping channel name to (H, W) array
    labels : (H, W) int32 label array
    mask : (H, W) uint8 multi-label mask (0=outside, 1..N=ROI labels)
    roi_names : mapping {mask_label_value: roi_name_string}
    metrics : metric names (None = all builtins)

    Returns
    -------
    DataFrame with core columns + {channel}_{metric} + {channel}_{roi}_{metric} columns.
    """
    if not images:
        raise ValueError("No channel images to measure")

    per_channel = {
        ch_name: measure_cells_multi_roi(
            image, labels, mask, roi_names, metrics=metrics
        )
        for ch_name, image in images.items()
    }
    return _merge_multichannel(per_channel)


def _build_column_list(metric_names: list[str], has_mask: bool) -> list[str]:
    """Build the expected column list for an empty DataFrame."""
    cols = list(CORE_COLUMNS)
    for name in metric_names:
        if name not in cols:
            cols.append(name)
    if has_mask:
        for name in metric_names:
            cols.append(f"{name}_mask_inside")
            cols.append(f"{name}_mask_outside")
    return cols


def _build_column_list_with_masks(
    metric_names: list[str],
    mask_names: list[str],
) -> list[str]:
    """Build the expected column list for an empty masked-multi DataFrame."""
    cols = list(CORE_COLUMNS)
    for name in metric_names:
        if name not in cols:
            cols.append(name)
    for round_name in mask_names:
        for name in metric_names:
            cols.append(f"{name}_in_{round_name}")
            cols.append(f"{name}_out_{round_name}")
    return cols


def measure_cells_with_masks(
    image: NDArray,
    labels: NDArray[np.int32],
    metrics: list[str] | None = None,
    masks: dict[str, NDArray[np.uint8]] | None = None,
) -> pd.DataFrame:
    """Single-pass per-cell measurement with multiple named masks.

    Produces the same whole-cell metric columns as :func:`measure_cells`,
    plus per-mask inside/outside columns named ``{metric}_in_{round_name}``
    and ``{metric}_out_{round_name}`` instead of the ``_mask_inside`` /
    ``_mask_outside`` suffixes used by the single-mask path. This avoids
    column collisions when the caller wants metrics for several rounds of
    thresholding in one pass, and re-uses a single ``find_objects`` +
    ``regionprops`` pass — much cheaper than calling
    :func:`measure_cells` once per mask.

    Parameters
    ----------
    image : (H, W) intensity image
    labels : (H, W) int32 label array (0 = background)
    metrics : metric names (None = all builtins)
    masks : mapping of ``{round_name: (H, W) uint8 mask}``. May be empty or
        ``None`` — in that case the result has only whole-cell columns.

    Returns
    -------
    DataFrame with one row per cell. See :func:`measure_cells` for the
    core + whole-cell metric columns, plus ``{metric}_in_{round_name}`` and
    ``{metric}_out_{round_name}`` for each mask.
    """
    metric_names = _validate_metrics(metrics)
    mask_items: list[tuple[str, NDArray[np.uint8]]] = (
        list(masks.items()) if masks else []
    )
    mask_names = [name for name, _ in mask_items]

    if labels.max() == 0:
        columns = _build_column_list_with_masks(metric_names, mask_names)
        return pd.DataFrame(columns=columns)

    rows: list[dict] = []
    for crop in _iter_cell_crops(image, labels):
        row = _core_row(crop)
        row.update(_compute_metrics(
            crop.image_crop, crop.cell_mask, metric_names, crop.area
        ))

        for round_name, mask in mask_items:
            mask_crop = mask[crop.sl]
            mask_bool = mask_crop > 0
            inside = crop.cell_mask & mask_bool
            outside = crop.cell_mask & ~mask_bool
            inside_area = float(np.sum(inside))
            outside_area = float(np.sum(outside))
            for name in metric_names:
                if name == "area":
                    row[f"{name}_in_{round_name}"] = inside_area
                    row[f"{name}_out_{round_name}"] = outside_area
                else:
                    row[f"{name}_in_{round_name}"] = BUILTIN_METRICS[name](
                        crop.image_crop, inside
                    )
                    row[f"{name}_out_{round_name}"] = BUILTIN_METRICS[name](
                        crop.image_crop, outside
                    )

        rows.append(row)

    if not rows:
        columns = _build_column_list_with_masks(metric_names, mask_names)
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(rows)
    df["label"] = df["label"].astype(np.int32)
    return df


def measure_multichannel_with_masks(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    metrics: list[str] | None = None,
    masks: dict[str, NDArray[np.uint8]] | None = None,
) -> pd.DataFrame:
    """Measure multiple channels in a single pass with multiple named masks.

    Result has columns named ``{channel}_{metric}`` for whole-cell plus
    ``{channel}_{metric}_in_{round_name}`` / ``_out_{round_name}`` for each
    configured mask. Designed for batch workflows that measure every
    channel against multiple rounds of thresholding in one sweep.

    Unlike calling :func:`measure_cells_with_masks` once per channel, this
    runs ``find_objects`` + ``regionprops`` exactly **once** per dataset
    and reuses the per-cell ``sl`` crop plus the per-round inside/outside
    boolean masks across all channels. For a 4-channel × 3-round dataset
    this is roughly 4× cheaper than the naive per-channel approach.

    Parameters
    ----------
    images : dict mapping channel name to (H, W) array
    labels : (H, W) int32 label array
    metrics : metric names (None = all builtins)
    masks : mapping of ``{round_name: (H, W) uint8 mask}``. Empty or ``None``
        produces whole-cell columns only.
    """
    if not images:
        raise ValueError("No channel images to measure")

    metric_names = _validate_metrics(metrics)
    mask_items: list[tuple[str, NDArray[np.uint8]]] = (
        list(masks.items()) if masks else []
    )
    mask_names = [name for name, _ in mask_items]

    # Build the empty-frame fallback matching _merge_multichannel's output.
    if labels.max() == 0:
        # Each channel contributes an empty DataFrame with the expected column
        # layout; _merge_multichannel handles the empty inputs gracefully.
        per_channel = {
            ch_name: pd.DataFrame(
                columns=_build_column_list_with_masks(metric_names, mask_names)
            )
            for ch_name in images
        }
        return _merge_multichannel(per_channel)

    # Precompute per-cell crop metadata once: bounding box slice, cell mask,
    # and per-round inside/outside boolean arrays. Everything here is
    # label-only — it does not depend on any channel image, so the cost is
    # paid once per dataset, not once per channel.
    #
    # Each entry is ``(cell_crop, per_round)`` where ``per_round`` is a list
    # of _PerRound tuples defined at module level.
    crop_metas: list[tuple[_CellCrop, list[_PerRound]]] = []
    # _iter_cell_crops is called with the first channel's image just to get
    # the slice metadata; the .image_crop field is re-derived per channel
    # below so we never rely on the first channel's data.
    first_image = next(iter(images.values()))
    for crop in _iter_cell_crops(first_image, labels):
        per_round: list[_PerRound] = []
        for round_name, mask in mask_items:
            mask_bool = mask[crop.sl] > 0
            inside = crop.cell_mask & mask_bool
            outside = crop.cell_mask & ~mask_bool
            per_round.append(
                (
                    round_name,
                    inside,
                    outside,
                    float(np.sum(inside)),
                    float(np.sum(outside)),
                )
            )
        crop_metas.append((crop, per_round))

    # Now iterate channels, reusing crop_metas.
    rows_per_channel: dict[str, list[dict]] = {ch: [] for ch in images}
    for crop, per_round in crop_metas:
        core = _core_row(crop)
        for ch_name, image in images.items():
            img_crop = image[crop.sl]
            row = dict(core)
            # whole-cell metrics
            for m in metric_names:
                if m == "area":
                    row[m] = crop.area
                else:
                    row[m] = BUILTIN_METRICS[m](img_crop, crop.cell_mask)
            # per-round inside/outside metrics
            for round_name, inside, outside, inside_area, outside_area in per_round:
                for m in metric_names:
                    if m == "area":
                        row[f"{m}_in_{round_name}"] = inside_area
                        row[f"{m}_out_{round_name}"] = outside_area
                    else:
                        row[f"{m}_in_{round_name}"] = BUILTIN_METRICS[m](
                            img_crop, inside
                        )
                        row[f"{m}_out_{round_name}"] = BUILTIN_METRICS[m](
                            img_crop, outside
                        )
            rows_per_channel[ch_name].append(row)

    per_channel = {}
    for ch_name, rows in rows_per_channel.items():
        if rows:
            df = pd.DataFrame(rows)
            df["label"] = df["label"].astype(np.int32)
            per_channel[ch_name] = df
        else:
            per_channel[ch_name] = pd.DataFrame(
                columns=_build_column_list_with_masks(metric_names, mask_names)
            )
    return _merge_multichannel(per_channel)
