"""Pure (Qt-free) per-frame whole-field thresholding logic (U10).

The interactive Whole-Field Thresholding panel previews on the active frame, but
**Accept** must threshold every timepoint. For an auto method the threshold is
recomputed per frame (contract D3 — handles photobleaching / intensity drift);
for a manual scalar the same value broadcasts. Extracted so the per-frame loop is
unit-testable without the Qt panel or ``AcceptThreshold`` (which stays a
shape-transparent single-image use case).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def build_threshold_mask_stack(
    frames: list[NDArray],
    sigma: float,
    method: str,
    manual_value: float,
) -> tuple[NDArray, list[float]]:
    """Build a ``(T, H, W)`` ``{0,1}`` mask from per-frame 2D channel ``frames``.

    Each frame is Gaussian-smoothed (when ``sigma > 0``), then thresholded: a
    ``"manual"`` method broadcasts ``manual_value`` to every frame, while an auto
    method (otsu/li/triangle/adaptive) recomputes the cutoff per frame. Returns
    ``(mask (T,H,W) uint8, values list[float])`` — one threshold value per frame.
    """
    from percell4.domain.measure.thresholding import (
        THRESHOLD_METHODS,
        apply_gaussian_smoothing,
    )

    masks: list[NDArray] = []
    values: list[float] = []
    for frame in frames:
        img = np.asarray(frame, dtype=np.float32)
        if sigma > 0:
            img = apply_gaussian_smoothing(img, sigma)
        if method == "manual":
            value = float(manual_value)
        else:
            _, value = THRESHOLD_METHODS[method](img)
        masks.append((img > value).astype(np.uint8))
        values.append(float(value))
    return np.stack(masks, axis=0), values
