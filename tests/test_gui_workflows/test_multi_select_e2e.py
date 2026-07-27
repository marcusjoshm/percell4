"""Headless end-to-end test for the napari multi-label selection tool.

Spins up a real QApplication (via ``qtbot``), a real ``napari.Viewer`` with
``show=False``, a real ``Session`` + ``CellDataModel`` bridge, and a real
``MultiLabelSelectController``. Exercises the full stage → accept and
stage → cancel paths and asserts the canonical selection state in
``Session`` is updated correctly.

Unlike the MagicMock-driven tests in ``test_multi_select.py``, this test
verifies the real wiring between the controller, the napari viewer's
overlay layer, and the ``SELECTION_CHANGED`` event ripple.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.application.session import Event, Session
from percell4.config.viewer_presets import (
    STAGED_OVERLAY_LAYER_NAME as _OVERLAY_LAYER_NAME,
)
from percell4.gui.multi_select import MultiLabelSelectController
from percell4.gui.viewer import ViewerWindow
from percell4.model import CellDataModel

# Builds a real napari viewer, so this module carries the ``napari_viewer``
# marker: skipped by default (see pyproject addopts), run explicitly on CI.
pytestmark = pytest.mark.napari_viewer


class _FakeLauncher:
    """Minimal ``ToolLock`` Protocol impl — mirrors LauncherWindow's
    workflow-lock surface without pulling in the whole launcher."""

    def __init__(self) -> None:
        self._locked = False

    @property
    def is_workflow_locked(self) -> bool:
        return self._locked

    def set_workflow_locked(self, locked: bool) -> None:
        self._locked = locked


@pytest.fixture
def viewer_harness(qtbot, sample_labels: np.ndarray):
    """Real napari.Viewer + ViewerWindow + Session + CellDataModel +
    launcher, with a labels layer already added and marked active.

    Yields ``(session, data_model, viewer_win, launcher)``.
    Teardown closes the napari viewer so Qt objects are collected.
    """
    session = Session()
    data_model = CellDataModel(session=session)
    viewer_win = ViewerWindow(data_model)

    # Force napari.Viewer construction. show=False → no window pops up.
    viewer_win._ensure_viewer()
    viewer = viewer_win.viewer
    viewer.add_labels(sample_labels, name="seg")
    data_model.set_active_segmentation("seg")

    launcher = _FakeLauncher()

    yield session, data_model, viewer_win, launcher

    # Teardown — tolerate a torn-down viewer
    try:
        viewer.close()
    except Exception:
        pass


def test_stage_and_accept_commits_selection(viewer_harness, qtbot) -> None:
    session, data_model, viewer_win, launcher = viewer_harness
    assert session.selection == frozenset()

    events: list[None] = []
    unsubscribe = session.subscribe(Event.SELECTION_CHANGED, lambda: events.append(None))

    # Set a non-default mode so we can verify restoration after teardown.
    primary = viewer_win.active_labels_layer_or_none()
    assert primary is not None
    primary.mode = "pick"
    assert str(primary.mode) == "pick"

    ctrl = MultiLabelSelectController(
        viewer_win=viewer_win,
        data_model=data_model,
        launcher=launcher,
    )
    assert ctrl.show() is True
    qtbot.addWidget(ctrl._window)

    # Overlay layer present, forwarding suspended, lock acquired, mode forced to pan_zoom
    assert _OVERLAY_LAYER_NAME in viewer_win.viewer.layers
    assert viewer_win._selected_label_forwarding_suspended is True
    assert launcher.is_workflow_locked is True
    assert str(primary.mode) == "pan_zoom"

    # Stage 1, 3, then toggle 1 off
    ctrl.toggle(1)
    ctrl.toggle(3)
    ctrl.toggle(1)
    assert ctrl._buffer.current == {3}

    # Commit
    ctrl.accept()

    # Canonical selection state updated
    assert session.selection == frozenset({3})
    assert data_model.selected_ids == [3]

    # Ripple fired exactly once for the non-empty commit
    assert len(events) == 1

    # Teardown: overlay gone, lock released, forwarding restored, mode restored
    assert _OVERLAY_LAYER_NAME not in viewer_win.viewer.layers
    assert launcher.is_workflow_locked is False
    assert viewer_win._selected_label_forwarding_suspended is False
    assert str(primary.mode) == "pick"  # restored to the pre-show mode
    assert ctrl._torn_down is True

    unsubscribe()


def test_stage_and_cancel_discards_staging(viewer_harness, qtbot) -> None:
    session, data_model, viewer_win, launcher = viewer_harness

    # Pre-populate selection to verify cancel preserves it
    session.set_selection(frozenset({2, 4}))
    assert data_model.selected_ids == sorted([2, 4]) or set(data_model.selected_ids) == {2, 4}

    events_before = []
    unsubscribe = session.subscribe(Event.SELECTION_CHANGED, lambda: events_before.append(None))

    ctrl = MultiLabelSelectController(
        viewer_win=viewer_win,
        data_model=data_model,
        launcher=launcher,
    )
    assert ctrl.show() is True
    qtbot.addWidget(ctrl._window)

    # Staging pre-filled from current selection
    assert ctrl._buffer.current == {2, 4}

    # Stage a change then cancel
    ctrl.toggle(5)
    assert ctrl._buffer.current == {2, 4, 5}
    ctrl.cancel()

    # Session selection unchanged by cancel
    assert session.selection == frozenset({2, 4})
    # No new SELECTION_CHANGED events fired during cancel
    assert events_before == []

    # Teardown invariants
    assert _OVERLAY_LAYER_NAME not in viewer_win.viewer.layers
    assert launcher.is_workflow_locked is False
    assert viewer_win._selected_label_forwarding_suspended is False
    assert ctrl._torn_down is True

    unsubscribe()


def test_show_refuses_when_no_labels_layer(qtbot) -> None:
    """If the viewer has no labels layer, show() returns False and the
    tool does not install any state on the viewer or launcher."""
    session = Session()
    data_model = CellDataModel(session=session)
    viewer_win = ViewerWindow(data_model)
    viewer_win._ensure_viewer()  # real viewer, but no layers added
    launcher = _FakeLauncher()

    ctrl = MultiLabelSelectController(
        viewer_win=viewer_win,
        data_model=data_model,
        launcher=launcher,
    )
    try:
        assert ctrl.show() is False
        assert launcher.is_workflow_locked is False
        assert viewer_win._selected_label_forwarding_suspended is False
    finally:
        try:
            viewer_win.viewer.close()
        except Exception:
            pass


def test_show_refuses_when_launcher_already_locked(viewer_harness, qtbot) -> None:
    """If the workflow lock is already held, show() returns False."""
    _, data_model, viewer_win, launcher = viewer_harness
    launcher.set_workflow_locked(True)

    ctrl = MultiLabelSelectController(
        viewer_win=viewer_win,
        data_model=data_model,
        launcher=launcher,
    )
    assert ctrl.show() is False
    # Lock was already held; tool did not flip it or acquire its own state
    assert launcher.is_workflow_locked is True
    assert viewer_win._selected_label_forwarding_suspended is False
    assert _OVERLAY_LAYER_NAME not in viewer_win.viewer.layers


def test_coalesced_refresh_updates_overlay_colormap(viewer_harness, qtbot) -> None:
    """Toggling multiple labels rapidly schedules a single refresh that
    repaints the overlay layer's colormap to include all staged ids."""
    _, data_model, viewer_win, launcher = viewer_harness

    ctrl = MultiLabelSelectController(
        viewer_win=viewer_win,
        data_model=data_model,
        launcher=launcher,
    )
    assert ctrl.show() is True
    qtbot.addWidget(ctrl._window)

    # Burst of toggles
    ctrl.toggle(1)
    ctrl.toggle(2)
    ctrl.toggle(4)

    # Spin the event loop so the QTimer(0) fires once
    qtbot.wait(50)

    assert ctrl._buffer.current == {1, 2, 4}
    overlay = viewer_win.viewer.layers[_OVERLAY_LAYER_NAME]
    cmap = overlay.colormap.color_dict
    # All staged ids appear in the colormap with a non-transparent color
    for lid in (1, 2, 4):
        assert lid in cmap, f"Expected label {lid} in overlay colormap"

    ctrl.cancel()
