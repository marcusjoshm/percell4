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
        model_type: str = "cyto3",
        diameter: float | None = None,
        gpu: bool = False,
    ) -> NDArray[np.int32]:
        """Run segmentation on an image. Returns label array."""
        ...
