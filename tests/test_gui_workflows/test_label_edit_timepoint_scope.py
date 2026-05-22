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


class _FakeStore:
    """Minimal store: records label writes, reports tracked resources."""

    def __init__(self, tracked_names=()):
        self._tracked = list(tracked_names)
        self.writes = {}

    def list_tracks(self):
        return list(self._tracked)

    def write_labels(self, name, data):
        self.writes[name] = np.asarray(data).copy()


class _FakeLauncher:
    def __init__(self, viewer_win, store=None):
        self._windows = {"viewer": viewer_win}
        self._bar = _FakeStatusBar()
        self._current_store = store

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


@pytest.fixture
def tracked_harness(qtbot):
    """A time-lapse viewer whose active segmentation is tracked (has lineage)."""
    session = Session()
    session.set_dataset(
        DatasetHandle(path=Path("/tmp/movie.h5"), metadata={"n_timepoints": 2})
    )
    model = CellDataModel(session=session)
    viewer_win = ViewerWindow(model)
    viewer_win._ensure_viewer()
    labels = np.zeros((2, 8, 8), dtype=np.int32)
    labels[0, 1:4, 1:4] = 5
    labels[1, 1:4, 1:4] = 5
    viewer_win.add_labels(labels, name="seg")
    store = _FakeStore(tracked_names=["seg"])
    panel = SegmentationPanel(model, launcher=_FakeLauncher(viewer_win, store=store))
    qtbot.addWidget(panel)
    # Selecting the segmentation drives _sync_relabel_enabled via state_changed.
    session.set_active_segmentation("seg")
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


# ── Relabel sequential: frame-scoped + disabled on tracked ────


def test_relabel_sequential_scoped_to_displayed_timepoint(qtbot):
    session = Session()
    session.set_dataset(
        DatasetHandle(path=Path("/tmp/movie.h5"), metadata={"n_timepoints": 2})
    )
    model = CellDataModel(session=session)
    viewer_win = ViewerWindow(model)
    viewer_win._ensure_viewer()
    # Frame 0 has non-contiguous ids {2,5}; frame 1 has {3}.
    labels = np.zeros((2, 8, 8), dtype=np.int32)
    labels[0, 0:2, 0:2] = 2
    labels[0, 4:6, 4:6] = 5
    labels[1, 0:2, 0:2] = 3
    viewer_win.add_labels(labels, name="seg")
    session.set_active_segmentation("seg")
    panel = SegmentationPanel(model, launcher=_FakeLauncher(viewer_win))
    qtbot.addWidget(panel)

    viewer_win.viewer.dims.set_current_step(0, 0)  # display frame 0
    panel._on_relabel_sequential()

    data = viewer_win.viewer.layers["seg"].data
    # Frame 0 compacted to {1,2}; frame 1 untouched ({3}).
    assert set(np.unique(data[0])) == {0, 1, 2}
    assert set(np.unique(data[1])) == {0, 3}
    viewer_win.viewer.close()


def test_relabel_disabled_and_blocked_for_tracked(tracked_harness):
    session, viewer_win, panel = tracked_harness

    # Button is disabled while a tracked segmentation is active.
    assert panel._btn_relabel.isEnabled() is False

    # And the action is a guarded no-op even if invoked directly.
    before = viewer_win.viewer.layers["seg"].data.copy()
    panel._on_relabel_sequential()
    after = viewer_win.viewer.layers["seg"].data
    assert np.array_equal(before, after)


def test_relabel_reenabled_for_untracked(harness):
    # The plain harness uses a launcher with no store -> not tracked.
    session, viewer_win, panel = harness
    panel._sync_relabel_enabled()
    assert panel._btn_relabel.isEnabled() is True


# ── Cleanup: frame-scoped ─────────────────────────────────────


def test_cleanup_apply_scoped_to_displayed_timepoint(qtbot):
    session = Session()
    session.set_dataset(
        DatasetHandle(path=Path("/tmp/movie.h5"), metadata={"n_timepoints": 2})
    )
    model = CellDataModel(session=session)
    viewer_win = ViewerWindow(model)
    viewer_win._ensure_viewer()
    # Each frame: one big interior cell (label 1) + one 1px cell (label 2).
    labels = np.zeros((2, 10, 10), dtype=np.int32)
    for t in range(2):
        labels[t, 3:7, 3:7] = 1   # big interior cell
        labels[t, 8, 8] = 2       # 1px cell
    viewer_win.add_labels(labels, name="seg")
    session.set_active_segmentation("seg")
    panel = SegmentationPanel(model, launcher=_FakeLauncher(viewer_win))
    qtbot.addWidget(panel)

    panel._cleanup_margin.setValue(0)       # only drop cells on the border
    panel._cleanup_min_area.setValue(2)     # removes the 1px cell
    viewer_win.viewer.dims.set_current_step(0, 0)  # display frame 0
    panel._on_cleanup_apply()

    data = viewer_win.viewer.layers["seg"].data
    # Frame 0: 1px cell removed; frame 1 untouched (still has its 1px cell).
    assert data[0, 8, 8] == 0
    assert data[1, 8, 8] != 0
    assert np.any(data[0] > 0)  # big cell survives
    viewer_win.viewer.close()
