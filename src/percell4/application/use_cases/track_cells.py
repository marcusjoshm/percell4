"""Use case: track cells across timepoints and persist a tracked segmentation.

Reads a raw per-frame ``(T, H, W)`` segmentation, runs a :class:`Tracker`,
relabels every frame so each cell's pixel value equals its (1-based) track id
— stable across time — and writes both the tracked segmentation and a lineage
table. Follows the Creator contract: store, refresh inventory, set active.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.errors import NoDatasetError
from percell4.domain.tracking.build import build_tracked_result
from percell4.ports.dataset_repository import DatasetRepository
from percell4.ports.tracker import Tracker


@dataclass
class TrackResult:
    """Result of a tracking run."""

    tracked_labels: NDArray[np.int32]
    seg_name: str
    n_tracks: int
    n_divisions: int


class TrackCells:
    """Track cells across a time-lapse segmentation (Creator)."""

    def __init__(
        self, repo: DatasetRepository, session: Session, tracker: Tracker
    ) -> None:
        self._repo = repo
        self._session = session
        self._tracker = tracker

    def execute(
        self, raw_seg_name: str, tracked_name: str | None = None
    ) -> TrackResult:
        """Track ``raw_seg_name`` and write a tracked segmentation + lineage.

        Writes a **new** ``"<raw>_tracked"`` resource (``tracked_name``
        overrides) and **preserves the original** raw per-frame Cellpose
        segmentation: the raw layer is far easier to hand-edit if a track is
        wrong, so both are kept. The tracked resource carries track-consistent
        ids (label value == track id) with a ``/tracks/<name>`` lineage table.

        Raises :class:`NoDatasetError` when no dataset is loaded and
        ``ValueError`` when the dataset is not time-lapse or the raw
        segmentation is not a ``(T, H, W)`` stack.
        """
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")
        if self._session.n_timepoints <= 1:
            raise ValueError(
                "Tracking requires a time-lapse dataset (>= 2 timepoints)."
            )

        raw = self._repo.read_labels(handle, raw_seg_name)
        if raw.ndim != 3:
            raise ValueError(
                f"Raw segmentation {raw_seg_name!r} must be a (T, H, W) stack, "
                f"got {raw.ndim}D — segment all timepoints first."
            )

        result = self._tracker.track(raw)
        built = build_tracked_result(raw, result.track_df, result.split_df)

        # Write a new resource and keep the raw segmentation intact (the raw
        # per-frame labels are easier to correct than the tracked ones).
        seg_name = tracked_name or f"{raw_seg_name}_tracked"

        # Creator: store labels + lineage, refresh inventory, set active.
        self._repo.write_labels(handle, seg_name, built.tracked_labels)
        self._repo.write_tracks(handle, seg_name, built.lineage)
        mask_set = set(self._repo.list_masks(handle))
        seg_names = [
            n for n in self._repo.list_labels(handle) if n not in mask_set
        ]
        self._session.refresh_resource_lists(segmentation_names=seg_names)
        self._session.set_active_segmentation(seg_name)

        return TrackResult(
            tracked_labels=built.tracked_labels,
            seg_name=seg_name,
            n_tracks=built.n_tracks,
            n_divisions=built.n_divisions,
        )
