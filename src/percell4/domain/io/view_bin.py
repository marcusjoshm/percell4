"""Spatial downsamplers and upsamplers for the dataset-wide view-bin model.

The dataset-wide spatial binning model (see
``docs/plans/2026-05-18-001-feat-dataset-wide-spatial-binning-plan.md``)
keeps every HDF5 array at the dataset's native (k=1) resolution. Reads
downsample on the fly via the functions here; writes from Creators
nearest-neighbor-upsample back to native before they hit the store.

Every consumer (``DatasetStore`` read methods, viewer rebuild, measure,
segment, FLIM) imports from this module. Routing the math through one
file keeps the per-kind rule honest:

  * ``/intensity`` and ``/decay/<ch>`` carry photon counts -> sum-bin.
  * ``/labels/<name>`` are int32 instance IDs -> block mode (ties -> 0).
  * ``/masks/<name>`` are binary uint8 -> majority vote (>= ceil(k**2 / 2)).
  * ``/phasor/<ch>/{g, s, g_filtered, s_filtered, lifetime, lifetime_filtered}``
    are intensive (range [0, 1] or ns) -> mean-bin.

All functions raise ``ValueError`` on ``k < 1`` and return the input
unchanged at ``k == 1`` (no copy).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def _validate_k(k: int) -> None:
    if k < 1:
        raise ValueError(f"spatial bin factor must be >= 1, got {k}")


def _truncate_trailing_hw(arr: NDArray, k: int) -> tuple[NDArray, int, int]:
    """Truncate the trailing two axes so they're divisible by ``k``.

    Returns ``(truncated, h_binned, w_binned)``. Works on any rank >= 2;
    the trailing two axes are the spatial axes (H, W). Residual pixels
    are dropped.
    """
    h, w = arr.shape[-2], arr.shape[-1]
    h_trunc = (h // k) * k
    w_trunc = (w // k) * k
    sl = (Ellipsis, slice(0, h_trunc), slice(0, w_trunc))
    return arr[sl], h_trunc // k, w_trunc // k


def sum_bin_2d(arr: NDArray, k: int) -> NDArray:
    """Sum-bin the trailing two axes by ``k`` x ``k``.

    Rank-polymorphic: ``(H, W)``, ``(C, H, W)``, ``(N, C, H, W)``, ... all
    work. Trailing axes are spatial; everything else passes through.
    Residual rows/cols (H % k, W % k) are truncated.

    Use for photon-count-bearing arrays (``/intensity``). For decay
    ``(H, W, T)``, use :func:`sum_bin_decay` instead -- the T axis is the
    last axis there, not a spatial axis.
    """
    _validate_k(k)
    if k == 1:
        return arr
    truncated, h_b, w_b = _truncate_trailing_hw(arr, k)
    lead = truncated.shape[:-2]
    reshaped = truncated.reshape(*lead, h_b, k, w_b, k)
    # Sum the two "k" axes. They occupy positions len(lead)+1 and len(lead)+3.
    axis_lo = len(lead) + 1
    axis_hi = len(lead) + 3
    return reshaped.sum(axis=(axis_lo, axis_hi))


def mean_bin_2d(arr: NDArray, k: int) -> NDArray:
    """Mean-bin the trailing two axes by ``k`` x ``k``.

    Same shape contract as :func:`sum_bin_2d`. Use for intensive
    quantities (phasor g, s, lifetime) where the magnitude shouldn't
    scale with bin size.
    """
    _validate_k(k)
    if k == 1:
        return arr
    return sum_bin_2d(arr, k) / (k * k)


def sum_bin_decay(arr: NDArray, k: int) -> NDArray:
    """Sum-bin (H, W) of a TCSPC decay array ``(H, W, T)``.

    Preserves the T axis. Residual rows/cols truncated.

    Use for ``/decay/<ch>`` arrays specifically. The math matches the
    existing ``adapters/importer._spatial_bin_tile`` helper.
    """
    _validate_k(k)
    if k == 1:
        return arr
    h, w = arr.shape[:2]
    h_trunc = (h // k) * k
    w_trunc = (w // k) * k
    truncated = arr[:h_trunc, :w_trunc]
    h_b, w_b = h_trunc // k, w_trunc // k
    return truncated.reshape(h_b, k, w_b, k, -1).sum(axis=(1, 3))


def majority_vote_mask(arr: NDArray, k: int) -> NDArray:
    """Downsample a binary uint8 mask by majority vote.

    A binned pixel is True iff at least ``ceil(k**2 / 2)`` source pixels
    in its block are True. At ``k == 2`` this requires 2 of 4. At
    ``k == 3`` this requires 5 of 9. Residual pixels truncated.

    The mask is treated as 0/non-zero on the way in (any non-zero count)
    and returned as ``uint8`` with values exactly 0 or 1.
    """
    _validate_k(k)
    if k == 1:
        return arr.astype(np.uint8, copy=False)
    # Cast to a small int for the sum so uint8 doesn't overflow at k > 16
    # (16*16=256, beyond uint8's 255 limit). uint16 is enough for k<=255.
    binary = (arr != 0).astype(np.uint16)
    summed = sum_bin_2d(binary, k)
    threshold = (k * k + 1) // 2
    return (summed >= threshold).astype(np.uint8)


def mode_labels(arr: NDArray, k: int) -> NDArray:
    """Downsample an int32 labels array by block mode (ties -> 0).

    For each k x k block, return the label that appears most often.
    Ties resolve to 0 (background) -- conservatively dropping ambiguous
    pixels rather than picking arbitrarily.

    Rank-polymorphic on the trailing two axes (same contract as
    :func:`sum_bin_2d`): ``(H, W)`` and time-stacked ``(T, H, W)`` (or any
    rank >= 2) both work -- each leading frame is moded independently.
    Residual rows/cols truncated.
    """
    _validate_k(k)
    if k == 1:
        return arr.astype(np.int32, copy=False)
    truncated, h_b, w_b = _truncate_trailing_hw(arr, k)
    lead = truncated.shape[:-2]
    if lead:
        # Mode each leading frame independently, then restore the leading
        # shape. Flatten leading axes so the per-frame loop stays simple.
        flat = truncated.reshape(-1, truncated.shape[-2], truncated.shape[-1])
        out = np.stack(
            [_mode_labels_2d(flat[i], k, h_b, w_b) for i in range(flat.shape[0])],
            axis=0,
        )
        return out.reshape(*lead, h_b, w_b)
    return _mode_labels_2d(truncated, k, h_b, w_b)


def _mode_labels_2d(truncated: NDArray, k: int, h_b: int, w_b: int) -> NDArray:
    """Block-mode a single already-truncated 2D label plane."""
    # Reshape to (h_b, k, w_b, k) then move both k axes adjacent so we can
    # consider each k*k block as a flat vector of source labels.
    reshaped = truncated.reshape(h_b, k, w_b, k)
    # (h_b, w_b, k, k) -> (h_b, w_b, k*k)
    blocks = reshaped.transpose(0, 2, 1, 3).reshape(h_b, w_b, k * k)
    out = np.zeros((h_b, w_b), dtype=np.int32)
    # Loop pixel-by-pixel: per-block bincount + argmax + tie check.
    # k is bounded by 16 (Session validation), so the inner loop sees at
    # most 256 source pixels per block -- fast enough in practice.
    for i in range(h_b):
        for j in range(w_b):
            block = blocks[i, j]
            counts = np.bincount(block.astype(np.int64))
            top = int(counts.max())
            # Tie if the same top count appears for more than one label.
            winners = np.flatnonzero(counts == top)
            if winners.size == 1:
                out[i, j] = int(winners[0])
            # else: leave as 0 (tie -> background)
    return out


def nn_upsample_2d(
    arr: NDArray, k: int, target_hw: tuple[int, int]
) -> NDArray:
    """Nearest-neighbor upsample by ``k`` x ``k``, zero-padding to ``target_hw``.

    Used by Creators to bring a result produced at a binned shape back to
    the dataset's native shape before write. If ``k * arr.shape[-2] >=
    target_hw[0]`` (and analogously for W), the upsample plus a final
    truncation matches ``target_hw`` exactly. Otherwise (the native shape
    is not a multiple of k -- e.g. native 513, k 2, binned 256), the
    upsample reaches 512 and the final row/col is zero-padded.

    Rank-polymorphic on the trailing two axes (same contract as
    :func:`sum_bin_2d`).
    """
    _validate_k(k)
    if k == 1:
        # Even at k=1 we may need to pad if shapes don't match. Defensive
        # path that keeps the contract honest.
        return _fit_to_target(arr, target_hw)
    upsampled = np.repeat(np.repeat(arr, k, axis=-2), k, axis=-1)
    return _fit_to_target(upsampled, target_hw)


def _fit_to_target(arr: NDArray, target_hw: tuple[int, int]) -> NDArray:
    """Crop or zero-pad the trailing two axes to ``target_hw``."""
    target_h, target_w = target_hw
    h, w = arr.shape[-2], arr.shape[-1]
    if h == target_h and w == target_w:
        return arr
    if h >= target_h and w >= target_w:
        sl = (Ellipsis, slice(0, target_h), slice(0, target_w))
        return arr[sl]
    # Need to zero-pad on at least one trailing axis.
    out_shape = arr.shape[:-2] + (target_h, target_w)
    out = np.zeros(out_shape, dtype=arr.dtype)
    h_keep = min(h, target_h)
    w_keep = min(w, target_w)
    sl_out = (Ellipsis, slice(0, h_keep), slice(0, w_keep))
    sl_in = (Ellipsis, slice(0, h_keep), slice(0, w_keep))
    out[sl_out] = arr[sl_in]
    return out
