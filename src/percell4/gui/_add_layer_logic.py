"""Pure (Qt-free) time-axis logic for the Add Layer dialog.

Extracted so the ``(T, …)`` coercion and channel-concat — the operations that
previously corrupted ``/intensity`` on a time-lapse dataset (an older
``_write_layer`` concatenated a 2D plane onto a ``(T, H, W)`` array along the
time axis and stamped ``dims=['C','H','W']``) — are unit-testable without
constructing a ``QDialog``.

The contract (see the multi-timepoint time-handling contract):
- Single-timepoint datasets behave byte-identically to the historical path.
- On a time-lapse dataset a new channel is coerced to ``(T, H, W)`` and
  concatenated on the **C** axis, producing ``(T, C, H, W)`` with the canonical
  ``dims`` attribute.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from percell4.store import LayerSizeMismatchError


def coerce_added_array(array: NDArray, n_timepoints: int) -> NDArray:
    """Return the array to write for an added **single-TIFF** layer.

    Single-timepoint dataset (``n_timepoints <= 1``): a ``>2D`` TIFF is
    flattened to its first plane — today's behavior, byte-identical.

    Time-lapse dataset: a 2D TIFF is kept as-is (an explicit *time-invariant*
    layer); a 3D TIFF is a ``(T, H, W)`` stack whose leading axis must equal
    ``n_timepoints`` (else :class:`LayerSizeMismatchError`); a higher-rank TIFF
    raises (use the Discover-TIFFs tab for ``(T, C, …)`` sources).
    """
    if n_timepoints <= 1:
        if array.ndim > 2:
            return array[0] if array.ndim == 3 else array[0, 0]
        return array
    if array.ndim == 2:
        return array
    if array.ndim == 3:
        if array.shape[0] != n_timepoints:
            raise LayerSizeMismatchError(
                f"TIFF has {array.shape[0]} frame(s) but the dataset has "
                f"{n_timepoints} timepoints; it cannot be added as a per-frame "
                "layer. Every frame of the time axis must be present."
            )
        return array
    raise ValueError(
        f"Cannot add a {array.ndim}D TIFF to a time-lapse dataset via the "
        "Single TIFF tab; use Discover TIFFs for multi-channel/timepoint sources."
    )


def is_time_invariant_add(array: NDArray, n_timepoints: int) -> bool:
    """True when adding ``array`` to this dataset stores a time-invariant layer.

    Used by the dialog to surface the "added as a time-invariant layer — same
    plane for every timepoint" note: a 2D source on a multi-timepoint dataset.
    """
    return n_timepoints > 1 and array.ndim == 2


def build_added_channel_intensity(
    existing: NDArray | None,
    new_array: NDArray,
    n_timepoints: int,
) -> tuple[NDArray, list[str]]:
    """Return ``(stacked_intensity, dims)`` for adding a channel to ``/intensity``.

    ``existing`` is the current ``/intensity`` array, or ``None`` for the first
    channel. ``new_array`` is the new channel (2D, or ``(T, H, W)`` on a
    time-lapse dataset). On a time-lapse dataset the new channel is coerced to
    ``(T, H, W)`` (a 2D source broadcasts across time) and concatenated on the
    **C** axis, never the time axis — the fix for the silent corruption.
    """
    new_array = np.asarray(new_array, dtype=np.float32)

    if n_timepoints <= 1:
        if new_array.ndim != 2:
            raise ValueError(
                f"cannot add a {new_array.ndim}D channel to a single-timepoint "
                "dataset; the source carries a time/extra axis the target lacks."
            )
        if existing is None:
            return new_array, ["H", "W"]
        existing = np.asarray(existing, dtype=np.float32)
        if existing.ndim == 2:
            return np.stack([existing, new_array], axis=0), ["C", "H", "W"]
        return (
            np.concatenate([existing, new_array[np.newaxis]], axis=0),
            ["C", "H", "W"],
        )

    # Time-lapse: the new channel must be (T, H, W); concat on the C axis.
    ch = _as_thw(new_array, n_timepoints)
    if existing is None:
        return ch, ["T", "H", "W"]
    existing = np.asarray(existing, dtype=np.float32)
    if existing.ndim == 3:        # (T, H, W) -> (T, 1, H, W)
        existing4 = existing[:, np.newaxis]
    elif existing.ndim == 4:      # (T, C, H, W)
        existing4 = existing
    else:
        raise ValueError(
            f"unexpected /intensity rank {existing.ndim} on a time-lapse dataset"
        )
    return np.concatenate([existing4, ch[:, np.newaxis]], axis=1), [
        "T", "C", "H", "W",
    ]


def _as_thw(array: NDArray, n_timepoints: int) -> NDArray:
    """Coerce a channel array to ``(T, H, W)`` (broadcast 2D, validate 3D)."""
    if array.ndim == 2:
        return np.broadcast_to(
            array, (n_timepoints, *array.shape)
        ).astype(np.float32, copy=True)
    if array.ndim == 3:
        if array.shape[0] != n_timepoints:
            raise LayerSizeMismatchError(
                f"channel has {array.shape[0]} frame(s) but the dataset has "
                f"{n_timepoints} timepoints."
            )
        return array.astype(np.float32, copy=False)
    raise ValueError(f"channel source must be 2D or (T,H,W); got {array.ndim}D")
