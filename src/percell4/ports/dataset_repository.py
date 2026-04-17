"""Port: dataset storage and retrieval."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from percell4.domain.dataset import DatasetHandle, DatasetView


class DatasetRepository(Protocol):
    """Interface for dataset persistence.

    Implementations: Hdf5DatasetRepository (adapters/hdf5_store.py).
    """

    def open(self, path: Path) -> DatasetHandle:
        """Open an existing dataset file. Raises if not found."""
        ...

    def build_view(self, handle: DatasetHandle) -> DatasetView:
        """Build a displayable snapshot from an open dataset."""
        ...
