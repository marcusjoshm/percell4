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
