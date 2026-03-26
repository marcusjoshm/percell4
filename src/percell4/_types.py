"""Shared type aliases and cross-module dataclasses for PerCell4."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

# Array type aliases
LabelArray = NDArray[np.int32]
IntensityImage = NDArray[np.float32]
BinaryMask = NDArray[np.uint8]


@dataclass(frozen=True)
class DatasetMetadata:
    """Metadata stored as HDF5 attributes on /metadata/ group."""

    source_files: list[str] = field(default_factory=list)
    channel_names: list[str] = field(default_factory=list)
    dims: list[str] = field(default_factory=list)  # e.g. ["C", "H", "W"]
    pixel_size_um: float | None = None
    laser_frequency_mhz: float | None = None
    time_resolution_ps: float | None = None
    import_params: dict = field(default_factory=dict)
