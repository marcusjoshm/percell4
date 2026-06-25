"""Tests for overlap-aware phase-correlation registration + offset placement.

Covers U3 of the mosaic-merge overlap-stitching plan: the three pure functions
``canvas_from_offsets`` / ``estimate_tile_offsets`` / ``assemble_tiles_with_offsets``
in ``percell4.domain.io.assembler``.

The AXIS-SWAP guard is load-bearing: the prior critical FLIM bug class came from
an ``(x, y) <-> (y, x)`` transposition, so an *asymmetric* shift must recover
``(y0, x0)`` in image-axis order, never swapped.
"""

from __future__ import annotations

import numpy as np
import pytest

# Map PerCell4's two-field (grid_type, order) onto grid_positions' single
# hyphenated order vocabulary. Imported from the module under test so the test
# pins the public mapping table.
from percell4.domain.io.assembler import (
    _GRID_TYPE_TO_VENDOR_ORDER,
    RegistrationError,
    assemble_tiles_with_offsets,
    canvas_from_offsets,
    estimate_tile_offsets,
)

# ── Synthetic mosaic helpers ──────────────────────────────────


def _textured_tile(rng, h, w):
    """A richly-textured tile so phase correlation has a sharp peak."""
    return (rng.random((h, w)) * 1000).astype(np.float32)


def _inband_pair(overlap=0.25, h=64, w=64, jitter_y=5, jitter_x=-3, seed=1234):
    """A 1x2 grid-adjacent pair: tile 1 at the grid step plus a small in-band
    jitter (realistic stage drift), so it survives the overlap-band constraint.

    Returns ``(tiles, overlap, (dy, dx))`` where ``(dy, dx)`` is tile 1's true
    image-axis offset relative to tile 0. The jitter keeps the pair within the
    overlap band of its grid seed, so the grid-prior constraint keeps it.
    """
    step = int(round(w * (1 - overlap)))
    dy = int(jitter_y)
    dx = int(step + jitter_x)
    rng = np.random.default_rng(seed)
    big = (rng.random((h + dy + 8, w + dx + 8)) * 1000).astype(np.float32)
    t0 = big[0:h, 0:w].copy()
    t1 = big[dy : dy + h, dx : dx + w].copy()
    return {0: t0, 1: t1}, overlap, (dy, dx)


def _cell_like_grid(rows, cols, th, tw, overlap, seed=0):
    """Cut a grid of quasi-repetitive 'cell-like' tiles (many similar blobs +
    a faint periodic background) — the texture that makes UNCONSTRAINED
    full-tile phase correlation lock onto spurious peaks and relocate tiles
    1-2 tile-widths away. Adjacent tiles share the requested overlap.
    """
    rng = np.random.default_rng(seed)
    step_y = int(round(th * (1 - overlap)))
    step_x = int(round(tw * (1 - overlap)))
    big_h = step_y * (rows - 1) + th
    big_w = step_x * (cols - 1) + tw
    field = np.zeros((big_h, big_w), np.float32)
    # Sparse, quasi-repetitive blobs: dense enough to register confidently,
    # sparse enough that full-tile correlation locks onto the wrong peak.
    n_blobs = (big_h * big_w) // 3000
    ys = rng.integers(0, big_h, n_blobs)
    xs = rng.integers(0, big_w, n_blobs)
    yy, xx = np.ogrid[-12:13, -12:13]
    blob = np.exp(-(yy**2 + xx**2) / 40.0).astype(np.float32)
    bh, bw = blob.shape
    for y, x in zip(ys, xs):
        y0 = min(int(y), big_h - bh)
        x0 = min(int(x), big_w - bw)
        field[y0 : y0 + bh, x0 : x0 + bw] += blob
    gy, gx = np.mgrid[0:big_h, 0:big_w]
    field += 0.3 * np.sin(2 * np.pi * gx / 47.0) * np.sin(2 * np.pi * gy / 47.0)
    tiles: dict[int, np.ndarray] = {}
    idx = 0
    for r in range(rows):
        for c in range(cols):
            y0, x0 = r * step_y, c * step_x
            tiles[idx] = field[y0 : y0 + th, x0 : x0 + tw].copy()
            idx += 1
    return tiles


# ── canvas_from_offsets ───────────────────────────────────────


def test_canvas_from_offsets_basic_bbox():
    """Canvas is max(y0+th), max(x0+tw) over offsets."""
    offsets = np.array([[0, 0], [0, 50], [60, 0], [60, 50]], dtype=np.int32)
    h, w = canvas_from_offsets(offsets, (64, 64))
    assert (h, w) == (60 + 64, 50 + 64)


