"""Delete Selected Label is scoped to the displayed timepoint (time-lapse)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.gui.segmentation_panel import SegmentationPanel
from percell4.gui.viewer import ViewerWindow
from percell4.model import CellDataModel


class _FakeStatusBar:
    def showMessage(self, msg):
        pass


class _FakeLauncher:
    def __init__(self, viewer_win):
        self._windows = {"viewer": viewer_win}
        self._bar = _FakeStatusBar()

    def statusBar(self):
        return self._bar


@pytest.fixture
def harness(qtbot):
    session = Session()
    session.set_dataset(
        DatasetHandle(path=Path("/tmp/movie.h5"), metadata={"n_timepoints": 2})
    )
    model = CellDataModel(session=session)
    viewer_win = ViewerWindow(model)
    viewer_win._ensure_viewer()

    # (T=2, H, W) labels: label 5 occupies the same spot in BOTH frames.
    labels = np.zeros((2, 8, 8), dtype=np.int32)
    labels[0, 1:4, 1:4] = 5
    labels[1, 1:4, 1:4] = 5
    viewer_win.add_labels(labels, name="seg")
    session.set_active_segmentation("seg")

    panel = SegmentationPanel(model, launcher=_FakeLauncher(viewer_win))
    qtbot.addWidget(panel)
    yield session, viewer_win, panel
    try:
        viewer_win.viewer.close()
    except Exception:
        pass


def test_delete_only_removes_label_in_displayed_timepoint(harness):
    session, viewer_win, panel = harness
    layer = viewer_win.viewer.layers["seg"]

    # Display timepoint 1 and select label 5.
    viewer_win.viewer.dims.set_current_step(0, 1)
    layer.selected_label = 5

    panel._on_delete_selected_label()

    data = viewer_win.viewer.layers["seg"].data
    # Frame 1 (displayed) cleared; frame 0 untouched.
    assert not np.any(data[1] == 5)
    assert np.any(data[0] == 5)


def test_delete_at_timepoint_zero_leaves_other_frame(harness):
    session, viewer_win, panel = harness
    layer = viewer_win.viewer.layers["seg"]

    viewer_win.viewer.dims.set_current_step(0, 0)
    layer.selected_label = 5
    panel._on_delete_selected_label()

    data = viewer_win.viewer.layers["seg"].data
    assert not np.any(data[0] == 5)
    assert np.any(data[1] == 5)
