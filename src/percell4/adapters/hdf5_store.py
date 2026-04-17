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
from percell4.store import DatasetStore


class Hdf5DatasetRepository:
    """DatasetRepository backed by HDF5 files via DatasetStore.

    Conforms to percell4.ports.dataset_repository.DatasetRepository.
    """

    def _store(self, handle: DatasetHandle) -> DatasetStore:
        return DatasetStore(handle.path)

    # ── Lifecycle ────────────────────────────────────────────

    def open(self, path: Path) -> DatasetHandle:
        store = DatasetStore(path)
        if not store.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")
        return DatasetHandle(path=path, metadata=store.metadata)

    def build_view(self, handle: DatasetHandle) -> DatasetView:
        store = self._store(handle)
        channel_images: dict[str, np.ndarray] = {}
        labels: dict[str, np.ndarray] = {}
        masks: dict[str, np.ndarray] = {}

        with store.open_read() as s:
            intensity = s.read_array("intensity")
            channel_names = list(handle.metadata.get("channel_names", []))

            if intensity.ndim == 2:
                name = channel_names[0] if channel_names else "Intensity"
                channel_images[name] = intensity.astype(np.float32)
            elif intensity.ndim == 3 and intensity.shape[0] <= 20:
                for i in range(intensity.shape[0]):
                    name = channel_names[i] if i < len(channel_names) else f"ch{i}"
                    channel_images[name] = intensity[i].astype(np.float32)
            else:
                channel_images["Intensity"] = intensity.astype(np.float32)

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

    def read_channel_images(self, handle: DatasetHandle) -> dict[str, NDArray[np.float32]]:
        store = self._store(handle)
        result: dict[str, np.ndarray] = {}
        with store.open_read() as s:
            intensity = s.read_array("intensity")
            channel_names = list(handle.metadata.get("channel_names", []))

            if intensity.ndim == 2:
                name = channel_names[0] if channel_names else "Intensity"
                result[name] = intensity.astype(np.float32)
            elif intensity.ndim == 3:
                for i in range(intensity.shape[0]):
                    name = channel_names[i] if i < len(channel_names) else f"ch{i}"
                    result[name] = intensity[i].astype(np.float32)
            else:
                result["Intensity"] = intensity.astype(np.float32)
        return result

    # ── Segmentation labels ──────────────────────────────────

    def read_labels(self, handle: DatasetHandle, name: str) -> NDArray[np.int32]:
        return self._store(handle).read_labels(name)

    def list_labels(self, handle: DatasetHandle) -> list[str]:
        return self._store(handle).list_labels()

    # ── Masks ────────────────────────────────────────────────

    def read_mask(self, handle: DatasetHandle, name: str) -> NDArray[np.uint8]:
        return self._store(handle).read_mask(name)

    def write_mask(self, handle: DatasetHandle, name: str, data: NDArray) -> None:
        self._store(handle).write_mask(name, data)

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

    # ── Generic arrays ───────────────────────────────────────

    def write_array(
        self,
        handle: DatasetHandle,
        path: str,
        data: NDArray,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        self._store(handle).write_array(path, data, attrs=attrs)

    def read_array(self, handle: DatasetHandle, path: str) -> NDArray:
        return self._store(handle).read_array(path)

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
        except (KeyError, Exception):
            return None
