# Vendored into percell4 from the user's `grid_stitching` package.
# Algorithm: Preibisch, Saalfeld & Tomancak 2009 (Fiji Grid/Collection Stitching,
# Bioinformatics 25(11):1463-1465). Numpy-only computational core.
#
# RELOCATED for the vendored copy: `compute_pairwise_shifts` and its private
# helpers (`_overlap_region`, `_img_shape_xy`, `_slice_xy`, `_imgshift_to_xy`)
# are moved here, out of the source `stitcher.py`. In the source they live
# alongside the `Stitcher` class, whose module-level imports pull in the stripped
# file-I/O loaders; isolating them here keeps this module importing numpy + the
# vendored siblings only (no `Stitcher`, no `fuse`, no file I/O).
"""
Pairwise relative-shift estimation between overlapping tiles.

For every pair of tiles whose initial positions overlap, estimate the
relative shift by phase correlation (restricted to the overlapping region
for differently-sized tiles), keeping only shifts whose correlation
exceeds a threshold. Downstream, :func:`optimize_positions` resolves these
pairwise shifts into one globally consistent set of absolute positions.
"""

from __future__ import annotations

import itertools

import numpy as np

from .optimize import PairwiseShift
from .phase_correlation import register_pair


def _overlap_region(pos_i, shape_i, pos_j, shape_j, ndim):
    """
    Bounding box (in each tile's local frame) of the region where two
    tiles overlap given their current positions.  Returns None if they
    do not overlap.  Positions/shapes are in (x, y[, z]) order.
    """
    lo = np.maximum(pos_i, pos_j)
    hi = np.minimum(pos_i + shape_i, pos_j + shape_j)
    if np.any(hi <= lo):
        return None
    # Common integer extent per axis so both sub-regions have *identical*
    # shape (rounding of float positions can otherwise differ by 1 px).
    sl_i, sl_j = [], []
    for a in range(ndim):
        start_i = int(round(lo[a] - pos_i[a]))
        start_j = int(round(lo[a] - pos_j[a]))
        extent = int(np.floor(hi[a] - lo[a]))
        # clamp so neither slice runs past its tile
        extent = min(extent,
                     int(shape_i[a]) - start_i,
                     int(shape_j[a]) - start_j)
        if extent <= 0:
            return None
        sl_i.append(slice(start_i, start_i + extent))
        sl_j.append(slice(start_j, start_j + extent))
    return tuple(sl_i), tuple(sl_j)


def _img_shape_xy(image, ndim):
    """Return image shape in (x, y[, z]) order to match positions."""
    if image.ndim == 2:                 # (y, x)
        s = np.array([image.shape[1], image.shape[0]])
    elif image.ndim == 3:               # (z, y, x)
        s = np.array([image.shape[2], image.shape[1], image.shape[0]])
    else:
        raise ValueError("only 2D/3D tiles supported")
    return s[:ndim]


def _slice_xy(image, sl_xy):
    """Apply an (x, y[, z])-ordered slice tuple to an image array."""
    if image.ndim == 2:
        return image[sl_xy[1], sl_xy[0]]
    else:  # (z, y, x)
        z = sl_xy[2] if len(sl_xy) >= 3 else slice(None)
        return image[z, sl_xy[1], sl_xy[0]]


def _imgshift_to_xy(shift, img_ndim, ndim):
    """Convert an image-axis shift (row,col[,plane]) to (x,y[,z])."""
    shift = np.asarray(shift, dtype=np.float64)
    if img_ndim == 2:                   # (y, x) -> (x, y)
        out = np.array([shift[1], shift[0]])
    else:                               # (z, y, x) -> (x, y, z)
        out = np.array([shift[2], shift[1], shift[0]])
    return out[:ndim]


def compute_pairwise_shifts(tiles, ndim,
                            n_peaks=5,
                            regression_threshold=0.3,
                            only_neighbors=True):
    """
    Estimate relative shifts for all overlapping tile pairs.

    Full tiles are phase-correlated against each other (they share a
    large textured overlap), and the multi-peak / sign-disambiguation
    machinery in :func:`register_pair` selects the shift whose real-space
    overlap correlation is highest.  Only pairs whose initial positions
    overlap, and whose resulting correlation exceeds
    ``regression_threshold``, are kept.
    """
    shifts = []
    positions = [t.position for t in tiles]
    shapes = [_img_shape_xy(t.image, ndim) for t in tiles]

    for i, j in itertools.combinations(range(len(tiles)), 2):
        # Skip pairs that are not expected to overlap at all.
        reg = _overlap_region(positions[i], shapes[i],
                              positions[j], shapes[j], ndim)
        if reg is None:
            continue
        if tiles[i].image.shape != tiles[j].image.shape:
            # Full-tile correlation needs equal shapes; fall back to the
            # cropped overlap region when tile sizes differ.
            sl_i, sl_j = reg
            a = _slice_xy(tiles[i].image, sl_i)
            b = _slice_xy(tiles[j].image, sl_j)
            if min(a.shape) < 4 or a.shape != b.shape:
                continue
            res = register_pair(a, b, n_peaks=n_peaks)
            if res.correlation < regression_threshold:
                continue
            sub_shift = _imgshift_to_xy(res.shift, tiles[i].image.ndim, ndim)
            rel = (positions[j] - positions[i]) - sub_shift
            shifts.append(PairwiseShift(i=i, j=j, shift=rel,
                                        weight=res.correlation))
            continue

        # Equal-sized tiles: correlate the whole tiles.
        res = register_pair(tiles[i].image, tiles[j].image,
                            n_peaks=n_peaks)
        if res.correlation < regression_threshold:
            continue
        # res.shift maps tile j onto tile i in image-axis order; this *is*
        # the relative offset pos[j]-pos[i] expressed as (x, y[, z]).
        rel = _imgshift_to_xy(res.shift, tiles[i].image.ndim, ndim)
        shifts.append(PairwiseShift(i=i, j=j, shift=rel,
                                    weight=res.correlation))
    return shifts
