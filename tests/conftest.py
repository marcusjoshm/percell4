"""Shared test fixtures for PerCell4."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def tmp_h5(tmp_path: Path) -> Path:
    """Return a temporary .h5 file path (does not create the file)."""
    return tmp_path / "test_dataset.h5"


@pytest.fixture
def sample_labels() -> np.ndarray:
    """Synthetic 100x100 label array with 5 cells.

    Cell 1: 20x20 block at (10, 10)
    Cell 2: 20x20 block at (10, 60)
    Cell 3: 20x20 block at (50, 10)
    Cell 4: 20x20 block at (50, 60)
    Cell 5: 15x15 block at (70, 35)
    """
    labels = np.zeros((100, 100), dtype=np.int32)
    labels[10:30, 10:30] = 1
    labels[10:30, 60:80] = 2
    labels[50:70, 10:30] = 3
    labels[50:70, 60:80] = 4
    labels[70:85, 35:50] = 5
    return labels


@pytest.fixture
def sample_image() -> np.ndarray:
    """Synthetic 100x100 intensity image with known values per cell region.

    Cell 1 region: intensity 100
    Cell 2 region: intensity 200
    Cell 3 region: intensity 150
    Cell 4 region: intensity 250
    Cell 5 region: intensity 175
    Background: intensity 10
    """
    image = np.full((100, 100), 10.0, dtype=np.float32)
    image[10:30, 10:30] = 100.0
    image[10:30, 60:80] = 200.0
    image[50:70, 10:30] = 150.0
    image[50:70, 60:80] = 250.0
    image[70:85, 35:50] = 175.0
    return image
