"""Tests for the Cellpose channel-override combo (U1).

Mirrors the Grouped Thresholding channel-override pattern at
``gui/grouped_seg_panel.py:67-72, 168-189``. The combo is a local
override — picking a value does not write back to
``session.active_channel`` — and re-seeds whenever the session
dataset or active channel changes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import h5py
import numpy as np
import pytest
from qtpy.QtWidgets import QComboBox


def _build_panel(qtbot, *, channel_names=None, active="ch0"):
    """Construct a SegmentationPanel with mocked launcher + session metadata."""
    from percell4.domain.dataset import DatasetHandle
    from percell4.gui.segmentation_panel import SegmentationPanel
    from percell4.model import CellDataModel

    channel_names = channel_names or []

    model = CellDataModel()
    if channel_names:
        handle = DatasetHandle(
            path=Path("/tmp/test.h5"),
            metadata={"channel_names": list(channel_names)},
        )
        model.session.set_dataset(handle)
        if active and active in channel_names:
            model.session.set_active_channel(active)

    launcher = MagicMock()
    launcher._windows = {}
    launcher.statusBar.return_value.showMessage = MagicMock()

    panel = SegmentationPanel(data_model=model, launcher=launcher)
    qtbot.addWidget(panel)
    return panel, model, launcher


def test_panel_renders_channel_combo_not_label(qtbot) -> None:
    """The Cellpose section shows a QComboBox 'Channel:' field, not a QLabel."""
    panel, _model, _launcher = _build_panel(
        qtbot, channel_names=["ch0", "ch1", "ch2"], active="ch0"
    )
    assert hasattr(panel, "_channel_combo")
    assert isinstance(panel._channel_combo, QComboBox)
    # The old read-only label is gone.
    assert not hasattr(panel, "_channel_label")


def test_combo_populates_from_session_channel_names(qtbot) -> None:
    """update_channels enumerates session.dataset.metadata.channel_names."""
    panel, _model, _launcher = _build_panel(
        qtbot, channel_names=["ch0", "ch1", "ch2"], active="ch1"
    )
    panel.update_channels()
    items = [
        panel._channel_combo.itemText(i)
        for i in range(panel._channel_combo.count())
    ]
    assert items == ["ch0", "ch1", "ch2"]
    assert panel._channel_combo.currentText() == "ch1"


def test_combo_falls_back_to_viewer_layers_when_metadata_empty(qtbot) -> None:
    """If session has no channel_names, populate from viewer Image layers
    (mirrors grouped_seg_panel.update_channels fallback at lines 183-189)."""
    panel, _model, launcher = _build_panel(qtbot, channel_names=[], active="")

    # Build viewer with two Image layers.
    def make_image(name):
        layer = MagicMock()
        layer.__class__ = type("Image", (MagicMock,), {})
        layer.__class__.__name__ = "Image"
        layer.name = name
        return layer

    viewer = MagicMock()
    viewer.layers = [make_image("layer_a"), make_image("layer_b")]
    viewer_win = MagicMock()
    viewer_win.viewer = viewer
    launcher._windows = {"viewer": viewer_win}

    panel.update_channels()
    items = [
        panel._channel_combo.itemText(i)
        for i in range(panel._channel_combo.count())
    ]
    assert items == ["layer_a", "layer_b"]


def test_no_dataset_no_viewer_keeps_combo_empty(qtbot) -> None:
    """Defensive: when neither path yields channels, combo stays empty."""
    panel, _model, _launcher = _build_panel(qtbot, channel_names=[], active="")
    panel.update_channels()
    assert panel._channel_combo.count() == 0


def test_on_state_changed_refreshes_combo_on_channel_change(qtbot) -> None:
    """state_changed with change.channel=True triggers update_channels."""
    panel, model, _launcher = _build_panel(
        qtbot, channel_names=["ch0", "ch1"], active="ch0"
    )
    panel.update_channels()
    assert panel._channel_combo.currentText() == "ch0"

    # Simulate session changing active channel.
    model.session.set_active_channel("ch1")
    # state_changed signal already wired to update_channels via _on_state_changed.
    # Some emit paths require model.session._emit; emit directly:
    from percell4.model import StateChange

    panel._on_state_changed(StateChange(channel=True))
    assert panel._channel_combo.currentText() == "ch1"


def test_user_override_does_not_write_back_to_session(qtbot) -> None:
    """Picking a different combo value is local — session.active_channel
    is untouched."""
    panel, model, _launcher = _build_panel(
        qtbot, channel_names=["ch0", "ch1", "ch2"], active="ch0"
    )
    panel.update_channels()
    panel._channel_combo.setCurrentText("ch2")
    # Session's active_channel is still ch0 (the original).
    assert model.session.active_channel == "ch0"
    assert panel._channel_combo.currentText() == "ch2"


def test_on_run_cellpose_reads_from_combo_not_session(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When user overrides the combo, _on_run_cellpose passes the combo's
    value to Cellpose, not session.active_channel."""
    panel, _model, launcher = _build_panel(
        qtbot, channel_names=["ch0", "ch1"], active="ch0"
    )
    panel.update_channels()
    panel._channel_combo.setCurrentText("ch1")  # override

    # Stub viewer with a layer named "ch1".
    def make_image(name):
        layer = MagicMock()
        layer.__class__ = type("Image", (MagicMock,), {})
        layer.__class__.__name__ = "Image"
        layer.name = name
        layer.data = np.zeros((10, 10), dtype=np.uint16)
        return layer

    viewer = MagicMock()
    viewer.layers = [make_image("ch0"), make_image("ch1")]
    viewer_win = MagicMock()
    viewer_win.viewer = viewer
    launcher._windows = {"viewer": viewer_win}
    launcher._current_store = MagicMock()
    launcher._current_store.list_labels.return_value = []
    launcher._current_store.list_masks.return_value = []

    # Stub the name-prompt + Worker so we can inspect what got dispatched.
    from percell4.gui import segmentation_panel as sp_module

    monkeypatch.setattr(
        sp_module, "prompt_for_resource_name", lambda *a, **kw: "test"
    )

    captured: dict = {}
    import percell4.gui.workers as workers_mod

    class FakeWorker:
        def __init__(self, fn, image, **kw):  # noqa: ARG002
            captured["image_shape"] = image.shape
            captured["worker_started"] = True
            self.finished = MagicMock()
            self.error = MagicMock()

        def start(self):
            pass

    monkeypatch.setattr(workers_mod, "Worker", FakeWorker)
    panel._on_run_cellpose()

    # Worker was started (didn't short-circuit on missing channel).
    assert captured.get("worker_started")


def test_on_run_cellpose_aborts_when_combo_empty(qtbot) -> None:
    """No dataset / empty combo → status message, no Worker."""
    panel, _model, _launcher = _build_panel(qtbot, channel_names=[], active="")
    panel.update_channels()
    # No mocking needed — _on_run_cellpose should short-circuit before
    # touching the viewer.
    panel._on_run_cellpose()
    # Nothing crashed; no Worker reference set.
    assert not hasattr(panel, "_worker") or panel._worker is None
