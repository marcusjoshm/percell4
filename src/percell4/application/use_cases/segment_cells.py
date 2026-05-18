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
from percell4.domain.errors import NoDatasetError, NoMaskError, NoSegmentationError


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
        remove_edge_cells: bool = True,
        name: str | None = None,
        view_bin: int = 1,
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

        if remove_edge_cells:
            labels, edge_removed = filter_edge_cells(raw_masks)
        else:
            labels = raw_masks.copy()
            edge_removed = 0
        labels, small_removed = filter_small_cells(labels, min_area=min_area)
        labels = relabel_sequential(labels)
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
