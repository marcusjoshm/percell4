"""Post-processing filters for segmentation label arrays.

All functions return (new_labels, removed_count) and never mutate the input.
Apply in order: edge removal first, then small cell filtering, then relabel.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from skimage.measure import regionprops


def filter_edge_cells(
    labels: NDArray[np.int32],
    edge_margin: int = 0,
) -> tuple[NDArray[np.int32], int]:
    """Remove cells that touch the image border.

    Parameters
    ----------
    labels : 2D label array
    edge_margin : extra pixel margin from the edge (0 = strict border only)

    Returns
    -------
    (filtered_labels, count_removed)
    """
    result = labels.copy()
    h, w = result.shape

    # Collect labels touching any border (including margin)
    border_labels: set[int] = set()

    # Top and bottom edges
    for row in range(edge_margin + 1):
        border_labels.update(result[row, :])
        border_labels.update(result[h - 1 - row, :])

    # Left and right edges
    for col in range(edge_margin + 1):
        border_labels.update(result[:, col])
        border_labels.update(result[:, w - 1 - col])

    # Remove background from the set
    border_labels.discard(0)

    if not border_labels:
        return result, 0

    # Zero out border-touching labels
    mask = np.isin(result, list(border_labels))
    result[mask] = 0

    return result, len(border_labels)


def filter_small_cells(
    labels: NDArray[np.int32],
    min_area: int,
) -> tuple[NDArray[np.int32], int]:
    """Remove cells with area below the minimum threshold.

    Parameters
    ----------
    labels : 2D label array
    min_area : minimum cell area in pixels

    Returns
    -------
    (filtered_labels, count_removed)
    """
    result = labels.copy()
    props = regionprops(result)

    small_labels = [p.label for p in props if p.area < min_area]

    if not small_labels:
        return result, 0

    mask = np.isin(result, small_labels)
    result[mask] = 0

    return result, len(small_labels)


def relabel_sequential(labels: NDArray[np.int32]) -> NDArray[np.int32]:
    """Renumber labels to be sequential starting from 1.

    After filtering, labels may have gaps (e.g., [1, 3, 7]).
    This renumbers them to [1, 2, 3].

    Always call this BEFORE measuring — prevents sparse label ID memory
    issues in scipy.ndimage.find_objects.
    """
    result = labels.copy()
    unique_labels = np.unique(result)
    unique_labels = unique_labels[unique_labels > 0]

    if len(unique_labels) == 0:
        return result

    # Check if already sequential
    if np.array_equal(unique_labels, np.arange(1, len(unique_labels) + 1)):
        return result

    # Build remapping
    new_label = 1
    for old_label in unique_labels:
        if old_label != new_label:
            result[labels == old_label] = new_label
        new_label += 1

    return result
