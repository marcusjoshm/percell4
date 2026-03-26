"""HDF5-based dataset storage for PerCell4.

Each dataset is a single .h5 file containing images, labels, masks,
measurements, and metadata. DatasetStore provides read/write access
with crash-safe per-operation file handling for writes and an optional
session mode for efficient repeated reads.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pandas as pd
from numpy.typing import NDArray

# Chunk cache size for session reads (64 MB)
_READ_CACHE_BYTES = 64 * 1024 * 1024


def _choose_chunks(shape: tuple[int, ...], is_decay: bool = False) -> tuple[int, ...]:
    """Choose HDF5 chunk shape based on array dimensions.

    - 2D spatial: (256, 256) or smaller if image is small
    - 3D+ with TCSPC: (64, 64, N_bins) — keep full time axis per chunk
    - Other 3D+: (1, 256, 256) — one plane at a time
    """
    ndim = len(shape)
    if ndim == 2:
        return (min(256, shape[0]), min(256, shape[1]))
    if ndim >= 3 and is_decay:
        # TCSPC: spatial chunks of 64x64, full time axis
        return (min(64, shape[0]), min(64, shape[1])) + shape[2:]
    if ndim >= 3:
        # Default: one plane at a time for leading dims
        chunks = [1] * ndim
        chunks[-2] = min(256, shape[-2])
        chunks[-1] = min(256, shape[-1])
        return tuple(chunks)
    return None  # let h5py auto-chunk


def _compression_kwargs(is_decay: bool = False) -> dict[str, Any]:
    """Return compression keyword arguments for dataset creation."""
    if is_decay:
        return {"compression": "lzf"}
    return {"compression": "gzip", "compression_opts": 4, "shuffle": True}


class DatasetStore:
    """Read/write interface for a single .h5 dataset file.

    Writes open/close the file per operation (crash-safe).
    Reads can use per-operation mode or a session context manager
    for efficient repeated access with a large chunk cache.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._session_file: h5py.File | None = None

    # ── Session mode for reads ────────────────────────────────

    @contextmanager
    def open_read(self):
        """Context manager for efficient repeated reads.

        Keeps the file open with a large chunk cache. Use for interactive
        sessions where multiple reads happen in quick succession::

            with store.open_read() as s:
                intensity = s.read_array("intensity")
                labels = s.read_labels("cellpose")
        """
        self._session_file = h5py.File(
            self.path, "r", rdcc_nbytes=_READ_CACHE_BYTES
        )
        try:
            yield self
        finally:
            self._session_file.close()
            self._session_file = None

    def _open_read(self) -> h5py.File:
        """Get a file handle for reading (session or per-operation)."""
        if self._session_file is not None:
            return self._session_file
        return h5py.File(self.path, "r")

    def _close_if_not_session(self, f: h5py.File) -> None:
        """Close the file handle if not in session mode."""
        if f is not self._session_file:
            f.close()

    # ── Generic write operations ──────────────────────────────

    def write_array(
        self,
        hdf5_path: str,
        array: NDArray,
        attrs: dict[str, Any] | None = None,
        is_decay: bool = False,
    ) -> int:
        """Write a numpy array to the specified HDF5 path.

        Returns the number of elements written.
        """
        with h5py.File(self.path, "a") as f:
            if hdf5_path in f:
                del f[hdf5_path]
            chunks = _choose_chunks(array.shape, is_decay=is_decay)
            f.create_dataset(
                hdf5_path,
                data=array,
                chunks=chunks,
                **_compression_kwargs(is_decay=is_decay),
            )
            # Store dimension names if provided in attrs
            if attrs:
                for key, val in attrs.items():
                    f[hdf5_path].attrs[key] = val
        return array.size

    def read_array(self, hdf5_path: str) -> NDArray:
        """Read a numpy array from the specified HDF5 path."""
        f = self._open_read()
        try:
            if hdf5_path not in f:
                raise KeyError(f"Dataset not found: {hdf5_path}")
            return f[hdf5_path][()]
        finally:
            self._close_if_not_session(f)

    # ── DataFrame operations ──────────────────────────────────

    def write_dataframe(self, hdf5_path: str, df: pd.DataFrame) -> int:
        """Write a pandas DataFrame as a CSV string at the given path.

        Returns the number of rows written.
        """
        with h5py.File(self.path, "a") as f:
            if hdf5_path in f:
                del f[hdf5_path]
            csv_str = df.to_csv(index=False)
            f.create_dataset(hdf5_path, data=csv_str)
        return len(df)

    def read_dataframe(self, hdf5_path: str) -> pd.DataFrame:
        """Read a pandas DataFrame from a CSV string at the given path."""
        f = self._open_read()
        try:
            if hdf5_path not in f:
                raise KeyError(f"Dataset not found: {hdf5_path}")
            csv_bytes = f[hdf5_path][()]
            if isinstance(csv_bytes, bytes):
                csv_str = csv_bytes.decode("utf-8")
            else:
                csv_str = str(csv_bytes)
            return pd.read_csv(StringIO(csv_str))
        finally:
            self._close_if_not_session(f)

    # ── Convenience: labels ───────────────────────────────────

    def write_labels(self, name: str, array: NDArray) -> int:
        """Write a segmentation label array at /labels/<name>.

        Enforces int32 dtype. Returns element count.
        """
        if array.ndim != 2:
            raise ValueError(f"Labels must be 2D, got {array.ndim}D")
        array = array.astype(np.int32, copy=False)
        return self.write_array(
            f"labels/{name}", array, attrs={"dims": ["H", "W"]}
        )

    def read_labels(self, name: str) -> NDArray[np.int32]:
        """Read a segmentation label array from /labels/<name>."""
        return self.read_array(f"labels/{name}")

    def list_labels(self) -> list[str]:
        """List all label set names under /labels/."""
        return self.list_groups("labels")

    # ── Convenience: masks ────────────────────────────────────

    def write_mask(self, name: str, array: NDArray) -> int:
        """Write a binary mask at /masks/<name>.

        Enforces uint8 dtype (0/1). Returns element count.
        """
        if array.ndim != 2:
            raise ValueError(f"Mask must be 2D, got {array.ndim}D")
        array = array.astype(np.uint8, copy=False)
        return self.write_array(
            f"masks/{name}", array, attrs={"dims": ["H", "W"]}
        )

    def read_mask(self, name: str) -> NDArray[np.uint8]:
        """Read a binary mask from /masks/<name>."""
        return self.read_array(f"masks/{name}")

    def list_masks(self) -> list[str]:
        """List all mask names under /masks/."""
        return self.list_groups("masks")

    # ── Groups and metadata ───────────────────────────────────

    def list_groups(self, prefix: str) -> list[str]:
        """List child dataset/group names under a given path."""
        f = self._open_read()
        try:
            if prefix not in f:
                return []
            return list(f[prefix].keys())
        finally:
            self._close_if_not_session(f)

    @property
    def metadata(self) -> dict[str, Any]:
        """Read /metadata/ group attributes as a dict."""
        f = self._open_read()
        try:
            if "metadata" not in f:
                return {}
            return dict(f["metadata"].attrs)
        finally:
            self._close_if_not_session(f)

    def set_metadata(self, attrs: dict[str, Any]) -> int:
        """Write attributes to the /metadata/ group. Returns count written."""
        with h5py.File(self.path, "a") as f:
            grp = f.require_group("metadata")
            for key, val in attrs.items():
                grp.attrs[key] = val
        return len(attrs)

    # ── File lifecycle ────────────────────────────────────────

    def create(self, metadata: dict[str, Any] | None = None) -> None:
        """Create a new empty .h5 file, optionally with metadata."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with h5py.File(self.path, "w") as f:
            if metadata:
                grp = f.create_group("metadata")
                for key, val in metadata.items():
                    grp.attrs[key] = val

    def exists(self) -> bool:
        """Check if the .h5 file exists."""
        return self.path.exists()

    def delete_item(self, hdf5_path: str) -> bool:
        """Delete a dataset or group at the given HDF5 path. Returns True if deleted."""
        with h5py.File(self.path, "a") as f:
            if hdf5_path in f:
                del f[hdf5_path]
                return True
        return False

    def rename_item(self, old_path: str, new_path: str) -> bool:
        """Rename a dataset or group within the HDF5 file. Returns True if renamed."""
        with h5py.File(self.path, "a") as f:
            if old_path not in f:
                return False
            if new_path in f:
                raise ValueError(f"Target path already exists: {new_path}")
            f.move(old_path, new_path)
            return True

    @staticmethod
    def create_atomic(
        path: str | Path,
        build_fn,
    ) -> None:
        """Create an .h5 file atomically via write-to-temp-then-rename.

        Use for import operations where crash safety matters::

            def build(h5_file):
                h5_file.create_dataset("intensity", data=image)

            DatasetStore.create_atomic("output.h5", build)
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            suffix=".h5.tmp", dir=path.parent
        )
        os.close(fd)
        try:
            with h5py.File(tmp_path, "w") as f:
                build_fn(f)
            os.replace(tmp_path, path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
