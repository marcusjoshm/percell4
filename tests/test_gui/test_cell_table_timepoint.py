"""Slider-follows-frame slicing for peer views (U16/U17)."""

from __future__ import annotations

import pandas as pd

from percell4.interfaces.gui.peer_views._timepoint_view import active_timepoint_view


def test_slices_to_active_timepoint():
    df = pd.DataFrame(
        {"label": [1, 2, 1, 2], "timepoint": [0, 0, 1, 1], "area": [10, 20, 30, 40]}
    )
    v0 = active_timepoint_view(df, 0)
    assert list(v0["label"]) == [1, 2]
    assert list(v0["area"]) == [10, 20]
    # Index reset so positional label->row maps stay clean.
    assert list(v0.index) == [0, 1]

    v1 = active_timepoint_view(df, 1)
    assert list(v1["area"]) == [30, 40]


def test_no_timepoint_column_returns_unchanged():
    """Single-timepoint measurements (no timepoint column) are byte-identical."""
    df = pd.DataFrame({"label": [1, 2], "area": [10, 20]})
    out = active_timepoint_view(df, 0)
    pd.testing.assert_frame_equal(out, df)


def test_empty_when_timepoint_absent_from_data():
    df = pd.DataFrame({"label": [1], "timepoint": [0], "area": [5]})
    assert len(active_timepoint_view(df, 5)) == 0


def test_label_to_row_unique_per_frame():
    """After slicing, each frame's labels are unique -> the table's label->row
    map no longer collapses a tracked label to its last frame."""
    df = pd.DataFrame(
        {"label": [1, 2, 1, 2], "timepoint": [0, 0, 1, 1], "area": [1, 2, 3, 4]}
    )
    frame1 = active_timepoint_view(df, 1)
    label_to_row = {int(v): i for i, v in enumerate(frame1["label"])}
    assert label_to_row == {1: 0, 2: 1}
    assert frame1.iloc[label_to_row[1]]["area"] == 3  # frame 1's value, not frame 0


def test_peer_views_import():
    """Both peer views import cleanly with the new subscription wiring."""
    import percell4.interfaces.gui.main_window  # noqa: F401
    import percell4.interfaces.gui.peer_views.cell_table  # noqa: F401
    import percell4.interfaces.gui.peer_views.data_plot  # noqa: F401
