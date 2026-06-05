"""Grouped Thresholding time-lapse crash fix (U9)."""

from __future__ import annotations

import numpy as np
import pytest

from percell4.domain.measure.measurer import measure_cells
from percell4.gui.grouped_seg_panel import slice_to_active_frame


def test_slice_to_active_frame_slices_thw():
    ch = np.zeros((6, 8, 8), dtype=np.float32)
    ch[3] = 7
    labels = np.zeros((6, 8, 8), dtype=np.int32)
    labels[3, 2, 2] = 1
    c2, l2 = slice_to_active_frame(ch, labels, 3)
    assert c2.shape == (8, 8) and l2.shape == (8, 8)
    assert np.all(c2 == 7)
    assert l2[2, 2] == 1


def test_slice_leaves_2d_time_invariant_labels():
    """A 2D whole-field gate stays 2D (only a (T,H,W) stack is sliced)."""
    ch = np.zeros((6, 8, 8), dtype=np.float32)
    labels = np.ones((8, 8), dtype=np.int32)
    c2, l2 = slice_to_active_frame(ch, labels, 2)
    assert c2.shape == (8, 8)
    assert l2.shape == (8, 8)


def test_grouped_threshold_measure_no_longer_crashes():
    """Regression: measuring the sliced active frame succeeds, where measuring a
    (T,H,W) channel against 2D labels is the '6 vs 485' crash (now a loud guard)."""
    channel_thw = np.ones((6, 20, 20), dtype=np.float32)
    labels_2d = np.zeros((20, 20), dtype=np.int32)
    labels_2d[5:10, 5:10] = 1

    # After U9 the panel slices to the active frame before measuring.
    ch2, lbl2 = slice_to_active_frame(channel_thw, labels_2d, 2)
    df = measure_cells(ch2, lbl2, metrics=["mean_intensity"])
    assert len(df) == 1  # one cell, no IndexError

    # The unsliced combination fails loud (U4 guard) instead of a cryptic crash.
    with pytest.raises(ValueError, match="same 2D shape"):
        measure_cells(channel_thw, labels_2d, metrics=["mean_intensity"])
