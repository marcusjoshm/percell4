"""Tests for Cellpose wrapper.

The actual Cellpose test is marked slow (requires model download).
The import/instantiation smoke test runs without the model.
"""

from __future__ import annotations

import numpy as np
import pytest


def test_cellpose_importable():
    """Cellpose module can be imported without errors."""
    from percell4.adapters.cellpose import run_cellpose  # noqa: F401


def _install_fake_cellpose(monkeypatch):
    """Patch cellpose.models.CellposeModel with a recorder, returning the list
    of built instances. Lets the wrapper be tested without a model download.

    ``**kwargs`` rather than a fixed signature: the wrapper passes ``device=``
    instead of ``gpu=`` once a non-CPU device resolves, and a fake that only
    accepted the old shape would raise TypeError on any machine that happens
    to have an accelerator -- turning a green suite into a machine-dependent
    one. ``init_kwargs`` records the whole call so tests can assert on which
    shape was used.
    """
    built = []

    class _FakeModel:
        def __init__(self, gpu=False, pretrained_model=None, **kwargs):
            self.gpu = gpu
            self.pretrained_model = pretrained_model
            self.device = kwargs.get("device")
            self.init_kwargs = {
                "gpu": gpu,
                "pretrained_model": pretrained_model,
                **kwargs,
            }
            self.eval_kwargs = None
            built.append(self)

        def eval(self, image, **kwargs):
            self.eval_kwargs = kwargs
            # Cellpose 4.x returns a 3-tuple (masks, flows, diams).
            masks = np.zeros(np.asarray(image).shape[:2], dtype=np.int32)
            return masks, None, None

    monkeypatch.setattr("cellpose.models.CellposeModel", _FakeModel)
    return built


def test_build_forwards_pretrained_model(monkeypatch):
    """build_cellpose_model passes the chosen model as pretrained_model (the
    core regression: the 4.x branch used to drop the model name)."""
    from percell4.adapters.cellpose import build_cellpose_model

    built = _install_fake_cellpose(monkeypatch)
    m = build_cellpose_model("cpdino", gpu=False)
    assert m.pretrained_model == "cpdino"
    assert m.gpu is False
    assert built[-1] is m

    m2 = build_cellpose_model("cpdino-vitb", gpu=False)  # hyphen preserved
    assert m2.pretrained_model == "cpdino-vitb"


def test_build_defaults_to_cpsam_v2(monkeypatch):
    """No model name resolves to the cpsam_v2 default, not the old cyto3."""
    from percell4.adapters.cellpose import build_cellpose_model

    _install_fake_cellpose(monkeypatch)
    assert build_cellpose_model(None, gpu=False).pretrained_model == "cpsam_v2"


def test_run_cellpose_forwards_model_and_threads_eval_params(monkeypatch):
    """run_cellpose builds with the chosen model, returns an int32 label array,
    and threads diameter/flow/cellprob/min-size into eval unchanged."""
    from percell4.adapters.cellpose import run_cellpose

    built = _install_fake_cellpose(monkeypatch)
    img = np.zeros((32, 32), dtype=np.float32)
    labels = run_cellpose(
        img, model_type="cpdino", diameter=30,
        flow_threshold=0.5, cellprob_threshold=-1.0, min_size=20,
    )
    assert labels.dtype == np.int32 and labels.shape == (32, 32)
    assert built[-1].pretrained_model == "cpdino"
    ek = built[-1].eval_kwargs
    assert ek["diameter"] == 30
    assert ek["flow_threshold"] == 0.5
    assert ek["cellprob_threshold"] == -1.0
    assert ek["min_size"] == 20


