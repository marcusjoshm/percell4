"""Pure relabel-by-track helper."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from percell4.domain.tracking.relabel import relabel_stack_by_track


def _track_df(rows):
    return pd.DataFrame(rows, columns=["timepoint", "label", "track_id"])


def test_relabel_makes_pixel_value_equal_track_id_across_frames():
    # Cell raw-label 5 at t0 and raw-label 2 at t1 are the same track (id 1).
    raw = np.zeros((2, 6, 6), dtype=np.int32)
    raw[0, 0:2, 0:2] = 5
    raw[1, 1:3, 1:3] = 2
    track_df = _track_df([(0, 5, 1), (1, 2, 1)])

    out = relabel_stack_by_track(raw, track_df)

    assert out.shape == (2, 6, 6)
    # Same track id (1) in both frames where the cell appears.
    assert out[0, 0, 0] == 1
    assert out[1, 1, 1] == 1
    # Background stays 0.
    assert out[0, 5, 5] == 0


def test_relabel_division_assigns_distinct_daughter_ids():
    raw = np.zeros((2, 6, 6), dtype=np.int32)
    raw[0, 0:4, 0:4] = 1  # parent
    raw[1, 0:4, 0:2] = 7  # daughter A
    raw[1, 0:4, 4:6] = 9  # daughter B
    # parent track 1, daughters tracks 2 and 3
    track_df = _track_df([(0, 1, 1), (1, 7, 2), (1, 9, 3)])

    out = relabel_stack_by_track(raw, track_df)
    assert out[0, 0, 0] == 1
    assert out[1, 0, 0] == 2
    assert out[1, 0, 5] == 3


def test_relabel_drops_untracked_cells():
    raw = np.zeros((1, 4, 4), dtype=np.int32)
    raw[0, 0:2, 0:2] = 5  # tracked
    raw[0, 2:4, 2:4] = 8  # NOT in track_df -> dropped
    track_df = _track_df([(0, 5, 1)])
    out = relabel_stack_by_track(raw, track_df)
    assert out[0, 0, 0] == 1
    assert out[0, 3, 3] == 0


def test_relabel_requires_3d():
    with pytest.raises(ValueError, match=r"\(T, H, W\)"):
        relabel_stack_by_track(np.zeros((4, 4), np.int32), _track_df([]))
