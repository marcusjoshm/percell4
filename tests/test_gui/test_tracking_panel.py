"""Track Cells Creator guard conditions (no HDF5 / worker needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.gui.segmentation_panel import SegmentationPanel
from percell4.model import CellDataModel


class _FakeStatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, msg):
        self.messages.append(msg)


class _FakeLauncher:
    def __init__(self):
        self._bar = _FakeStatusBar()
        self._windows = {}

    def statusBar(self):
        return self._bar


@pytest.fixture
def panel(qtbot):
    session = Session()
    model = CellDataModel(session=session)
    launcher = _FakeLauncher()
    p = SegmentationPanel(model, launcher=launcher)
    qtbot.addWidget(p)
    return p, session, launcher


def test_track_button_requires_timelapse(panel):
    p, session, launcher = panel
    session.set_dataset(
        DatasetHandle(path=Path("/tmp/still.h5"), metadata={"n_timepoints": 1})
    )
    session.set_active_segmentation("cellpose")

    p._on_track_cells()

    assert any("time-lapse" in m for m in launcher._bar.messages)


def test_track_button_requires_active_segmentation(panel):
    p, session, launcher = panel
    session.set_dataset(
        DatasetHandle(path=Path("/tmp/movie.h5"), metadata={"n_timepoints": 3})
    )
    # No active segmentation selected.
    p._on_track_cells()

    assert any("segmentation" in m.lower() for m in launcher._bar.messages)
