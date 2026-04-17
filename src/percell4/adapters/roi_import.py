"""Import segmentation masks from external sources.

Supports ImageJ ROI .zip files and Cellpose _seg.npy files.
Both produce (H, W) int32 label arrays compatible with the rest
of the pipeline.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray


def import_imagej_rois(
    zip_path: str | Path,
    shape: tuple[int, int],
) -> NDArray[np.int32]:
    """Convert an ImageJ ROI .zip file to a label array.

    Each ROI in the zip becomes a unique label ID (1, 2, 3, ...).
    Overlapping ROIs: the last one wins (overwrites earlier labels).

    Parameters
    ----------
    zip_path : path to the .zip file containing ImageJ ROIs
    shape : (height, width) of the target image

    Returns
    -------
    Label array (H, W) int32.
    """
    import roifile

    labels = np.zeros(shape, dtype=np.int32)
    rois = roifile.roiread(str(zip_path))

    if not isinstance(rois, list):
        rois = [rois]

    for label_id, roi in enumerate(rois, start=1):
        coords = roi.coordinates()
        if coords is None or len(coords) == 0:
            continue

        # ROI coordinates are (row, col) pairs defining a polygon
        # Rasterize the polygon into the label array
        from skimage.draw import polygon

        rr, cc = polygon(coords[:, 1], coords[:, 0], shape=shape)
        labels[rr, cc] = label_id

    return labels


def import_cellpose_seg(seg_path: str | Path) -> NDArray[np.int32]:
    """Load a Cellpose _seg.npy segmentation file.

    Cellpose saves segmentation results as .npy files containing a dict
    with a 'masks' key holding the label array.

    Parameters
    ----------
    seg_path : path to the _seg.npy file

    Returns
    -------
    Label array (H, W) int32.
    """
    seg_path = Path(seg_path)

    loaded = np.load(str(seg_path), allow_pickle=True)

    # _seg.npy files are saved with np.save({...}) which wraps the dict
    # in a 0-d object array. .item() extracts it.
    try:
        data = loaded.item()
    except (ValueError, AttributeError):
        data = loaded

    if not isinstance(data, dict):
        raise ValueError(f"Expected dict in _seg.npy, got {type(data).__name__}")

    if "masks" not in data:
        raise KeyError("_seg.npy file does not contain 'masks' key")

    masks = data["masks"]
    return np.asarray(masks, dtype=np.int32)
