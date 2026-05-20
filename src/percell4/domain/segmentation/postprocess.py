"""Post-processing filters for segmentation label arrays.

All functions return (new_labels, removed_count) and never mutate the input.
Apply in order: edge removal first, then small cell filtering, then relabel.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from skimage.measure import regionprops


def _collect_border_labels(
    labels: NDArray[np.int32],
    edge_margin: int = 0,
) -> set[int]:
    """Return the set of non-zero label IDs that appear on the image border.

    Shared by :func:`filter_edge_cells` (which removes them) and
    :func:`get_edge_labels` (which only reports them). Pure read — never
    mutates ``labels``.
    """
    h, w = labels.shape

    border_labels: set[int] = set()

    # Top and bottom edges
    for row in range(edge_margin + 1):
        border_labels.update(labels[row, :])
        border_labels.update(labels[h - 1 - row, :])

    # Left and right edges
    for col in range(edge_margin + 1):
        border_labels.update(labels[:, col])
        border_labels.update(labels[:, w - 1 - col])

    # Background is not a cell
    border_labels.discard(0)

    return border_labels


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
    border_labels = _collect_border_labels(result, edge_margin=edge_margin)

    if not border_labels:
        return result, 0

    # Zero out border-touching labels
    mask = np.isin(result, list(border_labels))
    result[mask] = 0

    return result, len(border_labels)


def get_edge_labels(
    labels: NDArray[np.int32],
    edge_margin: int = 0,
) -> set[int]:
    """Return the set of cell label IDs that touch the image border.

    Sibling of :func:`filter_edge_cells` that only *reports* edge labels
    rather than zeroing them out. Used at measurement time to compute
    the ``is_edge`` per-cell column without persisting an extra HDF5
    dataset. Pure read — never mutates ``labels``.

    Parameters
    ----------
    labels : 2D label array
    edge_margin : extra pixel margin from the edge (0 = strict border only)

    Returns
    -------
    set[int] : non-zero label IDs that appear on the border
    """
    return _collect_border_labels(labels, edge_margin=edge_margin)


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
