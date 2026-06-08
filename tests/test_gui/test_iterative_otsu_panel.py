"""Tests for IterativeOtsuPanel + run_iterative_otsu worker body (U5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from percell4.gui import iterative_otsu_panel as panel_module
from percell4.gui.iterative_otsu_panel import run_iterative_otsu
from percell4.workflows.models import IterativeOtsuSettings

# ── synchronous fake Worker so the Creator chain runs deterministically ──


class _Sig:
    def __init__(self) -> None:
        self._cbs: list = []

    def connect(self, cb) -> None:
        self._cbs.append(cb)

    def emit(self, *a) -> None:
        for cb in list(self._cbs):
            cb(*a)


class FakeWorker:
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


class FakeRepo:
    def __init__(self) -> None:
        self.masks: dict[str, np.ndarray] = {}

    def write_mask(self, handle, name, data, attrs=None):  # noqa: ARG002
        self.masks[name] = data

    def list_masks(self, handle):  # noqa: ARG002
        return sorted(self.masks.keys())


def _image_and_labels(shape=(80, 80)):
    img = np.full(shape, 10.0, dtype=np.float32)
    img[20:30, 20:30] = 200.0  # one bright focus
    img[55:60, 55:60] = 200.0  # another bright focus
    labels = np.ones(shape, dtype=np.int32)  # whole field is one cell
    return img, labels


# ── worker body (pure, no Qt) ───────────────────────────────────────────


def test_run_iterative_otsu_per_cell_returns_mask_and_report():
    img, labels = _image_and_labels()
    settings = IterativeOtsuSettings(scope="per-cell", stop_criteria=("min-positive",))
    mask, report = run_iterative_otsu(img, labels, 0.0, settings)
    assert mask.dtype == np.uint8
    assert set(np.unique(mask).tolist()) <= {0, 1}
    assert mask.sum() > 0
    assert report.n_positive == int(mask.sum())


def test_run_iterative_otsu_empty_labels_returns_empty():
    img, _ = _image_and_labels()
    labels = np.zeros(img.shape, dtype=np.int32)  # no cells
    settings = IterativeOtsuSettings(scope="per-cell")
    mask, report = run_iterative_otsu(img, labels, 0.0, settings)
    assert mask.sum() == 0
    assert report.n_positive == 0


# ── panel (Creator) ─────────────────────────────────────────────────────


def _build(qtbot, monkeypatch, *, channel="GFP", segmentation="cellpose", labels_present=True):
    from percell4.domain.dataset import DatasetHandle
    from percell4.gui.iterative_otsu_panel import IterativeOtsuPanel
    from percell4.model import CellDataModel

    img, labels = _image_and_labels()

    model = CellDataModel()
    model.session.set_dataset(DatasetHandle(path=Path("/tmp/t.h5"), metadata={}))
    if channel:
        model.session.set_active_channel(channel)
    if segmentation:
        model.session.set_active_segmentation(segmentation)

    repo = FakeRepo()
    store = MagicMock()
    store.list_masks.return_value = []
    store.list_labels.return_value = ["cellpose"] if labels_present else []
    store.read_labels.return_value = labels

    layer = MagicMock()
    layer.__class__ = type("Image", (MagicMock,), {})
    layer.__class__.__name__ = "Image"
    layer.name = channel
    layer.data = img
    viewer = MagicMock()
    viewer.layers = [layer] if channel else []
    viewer_win = MagicMock()
    viewer_win.viewer = viewer
    viewer_win.add_mask = MagicMock()

    monkeypatch.setattr("percell4.gui.workers.Worker", FakeWorker)

    panel = IterativeOtsuPanel(
        model,
        get_repo=lambda: repo,
        get_store=lambda: store,
        get_viewer_window=lambda: viewer_win,
        show_status=lambda _m: None,
    )
    qtbot.addWidget(panel)
    return panel, model, repo, viewer_win


def test_run_creates_and_selects_mask(qtbot, monkeypatch):
    panel, model, repo, viewer_win = _build(qtbot, monkeypatch)
    # Use a non-premature criterion so the small fixture reliably captures.
    panel._settings._rows["min-positive"][0].setChecked(True)
    for crit in ("bg-floor", "positive-fraction-high"):
        panel._settings._rows[crit][0].setChecked(False)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "iter1")

    panel._on_run()

    assert "iter1" in repo.masks
    stored = repo.masks["iter1"]
    assert stored.dtype == np.uint8
    assert set(np.unique(stored)).issubset({0, 1})
    assert int(stored.sum()) > 0
    viewer_win.add_mask.assert_called_once()
    assert model.session.active_mask == "iter1"


def test_no_active_channel_aborts(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch, channel="")
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    panel._on_run()
    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()


def test_no_segmentation_exists_aborts(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch, labels_present=False)
    called = []
    monkeypatch.setattr(
        panel_module, "prompt_for_resource_name", lambda *a, **kw: called.append(1) or "m"
    )
    panel._on_run()
    assert called == []  # aborted before the name prompt
    assert repo.masks == {}


def test_no_active_segmentation_aborts(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch, segmentation="")
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    panel._on_run()
    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()


def test_no_stop_criteria_aborts(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch)
    for cb, _, _ in panel._settings._rows.values():
        cb.setChecked(False)
    called = []
    monkeypatch.setattr(
        panel_module, "prompt_for_resource_name", lambda *a, **kw: called.append(1) or "m"
    )
    panel._on_run()
    assert called == []  # aborted before the name prompt
    assert repo.masks == {}


def test_cancel_prompt_writes_nothing(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: None)
    panel._on_run()
    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()
