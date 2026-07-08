"""Tests for the grouped thresholding name prompt (U3).

The grouped `_on_run` flow now prompts the user for a mask name via the
shared helper instead of auto-deriving `grouped_{channel}_{metric}` and
showing the inline Yes/No/Cancel overwrite dialog.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.gui import grouped_seg_panel as gsp_module


def _build_panel(qtbot, *, channel="ch0", active_seg="seg", existing_masks=None):
    """Construct a GroupedSegPanel with mocked store + viewer."""
    from percell4.gui.grouped_seg_panel import GroupedSegPanel
    from percell4.model import CellDataModel

    existing_masks = existing_masks or []

    model = CellDataModel()
    model.session.set_active_channel(channel)
    model.session.set_active_segmentation(active_seg)

    # Stub store
    store = MagicMock()
    store.list_masks.return_value = list(existing_masks)

    # Stub viewer window with the channel + segmentation layers present
    def make_layer(name, klass_name):
        layer = MagicMock()
        layer.__class__ = type(klass_name, (MagicMock,), {})
        layer.__class__.__name__ = klass_name
        layer.name = name
        layer.data = np.zeros((10, 10), dtype=np.uint16)
        return layer

    channel_layer = make_layer(channel, "Image")
    seg_layer = make_layer(active_seg, "Labels")

    viewer = MagicMock()
    viewer.layers = [channel_layer, seg_layer]
    viewer_win = MagicMock()
    viewer_win.viewer = viewer

    panel = GroupedSegPanel(
        data_model=model,
        get_store=lambda: store,
        get_viewer_window=lambda: viewer_win,
        show_status=lambda _msg: None,
    )
    qtbot.addWidget(panel)
    # The Run handler reads session.active_channel (the SessionWindow is
    # the only Selector site). The model fixture above already seeds it.
    return panel, store


def test_on_run_calls_helper_with_grouped_default_and_existing_masks(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`existing_names` passed to the helper is `store.list_masks()`;
    default is 'grouped'."""
    panel, _store = _build_panel(
        qtbot, existing_masks=["grouped", "phasor_mask"]
    )

    captured: dict[str, Any] = {}

    def fake_prompt(parent, *, title, label, default, existing_names):  # noqa: ARG001
        captured["default"] = default
        captured["existing"] = set(existing_names)
        return None  # cancel; we only care about args

    monkeypatch.setattr(gsp_module, "prompt_for_resource_name", fake_prompt)
    panel._on_run()

    assert captured["default"] == "grouped"
    assert captured["existing"] == {"grouped", "phasor_mask"}


def test_on_run_aborts_when_user_cancels_prompt(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel returns None → no measurement worker, no grouping worker."""
    panel, _store = _build_panel(qtbot)

    monkeypatch.setattr(
        gsp_module, "prompt_for_resource_name", lambda *a, **kw: None
    )

    # If we got past the cancel, _auto_measure_then_group or _run_grouping
    # would be called. Spy on both to confirm neither fires.
    panel._auto_measure_then_group = MagicMock()
    panel._run_grouping = MagicMock()
    panel._on_run()
    panel._auto_measure_then_group.assert_not_called()
    panel._run_grouping.assert_not_called()


def test_on_run_threads_chosen_name_into_grouping(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chosen name is passed downstream to _auto_measure_then_group /
    _run_grouping as the mask_name argument."""
    panel, _store = _build_panel(qtbot)

    monkeypatch.setattr(
        gsp_module, "prompt_for_resource_name", lambda *a, **kw: "my_grouped"
    )

    captured_name: dict[str, Any] = {}

    def fake_measure(channel, channel_image, seg_labels, metric, sigma, mask_name):  # noqa: ARG001
        captured_name["name"] = mask_name

    def fake_group(channel, channel_image, seg_labels, metric, sigma, mask_name):  # noqa: ARG001
        captured_name["name"] = mask_name

    panel._auto_measure_then_group = fake_measure
    panel._run_grouping = fake_group
    panel._on_run()

    assert captured_name.get("name") == "my_grouped"


def test_on_run_inline_overwrite_dialog_is_gone(qtbot, monkeypatch) -> None:
    """The old QMessageBox.question Yes/No/Cancel overwrite dialog must
    no longer fire — the helper's refuse-and-re-prompt replaces it."""
    panel, _store = _build_panel(qtbot, existing_masks=["grouped"])

    monkeypatch.setattr(
        gsp_module, "prompt_for_resource_name", lambda *a, **kw: "grouped_v2"
    )

    # QMessageBox.question should not be called from _on_run anymore. Patch it
    # on its qtpy source (the panel no longer imports QMessageBox at all now
    # that the inline overwrite dialog is gone).
    from qtpy.QtWidgets import QMessageBox

    qm_calls = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **kw: qm_calls.append(1)),
    )

    panel._auto_measure_then_group = MagicMock()
    panel._run_grouping = MagicMock()
    panel._on_run()
    assert qm_calls == []
