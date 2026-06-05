"""Observable application state hub.

The Session is the single source of truth for what's currently loaded,
selected, and active. Peer views subscribe to its events; use cases
mutate it after completing work. No Qt, no napari — pure Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

import pandas as pd

from percell4.domain.dataset import CellId, ChannelName, DatasetHandle, LayerName


class Event(Enum):
    """Session events that observers can subscribe to."""

    DATASET_CHANGED = auto()
    SELECTION_CHANGED = auto()
    FILTER_CHANGED = auto()
    ACTIVE_SEGMENTATION_CHANGED = auto()
    ACTIVE_MASK_CHANGED = auto()
    ACTIVE_CHANNEL_CHANGED = auto()
    ACTIVE_BIN_CHANGED = auto()
    ACTIVE_TIMEPOINT_CHANGED = auto()
    MEASUREMENTS_UPDATED = auto()
    CHANNEL_LIST_CHANGED = auto()
    SEGMENTATION_LIST_CHANGED = auto()
    MASK_LIST_CHANGED = auto()


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
    _active_channel: ChannelName | None = field(default=None, repr=False)
    _active_bin: int = field(default=1, repr=False)
    _active_timepoint: int = field(default=0, repr=False)
    # Cross-window selection is GLOBAL BY LABEL (no timepoint dimension). This is
    # correct for a TRACKED segmentation, where the label value equals the track
    # id and is stable across frames, so selecting a cell highlights the same
    # track in every frame it exists. On an UNTRACKED per-frame stack (raw
    # run_cellpose_stack output) label numbers are independent per frame, so a
    # global selection cross-links unrelated physical cells across frames — peer
    # views stay frame-scoped (slider-follows-frame, U16/U17) to contain this.
    # A (label, timepoint) selection key is intentionally NOT added (large
    # blast radius on the selection contract, no concrete feature needs it).
    _selection: frozenset[CellId] = field(default_factory=frozenset, repr=False)
    _filter_ids: frozenset[CellId] | None = field(default=None, repr=False)
    _measurements: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)
    _filtered_df_cache: pd.DataFrame | None = field(default=None, repr=False)

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

    @property
    def active_channel(self) -> ChannelName | None:
        return self._active_channel

    @property
    def active_bin(self) -> int:
        """Session-level view bin (k >= 1).

        Acts as a read-time lens on the dataset: at ``active_bin > 1``,
        every store read downsamples by the per-kind rule (see
        ``DatasetStore.read_array``). On-disk arrays are unchanged --
        this is purely an observation setting that callers thread into
        their reads.

        Default is 1. ``set_dataset`` and ``clear`` reset it to 1.
        """
        return self._active_bin

    @property
    def n_timepoints(self) -> int:
        """Number of acquisition timepoints in the active dataset (>= 1).

        Read from the dataset's ``n_timepoints`` metadata. ``1`` for
        single-timepoint datasets and when no dataset is loaded.
        """
        if self._dataset is None:
            return 1
        return int(self._dataset.metadata.get("n_timepoints", 1) or 1)

    @property
    def active_timepoint(self) -> int:
        """Zero-based index of the currently displayed timepoint.

        A selection field (mutated only by the timepoint Selector — the
        napari dims slider). Range is ``[0, n_timepoints - 1]``; always 0
        for single-timepoint datasets. ``set_dataset`` and ``clear`` reset
        it to 0.
        """
        return self._active_timepoint

    @property
    def df(self) -> pd.DataFrame:
        """Current per-cell measurements. Read-only — do not modify."""
        return self._measurements

    @property
    def filtered_df(self) -> pd.DataFrame:
        """Filtered DataFrame (by filter_ids), or full df if no filter."""
        if self._filter_ids is None or self._measurements.empty:
            return self._measurements
        if "label" not in self._measurements.columns:
            return self._measurements
        if self._filtered_df_cache is None:
            self._filtered_df_cache = self._measurements[
                self._measurements["label"].isin(self._filter_ids)
            ]
        return self._filtered_df_cache

    @property
    def selected_ids(self) -> list[int]:
        """Selected cell IDs as a list (convenience for Qt code)."""
        return list(self._selection)

    # ── Mutations ────────────────────────────────────────────

    def set_dataset(self, handle: DatasetHandle | None) -> None:
        """Set the active dataset. Resets selection, filter, and measurements;
        auto-selects first available channel/segmentation/mask.

        Emit ordering: DATASET_CHANGED, then the three list events
        (CHANNEL_LIST_CHANGED, SEGMENTATION_LIST_CHANGED, MASK_LIST_CHANGED)
        so subscribers re-list before they look up the new active values,
        then per-slot ACTIVE_*_CHANGED for slots whose value transitioned
        (covers both prev-non-None-now-None and prev-None-now-auto-selected).
        """
        prev_channel = self._active_channel
        prev_segmentation = self._active_segmentation
        prev_mask = self._active_mask
        prev_filter = self._filter_ids
        prev_selection = self._selection
        prev_bin = self._active_bin
        prev_timepoint = self._active_timepoint

        self._dataset = handle
        self._selection = frozenset()
        self._filter_ids = None
        self._measurements = pd.DataFrame()
        self._filtered_df_cache = None
        self._active_bin = 1
        self._active_timepoint = 0
        if handle is not None:
            ch_names = list(handle.metadata.get("channel_names", []))
            seg_names = list(handle.metadata.get("segmentation_names", []))
            mask_names = list(handle.metadata.get("mask_names", []))
            self._active_channel = ch_names[0] if ch_names else None
            self._active_segmentation = seg_names[0] if seg_names else None
            self._active_mask = mask_names[0] if mask_names else None
        else:
            self._active_channel = None
            self._active_segmentation = None
            self._active_mask = None

        self._emit(Event.DATASET_CHANGED)
        self._emit(Event.CHANNEL_LIST_CHANGED)
        self._emit(Event.SEGMENTATION_LIST_CHANGED)
        self._emit(Event.MASK_LIST_CHANGED)
        if prev_channel != self._active_channel:
            self._emit(Event.ACTIVE_CHANNEL_CHANGED)
        if prev_segmentation != self._active_segmentation:
            self._emit(Event.ACTIVE_SEGMENTATION_CHANGED)
        if prev_mask != self._active_mask:
            self._emit(Event.ACTIVE_MASK_CHANGED)
        if prev_filter is not None:
            self._emit(Event.FILTER_CHANGED)
        if prev_selection:
            self._emit(Event.SELECTION_CHANGED)
        if prev_bin != 1:
            self._emit(Event.ACTIVE_BIN_CHANGED)
        if prev_timepoint != 0:
            self._emit(Event.ACTIVE_TIMEPOINT_CHANGED)

    def set_selection(self, ids: frozenset[CellId]) -> None:
        if ids == self._selection:
            return
        self._selection = ids
        self._emit(Event.SELECTION_CHANGED)

    def set_measurements(self, df: pd.DataFrame) -> None:
        """Replace the measurements DataFrame. Prunes stale filter/selection IDs."""
        self._measurements = df
        self._filtered_df_cache = None
        if "label" in df.columns and (self._filter_ids is not None or self._selection):
            valid = frozenset(df["label"].tolist())
            if self._filter_ids is not None:
                self._filter_ids = self._filter_ids & valid
            if self._selection:
                self._selection = self._selection & valid
        self._emit(Event.MEASUREMENTS_UPDATED)

    def set_filter(self, ids: frozenset[CellId] | None) -> None:
        self._filter_ids = ids
        self._filtered_df_cache = None
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

    def set_active_channel(self, name: ChannelName | None) -> None:
        if name == self._active_channel:
            return
        self._active_channel = name
        self._emit(Event.ACTIVE_CHANNEL_CHANGED)

    def set_active_bin(self, k: int) -> None:
        """Set the session view bin (1 <= k <= 16).

        Idempotent: no-op when the value is unchanged. Raises
        ``ValueError`` outside the [1, 16] range -- the upper bound
        mirrors the spinner range in SessionWindow and the importer
        path.
        """
        if not isinstance(k, int) or isinstance(k, bool):
            raise ValueError(f"active_bin must be an int in [1, 16], got {k!r}")
        if k < 1 or k > 16:
            raise ValueError(f"active_bin must be in [1, 16], got {k}")
        if k == self._active_bin:
            return
        self._active_bin = k
        self._emit(Event.ACTIVE_BIN_CHANGED)

    def set_active_timepoint(self, t: int) -> None:
        """Set the active timepoint index (0 <= t < n_timepoints).

        Idempotent: no-op when unchanged. Raises ``ValueError`` outside the
        ``[0, n_timepoints - 1]`` range. Mutated only by the timepoint
        Selector (the napari dims slider).
        """
        if not isinstance(t, int) or isinstance(t, bool):
            raise ValueError(f"active_timepoint must be an int, got {t!r}")
        n = self.n_timepoints
        if t < 0 or t >= n:
            raise ValueError(
                f"active_timepoint must be in [0, {n - 1}], got {t}"
            )
        if t == self._active_timepoint:
            return
        self._active_timepoint = t
        self._emit(Event.ACTIVE_TIMEPOINT_CHANGED)

    def refresh_resource_lists(
        self,
        *,
        channel_names: list[str] | None = None,
        segmentation_names: list[str] | None = None,
        mask_names: list[str] | None = None,
    ) -> None:
        """Update the dataset's resource inventory and emit list events.

        Called by Creators after writing a new channel / segmentation / mask
        so subscribers (combos, peer-view caches) can re-list. Each kwarg
        that is non-None replaces the corresponding entry in the current
        DatasetHandle.metadata and fires the matching list event. None
        means "skip this kind."

        No-op when no dataset is loaded.
        """
        if self._dataset is None:
            return
        md = self._dataset.metadata
        if channel_names is not None:
            md["channel_names"] = list(channel_names)
            self._emit(Event.CHANNEL_LIST_CHANGED)
        if segmentation_names is not None:
            md["segmentation_names"] = list(segmentation_names)
            self._emit(Event.SEGMENTATION_LIST_CHANGED)
        if mask_names is not None:
            md["mask_names"] = list(mask_names)
            self._emit(Event.MASK_LIST_CHANGED)

    def clear(self) -> None:
        """Reset all state. Called when closing a dataset."""
        prev_bin = self._active_bin
        prev_timepoint = self._active_timepoint
        self._dataset = None
        self._active_segmentation = None
        self._active_mask = None
        self._active_channel = None
        self._active_bin = 1
        self._active_timepoint = 0
        self._selection = frozenset()
        self._filter_ids = None
        self._measurements = pd.DataFrame()
        self._filtered_df_cache = None
        self._emit(Event.DATASET_CHANGED)
        self._emit(Event.CHANNEL_LIST_CHANGED)
        self._emit(Event.SEGMENTATION_LIST_CHANGED)
        self._emit(Event.MASK_LIST_CHANGED)
        if prev_bin != 1:
            self._emit(Event.ACTIVE_BIN_CHANGED)
        if prev_timepoint != 0:
            self._emit(Event.ACTIVE_TIMEPOINT_CHANGED)
