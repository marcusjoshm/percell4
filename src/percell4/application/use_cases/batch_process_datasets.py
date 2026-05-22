"""Use case: headless batch compress → segment-all-timepoints → track.

Built for overnight processing of large multi-timepoint experiments: per
dataset it imports the TIFF series to an ``.h5``, segments every timepoint,
and (when the dataset is time-lapse) tracks cells. No Qt/napari — uses the
same composition root as ``run_pipeline`` (repo + NullViewerAdapter +
Session). Robust to per-dataset failures: one bad dataset is recorded and the
batch continues.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatasetSpec:
    """One dataset to process: a TIFF source directory and its output ``.h5``."""

    source_dir: Path
    output_h5: Path

    @property
    def name(self) -> str:
        return self.output_h5.stem


@dataclass
class BatchProcessItemResult:
    """Per-dataset outcome of a batch run."""

    name: str
    output_h5: Path | None = None
    n_timepoints: int = 1
    n_cells: int = 0          # max cells in any frame (segmentation)
    n_tracks: int = 0
    tracked: bool = False
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass
class BatchProcessReport:
    """Aggregated batch result."""

    items: list[BatchProcessItemResult] = field(default_factory=list)

    @property
    def n_succeeded(self) -> int:
        return sum(1 for r in self.items if r.succeeded)

    @property
    def n_failed(self) -> int:
        return sum(1 for r in self.items if not r.succeeded)


def batch_process_datasets(
    specs: list[DatasetSpec],
    *,
    seg_channel: str | None = None,
    cellpose_model: str = "cyto3",
    cellpose_diameter: float | None = None,
    gpu: bool = False,
    track: bool = True,
    import_kwargs: dict | None = None,
    segmenter=None,
    tracker=None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> BatchProcessReport:
    """Compress + segment (all timepoints) + track each dataset, headlessly.

    ``seg_channel`` selects the segmentation channel (defaults to the first).
    ``track`` runs tracking for time-lapse datasets (``n_timepoints > 1``).
    ``segmenter`` / ``tracker`` may be injected for testing; otherwise the
    real Cellpose / laptrack adapters are constructed lazily. A per-dataset
    failure is recorded on the report and never aborts the batch.
    ``progress_callback(done, total, message)`` fires after each dataset.
    """
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository
    from percell4.adapters.importer import import_dataset
    from percell4.adapters.null_viewer import NullViewerAdapter
    from percell4.application.session import Session
    from percell4.application.use_cases.load_dataset import LoadDataset
    from percell4.application.use_cases.segment_cells import SegmentCells
    from percell4.application.use_cases.track_cells import TrackCells

    report = BatchProcessReport()
    total = len(specs)

    for i, spec in enumerate(specs):
        try:
            import_dataset(spec.source_dir, spec.output_h5, **(import_kwargs or {}))

            repo = Hdf5DatasetRepository()
            session = Session()
            handle = LoadDataset(repo, NullViewerAdapter(), session).execute(spec.output_h5)
            channel_names = list(handle.metadata.get("channel_names", []))
            n_timepoints = int(handle.metadata.get("n_timepoints", 1) or 1)

            ch = seg_channel or (channel_names[0] if channel_names else None)
            if ch is None:
                raise ValueError("dataset has no channels to segment")
            if channel_names and ch not in channel_names:
                raise ValueError(
                    f"seg channel {ch!r} not in dataset; available: {channel_names}"
                )

            seg_uc = SegmentCells(
                repo, session,
                segmenter=segmenter or _default_segmenter(),
            )
            if n_timepoints > 1:
                stack = np.stack(
                    [
                        repo.read_channel_images(handle, timepoint=t)[ch]
                        for t in range(n_timepoints)
                    ],
                    axis=0,
                )
                raw = seg_uc.run_inference_stack(
                    stack, model_type=cellpose_model,
                    diameter=cellpose_diameter, gpu=gpu,
                )
            else:
                image = repo.read_channel_images(handle)[ch]
                raw = seg_uc.run_inference(
                    image, model_type=cellpose_model,
                    diameter=cellpose_diameter, gpu=gpu,
                )
            seg_result = seg_uc.finalize(raw)

            n_tracks = 0
            tracked = False
            if n_timepoints > 1 and track:
                track_result = TrackCells(
                    repo, session, tracker or _default_tracker()
                ).execute(seg_result.seg_name)
                n_tracks = track_result.n_tracks
                tracked = True

            report.items.append(
                BatchProcessItemResult(
                    name=spec.name, output_h5=spec.output_h5,
                    n_timepoints=n_timepoints, n_cells=seg_result.n_cells,
                    n_tracks=n_tracks, tracked=tracked,
                )
            )
            msg = f"{spec.name}: {seg_result.n_cells} cells"
            if tracked:
                msg += f", {n_tracks} tracks"
        except Exception as e:  # noqa: BLE001 — per-dataset isolation
            logger.exception("batch processing failed for %s", spec.name)
            report.items.append(
                BatchProcessItemResult(name=spec.name, error=f"{type(e).__name__}: {e}")
            )
            msg = f"{spec.name}: FAILED — {e}"

        if progress_callback is not None:
            progress_callback(i + 1, total, msg)

    return report


def _default_segmenter():
    from percell4.adapters.cellpose import CellposeSegmenter

    return CellposeSegmenter()


def _default_tracker():
    from percell4.adapters.laptrack_tracker import LaptrackTracker

    return LaptrackTracker()
