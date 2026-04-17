"""Adapter: HDF5-based DatasetRepository implementation.

Wraps the existing DatasetStore to conform to the DatasetRepository port.
No behavior changes — this is a protocol conformance layer.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from percell4.domain.dataset import DatasetHandle, DatasetView
from percell4.store import DatasetStore


class Hdf5DatasetRepository:
    """DatasetRepository backed by HDF5 files via DatasetStore.

    Conforms to percell4.ports.dataset_repository.DatasetRepository.
    """

    def open(self, path: Path) -> DatasetHandle:
        """Open an existing .h5 dataset file."""
        store = DatasetStore(path)
        if not store.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")
        return DatasetHandle(path=path, metadata=store.metadata)

    def build_view(self, handle: DatasetHandle) -> DatasetView:
        """Build a displayable snapshot: channel images, labels, masks."""
        store = DatasetStore(handle.path)

        channel_images: dict[str, np.ndarray] = {}
        labels: dict[str, np.ndarray] = {}
        masks: dict[str, np.ndarray] = {}

        with store.open_read() as s:
            # Read intensity data and split into named channels
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

            # Read labels (segmentations only — exclude masks)
            mask_names = set(s.list_masks())
            for label_name in s.list_labels():
                if label_name not in mask_names:
                    labels[label_name] = s.read_labels(label_name)

            # Read masks
            for mask_name in s.list_masks():
                masks[mask_name] = s.read_mask(mask_name)

        return DatasetView(
            channel_images=channel_images,
            labels=labels,
            masks=masks,
        )
