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
from scipy.stats import mode as _scipy_mode

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


def mode_intensity(image: NDArray, mask: NDArray) -> float:
    """Most frequent pixel intensity within the cell mask.

    Uses scipy.stats.mode for correctness on both integer and float images.
    Ties are broken by returning the smallest value.
    """
    pixels = image[mask]
    if len(pixels) == 0:
        return 0.0
    result = _scipy_mode(pixels, keepdims=False)
    return float(result.mode)


def sg_ratio(image: NDArray, mask: NDArray) -> float:
    """Signal-to-ground contrast ratio (cell-size-invariant).

    Computed as mean(pixels >= 95th percentile) / mean(pixels <= 50th
    percentile).  Uses means (not sums) so the ratio is independent of
    cell area.

    Returns NaN when the ground mean is zero (common when the median
    intensity is zero in fluorescence data).
    """
    pixels = image[mask]
    if len(pixels) == 0:
        return 0.0
    p50, p95 = np.percentile(pixels, [50, 95])
    signal = pixels[pixels >= p95]
    ground = pixels[pixels <= p50]
    if len(ground) == 0 or ground.mean() == 0:
        return float("nan")
    return float(signal.mean() / ground.mean())


# Registry of all built-in metrics
BUILTIN_METRICS: dict[str, MetricFunction] = {
    "mean_intensity": mean_intensity,
    "max_intensity": max_intensity,
    "min_intensity": min_intensity,
    "integrated_intensity": integrated_intensity,
    "std_intensity": std_intensity,
    "median_intensity": median_intensity,
    "area": area,
    "mode_intensity": mode_intensity,
    "sg_ratio": sg_ratio,
}
