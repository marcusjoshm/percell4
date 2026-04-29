"""Tests for the napari multi-label selection tool.

The pure-Python state class (`StagingBuffer`) is unit-tested directly.
The Qt `MultiLabelSelectController` is tested with minimal fakes for
the `StagedRenderer`, `SelectionSink`, and `ToolLock` Protocols — no
real napari Viewer or QApplication needed for most paths.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from percell4.gui.multi_select import (
    MultiLabelSelectController,
    StagingBuffer,
)

# ── StagingBuffer ─────────────────────────────────────────────


class TestStagingBuffer:
    def test_empty_pre_fill(self) -> None:
        buf = StagingBuffer(initial_ids=frozenset())
        assert buf.current == set()
        assert buf.snapshot() == frozenset()
        assert buf.is_dirty() is False

    def test_pre_fill_does_not_share_state(self) -> None:
        initial = frozenset({1, 2, 3})
        buf = StagingBuffer(initial_ids=initial)
        buf.toggle(4)
        # initial_ids is frozen, so it can't leak; current is a new set
        assert buf.current == {1, 2, 3, 4}
        assert buf.initial_ids == frozenset({1, 2, 3})

    def test_toggle_adds_new_label(self) -> None:
        buf = StagingBuffer(initial_ids=frozenset())
        buf.toggle(5)
        assert buf.current == {5}
        assert buf.is_dirty() is True

    def test_toggle_removes_existing_label(self) -> None:
        buf = StagingBuffer(initial_ids=frozenset({1, 2, 3}))
        buf.toggle(2)
        assert buf.current == {1, 3}
        assert buf.is_dirty() is True

    def test_toggle_twice_is_symmetric(self) -> None:
        buf = StagingBuffer(initial_ids=frozenset())
        buf.toggle(7)
        buf.toggle(7)
        assert buf.current == set()
        assert buf.is_dirty() is False

    def test_snapshot_returns_frozenset(self) -> None:
        buf = StagingBuffer(initial_ids=frozenset())
        buf.toggle(1)
        snap = buf.snapshot()
        assert isinstance(snap, frozenset)
        # Mutating current after snapshot does not affect the snapshot.
        buf.toggle(2)
        assert snap == frozenset({1})

    def test_is_dirty_false_when_same_as_initial(self) -> None:
        buf = StagingBuffer(initial_ids=frozenset({1, 2}))
        assert buf.is_dirty() is False
        buf.toggle(3)
        assert buf.is_dirty() is True
        buf.toggle(3)
        assert buf.is_dirty() is False

    def test_is_dirty_detects_equal_but_different_mutations(self) -> None:
        """Remove one, add a different one — dirty even though count is unchanged."""
        buf = StagingBuffer(initial_ids=frozenset({1, 2}))
        buf.toggle(2)  # remove
        buf.toggle(3)  # add different
        assert buf.current == {1, 3}
        assert buf.is_dirty() is True


# ── MultiLabelSelectController (Qt-side, with fakes) ─────────


def _make_fake_renderer(*, alive: bool = True, labels_layer=None):
    """Return a MagicMock conforming to StagedRenderer."""
    renderer = MagicMock()
    renderer.is_viewer_alive.return_value = alive
    renderer.active_labels_layer_or_none.return_value = (
        labels_layer if labels_layer is not None else _make_fake_labels_layer()
    )
    return renderer


def _make_fake_labels_layer():
    """Return a MagicMock that acts like a napari Labels layer."""
    layer = MagicMock()
    layer.mode = "paint"  # arbitrary prior mode to verify restore
    layer.mouse_drag_callbacks = []
    return layer


def _make_fake_data_model(selected_ids=None):
    model = MagicMock()
    # selected_ids is a property — use PropertyMock if strict, but plain
    # attribute set works for MagicMock.
    model.selected_ids = list(selected_ids) if selected_ids is not None else []
    return model


def _make_fake_launcher(*, workflow_locked: bool = False):
    launcher = MagicMock()
    launcher.is_workflow_locked = workflow_locked
    return launcher


class TestControllerConstruction:
    def test_pre_fills_buffer_from_current_selection(self, qtbot) -> None:
        renderer = _make_fake_renderer()
        model = _make_fake_data_model(selected_ids=[1, 2, 3])
        launcher = _make_fake_launcher()

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        assert ctrl._buffer.initial_ids == frozenset({1, 2, 3})
        assert ctrl._buffer.current == {1, 2, 3}
        assert ctrl._buffer.is_dirty() is False

    def test_empty_selection_pre_fill(self, qtbot) -> None:
        renderer = _make_fake_renderer()
        model = _make_fake_data_model(selected_ids=[])
        launcher = _make_fake_launcher()

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        assert ctrl._buffer.initial_ids == frozenset()
        assert ctrl._buffer.current == set()


class TestShowGuards:
    def test_show_refuses_when_workflow_locked(self, qtbot) -> None:
        renderer = _make_fake_renderer()
        model = _make_fake_data_model()
        launcher = _make_fake_launcher(workflow_locked=True)

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        assert ctrl.show() is False
        # No install happened.
        launcher.set_workflow_locked.assert_not_called()

    def test_show_refuses_when_viewer_dead(self, qtbot) -> None:
        renderer = _make_fake_renderer(alive=False)
        model = _make_fake_data_model()
        launcher = _make_fake_launcher()

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        assert ctrl.show() is False
        launcher.set_workflow_locked.assert_not_called()

    def test_show_refuses_when_no_labels_layer(self, qtbot) -> None:
        renderer = _make_fake_renderer()
        renderer.active_labels_layer_or_none.return_value = None
        model = _make_fake_data_model()
        launcher = _make_fake_launcher()

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        assert ctrl.show() is False
        launcher.set_workflow_locked.assert_not_called()


class TestInstallTeardown:
    """Install + teardown interactions with the renderer/layer/launcher."""

    def test_show_installs_in_documented_order(self, qtbot) -> None:
        layer = _make_fake_labels_layer()
        renderer = _make_fake_renderer(labels_layer=layer)
        model = _make_fake_data_model(selected_ids=[1, 2])
        launcher = _make_fake_launcher()

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        assert ctrl.show() is True
        qtbot.addWidget(ctrl._window)

        # Assertions in documented order:
        renderer.suspend_selected_label_forwarding.assert_called_once()
        assert layer.mode == "pan_zoom"
        renderer.add_staged_overlay.assert_called_once_with(frozenset({1, 2}))
        assert len(layer.mouse_drag_callbacks) == 1
        launcher.set_workflow_locked.assert_called_once_with(True)

    def test_cancel_teardown_calls_all_restorations(self, qtbot) -> None:
        layer = _make_fake_labels_layer()
        layer.mode = "paint"
        renderer = _make_fake_renderer(labels_layer=layer)
        model = _make_fake_data_model(selected_ids=[1])
        launcher = _make_fake_launcher()

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        ctrl.show()
        qtbot.addWidget(ctrl._window)
        ctrl.cancel()

        # Teardown effects:
        assert len(layer.mouse_drag_callbacks) == 0
        assert layer.mode == "paint"  # Restored
        renderer.remove_staged_overlay.assert_called_once()
        renderer.resume_selected_label_forwarding.assert_called_once()
        launcher.set_workflow_locked.assert_any_call(False)
        # Domain state NOT touched.
        model.set_selection.assert_not_called()
        assert ctrl._torn_down is True

    def test_cancel_is_idempotent(self, qtbot) -> None:
        layer = _make_fake_labels_layer()
        renderer = _make_fake_renderer(labels_layer=layer)
        model = _make_fake_data_model()
        launcher = _make_fake_launcher()

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        ctrl.show()
        qtbot.addWidget(ctrl._window)
        ctrl.cancel()
        ctrl.cancel()  # Second call must be a no-op

        renderer.remove_staged_overlay.assert_called_once()
        renderer.resume_selected_label_forwarding.assert_called_once()

    def test_accept_commits_and_tears_down(self, qtbot) -> None:
        layer = _make_fake_labels_layer()
        renderer = _make_fake_renderer(labels_layer=layer)
        model = _make_fake_data_model(selected_ids=[1])
        launcher = _make_fake_launcher()

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        ctrl.show()
        qtbot.addWidget(ctrl._window)
        ctrl.toggle(5)
        ctrl.accept()

        # Selection was committed with the staged snapshot (order-insensitive).
        model.set_selection.assert_called_once()
        committed = model.set_selection.call_args.args[0]
        assert set(committed) == {1, 5}
        assert isinstance(committed, list)
        # Teardown still happened.
        renderer.remove_staged_overlay.assert_called_once()
        launcher.set_workflow_locked.assert_any_call(False)
        assert ctrl._torn_down is True

    def test_accept_after_no_changes_still_commits_snapshot(self, qtbot) -> None:
        """If the user hits Accept without changes, we still call
        set_selection — Session's own no-op guard (session.py:144-148)
        handles the idempotency. This test just documents the contract."""
        layer = _make_fake_labels_layer()
        renderer = _make_fake_renderer(labels_layer=layer)
        model = _make_fake_data_model(selected_ids=[1, 2])
        launcher = _make_fake_launcher()

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        ctrl.show()
        qtbot.addWidget(ctrl._window)
        ctrl.accept()

        model.set_selection.assert_called_once()
        committed = model.set_selection.call_args.args[0]
        assert set(committed) == {1, 2}


class TestToggleRefresh:
    def test_toggle_after_teardown_is_noop(self, qtbot) -> None:
        layer = _make_fake_labels_layer()
        renderer = _make_fake_renderer(labels_layer=layer)
        model = _make_fake_data_model()
        launcher = _make_fake_launcher()

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        ctrl.show()
        qtbot.addWidget(ctrl._window)
        ctrl.cancel()
        ctrl.toggle(42)
        assert 42 not in ctrl._buffer.current

    def test_do_refresh_early_returns_when_torn_down(self, qtbot) -> None:
        layer = _make_fake_labels_layer()
        renderer = _make_fake_renderer(labels_layer=layer)
        model = _make_fake_data_model()
        launcher = _make_fake_launcher()

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        ctrl.show()
        qtbot.addWidget(ctrl._window)
        # Simulate the race: toggle schedules a refresh, then we tear
        # down synchronously; if the QTimer were to fire late, _do_refresh
        # must still early-return.
        ctrl._buffer.toggle(99)
        ctrl.cancel()
        # Manually invoke the refresh callback post-teardown.
        renderer.update_staged_overlay.reset_mock()
        ctrl._do_refresh()
        renderer.update_staged_overlay.assert_not_called()

    def test_do_refresh_early_returns_when_viewer_not_alive(
        self, qtbot
    ) -> None:
        layer = _make_fake_labels_layer()
        renderer = _make_fake_renderer(labels_layer=layer)
        model = _make_fake_data_model()
        launcher = _make_fake_launcher()

        ctrl = MultiLabelSelectController(renderer, model, launcher)
        ctrl.show()
        qtbot.addWidget(ctrl._window)
        ctrl._buffer.toggle(99)

        # Simulate the viewer dying mid-session.
        renderer.is_viewer_alive.return_value = False
        renderer.update_staged_overlay.reset_mock()
        ctrl._do_refresh()
        renderer.update_staged_overlay.assert_not_called()


class TestClickCallback:
    """The callback must be left-click only, bounded to non-zero labels."""

    def _install_and_get_callback(self, qtbot, layer, staged_initial=None):
        renderer = _make_fake_renderer(labels_layer=layer)
        model = _make_fake_data_model(selected_ids=staged_initial or [])
        launcher = _make_fake_launcher()
        ctrl = MultiLabelSelectController(renderer, model, launcher)
        ctrl.show()
        qtbot.addWidget(ctrl._window)
        cb = layer.mouse_drag_callbacks[0]
        return ctrl, cb

    def _fake_event(self, *, button: int = 1, value):
        event = MagicMock()
        event.button = button
        event.position = (0, 0)
        event.view_direction = None
        event.dims_displayed = (0, 1)
        # layer_.get_value is what the callback actually uses; we stub
        # by patching the layer's return.
        return event

    def test_left_click_on_label_toggles_into_staging(self, qtbot) -> None:
        layer = _make_fake_labels_layer()
        layer.get_value.return_value = 7
        ctrl, cb = self._install_and_get_callback(qtbot, layer)

        cb(layer, self._fake_event(button=1, value=7))
        assert 7 in ctrl._buffer.current

    def test_middle_click_is_ignored(self, qtbot) -> None:
        layer = _make_fake_labels_layer()
        layer.get_value.return_value = 7
        ctrl, cb = self._install_and_get_callback(qtbot, layer)

        cb(layer, self._fake_event(button=2, value=7))
        assert 7 not in ctrl._buffer.current

    def test_right_click_is_ignored(self, qtbot) -> None:
        layer = _make_fake_labels_layer()
        layer.get_value.return_value = 7
        ctrl, cb = self._install_and_get_callback(qtbot, layer)

        cb(layer, self._fake_event(button=3, value=7))
        assert 7 not in ctrl._buffer.current

    def test_click_on_background_label_zero_is_noop(self, qtbot) -> None:
        layer = _make_fake_labels_layer()
        layer.get_value.return_value = 0
        ctrl, cb = self._install_and_get_callback(qtbot, layer)

        cb(layer, self._fake_event(button=1, value=0))
        assert ctrl._buffer.current == set()

    def test_click_outside_layer_bounds_returns_none_is_noop(
        self, qtbot
    ) -> None:
        layer = _make_fake_labels_layer()
        layer.get_value.return_value = None
        ctrl, cb = self._install_and_get_callback(qtbot, layer)

        cb(layer, self._fake_event(button=1, value=None))
        assert ctrl._buffer.current == set()

    def test_get_value_raising_does_not_crash_the_callback(
        self, qtbot
    ) -> None:
        layer = _make_fake_labels_layer()
        layer.get_value.side_effect = RuntimeError("napari internal hiccup")
        ctrl, cb = self._install_and_get_callback(qtbot, layer)

        # Must not propagate.
        cb(layer, self._fake_event(button=1, value=None))
        assert ctrl._buffer.current == set()

    def test_click_on_staged_label_toggles_out(self, qtbot) -> None:
        layer = _make_fake_labels_layer()
        layer.get_value.return_value = 3
        ctrl, cb = self._install_and_get_callback(
            qtbot, layer, staged_initial=[3]
        )
        assert 3 in ctrl._buffer.current  # Pre-filled

        cb(layer, self._fake_event(button=1, value=3))
        assert 3 not in ctrl._buffer.current

    def test_numpy_integer_label_coerces_to_int(self, qtbot) -> None:
        """napari often returns numpy integer types from get_value."""
        import numpy as np

        layer = _make_fake_labels_layer()
        layer.get_value.return_value = np.int64(11)
        ctrl, cb = self._install_and_get_callback(qtbot, layer)

        cb(layer, self._fake_event(button=1, value=np.int64(11)))
        assert 11 in ctrl._buffer.current
        # Stored as plain int, not numpy.
        assert all(type(x) is int for x in ctrl._buffer.current)
