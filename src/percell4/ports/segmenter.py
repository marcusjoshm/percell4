"""Port: cell segmentation (driven adapter interface)."""

from __future__ import annotations

from typing import Protocol

import numpy as np
from numpy.typing import NDArray


class Segmenter(Protocol):
    """Interface for cell segmentation.

    Implementations: CellposeSegmenter (adapters/cellpose.py).
    """

    def run(
        self,
        image: NDArray,
        model_type: str = "cpsam_v2",
        diameter: float | None = None,
        gpu: bool = False,
        flow_threshold: float = 0.4,
        cellprob_threshold: float = 0.0,
        min_size: int = 15,
        device: str | None = None,
    ) -> NDArray[np.int32]:
        """Run segmentation on an image. Returns label array.

        ``flow_threshold``, ``cellprob_threshold``, and ``min_size`` carry
        the full Cellpose inference controls; defaults match
        :func:`percell4.adapters.cellpose.run_cellpose`.

        ``device`` names an explicit compute device, or None to let the
        implementation resolve one. Deliberately a plain string rather than a
        torch or adapter type: this package must not import either.
        """
        ...
