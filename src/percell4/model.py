"""Central data model for PerCell4.

CellDataModel is the communication hub — all windows connect to its signals.
The DataFrame is ephemeral: computed on the fly for the currently loaded dataset,
not persisted across datasets. Persistent measurements live in each .h5 file.

Convention: model.df for schema discovery (columns, types).
            model.filtered_df for data display (rows to show).

Thread safety: All mutations must occur on the main (GUI) thread. Worker threads
emit results via Qt signals which are marshaled to the main thread by AutoConnection.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from qtpy.QtCore import QObject, Signal


@dataclass
class StateChange:
    """Descriptor for what changed in a CellDataModel state transition.

    Carried by the ``state_changed`` signal so that each window can process
    all relevant changes in a defined order within a single handler call.
    """

    data: bool = False          # DataFrame was replaced
    selection: bool = False     # selected_ids changed
    filter: bool = False        # filtered_ids changed
    segmentation: bool = False  # active segmentation layer changed
    mask: bool = False          # active mask layer changed


class CellDataModel(QObject):
    """Holds per-cell measurements, selection, and filter state.

    All windows listen to signals on this object — they never talk to each other
    directly. Listeners get a read-only view of the DataFrame; they must not
    modify it.
    """

    state_changed = Signal(object)  # emits StateChange

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._df = pd.DataFrame()
        self._selected_ids: list[int] = []
        self._filtered_ids: set[int] | None = None  # None = no filter
        self._filtered_df_cache: pd.DataFrame | None = None
        self._active_segmentation: str = ""
        self._active_mask: str = ""

    @property
    def df(self) -> pd.DataFrame:
        """Current per-cell measurements. Read-only — do not modify."""
        return self._df

    @property
    def filtered_df(self) -> pd.DataFrame:
        """Return filtered DataFrame, or full DataFrame if no filter.

        Cached — invalidated by set_filter() and set_measurements().
        """
        if self._filtered_ids is None or self._df.empty or "label" not in self._df.columns:
            return self._df
        if self._filtered_df_cache is None:
            self._filtered_df_cache = self._df[
                self._df["label"].isin(self._filtered_ids)
            ]
        return self._filtered_df_cache

    @property
    def is_filtered(self) -> bool:
        """True when a cell filter is active."""
        return self._filtered_ids is not None

    @property
    def filtered_ids(self) -> set[int] | None:
        """Currently active filter IDs, or None if no filter."""
        return self._filtered_ids

    def set_filter(self, label_ids: list[int] | None) -> None:
        """Set filter. None clears. Also clears selection."""
        self._filtered_ids = set(label_ids) if label_ids is not None else None
        self._filtered_df_cache = None
        self._selected_ids = []
        self.state_changed.emit(StateChange(filter=True, selection=True))

    def set_measurements(self, df: pd.DataFrame) -> None:
        """Replace the measurements DataFrame and notify all listeners.

        Preserves filter and selection — cell IDs are stable (from segmentation
        labels). Prunes any stale IDs that no longer exist in the new DataFrame.
        """
        self._df = df
        self._filtered_df_cache = None
        # Prune stale IDs but preserve the user's filter/selection intent
        if self._filtered_ids is not None and "label" in df.columns:
            valid = set(df["label"].tolist())
            self._filtered_ids &= valid
        if self._selected_ids and "label" in df.columns:
            valid = set(df["label"].tolist())
            self._selected_ids = [s for s in self._selected_ids if s in valid]
        self.state_changed.emit(StateChange(data=True))

    def set_selection(self, label_ids: list[int]) -> None:
        """Update the selected cell IDs and notify all listeners."""
        self._selected_ids = list(label_ids)
        self.state_changed.emit(StateChange(selection=True))

    @property
    def selected_ids(self) -> list[int]:
        """Currently selected cell label IDs."""
        return self._selected_ids

    @property
    def active_segmentation(self) -> str:
        """Name of the currently active segmentation layer."""
        return self._active_segmentation

    def set_active_segmentation(self, name: str) -> None:
        """Set the active segmentation layer and notify all listeners."""
        if name != self._active_segmentation:
            self._active_segmentation = name
            self.state_changed.emit(StateChange(segmentation=True))

    @property
    def active_mask(self) -> str:
        """Name of the currently active mask layer."""
        return self._active_mask

    def set_active_mask(self, name: str) -> None:
        """Set the active mask layer and notify all listeners."""
        if name != self._active_mask:
            self._active_mask = name
            self.state_changed.emit(StateChange(mask=True))

    def clear(self) -> None:
        """Reset all state. Called when closing a dataset.

        Emits state_changed AFTER state is fully consistent.
        """
        self._df = pd.DataFrame()
        self._selected_ids = []
        self._filtered_ids = None
        self._filtered_df_cache = None
        self._active_segmentation = ""
        self._active_mask = ""
        self.state_changed.emit(StateChange(
            data=True, filter=True, selection=True,
            segmentation=True, mask=True,
        ))
