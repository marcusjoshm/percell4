"""Use case: per-cell particle/puncta analysis."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from percell4.application.session import Session
from percell4.domain.errors import NoDatasetError, NoMaskError, NoSegmentationError
from percell4.domain.measure.particle import analyze_particles, analyze_particles_detail
from percell4.ports.dataset_repository import DatasetRepository


@dataclass
class ParticleResult:
    """Result of particle analysis."""

    summary_df: pd.DataFrame
    detail_df: pd.DataFrame
    n_cells: int
    total_particles: int


class AnalyzeParticles:
    """Analyze particles within each cell using the active mask.

    Reads images/labels/mask from repo, computes particle stats,
    merges with existing measurements, writes to store + session.
    """

    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def execute(self, min_area: int = 1) -> ParticleResult:
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        seg_name = self._session.active_segmentation
        if not seg_name:
            raise NoSegmentationError("No active segmentation")

        mask_name = self._session.active_mask
        if not mask_name:
            raise NoMaskError("No active mask — apply a threshold first")

        filter_ids = (
            list(self._session.filter_ids)
            if (self._session.is_filtered and self._session.filter_ids)
            else None
        )

        # Time-lapse datasets analyze every timepoint and tag each row; a
        # single-timepoint dataset takes the historical path (no timepoint
        # column — byte-identical output). Mirrors MeasureCells.
        if self._session.n_timepoints > 1:
            particle_df, detail_df = self._analyze_timelapse(
                handle, seg_name, mask_name, filter_ids, min_area
            )
        else:
            particle_df, detail_df = self._analyze_single(
                handle, seg_name, mask_name, filter_ids, min_area
            )

        n_cells = len(particle_df)
        total_particles = (
            int(particle_df["particle_count"].sum())
            if (n_cells > 0 and "particle_count" in particle_df.columns)
            else 0
        )

        # Merge with existing measurements. The key is conditional: when both
        # frames carry a ``timepoint`` column (time-lapse), merge on
        # ``["label", "timepoint"]`` — a label-only merge would match every label
        # across all timepoints and cartesian-explode the row count T-fold.
        current_df = self._session.df
        if (
            not current_df.empty
            and "label" in current_df.columns
            and "label" in particle_df.columns
        ):
            if "timepoint" in current_df.columns and "timepoint" in particle_df.columns:
                merge_keys = ["label", "timepoint"]
            else:
                merge_keys = ["label"]
            merged = current_df.merge(particle_df, on=merge_keys, how="left")
        else:
            merged = particle_df

        # Store-before-session
        self._repo.write_measurements(handle, merged)
        self._session.set_measurements(merged)

        return ParticleResult(
            summary_df=particle_df,
            detail_df=detail_df,
            n_cells=n_cells,
            total_particles=total_particles,
        )

    def _filter_labels(self, labels, filter_ids):
        """Zero out labels not in ``filter_ids`` (a copy); no-op when None."""
        if filter_ids is None:
            return labels
        cell_mask = np.isin(labels, filter_ids)
        labels = labels.copy()
        labels[~cell_mask] = 0
        return labels

    def _analyze_single(self, handle, seg_name, mask_name, filter_ids, min_area):
        """Historical single-timepoint analysis (no ``timepoint`` column)."""
        images = self._repo.read_channel_images(handle)
        labels = self._filter_labels(
            self._repo.read_labels(handle, seg_name), filter_ids
        )
        mask = self._repo.read_mask(handle, mask_name)
        particle_df = analyze_particles(images, labels, mask, min_area=min_area)
        detail_df = analyze_particles_detail(images, labels, mask, min_area=min_area)
        return particle_df, detail_df

    def _analyze_timelapse(self, handle, seg_name, mask_name, filter_ids, min_area):
        """Analyze every timepoint; tag rows with ``timepoint`` and concat.

        Pure domain particle functions stay 2D (contract D); the use case slices
        each frame on disk (contract B) and stamps the ``timepoint`` column. A
        frame with no cells contributes no rows.
        """
        n_timepoints = self._session.n_timepoints
        p_frames: list[pd.DataFrame] = []
        d_frames: list[pd.DataFrame] = []
        for t in range(n_timepoints):
            labels_t = self._filter_labels(
                self._repo.read_labels(handle, seg_name, timepoint=t), filter_ids
            )
            if labels_t.max() == 0:
                continue
            images_t = self._repo.read_channel_images(handle, timepoint=t)
            mask_t = self._repo.read_mask(handle, mask_name, timepoint=t)
            pdf = analyze_particles(images_t, labels_t, mask_t, min_area=min_area)
            ddf = analyze_particles_detail(
                images_t, labels_t, mask_t, min_area=min_area
            )
            pdf["timepoint"] = t
            ddf["timepoint"] = t
            p_frames.append(pdf)
            d_frames.append(ddf)

        if p_frames:
            return (
                pd.concat(p_frames, ignore_index=True),
                pd.concat(d_frames, ignore_index=True),
            )
        # No frame had cells: return correctly-columned empty frames (read
        # frame 0 to establish the schema) tagged with a timepoint column.
        images0 = self._repo.read_channel_images(handle, timepoint=0)
        labels0 = self._repo.read_labels(handle, seg_name, timepoint=0)
        mask0 = self._repo.read_mask(handle, mask_name, timepoint=0)
        empty_p = analyze_particles(images0, labels0, mask0, min_area=min_area)
        empty_d = analyze_particles_detail(images0, labels0, mask0, min_area=min_area)
        empty_p["timepoint"] = pd.Series(dtype=int)
        empty_d["timepoint"] = pd.Series(dtype=int)
        return empty_p, empty_d
