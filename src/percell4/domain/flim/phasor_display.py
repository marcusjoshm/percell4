"""Display-time helpers for the phasor plot.

Pure functions with no Qt or pyqtgraph dependencies, so they can be
unit-tested independently of the GUI.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
from numpy.typing import NDArray


def compute_valid_phasor_pixels(
    g_flat: NDArray[np.floating],
    s_flat: NDArray[np.floating],
    labels_flat: NDArray[np.integer] | None,
    filter_ids: Iterable[int] | None,
    mask_flat: NDArray[np.integer] | None,
) -> NDArray[np.bool_]:
    """Compute the boolean mask of phasor pixels to render.

    The phasor plot's intensity-weighted 2D histogram restricts to pixels
    where this returns True.

    Three filters compose with AND:

    1. **Validity** — pixel has finite, non-zero (g, s). Always applied.
    2. **Cell selection** — when ``filter_ids`` is non-None and labels are
       available, restrict to pixels whose label is in ``filter_ids``.
    3. **Mask** — when ``mask_flat`` is provided and matches the per-pixel
       count of ``g_flat``, restrict to pixels where ``mask_flat`` is
       truthy. Shape mismatch silently bypasses the mask filter (the
       caller should surface a status message).

    Parameters
    ----------
    g_flat, s_flat : flattened (H*W,) phasor coordinate arrays.
    labels_flat : flattened (H*W,) segmentation labels, or None.
    filter_ids : set/iterable of selected cell ids, or None.
    mask_flat : flattened (H*W,) binary mask (0/non-zero), or None.

    Returns
    -------
    valid : boolean (H*W,) array — True for pixels to keep.
    """
    valid = np.isfinite(g_flat) & np.isfinite(s_flat) & (g_flat != 0)

    if filter_ids is not None and labels_flat is not None:
        cell_mask = np.isin(labels_flat, list(filter_ids))
        valid = valid & cell_mask

    if mask_flat is not None and mask_flat.size == g_flat.size:
        valid = valid & mask_flat.astype(bool)

    return valid


def mask_shape_matches(
    mask: NDArray | None, reference: NDArray | None
) -> bool:
    """Return True when ``mask`` is non-None and shape-aligned with ``reference``.

    Used by the phasor plot to decide whether to engage the mask filter
    or surface a "shape mismatch — filter not applied" status message.
    """
    if mask is None or reference is None:
        return False
    return mask.shape == reference.shape
