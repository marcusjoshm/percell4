"""Use case: batch export of dataset layers as TIFF files across many .h5 datasets.

Thin orchestration around :class:`ExportImages`. Per dataset, the
orchestrator:

1. Opens the ``.h5`` via :class:`Hdf5DatasetRepository` and reads the
   shape of ``/intensity`` (2D single-channel or 3D multi-channel) to
   build the ``(channel_name, channel_index)`` tuples ExportImages
   expects.
2. Enumerates every ``/labels/<name>`` and ``/masks/<name>`` via
   ``store.list_labels()`` / ``list_masks()``.
3. Builds an :class:`ExportRequest` with the full inventory and
   delegates to :meth:`ExportImages.execute`, which writes one
   ``<dataset_stem>_<layer>.tif`` per layer into ``output_dir``.
4. Classifies the dataset's status and fires the optional
   ``progress_callback``.

Sequential, single-process. Per-dataset failures isolate. Phasor /
lifetime / decay arrays are explicitly out of scope; the existing
:class:`ExportImages` use case only knows about intensity, labels,
and masks, and the user scoped this iteration to those.

Pattern source: ``src/percell4/application/use_cases/batch_compute_phasor.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from percell4.adapters.hdf5_store import Hdf5DatasetRepository
from percell4.application.use_cases.export_images import (
    ExportImages,
    ExportRequest,
)
from percell4.store import DatasetStore

logger = logging.getLogger(__name__)


# ── Result + report dataclasses ─────────────────────────────────────────


ITEM_STATUSES: tuple[str, ...] = (
    "succeeded",
    "skipped_no_changes",
    "failed",
)


@dataclass(frozen=True)
class BatchExportItemResult:
    """Outcome of one dataset in a batch export run.

    ``files_written`` is the count of TIFFs produced for this dataset
    (channels + labels + masks combined). ``error`` is the
    dataset-level failure message when the run could not even build
    the export request.
    """

    h5_path: Path
    status: str  # one of ITEM_STATUSES
    files_written: int = 0
    error: str | None = None


@dataclass(frozen=True)
class BatchExportReport:
    """Aggregated result of a batch export run."""

    items: tuple[BatchExportItemResult, ...] = ()

    @property
    def total_succeeded(self) -> int:
        return sum(1 for r in self.items if r.status == "succeeded")

    @property
    def total_failed(self) -> int:
        return sum(1 for r in self.items if r.status == "failed")

    @property
    def total_skipped(self) -> int:
        return sum(1 for r in self.items if r.status == "skipped_no_changes")

    @property
    def total_files_written(self) -> int:
        return sum(r.files_written for r in self.items)


# ── Channel enumeration helper ──────────────────────────────────────────


def _enumerate_channels(
    intensity_shape: tuple[int, ...] | None,
    channel_names: list[str],
) -> list[tuple[str, int]]:
    """Build the (name, idx) tuples ExportRequest.channels expects.

    Mirrors the rank-aware logic in
    ``Hdf5DatasetRepository.read_channel_images`` so a 2D /intensity
    (single channel) and a 3D (C, H, W) intensity both yield the
    canonical channel naming. Missing names default to ``"Intensity"``
    for the 2D case and ``f"ch{i}"`` for missing 3D slots.
    """
    if intensity_shape is None:
        return []
    if len(intensity_shape) == 2:
        name = channel_names[0] if channel_names else "Intensity"
        return [(name, 0)]
    if len(intensity_shape) == 3:
        n_channels = intensity_shape[0]
        return [
            (
                channel_names[i] if i < len(channel_names) else f"ch{i}",
                i,
            )
            for i in range(n_channels)
        ]
    # Higher-rank intensity is unexpected; export as a single layer
    # named "Intensity" using ExportImages's else-branch behavior
    # (which writes the full array). Mirrors the read_channel_images
    # fallback.
    return [("Intensity", 0)]


# ── Main entry point ────────────────────────────────────────────────────


def batch_export_images(
    h5_paths: list[Path],
    *,
    output_dir: Path,
    overwrite: bool = True,
    view_bin: int = 1,
    progress_callback: Callable[[BatchExportItemResult], None] | None = None,
) -> BatchExportReport:
    """Export every channel, label, and mask from each ``.h5`` to TIFFs.

    Per dataset, writes ``<output_dir>/<h5_stem>_<layer>.tif`` for
    every intensity channel, every segmentation label, and every mask.
    Layouts are flat (no per-dataset subfolders); existing files at
    those paths are overwritten silently by
    :func:`tifffile.imwrite`.

    Args:
        h5_paths: Datasets to export. Missing or unreadable paths are
            reported as failed items; the loop continues.
        output_dir: Target directory for the TIFF outputs. Created if
            missing (via :meth:`ExportImages.execute`).
        overwrite: Reserved for forward compatibility. Currently
            unused -- ``tifffile.imwrite`` always overwrites. Kept as
            a kwarg so a future ``--no-overwrite`` flag can be wired
            without changing the call shape.
        view_bin: Bin factor applied to every layer at read time.
            ``1`` (default) writes at native resolution -- the
            established export contract. ``>1`` routes each layer
            through the repository's view-bin lens (sum_bin_2d for
            intensity, mode_labels for /labels, majority_vote_mask
            for /masks), producing downsampled TIFFs.
        progress_callback: Invoked once per dataset after its
            :class:`BatchExportItemResult` is classified.

    Returns:
        A :class:`BatchExportReport` with one item per input path.
    """
    # ``overwrite=False`` is not yet supported. Silence the linter
    # warning by referencing the kwarg explicitly.
    _ = overwrite

    repo = Hdf5DatasetRepository()
    export_uc = ExportImages(repo)
    results: list[BatchExportItemResult] = []

    for h5_path in h5_paths:
        result = _process_one_dataset(
            h5_path=h5_path,
            repo=repo,
            export_uc=export_uc,
            output_dir=output_dir,
            view_bin=view_bin,
        )
        results.append(result)
        if progress_callback is not None:
            progress_callback(result)

    return BatchExportReport(items=tuple(results))


def _process_one_dataset(
    *,
    h5_path: Path,
    repo: Hdf5DatasetRepository,
    export_uc: ExportImages,
    output_dir: Path,
    view_bin: int = 1,
) -> BatchExportItemResult:
    """Export every layer of one dataset. Catches per-dataset failures."""
    try:
        handle = repo.open(h5_path)
    except Exception as exc:  # noqa: BLE001 — orchestrator never raises per-item
        return BatchExportItemResult(
            h5_path=h5_path,
            status="failed",
            error=f"open failed: {exc}",
        )

    store = DatasetStore(h5_path)

    try:
        # Read intensity shape via h5py directly (bin-independent --
        # we only need the channel-axis dimension to enumerate
        # channel names; the actual export reads inside ExportImages
        # honor the requested view_bin).
        import h5py

        with h5py.File(h5_path, "r") as f:
            intensity_shape: tuple[int, ...] | None = (
                tuple(f["intensity"].shape) if "intensity" in f else None
            )
        channel_names = list(handle.metadata.get("channel_names", []))
        channels = _enumerate_channels(intensity_shape, channel_names)
        labels = store.list_labels()
        masks = store.list_masks()
    except Exception as exc:  # noqa: BLE001
        return BatchExportItemResult(
            h5_path=h5_path,
            status="failed",
            error=f"layer enumeration failed: {exc}",
        )

    if not channels and not labels and not masks:
        return BatchExportItemResult(
            h5_path=h5_path,
            status="skipped_no_changes",
            files_written=0,
        )

    request = ExportRequest(
        output_folder=output_dir,
        dataset_name=h5_path.stem,
        channels=channels,
        labels=labels,
        masks=masks,
        view_bin=view_bin,
    )

    try:
        result = export_uc.execute(handle, request)
    except Exception as exc:  # noqa: BLE001
        return BatchExportItemResult(
            h5_path=h5_path,
            status="failed",
            error=f"export failed: {exc}",
        )

    return BatchExportItemResult(
        h5_path=h5_path,
        status="succeeded",
        files_written=result.exported_count,
    )
