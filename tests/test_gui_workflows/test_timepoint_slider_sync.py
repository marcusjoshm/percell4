"""Timepoint slider <-> session.active_timepoint sync (U4).

The napari dims slider acts as the timepoint Selector: moving it writes
session.active_timepoint, and a session write pushes the slider — a
controlled two-way sync guarded by a dedicated _timepoint_originator flag
so it never collides with label-selection or active-layer pushes.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.gui.viewer import ViewerWindow
from percell4.model import CellDataModel

# Builds a real napari viewer, so this module carries the ``napari_viewer``
# marker: skipped by default (see pyproject addopts), run explicitly on CI.
pytestmark = pytest.mark.napari_viewer


@pytest.fixture
def timelapse_viewer(qtbot):
    """Real napari viewer with a (T,H,W) image and a 3-timepoint dataset."""
    session = Session()
    session.set_dataset(DatasetHandle(path="/tmp/movie.h5", metadata={"n_timepoints": 3}))
    data_model = CellDataModel(session=session)
    viewer_win = ViewerWindow(data_model)
    viewer_win._ensure_viewer()
    # A (T,H,W) image gives napari a leading time axis -> dims slider.
    viewer_win.add_image(np.zeros((3, 8, 8), dtype=np.float32), name="GFP")
    yield session, data_model, viewer_win
    try:
        viewer_win.viewer.close()
    except Exception:
        pass


def test_session_push_moves_slider(timelapse_viewer):
    """session.set_active_timepoint moves the napari dims slider (axis 0)."""
    session, _model, viewer_win = timelapse_viewer
    viewer = viewer_win.viewer
    assert viewer.dims.ndim == 3  # time slider present

    session.set_active_timepoint(2)
    assert int(viewer.dims.current_step[0]) == 2


def test_slider_move_updates_session(timelapse_viewer):
    """Moving the napari slider writes session.active_timepoint (Selector)."""
    session, _model, viewer_win = timelapse_viewer
    viewer = viewer_win.viewer

    viewer.dims.set_current_step(0, 1)
    assert session.active_timepoint == 1


def test_no_feedback_loop(timelapse_viewer):
    """Push then settle: session and slider agree, originator flag cleared."""
    session, _model, viewer_win = timelapse_viewer
    viewer = viewer_win.viewer

    session.set_active_timepoint(2)
    assert session.active_timepoint == 2
    assert int(viewer.dims.current_step[0]) == 2
    assert viewer_win._timepoint_originator is False
    # The label-selection guard was never touched by the dims sync.
    assert viewer_win._is_originator is False


def test_single_timepoint_no_time_slider(qtbot):
    """A non-time-lapse dataset has no time slider; push is a safe no-op."""
    session = Session()
    session.set_dataset(DatasetHandle(path="/tmp/still.h5", metadata={"n_timepoints": 1}))
    data_model = CellDataModel(session=session)
    viewer_win = ViewerWindow(data_model)
    viewer_win._ensure_viewer()
    viewer_win.add_image(np.zeros((8, 8), dtype=np.float32), name="GFP")
    try:
        assert viewer_win.viewer.dims.ndim == 2  # no time slider
        # Pushing timepoint 0 is a no-op and must not raise.
        viewer_win._push_timepoint_to_napari(0)
        assert session.active_timepoint == 0
    finally:
        viewer_win.viewer.close()
