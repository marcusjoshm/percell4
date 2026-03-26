"""Cellpose segmentation wrapper.

Pure function: image in, label array out. No store or GUI coupling.
Lazy-imports cellpose to avoid heavy dependency at startup.

Supports both Cellpose 3.x (Cellpose class, model_type='cyto3') and
Cellpose 4.x (CellposeModel class, cpsam is the only/default model).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def _get_cellpose_version() -> int:
    """Detect major Cellpose version (3 or 4)."""
    from cellpose import models

    if hasattr(models, "CellposeModel") and not hasattr(models, "Cellpose"):
        return 4
    return 3


def run_cellpose(
    image: NDArray,
    model_type: str = "cpsam",
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
    model_type : Cellpose model name.
        - Cellpose 4.x: 'cpsam' (default, SAM-based — the only model in v4)
        - Cellpose 3.x: 'cyto3', 'cyto2', 'cyto', 'nuclei'
    diameter : estimated cell diameter in pixels (None = auto-detect)
    gpu : use GPU acceleration
    channels : channel mapping for Cellpose 3.x (ignored in v4)
    flow_threshold : flow error threshold (higher = more permissive)
    cellprob_threshold : cell probability threshold
    min_size : minimum cell size in pixels

    Returns
    -------
    Label array (H, W) int32 where each cell has a unique integer ID.
    Background is 0.
    """
    from cellpose import models

    version = _get_cellpose_version()

    if version >= 4:
        # Cellpose 4.x: CellposeModel, model_type is ignored (cpsam only)
        model = models.CellposeModel(gpu=gpu)

        # v4 eval returns 3-tuple: (masks, flows, diams)
        # channels parameter is deprecated in v4
        result = model.eval(
            image,
            diameter=diameter,
            flow_threshold=flow_threshold,
            cellprob_threshold=cellprob_threshold,
            min_size=min_size,
        )
        masks = result[0]
    else:
        # Cellpose 3.x: Cellpose class with model_type
        if channels is None:
            channels = [0, 0]

        model_cls = getattr(models, "Cellpose", models.CellposeModel)
        model = model_cls(model_type=model_type, gpu=gpu)

        # v3 eval returns 4-tuple: (masks, flows, styles, diams)
        masks, _flows, _styles, _diams = model.eval(
            image,
            diameter=diameter,
            channels=channels,
            flow_threshold=flow_threshold,
            cellprob_threshold=cellprob_threshold,
            min_size=min_size,
        )

    return np.asarray(masks, dtype=np.int32)