def test_canvas_from_offsets_single_tile():
    offsets = np.array([[0, 0]], dtype=np.int32)
    assert canvas_from_offsets(offsets, (10, 20)) == (10, 20)


def test_canvas_from_offsets_large_no_overflow():
    """math.prod-based sizing returns correct huge dims without overflow.

    np.prod would wrap to a negative int32 on Windows LLP64; the returned
    dims must be exact python ints whose product exceeds 2**31.
    """
    th, tw = 60000, 60000
    offsets = np.array([[0, 0]], dtype=np.int32)
    h, w = canvas_from_offsets(offsets, (th, tw))
    assert (h, w) == (60000, 60000)
    import math

    assert math.prod((h, w)) == 3_600_000_000  # > 2**31, would overflow int32
    assert h * w == 3_600_000_000


# ── estimate_tile_offsets: happy + axis-swap ──────────────────


def test_estimate_recovers_known_offset_1x2():
    """A 1x2 mosaic with known overlap recovers the seeded layout."""
    h = w = 64
    overlap = 0.25
    rng = np.random.default_rng(7)
    big = (rng.random((h, w + int(w * (1 - overlap)) + 5)) * 1000).astype(np.float32)
    step = int(round(w * (1 - overlap)))
    t0 = big[:, 0:w].copy()
    t1 = big[:, step : step + w].copy()
    offsets, canvas, quality = estimate_tile_offsets(
        {0: t0, 1: t1},
        grid_rows=1,
        grid_cols=2,
        grid_type="row_by_row",
        order="right_down",
        overlap=overlap,
    )
    assert offsets.shape == (2, 2)
    # Tile 0 at origin, tile 1 shifted right by `step` in x (axis 1).
    assert tuple(offsets[0]) == (0, 0)
    assert abs(int(offsets[1][0]) - 0) <= 1      # y ~ 0
    assert abs(int(offsets[1][1]) - step) <= 1   # x ~ step
    assert canvas == canvas_from_offsets(offsets, (h, w))


def test_axis_swap_guard_asymmetric_offset():
    """CRITICAL: an asymmetric in-band shift recovers (y0,x0) image-axis order.

    The asymmetry (dy != dx) distinguishes (y,x) from (x,y) and pins the
    image-axis convention the prior FLIM cross-layer bug violated. The offset
    stays within the overlap band of the grid seed (realistic stage drift), so
    the grid-prior constraint keeps the pair instead of rejecting it.
    """
    tiles, overlap, (dy, dx) = _inband_pair(overlap=0.25, jitter_y=5, jitter_x=-3)
    offsets, _canvas, _quality = estimate_tile_offsets(
        tiles,
        grid_rows=1,
        grid_cols=2,
        grid_type="row_by_row",
        order="right_down",
        overlap=overlap,
    )
    # tile 1 relative to tile 0 must be (dy, dx), NOT (dx, dy).
    rel_y = int(offsets[1][0]) - int(offsets[0][0])
    rel_x = int(offsets[1][1]) - int(offsets[0][1])
    assert (rel_y, rel_x) == (dy, dx), (
        f"axis swapped: got (y,x)=({rel_y},{rel_x}), expected ({dy},{dx})"
    )


def test_estimate_offsets_min_zero_per_axis():
    """Returned offsets always have per-axis min exactly 0."""
    tiles, overlap, _ = _inband_pair()
    offsets, _canvas, _quality = estimate_tile_offsets(
        tiles,
        grid_rows=1,
        grid_cols=2,
        grid_type="row_by_row",
        order="right_down",
        overlap=overlap,
    )
    assert int(offsets[:, 0].min()) == 0
    assert int(offsets[:, 1].min()) == 0


def test_estimate_offsets_dtype_int32():
    tiles, overlap, _ = _inband_pair()
    offsets, _canvas, _quality = estimate_tile_offsets(
        tiles, grid_rows=1, grid_cols=2, grid_type="row_by_row",
        order="right_down", overlap=overlap,
    )
    assert offsets.dtype == np.int32


def test_estimate_quality_carries_engine_params():
    tiles, overlap, _ = _inband_pair()
    _offsets, _canvas, quality = estimate_tile_offsets(
        tiles, grid_rows=1, grid_cols=2, grid_type="row_by_row",
        order="right_down", overlap=overlap,
    )
    assert quality["regression_threshold"] == 0.3
    assert quality["n_peaks"] == 5
    assert "correlations" in quality
    assert "disconnected" in quality
    assert "accepted_pair_fraction" in quality
    assert "coverage_fraction" in quality
    assert "n_adjacent" in quality
    assert 0.0 <= quality["coverage_fraction"] <= 1.0


