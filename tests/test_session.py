"""Tests for the application Session."""

from __future__ import annotations

from percell4.application.session import Event, Session
from percell4.domain.dataset import DatasetHandle


class TestSessionObserver:
    """Verify the observer/event pattern."""

    def test_subscribe_and_emit(self):
        session = Session()
        calls = []
        session.subscribe(Event.DATASET_CHANGED, lambda: calls.append("ds"))
        session._emit(Event.DATASET_CHANGED)
        assert calls == ["ds"]

    def test_unsubscribe(self):
        session = Session()
        calls = []
        unsub = session.subscribe(Event.DATASET_CHANGED, lambda: calls.append("ds"))
        unsub()
        session._emit(Event.DATASET_CHANGED)
        assert calls == []

    def test_unsubscribe_during_emit_is_safe(self):
        session = Session()
        calls = []

        def unsub_self():
            calls.append("self")
            unsub()

        unsub = session.subscribe(Event.DATASET_CHANGED, unsub_self)
        session.subscribe(Event.DATASET_CHANGED, lambda: calls.append("other"))
        session._emit(Event.DATASET_CHANGED)
        assert calls == ["self", "other"]

    def test_multiple_events_independent(self):
        session = Session()
        ds_calls = []
        sel_calls = []
        session.subscribe(Event.DATASET_CHANGED, lambda: ds_calls.append(1))
        session.subscribe(Event.SELECTION_CHANGED, lambda: sel_calls.append(1))
        session._emit(Event.DATASET_CHANGED)
        assert len(ds_calls) == 1
        assert len(sel_calls) == 0


class TestSessionDataset:
    """Verify dataset state management."""

    def test_initial_state(self):
        session = Session()
        assert session.dataset is None
        assert session.selection == frozenset()
        assert session.filter_ids is None
        assert session.active_segmentation is None
        assert session.active_mask is None

    def test_set_dataset_emits_event(self):
        session = Session()
        events = []
        session.subscribe(Event.DATASET_CHANGED, lambda: events.append(1))
        handle = DatasetHandle(path="/tmp/test.h5")
        session.set_dataset(handle)
        assert events == [1]
        assert session.dataset is handle

    def test_set_dataset_resets_state(self):
        session = Session()
        session._selection = frozenset({1, 2, 3})
        session._active_segmentation = "old_seg"
        session._active_mask = "old_mask"
        session._filter_ids = frozenset({1, 2})

        session.set_dataset(DatasetHandle(path="/tmp/test.h5"))

        assert session.selection == frozenset()
        assert session.active_segmentation is None
        assert session.active_mask is None
        assert session.filter_ids is None

    def test_clear_resets_everything(self):
        session = Session()
        session.set_dataset(DatasetHandle(path="/tmp/test.h5"))
        session.set_selection(frozenset({1, 2}))
        session.set_active_segmentation("seg")

        session.clear()

        assert session.dataset is None
        assert session.selection == frozenset()
        assert session.active_segmentation is None


class TestSessionSelection:
    """Verify selection state management."""

    def test_set_selection_emits_event(self):
        session = Session()
        events = []
        session.subscribe(Event.SELECTION_CHANGED, lambda: events.append(1))
        session.set_selection(frozenset({1, 2}))
        assert events == [1]
        assert session.selection == frozenset({1, 2})

    def test_set_same_selection_no_event(self):
        session = Session()
        session.set_selection(frozenset({1, 2}))
        events = []
        session.subscribe(Event.SELECTION_CHANGED, lambda: events.append(1))
        session.set_selection(frozenset({1, 2}))
        assert events == []

    def test_selection_is_immutable(self):
        session = Session()
        ids = frozenset({1, 2, 3})
        session.set_selection(ids)
        assert session.selection is ids


class TestSessionFilter:
    """Verify filter state management."""

    def test_set_filter_emits_events(self):
        session = Session()
        filter_events = []
        sel_events = []
        session.subscribe(Event.FILTER_CHANGED, lambda: filter_events.append(1))
        session.subscribe(Event.SELECTION_CHANGED, lambda: sel_events.append(1))
        session.set_filter(frozenset({1, 2}))
        assert filter_events == [1]
        assert sel_events == [1]  # filter clears selection

    def test_set_filter_clears_selection(self):
        session = Session()
        session.set_selection(frozenset({1, 2, 3}))
        session.set_filter(frozenset({1, 2}))
        assert session.selection == frozenset()

    def test_is_filtered(self):
        session = Session()
        assert not session.is_filtered
        session.set_filter(frozenset({1}))
        assert session.is_filtered
        session.set_filter(None)
        assert not session.is_filtered


class TestSessionActiveLayers:
    """Verify active segmentation/mask tracking."""

    def test_set_active_segmentation(self):
        session = Session()
        events = []
        session.subscribe(Event.ACTIVE_SEGMENTATION_CHANGED, lambda: events.append(1))
        session.set_active_segmentation("cellpose")
        assert events == [1]
        assert session.active_segmentation == "cellpose"

    def test_same_segmentation_no_event(self):
        session = Session()
        session.set_active_segmentation("cellpose")
        events = []
        session.subscribe(Event.ACTIVE_SEGMENTATION_CHANGED, lambda: events.append(1))
        session.set_active_segmentation("cellpose")
        assert events == []

    def test_set_active_mask(self):
        session = Session()
        events = []
        session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: events.append(1))
        session.set_active_mask("threshold")
        assert events == [1]
        assert session.active_mask == "threshold"
