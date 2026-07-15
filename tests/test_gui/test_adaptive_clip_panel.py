"""Tests for AdaptiveClipPanel (auto-extraction-only + CNR tools)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from skimage.draw import disk

from percell4.gui import adaptive_clip_panel as panel_module

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


def _blob_image(shape=(120, 120), radius=6) -> np.ndarray:
    # Deterministic faint noise so a robust (MAD) noise scale is well-defined —
    # the default estimator is MAD, and a perfectly flat background gives MAD=0
    # (no scale, empty mask), which real microscopy data never does.
    rng = np.random.RandomState(0)
    img = (10.0 + rng.normal(0.0, 1.0, shape)).astype(np.float32)
    for c in [(30, 30), (30, 90), (90, 60)]:
        rr, cc = disk(c, radius, shape=shape)
        img[rr, cc] = 200.0
    return img


def _labels_one_cell(shape=(120, 120)) -> np.ndarray:
    # One cell covering the frame so the blobs fall inside it (per-cell σ).
    return np.ones(shape, dtype=np.int32)


def _build(
    qtbot,
    monkeypatch,
    *,
    channel="mNG",
    pixel_size_um=None,
    existing=None,
    with_channel=True,
    segmentation=None,
    labels=None,
):
    from percell4.domain.dataset import DatasetHandle
    from percell4.gui.adaptive_clip_panel import AdaptiveClipPanel
    from percell4.model import CellDataModel

    model = CellDataModel()
    model.session.set_dataset(DatasetHandle(path=Path("/tmp/t.h5"), metadata={}))
    if channel:
        model.session.set_active_channel(channel)
    if segmentation:
        model.session.set_active_segmentation(segmentation)

    repo = FakeRepo()
    store = MagicMock()
    store.list_masks.return_value = list(existing or [])
    store.metadata = {} if pixel_size_um is None else {"pixel_size_um": pixel_size_um}

    layers = []
    if with_channel and channel:
        layer = MagicMock()
        layer.__class__ = type("Image", (MagicMock,), {})
        layer.__class__.__name__ = "Image"
        layer.name = channel
        layer.data = _blob_image()
        layers.append(layer)
    if segmentation:
        lab_layer = MagicMock()
        lab_layer.__class__ = type("Labels", (MagicMock,), {})
        lab_layer.__class__.__name__ = "Labels"
        lab_layer.name = segmentation
        lab_layer.data = _labels_one_cell() if labels is None else labels
        layers.append(lab_layer)
    viewer = MagicMock()
    viewer.layers = layers
    viewer_win = MagicMock()
    viewer_win.viewer = viewer
    viewer_win.add_mask = MagicMock()

    monkeypatch.setattr("percell4.gui.workers.Worker", FakeWorker)

    panel = AdaptiveClipPanel(
        model,
        get_repo=lambda: repo,
        get_store=lambda: store,
        get_viewer_window=lambda: viewer_win,
        show_status=lambda _m: None,
    )
    qtbot.addWidget(panel)
    return panel, model, repo, viewer_win


# ── error / edge paths (pre-flight) ──────────────────────────────────────

def test_um2_without_pixel_size_aborts(qtbot, monkeypatch):
    """A µm² min-size filter with no calibration aborts before the name prompt."""
    panel, _model, repo, viewer_win = _build(
        qtbot, monkeypatch, pixel_size_um=None, segmentation="cells"
    )
    panel._settings._unit.setCurrentText("µm²")
    called = []
    monkeypatch.setattr(
        panel_module, "prompt_for_resource_name", lambda *a, **kw: called.append(1) or "m"
    )

    panel._on_run()

    assert called == []  # aborted before the name prompt
    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()


def test_no_active_channel_aborts(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch, channel="")
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    panel._on_run()
    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()


def test_channel_layer_missing_aborts(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch, with_channel=False)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    panel._on_run()
    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()


def test_cancel_prompt_writes_nothing(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch, segmentation="cells")
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: None)
    panel._on_run()
    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()


# ── terminal debug output ───────────────────────────────────────────────


def test_run_prints_all_settings_to_terminal(qtbot, monkeypatch, capsys):
    """Every run dumps the auto-extraction settings block to stdout."""
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells")
    panel._settings._sigma.setValue(2.5)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    panel._on_run()
    out = capsys.readouterr().out
    assert "Adaptive Local Clipping run" in out
    for field in (
        "gaussian_sigma",
        "smallest particle",
        "auto-detect smallest",
        "min particle size",
    ):
        assert field in out
    assert "2.5" in out  # the gaussian σ value flows into the dump


# ── U4: run_cnr_classification pure worker body ──────────────────────────────


def _cnr_foci(levels, *, shape=(360, 360), noise_std=5.0, baseline=100.0, seed=0,
              one_outside=False):
    """(image, mask, labels): one radius-3 focus per level in a whole-frame cell."""
    rng = np.random.RandomState(seed)
    img = rng.normal(baseline, noise_std, shape).astype(np.float32)
    labels = np.ones(shape, dtype=np.int32)
    mask = np.zeros(shape, dtype=np.uint8)
    n_side = int(round(len(levels) ** 0.5))
    margin = 30
    step = (shape[0] - 2 * margin) // (n_side - 1)
    centers = [(margin + i * step, margin + j * step)
               for i in range(n_side) for j in range(n_side)][: len(levels)]
    for k, ((cy, cx), lvl) in enumerate(zip(centers, levels)):
        rr, cc = disk((cy, cx), 3, shape=shape)
        img[rr, cc] = baseline + float(lvl)
        mask[rr, cc] = 1
        if one_outside and k == len(levels) - 1:
            labels[rr, cc] = 0
    return img, mask, labels


def test_run_cnr_classification_discover_two_populations():
    """A CNR gap -> two non-empty population masks (_low/_high) + report."""
    levels = np.concatenate([np.full(32, 30.0), np.full(32, 400.0)])
    img, mask, labels = _cnr_foci(levels)
    pop_masks, components, report = panel_module.run_cnr_classification(
        img, mask, labels, mode="discover", threshold=None
    )
    suffixes = [s for s, _ in pop_masks]
    assert suffixes == ["_low", "_high"]
    for _, m in pop_masks:
        assert m.dtype == np.uint8 and set(np.unique(m)) <= {0, 1} and m.sum() > 0
    assert report["dip_cnr"]["bimodal"] is True
    assert any(c.get("subpopulation") in (1, 2) for c in components)


def test_run_cnr_classification_discover_single_population():
    """A continuum -> exactly one mask under the base name (empty suffix)."""
    rng = np.random.RandomState(3)
    levels = np.clip(rng.normal(150.0, 45.0, 64), 20.0, None)
    img, mask, labels = _cnr_foci(levels, seed=3)
    pop_masks, _, report = panel_module.run_cnr_classification(
        img, mask, labels, mode="discover", threshold=None
    )
    assert [s for s, _ in pop_masks] == [""]
    assert pop_masks[0][1].sum() > 0


def test_run_cnr_classification_guided_splits_at_threshold():
    """Guided mode splits a continuum into two masks at the supplied threshold."""
    rng = np.random.RandomState(5)
    levels = np.clip(rng.normal(150.0, 45.0, 64), 20.0, None)
    img, mask, labels = _cnr_foci(levels, seed=5)
    # discover first to get a sensible candidate threshold
    _, _, base = panel_module.run_cnr_classification(
        img, mask, labels, mode="discover", threshold=None
    )
    thr = base["candidate_cnr_threshold"]
    pop_masks, _, report = panel_module.run_cnr_classification(
        img, mask, labels, mode="guided", threshold=thr
    )
    assert [s for s, _ in pop_masks] == ["_low", "_high"]
    assert report["mode"].startswith("guided")


def test_run_cnr_classification_forced_warns_on_continuum():
    """Forced mode splits a continuum and flags low confidence."""
    rng = np.random.RandomState(8)
    levels = np.clip(rng.normal(150.0, 45.0, 64), 20.0, None)
    img, mask, labels = _cnr_foci(levels, seed=8)
    pop_masks, _, report = panel_module.run_cnr_classification(
        img, mask, labels, mode="forced", threshold=None
    )
    assert [s for s, _ in pop_masks] == ["_low", "_high"]
    assert any("low confidence" in w for w in report["warnings"])


def test_run_cnr_classification_empty_mask_returns_no_masks():
    """An empty feature mask -> no population masks, no exception."""
    img = np.random.RandomState(0).normal(100.0, 5.0, (120, 120)).astype(np.float32)
    mask = np.zeros((120, 120), dtype=np.uint8)
    labels = np.ones((120, 120), dtype=np.int32)
    pop_masks, components, report = panel_module.run_cnr_classification(
        img, mask, labels, mode="discover", threshold=None
    )
    assert pop_masks == []
    assert components == []


# ── U6/U7: panel classify wiring, pre-flight, Creator save ───────────────────


def _select_source_mask(panel, name):
    """Populate the CNR source combo from the store and pick ``name``."""
    panel._refresh_cnr_masks()
    panel._cnr_settings._source.setCurrentText(name)


def test_classify_without_segmentation_aborts(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, existing=["adaptive"])  # no segmentation
    _select_source_mask(panel, "adaptive")
    panel._on_classify()
    assert panel._cnr_worker is None  # never dispatched


def test_classify_without_source_mask_aborts(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells")  # no masks exist
    panel._on_classify()
    assert panel._cnr_worker is None


def test_classify_timelapse_dispatches_stack_and_saves_THW(qtbot, monkeypatch):
    """A (T,H,W) channel classifies per frame (the stack worker) and saves (T,H,W)
    population masks — no longer refused."""
    tl_lab = np.stack([_labels_one_cell(), _labels_one_cell()], axis=0)
    panel, model, repo, viewer_win = _build(
        qtbot, monkeypatch, segmentation="cells", existing=["adaptive"], labels=tl_lab
    )
    store = panel._get_store()
    store.metadata = {"n_timepoints": 2}
    viewer_win.viewer.layers[0].data = np.stack([_blob_image(), _blob_image()], axis=0)
    store.read_mask.return_value = np.ones((2, 120, 120), dtype=np.uint8)
    _select_source_mask(panel, "adaptive")
    panel._cnr_settings._mode.setCurrentText("Guided (CNR threshold)")
    panel._cnr_settings._threshold.setValue(5.0)

    low = np.zeros((2, 120, 120), dtype=np.uint8)
    low[:, 10:20, 10:20] = 1
    high = np.zeros((2, 120, 120), dtype=np.uint8)
    high[:, 30:40, 30:40] = 1
    comps = [{"label": 1, "cnr": 3.0, "subpopulation": 1, "timepoint": 0}]
    report = {"decision": "time-lapse CNR: 2/2 split", "warnings": []}
    captured = {}

    def _stub(image, feature_mask, labels, *, mode, threshold):
        captured["shape"] = np.asarray(image).shape
        return [("_low", low), ("_high", high)], comps, report

    monkeypatch.setattr(panel_module, "run_cnr_classification_stack", _stub)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "out")

    panel._on_classify()

    assert panel._cnr_worker._fn is panel_module.run_cnr_classification_stack
    assert captured["shape"] == (2, 120, 120)  # the (T,H,W) channel reached the worker
    assert "out_low" in repo.masks and "out_high" in repo.masks
    assert repo.masks["out_low"].shape == (2, 120, 120)
    paths = [c.args[0] for c in store.write_dataframe.call_args_list]
    assert "/classification/out" in paths


def test_segment_cnr_timelapse_pools_and_opens_window(qtbot, monkeypatch):
    """A (T,H,W) channel pools foci across frames (run_cnr_measure_stack) and opens the
    segmenter with a (T,H,W) component image — no longer refused."""
    tl_lab = np.stack([_labels_one_cell(), _labels_one_cell()], axis=0)
    panel, _model, _repo, viewer_win = _build(
        qtbot, monkeypatch, segmentation="cells", existing=["adaptive"], labels=tl_lab
    )
    store = panel._get_store()
    store.metadata = {"n_timepoints": 2}
    viewer_win.viewer.layers[0].data = np.stack([_blob_image(), _blob_image()], axis=0)
    store.read_mask.return_value = np.ones((2, 120, 120), dtype=np.uint8)
    _select_source_mask(panel, "adaptive")

    comp = np.zeros((2, 120, 120), dtype=np.int32)
    comp[0, 10:20, 10:20] = 1
    comp[1, 30:40, 30:40] = 2  # globally-unique ids across frames
    records = [
        {"label": 1, "cnr": 3.0, "timepoint": 0},
        {"label": 2, "cnr": 30.0, "timepoint": 1},
    ]
    captured = {}

    def _stub(image, feature_mask, labels):
        captured["shape"] = np.asarray(image).shape
        return records, comp

    monkeypatch.setattr(panel_module, "run_cnr_measure_stack", _stub)

    panel._on_segment_cnr()

    assert panel._measure_worker._fn is panel_module.run_cnr_measure_stack
    assert captured["shape"] == (2, 120, 120)  # the (T,H,W) channel reached the pooler
    from percell4.gui.cnr_segmenter import CnrSegmenterWindow

    assert isinstance(panel._cnr_segmenter, CnrSegmenterWindow)
    assert panel._cnr_segmenter._comp.shape == (2, 120, 120)  # window got (T,H,W)
    panel._cnr_segmenter.close()


def test_classify_reads_source_mask_and_passes_mode(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells", existing=["adaptive"])
    store = panel._get_store()
    fmask = np.zeros((120, 120), dtype=np.uint8)
    fmask[40:60, 40:60] = 1
    store.read_mask.return_value = fmask
    _select_source_mask(panel, "adaptive")
    panel._cnr_settings._mode.setCurrentText("Guided (CNR threshold)")
    panel._cnr_settings._threshold.setValue(7.5)

    captured = {}

    def _stub(image, feature_mask, labels, *, mode, threshold):
        captured.update(mode=mode, threshold=threshold, fmask=feature_mask)
        return [], [], {"decision": "single", "warnings": []}

    monkeypatch.setattr(panel_module, "run_cnr_classification", _stub)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "out")

    panel._on_classify()

    store.read_mask.assert_called_once_with("adaptive")
    assert captured["mode"] == "guided"
    assert captured["threshold"] == 7.5
    assert np.array_equal(captured["fmask"], fmask)


def test_classify_saves_two_populations_and_writes_table(qtbot, monkeypatch):
    panel, model, repo, viewer_win = _build(
        qtbot, monkeypatch, segmentation="cells", existing=["adaptive"]
    )
    store = panel._get_store()
    store.read_mask.return_value = np.ones((120, 120), dtype=np.uint8)
    _select_source_mask(panel, "adaptive")

    low = np.zeros((120, 120), dtype=np.uint8)
    low[10:20, 10:20] = 1
    high = np.zeros((120, 120), dtype=np.uint8)
    high[30:40, 30:40] = 1
    comps = [
        {"label": 1, "cnr": 3.0, "subpopulation": 1},
        {"label": 2, "cnr": 30.0, "subpopulation": 2},
    ]
    report = {"decision": "2 populations", "warnings": [], "group_sizes": [1, 1]}
    monkeypatch.setattr(
        panel_module,
        "run_cnr_classification",
        lambda *a, **kw: ([("_low", low), ("_high", high)], comps, report),
    )
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "out")

    panel._on_classify()

    # Both populations persisted as {0,1} masks + added to the viewer.
    assert "out_low" in repo.masks and "out_high" in repo.masks
    assert set(np.unique(repo.masks["out_low"])) <= {0, 1}
    assert viewer_win.add_mask.call_count == 2
    # Last population is the active selection (second Creator wins).
    assert model.session.active_mask == "out_high"
    # Per-focus table written to its own /classification group.
    paths = [c.args[0] for c in store.write_dataframe.call_args_list]
    assert "/classification/out" in paths


def test_classify_single_population_saves_one_mask(qtbot, monkeypatch):
    panel, model, repo, viewer_win = _build(
        qtbot, monkeypatch, segmentation="cells", existing=["adaptive"]
    )
    store = panel._get_store()
    store.read_mask.return_value = np.ones((120, 120), dtype=np.uint8)
    _select_source_mask(panel, "adaptive")

    only = np.zeros((120, 120), dtype=np.uint8)
    only[10:30, 10:30] = 1
    comps = [{"label": 1, "cnr": 5.0, "subpopulation": 1}]
    report = {"decision": "single population", "warnings": []}
    monkeypatch.setattr(
        panel_module,
        "run_cnr_classification",
        lambda *a, **kw: ([("", only)], comps, report),
    )
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "out")

    panel._on_classify()

    assert "out" in repo.masks and "out_low" not in repo.masks
    assert viewer_win.add_mask.call_count == 1
    assert model.session.active_mask == "out"


def test_classify_prints_report(qtbot, monkeypatch, capsys):
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells", existing=["adaptive"])
    store = panel._get_store()
    store.read_mask.return_value = np.ones((120, 120), dtype=np.uint8)
    _select_source_mask(panel, "adaptive")
    report = {
        "decision": "2 populations",
        "n_components_valid": 50,
        "n_components_total": 52,
        "dip_cnr": {"method": "hartigan_dip", "pvalue": 0.001, "bimodal": True, "reliable": True},
        "cnr_percentiles": {50: 8.0},
        "candidate_cnr_threshold": 8.8,
        "mode": "discovered (significant CNR gap)",
        "group_sizes": [20, 30],
        "smaller_group_fraction": 0.4,
        "warnings": [],
    }
    only = np.ones((120, 120), dtype=np.uint8)
    monkeypatch.setattr(
        panel_module,
        "run_cnr_classification",
        lambda *a, **kw: ([("", only)], [{"label": 1}], report),
    )
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "out")

    panel._on_classify()

    out = capsys.readouterr().out
    assert "CNR subpopulation classification" in out
    assert "hartigan_dip" in out
    assert "candidate th" in out


# ── auto-extraction (two-pass): the only detection mode ──────────────────────


def _select_auto_extract(panel) -> None:
    """Auto extraction (two-pass) is the only detection mode — no selection needed."""


def _manual_smallest(panel) -> None:
    """Turn off Auto-detect so the smallest-Ø field is the manual override."""
    panel._settings._ae_smallest_auto.setChecked(False)


def test_auto_extract_run_uses_auto_extract_and_saves(qtbot, monkeypatch):
    # Default: smallest auto-detected (LoG) on the blob fixture.
    panel, model, repo, viewer_win = _build(qtbot, monkeypatch, segmentation="cells")
    _select_auto_extract(panel)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "ax")

    panel._on_run()

    assert panel._worker._fn is panel_module.run_adaptive_auto_extract
    assert panel._worker._args[2] is None  # auto-detect -> None passed to worker
    assert "ax" in repo.masks
    assert set(np.unique(repo.masks["ax"])).issubset({0, 1})
    viewer_win.add_mask.assert_called_once()
    assert model.session.active_mask == "ax"


def test_auto_extract_auto_backfills_smallest_readout(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells")
    _select_auto_extract(panel)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "ax")

    panel._on_run()

    # After an auto run the smallest-Ø readout shows the adapted (LoG) value.
    cfg = panel._settings.current_config()
    assert cfg.smallest_particle_unit == "px"
    assert cfg.smallest_particle_value > 0.0


def test_auto_extract_manual_passes_smallest_px_to_worker(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells")
    _select_auto_extract(panel)
    _manual_smallest(panel)
    panel._settings._smallest.setValue(4.0)  # px
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "ax")

    panel._on_run()

    # Worker args: (image, labels, smallest_px, presmooth, min_spot_px).
    assert panel._worker._args[2] == 4.0


def test_auto_extract_manual_um_converts_to_px(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, pixel_size_um=0.5, segmentation="cells")
    _select_auto_extract(panel)
    _manual_smallest(panel)
    panel._settings._smallest.setValue(2.0)
    panel._settings._smallest_unit.setCurrentText("µm")
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "ax")

    panel._on_run()

    # 2 µm / 0.5 µm/px = 4 px
    assert panel._worker._args[2] == pytest.approx(4.0)


def test_auto_extract_manual_um_without_pixel_size_aborts(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells")  # no pixel size
    _select_auto_extract(panel)
    _manual_smallest(panel)
    panel._settings._smallest_unit.setCurrentText("µm")
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "ax")

    panel._on_run()

    assert panel._worker is None  # aborted before dispatch


def test_auto_extract_without_segmentation_aborts(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch)  # no segmentation
    _select_auto_extract(panel)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "ax")

    panel._on_run()

    assert panel._worker is None


def test_auto_extract_timelapse_dispatches_stack_and_saves_THW(qtbot, monkeypatch):
    """A (T,H,W) channel auto-extracts per frame (the stack worker) and saves one
    (T,H,W) mask — no longer refused."""
    tl_lab = np.stack([_labels_one_cell(), _labels_one_cell()], axis=0)  # (2,120,120)
    panel, model, repo, viewer_win = _build(
        qtbot, monkeypatch, segmentation="cells", labels=tl_lab
    )
    store = panel._get_store()
    store.metadata = {"n_timepoints": 2}
    viewer_win.viewer.layers[0].data = np.stack([_blob_image(), _blob_image()], axis=0)
    _select_auto_extract(panel)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "axtl")

    panel._on_run()

    assert panel._worker._fn is panel_module.run_adaptive_auto_extract_stack
    assert "axtl" in repo.masks
    assert repo.masks["axtl"].shape == (2, 120, 120)
    assert set(np.unique(repo.masks["axtl"])).issubset({0, 1})
    assert model.session.active_mask == "axtl"


def test_auto_extract_prints_report(qtbot, monkeypatch, capsys):
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells")
    _select_auto_extract(panel)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "ax")

    panel._on_run()

    out = capsys.readouterr().out
    assert "[auto-extract]" in out
    assert "passes" in out


# ── SEG-U3: interactive CNR segmenter wiring ─────────────────────────────────


def test_segment_without_segmentation_aborts(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, existing=["adaptive"])  # no segmentation
    _select_source_mask(panel, "adaptive")
    panel._on_segment_cnr()
    assert panel._measure_worker is None
    assert panel._cnr_segmenter is None


def test_segment_without_source_mask_aborts(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells")  # no masks exist
    panel._on_segment_cnr()
    assert panel._measure_worker is None


def test_segment_dispatches_measure_and_opens_window(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells", existing=["adaptive"])
    store = panel._get_store()
    store.read_mask.return_value = np.ones((120, 120), dtype=np.uint8)
    _select_source_mask(panel, "adaptive")

    comp = np.zeros((120, 120), dtype=np.int32)
    comp[10:20, 10:20] = 1
    comp[30:40, 30:40] = 2
    records = [{"label": 1, "cnr": 3.0}, {"label": 2, "cnr": 30.0}]
    monkeypatch.setattr(panel_module, "run_cnr_measure", lambda *a, **k: (records, comp))

    panel._on_segment_cnr()

    store.read_mask.assert_called_once_with("adaptive")
    from percell4.gui.cnr_segmenter import CnrSegmenterWindow

    assert isinstance(panel._cnr_segmenter, CnrSegmenterWindow)
    panel._cnr_segmenter.close()


def test_segment_no_foci_shows_no_window(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells", existing=["adaptive"])
    store = panel._get_store()
    store.read_mask.return_value = np.ones((120, 120), dtype=np.uint8)
    _select_source_mask(panel, "adaptive")
    comp = np.zeros((120, 120), dtype=np.int32)
    # records all have non-finite CNR -> nothing to segment
    records = [{"label": 1, "cnr": float("nan")}]
    monkeypatch.setattr(panel_module, "run_cnr_measure", lambda *a, **k: (records, comp))

    panel._on_segment_cnr()

    assert panel._cnr_segmenter is None


# ── U3: largest-only single-pass dispatch ────────────────────────────────────


def _spy_domain(monkeypatch):
    """Wrap the two domain entry points with call counters, delegating to the real ones."""
    import percell4.domain.measure.auto_extraction as ae

    calls = {"largest": 0, "auto": 0}
    real_largest, real_auto = ae.extract_largest_only, ae.auto_extract

    def spy_largest(*a, **k):
        calls["largest"] += 1
        return real_largest(*a, **k)

    def spy_auto(*a, **k):
        calls["auto"] += 1
        return real_auto(*a, **k)

    monkeypatch.setattr(ae, "extract_largest_only", spy_largest)
    monkeypatch.setattr(ae, "auto_extract", spy_auto)
    return calls


def test_largest_only_dispatches_to_extract_largest_only(qtbot, monkeypatch):
    """Checked -> the coarse-only domain call runs (and the two-pass one does not)."""
    calls = _spy_domain(monkeypatch)
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch, segmentation="cells")
    panel._settings._largest_only.setChecked(True)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    assert calls == {"largest": 1, "auto": 0}
    assert "m" in repo.masks
    viewer_win.add_mask.assert_called_once()


def test_default_off_path_still_calls_auto_extract(qtbot, monkeypatch):
    """Box unchecked (default) -> the two-pass path is untouched (regression guard)."""
    calls = _spy_domain(monkeypatch)
    panel, _model, repo, _viewer_win = _build(qtbot, monkeypatch, segmentation="cells")
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    assert calls == {"largest": 0, "auto": 1}


def test_largest_only_needs_segmentation(qtbot, monkeypatch):
    """Largest-only is per-cell, so no active segmentation -> abort, nothing saved."""
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch)  # no segmentation
    panel._settings._largest_only.setChecked(True)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()


def test_largest_only_no_smallest_backfill(qtbot, monkeypatch):
    """No fine pass -> the smallest-Ø readout is not back-filled after the run."""
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells")
    panel._settings._largest_only.setChecked(True)
    filled: list = []
    monkeypatch.setattr(panel._settings, "set_smallest_value", lambda v: filled.append(v))
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    assert filled == []
    assert panel._pending_ae_auto is False


def test_largest_only_prints_mode_to_terminal(qtbot, monkeypatch, capsys):
    panel, *_ = _build(qtbot, monkeypatch, segmentation="cells")
    panel._settings._largest_only.setChecked(True)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    out = capsys.readouterr().out.lower()
    assert "largest particle only" in out


def test_run_adaptive_auto_extract_largest_only_flag():
    """The 2D worker body honours largest_only -> a largest-only report."""
    img = _blob_image()
    labels = _labels_one_cell()
    mask, report = panel_module.run_adaptive_auto_extract(
        img, labels, None, 1.0, 2, largest_only=True
    )
    assert report.largest_only is True
    assert report.fine_window == 0
    assert mask.shape == img.shape
