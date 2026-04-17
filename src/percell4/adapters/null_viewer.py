"""Null viewer adapter — no-op implementation of ViewerPort for headless use.

Used by the CLI adapter and batch workflows that don't need visual display.
All methods are silent no-ops. This is the proof that the ViewerPort
abstraction works: use cases accept any ViewerPort, and the CLI provides
one that does nothing.
"""

from __future__ import annotations

from percell4.domain.dataset import DatasetView


class NullViewerAdapter:
    """ViewerPort implementation that does nothing.

    Satisfies the ViewerPort protocol for use cases that require one
    (e.g., LoadDataset calls viewer.show_dataset) without importing
    Qt or napari.
    """

    def show_dataset(self, view: DatasetView) -> None:
        pass

    def clear(self) -> None:
        pass

    def close(self) -> None:
        pass
