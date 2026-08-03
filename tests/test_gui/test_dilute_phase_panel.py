"""U7 tests: DilutePhaseMaskPanel Qt widget.

Verifies the panel's validation pipeline, setup → iteration block
transition, and button → controller method wiring. The
:class:`DilutePhaseMaskController` is patched at the panel module level
in the Start-flow tests so we can assert the controller's methods get
invoked without spinning up a real round.

Scenarios covered:

* AE-3: Start enabled when all prerequisites are present.
* AE-3: Start disabled when ``active_segmentation`` is None.
* AE-4: Start disabled when the name collides with an existing
  ``/masks/<name>``; status message includes a suggested fallback.
* Edge: name collision with ``/labels/<name>``.
* Edge: name collision with a channel name.
* Happy path: ``qtbot.keyClicks`` typing fires revalidation and Start.
* Happy path: Start click disables the setup block widgets, reveals
  the iteration block, and instantiates the controller.
* Happy path: Run-another-round / Done / Cancel each invoke the
  controller method exactly once.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle

# ── Fixtures ────────────────────────────────────────────────


@pytest.fixture
def fake_viewer():
    """Minimal viewer-window fake: ``viewer.layers`` list of fake layers."""

    class _Layers(list):
        pass

    class _FakeImageLayer:
        def __init__(self, name, data):
            self.name = name
            self.data = data
            # Class name is what GroupedSegPanel-style capture checks.
            self.__class__.__name__ = "Image"

    layers = _Layers()
    layers.append(_FakeImageLayer("ch0", np.ones((16, 16), dtype=np.float32) * 5.0))

    viewer = MagicMock()
    viewer.layers = layers
    viewer_win = MagicMock()
    viewer_win.viewer = viewer
    viewer_win.existing_viewer = viewer
    viewer_win.add_mask = MagicMock()
    viewer_win.show = MagicMock()
    return viewer_win


@pytest.fixture
def seg_labels_array():
    """16x16 with two labelled cells so measure_cells has multiple groups."""
    arr = np.zeros((16, 16), dtype=np.int32)
    arr[2:6, 2:6] = 1
    arr[10:14, 10:14] = 2
    return arr


@pytest.fixture
def handle():
    return DatasetHandle(
        path=Path("/tmp/dilute_panel_test.h5"),
        metadata={"channel_names": ["ch0"]},
    )


@pytest.fixture
def session(handle):
    """Real Session with channel + segmentation primed.

    The Session is lightweight and avoids the brittleness of MagicMock-ing
    the event-subscription surface.
    """
    s = Session()
    s.set_dataset(handle)
    s.set_active_channel("ch0")
    s.set_active_segmentation("seg")
    return s


@pytest.fixture
def store():
    """MagicMock store with empty mask/label lists by default."""
    s = MagicMock()
    s.list_masks.return_value = []
    s.list_labels.return_value = []
    return s


@pytest.fixture
def data_model():
    return MagicMock()


def _build_panel(
    qtbot,
    *,
    store,
    data_model,
    session,
    viewer_win,
    seg_labels_array,
    handle,
):
    """Spin up a panel + qtbot-register it."""
    from percell4.gui.workflows.dilute_phase.panel import DilutePhaseMaskPanel

    panel = DilutePhaseMaskPanel(
        parent=None,
        store=store,
        data_model=data_model,
        session=session,
        viewer_win=viewer_win,
        get_active_seg_labels=lambda: seg_labels_array,
        handle=handle,
    )
    qtbot.addWidget(panel)
    return panel


# ── AE-3: all prerequisites set → Start enabled ─────────────


def test_start_enabled_when_all_prereqs_set(
    qtbot, store, data_model, session, fake_viewer, seg_labels_array, handle,
):
    panel = _build_panel(
        qtbot,
        store=store, data_model=data_model, session=session,
        viewer_win=fake_viewer, seg_labels_array=seg_labels_array,
        handle=handle,
    )
    assert panel._start_btn.isEnabled(), (
        "Default mask name 'dilute_phase' + valid session → Start enabled"
    )
    assert panel._status_label.text() == ""


# ── AE-3: no active segmentation → Start disabled ───────────


def test_start_disabled_when_no_active_segmentation(
    qtbot, store, data_model, session, fake_viewer, seg_labels_array, handle,
):
    session.set_active_segmentation(None)
    panel = _build_panel(
        qtbot,
        store=store, data_model=data_model, session=session,
        viewer_win=fake_viewer, seg_labels_array=seg_labels_array,
        handle=handle,
    )
    assert not panel._start_btn.isEnabled()
    assert "segmentation" in panel._status_label.text().lower()


def test_start_disabled_when_no_active_channel(
    qtbot, store, data_model, session, fake_viewer, seg_labels_array, handle,
):
    session.set_active_channel(None)
    panel = _build_panel(
        qtbot,
        store=store, data_model=data_model, session=session,
        viewer_win=fake_viewer, seg_labels_array=seg_labels_array,
        handle=handle,
    )
    assert not panel._start_btn.isEnabled()
    assert "channel" in panel._status_label.text().lower()


def test_start_disabled_when_seg_labels_unreadable(
    qtbot, store, data_model, session, fake_viewer, handle,
):
    """``get_active_seg_labels`` returns None → inline status surfaces it."""
    from percell4.gui.workflows.dilute_phase.panel import DilutePhaseMaskPanel

    panel = DilutePhaseMaskPanel(
        parent=None,
        store=store,
        data_model=data_model,
        session=session,
        viewer_win=fake_viewer,
        get_active_seg_labels=lambda: None,
        handle=handle,
    )
    qtbot.addWidget(panel)

    assert not panel._start_btn.isEnabled()
    assert "labels" in panel._status_label.text().lower()


# ── AE-4: mask-name collisions ──────────────────────────────


def test_start_disabled_when_name_collides_with_mask(
    qtbot, store, data_model, session, fake_viewer, seg_labels_array, handle,
):
    """`/masks/dilute_v1` exists; entering 'dilute_v1' disables Start."""
    store.list_masks.return_value = ["dilute_v1"]
    panel = _build_panel(
        qtbot,
        store=store, data_model=data_model, session=session,
        viewer_win=fake_viewer, seg_labels_array=seg_labels_array,
        handle=handle,
    )
    panel._name_edit.setText("dilute_v1")

    assert not panel._start_btn.isEnabled()
    status = panel._status_label.text()
    assert "mask" in status.lower()
    # Suggested fallback in the status text.
    assert "dilute_v1_2" in status


def test_start_disabled_when_name_collides_with_label(
    qtbot, store, data_model, session, fake_viewer, seg_labels_array, handle,
):
    store.list_labels.return_value = ["dilute_v1"]
    panel = _build_panel(
        qtbot,
        store=store, data_model=data_model, session=session,
        viewer_win=fake_viewer, seg_labels_array=seg_labels_array,
        handle=handle,
    )
    panel._name_edit.setText("dilute_v1")

    assert not panel._start_btn.isEnabled()
    assert "label" in panel._status_label.text().lower()


def test_start_disabled_when_name_collides_with_channel(
    qtbot, store, data_model, session, fake_viewer, seg_labels_array, handle,
):
    """Channel-name collision is read from handle.metadata['channel_names']."""
    panel = _build_panel(
        qtbot,
        store=store, data_model=data_model, session=session,
        viewer_win=fake_viewer, seg_labels_array=seg_labels_array,
        handle=handle,  # channel_names=["ch0"]
    )
    panel._name_edit.setText("ch0")

    assert not panel._start_btn.isEnabled()
    assert "channel" in panel._status_label.text().lower()


# ── Happy path: typing fires revalidation ───────────────────


def test_typing_fires_revalidation_and_enables_start(
    qtbot, store, data_model, session, fake_viewer, seg_labels_array, handle,
):
    """`qtbot.keyClicks` exercises the textChanged → revalidate signal path.

    Per `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`
    we cover the signal wiring with at least one test that types rather
    than `setText`.
    """
    store.list_masks.return_value = ["existing"]
    panel = _build_panel(
        qtbot,
        store=store, data_model=data_model, session=session,
        viewer_win=fake_viewer, seg_labels_array=seg_labels_array,
        handle=handle,
    )
    # Start by typing the colliding name — Start should be disabled.
    panel._name_edit.clear()
    qtbot.keyClicks(panel._name_edit, "existing")
    assert not panel._start_btn.isEnabled()

    # Now switch to a unique name; Start re-enables.
    panel._name_edit.clear()
    qtbot.keyClicks(panel._name_edit, "dilute_v1")
    assert panel._start_btn.isEnabled()


# ── Happy path: Start click collapses setup, reveals iteration ──


def test_start_click_transitions_to_iteration_block(
    qtbot, store, data_model, session, fake_viewer, seg_labels_array, handle,
    monkeypatch,
):
    """Click Start → setup block disabled, iteration block visible,
    controller constructed, start_round called.

    Uses the widget's programmatic ``.click()`` entry point (equivalent
    to a synthetic mouse-up release on the button) — the U6 controller
    is patched at the panel-module level so we never spin a real round.
    """
    from percell4.gui.workflows.dilute_phase import panel as panel_mod

    mock_controller = MagicMock()
    mock_controller_cls = MagicMock(return_value=mock_controller)
    monkeypatch.setattr(
        panel_mod, "DilutePhaseMaskController", mock_controller_cls,
    )

    panel = _build_panel(
        qtbot,
        store=store, data_model=data_model, session=session,
        viewer_win=fake_viewer, seg_labels_array=seg_labels_array,
        handle=handle,
    )
    panel.show()
    qtbot.waitExposed(panel)

    # Before Start: iteration block is hidden.
    assert not panel._iteration_block.isVisible()

    panel._start_btn.click()

    # After Start: iteration block visible; setup edits disabled; Start hidden.
    assert panel._iteration_block.isVisible()
    assert not panel._name_edit.isEnabled()
    assert not panel._dilation_spin.isEnabled()
    assert not panel._start_btn.isVisible()

    # Controller was constructed once with the captured snapshot.
    mock_controller_cls.assert_called_once()
    construction_kwargs = mock_controller_cls.call_args.kwargs
    assert construction_kwargs["final_mask_name"] == "dilute_phase"
    assert construction_kwargs["dilation_radius_px"] == 5
    assert construction_kwargs["handle"] is handle
    assert construction_kwargs["channel"] == "ch0"
    # Round 1 kicked off automatically.
    mock_controller.start_round.assert_called_once()


# ── Happy path: iteration-block buttons forward to controller ──


def _start_workflow(panel, monkeypatch):
    """Helper: patch the controller, click Start, return the mock controller."""
    from percell4.gui.workflows.dilute_phase import panel as panel_mod

    mock_controller = MagicMock()
    mock_controller_cls = MagicMock(return_value=mock_controller)
    monkeypatch.setattr(panel_mod, "DilutePhaseMaskController", mock_controller_cls)

    panel._start_btn.click()
    # Reset start_round so the iteration-block tests get a clean count.
    mock_controller.start_round.reset_mock()
    return mock_controller


def test_run_another_round_calls_controller_start_round(
    qtbot, store, data_model, session, fake_viewer, seg_labels_array, handle,
    monkeypatch,
):
    panel = _build_panel(
        qtbot,
        store=store, data_model=data_model, session=session,
        viewer_win=fake_viewer, seg_labels_array=seg_labels_array,
        handle=handle,
    )
    mock_controller = _start_workflow(panel, monkeypatch)

    panel._run_btn.click()

    mock_controller.start_round.assert_called_once()


def test_done_button_calls_controller_finish(
    qtbot, store, data_model, session, fake_viewer, seg_labels_array, handle,
    monkeypatch,
):
    panel = _build_panel(
        qtbot,
        store=store, data_model=data_model, session=session,
        viewer_win=fake_viewer, seg_labels_array=seg_labels_array,
        handle=handle,
    )
    mock_controller = _start_workflow(panel, monkeypatch)

    panel._done_btn.click()

    mock_controller.finish.assert_called_once()


def test_cancel_button_calls_controller_cancel(
    qtbot, store, data_model, session, fake_viewer, seg_labels_array, handle,
    monkeypatch,
):
    panel = _build_panel(
        qtbot,
        store=store, data_model=data_model, session=session,
        viewer_win=fake_viewer, seg_labels_array=seg_labels_array,
        handle=handle,
    )
    mock_controller = _start_workflow(panel, monkeypatch)

    panel._cancel_btn.click()

    mock_controller.cancel.assert_called_once()
