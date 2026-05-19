"""Use case: accept the final dilute-phase mask from the dilute workflow.

A Qt-free Creator that writes the final dilute-phase mask to
``/masks/<name>`` and auto-selects it on the session. Mirrors the
canonical Creator pattern from ``accept_threshold.py``:

    1. ``store.write_mask`` first (store-before-session invariant).
    2. ``session.refresh_resource_lists(mask_names=...)`` so subscribers
       re-list the mask inventory before observing the new active value.
    3. ``session.set_active_mask`` last.

The use case takes a bool array (the workflow controller's composed
``in_cell AND NOT cumulative_condensed``) and is the audit-visible
``session.set_active_mask`` callsite for the dilute workflow.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.ports.dataset_repository import DatasetRepository


class AcceptDiluteMask:
    """Persist the final dilute-phase mask and auto-select it.

    Constructor takes ports (the ``DatasetRepository`` and the
    ``Session``); ``execute`` validates the input mask, writes via the
    repository, and threads the session through the Creator triplet
    (write -> refresh -> set_active).
    """

    def __init__(self, *, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def execute(
        self,
        handle: DatasetHandle,
        mask_name: str,
        mask: NDArray[np.bool_],
    ) -> None:
        """Validate, write, refresh, and auto-select the dilute mask.

        Args:
            handle: The active dataset handle.
            mask_name: Name to write under ``/masks/<mask_name>``.
            mask: 2D boolean array — the composed dilute mask
                (``in_cell AND NOT cumulative_condensed``).

        Raises:
            ValueError: If ``mask`` is not 2D, or its dtype is not bool.
                Raised before any store write occurs.
        """
        if mask.ndim != 2:
            raise ValueError(
                f"dilute mask must be 2D, got {mask.ndim}D"
            )
        if mask.dtype != np.bool_:
            raise ValueError(
                f"dilute mask must be boolean, got dtype {mask.dtype}"
            )

        # Store-before-session: write to HDF5 first.
        self._repo.write_mask(handle, mask_name, mask.astype(np.uint8))

        # Refresh inventory before auto-selecting so subscribers re-list
        # the mask combos before they look up the just-written name.
        self._session.refresh_resource_lists(
            mask_names=self._repo.list_masks(handle),
        )
        self._session.set_active_mask(mask_name)
