"""Tests for the FLIM panel channel-override combo (U2).

Mirrors the Grouped Thresholding pattern at
``gui/grouped_seg_panel.py:67-72, 168-189``. The combo is a local
override — picking a value does not write back to
``session.active_channel`` — and re-seeds on dataset change via
``Event.DATASET_CHANGED`` and on napari layer-selection events via
the launcher wire.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from qtpy.QtWidgets import QComboBox


def _build_panel(qtbot, *, channel_names=None, active="ch0", viewer_win=None):
    """Construct a FlimPanel with mocked dependencies + optional session metadata."""
    from percell4.domain.dataset import DatasetHandle
    from percell4.interfaces.gui.task_panels.flim_panel import FlimPanel
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

    panel = FlimPanel(
        data_model=model,
        get_repo=lambda: MagicMock(),
        get_viewer_window=lambda: viewer_win,
        get_phasor_window=lambda: None,
        get_active_seg_labels=lambda: None,
        show_window=lambda _name: None,
        show_status=lambda _msg: None,
    )
    qtbot.addWidget(panel)
    return panel, model


def test_panel_renders_channel_combo_at_top(qtbot) -> None:
    """FLIM panel now has a _channel_combo QComboBox."""
    panel, _model = _build_panel(qtbot, channel_names=["ch0", "ch1"], active="ch0")
    assert hasattr(panel, "_channel_combo")
    assert isinstance(panel._channel_combo, QComboBox)


def test_combo_populates_from_session_on_construction(qtbot) -> None:
    """update_channels is called in __init__ so the combo is populated as
    soon as the panel is shown into an already-loaded dataset."""
    panel, _model = _build_panel(
        qtbot, channel_names=["ch0", "ch1", "ch2"], active="ch1"
    )
    items = [
        panel._channel_combo.itemText(i)
        for i in range(panel._channel_combo.count())
    ]
    assert items == ["ch0", "ch1", "ch2"]
    assert panel._channel_combo.currentText() == "ch1"


def test_combo_falls_back_to_viewer_layers_when_metadata_empty(qtbot) -> None:
    """If session has no channel_names, populate from viewer Image layers
    (mirrors grouped_seg_panel.update_channels fallback)."""
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

    panel, _model = _build_panel(qtbot, channel_names=[], viewer_win=viewer_win)
    items = [
        panel._channel_combo.itemText(i)
        for i in range(panel._channel_combo.count())
    ]
    assert items == ["layer_a", "layer_b"]


def test_no_dataset_no_viewer_keeps_combo_empty(qtbot) -> None:
    """Defensive: when neither path yields channels, combo stays empty."""
    panel, _model = _build_panel(qtbot, channel_names=[], viewer_win=None)
    assert panel._channel_combo.count() == 0
    assert panel._get_active_channel() is None


def test_get_active_channel_reads_from_combo_after_override(qtbot) -> None:
    """User overrides combo; _get_active_channel returns the override,
    not session.active_channel."""
    panel, model = _build_panel(
        qtbot, channel_names=["ch0", "ch1", "ch2"], active="ch0"
    )
    panel._channel_combo.setCurrentText("ch2")
    assert panel._get_active_channel() == "ch2"
    # Session active_channel is unchanged — override is local.
    assert model.session.active_channel == "ch0"


def test_dataset_changed_event_re_seeds_combo(qtbot) -> None:
    """Event.DATASET_CHANGED fires update_channels via the subscription."""
    from percell4.application.session import Event
    from percell4.domain.dataset import DatasetHandle

    panel, model = _build_panel(qtbot, channel_names=["ch0", "ch1"], active="ch0")
    assert panel._channel_combo.count() == 2

    # Replace the dataset with a different channel set.
    new_handle = DatasetHandle(
        path=Path("/tmp/other.h5"),
        metadata={"channel_names": ["ch99"]},
    )
    model.session.set_dataset(new_handle)
    # set_dataset emits DATASET_CHANGED; the subscription should re-seed.
    items = [
        panel._channel_combo.itemText(i)
        for i in range(panel._channel_combo.count())
    ]
    assert items == ["ch99"]


def test_get_active_channel_returns_none_when_combo_empty(qtbot) -> None:
    """_get_active_channel preserves None semantics for empty combo."""
    panel, _model = _build_panel(qtbot, channel_names=[])
    assert panel._get_active_channel() is None
