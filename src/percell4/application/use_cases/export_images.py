"""Use case: export dataset layers as TIFF files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from percell4.ports.dataset_repository import DatasetRepository


@dataclass
class ExportRequest:
    """What to export from the dataset."""

    output_folder: Path
    dataset_name: str
    channels: list[tuple[str, int]]  # (channel_name, channel_index)
    labels: list[str]  # segmentation label names
    masks: list[str]  # mask names


@dataclass
class ExportResult:
    """Result of an image export."""

    exported_count: int
    output_folder: Path


class ExportImages:
    """Export selected layers from a dataset as TIFF files.

    Reads from the repository, writes to disk via tifffile.
    The caller (dialog) collects the user's selection; this use case
    does the I/O.
    """

    def __init__(self, repo: DatasetRepository) -> None:
        self._repo = repo

    def execute(self, handle, request: ExportRequest) -> ExportResult:
        import tifffile

        request.output_folder.mkdir(parents=True, exist_ok=True)
        exported = 0

        # Export intensity channels
        if request.channels:
            intensity = self._repo.read_array(handle, "intensity")
            for name, idx in request.channels:
                if intensity.ndim == 3:
                    data = intensity[idx]
                else:
                    data = intensity
                out_path = request.output_folder / f"{request.dataset_name}_{name}.tif"
                tifffile.imwrite(str(out_path), data)
                exported += 1

        # Export segmentation labels
        for name in request.labels:
            data = self._repo.read_labels(handle, name)
            out_path = request.output_folder / f"{request.dataset_name}_{name}.tif"
            tifffile.imwrite(str(out_path), data)
            exported += 1

        # Export masks
        for name in request.masks:
            data = self._repo.read_mask(handle, name)
            out_path = request.output_folder / f"{request.dataset_name}_{name}.tif"
            tifffile.imwrite(str(out_path), data)
            exported += 1

        return ExportResult(exported_count=exported, output_folder=request.output_folder)
