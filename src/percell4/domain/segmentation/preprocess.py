"""Pre-segmentation image preprocessing — clip + stretch LUT helpers.

Mathematically equivalent to ImageJ's "Apply LUT" with a brightness/
contrast min/max. Used as a Cellpose-input transformation when the
raw channel has long-tail intensity outliers (dust, hot pixels) that
skew Cellpose's internal percentile normalization. The transformation
is in-memory only; persisted ``/intensity`` is never modified.

Two entry points:

- :func:`apply_lut` — explicit ``(lo, hi)`` clip + stretch. Used by
  the seg-QC controller's interactive histogram-handle UI.
- :func:`apply_saturation_lut` — saturation-percentile wrapper. Used
  by the single-cell workflow's segmentation phase. Mirrors ImageJ's
  Enhance Contrast: ``saturation_pct`` % of the brightest pixels are
  clipped to the dtype max; ``lo`` is the channel min.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

# Minimum separation between lo and hi (in source units). One unit
# for uint16-quantized microscopy data — protects against
# divide-by-zero on degenerate ranges (e.g. all-zero channels).
_LUT_EPSILON = 1.0


def apply_lut(
    channel: NDArray, lo: float, hi: float,
) -> NDArray:
    """Clip ``channel`` to ``[lo, hi]`` and linearly stretch to its dtype.

    Pure-numpy operation. Equivalent to ImageJ's "Apply LUT" with
    brightness/contrast min/max set to ``(lo, hi)``. When ``lo`` and
    ``hi`` are within ``_LUT_EPSILON`` of each other (degenerate range,
    e.g. all-zero image) the channel is returned unchanged so the
    caller never sees NaN / divide-by-zero artifacts.

    Output dtype matches the input. For integer dtypes the stretched
    range fills ``[0, dtype_max]``; for float dtypes the output is in
    ``[0.0, 1.0]``.

    Parameters
    ----------
    channel : NDArray
        Source intensity array of any shape and any numeric dtype.
    lo : float
        Lower clip bound. Pixels below ``lo`` map to 0 in the output.
    hi : float
        Upper clip bound. Pixels at or above ``hi`` map to the dtype
        max (or 1.0 for floats).

    Returns
    -------
    NDArray
        Same shape and dtype as ``channel``.
    """
    if hi - lo < _LUT_EPSILON:
        return channel
    clipped = np.clip(channel, lo, hi).astype(np.float64)
    normalized = (clipped - lo) / (hi - lo)  # [0, 1]
    if np.issubdtype(channel.dtype, np.integer):
        info = np.iinfo(channel.dtype)
        scaled = normalized * float(info.max)
        return scaled.astype(channel.dtype, copy=False)
    return normalized.astype(channel.dtype, copy=False)


def apply_saturation_lut(
    channel: NDArray, saturation_pct: float,
) -> NDArray:
    """Apply an ImageJ-style Enhance Contrast LUT to ``channel``.

    Computes ``hi = percentile(channel, 100 - saturation_pct)`` (so
    ``saturation_pct`` % of the brightest pixels saturate) and
    ``lo = channel.min()``, then delegates to :func:`apply_lut`.

    ``saturation_pct == 0`` is a no-op: returns the channel unchanged
    so callers can use this function unconditionally and let the
    parameter decide whether the LUT is engaged. Reasonable values
    are typically 0.1 to 5.0.

    Parameters
    ----------
    channel : NDArray
        Source intensity. For time-lapse stacks the caller should
        apply per-frame — this function computes percentiles over the
        full input, which would be the wrong reference for a stack.
    saturation_pct : float
        Percent of brightest pixels to saturate to the dtype max.
        Must be in ``[0, 50]``; outside that range raises ``ValueError``.

    Returns
    -------
    NDArray
        Same shape and dtype as ``channel``.
    """
    if not (0.0 <= saturation_pct <= 50.0):
        raise ValueError(
            f"saturation_pct must be in [0, 50], got {saturation_pct}"
        )
    if saturation_pct == 0.0:
        return channel
    lo = float(channel.min())
    hi = float(np.percentile(channel, 100.0 - saturation_pct))
    return apply_lut(channel, lo, hi)
