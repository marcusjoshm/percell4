"""Port: cell tracking across a time-lapse label stack (driven adapter)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd
from numpy.typing import NDArray

# Column contracts the rest of the app depends on (kept stable across
# tracking engines so the use case / lineage helpers never see laptrack).
TRACK_COLUMNS = ["timepoint", "label", "track_id", "tree_id"]
SPLIT_COLUMNS = ["parent_track_id", "child_track_id"]


@dataclass
class TrackingResult:
    """Tracking output as plain pandas frames (engine-agnostic).

    ``track_df`` — one row per (timepoint, segmented cell), columns
    :data:`TRACK_COLUMNS`. ``track_id`` is stable across timepoints for the
    same physical cell; ``tree_id`` groups a lineage (a dividing cell and
    its descendants share a tree).

    ``split_df`` — one row per parent→daughter division link, columns
    :data:`SPLIT_COLUMNS`. Empty when no divisions were detected.
    """

    track_df: pd.DataFrame
    split_df: pd.DataFrame


class Tracker(Protocol):
    """Interface for cell tracking. Implementation: LaptrackTracker."""

    def track(self, label_stack: NDArray) -> TrackingResult:
        """Track cells across a ``(T, H, W)`` label stack.

        Returns a :class:`TrackingResult`. Never raises on cells that
        appear, die, or leave the field of view — those simply begin/end
        their track.
        """
        ...
