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
    """One dataset to process.

    ``source_dir`` is either a TIFF source **directory** (imported to
    ``output_h5``) or an already-compressed ``.h5`` **file** (the
    compress/import step is skipped). When the source is an ``.h5`` and
    ``output_h5`` resolves to that same path the dataset is segmented in
    place; otherwise the ``.h5`` is copied to ``output_h5`` first and the
    copy is processed (the original is never modified).
    """

    source_dir: Path
    output_h5: Path

    @property
    def name(self) -> str:
        return self.output_h5.stem

    @property
    def is_h5_source(self) -> bool:
        """True when the source is an already-compressed ``.h5`` file."""
        return self.source_dir.suffix.lower() == ".h5"

    @property
    def in_place(self) -> bool:
        """True when an ``.h5`` source is processed where it lives (no copy)."""
        return self.is_h5_source and self.source_dir.resolve() == self.output_h5.resolve()


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
    channel_names: list[str] | None = None,
    seg_name: str | None = None,
    settings: "CellposeSettings | None" = None,
    remove_edge_cells: bool = True,
    edge_margin: int = 0,
    skip_segmentation: bool = False,
    track: bool = True,
    import_kwargs: dict | None = None,
    segmenter=None,
    tracker=None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> BatchProcessReport:
    """Compress + segment (all timepoints) + track each dataset, headlessly.

    A spec's ``source_dir`` may be a TIFF directory (imported) or an
    already-compressed ``.h5`` (the compress/import step is skipped — see
    :class:`DatasetSpec`). ``.h5`` sources are segmented in place, or copied
    to ``output_h5`` first when that differs from the source.

    ``seg_channel`` selects the segmentation channel (defaults to the first);
    it is resolved against the *effective* channel names, i.e. after any
    ``channel_names`` override is applied.

    ``channel_names`` optionally **overrides** the imported channel names
    (e.g. friendly ``["DAPI", "GFP"]`` in place of the importer's
    ``ch00``/``ch01``). It must have one name per imported channel — the
    override is a pure relabel that preserves channel order (channel *i*
    keeps its pixels). Applied uniformly to every dataset in the batch; a
    count mismatch is recorded as that dataset's failure and the batch
    continues.

    ``seg_name`` optionally sets the segmentation layer name (passed to
    ``SegmentCells.finalize``); defaults to the historical
    ``cellpose_<n_cells>``. A name that collides with an existing channel or
    segmentation in the flat HDF5 namespace is rejected (such a collision
    crashes the GUI on later load).

    ``settings`` is the full :class:`CellposeSettings` (model, diameter, GPU,
    flow/cellprob thresholds, min size, saturation %, blur sigma); defaults to
    ``CellposeSettings()`` when not given. The per-frame saturation LUT and
    Gaussian blur are applied to an in-memory copy of the segmentation channel
    before inference — mirroring the GUI Segment panel and
    ``workflows.phases.segment_one``; the on-disk ``/intensity`` is never
    modified. ``remove_edge_cells`` / ``edge_margin`` control border-cell
    removal in post-processing.

    ``skip_segmentation`` runs tracking only on an **existing** segmentation
    layer (no Cellpose): ``seg_name`` then names the existing ``(T, H, W)``
    segmentation to track and is required. Useful for re-tracking an already
    segmented ``.h5`` with different laptrack behavior, or after hand-editing
    raw masks. Only meaningful for a time-lapse dataset with ``track=True``.

    ``track`` runs tracking for time-lapse datasets (``n_timepoints > 1``).
    ``segmenter`` / ``tracker`` may be injected for testing; otherwise the
    real Cellpose / laptrack adapters are constructed lazily. A per-dataset
    failure is recorded on the report and never aborts the batch.
    ``progress_callback(done, total, message)`` fires after each dataset.
    Granular per-frame / timing detail is emitted via this module's logger at
    DEBUG level (surfaced by the CLI's ``--verbose``).
    """
    from time import perf_counter
    import shutil

    from percell4.adapters.hdf5_store import Hdf5DatasetRepository
    from percell4.adapters.importer import import_dataset
    from percell4.adapters.null_viewer import NullViewerAdapter
    from percell4.application.session import Session
    from percell4.application.use_cases.load_dataset import LoadDataset
    from percell4.application.use_cases.segment_cells import SegmentCells
    from percell4.application.use_cases.track_cells import TrackCells
    from percell4.domain.segmentation.preprocess import (
        apply_gaussian_blur,
        apply_saturation_lut,
    )
    from percell4.store import DatasetStore
    from percell4.workflows.models import CellposeSettings

    if settings is None:
        settings = CellposeSettings()

    def _preprocess(plane: NDArray) -> NDArray:
        """Per-frame saturation LUT then Gaussian blur (mirrors the GUI /
        ``phases.segment_one``). Both default to no-ops. Operates on a copy;
        the on-disk /intensity is never touched."""
        if settings.saturation_pct > 0.0:
            plane = apply_saturation_lut(plane, settings.saturation_pct)
        if settings.blur_sigma > 0.0:
            plane = apply_gaussian_blur(plane, settings.blur_sigma)
        return plane

    report = BatchProcessReport()
    total = len(specs)

    for i, spec in enumerate(specs):
        try:
            if spec.is_h5_source:
                # Already compressed: skip import. Copy into output_h5 first
                # unless we're segmenting the source in place.
                if not spec.in_place:
                    spec.output_h5.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(spec.source_dir, spec.output_h5)
            else:
                import_dataset(spec.source_dir, spec.output_h5, **(import_kwargs or {}))

            repo = Hdf5DatasetRepository()
            session = Session()
            handle = LoadDataset(repo, NullViewerAdapter(), session).execute(spec.output_h5)
            imported_channels = list(handle.metadata.get("channel_names", []))

            # Optional channel-name override (e.g. friendly "DAPI,GFP" in place
            # of the importer's "ch00,ch01"). One name per imported channel;
            # order is preserved, so this is a pure relabel.
            if channel_names is not None:
                if imported_channels and len(channel_names) != len(imported_channels):
                    raise ValueError(
                        f"channel-names has {len(channel_names)} name(s) but dataset "
                        f"has {len(imported_channels)} channel(s): {imported_channels}"
                    )
                DatasetStore(spec.output_h5).set_metadata(
                    {"channel_names": list(channel_names)}
                )
                # Refresh the in-memory snapshot too: re-reading alone can serve
                # a stale h5py metadata cache within the same process.
                handle.metadata["channel_names"] = list(channel_names)

            active_channels = list(handle.metadata.get("channel_names", []))
            n_timepoints = int(handle.metadata.get("n_timepoints", 1) or 1)

            if skip_segmentation:
                # Track-only: reuse an existing segmentation, no Cellpose.
                if seg_name is None:
                    raise ValueError(
                        "skip_segmentation requires seg_name (the existing "
                        "segmentation layer to track)"
                    )
                existing_labels = set(repo.list_labels(handle))
                if seg_name not in existing_labels:
                    raise ValueError(
                        f"skip_segmentation: segmentation {seg_name!r} not found; "
                        f"available: {sorted(existing_labels)}"
                    )
                if not (n_timepoints > 1 and track):
                    raise ValueError(
                        "skip_segmentation has nothing to do without tracking a "
                        "time-lapse dataset (need n_timepoints > 1 and track=True)"
                    )
                labels = repo.read_labels(handle, seg_name)
                n_cells = int(labels.max())
                resolved_seg_name = seg_name
                logger.debug(
                    "%s: skip segmentation — tracking existing %r (%d timepoints)",
                    spec.name, seg_name, n_timepoints,
                )
            else:
                ch = seg_channel or (active_channels[0] if active_channels else None)
                if ch is None:
                    raise ValueError("dataset has no channels to segment")
                if active_channels and ch not in active_channels:
                    raise ValueError(
                        f"seg channel {ch!r} not in dataset; available: {active_channels}"
                    )

                # Reject a segmentation name that collides with an existing
                # channel/segmentation in the flat HDF5 namespace — a collision
                # crashes the GUI on later load.
                if seg_name is not None:
                    existing = set(active_channels) | set(repo.list_labels(handle))
                    if seg_name in existing:
                        raise ValueError(
                            f"seg-name {seg_name!r} collides with an existing channel/"
                            f"segmentation name: {sorted(existing)}"
                        )

                seg_uc = SegmentCells(
                    repo, session,
                    segmenter=segmenter or _default_segmenter(),
                )
                cp_diameter = settings.diameter if settings.diameter > 0 else None
                logger.debug(
                    "%s: segmenting channel %r — %d timepoint(s), model=%s, "
                    "diameter=%s, flow=%.2f, cellprob=%.2f, min_size=%d, "
                    "saturation=%.1f%%, blur=%.1f, gpu=%s",
                    spec.name, ch, n_timepoints, settings.model,
                    cp_diameter if cp_diameter is not None else "auto",
                    settings.flow_threshold, settings.cellprob_threshold,
                    settings.min_size, settings.saturation_pct,
                    settings.blur_sigma, settings.gpu,
                )
                t0 = perf_counter()
                if n_timepoints > 1:
                    stack = np.stack(
                        [
                            _preprocess(repo.read_channel_images(handle, timepoint=t)[ch])
                            for t in range(n_timepoints)
                        ],
                        axis=0,
                    )

                    def _frame_progress(done: int, n: int) -> None:
                        logger.debug("%s: segmented frame %d/%d", spec.name, done, n)

                    raw = seg_uc.run_inference_stack(
                        stack, model_type=settings.model,
                        diameter=cp_diameter, gpu=settings.gpu,
                        flow_threshold=settings.flow_threshold,
                        cellprob_threshold=settings.cellprob_threshold,
                        min_size=settings.min_size,
                        progress_callback=_frame_progress,
                    )
                else:
                    image = _preprocess(repo.read_channel_images(handle)[ch])
                    raw = seg_uc.run_inference(
                        image, model_type=settings.model,
                        diameter=cp_diameter, gpu=settings.gpu,
                        flow_threshold=settings.flow_threshold,
                        cellprob_threshold=settings.cellprob_threshold,
                        min_size=settings.min_size,
                    )
                logger.debug(
                    "%s: cellpose inference finished in %.1f s",
                    spec.name, perf_counter() - t0,
                )
                seg_result = seg_uc.finalize(
                    raw, name=seg_name,
                    min_area=settings.min_size,
                    remove_edge_cells=remove_edge_cells,
                    edge_margin=edge_margin,
                )
                n_cells = seg_result.n_cells
                resolved_seg_name = seg_result.seg_name
                logger.debug(
                    "%s: post-process — %d cells, %d edge removed, %d small removed",
                    spec.name, n_cells, seg_result.edge_removed,
                    seg_result.small_removed,
                )

            n_tracks = 0
            tracked = False
            if n_timepoints > 1 and track:
                t1 = perf_counter()
                logger.debug(
                    "%s: tracking %r across %d timepoints",
                    spec.name, resolved_seg_name, n_timepoints,
                )
                track_result = TrackCells(
                    repo, session, tracker or _default_tracker()
                ).execute(resolved_seg_name)
                n_tracks = track_result.n_tracks
                tracked = True
                logger.debug(
                    "%s: tracking finished in %.1f s — %d tracks, %d divisions",
                    spec.name, perf_counter() - t1, n_tracks,
                    track_result.n_divisions,
                )

            report.items.append(
                BatchProcessItemResult(
                    name=spec.name, output_h5=spec.output_h5,
                    n_timepoints=n_timepoints, n_cells=n_cells,
                    n_tracks=n_tracks, tracked=tracked,
                )
            )
            msg = f"{spec.name}: {n_cells} cells"
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
