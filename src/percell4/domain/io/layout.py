"""Intensity-array → display-layer splitting.

A leading time axis ``(T, ...)`` and a leading channel axis ``(C, H, W)``
are indistinguishable from shape alone, so the split is driven by the
dataset's ``n_timepoints`` metadata. Display code keeps the time axis so
napari builds a single dims slider; compute code asks for one timepoint
frame and splits that into 2D per-channel planes.

Pure numpy — no h5py, Qt, or napari.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

__all__ = ["split_channels_2d", "split_intensity_layers"]

# Historical heuristic: a leading axis this small on a non-time-lapse
# dataset is treated as channels; anything larger is shown as one layer.
_MAX_SPLIT_CHANNELS = 20


def _channel_name(channel_names: list[str], i: int) -> str:
    return channel_names[i] if i < len(channel_names) else f"ch{i}"


def split_channels_2d(
    plane: NDArray, channel_names: list[str] | None
) -> list[tuple[str, NDArray]]:
    """Split a single-timepoint intensity plane into ``[(name, 2D array)]``.

    ``plane`` is ``(H, W)`` (single channel) or ``(C, H, W)`` (multi). The
    ``(C, H, W)`` split uses the historical ``<= 20`` channel heuristic;
    larger leading dims fall back to a single ``"Intensity"`` layer.
    """
    # Don't use ``channel_names or []`` — a numpy string array (what h5py
    # returns for a multi-element attr) has an ambiguous truth value.
    names = list(channel_names) if channel_names is not None else []
    if plane.ndim == 2:
        return [(names[0] if names else "Intensity", plane)]
    if plane.ndim == 3 and plane.shape[0] <= _MAX_SPLIT_CHANNELS:
        return [(_channel_name(names, i), plane[i]) for i in range(plane.shape[0])]
    return [("Intensity", plane)]


def split_intensity_layers(
    intensity: NDArray, channel_names: list[str] | None, n_timepoints: int
) -> list[tuple[str, NDArray]]:
    """Split ``/intensity`` into display layers, keeping any leading time axis.

    Disambiguates a leading T axis from a leading C axis via
    ``n_timepoints``. For a time-lapse dataset each returned array keeps
    its ``(T, ...)`` shape so napari shows a single dims slider:

    - ``(T, H, W)``   -> one layer, array kept ``(T, H, W)``
    - ``(T, C, H, W)`` -> C layers, each ``intensity[:, c]`` kept ``(T, H, W)``

    For a non-time-lapse dataset behavior matches the historical channel
    split (see :func:`split_channels_2d`).
    """
    # Don't use ``channel_names or []`` — a numpy string array (what h5py
    # returns for a multi-element attr) has an ambiguous truth value.
    names = list(channel_names) if channel_names is not None else []
    nt = int(n_timepoints or 1)
    if nt <= 1:
        return split_channels_2d(intensity, names)
    if intensity.ndim == 3:  # (T, H, W) — single channel
        return [(names[0] if names else "Intensity", intensity)]
    if intensity.ndim == 4:  # (T, C, H, W)
        return [
            (_channel_name(names, c), intensity[:, c])
            for c in range(intensity.shape[1])
        ]
    # Unexpected rank for a time-lapse dataset — show as one layer.
    return [("Intensity", intensity)]
