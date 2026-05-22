"""Relabel a raw per-frame label stack so pixel value == track id.

Pure numpy/pandas — no laptrack, h5py, Qt, or napari.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import NDArray

__all__ = ["relabel_stack_by_track"]


def relabel_stack_by_track(
    raw_stack: NDArray, track_df: pd.DataFrame
) -> NDArray[np.int32]:
    """Return a ``(T, H, W)`` stack where each cell's pixels carry its track id.

    ``track_df`` columns: ``timepoint``, ``label`` (the raw per-frame
    Cellpose id), ``track_id`` (the value to write — must be ``>= 1`` so 0
    stays background). The same physical cell therefore has an identical
    pixel value across every frame it appears in. Raw cells with no track
    row (e.g. dropped by the tracker) become background.
    """
    if raw_stack.ndim != 3:
        raise ValueError(f"raw_stack must be (T, H, W), got {raw_stack.ndim}D")
    out = np.zeros(raw_stack.shape, dtype=np.int32)
    for t in range(raw_stack.shape[0]):
        frame = raw_stack[t]
        sub = track_df[track_df["timepoint"] == t]
        if sub.empty:
            continue
        max_label = int(frame.max())
        if max_label == 0:
            continue
        # Lookup table old-label -> track_id (0 for unmapped labels/background).
        lut = np.zeros(max_label + 1, dtype=np.int32)
        for old, new in zip(sub["label"].astype(int), sub["track_id"].astype(int)):
            if 0 <= old <= max_label:
                lut[old] = new
        out[t] = lut[frame]
    return out