# ── large-mosaic success gate (FIX A) ─────────────────────────


def _overlapping_grid_tiles(grid_rows, grid_cols, overlap, h=48, w=48, seed=0):
    """Cut a perfect overlapping grid of richly-textured tiles from one field.

    Returns a row-by-row tile dict (idx -> array). Every adjacent pair shares
    the requested overlap so phase correlation has a sharp peak on each edge.
    """
    rng = np.random.default_rng(seed)
    step_y = int(round(h * (1 - overlap)))
    step_x = int(round(w * (1 - overlap)))
    big_h = step_y * (grid_rows - 1) + h
    big_w = step_x * (grid_cols - 1) + w
    big = (rng.random((big_h, big_w)) * 1000).astype(np.float32)
    tiles: dict[int, np.ndarray] = {}
    idx = 0
    for r in range(grid_rows):
        for c in range(grid_cols):
            y0 = r * step_y
            x0 = c * step_x
            tiles[idx] = big[y0 : y0 + h, x0 : x0 + w].copy()
            idx += 1
    return tiles


def test_perfect_4x4_mosaic_does_not_raise():
    """FIX A: a perfect 4x4 overlapping mosaic registers (no false-reject).

    With the old C(n, 2) denominator a perfect 4x4 emitted ~24 shifts / 120
    combinations = 0.20 < 0.25 and wrongly raised RegistrationError. Against
    the grid-adjacency count (24 edges) the fraction is ~1.0 and it passes.
    """
    tiles = _overlapping_grid_tiles(4, 4, overlap=0.25, seed=4)
    offsets, canvas, quality = estimate_tile_offsets(
        tiles, grid_rows=4, grid_cols=4, grid_type="row_by_row",
        order="right_down", overlap=0.25,
    )
    assert offsets.shape == (16, 2)
    assert canvas == canvas_from_offsets(offsets, tiles[0].shape)
    # 24 grid-adjacent edges for a 4x4: 3*4 horizontal + 3*4 vertical.
    assert quality["n_adjacent"] == 24
    # A clean solve clears (nearly) all adjacent pairs; well above 0.5.
    assert quality["accepted_pair_fraction"] >= 0.5


def test_perfect_5x5_mosaic_does_not_raise():
    """FIX A: a perfect 5x5 overlapping mosaic registers (no false-reject)."""
    tiles = _overlapping_grid_tiles(5, 5, overlap=0.25, seed=5)
    offsets, canvas, quality = estimate_tile_offsets(
        tiles, grid_rows=5, grid_cols=5, grid_type="row_by_row",
        order="right_down", overlap=0.25,
    )
    assert offsets.shape == (25, 2)
    assert canvas == canvas_from_offsets(offsets, tiles[0].shape)
    # 40 grid-adjacent edges for a 5x5: 4*5 horizontal + 4*5 vertical.
    assert quality["n_adjacent"] == 40
    assert quality["accepted_pair_fraction"] >= 0.5


# ── degenerate solve gate ─────────────────────────────────────


def test_degenerate_solve_raises():
    """Blank/uniform tiles produce no reliable pairs -> raise, not return."""
    blank = {
        0: np.zeros((64, 64), dtype=np.float32),
        1: np.zeros((64, 64), dtype=np.float32),
        2: np.zeros((64, 64), dtype=np.float32),
        3: np.zeros((64, 64), dtype=np.float32),
    }
    with pytest.raises(RegistrationError):
        estimate_tile_offsets(
            blank, grid_rows=2, grid_cols=2, grid_type="row_by_row",
            order="right_down", overlap=0.2,
        )


# ── overlap-band constraint (grid-prior + final clamp) ────────