def test_run_cellpose_stack_builds_once_and_forwards(monkeypatch):
    """A (T,H,W) stack builds ONE model, reused across frames, forwarding the
    chosen model name."""
    from percell4.adapters.cellpose import run_cellpose_stack

    built = _install_fake_cellpose(monkeypatch)
    out = run_cellpose_stack(np.zeros((3, 16, 16), dtype=np.float32), model_type="cpsam")
    assert out.shape == (3, 16, 16) and out.dtype == np.int32
    assert len(built) == 1  # built once, not per-frame
    assert built[0].pretrained_model == "cpsam"


# ── Device resolution ────────────────────────────────────────────────
#
# The probe is patched throughout so these assert the wrapper's routing
# rather than the machine's hardware.


def _patch_resolution(monkeypatch, usable=()):
    """Make the named device specs probe as usable and all others as not."""
    from percell4.adapters import torch_device

    monkeypatch.setattr(
        torch_device,
        "_probe_device",
        lambda spec: None if spec in usable else f"no {spec} here",
    )


def test_default_path_is_unchanged_when_nothing_is_configured(monkeypatch):
    """AE2: with no override stored and no device passed, the construction
    call must look exactly as it did before device resolution existed."""
    from percell4.adapters.cellpose import build_cellpose_model

    built = _install_fake_cellpose(monkeypatch)
    _patch_resolution(monkeypatch, usable=())
    build_cellpose_model("cpsam_v2", gpu=True)

    assert built[-1].init_kwargs == {"gpu": False, "pretrained_model": "cpsam_v2"}
    assert "device" not in built[-1].init_kwargs


def test_resolved_accelerator_is_passed_as_an_explicit_device(monkeypatch):
    """A non-CPU resolution bypasses Cellpose's own two-branch resolver by
    handing it the device outright."""
    from percell4.adapters.cellpose import build_cellpose_model

    built = _install_fake_cellpose(monkeypatch)
    _patch_resolution(monkeypatch, usable=("cuda",))
    build_cellpose_model("cpsam_v2", gpu=True)

    assert built[-1].init_kwargs["device"] is not None
    assert str(built[-1].init_kwargs["device"]) == "cuda"


def test_stored_override_is_used_when_no_device_is_passed(monkeypatch):
    """The Advanced panel's setting reaches a caller that knows nothing
    about it -- that is what makes the override apply everywhere."""
    from percell4.adapters.cellpose import build_cellpose_model
    from percell4.config import advanced

    built = _install_fake_cellpose(monkeypatch)
    _patch_resolution(monkeypatch, usable=("xpu",))
    advanced.save_advanced_settings(advanced.AdvancedSettings(cellpose_device="xpu"))

    build_cellpose_model("cpsam_v2", gpu=True)
    assert str(built[-1].init_kwargs["device"]) == "xpu"


def test_explicit_device_beats_the_stored_override(monkeypatch):
    """AE4: an argument at the call site wins over the stored setting."""
    from percell4.adapters.cellpose import build_cellpose_model
    from percell4.config import advanced

    built = _install_fake_cellpose(monkeypatch)
    _patch_resolution(monkeypatch, usable=("xpu", "cuda:1"))
    advanced.save_advanced_settings(advanced.AdvancedSettings(cellpose_device="xpu"))

    build_cellpose_model("cpsam_v2", gpu=True, device="cuda:1")
    assert str(built[-1].init_kwargs["device"]) == "cuda:1"


def test_unusable_override_falls_back_to_the_cpu_call_shape(monkeypatch):
    from percell4.adapters.cellpose import build_cellpose_model

    built = _install_fake_cellpose(monkeypatch)
    _patch_resolution(monkeypatch, usable=())
    build_cellpose_model("cpsam_v2", gpu=True, device="xpu")

    assert built[-1].init_kwargs == {"gpu": False, "pretrained_model": "cpsam_v2"}


def test_device_callback_fires_once_on_build(monkeypatch):
    from percell4.adapters.cellpose import build_cellpose_model

    _install_fake_cellpose(monkeypatch)
    _patch_resolution(monkeypatch, usable=())
    seen = []
    build_cellpose_model("cpsam_v2", gpu=True, device_callback=seen.append)

    assert len(seen) == 1
    assert seen[0].device == "cpu"
    assert seen[0].fell_back is True


