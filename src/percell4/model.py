"""Central data model for PerCell4.

CellDataModel is the communication hub — all windows connect to its signals.
The DataFrame is ephemeral: computed on the fly for the currently loaded dataset,
not persisted across datasets. Persistent measurements live in each .h5 file.
"""

from __future__ import annotations

import pandas as pd
from qtpy.QtCore import QObject, Signal


class CellDataModel(QObject):
    """Holds per-cell measurements and selection state.

    All windows listen to signals on this object — they never talk to each other
    directly. Listeners get a read-only view of the DataFrame; they must not
    modify it.
    """

    data_updated = Signal()
    selection_changed = Signal(list)  # list of selected label IDs (int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._df = pd.DataFrame()
        self._selected_ids: list[int] = []

    @property
    def df(self) -> pd.DataFrame:
        """Current per-cell measurements. Read-only — do not modify."""
        return self._df

    def set_measurements(self, df: pd.DataFrame) -> None:
        """Replace the measurements DataFrame and notify all listeners."""
        self._df = df
        self.data_updated.emit()

    def set_selection(self, label_ids: list[int]) -> None:
        """Update the selected cell IDs and notify all listeners."""
        self._selected_ids = list(label_ids)
        self.selection_changed.emit(self._selected_ids)

    @property
    def selected_ids(self) -> list[int]:
        """Currently selected cell label IDs."""
        return self._selected_ids

    def clear(self) -> None:
        """Reset all state. Called when closing a dataset."""
        self._df = pd.DataFrame()
        self._selected_ids = []
        self.data_updated.emit()
        self.selection_changed.emit([])
