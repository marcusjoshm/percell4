"""Interactive ops force lazy frames resident before reading layer data (U4).

Covers the controller's whole-stack fill and the ViewerWindow delegation that
_get_active_seg_labels / the Cellpose run use to avoid reading not-yet-streamed
(zero) frames.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.store import DatasetStore


def _make_timelapse(path, n_t=4, n_c=2, h=16, w=16):
    rng = np.random.default_rng(0)
    intensity = (rng.random((n_t, n_c, h, w)) * 100).astype(np.float32)
    store = DatasetStore(path)
    store.create(metadata={})
    store.write_array("intensity", intensity, attrs={"dims": ["T", "C", "H", "W"]})
    store.set_metadata({"channel_names": ["A", "B"], "n_timepoints": n_t})
    labels = np.zeros((n_t, h, w), dtype=np.int32)
    for t in range(n_t):
        labels[t, : t + 2, : t + 2] = t + 1
    store.write_labels("cellpose", labels)
    return intensity


class _NoopFiller:
    """Stand-in that never actually fills, so on-demand paths are exercised."""

    def __init__(self, buffer, path, frames):
        self.frame_filled = _Sig()
        self.fill_finished = _Sig()
        self.error = _Sig()

    def start(self):
        pass

    def request_abort(self):
        pass

    def wait(self, *a):
        return True


class _Sig:
    def connect(self, *a):
        pass


class _FakeViewerWin:
    def __init__(self):
        self.images = {}
        self.labels = {}
        self.masks = {}
        self._viewer = None
        self._step = 0
        self.refreshed = 0

    def clear(self):
        pass

    def add_image(self, data, name, **kw):
        self.images[name] = data

    def add_labels(self, data, name, **kw):
        self.labels[name] = data

    def add_mask(self, data, name, **kw):
        self.masks[name] = data

    def refresh_all_layers(self):
        self.refreshed += 1

    def current_timepoint(self):
        return self._step


@pytest.fixture
def controller(monkeypatch):
    import percell4.gui.lazy_load as mod

    monkeypatch.setattr(mod, "BackgroundFrameFiller", _NoopFiller)
    return mod.LazyLoadController()


def test_ensure_all_ready_fills_every_frame(controller, tmp_h5):
    intensity = _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    vw = _FakeViewerWin()
    controller.load(store, vw, view_bin=1)

    # Only frame 0 is resident (the no-op filler never ran).
    assert controller.buffer.pending_frames() == [1, 2, 3]

    controller.ensure_all_ready()

    assert controller.buffer.pending_frames() == []
    # Whole (T,H,W) stack now matches the eager per-channel arrays.
    np.testing.assert_array_equal(controller.buffer.arrays["A"], intensity[:, 0])
    np.testing.assert_array_equal(controller.buffer.arrays["B"], intensity[:, 1])


def test_ensure_frame_ready_single_frame(controller, tmp_h5):
    intensity = _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    vw = _FakeViewerWin()
    controller.load(store, vw, view_bin=1)

    controller.ensure_frame_ready(3)

    assert controller.buffer.is_ready(3)
    assert controller.buffer.is_ready(0)
    # Frames not requested stay pending.
    assert not controller.buffer.is_ready(1)
    np.testing.assert_array_equal(
        controller.buffer.arrays["cellpose"][3], _label_frame(intensity, 3)
    )


def _label_frame(intensity, t):
    n_t, _, h, w = intensity.shape
    frame = np.zeros((h, w), dtype=np.int32)
    frame[: t + 2, : t + 2] = t + 1
    return frame


def test_viewer_delegates_ensure_to_controller(qtbot):
    from unittest.mock import MagicMock

    from percell4.application.session import Session
    from percell4.gui.viewer import ViewerWindow
    from percell4.model import CellDataModel

    viewer_win = ViewerWindow(CellDataModel(session=Session()))
    fake = MagicMock()
    viewer_win.set_lazy_controller(fake)

    viewer_win.ensure_timepoint_ready(2)
    fake.ensure_frame_ready.assert_called_once_with(2)

    viewer_win.ensure_all_timepoints_ready()
    fake.ensure_all_ready.assert_called_once_with()


def test_viewer_ensure_is_noop_without_controller(qtbot):
    from percell4.application.session import Session
    from percell4.gui.viewer import ViewerWindow
    from percell4.model import CellDataModel

    viewer_win = ViewerWindow(CellDataModel(session=Session()))
    # No controller attached (eager dataset) — must not raise.
    viewer_win.ensure_timepoint_ready(1)
    viewer_win.ensure_all_timepoints_ready()
