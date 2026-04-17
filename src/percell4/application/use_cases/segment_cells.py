"""Use case: run cell segmentation and post-processing."""

from __future__ import annotations

from dataclasses import dataclass

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
        model_type: str = "cyto3",
        diameter: float | None = None,
        gpu: bool = False,
    ) -> NDArray[np.int32]:
        """Run segmentation inference (synchronous, CPU-heavy).

        Requires a Segmenter to be injected at construction.
        Call from a worker thread in the GUI, or directly in the CLI.
        """
        if self._segmenter is None:
            raise ValueError(
                "No segmenter injected. Pass a Segmenter at construction "
                "(e.g., CellposeSegmenter from adapters/cellpose.py)."
            )
        return self._segmenter.run(image, model_type=model_type, diameter=diameter, gpu=gpu)

    def finalize(
        self,
        raw_masks: NDArray[np.int32],
        min_area: int = 15,
    ) -> SegmentationResult:
        """Post-process masks, write to store, update session.

        Call on the main thread after inference completes.
        """
        handle = self._session.dataset
        if handle is None:
            raise ValueError("No dataset loaded")

        labels, edge_removed = filter_edge_cells(raw_masks)
        labels, small_removed = filter_small_cells(labels, min_area=min_area)
        labels = relabel_sequential(labels)
        n_cells = int(labels.max())

        seg_name = f"cellpose_{n_cells}"

        # Store-before-viewer: write to HDF5 first
        self._repo.write_labels(handle, seg_name, labels)
        self._session.set_active_segmentation(seg_name)

        return SegmentationResult(
            labels=labels,
            seg_name=seg_name,
            n_cells=n_cells,
            edge_removed=edge_removed,
            small_removed=small_removed,
        )
