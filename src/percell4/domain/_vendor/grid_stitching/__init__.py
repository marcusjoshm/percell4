# Vendored into percell4 from the user's `grid_stitching` package.
# Algorithm: Preibisch, Saalfeld & Tomancak 2009 (Fiji Grid/Collection Stitching,
# Bioinformatics 25(11):1463-1465). Numpy-only computational core.
"""
grid_stitching (vendored computational core)
============================================

The pure-numpy phase-correlation engine from the user's `grid_stitching`
package, vendored into the percell4 domain layer for reproducibility and to
keep the import-linter / qt-free-imports contracts green.

Only the *computational core* is vendored and re-exported. The package's
file-I/O helpers (`load_tile_images`, `parse_tile_configuration`,
`write_tile_configuration` — which use `tifffile` / `skimage.io` / `open`) and
the `Stitcher` orchestration class and `fuse` are intentionally NOT vendored:
percell4 already holds tiles in memory and writes its own placement loop.
"""

from .phase_correlation import (register_pair, phase_correlation_matrix,
                                PeakResult)
from .optimize import optimize_positions, PairwiseShift
from .pairwise import compute_pairwise_shifts
from .tiles import Tile, grid_positions

__all__ = [
    "register_pair", "phase_correlation_matrix", "PeakResult",
    "optimize_positions", "PairwiseShift",
    "compute_pairwise_shifts",
    "Tile", "grid_positions",
]
