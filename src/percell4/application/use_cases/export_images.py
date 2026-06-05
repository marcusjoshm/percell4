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
        write_kwargs = {
            "pixel_size_um": request.pixel_size_um,
            "view_bin": request.view_bin,
        }

        n_timepoints = int(handle.metadata.get("n_timepoints", 1) or 1)
        if n_timepoints > 1:
            exported = self._export_timelapse(
                handle, request, write_kwargs, n_timepoints
            )
            return ExportResult(
                exported_count=exported, output_folder=request.output_folder
            )

        exported = 0
        # Export intensity channels (single-timepoint: historical path)
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

    def _export_timelapse(
        self, handle, request, write_kwargs, n_timepoints: int
    ) -> int:
        """Export one TIFF per timepoint with a ``_t{NN}`` suffix.

        Each channel/label/mask is sliced to a 2D frame on disk per timepoint
        (channel idx is the C index after the time axis is sliced) and written
        as ``<dataset>_<name>_t{NN}.tif`` — mirroring the import token
        convention so the export round-trips back through Add Layer / Compress.
        """
        from percell4.adapters.tiff_writer import write_tiff_with_metadata
        from percell4.domain.io.timepoints import timepoint_label

        exported = 0
        folder = request.output_folder
        ds = request.dataset_name
        vb = request.view_bin
        for t in range(n_timepoints):
            suffix = timepoint_label(t)  # "t00", "t01", ...
            for name, idx in request.channels:
                data = self._repo.read_channel(
                    handle, "intensity", idx, view_bin=vb, timepoint=t,
                )
                out_path = folder / f"{ds}_{name}_{suffix}.tif"
                write_tiff_with_metadata(out_path, data, **write_kwargs)
                exported += 1
            for name in request.labels:
                data = self._repo.read_labels(handle, name, view_bin=vb, timepoint=t)
                out_path = folder / f"{ds}_{name}_{suffix}.tif"
                write_tiff_with_metadata(out_path, data, **write_kwargs)
                exported += 1
            for name in request.masks:
                data = self._repo.read_mask(handle, name, view_bin=vb, timepoint=t)
                out_path = folder / f"{ds}_{name}_{suffix}.tif"
                write_tiff_with_metadata(out_path, data, **write_kwargs)
                exported += 1
        return exported
