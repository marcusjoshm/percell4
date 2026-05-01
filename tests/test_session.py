"""Tests for the application Session."""

from __future__ import annotations

import pandas as pd

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

    def test_set_dataset_emits_per_state_events_when_clearing(self):
        """Switching datasets emits per-state events for slots that actually changed.

        Regression: peer views (e.g., the phasor plot's mask filter) subscribe
        to ACTIVE_MASK_CHANGED, not DATASET_CHANGED. Without these emits, a
        cached mask from the previous dataset stays applied to the new
        dataset's phasor data.
        """
        session = Session()
        session._active_segmentation = "old_seg"
        session._active_mask = "old_mask"
        session._filter_ids = frozenset({1, 2})
        session._selection = frozenset({1, 2, 3})

        events = []
        session.subscribe(
            Event.ACTIVE_MASK_CHANGED, lambda: events.append("mask")
        )
        session.subscribe(
            Event.ACTIVE_SEGMENTATION_CHANGED, lambda: events.append("seg")
        )
        session.subscribe(Event.FILTER_CHANGED, lambda: events.append("filter"))
        session.subscribe(
            Event.SELECTION_CHANGED, lambda: events.append("selection")
        )

        session.set_dataset(DatasetHandle(path="/tmp/test.h5"))

        assert "mask" in events
        assert "seg" in events
        assert "filter" in events
        assert "selection" in events

    def test_set_dataset_no_spurious_events_when_already_clean(self):
        """Switching datasets when nothing was set doesn't fire stale events."""
        session = Session()
        events = []
        session.subscribe(
            Event.ACTIVE_MASK_CHANGED, lambda: events.append("mask")
        )
        session.subscribe(
            Event.ACTIVE_SEGMENTATION_CHANGED, lambda: events.append("seg")
        )
        session.subscribe(Event.FILTER_CHANGED, lambda: events.append("filter"))
        session.subscribe(
            Event.SELECTION_CHANGED, lambda: events.append("selection")
        )

        session.set_dataset(DatasetHandle(path="/tmp/test.h5"))

        assert events == []  # Nothing was set, nothing changed

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


class TestSessionActiveChannel:
    """Verify active channel tracking."""

    def test_set_active_channel_emits_event(self):
        session = Session()
        events = []
        session.subscribe(Event.ACTIVE_CHANNEL_CHANGED, lambda: events.append(1))
        session.set_active_channel("GFP")
        assert events == [1]
        assert session.active_channel == "GFP"

    def test_same_channel_no_event(self):
        session = Session()
        session.set_active_channel("GFP")
        events = []
        session.subscribe(Event.ACTIVE_CHANNEL_CHANGED, lambda: events.append(1))
        session.set_active_channel("GFP")
        assert events == []

    def test_set_dataset_auto_selects_first_channel(self):
        from pathlib import Path

        session = Session()
        handle = DatasetHandle(
            path=Path("/tmp/test.h5"),
            metadata={"channel_names": ["GFP", "DAPI"]},
        )
        session.set_dataset(handle)
        assert session.active_channel == "GFP"

    def test_set_dataset_no_channels_leaves_none(self):
        from pathlib import Path

        session = Session()
        handle = DatasetHandle(
            path=Path("/tmp/test.h5"),
            metadata={},
        )
        session.set_dataset(handle)
        assert session.active_channel is None

    def test_clear_resets_channel(self):
        session = Session()
        session.set_active_channel("GFP")
        session.clear()
        assert session.active_channel is None


class TestSessionMeasurements:
    """Verify measurements DataFrame support."""

    def test_initial_df_is_empty(self):
        session = Session()
        assert session.df.empty

    def test_set_measurements_emits_event(self):
        session = Session()
        events = []
        session.subscribe(Event.MEASUREMENTS_UPDATED, lambda: events.append(1))
        df = pd.DataFrame({"label": [1, 2, 3], "area": [10, 20, 30]})
        session.set_measurements(df)
        assert events == [1]
        assert len(session.df) == 3

    def test_filtered_df_with_no_filter(self):
        session = Session()
        df = pd.DataFrame({"label": [1, 2, 3], "area": [10, 20, 30]})
        session.set_measurements(df)
        assert len(session.filtered_df) == 3

    def test_filtered_df_with_filter(self):
        session = Session()
        df = pd.DataFrame({"label": [1, 2, 3], "area": [10, 20, 30]})
        session.set_measurements(df)
        session.set_filter(frozenset({1, 3}))
        assert len(session.filtered_df) == 2
        assert set(session.filtered_df["label"]) == {1, 3}

    def test_set_measurements_prunes_stale_filter(self):
        session = Session()
        session.set_filter(frozenset({1, 2, 99}))
        df = pd.DataFrame({"label": [1, 2, 3]})
        session.set_measurements(df)
        # 99 was pruned because it's not in the new df
        assert 99 not in session.filter_ids

    def test_set_measurements_prunes_stale_selection(self):
        session = Session()
        session._selection = frozenset({1, 2, 99})
        df = pd.DataFrame({"label": [1, 2, 3]})
        session.set_measurements(df)
        assert 99 not in session.selection

    def test_selected_ids_returns_list(self):
        session = Session()
        session.set_selection(frozenset({3, 1, 2}))
        ids = session.selected_ids
        assert isinstance(ids, list)
        assert set(ids) == {1, 2, 3}

    def test_set_dataset_clears_measurements(self):
        session = Session()
        session.set_measurements(pd.DataFrame({"label": [1]}))
        session.set_dataset(DatasetHandle(path="/tmp/test.h5"))
        assert session.df.empty

    def test_clear_clears_measurements(self):
        session = Session()
        session.set_measurements(pd.DataFrame({"label": [1]}))
        session.clear()
        assert session.df.empty


