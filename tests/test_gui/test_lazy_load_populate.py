"""Tests for the lazy-load controller (U3).

Exercises the controller against real .h5 files with a fake viewer window and a
fake background filler, so no QApplication / napari / real threads are needed
(mirrors the worker-monkeypatch pattern used elsewhere in the GUI tests).
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.store import DatasetStore


def _make_timelapse(path, n_t=4, n_c=2, h=16, w=16, *, with_labels=True):
    rng = np.random.default_rng(0)
    intensity = (rng.random((n_t, n_c, h, w)) * 100).astype(np.float32)
    store = DatasetStore(path)
    store.create(metadata={})
    store.write_array("intensity", intensity, attrs={"dims": ["T", "C", "H", "W"]})
    store.set_metadata({"channel_names": ["A", "B"], "n_timepoints": n_t})
    if with_labels:
        labels = np.zeros((n_t, h, w), dtype=np.int32)
        for t in range(n_t):
            labels[t, : t + 2, : t + 2] = t + 1
        store.write_labels("cellpose", labels)
    return intensity


class _FakeSignal:
    def __init__(self):
        self._cbs = []

    def connect(self, cb):
        self._cbs.append(cb)

    def emit(self, *args):
        for cb in list(self._cbs):
            cb(*args)


class _FakeFiller:
    instances: list = []

    def __init__(self, buffer, path, frames):
        self.buffer = buffer
        self.path = path
        self.frames = list(frames)
        self.frame_filled = _FakeSignal()
        self.fill_finished = _FakeSignal()
        self.error = _FakeSignal()
        self.started = False
        self.aborted = False
        _FakeFiller.instances.append(self)

    def start(self):
        self.started = True

    def request_abort(self):
        self.aborted = True

    def wait(self, *a):
        return True


class _FakeViewerWin:
    def __init__(self):
        self.images = {}
        self.labels = {}
        self.masks = {}
        self.cleared = 0
        self.refreshed = 0
        self._viewer = None  # no dims emitter -> _connect_dims no-ops
        self._step = 0

    def clear(self):
        self.cleared += 1

    def add_image(self, data, name, **kw):
        self.images[name] = (data, kw)

    def add_labels(self, data, name, **kw):
        self.labels[name] = (data, kw)

    def add_mask(self, data, name, **kw):
        self.masks[name] = (data, kw)

    def refresh_all_layers(self):
        self.refreshed += 1

    def current_timepoint(self):
        return self._step


@pytest.fixture
def controller(monkeypatch):
    import percell4.gui.lazy_load as mod

    _FakeFiller.instances = []
    monkeypatch.setattr(mod, "BackgroundFrameFiller", _FakeFiller)
    return mod.LazyLoadController()


def test_load_shows_frame0_and_starts_background(controller, tmp_h5):
    intensity = _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    vw = _FakeViewerWin()

    controller.load(store, vw, view_bin=1)

    # Layers added from the resident buffers (full (T,H,W) shape).
    assert set(vw.images) == {"A", "B"}
    assert set(vw.labels) == {"cellpose"}
    assert vw.images["A"][0].shape == (4, 16, 16)
    # Explicit contrast_limits passed (no full-stack scan).
    assert "contrast_limits" in vw.images["A"][1]

    # Frame 0 is filled and correct; the rest are pending.
    buf = controller.buffer
    assert buf.is_ready(0)
    assert buf.pending_frames() == [1, 2, 3]
    np.testing.assert_array_equal(buf.arrays["A"][0], intensity[0, 0])

    # Background filler started for the remaining frames.
    assert len(_FakeFiller.instances) == 1
    filler = _FakeFiller.instances[0]
    assert filler.started and filler.frames == [1, 2, 3]


def test_contrast_from_frame0_not_zero_buffer(controller, tmp_h5):
    intensity = _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    vw = _FakeViewerWin()
    controller.load(store, vw, view_bin=1)
    lo, hi = vw.images["A"][1]["contrast_limits"]
    assert lo == float(intensity[0, 0].min())
    assert hi == float(intensity[0, 0].max())


def test_non_timelapse_is_eager_no_filler(controller, tmp_h5):
    store = DatasetStore(tmp_h5)
    store.create(metadata={"channel_names": ["GFP", "DAPI"]})
    img = (np.random.default_rng(1).random((32, 32)) * 10).astype(np.float32)
    store.write_array("intensity", np.stack([img, img * 0.5]))
    store.write_labels("cellpose", (img > 5).astype(np.int32))
    vw = _FakeViewerWin()

    controller.load(store, vw, view_bin=1)

    assert set(vw.images) == {"GFP", "DAPI"}
    assert "cellpose" in vw.labels
    assert controller.buffer is None
    assert _FakeFiller.instances == []


def test_on_demand_fill_on_scrub(controller, tmp_h5):
    intensity = _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    vw = _FakeViewerWin()
    controller.load(store, vw, view_bin=1)

    # User scrubs to frame 2 before the background reaches it.
    assert not controller.buffer.is_ready(2)
    vw._step = 2
    controller.ensure_frame_ready(2)

    assert controller.buffer.is_ready(2)
    np.testing.assert_array_equal(controller.buffer.arrays["A"][2], intensity[2, 0])
    assert vw.refreshed >= 1


def test_frame_filled_refreshes_only_current(controller, tmp_h5):
    _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    vw = _FakeViewerWin()
    controller.load(store, vw, view_bin=1)
    filler = _FakeFiller.instances[0]

    vw.refreshed = 0
    vw._step = 0
    filler.frame_filled.emit(3)  # not the current frame -> no repaint
    assert vw.refreshed == 0

    vw._step = 3
    filler.frame_filled.emit(3)  # now current -> repaint
    assert vw.refreshed == 1


def test_teardown_aborts_and_frees(controller, tmp_h5):
    _make_timelapse(tmp_h5)
    store = DatasetStore(tmp_h5)
    vw = _FakeViewerWin()
    controller.load(store, vw, view_bin=1)
    filler = _FakeFiller.instances[0]

    controller.teardown()

    assert filler.aborted
    assert controller.buffer is None


def test_load_twice_tears_down_previous(controller, tmp_h5, tmp_path):
    _make_timelapse(tmp_h5)
    store1 = DatasetStore(tmp_h5)
    vw = _FakeViewerWin()
    controller.load(store1, vw, view_bin=1)
    first = _FakeFiller.instances[0]

    second_path = tmp_path / "second.h5"
    _make_timelapse(second_path)
    store2 = DatasetStore(second_path)
    controller.load(store2, vw, view_bin=1)

    assert first.aborted  # previous filler stopped
    assert len(_FakeFiller.instances) == 2
    assert vw.cleared == 2  # viewer cleared on each load


def test_no_intensity_raises_keyerror(controller, tmp_h5):
    store = DatasetStore(tmp_h5)
    store.create(metadata={})  # no /intensity
    vw = _FakeViewerWin()
    with pytest.raises(KeyError):
        controller.load(store, vw, view_bin=1)
