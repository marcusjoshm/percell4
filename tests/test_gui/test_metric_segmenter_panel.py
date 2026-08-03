"""Tests for the Segment-by-Metric panel (SEG-U3)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

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

    @property
    def existing_viewer(self):
        """Mirrors ``ViewerWindow.existing_viewer`` — the fake always has one."""
        return self.viewer

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


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("Variance of Laplacian (focus)", "laplacian_variance"),
        ("Tenengrad (focus)", "tenengrad"),
    ],
)
def test_focus_metric_is_offered_and_runs(qtbot, monkeypatch, label, key):
    """Each added focus metric is selectable and opens a preview window."""
    panel, viewer_win, _repo, _model = _build(qtbot, monkeypatch)
    labels = [panel._metric.itemText(i) for i in range(panel._metric.count())]
    assert label in labels

    idx = next(i for i, (_lbl, k) in enumerate(panel._choices) if k == key)
    panel._metric.setCurrentIndex(idx)
    assert panel._selected_metric() == key

    panel._on_segment()
    assert panel._window is not None
    qtbot.addWidget(panel._window)
    names = [getattr(layer, "name", "") for layer in viewer_win.viewer.layers]
    assert any(f"{key} segments (preview)" == n for n in names)


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
    panel._restrict_cb.setChecked(True)   # Action: reads filter_ids, writes nothing
    panel._restrict_cb.setChecked(False)
    panel._on_segment()                # measure + open window
    if panel._window is not None:
        qtbot.addWidget(panel._window)
        panel._window._dividers[0].setValue(0.2)  # drag the threshold
        panel._window._update_preview()

    assert fired == []                 # no ACTIVE_MASK_CHANGED from any Action
    assert model.session.active_mask is None  # active mask untouched until Save


def test_restrict_to_filtered_cells_masks_other_cells(qtbot, monkeypatch):
    """A filter restricts the source mask to just the filtered cells."""
    panel, _viewer_win, _repo, model = _build(qtbot, monkeypatch)
    labels = np.zeros((10, 10), dtype=np.int32)
    labels[0:5, :] = 1
    labels[5:10, :] = 2
    mask = np.ones((10, 10), dtype=np.uint8)

    model.session.set_filter(frozenset({1}))
    out, desc = panel._restrict_to_cells(mask, labels)

    assert out is not None
    assert (out[0:5, :] == 1).all()   # cell 1 kept
    assert (out[5:10, :] == 0).all()  # cell 2 dropped
    assert desc == "1 filtered cell"


def test_restrict_no_filter_uses_whole_segmentation(qtbot, monkeypatch):
    """No filter → every labelled cell is kept; out-of-cell pixels dropped."""
    panel, _viewer_win, _repo, _model = _build(qtbot, monkeypatch)
    labels = np.zeros((10, 10), dtype=np.int32)
    labels[2:8, 2:8] = 1
    mask = np.ones((10, 10), dtype=np.uint8)

    out, desc = panel._restrict_to_cells(mask, labels)

    assert out is not None
    assert (out[labels > 0] == 1).all()
    assert (out[labels == 0] == 0).all()
    assert desc == "the active segmentation"


def test_restrict_empty_returns_none(qtbot, monkeypatch):
    """No pixels survive the restriction → (None, desc) so the caller can bail."""
    panel, _viewer_win, _repo, _model = _build(qtbot, monkeypatch)
    labels = np.zeros((10, 10), dtype=np.int32)  # no cells
    mask = np.ones((10, 10), dtype=np.uint8)

    out, _desc = panel._restrict_to_cells(mask, labels)
    assert out is None


def test_segment_with_restrict_notes_restriction(qtbot, monkeypatch):
    """Checkbox on + filter → window opens on the filtered cell and the status
    reports the restriction."""
    panel, _viewer_win, _repo, model = _build(qtbot, monkeypatch)
    panel._restrict_cb.setChecked(True)
    model.session.set_filter(frozenset({1}))  # the fixture's only cell

    panel._on_segment()

    assert panel._window is not None
    qtbot.addWidget(panel._window)
    assert "restricted to 1 filtered cell" in panel._status.text()


def test_segment_with_restrict_empty_filter_bails(qtbot, monkeypatch):
    """Checkbox on + filter selecting a non-existent cell → no window, clear
    status, and the button is re-enabled."""
    panel, _viewer_win, _repo, model = _build(qtbot, monkeypatch)
    panel._restrict_cb.setChecked(True)
    model.session.set_filter(frozenset({999}))  # no such label

    panel._on_segment()

    assert panel._window is None
    assert "No particles inside" in panel._status.text()
    assert panel._segment_btn.isEnabled() is True


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
