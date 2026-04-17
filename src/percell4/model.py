"""CellDataModel — Qt signal bridge over the application Session.

During the hex architecture migration, CellDataModel delegates all state
to Session and re-emits Session events as Qt signals. This lets the
launcher, viewer, task panels, and workflow runners keep working with
their existing state_changed signal connections while peer views are
migrated to subscribe to Session directly.

Once all consumers are migrated to Session (Stage 5), this file is deleted.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from qtpy.QtCore import QObject, Signal

from percell4.application.session import Event, Session


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
    """Qt signal bridge over Session.

    Delegates all state to a Session instance. Subscribes to Session
    events and re-emits them as Qt state_changed signals for legacy
    consumers (launcher, viewer, task panels, workflow runners).
    """

    state_changed = Signal(object)  # emits StateChange

    def __init__(self, session: Session | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._session = session or Session()
        self._wiring_session = False  # guard against re-entrant emit

        # Subscribe to all Session events and re-emit as Qt signals
        self._session.subscribe(Event.DATASET_CHANGED, self._on_dataset_changed)
        self._session.subscribe(Event.MEASUREMENTS_UPDATED, self._on_measurements_updated)
        self._session.subscribe(Event.SELECTION_CHANGED, self._on_selection_changed)
        self._session.subscribe(Event.FILTER_CHANGED, self._on_filter_changed)
        self._session.subscribe(
            Event.ACTIVE_SEGMENTATION_CHANGED, self._on_segmentation_changed
        )
        self._session.subscribe(Event.ACTIVE_MASK_CHANGED, self._on_mask_changed)

    @property
    def session(self) -> Session:
        """Access the underlying Session (for passing to peer views)."""
        return self._session

    # ── Session event handlers → Qt signal emission ─────────

    def _on_dataset_changed(self) -> None:
        if not self._wiring_session:
            self.state_changed.emit(StateChange(
                data=True, filter=True, selection=True,
                segmentation=True, mask=True,
            ))

    def _on_measurements_updated(self) -> None:
        if not self._wiring_session:
            self.state_changed.emit(StateChange(data=True))

    def _on_selection_changed(self) -> None:
        if not self._wiring_session:
            self.state_changed.emit(StateChange(selection=True))

    def _on_filter_changed(self) -> None:
        if not self._wiring_session:
            self.state_changed.emit(StateChange(filter=True))

    def _on_segmentation_changed(self) -> None:
        if not self._wiring_session:
            self.state_changed.emit(StateChange(segmentation=True))

    def _on_mask_changed(self) -> None:
        if not self._wiring_session:
            self.state_changed.emit(StateChange(mask=True))

    # ── Read-only properties (delegate to Session) ──────────

    @property
    def df(self) -> pd.DataFrame:
        return self._session.df

    @property
    def filtered_df(self) -> pd.DataFrame:
        return self._session.filtered_df

    @property
    def is_filtered(self) -> bool:
        return self._session.is_filtered

    @property
    def filtered_ids(self) -> set[int] | None:
        ids = self._session.filter_ids
        return set(ids) if ids is not None else None

    @property
    def selected_ids(self) -> list[int]:
        return self._session.selected_ids

    @property
    def active_segmentation(self) -> str:
        return self._session.active_segmentation or ""

    @property
    def active_mask(self) -> str:
        return self._session.active_mask or ""

    # ── Mutators (delegate to Session, which fires events) ──

    def set_measurements(self, df: pd.DataFrame) -> None:
        self._session.set_measurements(df)

    def set_selection(self, label_ids: list[int]) -> None:
        self._session.set_selection(frozenset(label_ids))

    def set_filter(self, label_ids: list[int] | None) -> None:
        ids = frozenset(label_ids) if label_ids is not None else None
        self._session.set_filter(ids)

    def set_active_segmentation(self, name: str) -> None:
        self._session.set_active_segmentation(name or None)

    def set_active_mask(self, name: str) -> None:
        self._session.set_active_mask(name or None)

    def clear(self) -> None:
        self._session.clear()
