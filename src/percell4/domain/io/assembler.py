"""Tile stitching, channel assembly, and Z-projection.

Pure numpy functions — no HDF5 or GUI dependencies.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from percell4.domain._vendor.grid_stitching import (
    compute_pairwise_shifts,
    grid_positions,
    optimize_positions,
)


class RegistrationError(RuntimeError):
    """Raised when phase-correlation registration produces a degenerate solve.

    A degenerate solve is one where too few tile pairs clear
    ``regression_threshold`` so most/all tiles fall back to their initial grid
    position — the result would collapse to a grid-equivalent canvas that must
    never be stored as "registered" (R11 success gate).
    """


def assemble_tiles(
    tiles: dict[int, NDArray],
    grid_rows: int,
    grid_cols: int,
    grid_type: str = "row_by_row",
    order: str = "right_down",
) -> NDArray:
    """Stitch tiles into a single composite image.

    Parameters
    ----------
    tiles : dict mapping tile index (0-based) to 2D array
    grid_rows, grid_cols : grid dimensions
    grid_type : 'row_by_row', 'column_by_column', 'snake_by_row', 'snake_by_column'
    order : 'right_down', 'right_up', 'left_down', 'left_up'

    Returns
    -------
    Stitched 2D array.
    """
    if not tiles:
        raise ValueError("No tiles to assemble")

    # Get tile shape from first tile
    first = next(iter(tiles.values()))
    tile_h, tile_w = first.shape[:2]

    # Build tile position mapping
    positions = _tile_positions(grid_rows, grid_cols, grid_type, order)

    # Allocate output
    out_h = grid_rows * tile_h
    out_w = grid_cols * tile_w
    output = np.zeros((out_h, out_w), dtype=first.dtype)

    for tile_idx, (row, col) in positions.items():
        if tile_idx not in tiles:
            continue
        y0 = row * tile_h
        x0 = col * tile_w
        output[y0 : y0 + tile_h, x0 : x0 + tile_w] = tiles[tile_idx]

    return output


def _tile_positions(
    rows: int, cols: int, grid_type: str, order: str
) -> dict[int, tuple[int, int]]:
    """Map tile index to (row, col) grid position.

    Parameters
    ----------
    grid_type : scanning pattern
        'row_by_row', 'column_by_column', 'snake_by_row', 'snake_by_column'
    order : starting corner and initial direction
        'right_down' — start top-left, scan right then down (default)
        'right_up'   — start bottom-left, scan right then up
        'left_down'  — start top-right, scan left then down
        'left_up'    — start bottom-right, scan left then up
    """
    # Normalize order to start_bottom / start_right flags
    # Accepts both old (right_down) and new (top_left) naming
    order_map = {
        "top_left": (False, False),
        "top_right": (False, True),
        "bottom_left": (True, False),
        "bottom_right": (True, True),
        "right_down": (False, False),
        "right_up": (True, False),
        "left_down": (False, True),
        "left_up": (True, True),
    }
    if order not in order_map:
        raise ValueError(f"Unknown order: {order!r}")
    start_bottom, start_right = order_map[order]

    # Build row and column sequences based on starting corner
    if start_bottom:
        row_seq = list(range(rows - 1, -1, -1))  # bottom to top
    else:
        row_seq = list(range(rows))  # top to bottom

    if start_right:
        col_seq = list(range(cols - 1, -1, -1))  # right to left
    else:
        col_seq = list(range(cols))  # left to right

    positions: dict[int, tuple[int, int]] = {}
    idx = 0

    if grid_type == "row_by_row":
        for r in row_seq:
            for c in col_seq:
                positions[idx] = (r, c)
                idx += 1
    elif grid_type == "column_by_column":
        for c in col_seq:
            for r in row_seq:
                positions[idx] = (r, c)
                idx += 1
    elif grid_type == "snake_by_row":
        for i, r in enumerate(row_seq):
            cs = col_seq if i % 2 == 0 else col_seq[::-1]
            for c in cs:
                positions[idx] = (r, c)
                idx += 1
    elif grid_type == "snake_by_column":
        for i, c in enumerate(col_seq):
            rs = row_seq if i % 2 == 0 else row_seq[::-1]
            for r in rs:
                positions[idx] = (r, c)
                idx += 1
    else:
        raise ValueError(f"Unknown grid_type: {grid_type!r}")

    return positions


# ──────────────────────────────────────────────────────────────────────────
# Overlap-aware (phase-correlation) registration + offset-driven placement.
#
# These three functions are the pure-domain core of the mosaic-merge stitching
# path. ``assemble_tiles`` / ``_tile_positions`` above remain the 0%-overlap
# edge-to-edge grid path and are untouched.
# ──────────────────────────────────────────────────────────────────────────

# PerCell4 carries the scan pattern as two fields: ``grid_type`` (row/column/
# snake) + ``order`` (start corner, e.g. ``right_down``). The vendored
# ``grid_positions`` accepts only a single hyphenated ``order`` string that
# encodes the row/column/snake pattern. This table maps PerCell4's ``grid_type``
# onto that vocabulary. The start-corner ``order`` has no ``grid_positions``
# concept: v1 always seeds from the top-left corner and lets phase-correlation
# refine the absolute positions from there — the seed only needs to be close
# enough for neighbouring tiles to overlap, which top-left seeding guarantees.
_GRID_TYPE_TO_VENDOR_ORDER: dict[str, str] = {
    "row_by_row": "row-by-row",
    "column_by_column": "column-by-column",
    "snake_by_row": "snake-by-rows",
    "snake_by_column": "snake-by-columns",
}

# Below this fraction of the expected grid-adjacent pairs (or with >half the
# tiles disconnected) the solve is treated as degenerate and rejected (R11
# success gate). Measured against grid adjacency (not the C(n, 2) combinatorial
# count), so a healthy mosaic where most adjacent pairs register passes.
_MIN_ACCEPTED_PAIR_FRACTION = 0.5


def canvas_from_offsets(
    offsets: NDArray, tile_shape: tuple[int, int]
) -> tuple[int, int]:
    """The single canvas computation for the registered path.

    The canvas bounding box is ``max(y0 + th)`` by ``max(x0 + tw)`` over the
    final non-negative integer offsets, where ``tile_shape == (th, tw)``.
    ``native_shape``, the intensity allocator, and the decay allocator all
    consume *this* value — the canvas is never recomputed elsewhere.

    Returned dims are plain Python ints (not numpy ints), so any element/byte
    sizing the caller does via :func:`math.prod` cannot wrap on Windows LLP64
    where ``np.prod`` would overflow int32 (registered mosaics get large).

    Parameters
    ----------
    offsets : (N, 2) int array of ``(y0, x0)`` top-left corners (per-axis min 0).
    tile_shape : ``(th, tw)`` — the uniform tile height and width.

    Returns
    -------
    (H, W) : tuple[int, int]
    """
    offsets = np.asarray(offsets)
    th, tw = int(tile_shape[0]), int(tile_shape[1])
    h = int(offsets[:, 0].max()) + th
    w = int(offsets[:, 1].max()) + tw
    return h, w


def estimate_tile_offsets(
    reference_tiles: dict[int, NDArray],
    grid_rows: int,
    grid_cols: int,
    grid_type: str,
    order: str,
    overlap: float,
) -> tuple[NDArray, tuple[int, int], dict]:
    """Compute integer per-tile pixel offsets from overlapping reference tiles.

    Seeds initial positions from ``grid_positions`` (grid + overlap), refines
    them by phase-correlation registration on the reference channel, then
    normalises the solved positions to non-negative integer ``(y0, x0)`` offsets.

    Parameters
    ----------
    reference_tiles : dict mapping 0-based tile index to a 2D array. All tiles
        must share one shape (raises ``ValueError`` otherwise — R14).
    grid_rows, grid_cols : grid dimensions.
    grid_type : ``'row_by_row'`` / ``'column_by_column'`` / ``'snake_by_row'`` /
        ``'snake_by_column'`` (mapped to the vendored vocabulary).
    order : start-corner string (``right_down`` …); v1 seeds from top-left and
        registration refines, so ``order`` only validates as a known value.
    overlap : fractional overlap in ``[0, 1)`` used to seed initial positions.

    Returns
    -------
    offsets : (N, 2) int32 ``(y0, x0)``, per-axis min exactly 0.
    canvas_hw : ``(H, W)`` from :func:`canvas_from_offsets`.
    quality : dict carrying ``correlations`` (per accepted pair), ``disconnected``
        (tile indices left at their seed position), ``accepted_pair_fraction``,
        ``coverage_fraction``, ``regression_threshold``, ``n_peaks``.

    Raises
    ------
    ValueError : non-uniform tile shapes, unknown ``grid_type``/``order``.
    RegistrationError : degenerate solve (R11 success gate).
    """
    if not reference_tiles:
        raise ValueError("No reference tiles to register")
    if grid_type not in _GRID_TYPE_TO_VENDOR_ORDER:
        raise ValueError(f"Unknown grid_type: {grid_type!r}")

    n_tiles = len(reference_tiles)
    indices = sorted(reference_tiles)

    # Uniform-shape gate (R14): reject ragged mosaics before any work.
    shapes = {reference_tiles[i].shape for i in indices}
    if len(shapes) != 1:
        raise ValueError(f"Reference tiles must have uniform shape, got {shapes}")
    th, tw = next(iter(shapes))[:2]

    vendor_order = _GRID_TYPE_TO_VENDOR_ORDER[grid_type]
    # Validate `order` against the existing grid-order vocabulary so a typo is
    # caught here even though v1 always seeds from the top-left corner.
    _tile_positions(grid_rows, grid_cols, grid_type, order)

    # Seed initial (x, y) positions from grid + overlap, then attach each
    # reference tile's array onto its image-less Tile (in tile-index order).
    seed_tiles = grid_positions(
        grid_size_x=grid_cols,
        grid_size_y=grid_rows,
        tile_width=tw,
        tile_height=th,
        overlap=float(overlap),
        order=vendor_order,
    )
    if len(seed_tiles) != n_tiles:
        raise ValueError(
            f"grid {grid_rows}x{grid_cols} expects {len(seed_tiles)} tiles, "
            f"got {n_tiles}"
        )
    for tile, idx in zip(seed_tiles, indices):
        tile.image = reference_tiles[idx]

    initial_positions = [t.position for t in seed_tiles]  # (x, y) order

    # Grid-prior band: the overlap distance in pixels. A solved tile may only
    # move within this band of its grid seed, so registration refines WITHIN the
    # overlap region and can never relocate a tile a tile-width away (the
    # full-tile spurious-peak failure mode). Auto-derived from the overlap
    # geometry — the in-house analogue of ASHLAR's max_shift / MIST's (4r)^2,
    # but tied to the overlap fraction rather than a user micron value.
    band_px = int(math.ceil(float(overlap) * max(int(th), int(tw))))

    # Phase-correlation pairwise shifts + global least-squares optimisation.
    regression_threshold = 0.3
    n_peaks = 5
    shifts = compute_pairwise_shifts(
        seed_tiles,
        ndim=2,
        n_peaks=n_peaks,
        regression_threshold=regression_threshold,
        max_dev=band_px if band_px > 0 else None,
    )

    # Iterative outlier rejection (ImageJ-style): solve, drop the single pairwise
    # shift least consistent with the global solution, re-solve — until every
    # surviving shift agrees within `outlier_threshold` px or only a spanning set
    # of pairs remains. Removes the few mis-registered pairs that would otherwise
    # pull the least-squares and bow the far-corner tiles.
    outlier_threshold = 2.5
    n_rejected = 0
    positions_xy = optimize_positions(
        n_tiles, shifts, initial_positions, ndim=2
    )  # (N, 2) in (x, y)
    while len(shifts) > n_tiles:
        residuals = [
            float(
                np.abs(
                    np.asarray(s.shift) - (positions_xy[s.j] - positions_xy[s.i])
                ).max()
            )
            for s in shifts
        ]
        worst = int(np.argmax(residuals))
        if residuals[worst] <= outlier_threshold:
            break
        shifts.pop(worst)
        n_rejected += 1
        positions_xy = optimize_positions(
            n_tiles, shifts, initial_positions, ndim=2
        )

    # Disconnected = tiles touched by no accepted pair (optimize_positions left
    # them at their seed / initial position; never trusted to win an overlap).
    connected: set[int] = set()
    for s in shifts:
        connected.add(s.i)
        connected.add(s.j)
    # Map vendor's 0..N-1 tile slot back to the caller's tile indices.
    slot_to_index = {slot: idx for slot, idx in enumerate(indices)}
    disconnected = sorted(
        slot_to_index[slot] for slot in range(n_tiles) if slot not in connected
    )

    # Final hard clamp (grid-prior band): pin every solved position to within
    # band_px of its own grid seed on each axis. The per-pair band gate already
    # keeps each measured shift in-band, but a global least-squares chain could
    # in principle accumulate drift; this clamp makes a tile escaping its
    # overlap band MATHEMATICALLY IMPOSSIBLE regardless of solver pathology.
    clamped_tiles: list[int] = []
    if band_px > 0:
        init_xy = np.asarray(initial_positions, dtype=np.float64)
        pos_xy = np.asarray(positions_xy, dtype=np.float64)
        clipped = np.minimum(np.maximum(pos_xy, init_xy - band_px), init_xy + band_px)
        moved = np.any(clipped != pos_xy, axis=1)
        clamped_tiles = sorted(
            slot_to_index[slot] for slot in range(n_tiles) if bool(moved[slot])
        )
        positions_xy = clipped

    # Convert (x, y) positions → image-axis (y0, x0); round to int.
    offsets = np.empty((n_tiles, 2), dtype=np.int64)
    for slot in range(n_tiles):
        x, y = positions_xy[slot]
        offsets[slot, 0] = int(round(float(y)))  # y0
        offsets[slot, 1] = int(round(float(x)))  # x0

    # Normalise to a non-negative origin: subtract per-axis min so min is 0.
    offsets[:, 0] -= int(offsets[:, 0].min())
    offsets[:, 1] -= int(offsets[:, 1].min())
    if not (int(offsets[:, 0].min()) == 0 and int(offsets[:, 1].min()) == 0):
        # Data invariant, not a debug check — must survive ``python -O``.
        raise ValueError("offset normalisation must leave per-axis min == 0")
    offsets = offsets.astype(np.int32)

    # Index offsets by the caller's tile index (offsets[tile_index]).
    offsets_by_index = np.zeros((n_tiles, 2), dtype=np.int32)
    for slot, idx in slot_to_index.items():
        offsets_by_index[idx] = offsets[slot]
    offsets = offsets_by_index

    canvas_hw = canvas_from_offsets(offsets, (th, tw))

    # Quality metrics.
    #
    # ``compute_pairwise_shifts`` only emits shifts for SEED-OVERLAPPING
    # (grid-adjacent) tile pairs, never every C(n, 2) combination — so the
    # denominator must be the expected grid adjacency count, not
    # ``n_tiles*(n_tiles-1)//2``. Using the combinatorial count false-rejected
    # every mosaic >=4x4 (a perfect 4x4 emits ~24 shifts / 120 combos = 0.20).
    # n_adjacent = horizontal edges + vertical edges in the grid.
    n_adjacent = (grid_cols - 1) * grid_rows + (grid_rows - 1) * grid_cols
    accepted_pair_fraction = (
        len(shifts) / n_adjacent if n_adjacent else 0.0
    )
    coverage_fraction = _coverage_fraction(offsets, (th, tw), canvas_hw)
    correlations = [float(s.weight) for s in shifts]
    quality = {
        "correlations": correlations,
        "disconnected": disconnected,
        "accepted_pair_fraction": float(accepted_pair_fraction),
        "coverage_fraction": float(coverage_fraction),
        "regression_threshold": float(regression_threshold),
        "n_peaks": int(n_peaks),
        "n_adjacent": int(n_adjacent),
        "overlap_band_px": int(band_px),
        "clamped_tiles": list(clamped_tiles),
        "outlier_threshold": float(outlier_threshold),
        "rejected_outliers": int(n_rejected),
    }

    # Degenerate-solve gate (R11): refuse to return a grid-equivalent canvas
    # that a caller would store as "registered". Measured against the expected
    # grid adjacency (above) so a healthy large mosaic — where most adjacent
    # pairs register — passes, while a low-contrast solve (few adjacent pairs
    # accepted, or most tiles left at their seed) is rejected.
    if n_tiles > 1 and (
        len(disconnected) > n_tiles // 2
        or (n_adjacent and accepted_pair_fraction < _MIN_ACCEPTED_PAIR_FRACTION)
    ):
        raise RegistrationError(
            "Degenerate registration: only "
            f"{len(shifts)}/{n_adjacent} adjacent tile pairs cleared "
            f"regression_threshold={regression_threshold} "
            f"({len(disconnected)}/{n_tiles} tiles disconnected). "
            "The reference channel is likely too low-contrast to register; "
            "the solve collapses to the grid layout and must not be stored "
            "as registered."
        )

    return offsets, canvas_hw, quality


def _coverage_fraction(
    offsets: NDArray, tile_shape: tuple[int, int], canvas_hw: tuple[int, int]
) -> float:
    """Fraction of canvas pixels covered by at least one placed tile."""
    h, w = int(canvas_hw[0]), int(canvas_hw[1])
    total = math.prod((h, w))
    if total == 0:
        return 0.0
    th, tw = int(tile_shape[0]), int(tile_shape[1])
    covered = np.zeros((h, w), dtype=bool)
    for y0, x0 in offsets:
        covered[int(y0) : int(y0) + th, int(x0) : int(x0) + tw] = True
    return float(int(covered.sum()) / total)


def assemble_tiles_with_offsets(
    tiles: dict[int, NDArray],
    offsets: NDArray,
    canvas_hw: tuple[int, int],
    fill_value: int = 0,
    disconnected: tuple[int, ...] = (),
) -> NDArray:
    """Place tiles at arbitrary integer offsets with overwrite fusion.

    A hand-written integer overwrite loop — deliberately NOT the vendored
    ``fuse`` (which upcasts to float64, re-clips, iterates in list order, and
    recomputes the canvas). The canvas is pinned once and passed in.

    Overlap priority is pinned **ascending tile index** so the highest tile
    index wins every overlap pixel — identical to the decay streamer's
    ``sorted(tile_bins.items())`` last-writer-wins, so intensity and decay
    resolve every overlap pixel to the same tile (R7). **Disconnected tiles are
    placed first (lowest priority)** so an untrusted, mis-placed tile can never
    overwrite a correctly-registered neighbour's overlap region.

    Parameters
    ----------
    tiles : dict mapping 0-based tile index to a 2D array.
    offsets : (N, 2) int array of ``(y0, x0)`` indexed by tile index.
    canvas_hw : ``(H, W)`` from :func:`canvas_from_offsets`.
    fill_value : value for uncovered canvas pixels (distinguishable from genuine
        zero signal when set non-zero).
    disconnected : tile indices to demote to lowest overwrite priority.

    Returns
    -------
    ndarray of shape ``canvas_hw``, dtype matching the tiles.
    """
    if not tiles:
        raise ValueError("No tiles to assemble")

    first = next(iter(tiles.values()))
    h, w = int(canvas_hw[0]), int(canvas_hw[1])
    output = np.full((h, w), fill_value, dtype=first.dtype)

    disconnected_set = set(disconnected)
    # Disconnected tiles first (lowest priority), then the rest in ascending
    # tile index so the highest registered index wins overlaps.
    placement_order = sorted(
        tiles, key=lambda idx: (idx not in disconnected_set, idx)
    )
    offsets = np.asarray(offsets)
    for tile_idx in placement_order:
        tile = tiles[tile_idx]
        th, tw = tile.shape[:2]
        y0 = int(offsets[tile_idx, 0])
        x0 = int(offsets[tile_idx, 1])
        output[y0 : y0 + th, x0 : x0 + tw] = tile

    return output


def assemble_channels(channel_images: list[NDArray]) -> NDArray:
    """Stack multiple 2D channel images into a (C, H, W) array.

    All images must have the same spatial dimensions.
    """
    if not channel_images:
        raise ValueError("No channel images to assemble")
    shapes = {img.shape for img in channel_images}
    if len(shapes) > 1:
        raise ValueError(f"Channel images have different shapes: {shapes}")
    return np.stack(channel_images, axis=0).astype(np.float32)


def stack_timepoints(planes: list[NDArray]) -> NDArray:
    """Stack per-timepoint planes into a leading time axis.

    Each plane is the fully-assembled image for one timepoint — a 2D
    ``(H, W)`` plane (single channel) or any higher-rank array, as long as
    all planes share a shape. The result adds a leading ``T`` axis:
    ``(T, H, W)`` from 2D planes. Dtype is preserved.

    Used by the importer after grouping a channel's files by timepoint;
    a single plane should be returned directly by the caller (no singleton
    time axis) to keep non-time-lapse datasets byte-identical.
    """
    if not planes:
        raise ValueError("No timepoints to stack")
    shapes = {p.shape for p in planes}
    if len(shapes) > 1:
        raise ValueError(f"Timepoint planes have different shapes: {shapes}")
    return np.stack(planes, axis=0)


def project_z(
    z_slices: list[NDArray] | None = None,
    method: str = "mip",
    *,
    streaming_paths: list[str] | None = None,
    read_fn=None,
) -> NDArray:
    """Flatten a z-series to a single 2D image.

    Uses streaming in-place accumulation — never loads all slices at once.

    Parameters
    ----------
    z_slices : list of 2D arrays (if data is already in memory)
    method : 'mip' (max intensity projection), 'mean', 'sum'
    streaming_paths : list of file paths for streaming mode
    read_fn : callable that reads a path and returns a 2D array

    Returns
    -------
    2D projected array.
    """
    if streaming_paths is not None and read_fn is not None:
        return _project_z_streaming(streaming_paths, read_fn, method)

    if z_slices is None or len(z_slices) == 0:
        raise ValueError("No z-slices to project")

    # In-memory streaming: accumulate without stacking all slices
    result = z_slices[0].astype(np.float64 if method != "mip" else z_slices[0].dtype).copy()
    n = len(z_slices)

    if method == "mip":
        for sl in z_slices[1:]:
            np.maximum(result, sl, out=result)
    elif method == "sum":
        # Use int64 for integer sums to prevent overflow
        result = result.astype(np.int64) if np.issubdtype(result.dtype, np.integer) else result
        for sl in z_slices[1:]:
            result += sl
    elif method == "mean":
        result = result.astype(np.float64)
        for sl in z_slices[1:]:
            result += sl
        result /= n
    else:
        raise ValueError(f"Unknown projection method: {method!r}")

    return result.astype(np.float32) if method in ("mean", "sum") else result


def _project_z_streaming(
    paths: list[str], read_fn, method: str
) -> NDArray:
    """Stream z-slices from disk one at a time."""
    first = read_fn(paths[0])
    if method == "mip":
        result = first.copy()
        for p in paths[1:]:
            np.maximum(result, read_fn(p), out=result)
    elif method == "sum":
        result = first.astype(np.int64)
        for p in paths[1:]:
            result += read_fn(p)
        result = result.astype(np.float32)
    elif method == "mean":
        result = first.astype(np.float64)
        for p in paths[1:]:
            result += read_fn(p)
        result = (result / len(paths)).astype(np.float32)
    else:
        raise ValueError(f"Unknown projection method: {method!r}")
    return result
