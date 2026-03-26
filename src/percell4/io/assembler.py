"""Tile stitching, channel assembly, and Z-projection.

Pure numpy functions — no HDF5 or GUI dependencies.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


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
    # Determine starting corner
    start_bottom = "up" in order
    start_right = "left" in order

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
