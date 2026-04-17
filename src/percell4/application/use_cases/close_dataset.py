"""Use case: close the current dataset."""

from __future__ import annotations

from percell4.application.session import Session
from percell4.ports.viewer import ViewerPort


class CloseDataset:
    """Clear the session and viewer. No store cleanup needed —
    HDF5 files are written per-operation, not held open."""

    def __init__(self, viewer: ViewerPort, session: Session) -> None:
        self._viewer = viewer
        self._session = session

    def execute(self) -> None:
        self._viewer.clear()
        self._session.clear()
