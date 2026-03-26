"""Tests for Cellpose wrapper.

The actual Cellpose test is marked slow (requires model download).
The import/instantiation smoke test runs without the model.
"""

from __future__ import annotations

import numpy as np
import pytest


def test_cellpose_importable():
    """Cellpose module can be imported without errors."""
    from percell4.segment.cellpose import run_cellpose  # noqa: F401


@pytest.mark.slow
def test_cellpose_runs_on_synthetic_image():
    """Run Cellpose on a small synthetic image (requires model download)."""
    from percell4.segment.cellpose import run_cellpose

    # Create a simple image with bright circles on dark background
    image = np.zeros((128, 128), dtype=np.float32)
    rr, cc = np.ogrid[:128, :128]
    for cy, cx in [(30, 30), (30, 90), (90, 60)]:
        mask = (rr - cy) ** 2 + (cc - cx) ** 2 < 15**2
        image[mask] = 200.0

    labels = run_cellpose(image, model_type="cyto3", diameter=30, gpu=False)

    assert labels.dtype == np.int32
    assert labels.shape == (128, 128)
    assert labels.max() > 0  # at least one cell found
