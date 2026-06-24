# Vendored into percell4 from the user's `grid_stitching` package.
# Algorithm: Preibisch, Saalfeld & Tomancak 2009 (Fiji Grid/Collection Stitching,
# Bioinformatics 25(11):1463-1465). Numpy-only computational core.
#
# STRIPPED for the vendored copy: load_tile_images / parse_tile_configuration /
# write_tile_configuration (filesystem I/O via tifffile / skimage.io / open) and
# their `os`/`re` imports are removed so this module imports numpy + stdlib only.
# Only the `Tile` dataclass and `grid_positions` (plus the pure grid-order helpers
# they depend on) are retained — percell4 already holds tiles in memory and never
# needs the engine's file loaders.
"""
Tile representation and layout generation.

Reproduces the way the Fiji plugin obtains initial tile positions for
**grid layouts** -- "Grid: row-by-row / column-by-column / snake ...",
computed from grid size + tile size + overlap percentage.

This produces a list of :class:`Tile` objects with floating-point initial
positions that the optimizer then refines.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Tile:
    """A single image tile and its position in the global coordinate frame."""
    index: int
    image: np.ndarray | None = None       # pixel data (may be lazy-loaded)
    position: np.ndarray = None           # current (x, y[, z]) top-left coord
    filename: str | None = None
    grid_coord: tuple | None = None       # (col, row) when from a grid

    def __post_init__(self):
        if self.position is not None:
            self.position = np.asarray(self.position, dtype=np.float64)

    @property
    def ndim(self) -> int:
        return len(self.position)

    @property
    def shape(self) -> tuple:
        return self.image.shape if self.image is not None else None


# --------------------------------------------------------------------------
# Grid orderings.  Names mirror the plugin's "Type"/"Order" dropdown.
# Each generator yields (col, row) grid coordinates in *acquisition* order.
# --------------------------------------------------------------------------

def _row_by_row(cols, rows):
    for r in range(rows):
        for c in range(cols):
            yield (c, r)


def _column_by_column(cols, rows):
    for c in range(cols):
        for r in range(rows):
            yield (c, r)


def _snake_by_rows(cols, rows):
    for r in range(rows):
        rng = range(cols) if r % 2 == 0 else range(cols - 1, -1, -1)
        for c in rng:
            yield (c, r)


def _snake_by_columns(cols, rows):
    for c in range(cols):
        rng = range(rows) if c % 2 == 0 else range(rows - 1, -1, -1)
        for r in rng:
            yield (c, r)


_ORDERS = {
    "row-by-row": _row_by_row,
    "column-by-column": _column_by_column,
    "snake-by-rows": _snake_by_rows,
    "snake-by-columns": _snake_by_columns,
}


def grid_positions(grid_size_x: int, grid_size_y: int,
                   tile_width: int, tile_height: int,
                   overlap: float = 0.2,
                   order: str = "row-by-row") -> list:
    """
    Generate initial tile positions for a regular grid.

    Parameters
    ----------
    grid_size_x, grid_size_y : int
        Number of columns and rows.
    tile_width, tile_height : int
        Pixel dimensions of each tile.
    overlap : float
        Fractional overlap between neighbours (0.2 == 20 %), as in the
        plugin's "Tile overlap [%]" field.
    order : str
        One of the keys in ``_ORDERS``.

    Returns
    -------
    list[Tile]
        Tiles with grid_coord and initial (x, y) position set, in
        acquisition order.
    """
    if order not in _ORDERS:
        raise ValueError(f"unknown order '{order}'. "
                         f"choose from {sorted(_ORDERS)}")

    step_x = tile_width * (1.0 - overlap)
    step_y = tile_height * (1.0 - overlap)

    tiles = []
    for i, (c, r) in enumerate(_ORDERS[order](grid_size_x, grid_size_y)):
        pos = np.array([c * step_x, r * step_y], dtype=np.float64)
        tiles.append(Tile(index=i, position=pos, grid_coord=(c, r)))
    return tiles
