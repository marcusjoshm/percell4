"""Use case: per-cell particle/puncta analysis."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from percell4.application.session import Session
from percell4.domain.measure.particle import analyze_particles, analyze_particles_detail
from percell4.ports.dataset_repository import DatasetRepository
from percell4.domain.errors import NoDatasetError, NoMaskError, NoSegmentationError


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

        # Read data from repository
        images = self._repo.read_channel_images(handle)
        labels = self._repo.read_labels(handle, seg_name)

        # Apply cell filter
        if self._session.is_filtered and self._session.filter_ids:
            cell_mask = np.isin(labels, list(self._session.filter_ids))
            labels = labels.copy()
            labels[~cell_mask] = 0

        mask = self._repo.read_mask(handle, mask_name)

        # Run particle analysis
        particle_df = analyze_particles(images, labels, mask, min_area=min_area)
        detail_df = analyze_particles_detail(images, labels, mask, min_area=min_area)

        n_cells = len(particle_df)
        total_particles = (
            int(particle_df["particle_count"].sum()) if n_cells > 0 else 0
        )

        # Merge with existing measurements
        current_df = self._session.df
        if not current_df.empty and "label" in current_df.columns:
            merged = current_df.merge(particle_df, on="label", how="left")
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
