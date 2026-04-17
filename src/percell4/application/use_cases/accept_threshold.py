"""Use case: accept a threshold and save the mask."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.ports.dataset_repository import DatasetRepository
from percell4.ports.viewer import ViewerPort
from percell4.domain.errors import NoDatasetError, NoMaskError, NoSegmentationError


@dataclass
class ThresholdResult:
    """Result of accepting a threshold."""

    mask_name: str
    threshold_value: float
    n_positive: int
    n_total: int


class AcceptThreshold:
    """Accept a threshold value and save the binary mask.

    Computes the mask from a pre-smoothed image and threshold value,
    writes to store (before viewer — store-before-layer invariant),
    and updates the session's active mask.
    """

    def __init__(
        self,
        repo: DatasetRepository,
        viewer: ViewerPort,
        session: Session,
    ) -> None:
        self._repo = repo
        self._viewer = viewer
        self._session = session

    def execute(
        self,
        image: NDArray[np.float32],
        threshold_value: float,
        method: str,
        channel_name: str,
    ) -> ThresholdResult:
        """Apply threshold and persist the mask.

        Args:
            image: Pre-smoothed image to threshold (from preview state).
            threshold_value: The threshold cutoff.
            method: Threshold method name (for the mask name).
            channel_name: Channel name (for the mask name).
        """
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        mask = (image > threshold_value).astype(np.uint8)
        mask_name = f"{method}_{channel_name}"

        # Store-before-layer: write to HDF5 first
        self._repo.write_mask(handle, mask_name, mask)

        # Update session
        self._session.set_active_mask(mask_name)

        n_positive = int(mask.sum())
        return ThresholdResult(
            mask_name=mask_name,
            threshold_value=threshold_value,
            n_positive=n_positive,
            n_total=mask.size,
        )