def test_overlap_band_constrains_drift_on_ambiguous_texture():
    """Regression: on quasi-repetitive cell-like texture the UNCONSTRAINED
    engine relocated tiles 1-2 tile-widths away (~300px on a 200px tile). With
    the grid-prior band filter + final clamp, no tile may sit more than the
    overlap band from its grid seed — the user's 'a tile only ever moves within
    its ~10% overlap'. Either the solve stays in-band or it raises (collapses to
    grid); it must NEVER place a tile at an arbitrary overlap.
    """
    rows = cols = 3
    th = tw = 200
    overlap = 0.10
    band = int(np.ceil(overlap * max(th, tw)))  # 20 px
    tiles = _cell_like_grid(rows, cols, th, tw, overlap)
    try:
        offsets, _canvas, quality = estimate_tile_offsets(
            tiles, rows, cols, "row_by_row", "right_down", overlap
        )
    except RegistrationError:
        return  # acceptable: collapses to grid, never an arbitrary overlap
    step_y = th * (1 - overlap)
    step_x = tw * (1 - overlap)
    for idx in range(rows * cols):
        r, c = idx // cols, idx % cols
        # deviation from the ideal grid, relative to tile 0 (normalisation-free)
        rel_y = int(offsets[idx][0]) - int(offsets[0][0])
        rel_x = int(offsets[idx][1]) - int(offsets[0][1])
        assert abs(rel_y - r * step_y) <= 2 * band + 1, (idx, rel_y, r * step_y)
        assert abs(rel_x - c * step_x) <= 2 * band + 1, (idx, rel_x, c * step_x)
    assert quality["overlap_band_px"] == band


def test_clean_mosaic_unaffected_by_band():
    """A perfect overlapping mosaic still registers to the grid step exactly;
    the band constraint must not perturb a good solve, and clamps nothing."""
    tiles = _overlapping_grid_tiles(3, 3, overlap=0.2, seed=3)  # h=w=48
    offsets, _canvas, quality = estimate_tile_offsets(
        tiles, 3, 3, "row_by_row", "right_down", overlap=0.2
    )
    step = int(round(48 * (1 - 0.2)))  # 38
    # tile 1 one step right of tile 0; tile 3 one step below tile 0.
    assert abs((int(offsets[1][1]) - int(offsets[0][1])) - step) <= 1
    assert abs((int(offsets[3][0]) - int(offsets[0][0])) - step) <= 1
    assert quality["clamped_tiles"] == []


def test_quality_carries_overlap_band():
    """The band + clamp report into the quality dict for reproducibility."""
    tiles, overlap, _ = _inband_pair()
    _o, _c, quality = estimate_tile_offsets(
        tiles, grid_rows=1, grid_cols=2, grid_type="row_by_row",
        order="right_down", overlap=overlap,
    )
    assert quality["overlap_band_px"] == int(np.ceil(overlap * 64))
    assert quality["clamped_tiles"] == []


# ── seed mapping table ────────────────────────────────────────


def test_grid_type_order_mapping_table():
    """Each PerCell4 grid_type maps to the expected grid_positions order."""
    assert _GRID_TYPE_TO_VENDOR_ORDER["row_by_row"] == "row-by-row"
    assert _GRID_TYPE_TO_VENDOR_ORDER["column_by_column"] == "column-by-column"
    assert _GRID_TYPE_TO_VENDOR_ORDER["snake_by_row"] == "snake-by-rows"
    assert _GRID_TYPE_TO_VENDOR_ORDER["snake_by_column"] == "snake-by-columns"


def test_seed_layout_row_by_row_vs_column_by_column():
    """row_by_row and column_by_column seed different tile->position layouts.

    A 2x2 grid of distinctly-textured tiles registered with each grid_type
    must place tile index 1 in different grid cells (right vs. below tile 0),
    confirming the mapping actually drives the seed.
    """
    rng = np.random.default_rng(99)
    h = w = 48
    overlap = 0.2
    step = int(round(w * (1 - overlap)))
    # Build a 2x2 field and cut four overlapping tiles.
    big = (rng.random((h + step + 10, w + step + 10)) * 1000).astype(np.float32)

    def cut(r, c):
        return big[r * step : r * step + h, c * step : c * step + w].copy()

    # row_by_row acquisition order: (0,0)(0,1)(1,0)(1,1) -> idx 1 = top-right
    rbr = {0: cut(0, 0), 1: cut(0, 1), 2: cut(1, 0), 3: cut(1, 1)}
    off_rbr, _c1, _q1 = estimate_tile_offsets(
        rbr, grid_rows=2, grid_cols=2, grid_type="row_by_row",
        order="right_down", overlap=overlap,
    )
    # tile 1 to the right of tile 0: bigger x, similar y
    assert int(off_rbr[1][1]) - int(off_rbr[0][1]) > step // 2
    assert abs(int(off_rbr[1][0]) - int(off_rbr[0][0])) <= step // 2

    # column_by_column acquisition order: (0,0)(1,0)(0,1)(1,1) -> idx 1 = below
    cbc = {0: cut(0, 0), 1: cut(1, 0), 2: cut(0, 1), 3: cut(1, 1)}
    off_cbc, _c2, _q2 = estimate_tile_offsets(
        cbc, grid_rows=2, grid_cols=2, grid_type="column_by_column",
        order="right_down", overlap=overlap,
    )
    # tile 1 below tile 0: bigger y, similar x
    assert int(off_cbc[1][0]) - int(off_cbc[0][0]) > step // 2
    assert abs(int(off_cbc[1][1]) - int(off_cbc[0][1])) <= step // 2


