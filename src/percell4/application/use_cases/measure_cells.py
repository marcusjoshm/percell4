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
    ) -> pd.DataFrame:
        """Run measurement and return the resulting DataFrame.

        Args:
            metrics: List of metric names to compute (keys from BUILTIN_METRICS).
            roi_names: Optional label→name mapping for multi-ROI masks.

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

        # Read data from repository
        images = self._repo.read_channel_images(handle)
        labels = self._repo.read_labels(handle, seg_name)

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
                mask = self._repo.read_mask(handle, mask_name)
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
