"""Use case: measure per-cell metrics across all channels."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from percell4.application.session import Session
from percell4.domain.measure.measurer import measure_multichannel, measure_multichannel_multi_roi
from percell4.ports.dataset_repository import DatasetRepository
from percell4.domain.errors import NoDatasetError, NoMaskError, NoSegmentationError

logger = logging.getLogger(__name__)


class MeasureCells:
    """Measure per-cell metrics using active segmentation, optional mask.

    Reads data from the repository (source of truth), runs pure
    domain computation, writes results to store, and updates the session.
    """

    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def execute(
        self,
        metrics: list[str],
        roi_names: dict[int, str] | None = None,
        view_bin: int | None = None,
    ) -> pd.DataFrame:
        """Run measurement and return the resulting DataFrame.

        Args:
            metrics: List of metric names to compute (keys from BUILTIN_METRICS).
            roi_names: Optional label→name mapping for multi-ROI masks.
            view_bin: Session view bin to measure at (Phase 6 binning).
                ``None`` reads ``session.active_bin``. At ``view_bin > 1``,
                images, labels, and masks are read at the binned shape;
                the measurer computes against those; and pixel-count metrics
                are multiplied by ``k**2`` so the output is in
                k=1-equivalent units (areas at k=3 are comparable to areas
                at k=1 with k**2 resolution coarsening). Each row carries
                ``bin_at_measure = view_bin``.

        Raises:
            ValueError: If no dataset loaded, no active segmentation, or
                       segmentation has no cells.
        """
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        seg_name = self._session.active_segmentation
        if not seg_name:
            raise NoSegmentationError("No active segmentation")

        if view_bin is None:
            view_bin = self._session.active_bin

        # Read data from repository at the captured bin
        images = self._repo.read_channel_images(handle, view_bin=view_bin)
        labels = self._repo.read_labels(handle, seg_name, view_bin=view_bin)

        if labels.max() == 0:
            raise ValueError("Segmentation has no cells")

        # Apply cell filter if active
        if self._session.is_filtered and self._session.filter_ids:
            cell_mask = np.isin(labels, list(self._session.filter_ids))
            labels = labels.copy()
            labels[~cell_mask] = 0
            if labels.max() == 0:
                raise ValueError("No filtered cells to process")

        # Read active mask (optional)
        mask = None
        mask_name = self._session.active_mask
        if mask_name:
            try:
                mask = self._repo.read_mask(handle, mask_name, view_bin=view_bin)
            except KeyError:
                logger.warning("Mask '%s' not found, proceeding without mask", mask_name)

        # Run measurement
        is_multi_roi = mask is not None and mask.max() > 1
        if is_multi_roi:
            if not roi_names:
                unique_labels = np.unique(mask[mask > 0])
                roi_names = {int(v): f"roi_{v}" for v in unique_labels}
            df = measure_multichannel_multi_roi(
                images, labels, mask, roi_names, metrics=metrics,
            )
        else:
            df = measure_multichannel(images, labels, mask=mask, metrics=metrics)

        # Bin-aware unit conversion: pixel-count metrics scale by k**2
        # so areas measured at view_bin=3 are reported in k=1-equivalent
        # pixels (a binned pixel = k**2 source pixels). This makes
        # cross-bin comparison physically meaningful in the same
        # DataFrame.
        if view_bin > 1:
            scale = view_bin * view_bin
            for col in list(df.columns):
                if col == "area_pixels" or col.endswith("_area"):
                    df[col] = df[col] * scale

        # Tag every row so downstream plots can group/filter by bin.
        df["bin_at_measure"] = int(view_bin)

        # Merge stored group columns (survive re-measurement)
        groups_df = self._repo.read_group_columns(handle)
        if groups_df is not None and not groups_df.empty and "label" in df.columns:
            for col in groups_df.columns:
                if col != "label" and col not in df.columns:
                    label_to_val = dict(zip(groups_df["label"], groups_df[col]))
                    df[col] = df["label"].map(label_to_val)

        # Store-before-session: write to HDF5 first
        self._repo.write_measurements(handle, df)
        self._session.set_measurements(df)

        return df
