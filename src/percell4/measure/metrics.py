"""NaN-safe per-cell metric functions.

Each metric follows the signature::

    def metric(image_crop: NDArray, cell_mask: NDArray[bool]) -> float

where ``image_crop`` is the intensity image cropped to the cell's bounding
box and ``cell_mask`` is a boolean mask of the same shape indicating which
pixels belong to the cell.

Ported from PerCell3 measure/metrics.py.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

MetricFunction = Callable[[NDArray, NDArray], float]


def mean_intensity(image: NDArray, mask: NDArray) -> float:
    """Average pixel intensity within the cell mask (NaN-safe)."""
    pixels = image[mask]
    if len(pixels) == 0:
        return 0.0
    return float(np.nanmean(pixels))


def max_intensity(image: NDArray, mask: NDArray) -> float:
    """Maximum pixel intensity within the cell mask (NaN-safe)."""
    pixels = image[mask]
    if len(pixels) == 0:
        return 0.0
    return float(np.nanmax(pixels))


def min_intensity(image: NDArray, mask: NDArray) -> float:
    """Minimum pixel intensity within the cell mask (NaN-safe)."""
    pixels = image[mask]
    if len(pixels) == 0:
        return 0.0
    return float(np.nanmin(pixels))


def integrated_intensity(image: NDArray, mask: NDArray) -> float:
    """Total (summed) pixel intensity within the cell mask (NaN-safe)."""
    pixels = image[mask]
    if len(pixels) == 0:
        return 0.0
    return float(np.nansum(pixels))


def std_intensity(image: NDArray, mask: NDArray) -> float:
    """Standard deviation of pixel intensity within the cell mask (NaN-safe)."""
    pixels = image[mask]
    if len(pixels) == 0:
        return 0.0
    return float(np.nanstd(pixels))


def median_intensity(image: NDArray, mask: NDArray) -> float:
    """Median pixel intensity within the cell mask (NaN-safe)."""
    pixels = image[mask]
    if len(pixels) == 0:
        return 0.0
    return float(np.nanmedian(pixels))


def area(image: NDArray, mask: NDArray) -> float:
    """Cell area in pixels (count of True pixels in mask)."""
    return float(np.sum(mask))


# Registry of all built-in metrics
BUILTIN_METRICS: dict[str, MetricFunction] = {
    "mean_intensity": mean_intensity,
    "max_intensity": max_intensity,
    "min_intensity": min_intensity,
    "integrated_intensity": integrated_intensity,
    "std_intensity": std_intensity,
    "median_intensity": median_intensity,
    "area": area,
}