class TestSessionListEvents:
    """Verify the three resource-list events plumb through correctly."""

    def test_channel_list_event_subscribable(self):
        session = Session()
        calls = []
        session.subscribe(Event.CHANNEL_LIST_CHANGED, lambda: calls.append(1))
        session._emit(Event.CHANNEL_LIST_CHANGED)
        assert calls == [1]

    def test_segmentation_list_event_subscribable(self):
        session = Session()
        calls = []
        session.subscribe(Event.SEGMENTATION_LIST_CHANGED, lambda: calls.append(1))
        session._emit(Event.SEGMENTATION_LIST_CHANGED)
        assert calls == [1]

    def test_mask_list_event_subscribable(self):
        session = Session()
        calls = []
        session.subscribe(Event.MASK_LIST_CHANGED, lambda: calls.append(1))
        session._emit(Event.MASK_LIST_CHANGED)
        assert calls == [1]

    def test_list_events_independent_of_active_events(self):
        session = Session()
        list_calls = []
        active_calls = []
        session.subscribe(Event.MASK_LIST_CHANGED, lambda: list_calls.append(1))
        session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: active_calls.append(1))
        session._emit(Event.MASK_LIST_CHANGED)
        assert list_calls == [1]
        assert active_calls == []


class TestSessionAutoSelect:
    """Verify set_dataset auto-selects first available channel/seg/mask (R3)."""

    def test_auto_selects_all_three_slots(self):
        session = Session()
        handle = DatasetHandle(
            path="/tmp/x.h5",
            metadata={
                "channel_names": ["mNG", "CA-SiR"],
                "segmentation_names": ["cellpose", "manual"],
                "mask_names": ["SG_mask"],
            },
        )
        session.set_dataset(handle)
        assert session.active_channel == "mNG"
        assert session.active_segmentation == "cellpose"
        assert session.active_mask == "SG_mask"

    def test_empty_segmentation_list_yields_none(self):
        session = Session()
        handle = DatasetHandle(
            path="/tmp/x.h5",
            metadata={
                "channel_names": ["mNG"],
                "segmentation_names": [],
                "mask_names": ["SG_mask"],
            },
        )
        session.set_dataset(handle)
        assert session.active_segmentation is None
        assert session.active_mask == "SG_mask"

    def test_empty_mask_list_yields_none(self):
        session = Session()
        handle = DatasetHandle(
            path="/tmp/x.h5",
            metadata={
                "channel_names": ["mNG"],
                "segmentation_names": ["cellpose"],
                "mask_names": [],
            },
        )
        session.set_dataset(handle)
        assert session.active_mask is None
        assert session.active_segmentation == "cellpose"

    def test_missing_keys_treated_as_empty_no_exception(self):
        session = Session()
        handle = DatasetHandle(path="/tmp/x.h5", metadata={"channel_names": ["mNG"]})
        session.set_dataset(handle)
        assert session.active_channel == "mNG"
        assert session.active_segmentation is None
        assert session.active_mask is None

    def test_set_dataset_emits_three_list_events(self):
        session = Session()
        list_events = []
        session.subscribe(Event.CHANNEL_LIST_CHANGED, lambda: list_events.append("ch"))
        session.subscribe(Event.SEGMENTATION_LIST_CHANGED, lambda: list_events.append("seg"))
        session.subscribe(Event.MASK_LIST_CHANGED, lambda: list_events.append("mask"))
        session.set_dataset(DatasetHandle(path="/tmp/x.h5", metadata={}))
        assert list_events == ["ch", "seg", "mask"]

    def test_set_dataset_none_emits_three_list_events(self):
        session = Session()
        session.set_dataset(DatasetHandle(path="/tmp/x.h5", metadata={
            "channel_names": ["mNG"], "segmentation_names": ["cellpose"],
            "mask_names": ["SG_mask"],
        }))
        list_events = []
        session.subscribe(Event.CHANNEL_LIST_CHANGED, lambda: list_events.append("ch"))
        session.subscribe(Event.SEGMENTATION_LIST_CHANGED, lambda: list_events.append("seg"))
        session.subscribe(Event.MASK_LIST_CHANGED, lambda: list_events.append("mask"))
        session.set_dataset(None)
        assert list_events == ["ch", "seg", "mask"]
        assert session.active_channel is None
        assert session.active_segmentation is None
        assert session.active_mask is None

    def test_active_changed_fires_on_none_to_value_transition(self):
        """The C2 fix: ACTIVE_*_CHANGED must fire when prev was None and new is auto-selected."""
        session = Session()
        events = []
        session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: events.append("mask"))
        # No prior dataset, _active_mask starts at None, new is "SG_mask"
        session.set_dataset(DatasetHandle(path="/tmp/x.h5", metadata={
            "channel_names": ["mNG"], "mask_names": ["SG_mask"],
        }))
        assert events == ["mask"]

    def test_active_changed_fires_on_value_to_none_transition(self):
        session = Session()
        session.set_dataset(DatasetHandle(path="/tmp/x.h5", metadata={
            "channel_names": ["mNG"], "mask_names": ["SG_mask"],
        }))
        events = []
        session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: events.append("mask"))
        session.set_dataset(DatasetHandle(path="/tmp/y.h5", metadata={
            "channel_names": ["mNG"], "mask_names": [],
        }))
        assert events == ["mask"]

    def test_active_changed_does_not_fire_when_value_unchanged(self):
        """If both datasets have mask 'SG_mask', no spurious ACTIVE_MASK_CHANGED."""
        session = Session()
        session.set_dataset(DatasetHandle(path="/tmp/x.h5", metadata={
            "channel_names": ["mNG"], "mask_names": ["SG_mask"],
        }))
        events = []
        session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: events.append("mask"))
        # Second dataset: same mask name first
        session.set_dataset(DatasetHandle(path="/tmp/y.h5", metadata={
            "channel_names": ["mNG"], "mask_names": ["SG_mask"],
        }))
        assert events == []  # name unchanged

    def test_clear_emits_three_list_events(self):
        session = Session()
        session.set_dataset(DatasetHandle(path="/tmp/x.h5", metadata={
            "channel_names": ["mNG"], "mask_names": ["SG_mask"],
        }))
        list_events = []
        session.subscribe(Event.CHANNEL_LIST_CHANGED, lambda: list_events.append("ch"))
        session.subscribe(Event.SEGMENTATION_LIST_CHANGED, lambda: list_events.append("seg"))
        session.subscribe(Event.MASK_LIST_CHANGED, lambda: list_events.append("mask"))
        session.clear()
        assert list_events == ["ch", "seg", "mask"]


