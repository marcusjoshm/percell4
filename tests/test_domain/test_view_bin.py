"""Tests for src/percell4/domain/io/view_bin.py.

Covers happy paths, rank-polymorphism, residual truncation, idempotency
at k=1, error paths, and round-trip identity (R5 from the binning plan).
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.domain.io.view_bin import (
    majority_vote_mask,
    mean_bin_2d,
    mode_labels,
    nn_upsample_2d,
    sum_bin_2d,
    sum_bin_decay,
)

# ---------------------------------------------------------------------------
# sum_bin_2d
# ---------------------------------------------------------------------------

def test_sum_bin_2d_happy_path_2d():
    arr = np.ones((6, 6), dtype=np.float32)
    out = sum_bin_2d(arr, 2)
    assert out.shape == (3, 3)
    assert np.all(out == 4)  # 2x2 ones sum to 4


def test_sum_bin_2d_rank_polymorphic_3d():
    # (C, H, W) -> (C, h_b, w_b) with H, W binned
    arr = np.ones((2, 6, 6), dtype=np.float32)
    out = sum_bin_2d(arr, 2)
    assert out.shape == (2, 3, 3)
    assert np.all(out == 4)


def test_sum_bin_2d_rank_polymorphic_4d():
    # Any leading batch axes pass through.
    arr = np.ones((3, 2, 4, 4), dtype=np.float32)
    out = sum_bin_2d(arr, 2)
    assert out.shape == (3, 2, 2, 2)
    assert np.all(out == 4)


def test_sum_bin_2d_truncates_residual_rows_cols():
    arr = np.ones((7, 7), dtype=np.float32)
    out = sum_bin_2d(arr, 3)
    assert out.shape == (2, 2)  # 7 // 3 = 2; last row/col dropped
    assert np.all(out == 9)


def test_sum_bin_2d_k_equals_1_identity():
    arr = np.arange(36).reshape(6, 6).astype(np.float32)
    out = sum_bin_2d(arr, 1)
    np.testing.assert_array_equal(out, arr)
    assert out is arr  # no copy


def test_sum_bin_2d_preserves_values():
    # Distinct values per block -> exact sums.
    arr = np.array([[1, 2, 5, 6], [3, 4, 7, 8]], dtype=np.float32)
    # Two k=2 blocks: [[1,2],[3,4]] sum 10; [[5,6],[7,8]] sum 26
    out = sum_bin_2d(arr.reshape(2, 4), 2)
    assert out.shape == (1, 2)
    assert out[0, 0] == 10 and out[0, 1] == 26


def test_sum_bin_2d_rejects_zero_k():
    with pytest.raises(ValueError, match="must be >= 1"):
        sum_bin_2d(np.ones((4, 4)), 0)


def test_sum_bin_2d_rejects_negative_k():
    with pytest.raises(ValueError, match="must be >= 1"):
        sum_bin_2d(np.ones((4, 4)), -2)


# ---------------------------------------------------------------------------
# mean_bin_2d
# ---------------------------------------------------------------------------

def test_mean_bin_2d_happy_path():
    arr = np.ones((6, 6), dtype=np.float32)
    out = mean_bin_2d(arr, 2)
    assert out.shape == (3, 3)
    # Sum 4 / 4 -> mean 1 (not 4 like sum_bin_2d)
    assert np.allclose(out, 1.0)


def test_mean_bin_2d_intensive_quantity():
    # Phasor g values in [0, 1]: their mean should also be in [0, 1].
    g = np.full((4, 4), 0.7, dtype=np.float32)
    out = mean_bin_2d(g, 2)
    assert np.allclose(out, 0.7)


def test_mean_bin_2d_k1_identity():
    arr = np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32)
    np.testing.assert_array_equal(mean_bin_2d(arr, 1), arr)


# ---------------------------------------------------------------------------
# sum_bin_decay
# ---------------------------------------------------------------------------

def test_sum_bin_decay_preserves_t_axis():
    decay = np.ones((6, 6, 4), dtype=np.float32)
    out = sum_bin_decay(decay, 2)
    assert out.shape == (3, 3, 4)
    assert np.all(out == 4)


def test_sum_bin_decay_truncates_residuals():
    decay = np.ones((7, 7, 3), dtype=np.float32)
    out = sum_bin_decay(decay, 3)
    assert out.shape == (2, 2, 3)


def test_sum_bin_decay_k1_identity():
    decay = np.arange(36).reshape(3, 3, 4).astype(np.float32)
    np.testing.assert_array_equal(sum_bin_decay(decay, 1), decay)


def test_sum_bin_decay_matches_legacy_importer_math():
    """Importer's _spatial_bin_tile is the math we're lifting. They
    must produce identical output."""
    from percell4.adapters.importer import _spatial_bin_tile  # noqa: PLC0415

    rng = np.random.default_rng(42)
    decay = rng.integers(0, 1000, size=(8, 8, 5)).astype(np.float32)
    np.testing.assert_array_equal(
        sum_bin_decay(decay, 4),
        _spatial_bin_tile(decay, 4),
    )


# ---------------------------------------------------------------------------
# majority_vote_mask
# ---------------------------------------------------------------------------

def test_majority_vote_k2_two_of_four():
    # k=2 -> threshold = ceil(4/2) = 2
    # Block A: 3 set, 1 unset -> True
    # Block B: 2 set, 2 unset -> True (>= 2)
    # Block C: 1 set, 3 unset -> False
    # Block D: 0 set, 4 unset -> False
    arr = np.array(
        [
            [1, 1, 1, 0, 1, 0, 0, 0],
            [1, 0, 1, 0, 0, 0, 0, 0],
        ],
        dtype=np.uint8,
    )
    # Layout: 1 row, 4 horizontal blocks of 2x2 (need to reshape input to
    # (2, 8) which we have). Pick rows for the 2x2 blocks: rows 0-1.
    out = majority_vote_mask(arr, 2)
    assert out.shape == (1, 4)
    assert out.dtype == np.uint8
    np.testing.assert_array_equal(out, [[1, 1, 0, 0]])


def test_majority_vote_k3_five_of_nine():
    arr = np.zeros((3, 6), dtype=np.uint8)
    # Block A (cols 0-2): 5 pixels set -> True
    arr[0, 0:3] = 1  # 3
    arr[1, 0:2] = 1  # 2 (total 5)
    # Block B (cols 3-5): 4 pixels set -> False (need 5)
    arr[0, 3:5] = 1  # 2
    arr[1, 3:5] = 1  # 2 (total 4)
    out = majority_vote_mask(arr, 3)
    assert out.shape == (1, 2)
    np.testing.assert_array_equal(out, [[1, 0]])


def test_majority_vote_returns_uint8_zero_one():
    arr = np.ones((4, 4), dtype=np.uint8) * 250  # non-zero but not 1
    out = majority_vote_mask(arr, 2)
    # All blocks majority -> True
    assert out.dtype == np.uint8
    assert set(np.unique(out).tolist()).issubset({0, 1})


def test_majority_vote_k1_identity():
    arr = np.array([[0, 1], [1, 0]], dtype=np.uint8)
    out = majority_vote_mask(arr, 1)
    np.testing.assert_array_equal(out, arr)
    assert out.dtype == np.uint8


def test_majority_vote_no_overflow_at_large_k():
    # k=16 -> 256 source pixels per block. uint8 sum would overflow.
    arr = np.ones((16, 16), dtype=np.uint8)
    out = majority_vote_mask(arr, 16)
    assert out.shape == (1, 1)
    assert out[0, 0] == 1  # all 256 set -> majority


# ---------------------------------------------------------------------------
# mode_labels
# ---------------------------------------------------------------------------

def test_mode_labels_majority_wins():
    # 2x2 block with three 1s and one 2 -> mode is 1
    arr = np.array(
        [
            [1, 1],
            [1, 2],
        ],
        dtype=np.int32,
    )
    out = mode_labels(arr, 2)
    assert out.shape == (1, 1)
    assert out[0, 0] == 1


def test_mode_labels_tie_resolves_to_zero():
    # 2x2 block with two 1s and two 2s -> tie -> 0
    arr = np.array(
        [
            [1, 1],
            [2, 2],
        ],
        dtype=np.int32,
    )
    out = mode_labels(arr, 2)
    assert out[0, 0] == 0


def test_mode_labels_k1_identity():
    arr = np.array([[3, 7, 1], [0, 2, 5]], dtype=np.int32)
    np.testing.assert_array_equal(mode_labels(arr, 1), arr)


def test_mode_labels_int32_dtype():
    arr = np.ones((4, 4), dtype=np.int32)
    out = mode_labels(arr, 2)
    assert out.dtype == np.int32


def test_mode_labels_background_dominates():
    # 3x3 block with one cell pixel and eight background -> mode is 0
    arr = np.zeros((3, 3), dtype=np.int32)
    arr[1, 1] = 5
    out = mode_labels(arr, 3)
    assert out[0, 0] == 0


def test_mode_labels_rank_polymorphic_time_stack():
    # (T, H, W) labels: each frame moded independently, T preserved.
    frame0 = np.array([[1, 1], [1, 2]], dtype=np.int32)  # mode 1
    frame1 = np.array([[3, 3], [3, 3]], dtype=np.int32)  # mode 3
    stack = np.stack([frame0, frame1], axis=0)  # (2, 2, 2)
    out = mode_labels(stack, 2)
    assert out.shape == (2, 1, 1)
    assert out[0, 0, 0] == 1
    assert out[1, 0, 0] == 3
    assert out.dtype == np.int32


def test_mode_labels_time_stack_matches_per_frame():
    # The stacked result equals applying the 2D mode frame-by-frame.
    rng = np.random.default_rng(0)
    stack = rng.integers(0, 4, size=(3, 6, 6)).astype(np.int32)
    out = mode_labels(stack, 2)
    for t in range(3):
        np.testing.assert_array_equal(out[t], mode_labels(stack[t], 2))


# ---------------------------------------------------------------------------
# nn_upsample_2d
# ---------------------------------------------------------------------------

def test_nn_upsample_2d_happy_path():
    arr = np.array([[1, 2], [3, 4]], dtype=np.int32)
    out = nn_upsample_2d(arr, 2, target_hw=(4, 4))
    expected = np.array(
        [
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [3, 3, 4, 4],
            [3, 3, 4, 4],
        ],
        dtype=np.int32,
    )
    np.testing.assert_array_equal(out, expected)


def test_nn_upsample_2d_zero_pads_when_native_not_divisible():
    # Native 7, k=3, binned 2 -> NN upsample = 6, pad to 7.
    arr = np.array([[1, 2], [3, 4]], dtype=np.int32)
    out = nn_upsample_2d(arr, 3, target_hw=(7, 7))
    assert out.shape == (7, 7)
    # First 6x6 block is the upsampled content; the 7th row/col is zero.
    assert out[6, 0] == 0
    assert out[0, 6] == 0
    assert out[6, 6] == 0
    # Upper-left 3x3 should all be 1.
    assert np.all(out[0:3, 0:3] == 1)


def test_nn_upsample_2d_k1_passes_through_when_shape_matches():
    arr = np.array([[1, 2], [3, 4]], dtype=np.int32)
    out = nn_upsample_2d(arr, 1, target_hw=(2, 2))
    np.testing.assert_array_equal(out, arr)


def test_nn_upsample_2d_rank_polymorphic():
    # (C, H, W) input.
    arr = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]]], dtype=np.int32)
    out = nn_upsample_2d(arr, 2, target_hw=(4, 4))
    assert out.shape == (2, 4, 4)
    assert out[0, 0, 0] == 1
    assert out[1, 3, 3] == 8


def test_nn_upsample_2d_rejects_zero_k():
    with pytest.raises(ValueError):
        nn_upsample_2d(np.ones((2, 2)), 0, target_hw=(2, 2))


# ---------------------------------------------------------------------------
# Round-trip identity (R5 from the plan: byte-identity at view_bin=1)
# ---------------------------------------------------------------------------

def test_round_trip_at_k1():
    rng = np.random.default_rng(7)
    arr = rng.standard_normal((10, 10)).astype(np.float32)
    np.testing.assert_array_equal(
        nn_upsample_2d(sum_bin_2d(arr, 1), 1, target_hw=arr.shape), arr
    )


def test_sum_bin_then_nn_upsample_is_lossy_but_shape_preserving():
    # Real-world: bin then upsample is NOT identity for k>1 (it's blocky).
    # But the SHAPE should round-trip and the upsample should fill the
    # whole target.
    rng = np.random.default_rng(13)
    arr = (rng.standard_normal((6, 6)) * 100).astype(np.float32)
    binned = sum_bin_2d(arr, 2)
    upsampled = nn_upsample_2d(binned, 2, target_hw=(6, 6))
    assert upsampled.shape == (6, 6)
    # Upsampled values are constant 2x2 blocks.
    assert np.all(upsampled[0, 0] == upsampled[0, 1])
    assert np.all(upsampled[0, 0] == upsampled[1, 0])
