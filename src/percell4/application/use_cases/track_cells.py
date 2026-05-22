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
from percell4.domain.tracking.lineage import build_lineage_table
from percell4.domain.tracking.relabel import relabel_stack_by_track
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
        """Track ``raw_seg_name`` and write the tracked labels + lineage.

        Replaces the segmentation **in place** by default (``tracked_name``
        defaults to ``raw_seg_name``): the per-frame Cellpose labels are
        overwritten with track-consistent ids and a ``/tracks/<name>``
        lineage table is written alongside, so there is exactly one
        segmentation resource — not a separate ``_tracked`` copy. Pass an
        explicit ``tracked_name`` to write to a different resource instead.

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

        # Shift track ids to 1-based so the relabeled pixel value can equal the
        # track id without colliding with background (0). split links shift too
        # so lineage stays consistent with the stored labels.
        track_df = result.track_df.copy()
        if not track_df.empty:
            track_df["track_id"] = track_df["track_id"].astype(int) + 1
        split_df = result.split_df.copy()
        if not split_df.empty:
            split_df["parent_track_id"] = split_df["parent_track_id"].astype(int) + 1
            split_df["child_track_id"] = split_df["child_track_id"].astype(int) + 1

        tracked = relabel_stack_by_track(raw, track_df)
        lineage = build_lineage_table(track_df, split_df)

        # Replace the segmentation in place by default — overwrite the raw
        # labels rather than spawning a parallel "<name>_tracked" resource.
        seg_name = tracked_name or raw_seg_name

        # Creator: store labels + lineage, refresh inventory, set active.
        self._repo.write_labels(handle, seg_name, tracked)
        self._repo.write_tracks(handle, seg_name, lineage)
        mask_set = set(self._repo.list_masks(handle))
        seg_names = [
            n for n in self._repo.list_labels(handle) if n not in mask_set
        ]
        self._session.refresh_resource_lists(segmentation_names=seg_names)
        self._session.set_active_segmentation(seg_name)

        n_tracks = int(track_df["track_id"].nunique()) if not track_df.empty else 0
        # A division is one dividing parent (each yields multiple split rows).
        n_divisions = (
            int(split_df["parent_track_id"].nunique()) if not split_df.empty else 0
        )
        return TrackResult(
            tracked_labels=tracked,
            seg_name=seg_name,
            n_tracks=n_tracks,
            n_divisions=n_divisions,
        )