class TestSessionRefreshResourceLists:
    """Verify Session.refresh_resource_lists() API (R2)."""

    def test_no_dataset_is_noop(self):
        session = Session()
        events = []
        session.subscribe(Event.MASK_LIST_CHANGED, lambda: events.append(1))
        session.refresh_resource_lists(mask_names=["new_mask"])
        assert events == []

    def test_updates_metadata_and_fires_event(self):
        session = Session()
        handle = DatasetHandle(
            path="/tmp/x.h5",
            metadata={"channel_names": ["ch1"], "mask_names": []},
        )
        session.set_dataset(handle)
        events = []
        session.subscribe(Event.MASK_LIST_CHANGED, lambda: events.append(1))
        session.refresh_resource_lists(mask_names=["SG_mask"])
        assert events == [1]
        assert session.dataset.metadata["mask_names"] == ["SG_mask"]

    def test_only_specified_kinds_fire(self):
        session = Session()
        session.set_dataset(DatasetHandle(path="/tmp/x.h5", metadata={}))
        ch_events = []
        seg_events = []
        mask_events = []
        session.subscribe(Event.CHANNEL_LIST_CHANGED, lambda: ch_events.append(1))
        session.subscribe(Event.SEGMENTATION_LIST_CHANGED, lambda: seg_events.append(1))
        session.subscribe(Event.MASK_LIST_CHANGED, lambda: mask_events.append(1))
        session.refresh_resource_lists(mask_names=["m1"])
        assert ch_events == []
        assert seg_events == []
        assert mask_events == [1]

    def test_three_kinds_at_once(self):
        session = Session()
        session.set_dataset(DatasetHandle(path="/tmp/x.h5", metadata={}))
        events = []
        session.subscribe(Event.CHANNEL_LIST_CHANGED, lambda: events.append("ch"))
        session.subscribe(Event.SEGMENTATION_LIST_CHANGED, lambda: events.append("seg"))
        session.subscribe(Event.MASK_LIST_CHANGED, lambda: events.append("mask"))
        session.refresh_resource_lists(
            channel_names=["c1"], segmentation_names=["s1"], mask_names=["m1"]
        )
        assert events == ["ch", "seg", "mask"]
        assert session.dataset.metadata["channel_names"] == ["c1"]
        assert session.dataset.metadata["segmentation_names"] == ["s1"]
        assert session.dataset.metadata["mask_names"] == ["m1"]

    def test_none_means_skip(self):
        session = Session()
        session.set_dataset(DatasetHandle(
            path="/tmp/x.h5",
            metadata={"mask_names": ["original"]},
        ))
        session.refresh_resource_lists(channel_names=["c1"])
        # mask_names unchanged
        assert session.dataset.metadata["mask_names"] == ["original"]
