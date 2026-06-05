"""Use case: persist a puncta mask and select it (Creator)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.errors import NoDatasetError
from percell4.ports.dataset_repository import DatasetRepository


@dataclass
class PunctaMaskResult:
    """Result of persisting a puncta mask."""

    mask_name: str
    n_positive: int
    n_total: int


class AcceptPunctaMask:
    """Persist a ``{0, 1}`` puncta mask and auto-select it.

    Owns Creator steps 1/3/4 (see ``docs/solutions/architecture-patterns/
    creator-contract-four-step-sequence-2026-05-18.md``): write the mask to the
    store first (store-before-layer), refresh the resource inventory, then set it
    as the active mask. The calling panel owns step 2 (``viewer.add_mask``).

    Takes only ``repo`` + ``session`` (no viewer port) so it stays Qt-free and
    unit-testable against a real ``DatasetStore``.
    """

    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def execute(self, mask: NDArray, name: str) -> PunctaMaskResult:
        """Coerce to ``{0, 1}`` uint8, persist, and select.

        Args:
            mask: The detected mask (any binary-coercible array).
            name: The HDF5 ``/masks/<name>`` layer name (caller-chosen).
        """
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")
        if not name:
            raise ValueError("mask name must be non-empty")

        # Enforce the {0,1} uint8 contract at the store boundary.
        binary = (np.asarray(mask) > 0).astype(np.uint8)

        # Store-before-layer: write to HDF5 first.
        self._repo.write_mask(handle, name, binary)

        # Refresh inventory before auto-selecting so subscribers re-list the
        # mask combos before they look up the just-written name.
        self._session.refresh_resource_lists(
            mask_names=self._repo.list_masks(handle),
        )
        self._session.set_active_mask(name)

        return PunctaMaskResult(
            mask_name=name,
            n_positive=int(binary.sum()),
            n_total=binary.size,
        )
