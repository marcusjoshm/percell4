"""Port: dataset storage and retrieval."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from percell4.domain.dataset import DatasetHandle, DatasetView


class DatasetRepository(Protocol):
    """Interface for dataset persistence.

    Implementations: Hdf5DatasetRepository (adapters/hdf5_store.py).
    """

    # ── Lifecycle ────────────────────────────────────────────

    def open(self, path: Path) -> DatasetHandle:
        """Open an existing dataset file. Raises if not found."""
        ...

    def build_view(self, handle: DatasetHandle) -> DatasetView:
        """Build a displayable snapshot from an open dataset."""
        ...

    # ── Channel images ───────────────────────────────────────

    def read_channel_images(self, handle: DatasetHandle) -> dict[str, NDArray[np.float32]]:
        """Read all channel images from the dataset."""
        ...

    # ── Segmentation labels ──────────────────────────────────

    def read_labels(self, handle: DatasetHandle, name: str) -> NDArray[np.int32]:
        """Read a segmentation label array by name."""
        ...

    def write_labels(self, handle: DatasetHandle, name: str, data: NDArray) -> None:
        """Write a segmentation label array."""
        ...

    def list_labels(self, handle: DatasetHandle) -> list[str]:
        """List all segmentation label names."""
        ...

    # ── Masks ────────────────────────────────────────────────

    def read_mask(self, handle: DatasetHandle, name: str) -> NDArray[np.uint8]:
        """Read a mask array by name."""
        ...

    def write_mask(self, handle: DatasetHandle, name: str, data: NDArray) -> None:
        """Write a mask array."""
        ...

    def list_masks(self, handle: DatasetHandle) -> list[str]:
        """List all mask names."""
        ...

    # ── Measurements ─────────────────────────────────────────

    def write_measurements(self, handle: DatasetHandle, df: pd.DataFrame) -> None:
        """Write the measurements DataFrame."""
        ...

    def read_measurements(self, handle: DatasetHandle) -> pd.DataFrame | None:
        """Read the measurements DataFrame, or None if not present."""
        ...

    # ── Generic arrays (phasor maps, decay, etc.) ────────────

    def write_array(
        self,
        handle: DatasetHandle,
        path: str,
        data: NDArray,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        """Write a numpy array at an arbitrary HDF5 path."""
        ...

    def read_array(self, handle: DatasetHandle, path: str) -> NDArray:
        """Read a numpy array from an arbitrary HDF5 path. Raises KeyError if missing."""
        ...

    # ── Groups (for stored group columns) ────────────────────

    def read_group_columns(self, handle: DatasetHandle) -> pd.DataFrame | None:
        """Read and merge all stored group columns into a single DataFrame.

        Returns None if no groups exist. The returned DataFrame has a 'label'
        column plus one column per group assignment.
        """
        ...
