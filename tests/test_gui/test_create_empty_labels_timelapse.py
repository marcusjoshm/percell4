"""Create Empty Labels (T,H,W) on a time-lapse dataset (U12)."""

from __future__ import annotations

import numpy as np

from percell4.gui.segmentation_panel import empty_labels_array
from percell4.store import DatasetStore


def test_empty_labels_2d_single_timepoint():
    arr = empty_labels_array((8, 8), 1)
    assert arr.shape == (8, 8)
    assert arr.dtype == np.int32
    assert arr.sum() == 0


def test_empty_labels_thw_timelapse():
    arr = empty_labels_array((8, 8), 4)
    assert arr.shape == (4, 8, 8)
    assert arr.dtype == np.int32


def test_empty_labels_canvas_supports_per_frame_edits(tmp_h5):
    """The (T,H,W) empty canvas writes to the store and supports per-frame edits
    (the purpose of U12: the time-aware edit handlers engage)."""
    store = DatasetStore(tmp_h5)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity", np.zeros((4, 8, 8), dtype=np.float32),
        attrs={"dims": ["T", "H", "W"]},
    )

    labels = empty_labels_array((8, 8), store.metadata["n_timepoints"])
    assert labels.shape == (4, 8, 8)
    store.write_labels("manual", labels)
    assert store.read_labels("manual").shape == (4, 8, 8)

    # A per-frame edit persists to that frame only (via U3's write_labels_frame).
    frame = np.zeros((8, 8), dtype=np.int32)
    frame[2, 2] = 1
    store.write_labels_frame("manual", frame, timepoint=1)
    assert store.read_labels("manual", timepoint=1)[2, 2] == 1
    assert store.read_labels("manual", timepoint=0).sum() == 0
