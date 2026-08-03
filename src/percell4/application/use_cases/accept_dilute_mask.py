"""Use case: accept the final dilute-phase mask from the dilute workflow.

A Qt-free Creator that writes the final dilute-phase mask to
``/masks/<name>`` and (in single-dataset mode) auto-selects it on the
session. Mirrors the canonical Creator pattern from
``accept_threshold.py``:

    1. ``store.write_mask`` first (store-before-session invariant).
    2. ``session.refresh_resource_lists(mask_names=...)`` so subscribers
       re-list the mask inventory before observing the new active value.
    3. ``session.set_active_mask`` last.

In **batch mode** (used by the end-to-end workflow's Phase 5 queue),
steps 2 and 3 are intentionally skipped: the launcher's session is
either unbound or bound to a different dataset, so mutating it
per-iteration would either silently no-op or write the wrong dataset's
mask list into launcher metadata. The single-dataset workflow continues
to fire all three steps so its Creator contract stays whole.

The lower-level write happens through :func:`write_dilute_mask`, a
session-free helper exposed so batch-mode callers can persist the
mask without instantiating the use case at all.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.ports.dataset_repository import DatasetRepository


def write_dilute_mask(
    repo: DatasetRepository,
    handle: DatasetHandle,
    mask_name: str,
    mask: NDArray[np.bool_],
) -> None:
    """Validate and persist a dilute mask via the repository.

    Session-free: this is the store-side of the Creator contract. The
    end-to-end workflow's batch Phase 5 calls this directly to skip
    session mutation per-iteration; the single-dataset workflow goes
    through :class:`AcceptDiluteMask.execute` instead (which adds the
    session refresh + set-active steps).

    Raises:
        ValueError: If ``mask`` is neither 2D ``(H, W)`` nor 3D ``(T, H, W)``,
            or its dtype is not bool. Raised before any store write occurs.
            The store's ``write_mask`` enforces the per-frame/native-shape
            invariants for a ``(T, H, W)`` stack.
    """
    if mask.ndim not in (2, 3):
        raise ValueError(
            f"dilute mask must be 2D (H,W) or 3D (T,H,W), got {mask.ndim}D"
        )
    if mask.dtype != np.bool_:
        raise ValueError(
            f"dilute mask must be boolean, got dtype {mask.dtype}"
        )

    # Store-before-session: write to HDF5 first.
    repo.write_mask(handle, mask_name, mask.astype(np.uint8))


class AcceptDiluteMask:
    """Persist the final dilute-phase mask and auto-select it.

    Constructor takes ports (the ``DatasetRepository`` and the
    ``Session``); ``execute`` validates the input mask, writes via the
    repository, and threads the session through the Creator triplet
    (write -> refresh -> set_active).

    Pass ``batch_mode=True`` to ``execute`` to skip the session
    refresh + set-active steps. Used by the end-to-end workflow's
    Phase 5 queue where the launcher session must not be mutated
    per-dataset.
    """

    def __init__(self, *, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def execute(
        self,
        handle: DatasetHandle,
        mask_name: str,
        mask: NDArray[np.bool_],
        batch_mode: bool = False,
    ) -> None:
        """Validate, write, refresh, and auto-select the dilute mask.

        Args:
            handle: The active dataset handle.
            mask_name: Name to write under ``/masks/<mask_name>``.
            mask: 2D boolean array — the composed dilute mask
                (``in_cell AND NOT cumulative_condensed``).
            batch_mode: When True, skip the session refresh + set-active
                steps. The store write still fires. Used by the
                end-to-end workflow's Phase 5 queue.

        Raises:
            ValueError: If ``mask`` is not 2D, or its dtype is not bool.
                Raised before any store write occurs.
        """
        write_dilute_mask(self._repo, handle, mask_name, mask)

        if batch_mode:
            return

        # Refresh inventory before auto-selecting so subscribers re-list
        # the mask combos before they look up the just-written name.
        self._session.refresh_resource_lists(
            mask_names=self._repo.list_masks(handle),
        )
        self._session.set_active_mask(mask_name)
