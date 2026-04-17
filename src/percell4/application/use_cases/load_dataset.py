"""Use case: load a dataset from disk into the session and viewer."""

from __future__ import annotations

from pathlib import Path

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.ports.dataset_repository import DatasetRepository
from percell4.ports.viewer import ViewerPort


class LoadDataset:
    """Open a dataset file, update the session, and display in the viewer.

    Encapsulates the store-before-layer invariant: the session is updated
    before the viewer is configured, so any synchronous viewer events see
    consistent state.
    """

    def __init__(
        self,
        repo: DatasetRepository,
        viewer: ViewerPort,
        session: Session,
    ) -> None:
        self._repo = repo
        self._viewer = viewer
        self._session = session

    def execute(self, path: Path) -> DatasetHandle:
        handle = self._repo.open(path)
        view = self._repo.build_view(handle)

        # Store-before-layer: session is authoritative before viewer reconfigures
        self._session.set_dataset(handle)
        self._viewer.show_dataset(view)

        return handle
