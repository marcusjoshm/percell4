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
    # Bin factor applied at read time. ``view_bin=1`` (default) writes
    # TIFFs at native resolution — the established export contract. A
    # value > 1 routes each layer through the repository's view-bin
    # lens (sum_bin_2d for intensity, mode_labels for /labels,
    # majority_vote_mask for /masks), producing downsampled TIFFs at
    # the GUI's current viewing resolution.
    view_bin: int = 1
    # Stored (creation-bin-scaled) linear pixel size in µm/px, or
    # ``None`` for legacy datasets without TIFF resolution metadata.
    # The caller resolves this from ``repo.read_metadata(handle)``
    # per-dataset so the use case stays adapter-free; the writer
    # applies ``view_bin`` scaling so the exported file is
    # self-describing.
    pixel_size_um: float | None = None


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

    def execute(self, handle: "DatasetHandle", request: ExportRequest) -> ExportResult:
        from percell4.adapters.tiff_writer import write_tiff_with_metadata

        request.output_folder.mkdir(parents=True, exist_ok=True)
        exported = 0

        write_kwargs = {
            "pixel_size_um": request.pixel_size_um,
            "view_bin": request.view_bin,
        }

        # Export intensity channels
        if request.channels:
            intensity = self._repo.read_array(
                handle, "intensity", view_bin=request.view_bin,
            )
            for name, idx in request.channels:
                if intensity.ndim == 3:
                    data = intensity[idx]
                else:
                    data = intensity
                out_path = request.output_folder / f"{request.dataset_name}_{name}.tif"
                write_tiff_with_metadata(out_path, data, **write_kwargs)
                exported += 1

        # Export segmentation labels
        for name in request.labels:
            data = self._repo.read_labels(
                handle, name, view_bin=request.view_bin,
            )
            out_path = request.output_folder / f"{request.dataset_name}_{name}.tif"
            write_tiff_with_metadata(out_path, data, **write_kwargs)
            exported += 1

        # Export masks
        for name in request.masks:
            data = self._repo.read_mask(
                handle, name, view_bin=request.view_bin,
            )
            out_path = request.output_folder / f"{request.dataset_name}_{name}.tif"
            write_tiff_with_metadata(out_path, data, **write_kwargs)
            exported += 1

        return ExportResult(exported_count=exported, output_folder=request.output_folder)
