"""BBox-optimized per-cell measurement.

Pure function: arrays in, DataFrame out. No store or GUI coupling.
Uses scipy.ndimage.find_objects for O(1) bounding box lookup per cell.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.ndimage import find_objects
from skimage.measure import regionprops

from percell4.measure.metrics import BUILTIN_METRICS

logger = logging.getLogger(__name__)


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
    if metrics is None:
        metric_names = list(BUILTIN_METRICS.keys())
    else:
        metric_names = metrics
        for name in metric_names:
            if name not in BUILTIN_METRICS:
                raise ValueError(f"Unknown metric: {name!r}")

    # Handle empty labels
    if labels.max() == 0:
        columns = _build_column_list(metric_names, has_mask=mask is not None)
        return pd.DataFrame(columns=columns)

    # Get bounding boxes via find_objects (single C-level pass)
    slices = find_objects(labels)

    # Get cell properties via regionprops (centroids, area)
    props = regionprops(labels)

    rows: list[dict] = []

    for prop in props:
        label_val = prop.label
        sl = slices[label_val - 1]  # find_objects is 0-indexed

        if sl is None:
            continue

        # Crop to bounding box
        label_crop = labels[sl]
        image_crop = image[sl]
        cell_mask = label_crop == label_val

        if not np.any(cell_mask):
            continue

        # Core cell properties
        cy, cx = prop.centroid
        min_row, min_col, max_row, max_col = prop.bbox
        row: dict = {
            "label": int(label_val),
            "centroid_y": float(cy),
            "centroid_x": float(cx),
            "bbox_y": int(min_row),
            "bbox_x": int(min_col),
            "bbox_h": int(max_row - min_row),
            "bbox_w": int(max_col - min_col),
            "area": float(prop.area),
        }

        # Compute whole-cell metrics
        for name in metric_names:
            if name == "area":
                row[name] = float(prop.area)
            else:
                row[name] = BUILTIN_METRICS[name](image_crop, cell_mask)

        # Compute masked metrics if mask provided
        if mask is not None:
            mask_crop = mask[sl]
            mask_bool = mask_crop > 0

            inside_mask = cell_mask & mask_bool
            outside_mask = cell_mask & ~mask_bool

            for name in metric_names:
                if name == "area":
                    row[f"{name}_mask_inside"] = float(np.sum(inside_mask))
                    row[f"{name}_mask_outside"] = float(np.sum(outside_mask))
                else:
                    row[f"{name}_mask_inside"] = BUILTIN_METRICS[name](
                        image_crop, inside_mask
                    )
                    row[f"{name}_mask_outside"] = BUILTIN_METRICS[name](
                        image_crop, outside_mask
                    )

        rows.append(row)

    if not rows:
        columns = _build_column_list(metric_names, has_mask=mask is not None)
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(rows)
    df["label"] = df["label"].astype(np.int32)
    return df


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

    result_df: pd.DataFrame | None = None

    for ch_name, image in images.items():
        ch_df = measure_cells(image, labels, metrics=metrics, mask=mask)

        if ch_df.empty:
            continue

        if result_df is None:
            # First channel: keep core columns + rename metric columns
            core_cols = ["label", "centroid_y", "centroid_x",
                         "bbox_y", "bbox_x", "bbox_h", "bbox_w", "area"]
            rename_map = {}
            for col in ch_df.columns:
                if col not in core_cols:
                    rename_map[col] = f"{ch_name}_{col}"
            result_df = ch_df.rename(columns=rename_map)
        else:
            # Subsequent channels: only metric columns, prefixed
            core_cols = {"label", "centroid_y", "centroid_x",
                         "bbox_y", "bbox_x", "bbox_h", "bbox_w", "area"}
            metric_cols = [c for c in ch_df.columns if c not in core_cols]
            rename_map = {col: f"{ch_name}_{col}" for col in metric_cols}
            ch_metrics = ch_df[["label"] + metric_cols].rename(columns=rename_map)
            result_df = result_df.merge(ch_metrics, on="label", how="outer")

    if result_df is None:
        return pd.DataFrame()

    return result_df


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
    if metrics is None:
        metric_names = list(BUILTIN_METRICS.keys())
    else:
        metric_names = metrics

    if labels.max() == 0:
        return pd.DataFrame()

    slices = find_objects(labels)
    props = regionprops(labels)
    rows: list[dict] = []

    for prop in props:
        label_val = prop.label
        sl = slices[label_val - 1]
        if sl is None:
            continue

        label_crop = labels[sl]
        image_crop = image[sl]
        mask_crop = mask[sl]
        cell_mask = label_crop == label_val

        if not np.any(cell_mask):
            continue

        cy, cx = prop.centroid
        min_row, min_col, max_row, max_col = prop.bbox
        row: dict = {
            "label": int(label_val),
            "centroid_y": float(cy),
            "centroid_x": float(cx),
            "bbox_y": int(min_row),
            "bbox_x": int(min_col),
            "bbox_h": int(max_row - min_row),
            "bbox_w": int(max_col - min_col),
            "area": float(prop.area),
        }

        # Whole-cell metrics
        for name in metric_names:
            if name == "area":
                row[name] = float(prop.area)
            else:
                row[name] = BUILTIN_METRICS[name](image_crop, cell_mask)

        # Per-ROI metrics — computed on the small crop, fast
        for label_v, roi_name in roi_names.items():
            roi_cell = cell_mask & (mask_crop == label_v)
            n_pixels = int(roi_cell.sum())
            row[f"{roi_name}_area"] = float(n_pixels)
            if n_pixels > 0:
                for name in metric_names:
                    if name != "area":
                        row[f"{roi_name}_{name}"] = BUILTIN_METRICS[name](
                            image_crop, roi_cell
                        )
            else:
                for name in metric_names:
                    if name != "area":
                        row[f"{roi_name}_{name}"] = 0.0

        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["label"] = df["label"].astype(np.int32)
    return df


def _build_column_list(metric_names: list[str], has_mask: bool) -> list[str]:
    """Build the expected column list for an empty DataFrame."""
    cols = ["label", "centroid_y", "centroid_x",
            "bbox_y", "bbox_x", "bbox_h", "bbox_w", "area"]
    for name in metric_names:
        if name not in cols:
            cols.append(name)
    if has_mask:
        for name in metric_names:
            cols.append(f"{name}_mask_inside")
            cols.append(f"{name}_mask_outside")
    return cols
