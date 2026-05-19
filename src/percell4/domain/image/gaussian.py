"""NaN-aware Gaussian smoothing via normalized convolution.

A plain :func:`scipy.ndimage.gaussian_filter` call on an array
containing ``NaN`` poisons every output pixel whose kernel footprint
overlaps any ``NaN`` — the NaN spreads across a ``~3*sigma`` radius. The
dilute-phase mask workflow deliberately punches NaN holes in its
working buffer between rounds, so it needs a smoother that treats NaN
as "missing data" rather than as a contaminating value.

``nan_safe_gaussian_filter`` implements the standard normalized-
convolution recipe:

    smoothed(image_zero_filled) / smoothed(finite_indicator)

where both quantities are smoothed with the same Gaussian kernel. The
ratio is the kernel-weighted mean of finite neighbors. Pixels whose
kernel footprint contains no finite neighbor (smoothed weight is zero)
are reported as ``NaN`` in the output rather than ``0/0``.

Clean (all-finite) inputs are mathematically equivalent to the plain
``scipy.ndimage.gaussian_filter`` result; see the test suite.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import gaussian_filter


def nan_safe_gaussian_filter(
    image: NDArray[np.floating],
    sigma: float,
    *,
    mode: str = "reflect",
    cval: float = 0.0,
) -> NDArray[np.float32]:
    """Smooth ``image`` with a Gaussian kernel, treating ``NaN`` as missing.

    Parameters
    ----------
    image
        Floating-point input. May contain ``NaN`` values.
    sigma
        Standard deviation of the Gaussian kernel, in pixels. ``0``
        returns a ``float32`` copy of the input with ``NaN`` preserved.
    mode, cval
        Forwarded to :func:`scipy.ndimage.gaussian_filter`. Defaults
        match scipy's own default (``'reflect'``), which is also what
        the existing :func:`percell4.domain.measure.thresholding.apply_gaussian_smoothing`
        uses, so the NaN-safe dispatch in U2 is a drop-in replacement
        for clean-image consumers.

    Returns
    -------
    NDArray[np.float32]
        Smoothed image. ``NaN`` is preserved at any pixel whose entire
        kernel footprint is ``NaN`` (no finite neighbor to average).
    """
    arr = np.asarray(image, dtype=np.float32)

    # sigma == 0: scipy gaussian_filter is the identity, but it also
    # propagates NaN unchanged, which is what we want. Short-circuit to
    # avoid the normalized-convolution division entirely (no warnings,
    # exact NaN preservation, smallest possible diff vs. input).
    if sigma == 0:
        return arr.copy()

    finite_mask = np.isfinite(arr)

    # Degenerate case: nothing finite to average. Return all-NaN
    # without invoking scipy on a zero-weight image.
    if not finite_mask.any():
        return np.full(arr.shape, np.nan, dtype=np.float32)

    # Fast path: no NaN. Mathematically equivalent to the normalized-
    # convolution form (smoothed weight == 1.0 everywhere except at
    # boundaries; with mode='constant' the boundary still matches scipy
    # because both numerator and denominator are zero-padded the same
    # way — but to stay bit-equivalent to plain gaussian_filter we
    # bypass the division entirely).
    if finite_mask.all():
        return gaussian_filter(arr, sigma=sigma, mode=mode, cval=cval)

    # General NaN-aware path: normalized convolution.
    zeroed = np.where(finite_mask, arr, np.float32(0.0))
    weight = finite_mask.astype(np.float32)

    smoothed_num = gaussian_filter(zeroed, sigma=sigma, mode=mode, cval=cval)
    smoothed_den = gaussian_filter(weight, sigma=sigma, mode=mode, cval=cval)

    with np.errstate(divide="ignore", invalid="ignore"):
        out = smoothed_num / smoothed_den

    # Restore NaN where the kernel footprint had no finite neighbor.
    out = np.where(smoothed_den > 0, out, np.float32(np.nan))
    return out.astype(np.float32, copy=False)
