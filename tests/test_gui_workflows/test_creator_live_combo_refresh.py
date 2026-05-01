"""Anchor regression tests for AE3 (live combo refresh on Creator write)
and AE4 (Creator auto-select).

Covers C3: new layers from Creators don't appear in Data tab combos
until app restart. After the U7 fix, every Creator fires
session.refresh_resource_lists() before set_active_*, so subscribers
re-list immediately.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from percell4.application.session import Event, Session
from percell4.domain.dataset import DatasetHandle


def test_AE3_refresh_resource_lists_emits_event_for_each_kind():
    """Covers AE3. Creators that update the inventory via
    session.refresh_resource_lists() emit the matching list event so
    subscribers re-list."""
    session = Session()
    session.set_dataset(DatasetHandle(
        path="/tmp/x.h5",
        metadata={"channel_names": ["mNG"], "mask_names": []},
    ))

    events = []
    session.subscribe(Event.MASK_LIST_CHANGED, lambda: events.append("mask"))
    session.subscribe(Event.SEGMENTATION_LIST_CHANGED, lambda: events.append("seg"))
    session.subscribe(Event.CHANNEL_LIST_CHANGED, lambda: events.append("ch"))

    # Simulate a Creator writing a new mask and refreshing the inventory
    session.refresh_resource_lists(mask_names=["SG_mask"])
    assert events == ["mask"]
    assert session.dataset.metadata["mask_names"] == ["SG_mask"]

    events.clear()
    session.refresh_resource_lists(segmentation_names=["cellpose"])
    assert events == ["seg"]

    events.clear()
    session.refresh_resource_lists(channel_names=["mNG", "CA-SiR"])
    assert events == ["ch"]


def test_AE4_creator_auto_selects_after_list_event():
    """Covers AE4. The contract is: Creator fires list event, then
    set_active_*. Subscribers see the new name in the inventory before
    they look it up in the active-changed handler."""
    session = Session()
    session.set_dataset(DatasetHandle(
        path="/tmp/x.h5",
        metadata={"channel_names": ["mNG"], "mask_names": []},
    ))

    event_log = []
    session.subscribe(Event.MASK_LIST_CHANGED, lambda: event_log.append("mask_list"))
    session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: event_log.append("active_mask"))

    # Simulate Creator: refresh inventory, then auto-select
    session.refresh_resource_lists(mask_names=["new_mask"])
    session.set_active_mask("new_mask")

    # Ordering is load-bearing: list event must fire before active event
    assert event_log == ["mask_list", "active_mask"]
    assert session.active_mask == "new_mask"


def test_AE3_repeated_creator_writes_each_emit_event():
    """Two Creators in sequence each fire the list event."""
    session = Session()
    session.set_dataset(DatasetHandle(
        path="/tmp/x.h5",
        metadata={"channel_names": ["mNG"], "mask_names": []},
    ))
    events = []
    session.subscribe(Event.MASK_LIST_CHANGED, lambda: events.append(1))

    session.refresh_resource_lists(mask_names=["m1"])
    session.refresh_resource_lists(mask_names=["m1", "m2"])
    assert events == [1, 1]
    assert session.dataset.metadata["mask_names"] == ["m1", "m2"]
