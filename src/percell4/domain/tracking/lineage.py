"""Lineage records and napari graph from tracking output.

Pure functions over plain pandas frames — no laptrack, h5py, Qt, or napari
imports (only numpy/pandas). Consumes the engine-agnostic ``track_df`` /
``split_df`` shape defined in :mod:`percell4.ports.tracker`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import NDArray

# CTC-style lineage table: one row per track. ``parent_track_id == NO_PARENT``
# marks a root (a track with no detected parent — present from the first
# frame it appears, not born from a division). We use -1 rather than 0
# because laptrack track ids are 0-based, so 0 is a valid track.
NO_PARENT = -1
LINEAGE_COLUMNS = [
    "track_id",
    "tree_id",
    "begin_t",
    "end_t",
    "parent_track_id",
]

# Columns a measurements frame must carry to build the napari Tracks array.
TRACKS_SOURCE_COLUMNS = ["track_id", "timepoint", "centroid_y", "centroid_x"]

__all__ = [
    "NO_PARENT",
    "LINEAGE_COLUMNS",
    "TRACKS_SOURCE_COLUMNS",
    "build_lineage_table",
    "build_napari_graph",
    "build_graph_from_lineage",
    "build_tracks_array",
    "select_complete_tracks",
]


def build_lineage_table(
    track_df: pd.DataFrame, split_df: pd.DataFrame
) -> pd.DataFrame:
    """Build a per-track lineage table from tracking output.

    Columns (:data:`LINEAGE_COLUMNS`): ``track_id``, ``tree_id``,
    ``begin_t`` (first timepoint the track is present), ``end_t`` (last
    timepoint), ``parent_track_id`` (the dividing parent, or
    :data:`NO_PARENT` for a root). A track that ends before the final
    timepoint is a death / field-of-view exit; a track with a parent is a
    division daughter.
    """
    if track_df is None or len(track_df) == 0:
        return pd.DataFrame(columns=LINEAGE_COLUMNS)

    child_to_parent: dict[int, int] = {}
    if split_df is not None and len(split_df) > 0:
        for _, row in split_df.iterrows():
            child_to_parent[int(row["child_track_id"])] = int(row["parent_track_id"])

    rows = []
    for track_id, grp in track_df.groupby("track_id"):
        tid = int(track_id)
        rows.append(
            {
                "track_id": tid,
                "tree_id": int(grp["tree_id"].iloc[0]),
                "begin_t": int(grp["timepoint"].min()),
                "end_t": int(grp["timepoint"].max()),
                "parent_track_id": int(child_to_parent.get(tid, NO_PARENT)),
            }
        )
    return (
        pd.DataFrame(rows, columns=LINEAGE_COLUMNS)
        .sort_values("track_id")
        .reset_index(drop=True)
    )


def build_tracks_array(measurements: pd.DataFrame) -> NDArray:
    """Build the napari Tracks ``data`` array ``[track_id, t, y, x]``.

    Built from a time-lapse measurements frame carrying
    :data:`TRACKS_SOURCE_COLUMNS`. Rows are sorted by ``track_id`` then
    ``timepoint`` (napari's expected ordering). Returns an empty ``(0, 4)``
    array when the required columns are absent (e.g. an untracked or
    single-timepoint measurement).
    """
    if (
        measurements is None
        or len(measurements) == 0
        or not all(c in measurements.columns for c in TRACKS_SOURCE_COLUMNS)
    ):
        return np.empty((0, 4), dtype=float)
    sub = measurements[TRACKS_SOURCE_COLUMNS].dropna(
        subset=["track_id", "timepoint"]
    )
    if sub.empty:
        return np.empty((0, 4), dtype=float)
    sub = sub.sort_values(["track_id", "timepoint"])
    return sub.to_numpy(dtype=float)


def select_complete_tracks(
    measurements: pd.DataFrame, lineage_df: pd.DataFrame, n_timepoints: int
) -> pd.DataFrame:
    """Return the long-format measurement rows for *complete* tracks only.

    A complete track is followed cleanly through the whole movie: present in
    **every** timepoint (no gaps), not a division daughter
    (``parent_track_id == NO_PARENT``), and never itself a division parent.

    ``measurements`` is one dataset's per-``(timepoint, track_id)`` rows (must
    carry ``timepoint`` and ``track_id`` columns); ``lineage_df`` is that
    dataset's ``/tracks`` table. Returns the subset of ``measurements`` for
    the qualifying tracks (empty when none qualify or the inputs lack the
    required columns).
    """
    if (
        measurements is None
        or measurements.empty
        or not {"timepoint", "track_id"} <= set(measurements.columns)
        or lineage_df is None
        or lineage_df.empty
        or n_timepoints < 1
    ):
        empty = measurements.iloc[0:0] if measurements is not None else pd.DataFrame()
        return empty

    # Tracks that divide (appear as a parent of any daughter) are excluded.
    parents = {
        int(p) for p in lineage_df["parent_track_id"].astype(int) if p != NO_PARENT
    }
    eligible = {
        int(row["track_id"])
        for _, row in lineage_df.iterrows()
        if int(row["parent_track_id"]) == NO_PARENT
        and int(row["track_id"]) not in parents
    }

    # Require a row at every timepoint (no gaps; this also implies the track
    # spans begin_t == 0 to end_t == n_timepoints - 1).
    full = set(range(n_timepoints))
    covered = {
        int(track_id)
        for track_id, grp in measurements.groupby("track_id")
        if set(grp["timepoint"].astype(int)) >= full
    }

    keep = eligible & covered
    return measurements[measurements["track_id"].astype(int).isin(list(keep))]


def build_napari_graph(split_df: pd.DataFrame) -> dict[int, list[int]]:
    """Build the napari Tracks ``graph`` dict ``{child_track_id: [parent...]}``.

    Passed to ``viewer.add_tracks(data, graph=...)`` to draw division links.
    Empty when there are no divisions.
    """
    graph: dict[int, list[int]] = {}
    if split_df is None or len(split_df) == 0:
        return graph
    for _, row in split_df.iterrows():
        child = int(row["child_track_id"])
        parent = int(row["parent_track_id"])
        graph.setdefault(child, []).append(parent)
    return graph


def build_graph_from_lineage(lineage_df: pd.DataFrame) -> dict[int, list[int]]:
    """Build the napari Tracks ``graph`` from a stored lineage table.

    The lineage table (see :func:`build_lineage_table`) is per-track with a
    ``parent_track_id`` column; a row whose parent is not :data:`NO_PARENT`
    is a division daughter. Returns ``{child_track_id: [parent_track_id]}``.
    """
    graph: dict[int, list[int]] = {}
    if lineage_df is None or len(lineage_df) == 0:
        return graph
    for _, row in lineage_df.iterrows():
        parent = int(row["parent_track_id"])
        if parent == NO_PARENT:
            continue
        graph.setdefault(int(row["track_id"]), []).append(parent)
    return graph
