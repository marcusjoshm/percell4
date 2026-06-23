"""Tests for the interactive CNR segmenter window (SEG-U2)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from percell4.gui import cnr_segmenter as seg_module
from percell4.gui.cnr_segmenter import PREVIEW_LAYER_NAME, CnrSegmenterWindow


class _FakeLayers(list):
    """A napari-like layer list supporting name membership + remove."""


class _FakeViewer:
    def __init__(self) -> None:
        self.viewer = SimpleNamespace(layers=_FakeLayers())

    # The window calls viewer_win.add_labels / add_mask (the ViewerWindow API).
    def add_labels(self, data, name, **kwargs):
        # replace same-name layer if present (napari add would collide; the window
        # removes first, but be tolerant)
        self.viewer.layers.append(SimpleNamespace(name=name, data=np.asarray(data)))

    def add_mask(self, data, name, **kwargs):
        self.viewer.layers.append(SimpleNamespace(name=name, data=np.asarray(data)))


class _FakeRepo:
    def __init__(self) -> None:
        self.masks: dict[str, np.ndarray] = {}

    def write_mask(self, handle, name, data, attrs=None):  # noqa: ARG002
        self.masks[name] = data

    def list_masks(self, handle):  # noqa: ARG002
        return sorted(self.masks)


def _comp_and_records():
    """A 20x20 component image with 4 foci of increasing CNR + their records."""
    comp = np.zeros((20, 20), dtype=np.int32)
    comp[1:4, 1:4] = 1
    comp[1:4, 10:13] = 2
    comp[10:13, 1:4] = 3
    comp[10:13, 10:13] = 4
    records = [
        {"label": 1, "cnr": 2.0},
        {"label": 2, "cnr": 6.0},
        {"label": 3, "cnr": 12.0},
        {"label": 4, "cnr": 40.0},
    ]
    return comp, records


def _build(qtbot, monkeypatch):
    from pathlib import Path

    from percell4.domain.dataset import DatasetHandle
    from percell4.model import CellDataModel

    comp, records = _comp_and_records()
    viewer_win = _FakeViewer()
    repo = _FakeRepo()
    store = SimpleNamespace(list_masks=lambda: [])
    model = CellDataModel()
    model.session.set_dataset(DatasetHandle(path=Path("/tmp/t.h5"), metadata={}))
    win = CnrSegmenterWindow(
        records=records,
        component_labels=comp,
        source_mask="adaptive",
        get_viewer_window=lambda: viewer_win,
        get_store=lambda: store,
        get_repo=lambda: repo,
        session=model.session,
    )
    qtbot.addWidget(win)
    return win, viewer_win, repo


def test_init_builds_one_divider_and_preview(qtbot, monkeypatch):
    win, viewer_win, _repo = _build(qtbot, monkeypatch)
    # one initial divider -> two segments
    assert len(win._dividers) == 1
    assert win._n_segments() == 2
    # a preview labels layer was added with a 0/1/2 image
    previews = [layer for layer in viewer_win.viewer.layers if layer.name == PREVIEW_LAYER_NAME]
    assert len(previews) == 1
    assert set(np.unique(previews[0].data)) <= {0, 1, 2}
    assert {1, 2} <= set(np.unique(previews[0].data))


def test_add_and_remove_divider_changes_segments(qtbot, monkeypatch):
    win, viewer_win, _repo = _build(qtbot, monkeypatch)
    win._add_divider()
    assert win._n_segments() == 3
    preview = next(layer for layer in viewer_win.viewer.layers if layer.name == PREVIEW_LAYER_NAME)
    assert 3 in np.unique(preview.data)
    win._remove_divider()
    assert win._n_segments() == 2
    win._remove_divider()  # back to zero dividers -> a single segment
    assert win._n_segments() == 1
    assert win._dividers == []


def test_moving_divider_updates_segment_assignment(qtbot, monkeypatch):
    win, viewer_win, _repo = _build(qtbot, monkeypatch)
    # Move the single divider above all CNRs -> everything falls in segment 1.
    win._dividers[0].setValue(100.0)
    win._update_preview()
    preview = next(layer for layer in viewer_win.viewer.layers if layer.name == PREVIEW_LAYER_NAME)
    assert set(np.unique(preview.data)) <= {0, 1}  # no segment 2 anymore


def test_save_writes_per_segment_masks(qtbot, monkeypatch):
    win, viewer_win, repo = _build(qtbot, monkeypatch)
    # Two dividers -> three segments spanning the four foci (2, 6, 12, 40).
    win._remove_divider()  # clear the initial median divider
    win._add_divider(5.0)
    win._add_divider(20.0)
    assert win._n_segments() == 3
    monkeypatch.setattr(seg_module, "prompt_for_resource_name", lambda *a, **k: "out")

    win._on_save()

    # seg1: cnr 2 ; seg2: cnr 6,12 ; seg3: cnr 40  -> three non-empty masks
    assert "out_seg1" in repo.masks
    assert "out_seg2" in repo.masks
    assert "out_seg3" in repo.masks
    for m in repo.masks.values():
        assert set(np.unique(m)) <= {0, 1}
    # preview removed after save
    assert not any(layer.name == PREVIEW_LAYER_NAME for layer in viewer_win.viewer.layers)


def test_save_skips_empty_segment(qtbot, monkeypatch):
    win, viewer_win, repo = _build(qtbot, monkeypatch)
    # Place both dividers below every focus -> all foci in segment 3; segs 1 & 2 empty.
    win._remove_divider()
    win._add_divider(0.5)
    win._add_divider(1.0)
    monkeypatch.setattr(seg_module, "prompt_for_resource_name", lambda *a, **k: "out")

    win._on_save()

    assert "out_seg3" in repo.masks      # the only non-empty segment
    assert "out_seg1" not in repo.masks
    assert "out_seg2" not in repo.masks


def test_close_removes_preview_layer(qtbot, monkeypatch):
    win, viewer_win, _repo = _build(qtbot, monkeypatch)
    assert any(layer.name == PREVIEW_LAYER_NAME for layer in viewer_win.viewer.layers)
    win.close()
    assert not any(layer.name == PREVIEW_LAYER_NAME for layer in viewer_win.viewer.layers)
