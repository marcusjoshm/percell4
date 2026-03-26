"""Cellpose segmentation wrapper.

Pure function: image in, label array out. No store or GUI coupling.
Lazy-imports cellpose to avoid heavy dependency at startup.
Uses getattr() fallback for version-compatible model instantiation
(Cellpose 4.0 changed the API from Cellpose to CellposeModel).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def run_cellpose(
    image: NDArray,
    model_type: str = "cyto3",
    diameter: float | None = None,
    gpu: bool = False,
    channels: list[int] | None = None,
    flow_threshold: float = 0.4,
    cellprob_threshold: float = 0.0,
    min_size: int = 15,
) -> NDArray[np.int32]:
    """Run Cellpose segmentation on a 2D image.

    Parameters
    ----------
    image : 2D array (H, W) or 3D array (H, W, C) for multi-channel
    model_type : Cellpose model name ('cyto3', 'cyto2', 'cyto', 'nuclei')
    diameter : estimated cell diameter in pixels (None = auto-detect)
    gpu : use GPU acceleration
    channels : channel mapping for Cellpose (default [0, 0] for grayscale)
    flow_threshold : flow error threshold (higher = more permissive)
    cellprob_threshold : cell probability threshold
    min_size : minimum cell size in pixels

    Returns
    -------
    Label array (H, W) int32 where each cell has a unique integer ID.
    Background is 0.
    """
    from cellpose import models

    if channels is None:
        channels = [0, 0]

    # Version-compatible model instantiation
    # Cellpose 4.x uses CellposeModel, earlier versions use Cellpose
    model_cls = getattr(models, "CellposeModel", None) or getattr(
        models, "Cellpose"
    )
    model = model_cls(model_type=model_type, gpu=gpu)

    masks, _flows, _styles, _diams = model.eval(
        image,
        diameter=diameter,
        channels=channels,
        flow_threshold=flow_threshold,
        cellprob_threshold=cellprob_threshold,
        min_size=min_size,
    )

    return masks.astype(np.int32)
