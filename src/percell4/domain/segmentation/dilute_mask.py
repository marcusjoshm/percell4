"""Dilute-phase mask morphology: dilate a binary mask, invert within cells.

Pure-domain helper backing the batch "Dilute phase mask from mask" tool
(``docs/plans/2026-06-28-002-feat-dilute-phase-mask-from-mask-plan.md``)
and — once unified (U5) — the interactive dilute-phase workflow. This is
the **single source of truth** for the dilute-from-mask morphology (R7),
lifted byte-identical from the interactive dilute controller
(``gui/workflows/dilute_phase/controller.py``): the dilation at
``controller.py:362-372`` and the invert-within-cells at
``controller.py:238-239``.

The "dilute phase" is the set of in-cell pixels that are *not* (near) the
condensed phase:

    dilute = (seg_labels > 0) AND NOT dilation(condensed, disk(radius_px))

Public functions:

- :func:`dilate_mask` — grow a binary mask by a disk of the given **radius**
  in pixels (``radius_px == 0`` → no dilation). ``radius_px`` is the disk
  *radius*, not its diameter (D2).
- :func:`invert_within_cells` — keep the in-cell pixels that the (dilated)
  condensed mask does not cover.
- :func:`dilute_from_mask` — the composed op; the module's primary entry
  point. **Returns a ``bool`` array** — the use-case passes this straight to
  ``write_dilute_mask``, which requires bool and casts to ``uint8`` itself
  (never pre-binarize to ``uint8``).

The core is rank-2; the batch use-case applies it per timepoint, reading each
frame lazily from the store (so a 2D input broadcasts), rather than loading a
whole ``(T, H, W)`` stack into memory.

**Dilation is global, then clipped within cells (D8).** The mask is dilated
over the whole frame and *then* AND-ed within ``labels > 0`` — identical to
the interactive controller. A condensed blob in cell A near the A/B border
therefore grows across the border and removes dilute pixels inside neighbor
cell B; this is the intended global-halo semantic, pinned by a test.

No imports from ``qtpy``, ``PyQt5``, ``napari``, ``h5py``, or
``percell4.application``. Pure-domain isolation (numpy + skimage only).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from skimage.morphology import dilation, disk


def dilate_mask(
    mask: NDArray,
    radius_px: int,
) -> NDArray[np.bool_]:
    """Dilate a binary mask by a disk of the given radius in pixels.

    Lifted from ``controller.py:362-372``: for ``radius_px > 0`` grow the
    boolean mask with a symmetric ``disk(radius_px)`` footprint; for
    ``radius_px <= 0`` return the boolean mask unchanged (no dilation).

    Parameters
    ----------
    mask : array-like (H, W). Coerced to boolean via ``> 0`` semantics
        (``astype(bool)`` — any nonzero pixel is foreground).
    radius_px : disk **radius** in pixels (D2). ``0`` (or negative) means
        no dilation.

    Returns
    -------
    NDArray[np.bool_]
        The dilated boolean mask (or the unchanged boolean mask when
        ``radius_px <= 0``).
    """
    condensed = mask.astype(bool)
    if radius_px > 0:
        return dilation(condensed, footprint=disk(radius_px)).astype(bool)
    return condensed


def invert_within_cells(
    condensed: NDArray,
    seg_labels: NDArray,
) -> NDArray[np.bool_]:
    """Keep the in-cell pixels that the condensed mask does not cover.

    Lifted from ``controller.py:238-239``:
    ``in_cell = seg_labels > 0; dilute = in_cell & ~condensed``.

    Parameters
    ----------
    condensed : array-like (H, W) condensed-phase mask. Coerced to boolean
        via ``astype(bool)`` (any nonzero pixel is foreground).
    seg_labels : array-like (H, W) integer cell-label image; ``> 0`` is
        "inside a cell".

    Returns
    -------
    NDArray[np.bool_]
        ``(seg_labels > 0) & ~condensed`` — the dilute phase.
    """
    in_cell = seg_labels > 0
    return in_cell & ~condensed.astype(bool)


def dilute_from_mask(
    condensed: NDArray,
    seg_labels: NDArray,
    radius_px: int,
) -> NDArray[np.bool_]:
    """Dilate ``condensed`` by ``radius_px`` and invert it within cells.

    The composed op and the module's primary public function:

        dilute = (seg_labels > 0) AND NOT dilation(condensed, disk(radius_px))

    Returns a **boolean** array — the invariant the use-case preserves
    through ``np.stack`` (the writer ``write_dilute_mask`` requires bool and
    casts to ``uint8`` itself; never hand it a pre-binarized ``uint8``).

    Parameters
    ----------
    condensed : (H, W) condensed-phase mask (any nonzero pixel is
        foreground).
    seg_labels : (H, W) integer cell-label image (``> 0`` is in-cell).
    radius_px : disk **radius** in pixels (``0`` → no dilation, D2).

    Returns
    -------
    NDArray[np.bool_]
        The dilute-phase boolean mask, shape == ``condensed.shape``.

    Raises
    ------
    ValueError
        When ``condensed.shape != seg_labels.shape`` — surfaces a mask/seg
        mismatch as a clear reason rather than a numpy broadcast error
        (mirrors ``phasor_masks.py``'s shape-mismatch raise).
    """
    condensed = np.asarray(condensed)
    seg_labels = np.asarray(seg_labels)

    if condensed.shape != seg_labels.shape:
        raise ValueError(
            "condensed and seg_labels must share shape, got "
            f"{condensed.shape} and {seg_labels.shape}"
        )

    return invert_within_cells(dilate_mask(condensed, radius_px), seg_labels)
