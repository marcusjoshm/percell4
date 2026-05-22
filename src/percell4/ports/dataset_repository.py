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

    def read_channel_images(
        self,
        handle: DatasetHandle,
        view_bin: int = 1,
        timepoint: int | None = None,
    ) -> dict[str, NDArray[np.float32]]:
        """Read channel images from the dataset as ``{name: 2D plane}``.

        ``view_bin`` (>= 1) downsamples each channel via ``sum_bin_2d``
        on the trailing two axes. At k=1 the arrays are byte-identical
        to what was written. See ``src/percell4/domain/io/view_bin.py``.

        For a time-lapse dataset, ``timepoint`` selects the frame (default
        frame 0) so the returned planes are always 2D.
        """
        ...

    # ── Segmentation labels ──────────────────────────────────

    def read_labels(
        self,
        handle: DatasetHandle,
        name: str,
        view_bin: int = 1,
        timepoint: int | None = None,
    ) -> NDArray[np.int32]:
        """Read a segmentation label array by name.

        ``view_bin`` downsamples via block mode (ties resolve to 0). When
        ``timepoint`` is given, returns that single frame of a time-stacked
        ``(T, H, W)`` labels resource.
        """
        ...

    def write_labels(
        self,
        handle: DatasetHandle,
        name: str,
        data: NDArray,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        """Write a segmentation label array.

        ``attrs`` (optional) are stamped onto the stored dataset.
        Phase-6 Creators use this to record ``created_at_bin``.
        """
        ...

    def list_labels(self, handle: DatasetHandle) -> list[str]:
        """List all segmentation label names."""
        ...

    # ── Masks ────────────────────────────────────────────────

    def read_mask(
        self,
        handle: DatasetHandle,
        name: str,
        view_bin: int = 1,
    ) -> NDArray[np.uint8]:
        """Read a mask array by name.

        ``view_bin`` downsamples via majority vote.
        """
        ...

    def write_mask(
        self,
        handle: DatasetHandle,
        name: str,
        data: NDArray,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        """Write a mask array.

        ``attrs`` (optional) are stamped onto the stored dataset.
        Phase-6 Creators use this to record ``created_at_bin``.
        """
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

    # ── Tracks (lineage tables) ──────────────────────────────

    def write_tracks(
        self, handle: DatasetHandle, name: str, df: pd.DataFrame
    ) -> None:
        """Write a per-track lineage table at /tracks/<name>."""
        ...

    def read_tracks(self, handle: DatasetHandle, name: str) -> pd.DataFrame:
        """Read the lineage table from /tracks/<name>. Raises KeyError if missing."""
        ...

    def list_tracks(self, handle: DatasetHandle) -> list[str]:
        """List all lineage-table names under /tracks/."""
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

    def read_array(
        self,
        handle: DatasetHandle,
        path: str,
        view_bin: int = 1,
    ) -> NDArray:
        """Read a numpy array from an arbitrary HDF5 path. Raises KeyError if missing.

        ``view_bin`` dispatches by path prefix (``/intensity``, ``/decay/*``,
        ``/labels/*``, ``/masks/*``, ``/phasor/*``). Unknown prefixes pass
        through unchanged. See :meth:`DatasetStore.read_array`.
        """
        ...

    def read_metadata(self, handle: DatasetHandle) -> dict[str, Any]:
        """Read /metadata attrs FRESH from disk.

        ``handle.metadata`` is a snapshot taken at open time and does NOT
        reflect later writes. Consumers that need post-write metadata
        (e.g., FLIM calibration values written by an in-session TCSPC
        import) must call this method instead of reading the snapshot.
        """
        ...

    def delete_path(self, handle: DatasetHandle, path: str) -> bool:
        """Delete an HDF5 dataset or group at ``path``. Returns True if deleted.

        Used by use cases to invalidate downstream cached layers when
        upstream data is rewritten (e.g., compute_phasor invalidating
        ``/phasor/<ch>/g_filtered`` when ``/phasor/<ch>/g`` is rewritten,
        so a stale wavelet view of old phasor doesn't survive).
        """
        ...

    # ── Groups (for stored group columns) ────────────────────

    def read_group_columns(self, handle: DatasetHandle) -> pd.DataFrame | None:
        """Read and merge all stored group columns into a single DataFrame.

        Returns None if no groups exist. The returned DataFrame has a 'label'
        column plus one column per group assignment.
        """
        ...
