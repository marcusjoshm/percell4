"""select_complete_tracks: full-span, gap-free, non-dividing tracks only (U7)."""

from __future__ import annotations

import pandas as pd

from percell4.domain.tracking.lineage import select_complete_tracks


def _measurements():
    # n_timepoints = 3.
    return pd.DataFrame(
        [
            # track 1: present every frame, root, not a parent -> COMPLETE
            (0, 1, -1), (1, 1, -1), (2, 1, -1),
            # track 4: root but DIVIDES (parent of 5) and only at t0 -> excluded
            (0, 4, -1),
            # track 5: division daughter (parent 4), partial -> excluded
            (1, 5, 4), (2, 5, 4),
            # track 7: root, not a parent, but GAP at t1 -> excluded
            (0, 7, -1), (2, 7, -1),
        ],
        columns=["timepoint", "track_id", "parent_track_id"],
    )


def _lineage():
    return pd.DataFrame(
        {"track_id": [1, 4, 5, 7], "parent_track_id": [-1, -1, 4, -1]}
    )


def test_keeps_only_full_span_gapfree_nondividing():
    out = select_complete_tracks(_measurements(), _lineage(), n_timepoints=3)
    assert set(out["track_id"].unique()) == {1}
    assert len(out) == 3  # one row per timepoint for track 1


def test_empty_when_no_track_columns():
    plain = pd.DataFrame({"label": [1, 2], "area": [10, 20]})
    out = select_complete_tracks(plain, _lineage(), n_timepoints=3)
    assert out.empty


def test_empty_when_no_complete_tracks():
    # Only a dividing parent + its daughters -> nothing complete.
    meas = pd.DataFrame(
        [(0, 4, -1), (1, 5, 4), (2, 5, 4), (1, 6, 4), (2, 6, 4)],
        columns=["timepoint", "track_id", "parent_track_id"],
    )
    lineage = pd.DataFrame(
        {"track_id": [4, 5, 6], "parent_track_id": [-1, 4, 4]}
    )
    out = select_complete_tracks(meas, lineage, n_timepoints=3)
    assert out.empty


def test_single_timepoint_track_complete_when_n_is_one():
    meas = pd.DataFrame([(0, 1, -1)], columns=["timepoint", "track_id", "parent_track_id"])
    lineage = pd.DataFrame({"track_id": [1], "parent_track_id": [-1]})
    out = select_complete_tracks(meas, lineage, n_timepoints=1)
    assert set(out["track_id"]) == {1}