# ── non-uniform tile shapes ───────────────────────────────────


def test_non_uniform_tile_shapes_raises():
    tiles = {
        0: np.zeros((10, 10), dtype=np.float32),
        1: np.zeros((10, 12), dtype=np.float32),
    }
    with pytest.raises(ValueError, match="uniform"):
        estimate_tile_offsets(
            tiles, grid_rows=1, grid_cols=2, grid_type="row_by_row",
            order="right_down", overlap=0.2,
        )


# ── assemble_tiles_with_offsets: overlap winner ───────────────


def test_assemble_overlap_higher_index_wins():
    """AE1: two overlapping tiles -> higher tile index wins every overlap px."""
    t0 = np.full((10, 10), 7, dtype=np.uint16)
    t1 = np.full((10, 10), 9, dtype=np.uint16)
    # tile 1 overlaps the right half of tile 0
    offsets = np.array([[0, 0], [0, 5]], dtype=np.int32)
    canvas = canvas_from_offsets(offsets, (10, 10))
    out = assemble_tiles_with_offsets({0: t0, 1: t1}, offsets, canvas)
    assert out.shape == (10, 15)
    # overlap columns 5..9 must hold tile 1's value (higher index wins)
    assert np.all(out[:, 5:10] == 9)
    # tile 0's exclusive region 0..4 holds 7
    assert np.all(out[:, 0:5] == 7)
    # tile 1's exclusive region 10..14 holds 9
    assert np.all(out[:, 10:15] == 9)


def test_assemble_fill_value_uncovered():
    """Uncovered canvas pixels carry the requested fill_value."""
    t0 = np.full((4, 4), 3, dtype=np.uint16)
    # place a single tile in a larger canvas
    offsets = np.array([[0, 0]], dtype=np.int32)
    out = assemble_tiles_with_offsets(
        {0: t0}, offsets, (8, 8), fill_value=255
    )
    assert out.shape == (8, 8)
    assert np.all(out[0:4, 0:4] == 3)
    assert np.all(out[4:8, :] == 255)
    assert np.all(out[:, 4:8] == 255)


def test_assemble_dtype_preserved():
    t0 = np.full((4, 4), 3, dtype=np.uint16)
    out = assemble_tiles_with_offsets(
        {0: t0}, np.array([[0, 0]], dtype=np.int32), (4, 4)
    )
    assert out.dtype == np.uint16


# ── disconnected demotion ─────────────────────────────────────


def test_disconnected_tile_demoted_below_registered():
    """A disconnected tile overlapping a registered tile loses the overlap.

    Even though the disconnected tile has a HIGHER index (would normally win),
    being demoted to lowest priority means the registered neighbor wins.
    """
    registered = np.full((10, 10), 4, dtype=np.uint16)  # index 0, registered
    disconn = np.full((10, 10), 8, dtype=np.uint16)     # index 1, disconnected
    offsets = np.array([[0, 0], [0, 5]], dtype=np.int32)
    canvas = canvas_from_offsets(offsets, (10, 10))
    out = assemble_tiles_with_offsets(
        {0: registered, 1: disconn}, offsets, canvas, disconnected=(1,)
    )
    # overlap cols 5..9: registered (index 0) wins despite lower index,
    # because the disconnected tile was placed first (lowest priority).
    assert np.all(out[:, 5:10] == 4)
    # disconnected tile's exclusive region 10..14 still shows its value
    assert np.all(out[:, 10:15] == 8)


# ── bbox-expand by a disconnected tile ────────────────────────


def test_disconnected_tile_expands_bbox():
    """A disconnected tile that extends the bbox is included in the canvas."""
    a = np.full((10, 10), 1, dtype=np.uint16)
    b = np.full((10, 10), 2, dtype=np.uint16)
    # tile 1 placed far away (disconnected, left near its grid seed)
    offsets = np.array([[0, 0], [30, 40]], dtype=np.int32)
    canvas = canvas_from_offsets(offsets, (10, 10))
    assert canvas == (40, 50)
    out = assemble_tiles_with_offsets(
        {0: a, 1: b}, offsets, canvas, disconnected=(1,)
    )
    assert out.shape == canvas
    assert out[35, 45] == 2  # the far tile is present
