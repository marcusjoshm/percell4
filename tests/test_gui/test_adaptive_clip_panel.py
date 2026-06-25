"""Tests for AdaptiveClipPanel (U4)."""

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


# ── happy paths ─────────────────────────────────────────────────────────

def test_manual_run_creates_and_selects_mask(qtbot, monkeypatch):
    panel, model, repo, viewer_win = _build(qtbot, monkeypatch)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "adaptive")

    panel._on_run()

    assert "adaptive" in repo.masks
    stored = repo.masks["adaptive"]
    assert stored.dtype == np.uint8
    assert set(np.unique(stored)).issubset({0, 1})
    assert int(stored.sum()) > 0
    viewer_win.add_mask.assert_called_once()
    assert model.session.active_mask == "adaptive"


def test_manual_config_reaches_detector(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch)
    panel._settings._window.setValue(31)
    panel._settings._k.setValue(2.5)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    # The settings handed to the worker carry the configured window/k.
    settings = panel._worker._args[2]
    params = dict(settings.detector_params)
    assert params["window_px"] == 31
    assert params["k"] == 2.5


def test_default_noise_estimator_is_mad(qtbot, monkeypatch):
    """Out of the box the panel detects with the MAD noise estimate (matches ImageJ)."""
    panel, *_ = _build(qtbot, monkeypatch)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    panel._on_run()
    assert panel._worker._args[2].background_estimator_name == "mad"


def test_noise_estimator_selection_reaches_detector(qtbot, monkeypatch):
    """The Noise (σ) estimate dropdown drives settings.background_estimator_name."""
    panel, *_ = _build(qtbot, monkeypatch)
    panel._settings._noise.setCurrentText("gaussian-peak")
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    panel._on_run()
    assert panel._worker._args[2].background_estimator_name == "gaussian-peak"


def test_default_window_method_is_granule_size(qtbot, monkeypatch):
    """Out of the box the worker receives the granule-size window method."""
    panel, *_ = _build(qtbot, monkeypatch)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    panel._on_run()
    # Worker args: (image, gaussian_sigma, settings, auto_window, window_method)
    assert panel._worker._args[4] == "granule-size"


def test_window_method_selection_reaches_worker(qtbot, monkeypatch):
    """The Auto window method dropdown drives the method passed to the worker."""
    panel, *_ = _build(qtbot, monkeypatch)
    panel._settings._window_method.setCurrentText("Otsu mean (baseline)")
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    panel._on_run()
    assert panel._worker._args[4] == "otsu-mean"


def test_auto_window_estimates_and_writes_back(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch)
    panel._settings._auto.setChecked(True)
    # Pin the baseline method so the surfaced window matches the otsu-mean expected
    # below (the dropdown defaults to granule-size).
    panel._settings._window_method.setCurrentText("Otsu mean (baseline)")
    panel._settings._window.setValue(15)  # ignored under auto
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    # Expected estimate from the same image the panel sees.
    from percell4.domain.measure.adaptive_clip import (
        estimate_adaptive_window,
        otsu_first_pass,
    )
    from percell4.domain.measure.thresholding import apply_gaussian_smoothing

    cfg = panel._settings.current_config()
    expected = estimate_adaptive_window(
        otsu_first_pass(apply_gaussian_smoothing(_blob_image(), cfg.gaussian_sigma))
    )

    panel._on_run()

    # The estimated window is surfaced back into the (disabled) spinbox, in px.
    surfaced = panel._settings.current_config()
    assert surfaced.window_value == expected
    assert surfaced.window_unit == "px"
    assert expected % 2 == 1


# ── error / edge paths ──────────────────────────────────────────────────

def test_um2_without_pixel_size_aborts(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch, pixel_size_um=None)
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
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: None)
    panel._on_run()
    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()


# ── particle-size (one-knob) mode via "Otsu detect particle size" ──


