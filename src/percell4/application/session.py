"""Observable application state hub.

The Session is the single source of truth for what's currently loaded,
selected, and active. Peer views subscribe to its events; use cases
mutate it after completing work. No Qt, no napari — pure Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

from percell4.domain.dataset import CellId, DatasetHandle, LayerName


class Event(Enum):
    """Session events that observers can subscribe to."""

    DATASET_CHANGED = auto()
    SELECTION_CHANGED = auto()
    FILTER_CHANGED = auto()
    ACTIVE_SEGMENTATION_CHANGED = auto()
    ACTIVE_MASK_CHANGED = auto()
    MEASUREMENTS_UPDATED = auto()


# Callback type: no arguments, no return value
Observer = Callable[[], None]

# Unsubscribe function
Unsubscribe = Callable[[], None]


@dataclass
class Session:
    """Observable state hub for the application layer.

    Owns current dataset, selection, filter, and active layer references.
    Use cases mutate it; peer views subscribe to changes.
    """

    _dataset: DatasetHandle | None = field(default=None, repr=False)
    _active_segmentation: LayerName | None = field(default=None, repr=False)
    _active_mask: LayerName | None = field(default=None, repr=False)
    _selection: frozenset[CellId] = field(default_factory=frozenset, repr=False)
    _filter_ids: frozenset[CellId] | None = field(default=None, repr=False)

    _observers: dict[Event, list[Observer]] = field(
        default_factory=lambda: {e: [] for e in Event}, repr=False
    )

    # ── Event machinery ──────────────────────────────────────

    def subscribe(self, event: Event, cb: Observer) -> Unsubscribe:
        """Register an observer. Returns an unsubscribe function."""
        self._observers[event].append(cb)
        return lambda: self._observers[event].remove(cb)

    def _emit(self, event: Event) -> None:
        """Notify all observers of an event. Safe to unsubscribe during iteration."""
        for cb in list(self._observers[event]):
            cb()

    # ── Queries ──────────────────────────────────────────────

    @property
    def dataset(self) -> DatasetHandle | None:
        return self._dataset

    @property
    def selection(self) -> frozenset[CellId]:
        return self._selection

    @property
    def filter_ids(self) -> frozenset[CellId] | None:
        return self._filter_ids

    @property
    def is_filtered(self) -> bool:
        return self._filter_ids is not None

    @property
    def active_segmentation(self) -> LayerName | None:
        return self._active_segmentation

    @property
    def active_mask(self) -> LayerName | None:
        return self._active_mask

    # ── Mutations ────────────────────────────────────────────

    def set_dataset(self, handle: DatasetHandle | None) -> None:
        """Set the active dataset. Resets selection, filter, and active layers."""
        self._dataset = handle
        self._active_segmentation = None
        self._active_mask = None
        self._selection = frozenset()
        self._filter_ids = None
        self._emit(Event.DATASET_CHANGED)

    def set_selection(self, ids: frozenset[CellId]) -> None:
        if ids == self._selection:
            return
        self._selection = ids
        self._emit(Event.SELECTION_CHANGED)

    def set_filter(self, ids: frozenset[CellId] | None) -> None:
        self._filter_ids = ids
        self._selection = frozenset()
        self._emit(Event.FILTER_CHANGED)
        self._emit(Event.SELECTION_CHANGED)

    def set_active_segmentation(self, name: LayerName | None) -> None:
        if name == self._active_segmentation:
            return
        self._active_segmentation = name
        self._emit(Event.ACTIVE_SEGMENTATION_CHANGED)

    def set_active_mask(self, name: LayerName | None) -> None:
        if name == self._active_mask:
            return
        self._active_mask = name
        self._emit(Event.ACTIVE_MASK_CHANGED)

    def clear(self) -> None:
        """Reset all state. Called when closing a dataset."""
        self._dataset = None
        self._active_segmentation = None
        self._active_mask = None
        self._selection = frozenset()
        self._filter_ids = None
        self._emit(Event.DATASET_CHANGED)
