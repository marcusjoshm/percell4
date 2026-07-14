"""Tests for the Segment-by-Metric panel (SEG-U3)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np

from percell4.gui import metric_segmenter_panel as panel_module


class _Sig:
    def __init__(self) -> None:
        self._cbs: list = []

    def connect(self, cb) -> None:
        self._cbs.append(cb)

    def emit(self, *a) -> None:
        for cb in list(self._cbs):
            cb(*a)


class FakeWorker:
    """Synchronous worker so the measure→open chain runs deterministically."""

    def __init__(self, fn, *args, **kwargs):
        self._fn, self._args, self._kwargs = fn, args, kwargs
        self.finished = _Sig()
        self.error = _Sig()

    def start(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as e:  # noqa: BLE001
            err = type("E", (), {"exc_type": type(e).__name__, "message": str(e)})()
            self.error.emit(err)
            return
        self.finished.emit(result)


class Image:  # noqa: D101 — class NAME must be "Image" (_find_layer_data matches on it)
    def __init__(self, name, data):
        self.name = name
        self.data = np.asarray(data)


class Labels:  # noqa: D101 — class NAME must be "Labels"
    def __init__(self, name, data):
        self.name = name
        self.data = np.asarray(data)


class _FakeViewer:
    def __init__(self, layers):
        self.viewer = SimpleNamespace(layers=layers)

    def add_labels(self, data, name, **kwargs):
        self.viewer.layers.append(SimpleNamespace(name=name, data=np.asarray(data)))

    def add_mask(self, data, name, **kwargs):
        self.viewer.layers.append(SimpleNamespace(name=name, data=np.asarray(data)))


class _FakeRepo:
    def __init__(self):
        self.masks: dict[str, np.ndarray] = {}

    def write_mask(self, handle, name, data, attrs=None):  # noqa: ARG002
        self.masks[name] = data

    def list_masks(self, handle):  # noqa: ARG002
        return sorted(self.masks)


def _fixture():
    """One cell, one bright particle; channel 'C0', segmentation 'cells', mask 'pbody'."""
    img = np.full((30, 30), 8.0, dtype=np.float32)
    img[13:16, 13:16] = 200.0
    labels = np.zeros((30, 30), dtype=np.int32)
    labels[2:28, 2:28] = 1
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[13:16, 13:16] = 1
    return img, labels, mask


def _build(qtbot, monkeypatch, *, mask="pbody"):
    from percell4.domain.dataset import DatasetHandle
    from percell4.model import CellDataModel

    img, labels, mask_arr = _fixture()
    layers = [Image("C0", img), Labels("cells", labels)]
    viewer_win = _FakeViewer(layers)
    store = SimpleNamespace(
        list_masks=lambda: [mask],
        read_mask=lambda name: mask_arr,
        metadata={"pixel_size_um": 0.12, "n_timepoints": 1},
    )
    repo = _FakeRepo()
    model = CellDataModel()
    model.session.set_dataset(DatasetHandle(path=Path("/tmp/t.h5"), metadata={}))
    model.session.set_active_channel("C0")
    model.session.set_active_segmentation("cells")

    # Worker is imported inside _on_segment from percell4.gui.workers; patch there.
    import percell4.gui.workers as workers_mod

    monkeypatch.setattr(workers_mod, "Worker", FakeWorker)

    panel = panel_module.MetricSegmenterPanel(
        model,
        get_repo=lambda: repo,
        get_store=lambda: store,
        get_viewer_window=lambda: viewer_win,
    )
    qtbot.addWidget(panel)
    panel._refresh_masks()
    return panel, viewer_win, repo, model


def test_run_metric_measure_worker_is_pure_and_returns_quad(qtbot):
    img, labels, mask = _fixture()
    result = panel_module.run_metric_measure(img, mask, labels, "edge_skirt_ratio", 0.12)
    assert len(result) == 4
    records, comp, excluded, metric = result
    assert metric == "edge_skirt_ratio"
    assert len(records) == 1 and np.isfinite(records[0]["value"])
    assert comp.max() == 1


def test_segment_opens_window(qtbot, monkeypatch):
    panel, viewer_win, _repo, _model = _build(qtbot, monkeypatch)
    panel._on_segment()
    assert panel._window is not None
    qtbot.addWidget(panel._window)
    # a preview layer for the edge_skirt metric was pushed
    names = [getattr(layer, "name", "") for layer in viewer_win.viewer.layers]
    assert any("edge_skirt_ratio segments (preview)" == n for n in names)


def test_metric_combo_change_is_not_silent(qtbot, monkeypatch):
    panel, _viewer_win, _repo, _model = _build(qtbot, monkeypatch)
    panel._metric.setCurrentIndex(1)  # -> "Area / size"
    assert "Metric set to" in panel._status.text()


def test_channel_guard_warns_when_all_nan(qtbot, monkeypatch):
    """A source mask measured on a signal-free channel: edge-skirt is all NaN, so
    no window opens and the status explains the likely channel mismatch (R9)."""
    from percell4.domain.dataset import DatasetHandle
    from percell4.model import CellDataModel

    _img, labels, mask_arr = _fixture()
    dark = np.zeros((30, 30), dtype=np.float32)  # no signal on the active channel
    layers = [Image("C0", dark), Labels("cells", labels)]
    viewer_win = _FakeViewer(layers)
    store = SimpleNamespace(
        list_masks=lambda: ["pbody"],
        read_mask=lambda name: mask_arr,
        metadata={"pixel_size_um": 0.12, "n_timepoints": 1},
    )
    model = CellDataModel()
    model.session.set_dataset(DatasetHandle(path=Path("/tmp/t.h5"), metadata={}))
    model.session.set_active_channel("C0")
    model.session.set_active_segmentation("cells")
    import percell4.gui.workers as workers_mod

    monkeypatch.setattr(workers_mod, "Worker", FakeWorker)

    panel = panel_module.MetricSegmenterPanel(
        model, get_repo=lambda: _FakeRepo(), get_store=lambda: store,
        get_viewer_window=lambda: viewer_win,
    )
    qtbot.addWidget(panel)
    panel._refresh_masks()
    panel._on_segment()  # edge_skirt_ratio on the dark channel

    assert panel._window is None
    assert "NaN for every particle" in panel._status.text()


def test_no_session_write_from_non_save_widgets(qtbot, monkeypatch):
    """R6 guard: exercising the metric combo, source combo, the Segment button
    (measure + open window), and dragging the divider writes NO session field —
    only the window's Save is a Creator."""
    from percell4.application.session import Event

    panel, _viewer_win, _repo, model = _build(qtbot, monkeypatch)
    fired: list = []
    model.session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: fired.append(1))

    panel._metric.setCurrentIndex(1)   # Area
    panel._metric.setCurrentIndex(0)   # back to edge-skirt
    panel._source.setCurrentIndex(0)
    panel._on_segment()                # measure + open window
    if panel._window is not None:
        qtbot.addWidget(panel._window)
        panel._window._dividers[0].setValue(0.2)  # drag the threshold
        panel._window._update_preview()

    assert fired == []                 # no ACTIVE_MASK_CHANGED from any Action
    assert model.session.active_mask is None  # active mask untouched until Save


def test_resolve_requires_channel(qtbot, monkeypatch):
    from percell4.domain.dataset import DatasetHandle
    from percell4.model import CellDataModel

    model = CellDataModel()
    model.session.set_dataset(DatasetHandle(path=Path("/tmp/t.h5"), metadata={}))
    viewer_win = _FakeViewer([])
    panel = panel_module.MetricSegmenterPanel(
        model, get_store=lambda: SimpleNamespace(list_masks=lambda: []),
        get_viewer_window=lambda: viewer_win,
    )
    qtbot.addWidget(panel)
    panel._on_segment()
    assert panel._window is None
    assert "channel" in panel._status.text().lower()
