"""Shared tracked-result builder (U6) — id-shift + relabel + lineage."""

from __future__ import annotations

import numpy as np
import pandas as pd

from percell4.domain.tracking.build import build_tracked_result
from percell4.domain.tracking.lineage import NO_PARENT


def _track_df(rows):
    return pd.DataFrame(rows, columns=["timepoint", "label", "track_id", "tree_id"])


def _empty_split():
    return pd.DataFrame(columns=["parent_track_id", "child_track_id"])


def test_linking_shifts_to_one_based_and_relabels():
    raw = np.zeros((2, 6, 6), dtype=np.int32)
    raw[0, 0:2, 0:2] = 5   # raw label 5, track_id 0 (0-based)
    raw[1, 1:3, 1:3] = 2   # raw label 2, same track
    track_df = _track_df([(0, 5, 0, 0), (1, 2, 0, 0)])

    out = build_tracked_result(raw, track_df, _empty_split())

    # Track id 0 -> stored label 1 (1-based; 0 stays background).
    assert out.tracked_labels[0, 0, 0] == 1
    assert out.tracked_labels[1, 1, 1] == 1
    assert out.tracked_labels[0, 5, 5] == 0
    assert out.n_tracks == 1
    assert out.n_divisions == 0
    row = out.lineage.set_index("track_id").loc[1]
    assert row["parent_track_id"] == NO_PARENT
    assert row["begin_t"] == 0 and row["end_t"] == 1


def test_division_shifts_split_links_in_lockstep():
    raw = np.zeros((2, 6, 6), dtype=np.int32)
    raw[0, 0:4, 0:4] = 1            # parent, track 0
    raw[1, 0:4, 0:2] = 2            # daughter A, track 1
    raw[1, 0:4, 4:6] = 3            # daughter B, track 2
    track_df = _track_df([(0, 1, 0, 0), (1, 2, 1, 0), (1, 3, 2, 0)])
    split_df = pd.DataFrame({"parent_track_id": [0, 0], "child_track_id": [1, 2]})

    out = build_tracked_result(raw, track_df, split_df)

    # 1-based: parent track 1, daughters 2 and 3.
    assert out.tracked_labels[0, 0, 0] == 1
    assert out.tracked_labels[1, 0, 0] == 2
    assert out.tracked_labels[1, 0, 5] == 3
    assert out.n_tracks == 3
    assert out.n_divisions == 1
    lineage = out.lineage.set_index("track_id")
    assert lineage.loc[2, "parent_track_id"] == 1
    assert lineage.loc[3, "parent_track_id"] == 1
    assert lineage.loc[1, "parent_track_id"] == NO_PARENT


def test_empty_tracking_yields_empty_result():
    raw = np.zeros((2, 4, 4), dtype=np.int32)
    out = build_tracked_result(
        raw, _track_df([]), _empty_split()
    )
    assert out.n_tracks == 0
    assert out.n_divisions == 0
    assert not out.tracked_labels.any()
