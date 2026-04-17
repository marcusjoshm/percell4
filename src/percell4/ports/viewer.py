"""Port: viewer display (driven adapter interface)."""

from __future__ import annotations

from typing import Protocol

from percell4.domain.dataset import DatasetView


class ViewerPort(Protocol):
    """Interface for the image viewer.

    Implementations: NapariViewerAdapter (adapters/napari_viewer.py).
    Minimal surface for Stage 1 — will grow as use cases are migrated.
    """

    def show_dataset(self, view: DatasetView) -> None:
        """Clear existing layers and display the dataset contents."""
        ...

    def clear(self) -> None:
        """Remove all layers from the viewer."""
        ...

    def close(self) -> None:
        """Close the viewer window."""
        ...
