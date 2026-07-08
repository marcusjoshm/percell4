"""Use case: measure per-cell metrics across all channels."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from percell4.application.session import Session
from percell4.domain.errors import NoDatasetError, NoSegmentationError
from percell4.domain.measure.measurer import measure_multichannel, measure_multichannel_multi_roi
from percell4.ports.dataset_repository import DatasetRepository

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

        # Time-lapse datasets measure every timepoint and tag each row; a
        # single-timepoint dataset takes the historical path (no timepoint
        # column — byte-identical output).
        if self._session.n_timepoints > 1:
            df = self._measure_timelapse(
                handle, seg_name, metrics, roi_names, view_bin
            )
        else:
            df = self._measure_single(
                handle, seg_name, metrics, roi_names, view_bin
            )

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

    def _measure_single(
        self, handle, seg_name, metrics, roi_names, view_bin
    ) -> pd.DataFrame:
        """Historical single-timepoint measurement (no ``timepoint`` column)."""
        images = self._repo.read_channel_images(handle, view_bin=view_bin)
        labels = self._repo.read_labels(handle, seg_name, view_bin=view_bin)

        if labels.max() == 0:
            raise ValueError("Segmentation has no cells")

        if self._session.is_filtered and self._session.filter_ids:
            cell_mask = np.isin(labels, list(self._session.filter_ids))
            labels = labels.copy()
            labels[~cell_mask] = 0
            if labels.max() == 0:
                raise ValueError("No filtered cells to process")

        mask = self._read_active_mask(handle, view_bin)
        return self._measure_one(images, labels, mask, metrics, roi_names, view_bin)

    def _measure_timelapse(
        self, handle, seg_name, metrics, roi_names, view_bin
    ) -> pd.DataFrame:
        """Measure every timepoint; tag rows with ``timepoint`` and join lineage.

        A frame with no cells (death / field-of-view exit) simply contributes
        no rows, so per-timepoint counts may legitimately differ.
        """
        n_timepoints = self._session.n_timepoints
        filter_ids = (
            list(self._session.filter_ids)
            if (self._session.is_filtered and self._session.filter_ids)
            else None
        )

        frames: list[pd.DataFrame] = []
        any_cells = False
        for t in range(n_timepoints):
            labels_t = self._repo.read_labels(
                handle, seg_name, view_bin=view_bin, timepoint=t
            )
            if labels_t.max() == 0:
                continue
            any_cells = True
            if filter_ids is not None:
                cell_mask = np.isin(labels_t, filter_ids)
                labels_t = labels_t.copy()
                labels_t[~cell_mask] = 0
                if labels_t.max() == 0:
                    continue
            images_t = self._repo.read_channel_images(
                handle, view_bin=view_bin, timepoint=t
            )
            # Per-frame mask read (one broadcast policy): read_mask returns the
            # (H,W) frame of a (T,H,W) mask, or broadcasts a 2D time-invariant
            # mask. No manual mask_full[t] slice — see store.read_mask (U2).
            mask_t = self._read_active_mask(handle, view_bin, timepoint=t)
            df_t = self._measure_one(
                images_t, labels_t, mask_t, metrics, roi_names, view_bin
            )
            df_t["timepoint"] = t
            frames.append(df_t)

        if not frames:
            raise ValueError(
                "No filtered cells to process" if any_cells
                else "Segmentation has no cells"
            )
        df = pd.concat(frames, ignore_index=True)
        return self._join_lineage(handle, seg_name, df)

    def _read_active_mask(self, handle, view_bin, timepoint=None):
        """Read the active mask, or None when absent/missing.

        ``timepoint`` is threaded through to ``read_mask`` so a time-lapse
        measure reads each frame's mask directly (a 2D time-invariant mask
        broadcasts; a ``(T,H,W)`` mask slices the frame). The single-timepoint
        path passes ``timepoint=None`` and gets the whole 2D mask, unchanged.
        """
        mask_name = self._session.active_mask
        if not mask_name:
            return None
        try:
            return self._repo.read_mask(
                handle, mask_name, view_bin=view_bin, timepoint=timepoint
            )
        except KeyError:
            logger.warning("Mask '%s' not found, proceeding without mask", mask_name)
            return None

    def _measure_one(
        self, images, labels, mask, metrics, roi_names, view_bin
    ) -> pd.DataFrame:
        """Measure one frame (images + 2D labels + optional mask) -> DataFrame."""
        is_multi_roi = mask is not None and mask.max() > 1
        if is_multi_roi:
            rn = roi_names
            if not rn:
                unique_labels = np.unique(mask[mask > 0])
                rn = {int(v): f"roi_{v}" for v in unique_labels}
            df = measure_multichannel_multi_roi(
                images, labels, mask, rn, metrics=metrics,
            )
        else:
            df = measure_multichannel(images, labels, mask=mask, metrics=metrics)

        # Bin-aware unit conversion: pixel-count metrics scale by k**2 so areas
        # measured at view_bin>1 are reported in k=1-equivalent pixels.
        if view_bin > 1:
            scale = view_bin * view_bin
            for col in list(df.columns):
                if col == "area_pixels" or col.endswith("_area"):
                    df[col] = df[col] * scale

        df["bin_at_measure"] = int(view_bin)
        return df

    def _join_lineage(self, handle, seg_name, df: pd.DataFrame) -> pd.DataFrame:
        """Attach track_id + lineage columns when ``seg_name`` is tracked.

        For a tracked segmentation the label value IS the (1-based) track id,
        so ``track_id == label``; ``tree_id`` / ``parent_track_id`` are joined
        from ``/tracks/<seg_name>``. A raw (untracked) segmentation gets only
        the ``timepoint`` column.
        """
        if "label" not in df.columns:
            return df
        try:
            tracks = self._repo.read_tracks(handle, seg_name)
        except KeyError:
            return df  # untracked segmentation
        df = df.copy()
        df["track_id"] = df["label"]
        if tracks is not None and not tracks.empty:
            tree_lut = dict(zip(tracks["track_id"], tracks["tree_id"]))
            parent_lut = dict(zip(tracks["track_id"], tracks["parent_track_id"]))
            df["tree_id"] = df["track_id"].map(tree_lut)
            df["parent_track_id"] = df["track_id"].map(parent_lut)
        return df
