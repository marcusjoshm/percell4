# Vendored into percell4 from the user's `grid_stitching` package.
# Algorithm: Preibisch, Saalfeld & Tomancak 2009 (Fiji Grid/Collection Stitching,
# Bioinformatics 25(11):1463-1465). Numpy-only computational core; copied verbatim.
"""
Global tile-position optimization.

After pairwise phase correlation gives a relative shift for each
overlapping tile pair, the plugin resolves them into one globally
consistent set of absolute positions.  Pairwise shifts are noisy and
mutually inconsistent (a loop of three tiles rarely sums to zero), so a
global fit is required.

Approach (equivalent in spirit to the plugin's optimization):

* Treat every reliable pairwise shift as a linear constraint
  ``pos[j] - pos[i] = shift_ij`` weighted by its correlation.
* Solve the resulting over-determined linear system in a least-squares
  sense, per axis, fixing one tile as the origin to remove the global
  translation degree of freedom (gauge fixing).
* Tiles in connected components that have no reliable link to the anchor
  fall back to their initial (grid/file) position.

This is the standard graph/least-squares formulation of the global
registration problem and gives the same kind of result as the plugin's
optimizer for well-connected mosaics.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PairwiseShift:
    """A measured relative shift between tile i and tile j."""
    i: int
    j: int
    shift: np.ndarray       # pos[j] - pos[i], length == ndim
    weight: float           # confidence (e.g. correlation), >= 0


def _connected_components(n: int, edges: list) -> list:
    """Union-find connected components over tiles linked by edges."""
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for e in edges:
        union(e.i, e.j)

    comps = {}
    for v in range(n):
        comps.setdefault(find(v), []).append(v)
    return list(comps.values())


def optimize_positions(n_tiles: int,
                       shifts: list,
                       initial_positions: list,
                       ndim: int) -> np.ndarray:
    """
    Compute globally consistent absolute positions.

    Parameters
    ----------
    n_tiles : int
        Total number of tiles.
    shifts : list[PairwiseShift]
        Reliable pairwise shifts (already filtered by correlation).
    initial_positions : list[ndarray]
        Fallback positions (from grid/file), used for components not
        connected to the anchor and to translate each component into the
        original coordinate frame.
    ndim : int
        Number of spatial dimensions.

    Returns
    -------
    ndarray, shape (n_tiles, ndim)
        Optimized positions.
    """
    initial = np.asarray(initial_positions, dtype=np.float64)
    result = initial.copy()

    comps = _connected_components(n_tiles, shifts)

    # Map each tile to the list of shifts touching it for quick lookup.
    for comp in comps:
        if len(comp) == 1:
            continue  # isolated tile keeps its initial position

        comp_set = set(comp)
        local = {t: k for k, t in enumerate(comp)}    # tile -> local idx
        m = len(comp)

        comp_shifts = [s for s in shifts
                       if s.i in comp_set and s.j in comp_set]
        if not comp_shifts:
            continue

        # Build weighted least-squares system A x = b, per axis.
        # Each shift contributes one row: x[j] - x[i] = shift, * sqrt(w).
        # Plus one gauge row pinning the first tile of the component to 0.
        n_rows = len(comp_shifts) + 1
        A = np.zeros((n_rows, m), dtype=np.float64)
        b = np.zeros((n_rows, ndim), dtype=np.float64)

        for row, s in enumerate(comp_shifts):
            w = np.sqrt(max(s.weight, 1e-6))
            A[row, local[s.j]] += w
            A[row, local[s.i]] -= w
            b[row] = s.shift * w

        # Gauge: pin first tile of component to origin (strong weight).
        anchor_local = 0
        A[-1, anchor_local] = 1.0
        b[-1] = 0.0

        sol, *_ = np.linalg.lstsq(A, b, rcond=None)   # (m, ndim)

        # Re-anchor the component to the initial frame so different
        # components remain in their originally-intended locations:
        # align solution's anchor tile to its initial position.
        anchor_tile = comp[anchor_local]
        offset = initial[anchor_tile] - sol[anchor_local]
        for t in comp:
            result[t] = sol[local[t]] + offset

    return result
