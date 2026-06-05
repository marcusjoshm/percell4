"""Tests for the Segment panel's Cellpose settings run path (U3/U4).

The panel now hosts the shared CellposeSettingsForm. _on_run_cellpose must
(1) feed flow/cellprob/min-size to the cellpose adapter and (2) preprocess the
input image with the saturation LUT then the Gaussian blur, in that order,
before handing it to the Worker — never touching the on-disk /intensity.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.gui import segmentation_panel as sp_module


def _build_panel(qtbot, *, channel="ch0", layer_data=None):
    """Construct a SegmentationPanel with a mocked launcher/store/viewer."""
    from percell4.gui.segmentation_panel import SegmentationPanel
    from percell4.model import CellDataModel

    model = CellDataModel()
    model.session.set_active_channel(channel)

    store = MagicMock()
    store.list_labels.return_value = []
    store.list_masks.return_value = []

    if layer_data is None:
        layer_data = np.zeros((10, 10), dtype=np.uint16)
    layer = MagicMock()
    layer.name = channel
    layer.data = layer_data
    layer.__class__.__name__ = "Image"

    viewer = MagicMock()
    viewer.layers = [layer]
    viewer_win = MagicMock()
    viewer_win.viewer = viewer

    launcher = MagicMock()
    launcher._current_store = store
    launcher._windows = {"viewer": viewer_win}
    launcher.statusBar.return_value.showMessage = MagicMock()

    panel = SegmentationPanel(data_model=model, launcher=launcher)
    qtbot.addWidget(panel)
    return panel, layer_data


def _install_fake_worker(monkeypatch) -> list[Any]:
    """Replace Worker with a spy that records (args, kwargs); returns the list."""
    import percell4.gui.workers as workers_mod

    calls: list[Any] = []

    class FakeWorker:
        def __init__(self, *a, **kw):
            calls.append((a, kw))
            self.finished = MagicMock()
            self.error = MagicMock()

        def start(self):
            pass

    monkeypatch.setattr(workers_mod, "Worker", FakeWorker)
    return calls


def test_form_present_no_channel_combo(qtbot) -> None:
    """R4: the panel hosts the shared form and no channel-writing combo."""
    from percell4.gui._cellpose_settings_form import CellposeSettingsForm

    panel, _ = _build_panel(qtbot)
    assert isinstance(panel._cp_form, CellposeSettingsForm)
    assert not hasattr(panel, "_cp_seg_channel")
    # R5: edge margin control present with the documented range.
    assert panel._cp_remove_edges.isChecked() is True
    assert panel._cp_edge_margin.minimum() == 0
    assert panel._cp_edge_margin.maximum() == 500


def test_run_cellpose_passes_inference_kwargs(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flow/cellprob/min-size from the form reach the cellpose adapter."""
    panel, _ = _build_panel(qtbot)
    monkeypatch.setattr(sp_module, "prompt_for_resource_name", lambda *a, **kw: "seg")
    calls = _install_fake_worker(monkeypatch)

    panel._cp_form._flow.setValue(0.7)
    panel._cp_form._cellprob.setValue(0.3)
    panel._cp_form._min_size.setValue(40)

    panel._on_run_cellpose()

    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert kwargs["flow_threshold"] == pytest.approx(0.7)
    assert kwargs["cellprob_threshold"] == pytest.approx(0.3)
    assert kwargs["min_size"] == 40


def test_run_cellpose_applies_saturation_then_blur(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saturation LUT runs first, then the Gaussian blur, on the image fed
    to the worker."""
    panel, raw = _build_panel(qtbot)
    monkeypatch.setattr(sp_module, "prompt_for_resource_name", lambda *a, **kw: "seg")
    calls = _install_fake_worker(monkeypatch)

    import percell4.domain.segmentation.preprocess as pre_mod

    order: list[str] = []

    def fake_sat(plane, pct):
        order.append(f"sat:{pct}")
        return plane + 1

    def fake_blur(plane, sigma):
        order.append(f"blur:{sigma}")
        return plane + 10

    monkeypatch.setattr(pre_mod, "apply_saturation_lut", fake_sat)
    monkeypatch.setattr(pre_mod, "apply_gaussian_blur", fake_blur)

    panel._cp_form._saturation.setValue(1.0)
    panel._cp_form._blur_sigma.setValue(1.5)

    panel._on_run_cellpose()

    assert order == ["sat:1.0", "blur:1.5"]
    # Image fed to the worker is sat-then-blur applied (raw 0 → +1 → +11).
    _args, _kwargs = calls[0]
    image = _args[1]
    assert np.all(image == raw + 11)


def test_run_cellpose_skips_preprocess_at_zero(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """saturation_pct == 0 and blur_sigma == 0 → neither helper is called and
    the raw layer data passes through unchanged."""
    raw = np.arange(100, dtype=np.uint16).reshape((10, 10))
    panel, _ = _build_panel(qtbot, layer_data=raw)
    monkeypatch.setattr(sp_module, "prompt_for_resource_name", lambda *a, **kw: "seg")
    calls = _install_fake_worker(monkeypatch)

    import percell4.domain.segmentation.preprocess as pre_mod

    def boom(*a, **kw):  # noqa: ARG001
        raise AssertionError("preprocess should not run at zero")

    monkeypatch.setattr(pre_mod, "apply_saturation_lut", boom)
    monkeypatch.setattr(pre_mod, "apply_gaussian_blur", boom)

    panel._cp_form._saturation.setValue(0.0)
    panel._cp_form._blur_sigma.setValue(0.0)

    panel._on_run_cellpose()

    _args, _kwargs = calls[0]
    image = _args[1]
    assert np.array_equal(image, raw)


def test_run_cellpose_stack_routes_to_stack_with_kwargs(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A (T, H, W) layer routes to run_cellpose_stack and forwards the
    inference kwargs."""
    stack = np.zeros((3, 10, 10), dtype=np.uint16)
    panel, _ = _build_panel(qtbot, layer_data=stack)
    monkeypatch.setattr(sp_module, "prompt_for_resource_name", lambda *a, **kw: "seg")
    calls = _install_fake_worker(monkeypatch)

    panel._cp_form._min_size.setValue(25)
    panel._on_run_cellpose()

    from percell4.adapters.cellpose import run_cellpose_stack

    args, kwargs = calls[0]
    assert args[0] is run_cellpose_stack
    assert args[1].shape == (3, 10, 10)
    assert kwargs["min_size"] == 25
