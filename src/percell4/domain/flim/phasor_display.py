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
    *,
    intensity_flat: NDArray[np.floating] | None = None,
    intensity_threshold: float = 0.0,
    ref_circle_center: tuple[float, float] | None = None,
    ref_circle_radius: float | None = None,
) -> NDArray[np.bool_]:
    """Compute the boolean mask of phasor pixels to render.

    The phasor plot's intensity-weighted 2D histogram and the GMM input
    pipeline both restrict to pixels where this returns True.

    Five filters compose with AND:

    1. **Validity** — pixel has finite, non-zero (g, s). Always applied.
    2. **Cell selection** — when ``filter_ids`` is non-None and labels are
       available, restrict to pixels whose label is in ``filter_ids``.
       (``np.isin`` is called with ``list(filter_ids)`` because NumPy 2.x
       silently returns all-False when given a Python set.)
    3. **Mask** — when ``mask_flat`` is provided and matches the per-pixel
       count of ``g_flat``, restrict to pixels where ``mask_flat`` is
       truthy. Shape mismatch silently bypasses the mask filter (the
       caller should surface a status message).
    4. **Intensity threshold** — when ``intensity_flat`` is provided,
       matches ``g_flat`` shape, and ``intensity_threshold > 0``,
       restrict to pixels where ``intensity_flat >= intensity_threshold``.
       Shape mismatch silently bypasses (same convention as the mask
       filter).
    5. **Reference circle** — when ``ref_circle_center`` and
       ``ref_circle_radius`` are both provided, restrict to pixels where
       ``(g - G_c)² + (s - S_c)² <= radius²``. Used by the FLIM tab's
       "circular ROI at a target lifetime" filter; the caller computes
       ``(G_c, S_c)`` via ``universal_circle_gs``.

    Parameters
    ----------
    g_flat, s_flat : flattened (H*W,) phasor coordinate arrays.
    labels_flat : flattened (H*W,) segmentation labels, or None.
    filter_ids : set/iterable of selected cell ids, or None.
    mask_flat : flattened (H*W,) binary mask (0/non-zero), or None.
    intensity_flat : flattened (H*W,) intensity (photon counts), or None.
    intensity_threshold : minimum intensity (inclusive). Zero is a no-op.
    ref_circle_center : (G_c, S_c) of the reference-circle filter, or None.
    ref_circle_radius : radius of the reference-circle filter, or None.

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

    if (
        intensity_flat is not None
        and intensity_flat.size == g_flat.size
        and intensity_threshold > 0
    ):
        valid = valid & (intensity_flat >= intensity_threshold)

    if ref_circle_center is not None and ref_circle_radius is not None:
        g_c, s_c = ref_circle_center
        dg = g_flat - g_c
        ds = s_flat - s_c
        valid = valid & (dg * dg + ds * ds <= ref_circle_radius * ref_circle_radius)

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
