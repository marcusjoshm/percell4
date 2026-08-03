"""Assemble a tracked segmentation + lineage from raw labels + tracking output.

Pure numpy/pandas — no laptrack, h5py, Qt, or napari. Shared by the
``TrackCells`` use case (repo/Session path) and the workflow ``track_one``
phase (DatasetStore path) so the id-shift / relabel / lineage logic can't
drift between them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from percell4.domain.tracking.lineage import build_lineage_table
from percell4.domain.tracking.relabel import relabel_stack_by_track

__all__ = ["TrackedResult", "build_tracked_result"]


@dataclass
class TrackedResult:
    """Outcome of relabeling a raw stack into a track-consistent segmentation."""

    tracked_labels: NDArray[np.int32]  # (T, H, W), label value == track id
    lineage: pd.DataFrame              # LINEAGE_COLUMNS
    n_tracks: int
    n_divisions: int                   # count of dividing parents


def build_tracked_result(
    raw_stack: NDArray, track_df: pd.DataFrame, split_df: pd.DataFrame
) -> TrackedResult:
    """Shift track ids to 1-based, relabel the stack, and build the lineage.

    ``track_df`` / ``split_df`` are the engine-agnostic tracking frames (see
    :class:`percell4.ports.tracker.TrackingResult`). Track ids are shifted to
    1-based so the relabeled pixel value can equal the track id without
    colliding with background (0); the split links shift in lockstep so the
    lineage stays consistent with the stored labels.
    """
    td = track_df.copy()
    if not td.empty:
        td["track_id"] = td["track_id"].astype(int) + 1
    sd = split_df.copy()
    if not sd.empty:
        sd["parent_track_id"] = sd["parent_track_id"].astype(int) + 1
        sd["child_track_id"] = sd["child_track_id"].astype(int) + 1

    tracked = relabel_stack_by_track(raw_stack, td)
    lineage = build_lineage_table(td, sd)

    n_tracks = int(td["track_id"].nunique()) if not td.empty else 0
    # A division is one dividing parent (each yields multiple split rows).
    n_divisions = (
        int(sd["parent_track_id"].nunique()) if not sd.empty else 0
    )
    return TrackedResult(
        tracked_labels=tracked,
        lineage=lineage,
        n_tracks=n_tracks,
        n_divisions=n_divisions,
    )
