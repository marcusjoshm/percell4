"""Adapter: HDF5-based DatasetRepository implementation.

Wraps the existing DatasetStore to conform to the DatasetRepository port.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from percell4.domain.dataset import DatasetHandle, DatasetView
from percell4.domain.io.layout import split_channels_2d, split_intensity_layers
from percell4.store import DatasetStore


def _build_handle_metadata(store: DatasetStore) -> dict[str, Any]:
    """Build DatasetHandle.metadata from a DatasetStore.

    Combines the HDF5 ``/metadata`` attributes (channel_names, etc.) with
    the inventory of available segmentations and masks. Segmentations are
    label layers that are NOT in the mask set.
    """
    md = dict(store.metadata)
    label_names = store.list_labels()
    mask_names = store.list_masks()
    mask_set = set(mask_names)
    md["segmentation_names"] = [n for n in label_names if n not in mask_set]
    md["mask_names"] = list(mask_names)
    return md


class Hdf5DatasetRepository:
    """DatasetRepository backed by HDF5 files via DatasetStore.

    Conforms to percell4.ports.dataset_repository.DatasetRepository.
    Caches DatasetStore instances by path to avoid re-opening HDF5
    file metadata on every call (50-200ms overhead per open).
    """

    def __init__(self) -> None:
        self._stores: dict[Path, DatasetStore] = {}

    def _store(self, handle: DatasetHandle) -> DatasetStore:
        path = handle.path
        if path not in self._stores:
            self._stores[path] = DatasetStore(path)
        return self._stores[path]

    def close(self, handle: DatasetHandle) -> None:
        """Remove a cached store (called on dataset close)."""
        self._stores.pop(handle.path, None)

    # ── Lifecycle ────────────────────────────────────────────

    def open(self, path: Path) -> DatasetHandle:
        store = DatasetStore(path)
        if not store.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")
        # Detect a dims-corrupted dataset at open rather than silently treating
        # a mis-stamped (T,H,W) array as single-timepoint (U4).
        store.check_intensity_dims_consistency()
        return DatasetHandle(path=path, metadata=_build_handle_metadata(store))

    def build_view(self, handle: DatasetHandle) -> DatasetView:
        store = self._store(handle)
        channel_images: dict[str, np.ndarray] = {}
        labels: dict[str, np.ndarray] = {}
        masks: dict[str, np.ndarray] = {}

        with store.open_read() as s:
            intensity = s.read_array("intensity")
            channel_names = list(handle.metadata.get("channel_names", []))
            n_timepoints = int(handle.metadata.get("n_timepoints", 1) or 1)

            # Keep any leading time axis so napari builds a single dims
            # slider; disambiguate T-vs-C via n_timepoints.
            for name, arr in split_intensity_layers(
                intensity, channel_names, n_timepoints
            ):
                channel_images[name] = arr.astype(np.float32)

            mask_names = set(s.list_masks())
            for label_name in s.list_labels():
                if label_name not in mask_names:
                    labels[label_name] = s.read_labels(label_name)

            for mask_name in s.list_masks():
                masks[mask_name] = s.read_mask(mask_name)

        return DatasetView(
            channel_images=channel_images,
            labels=labels,
            masks=masks,
        )

    # ── Channel images ───────────────────────────────────────

    def read_channel_images(
        self,
        handle: DatasetHandle,
        view_bin: int = 1,
        timepoint: int | None = None,
    ) -> dict[str, NDArray[np.float32]]:
        """Return ``{channel_name: 2D plane}`` for compute consumers.

        For a time-lapse dataset a single timepoint is selected (``timepoint``,
        defaulting to frame 0) and split into 2D per-channel planes, so the
        ``{name: (H, W)}`` contract holds regardless of the time axis.
        """
        store = self._store(handle)
        n_timepoints = int(handle.metadata.get("n_timepoints", 1) or 1)
        channel_names = list(handle.metadata.get("channel_names", []))
        result: dict[str, np.ndarray] = {}
        with store.open_read() as s:
            if n_timepoints > 1:
                t = 0 if timepoint is None else int(timepoint)
                plane = s.read_array_frame("intensity", t, view_bin=view_bin)
            else:
                plane = s.read_array("intensity", view_bin=view_bin)
            for name, arr in split_channels_2d(plane, channel_names):
                result[name] = arr.astype(np.float32)
        return result

    def read_channel(
        self,
        handle: DatasetHandle,
        path: str,
        channel_idx: int,
        view_bin: int = 1,
        timepoint: int | None = None,
    ) -> NDArray:
        return self._store(handle).read_channel(
            path, channel_idx, view_bin=view_bin, timepoint=timepoint
        )

    # ── Segmentation labels ──────────────────────────────────

    def read_labels(
        self,
        handle: DatasetHandle,
        name: str,
        view_bin: int = 1,
        timepoint: int | None = None,
    ) -> NDArray[np.int32]:
        return self._store(handle).read_labels(
            name, view_bin=view_bin, timepoint=timepoint
        )

    def write_labels(
        self,
        handle: DatasetHandle,
        name: str,
        data: NDArray,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        self._store(handle).write_labels(name, data, attrs=attrs)

    def write_labels_frame(
        self,
        handle: DatasetHandle,
        name: str,
        frame: NDArray,
        timepoint: int,
    ) -> None:
        self._store(handle).write_labels_frame(name, frame, timepoint)

    def list_labels(self, handle: DatasetHandle) -> list[str]:
        return self._store(handle).list_labels()

    # ── Masks ────────────────────────────────────────────────

    def read_mask(
        self,
        handle: DatasetHandle,
        name: str,
        view_bin: int = 1,
        timepoint: int | None = None,
    ) -> NDArray[np.uint8]:
        return self._store(handle).read_mask(
            name, view_bin=view_bin, timepoint=timepoint
        )

    def write_mask(
        self,
        handle: DatasetHandle,
        name: str,
        data: NDArray,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        self._store(handle).write_mask(name, data, attrs=attrs)

    def write_mask_frame(
        self,
        handle: DatasetHandle,
        name: str,
        frame: NDArray,
        timepoint: int,
    ) -> None:
        self._store(handle).write_mask_frame(name, frame, timepoint)

    def list_masks(self, handle: DatasetHandle) -> list[str]:
        return self._store(handle).list_masks()

    # ── Measurements ─────────────────────────────────────────

    def write_measurements(self, handle: DatasetHandle, df: pd.DataFrame) -> None:
        self._store(handle).write_dataframe("measurements", df)

    def read_measurements(self, handle: DatasetHandle) -> pd.DataFrame | None:
        try:
            return self._store(handle).read_dataframe("measurements")
        except KeyError:
            return None

    # ── Tracks (lineage tables) ──────────────────────────────

    def write_tracks(
        self, handle: DatasetHandle, name: str, df: pd.DataFrame
    ) -> None:
        self._store(handle).write_tracks(name, df)

    def read_tracks(self, handle: DatasetHandle, name: str) -> pd.DataFrame:
        return self._store(handle).read_tracks(name)

    def list_tracks(self, handle: DatasetHandle) -> list[str]:
        return self._store(handle).list_tracks()

    # ── Generic arrays ───────────────────────────────────────

    def write_array(
        self,
        handle: DatasetHandle,
        path: str,
        data: NDArray,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        self._store(handle).write_array(path, data, attrs=attrs)

    def read_array(
        self, handle: DatasetHandle, path: str, view_bin: int = 1
    ) -> NDArray:
        return self._store(handle).read_array(path, view_bin=view_bin)

    def array_exists(self, handle: DatasetHandle, path: str) -> bool:
        """True if ``path`` is a dataset. Metadata only — no decompression."""
        return self._store(handle).array_exists(path)

    def read_metadata(self, handle: DatasetHandle) -> dict[str, Any]:
        """Read /metadata attrs fresh from disk (not the handle's snapshot)."""
        return self._store(handle).metadata

    def write_metadata(
        self, handle: DatasetHandle, attrs: dict[str, Any]
    ) -> None:
        """Merge attrs into /metadata on disk. Delegates to DatasetStore."""
        self._store(handle).set_metadata(attrs)

    def delete_path(self, handle: DatasetHandle, path: str) -> bool:
        return self._store(handle).delete_item(path)

    # ── Groups ───────────────────────────────────────────────

    def read_group_columns(self, handle: DatasetHandle) -> pd.DataFrame | None:
        """Read all /groups/<name> DataFrames and merge into one."""
        store = self._store(handle)
        try:
            with store.open_read() as s:
                group_names = s.list_groups("groups")
                if not group_names:
                    return None

                merged = None
                for name in group_names:
                    group_df = s.read_dataframe(f"groups/{name}")
                    if group_df is None or group_df.empty:
                        continue
                    if merged is None:
                        merged = group_df
                    else:
                        for col in group_df.columns:
                            if col != "label" and col not in merged.columns:
                                label_to_val = dict(
                                    zip(group_df["label"], group_df[col])
                                )
                                merged[col] = merged["label"].map(label_to_val)
                return merged
        except KeyError:
            return None
