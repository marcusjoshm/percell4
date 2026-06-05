"""Per-frame whole-field thresholding (U10)."""

from __future__ import annotations

import numpy as np

from percell4.gui._threshold_logic import build_threshold_mask_stack


def test_manual_threshold_broadcasts_to_all_frames():
    f0 = np.full((4, 4), 5.0, dtype=np.float32)
    f1 = np.full((4, 4), 5.0, dtype=np.float32)
    f0[0, 0] = 20.0
    f1[3, 3] = 20.0
    mask, values = build_threshold_mask_stack([f0, f1], 0.0, "manual", 10.0)

    assert mask.shape == (2, 4, 4)
    assert values == [10.0, 10.0]  # same scalar every frame
    assert mask[0, 0, 0] == 1 and mask[0, 1, 1] == 0
    assert mask[1, 3, 3] == 1


def test_auto_threshold_recomputed_per_frame():
    """A bright frame and a dim frame get DIFFERENT Otsu thresholds (D3) -- the
    cutoff is computed per frame, not once globally."""
    bright = np.zeros((10, 10), dtype=np.float32)
    bright[:5] = 100.0
    dim = np.zeros((10, 10), dtype=np.float32)
    dim[:5] = 10.0

    mask, values = build_threshold_mask_stack([bright, dim], 0.0, "otsu", 0.0)

    assert mask.shape == (2, 10, 10)
    assert values[0] != values[1]  # per-frame thresholds
    # Each frame's bright half is flagged.
    assert mask[0, :5].all() and not mask[0, 5:].any()
    assert mask[1, :5].all() and not mask[1, 5:].any()


def test_threshold_stack_round_trips_through_store(tmp_h5):
    from percell4.store import DatasetStore

    store = DatasetStore(tmp_h5)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity", np.zeros((2, 10, 10), dtype=np.float32),
        attrs={"dims": ["T", "H", "W"]},
    )
    f0 = np.zeros((10, 10), dtype=np.float32)
    f0[0, 0] = 50.0
    f1 = np.zeros((10, 10), dtype=np.float32)
    f1[9, 9] = 50.0
    mask_stack, _ = build_threshold_mask_stack([f0, f1], 0.0, "manual", 10.0)

    store.write_mask("otsu_GFP", mask_stack)
    assert store.read_mask("otsu_GFP").shape == (2, 10, 10)
    assert store.read_mask("otsu_GFP", timepoint=0)[0, 0] == 1
    assert store.read_mask("otsu_GFP", timepoint=1)[9, 9] == 1
