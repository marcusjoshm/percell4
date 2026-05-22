"""Pure lineage-table and napari-graph builders."""

from __future__ import annotations

import pandas as pd

from percell4.domain.tracking.lineage import (
    LINEAGE_COLUMNS,
    NO_PARENT,
    build_lineage_table,
    build_napari_graph,
)


def _track_df(rows):
    return pd.DataFrame(rows, columns=["timepoint", "label", "track_id", "tree_id"])


def test_lineage_table_single_track_no_parent():
    track_df = _track_df(
        [
            (0, 1, 0, 0),
            (1, 1, 0, 0),
            (2, 1, 0, 0),
        ]
    )
    split_df = pd.DataFrame(columns=["parent_track_id", "child_track_id"])
    out = build_lineage_table(track_df, split_df)

    assert list(out.columns) == LINEAGE_COLUMNS
    assert len(out) == 1
    row = out.iloc[0]
    assert row["track_id"] == 0
    assert row["begin_t"] == 0
    assert row["end_t"] == 2
    assert row["parent_track_id"] == NO_PARENT  # root


def test_lineage_table_division_links_parent():
    # Parent track 0 (t0) -> daughters 1 and 2 (t1..).
    track_df = _track_df(
        [
            (0, 1, 0, 0),
            (1, 2, 1, 0),
            (1, 3, 2, 0),
            (2, 2, 1, 0),
            (2, 3, 2, 0),
        ]
    )
    split_df = pd.DataFrame(
        {"parent_track_id": [0, 0], "child_track_id": [1, 2]}
    )
    out = build_lineage_table(track_df, split_df).set_index("track_id")

    assert out.loc[0, "parent_track_id"] == NO_PARENT
    assert out.loc[0, "end_t"] == 0  # parent ends at division frame
    assert out.loc[1, "parent_track_id"] == 0
    assert out.loc[2, "parent_track_id"] == 0
    assert out.loc[1, "begin_t"] == 1  # daughter born at t1
    # All share one tree.
    assert set(out["tree_id"]) == {0}


def test_lineage_table_death_track_ends_before_last():
    track_df = _track_df([(0, 1, 0, 0), (1, 1, 0, 0)])  # dies after t1
    out = build_lineage_table(track_df, pd.DataFrame(columns=["parent_track_id", "child_track_id"]))
    assert out.iloc[0]["end_t"] == 1


def test_lineage_table_empty_input():
    out = build_lineage_table(
        _track_df([]), pd.DataFrame(columns=["parent_track_id", "child_track_id"])
    )
    assert out.empty
    assert list(out.columns) == LINEAGE_COLUMNS


def test_napari_graph_maps_children_to_parents():
    split_df = pd.DataFrame(
        {"parent_track_id": [0, 0], "child_track_id": [1, 2]}
    )
    graph = build_napari_graph(split_df)
    assert graph == {1: [0], 2: [0]}


def test_napari_graph_empty_when_no_splits():
    graph = build_napari_graph(
        pd.DataFrame(columns=["parent_track_id", "child_track_id"])
    )
    assert graph == {}


def test_track_id_zero_is_valid_not_confused_with_no_parent():
    # Root track id 0 must report NO_PARENT (-1), not be mistaken for "parent 0".
    track_df = _track_df([(0, 1, 0, 0)])
    out = build_lineage_table(track_df, pd.DataFrame(columns=["parent_track_id", "child_track_id"]))
    assert out.iloc[0]["track_id"] == 0
    assert out.iloc[0]["parent_track_id"] == NO_PARENT
    assert NO_PARENT == -1
