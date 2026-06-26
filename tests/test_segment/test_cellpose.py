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
    of built instances. Lets the wrapper be tested without a model download."""
    built = []

    class _FakeModel:
        def __init__(self, gpu=False, pretrained_model=None):
            self.gpu = gpu
            self.pretrained_model = pretrained_model
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
