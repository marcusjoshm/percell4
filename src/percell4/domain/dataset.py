"""Domain types for dataset representation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

# Type aliases for domain clarity
ChannelName = str
LayerName = str
CellId = int


@dataclass(frozen=True)
class DatasetHandle:
    """Represents an open dataset — a handle to the storage, not the data itself.

    Immutable. Passed around to identify which dataset is active.
    """

    path: Path
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.stem


@dataclass
class DatasetView:
    """Read-only snapshot of a dataset's displayable content.

    Built by the repository from a DatasetHandle. Passed to the
    ViewerPort to populate the display. Contains numpy arrays —
    not h5py datasets, not napari layers.
    """

    channel_images: dict[ChannelName, NDArray[np.float32]]
    labels: dict[LayerName, NDArray[np.int32]]
    masks: dict[LayerName, NDArray[np.uint8]]