def _select_otsu_smallest(panel) -> None:
    """Enter the per-cell d_min engine: Auto on + the Otsu-smallest method.

    Selecting the method fires the panel's auto-fill (an Otsu first-pass that
    overwrites the d_min field); callers that pin a specific Ø set it afterwards.
    """
    panel._settings._auto.setChecked(True)
    panel._settings._window_method.setCurrentText("Otsu detect particle size")


def test_particle_mode_creates_mask_via_per_cell_detector(qtbot, monkeypatch):
    panel, model, repo, viewer_win = _build(
        qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells"
    )
    _select_otsu_smallest(panel)
    panel._settings._d_min.setValue(0.40)  # override the auto-filled value
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "pbody")

    panel._on_run()

    # Ran the per-cell particle detector, not the whole-frame path.
    assert panel._worker._fn is panel_module.run_adaptive_detection_by_particle_size
    assert "pbody" in repo.masks
    stored = repo.masks["pbody"]
    assert stored.dtype == np.uint8
    assert set(np.unique(stored)).issubset({0, 1})
    assert int(stored.sum()) > 0
    viewer_win.add_mask.assert_called_once()
    assert model.session.active_mask == "pbody"


def test_particle_mode_passes_pixel_size_fresh_dmin_and_k_to_worker(qtbot, monkeypatch):
    from percell4.domain.measure.adaptive_clip import otsu_smallest_particle

    panel, *_ = _build(qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells")
    _select_otsu_smallest(panel)  # adopts the default k=1
    panel._settings._k.setValue(2.5)  # raise k to be conservative
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    # Worker args: (image, labels, pixel_size_um, d_min_um, k, presmooth).
    args = panel._worker._args
    assert args[2] == 0.120369
    # d_min is the FRESHLY measured smallest particle (not a cached field value).
    expected = otsu_smallest_particle(
        _blob_image(), 1.0, 0.120369, cp_mask=_labels_one_cell() > 0, percentile=10.0
    ).d_min_um
    assert args[3] == pytest.approx(expected)
    assert args[4] == 2.5  # the user's k flows through (not locked)


def test_particle_mode_default_k_is_one(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells")
    _select_otsu_smallest(panel)  # validated default k=1
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    panel._on_run()
    assert panel._worker._args[4] == 1.0


def test_particle_mode_without_pixel_size_aborts(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(
        qtbot, monkeypatch, pixel_size_um=None, segmentation="cells"
    )
    _select_otsu_smallest(panel)  # auto-fill no-ops (no pixel size); run still aborts
    called = []
    monkeypatch.setattr(
        panel_module, "prompt_for_resource_name", lambda *a, **kw: called.append(1) or "m"
    )

    panel._on_run()

    assert called == []  # aborted before the name prompt
    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()


def test_particle_mode_without_segmentation_aborts(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(
        qtbot, monkeypatch, pixel_size_um=0.120369, segmentation=None
    )
    _select_otsu_smallest(panel)
    called = []
    monkeypatch.setattr(
        panel_module, "prompt_for_resource_name", lambda *a, **kw: called.append(1) or "m"
    )

    panel._on_run()

    assert called == []
    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()


def test_particle_mode_timelapse_aborts(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(
        qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells"
    )
    # Make the channel a (T, H, W) time-lapse; particle mode is single-frame only.
    img3d = np.stack([_blob_image(), _blob_image()], axis=0)
    for layer in viewer_win.viewer.layers:
        if layer.__class__.__name__ == "Image":
            layer.data = img3d
    panel._get_store().metadata["n_timepoints"] = 2
    _select_otsu_smallest(panel)
    called = []
    monkeypatch.setattr(
        panel_module, "prompt_for_resource_name", lambda *a, **kw: called.append(1) or "m"
    )

    panel._on_run()

    assert called == []
    assert repo.masks == {}


# ── Otsu-smallest Ø readout (re-detected fresh at run) ──────────────────


def test_selecting_otsu_smallest_does_not_autofill(qtbot, monkeypatch):
    """Selecting the method no longer changes Ø — detection happens at run."""
    panel, *_ = _build(qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells")
    _select_otsu_smallest(panel)
    assert panel._settings.current_config().d_min_um == 0.40  # unchanged until a run


def test_otsu_smallest_run_populates_dmin_readout(qtbot, monkeypatch):
    """After a run, the Ø readout shows the freshly-measured smallest particle."""
    from percell4.domain.measure.adaptive_clip import otsu_smallest_particle

    panel, *_ = _build(qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells")
    _select_otsu_smallest(panel)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    expected = otsu_smallest_particle(
        _blob_image(), 1.0, 0.120369, cp_mask=_labels_one_cell() > 0, percentile=10.0
    ).d_min_um
    d = panel._settings.current_config().d_min_um
    assert d == pytest.approx(expected, abs=1e-3)  # spinbox rounds to 3 decimals
    assert d != 0.40  # the run updated the readout off the default


def test_size_percentile_knob_reaches_detection(qtbot, monkeypatch):
    """The panel's Size percentile knob drives the percentile passed to the run."""
    from percell4.domain.measure.adaptive_clip import otsu_smallest_particle

    # Varied particle sizes so the percentile actually changes the result.
    img = _blob_image(radius=4)
    rr, cc = disk((60, 60), 14, shape=img.shape)
    img[rr, cc] = 200.0
    panel, _model, _repo, viewer_win = _build(
        qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells"
    )
    for layer in viewer_win.viewer.layers:
        if layer.__class__.__name__ == "Image":
            layer.data = img
    _select_otsu_smallest(panel)
    panel._settings._percentile.setValue(60.0)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    expected = otsu_smallest_particle(
        img, 1.0, 0.120369, cp_mask=_labels_one_cell() > 0, percentile=60.0
    ).d_min_um
    assert panel._worker._args[3] == pytest.approx(expected)


def test_failed_particle_run_does_not_mislabel_next_manual_run(qtbot, monkeypatch):
    """A failed per-cell run must not leave _pending_particle set for the next run."""
    panel, _model, repo, _viewer_win = _build(
        qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells"
    )
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    # 1) Force the per-cell detector to fail -> _on_detect_error must clear flags.
    def boom(*a, **kw):
        raise RuntimeError("per-cell failed")

    monkeypatch.setattr(panel_module, "run_adaptive_detection_by_particle_size", boom)
    _select_otsu_smallest(panel)
    panel._settings._d_min.setValue(0.40)
    panel._on_run()
    assert repo.masks == {}  # the per-cell run failed
    assert panel._pending_particle is False  # cleared by _on_detect_error

    # 2) A plain manual run must not emit a per-cell note nor overwrite the window.
    panel._settings._auto.setChecked(False)  # manual window path
    panel._settings._window.setValue(15)
    panel._on_run()
    assert "m" in repo.masks  # manual run succeeded
    assert "per-cell" not in panel._status.text()
    assert panel._settings._window.value() == 15  # spinbox untouched


# ── terminal debug output ───────────────────────────────────────────────


def test_run_prints_all_settings_to_terminal(qtbot, monkeypatch, capsys):
    """Every run dumps the full settings block to stdout."""
    panel, *_ = _build(qtbot, monkeypatch)
    panel._settings._k.setValue(2.5)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    panel._on_run()
    out = capsys.readouterr().out
    assert "Adaptive Local Clipping run" in out
    for field in ("auto_window", "window_method", "particle_mode", "k", "gaussian_sigma"):
        assert field in out
    assert "2.5" in out  # the k value flows into the dump


def test_otsu_smallest_run_prints_otsu_diagnostics(qtbot, monkeypatch, capsys):
    """An Otsu-smallest run prints the threshold + smallest particle + area stats."""
    panel, *_ = _build(qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells")
    _select_otsu_smallest(panel)
    panel._settings._d_min.setValue(0.40)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    capsys.readouterr()  # drop anything emitted during selection

    panel._on_run()

    out = capsys.readouterr().out
    assert "otsu first-pass" in out
    assert "Otsu threshold" in out
    assert "particle size" in out
    assert "threshold-area mean intensity" in out
    assert "mean − threshold" in out
    assert "threshold-area max intensity" in out
    assert "threshold-area min intensity" in out


def test_otsu_smallest_run_detects_fresh_per_dataset(qtbot, monkeypatch):
    """Each Run re-detects d_min on the CURRENT image, not the cached field.

    Captures the reported bug: after selecting the option once, switching to a
    new dataset (without re-selecting) must still size the window from the new
    dataset's smallest particle.
    """
    from percell4.domain.measure.adaptive_clip import otsu_smallest_particle

    panel, _model, _repo, viewer_win = _build(
        qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells"
    )
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    sigma = panel._settings.current_config().gaussian_sigma
    cp = _labels_one_cell() > 0

    # Dataset A (discs r=6): Run must use A's freshly-detected d_min.
    _select_otsu_smallest(panel)
    panel._on_run()
    expected_a = otsu_smallest_particle(
        _blob_image(radius=6), sigma, 0.120369, cp_mask=cp, percentile=10.0
    ).d_min_um
    assert panel._worker._args[3] == pytest.approx(expected_a)

    # Dataset B (discs r=12), swapped WITHOUT re-selecting the dropdown.
    img_b = _blob_image(radius=12)
    for layer in viewer_win.viewer.layers:
        if layer.__class__.__name__ == "Image":
            layer.data = img_b
    panel._on_run()
    expected_b = otsu_smallest_particle(
        img_b, sigma, 0.120369, cp_mask=cp, percentile=10.0
    ).d_min_um
    assert panel._worker._args[3] == pytest.approx(expected_b)
    assert expected_b != pytest.approx(expected_a)  # different dataset -> different d_min


# ── manual mode (Auto off): per-cell off segmentation / whole-frame / px·µm ──


def test_manual_mode_per_cell_when_segmentation(qtbot, monkeypatch):
    """Manual mode with an active segmentation runs the per-cell detector."""
    panel, _model, repo, _viewer = _build(
        qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells"
    )
    panel._settings._window.setValue(21.0)  # px (default unit)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    assert panel._worker._fn is panel_module.run_adaptive_detection_per_cell
    # Worker args: (image, labels, window_px, min_spot_px, k, presmooth).
    assert panel._worker._args[2] == 21
    assert "m" in repo.masks


def test_manual_mode_whole_frame_when_no_segmentation(qtbot, monkeypatch):
    """Manual mode falls back to whole-frame when no segmentation is active."""
    panel, *_ = _build(qtbot, monkeypatch)  # no segmentation
    panel._settings._window.setValue(21.0)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    assert panel._worker._fn is panel_module.run_adaptive_detection
    # Worker args: (image, gaussian_sigma, settings, auto_window, window_method).
    assert panel._worker._args[3] is False  # not auto
    assert dict(panel._worker._args[2].detector_params)["window_px"] == 21


def test_manual_window_um_converts_to_px(qtbot, monkeypatch):
    """A µm manual window is converted to an odd px window via the pixel size."""
    from percell4.domain.measure.adaptive_clip import resolve_window_px

    panel, *_ = _build(qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells")
    panel._settings._window.setValue(1.8)
    panel._settings._window_unit.setCurrentText("µm")
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    assert panel._worker._args[2] == resolve_window_px(1.8, "um", 0.120369)  # = 15 px


def test_manual_um_window_without_pixel_size_aborts(qtbot, monkeypatch):
    """A µm window with no calibration aborts before the name prompt."""
    panel, _model, repo, viewer_win = _build(
        qtbot, monkeypatch, pixel_size_um=None, segmentation="cells"
    )
    panel._settings._window.setValue(1.8)
    panel._settings._window_unit.setCurrentText("µm")
    called = []
    monkeypatch.setattr(
        panel_module, "prompt_for_resource_name", lambda *a, **kw: called.append(1) or "m"
    )

    panel._on_run()

    assert called == []  # aborted before the name prompt
    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()


# ── multi-scale mode (per-cell, doubling windows OR-combined) ────────────


def _select_multiscale(panel) -> None:
    panel._settings._auto.setChecked(True)
    panel._settings._window_method.setCurrentText("Multi-scale (particle range)")


def test_multiscale_run_uses_multiscale_detector(qtbot, monkeypatch):
    panel, _model, repo, _viewer = _build(
        qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells"
    )
    _select_multiscale(panel)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "ms")

    panel._on_run()

    assert panel._worker._fn is panel_module.run_adaptive_detection_multiscale
    assert "ms" in repo.masks
    assert set(np.unique(repo.masks["ms"])).issubset({0, 1})


def test_multiscale_auto_start_is_half_mean(qtbot, monkeypatch):
    from percell4.domain.measure.adaptive_clip import assess_particle_sizes_per_cell

    panel, *_ = _build(qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells")
    _select_multiscale(panel)  # auto start on by default
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    rep = assess_particle_sizes_per_cell(_blob_image(), _labels_one_cell(), 1.0)
    expected_start = max(3, int(round(0.5 * rep.mean_px)) | 1)
    # Worker args: (image, labels, start_window_px, max_particle_px, k, presmooth).
    assert panel._worker._args[2] == expected_start


def test_multiscale_manual_start_uses_window_field(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells")
    _select_multiscale(panel)
    panel._settings._ms_auto_start.setChecked(False)  # manual start window
    panel._settings._window.setValue(9.0)  # px
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    assert panel._worker._args[2] == 9  # the manual start window (odd px)


def test_multiscale_without_segmentation_aborts(qtbot, monkeypatch):
    panel, _model, repo, viewer_win = _build(qtbot, monkeypatch, pixel_size_um=0.120369)
    _select_multiscale(panel)
    called = []
    monkeypatch.setattr(
        panel_module, "prompt_for_resource_name", lambda *a, **kw: called.append(1) or "m"
    )

    panel._on_run()

    assert called == []  # aborted before the name prompt
    assert repo.masks == {}
    viewer_win.add_mask.assert_not_called()


def test_multiscale_run_prints_otsu_raw_range(qtbot, monkeypatch, capsys):
    panel, *_ = _build(qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells")
    _select_multiscale(panel)
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")
    capsys.readouterr()  # drop selection-time output

    panel._on_run()

    out = capsys.readouterr().out
    assert "multi-scale" in out
    assert "Otsu raw particle" in out  # raw min/max printed for calibration
    assert "windows" in out


def test_multiscale_manual_iterations_reach_worker(qtbot, monkeypatch):
    panel, *_ = _build(qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells")
    _select_multiscale(panel)
    panel._settings._iterations.setValue(4)  # exactly 4 doubling passes
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    # Worker args: (image, labels, start, max_particle, k, presmooth, force_passes).
    assert panel._worker._args[6] == 4


def test_multiscale_min_particle_size_reaches_worker(qtbot, monkeypatch):
    """The Min particle size field is applied to the multi-scale output."""
    panel, *_ = _build(qtbot, monkeypatch, pixel_size_um=0.120369, segmentation="cells")
    _select_multiscale(panel)
    panel._settings._min_size.setValue(9.0)  # px²
    monkeypatch.setattr(panel_module, "prompt_for_resource_name", lambda *a, **kw: "m")

    panel._on_run()

    # Worker args: (image, labels, start, max, k, presmooth, force_passes, min_spot_px).
    assert panel._worker._args[7] == 9


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


def test_classify_timelapse_aborts(qtbot, monkeypatch):
    panel, model, repo, viewer_win = _build(
        qtbot, monkeypatch, segmentation="cells", existing=["adaptive"]
    )
    store = panel._get_store()
    store.metadata = {"n_timepoints": 3}
    # a (T,H,W) channel layer
    viewer_win.viewer.layers[0].data = np.zeros((3, 120, 120), dtype=np.float32)
    _select_source_mask(panel, "adaptive")
    panel._on_classify()
    assert panel._cnr_worker is None


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


# ── AE-U4: auto-extraction (two-pass) mode ───────────────────────────────────


def _select_auto_extract(panel) -> None:
    panel._settings._auto.setChecked(True)
    panel._settings._window_method.setCurrentText("Auto extraction (two-pass)")


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
