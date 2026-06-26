"""Use case: run cell segmentation and post-processing."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.segmentation.postprocess import (
    filter_edge_cells,
    filter_small_cells,
    relabel_sequential,
)
from percell4.ports.dataset_repository import DatasetRepository
from percell4.ports.segmenter import Segmenter
from percell4.domain.errors import NoDatasetError, NoMaskError, NoSegmentationError

logger = logging.getLogger(__name__)


@dataclass
class SegmentationResult:
    """Result of a segmentation run."""

    labels: NDArray[np.int32]
    seg_name: str
    n_cells: int
    edge_removed: int
    small_removed: int


class SegmentCells:
    """Run Cellpose segmentation + post-processing, save to store.

    The actual Cellpose inference is CPU-heavy and should be run in a
    worker thread by the caller. This use case handles the synchronous
    part: post-processing + store write + session update.

    Typical pattern in the UI:
        worker = Worker(run_cellpose, image, ...)
        worker.finished.connect(lambda masks: segment_uc.finalize(masks))
    """

    def __init__(
        self,
        repo: DatasetRepository,
        session: Session,
        segmenter: Segmenter | None = None,
    ) -> None:
        self._repo = repo
        self._session = session
        self._segmenter = segmenter

    def run_inference(
        self,
        image: NDArray,
        model_type: str = "cpsam_v2",
        diameter: float | None = None,
        gpu: bool = False,
        flow_threshold: float = 0.4,
        cellprob_threshold: float = 0.0,
        min_size: int = 15,
    ) -> NDArray[np.int32]:
        """Run segmentation inference (synchronous, CPU-heavy).

        Requires a Segmenter to be injected at construction.
        Call from a worker thread in the GUI, or directly in the CLI.
        ``flow_threshold``, ``cellprob_threshold``, and ``min_size`` are
        the full Cellpose inference controls; defaults match
        :func:`percell4.adapters.cellpose.run_cellpose`.
        """
        if self._segmenter is None:
            raise ValueError(
                "No segmenter injected. Pass a Segmenter at construction "
                "(e.g., CellposeSegmenter from adapters/cellpose.py)."
            )
        t0 = perf_counter()
        labels = self._segmenter.run(
            image,
            model_type=model_type,
            diameter=diameter,
            gpu=gpu,
            flow_threshold=flow_threshold,
            cellprob_threshold=cellprob_threshold,
            min_size=min_size,
        )
        logger.debug(
            "cellpose: %d cells found in %.2f s",
            int(labels.max()), perf_counter() - t0,
        )
        return labels

    def run_inference_stack(
        self,
        images: NDArray,
        model_type: str = "cpsam_v2",
        diameter: float | None = None,
        gpu: bool = False,
        flow_threshold: float = 0.4,
        cellprob_threshold: float = 0.0,
        min_size: int = 15,
        progress_callback=None,
    ) -> NDArray[np.int32]:
        """Run segmentation on every timepoint of a ``(T, H, W)`` stack.

        Returns a ``(T, H, W)`` raw-mask stack (per-frame Cellpose ids,
        intentionally inconsistent across frames — tracking unifies them).
        CPU-heavy; call from a worker thread. ``progress_callback(t, n_t)``
        is invoked after each frame when supplied. ``flow_threshold``,
        ``cellprob_threshold``, and ``min_size`` are forwarded to every
        frame's inference.
        """
        if self._segmenter is None:
            raise ValueError(
                "No segmenter injected. Pass a Segmenter at construction "
                "(e.g., CellposeSegmenter from adapters/cellpose.py)."
            )
        n_t = len(images)
        frames = []
        for t in range(n_t):
            t0 = perf_counter()
            frame = self._segmenter.run(
                images[t],
                model_type=model_type,
                diameter=diameter,
                gpu=gpu,
                flow_threshold=flow_threshold,
                cellprob_threshold=cellprob_threshold,
                min_size=min_size,
            )
            logger.debug(
                "cellpose frame %d/%d: %d cells found in %.2f s",
                t + 1, n_t, int(frame.max()), perf_counter() - t0,
            )
            frames.append(frame)
            if progress_callback is not None:
                progress_callback(t + 1, n_t)
        return np.stack(frames, axis=0).astype(np.int32)

    @staticmethod
    def _postprocess_frame(
        raw: NDArray[np.int32],
        min_area: int,
        remove_edge_cells: bool,
        edge_margin: int = 0,
    ) -> tuple[NDArray[np.int32], int, int]:
        """Post-process one 2D label frame: edge filter, small filter, relabel.

        ``edge_margin`` widens the edge band: cells within ``edge_margin``
        pixels of any border are treated as edge (0 = strict border-touching).

        Returns ``(labels, edge_removed, small_removed)``.
        """
        if remove_edge_cells:
            labels, edge_removed = filter_edge_cells(raw, edge_margin=edge_margin)
        else:
            labels = raw.copy()
            edge_removed = 0
        labels, small_removed = filter_small_cells(labels, min_area=min_area)
        labels = relabel_sequential(labels)
        return labels, edge_removed, small_removed

    def finalize(
        self,
        raw_masks: NDArray[np.int32],
        min_area: int = 15,
        remove_edge_cells: bool = True,
        name: str | None = None,
        view_bin: int = 1,
        edge_margin: int = 0,
    ) -> SegmentationResult:
        """Post-process masks, write to store, update session.

        Call on the main thread after inference completes. When ``name``
        is supplied, the segmentation lands at ``/labels/<name>``;
        otherwise the historical auto-derived ``f"cellpose_{n_cells}"``
        name is used so non-GUI callers (workflow runners, tests) keep
        working unchanged.

        ``view_bin`` is the bin captured at worker start (Phase 6
        capture-by-value-on-worker-construction pattern). When > 1, the
        labels coming in are at the binned shape; we nearest-neighbor
        upsample to native_shape before write so every stored array
        stays canonical (dataset-wide-binning storage invariant) and we
        stamp ``created_at_bin = view_bin`` on the dataset so the
        DataPanel layer-list annotation knows.
        """
        from percell4.domain.io.view_bin import nn_upsample_2d
        from percell4.gui._bin_suffix import bin_suffix

        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        if raw_masks.ndim == 3:
            # Time-lapse: post-process each frame independently and stack on
            # the leading T axis. Per-frame ids are intentionally inconsistent
            # across frames; tracking (TrackCells) makes them consistent.
            frame_labels = []
            edge_removed = 0
            small_removed = 0
            per_frame_counts = []
            for t in range(raw_masks.shape[0]):
                lab, er, sr = self._postprocess_frame(
                    raw_masks[t], min_area, remove_edge_cells, edge_margin
                )
                frame_labels.append(lab)
                edge_removed += er
                small_removed += sr
                per_frame_counts.append(int(lab.max()))
            labels = np.stack(frame_labels, axis=0).astype(np.int32)
            n_cells = max(per_frame_counts) if per_frame_counts else 0
        else:
            labels, edge_removed, small_removed = self._postprocess_frame(
                raw_masks, min_area, remove_edge_cells, edge_margin
            )
            n_cells = int(labels.max())

        # Default name picks up bin_suffix(); explicit ``name`` from the
        # GUI is assumed to already be the user's final choice (the
        # prompt seeded it through bin_suffix at U5).
        if name is None:
            seg_name = bin_suffix(f"cellpose_{n_cells}", view_bin)
        else:
            seg_name = name

        # Upsample to native before write so on-disk shape matches every
        # other layer in this .h5.
        attrs: dict | None = None
        if view_bin > 1:
            metadata = self._repo.read_metadata(handle)
            native = metadata.get("native_shape")
            if native is None:
                raise NoDatasetError(
                    "Cannot write a binned segmentation: "
                    "/metadata.native_shape is missing. Re-compress the "
                    "dataset to populate it."
                )
            target = (int(native[0]), int(native[1]))
            labels = nn_upsample_2d(labels, view_bin, target_hw=target).astype(
                np.int32, copy=False
            )
            attrs = {"created_at_bin": int(view_bin)}

        # Store-before-viewer: write to HDF5 first
        self._repo.write_labels(handle, seg_name, labels, attrs=attrs)
        # Refresh inventory before auto-selecting so subscribers re-list
        # the combos before they look up the just-written name.
        mask_set = set(self._repo.list_masks(handle))
        seg_names = [n for n in self._repo.list_labels(handle) if n not in mask_set]
        self._session.refresh_resource_lists(segmentation_names=seg_names)
        self._session.set_active_segmentation(seg_name)

        return SegmentationResult(
            labels=labels,
            seg_name=seg_name,
            n_cells=n_cells,
            edge_removed=edge_removed,
            small_removed=small_removed,
        )
