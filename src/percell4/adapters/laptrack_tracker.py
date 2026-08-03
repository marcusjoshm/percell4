"""Adapter: cell tracking backed by laptrack's overlap tracker.

Isolates the ``laptrack`` dependency behind the :class:`Tracker` port so the
domain and application layers never import it. Translates laptrack's
``OverLapTrack`` output into the engine-agnostic :class:`TrackingResult`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from percell4.ports.tracker import (
    SPLIT_COLUMNS,
    TRACK_COLUMNS,
    TrackingResult,
)

# laptrack's OverLapTrack default link cost cutoff. Splitting (division)
# detection is OFF in laptrack by default (splitting_cutoff=False); we
# enable it at the same permissiveness as linking so lineage is detected
# out of the box. Both are exposed for tuning against real data.
_DEFAULT_CUTOFF = 225.0


class LaptrackTracker:
    """Tracker port implementation using laptrack overlap (IoU) tracking.

    Takes a stack of per-frame label masks and links the same physical cell
    across frames by spatial overlap, detecting divisions as one-parent →
    two-daughter splits.
    """

    def __init__(
        self,
        cutoff: float = _DEFAULT_CUTOFF,
        splitting_cutoff: float | None = None,
        **overlap_kwargs,
    ) -> None:
        self._cutoff = cutoff
        # None => enable splitting at the link cutoff (division on by default).
        self._splitting_cutoff = cutoff if splitting_cutoff is None else splitting_cutoff
        self._overlap_kwargs = overlap_kwargs

    def track(self, label_stack: NDArray) -> TrackingResult:
        from laptrack import OverLapTrack

        if label_stack.ndim != 3:
            raise ValueError(
                f"label_stack must be (T, H, W), got {label_stack.ndim}D"
            )
        frames = [np.asarray(label_stack[t]) for t in range(label_stack.shape[0])]

        # All-background input: laptrack raises on an empty coordinate set, so
        # short-circuit to an empty result (no cells -> no tracks/divisions).
        if not any(int(f.max()) > 0 for f in frames):
            return TrackingResult(
                track_df=pd.DataFrame(columns=TRACK_COLUMNS, dtype=int),
                split_df=pd.DataFrame(columns=SPLIT_COLUMNS, dtype=int),
            )

        tracker = OverLapTrack(
            cutoff=self._cutoff,
            splitting_cutoff=self._splitting_cutoff,
            **self._overlap_kwargs,
        )
        track_df, split_df, _merge_df = tracker.predict_overlap_dataframe(frames)

        return TrackingResult(
            track_df=_normalize_track_df(track_df),
            split_df=_normalize_split_df(split_df),
        )


def _normalize_track_df(track_df: pd.DataFrame) -> pd.DataFrame:
    """laptrack track_df (MultiIndex frame,label) -> flat TRACK_COLUMNS.

    Renames laptrack's ``frame`` to the app's ``timepoint`` term.
    """
    if track_df is None or len(track_df) == 0:
        return pd.DataFrame(columns=TRACK_COLUMNS, dtype=int)
    flat = track_df.reset_index().rename(columns={"frame": "timepoint"})
    out = flat[TRACK_COLUMNS].copy()
    return out.astype({c: int for c in TRACK_COLUMNS}).reset_index(drop=True)


def _normalize_split_df(split_df: pd.DataFrame) -> pd.DataFrame:
    """laptrack split_df -> flat SPLIT_COLUMNS (empty frame when no divisions)."""
    if split_df is None or len(split_df) == 0:
        return pd.DataFrame(columns=SPLIT_COLUMNS, dtype=int)
    out = split_df[SPLIT_COLUMNS].copy()
    return out.astype({c: int for c in SPLIT_COLUMNS}).reset_index(drop=True)
