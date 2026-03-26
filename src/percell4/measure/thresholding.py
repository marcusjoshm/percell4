"""Thresholding methods for generating binary masks.

All threshold functions return ``(mask_uint8, threshold_value)`` where the
mask is 0/1 uint8. Optional Gaussian smoothing can be applied before
thresholding. Adapted from PerCell3 measure/thresholding.py.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def threshold_otsu(image: NDArray) -> tuple[NDArray[np.uint8], float]:
    """Otsu's method — best for bimodal histograms."""
    from skimage.filters import threshold_otsu as _otsu

    value = float(_otsu(image))
    mask = (image > value).astype(np.uint8)
    return mask, value


def threshold_triangle(image: NDArray) -> tuple[NDArray[np.uint8], float]:
    """Triangle method — better for skewed histograms where one peak dominates."""
    from skimage.filters import threshold_triangle as _triangle

    value = float(_triangle(image))
    mask = (image > value).astype(np.uint8)
    return mask, value


def threshold_li(image: NDArray) -> tuple[NDArray[np.uint8], float]:
    """Li's minimum cross-entropy — good for low-contrast images."""
    from skimage.filters import threshold_li as _li

    value = float(_li(image))
    mask = (image > value).astype(np.uint8)
    return mask, value


def threshold_adaptive(
    image: NDArray,
    block_size: int | None = None,
) -> tuple[NDArray[np.uint8], float]:
    """Local adaptive thresholding — best for uneven illumination.

    Block size is auto-calculated if not provided:
    ``max(15, (min(image.shape) // 10) | 1)`` (ensures odd, >= 15).

    Returns the mask and the global Otsu value (for reference).
    """
    from skimage.filters import threshold_local
    from skimage.filters import threshold_otsu as _otsu

    if block_size is None:
        block_size = max(15, (min(image.shape) // 10) | 1)

    # Ensure odd block size
    if block_size % 2 == 0:
        block_size += 1

    local_thresh = threshold_local(image.astype(np.float32), block_size=block_size)
    mask = (image > local_thresh).astype(np.uint8)

    # Return global Otsu value as reference
    global_value = float(_otsu(image))
    return mask, global_value


def threshold_manual(
    image: NDArray,
    value: float,
) -> tuple[NDArray[np.uint8], float]:
    """Manual threshold — user provides the exact value."""
    mask = (image > value).astype(np.uint8)
    return mask, float(value)


def apply_gaussian_smoothing(
    image: NDArray,
    sigma: float | None,
) -> NDArray:
    """Apply Gaussian smoothing if sigma is set.

    Returns smoothed float32 copy, or original image unchanged if
    sigma is None or <= 0.
    """
    if sigma is None or sigma <= 0:
        return image
    from scipy.ndimage import gaussian_filter

    return gaussian_filter(image.astype(np.float32), sigma=sigma)


# Registry of available threshold methods
THRESHOLD_METHODS: dict[str, callable] = {
    "otsu": threshold_otsu,
    "triangle": threshold_triangle,
    "li": threshold_li,
    "adaptive": threshold_adaptive,
    "manual": threshold_manual,
}
