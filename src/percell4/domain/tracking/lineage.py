"""Lineage records and napari graph from tracking output.

Pure functions over plain pandas frames — no laptrack, h5py, Qt, or napari
imports (only numpy/pandas). Consumes the engine-agnostic ``track_df`` /
``split_df`` shape defined in :mod:`percell4.ports.tracker`.
"""

from __future__ import annotations

import pandas as pd

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

__all__ = ["NO_PARENT", "LINEAGE_COLUMNS", "build_lineage_table", "build_napari_graph"]


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
