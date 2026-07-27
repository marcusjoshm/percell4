"""Shared test fixtures for PerCell4."""

from __future__ import annotations

import os

# Pin qtpy's binding before anything can import it. A dev machine often has
# PyQt5, PyQt6, and PySide6 installed at once (napari and its plugins pull
# different ones); without this qtpy picks whichever it finds first, so the
# suite passes, fails, or segfaults from run to run. CI installs only PyQt5,
# so pinning it here makes a local run reproduce CI rather than testing a
# configuration nobody ships. pytest-qt's own binding is pinned via the
# ``qt_api`` ini option in pyproject.toml — both are needed, they select
# independently.
#
# setdefault, not assignment: an explicit QT_API in the environment still wins,
# so it stays possible to check binding portability deliberately.
os.environ.setdefault("QT_API", "pyqt5")

# pyqtgraph does NOT honour QT_API — it runs its own binding detection and will
# happily import PyQt6 when PyQt6 is installed, even with QT_API=pyqt5. Two
# bindings loaded into one process abort with SIGABRT the moment both try to
# own the Qt event loop, which is what made a full local run die partway
# through tests/test_gui while the same directory passed when run alone.
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402


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
