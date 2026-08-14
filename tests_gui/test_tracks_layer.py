"""ViewerWindow.add_tracks / show_tracks_from_measurements (real napari)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from percell4.application.session import Session
from percell4.gui.viewer import ViewerWindow
from percell4.model import CellDataModel


@pytest.fixture
def viewer_win(qtbot):
    vw = ViewerWindow(CellDataModel(session=Session()))
    vw._ensure_viewer()
    yield vw
    try:
        vw.viewer.close()
    except Exception:
        pass


def test_add_tracks_creates_tracks_layer(viewer_win):
    import napari

    # Two tracks over two timepoints: [track_id, t, y, x].
    data = np.array(
        [
            [1, 0, 1.0, 1.0],
            [1, 1, 2.0, 2.0],
            [2, 0, 5.0, 5.0],
            [2, 1, 6.0, 6.0],
        ]
    )
    viewer_win.add_tracks(data, graph={}, name="tracks")

    assert "tracks" in viewer_win.viewer.layers
    assert isinstance(viewer_win.viewer.layers["tracks"], napari.layers.Tracks)


def test_add_tracks_with_division_graph(viewer_win):
    # Parent track 1 (t0) -> daughters 2, 3 (t1).
    data = np.array(
        [
            [1, 0, 5.0, 5.0],
            [2, 1, 3.0, 3.0],
            [3, 1, 7.0, 7.0],
        ]
    )
    viewer_win.add_tracks(data, graph={2: [1], 3: [1]}, name="tracks")
    layer = viewer_win.viewer.layers["tracks"]
    assert layer.graph == {2: [1], 3: [1]}


def test_add_tracks_empty_is_noop(viewer_win):
    viewer_win.add_tracks(np.empty((0, 4)), graph={}, name="tracks")
    assert "tracks" not in viewer_win.viewer.layers


def test_add_tracks_replaces_existing(viewer_win):
    data = np.array([[1, 0, 1.0, 1.0], [1, 1, 2.0, 2.0]])
    viewer_win.add_tracks(data, name="tracks")
    viewer_win.add_tracks(data, name="tracks")  # second call must not duplicate
    n = sum(1 for layer in viewer_win.viewer.layers if layer.name == "tracks")
    assert n == 1


def test_show_tracks_from_measurements(viewer_win):
    measurements = pd.DataFrame(
        {
            "track_id": [1, 1, 2],
            "timepoint": [0, 1, 1],
            "centroid_y": [1.0, 2.0, 9.0],
            "centroid_x": [1.0, 2.0, 9.0],
            "label": [1, 1, 2],
        }
    )
    lineage = pd.DataFrame(
        {
            "track_id": [1, 2],
            "tree_id": [0, 0],
            "begin_t": [0, 1],
            "end_t": [1, 1],
            "parent_track_id": [-1, 1],
        }
    )
    viewer_win.show_tracks_from_measurements(measurements, lineage_df=lineage, name="tracks")

    assert "tracks" in viewer_win.viewer.layers
    # Daughter track 2 links to parent 1 in the layer graph.
    assert viewer_win.viewer.layers["tracks"].graph == {2: [1]}


def test_show_tracks_from_measurements_tracked_timelapse(viewer_win):
    """A tracked time-lapse measurements df renders a Tracks layer -- the
    lineage overlay U18a draws after a successful measure."""
    import napari

    df = pd.DataFrame(
        {
            "label": [1, 2, 1, 2],
            "track_id": [1, 2, 1, 2],
            "timepoint": [0, 0, 1, 1],
            "centroid_y": [1.0, 5.0, 2.0, 6.0],
            "centroid_x": [1.0, 5.0, 2.0, 6.0],
        }
    )
    viewer_win.show_tracks_from_measurements(df, lineage_df=None, name="cp_tracks")

    assert "cp_tracks" in viewer_win.viewer.layers
    assert isinstance(viewer_win.viewer.layers["cp_tracks"], napari.layers.Tracks)


def test_show_tracks_untracked_measurements_is_noop(viewer_win):
    """An untracked measurements df (no track_id) draws no Tracks layer."""
    df = pd.DataFrame(
        {"label": [1, 2], "timepoint": [0, 1], "centroid_y": [1.0, 2.0],
         "centroid_x": [1.0, 2.0]}
    )
    viewer_win.show_tracks_from_measurements(df, lineage_df=None, name="cp_tracks")
    assert "cp_tracks" not in viewer_win.viewer.layers