def test_device_callback_reports_the_override_that_produced_it(monkeypatch):
    """Callers cache the built model and compare this against the current
    stored override to decide whether the cache went stale."""
    from percell4.adapters.cellpose import build_cellpose_model

    _install_fake_cellpose(monkeypatch)
    _patch_resolution(monkeypatch, usable=("xpu",))
    seen = []
    build_cellpose_model("cpsam_v2", gpu=True, device="xpu", device_callback=seen.append)

    assert seen[0].requested == "xpu"


def test_run_cellpose_fires_the_callback_when_it_builds(monkeypatch):
    from percell4.adapters.cellpose import run_cellpose

    _install_fake_cellpose(monkeypatch)
    _patch_resolution(monkeypatch, usable=())
    seen = []
    run_cellpose(
        np.zeros((16, 16), dtype=np.float32), gpu=True, device_callback=seen.append
    )

    assert len(seen) == 1


def test_prebuilt_model_neither_resolves_nor_reports(monkeypatch):
    """A prebuilt model already decided its device. Re-resolving would be
    wrong, and the seg-QC and workflow surfaces -- which cache a model and
    pass it in -- depend on the callback firing at build time instead."""
    from percell4.adapters.cellpose import build_cellpose_model, run_cellpose

    _install_fake_cellpose(monkeypatch)
    _patch_resolution(monkeypatch, usable=())
    model = build_cellpose_model("cpsam_v2", gpu=True)

    seen = []
    called = []
    from percell4.adapters import torch_device

    monkeypatch.setattr(
        torch_device,
        "_probe_device",
        lambda spec: called.append(spec) or "no",
    )
    run_cellpose(
        np.zeros((16, 16), dtype=np.float32),
        model=model,
        device_callback=seen.append,
    )

    assert seen == []
    assert called == []


def test_stack_reports_the_device_once_not_once_per_frame(monkeypatch):
    """A 100-frame stack must not raise 100 identical fallback warnings."""
    from percell4.adapters.cellpose import run_cellpose_stack

    _install_fake_cellpose(monkeypatch)
    _patch_resolution(monkeypatch, usable=())
    seen = []
    progress = []
    run_cellpose_stack(
        np.zeros((4, 16, 16), dtype=np.float32),
        gpu=True,
        device_callback=seen.append,
        progress_callback=lambda done, total: progress.append(done),
    )

    assert len(seen) == 1
    assert progress == [1, 2, 3, 4]  # the existing per-frame signal still fires


def test_segmenter_port_forwards_the_device(monkeypatch):
    from percell4.adapters import cellpose as cp

    captured = {}

    def _fake_run_cellpose(image, **kwargs):
        captured.update(kwargs)
        return np.zeros((8, 8), dtype=np.int32)

    monkeypatch.setattr(cp, "run_cellpose", _fake_run_cellpose)
    cp.CellposeSegmenter().run(
        np.zeros((8, 8), dtype=np.float32), gpu=True, device="xpu"
    )

    assert captured["device"] == "xpu"


@pytest.mark.slow
def test_cellpose_runs_on_synthetic_image():
    """Run Cellpose on a small synthetic image (requires model download)."""
    from percell4.adapters.cellpose import run_cellpose

    # Create a simple image with bright circles on dark background
    image = np.zeros((128, 128), dtype=np.float32)
    rr, cc = np.ogrid[:128, :128]
    for cy, cx in [(30, 30), (30, 90), (90, 60)]:
        mask = (rr - cy) ** 2 + (cc - cx) ** 2 < 15**2
        image[mask] = 200.0

    labels = run_cellpose(image, model_type="cpsam_v2", diameter=30, gpu=False)

    assert labels.dtype == np.int32
    assert labels.shape == (128, 128)
    assert labels.max() > 0  # at least one cell found
