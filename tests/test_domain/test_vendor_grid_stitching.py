"""Tests for the vendored numpy-only ``grid_stitching`` computational core.

Ports the spirit of the source package's ``test_stitching.py`` (``test_pairwise``
and the registration half of ``test_full_pipeline``) and adds an import-isolation
guard asserting the vendored package pulls in no file-I/O / Qt dependencies.
"""

import subprocess
import sys

import numpy as np
import pytest

from percell4.domain._vendor.grid_stitching import (
    compute_pairwise_shifts,
    grid_positions,
    optimize_positions,
    register_pair,
)


def _make_ground_truth(H=600, W=800, seed=0):
    """A textured master image to cut tiles from (low-freq + high-freq signal)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    base = (np.sin(xx / 37.0) * np.cos(yy / 29.0) * 80 + 128)
    texture = rng.normal(0, 25, size=(H, W))
    return np.clip(base + texture, 0, 255).astype(np.uint16)


def _cut_grid(master, gx, gy, tile_h, tile_w, overlap, jitter, seed=1):
    """Cut a grid of overlapping tiles with small positional jitter.

    Returns (tiles, truth) where truth is the ground-truth (x0, y0) per tile.
    """
    rng = np.random.default_rng(seed)
    step_x = int(tile_w * (1 - overlap))
    step_y = int(tile_h * (1 - overlap))
    tiles, truth = [], []
    for r in range(gy):
        for c in range(gx):
            jx = int(rng.integers(-jitter, jitter + 1))
            jy = int(rng.integers(-jitter, jitter + 1))
            x0 = c * step_x + jx
            y0 = r * step_y + jy
            x0 = max(0, min(x0, master.shape[1] - tile_w))
            y0 = max(0, min(y0, master.shape[0] - tile_h))
            tiles.append(master[y0:y0 + tile_h, x0:x0 + tile_w].copy())
            truth.append((x0, y0))
    return tiles, np.array(truth, dtype=float)


def test_register_pair_recovers_known_integer_shift():
    """Two overlapping strips cut at a known shift -> phase corr recovers it.

    Ported from ``test_stitching.py::test_pairwise``: aligned overlapping
    strips should register to ~zero shift.
    """
    master = _make_ground_truth()
    tile_w, tile_h = 256, 256
    dx, dy = 180, 0
    A = master[80:80 + tile_h, 100:100 + tile_w]
    B = master[80 + dy:80 + dy + tile_h, 100 + dx:100 + dx + tile_w]
    ov = tile_w - dx
    subA = A[:, dx:]          # right strip of A
    subB = B[:, :ov]          # left strip of B

    res = register_pair(subA, subB)

    # Aligned strips -> near-zero residual shift on both axes.
    assert abs(res.shift[0]) <= 2
    assert abs(res.shift[1]) <= 2
    assert res.correlation > 0.5


def test_register_pair_recovers_asymmetric_shift():
    """A deliberately asymmetric (dy != dx) shift is recovered, axis-correct.

    Guards against an (x,y) <-> (y,x) transposition: register_pair returns the
    shift in image-axis (row, col) == (y, x) order.
    """
    master = _make_ground_truth()
    tile_h = tile_w = 256
    y0, x0 = 120, 90
    dy, dx = 7, 19  # asymmetric integer shift

    img1 = master[y0:y0 + tile_h, x0:x0 + tile_w]
    img2 = master[y0 + dy:y0 + dy + tile_h, x0 + dx:x0 + dx + tile_w]

    res = register_pair(img1, img2)

    # res.shift maps img2 onto img1: shifting img2 by (+dy, +dx) re-aligns it,
    # so register_pair reports (-dy, -dx) (or its magnitude) in (row, col) order.
    assert abs(abs(res.shift[0]) - dy) <= 2, f"row(y) axis wrong: {res.shift}"
    assert abs(abs(res.shift[1]) - dx) <= 2, f"col(x) axis wrong: {res.shift}"
    # The asymmetry must be preserved (not transposed).
    assert abs(res.shift[0]) != abs(res.shift[1])


def test_grid_optimize_recovers_positions_3x3():
    """grid_positions + compute_pairwise_shifts + optimize_positions on a 3x3
    jittered grid recovers per-tile positions to within a few px.

    Ports the registration half of ``test_stitching.py::test_full_pipeline``,
    using the core functions directly (the Stitcher class is not vendored).
    """
    master = _make_ground_truth()
    gx, gy = 3, 3
    tile_h = tile_w = 256
    overlap = 0.30
    jitter = 12
    tiles_arrays, truth = _cut_grid(master, gx, gy, tile_h, tile_w,
                                    overlap, jitter)

    # Build image-less Tiles from the grid layout, then attach each image.
    tiles = grid_positions(gx, gy, tile_w, tile_h,
                           overlap=overlap, order="row-by-row")
    for t, img in zip(tiles, tiles_arrays):
        t.image = img

    ndim = tiles[0].ndim
    initial = [t.position.copy() for t in tiles]
    shifts = compute_pairwise_shifts(tiles, ndim, n_peaks=5,
                                     regression_threshold=0.3)
    positions = optimize_positions(len(tiles), shifts, initial, ndim)

    # Align recovered positions to ground truth (remove the global offset).
    truth_rel = truth - truth[0]
    pos_rel = positions - positions[0]
    max_err = np.abs(pos_rel - truth_rel).max()

    assert len(shifts) > 0, "no reliable pairwise shifts were found"
    assert max_err <= 3.0, f"max position error too high: {max_err}"


def test_grid_optimize_recovers_positions_2x2():
    """A smaller 2x2 jittered grid also recovers positions to within a few px."""
    master = _make_ground_truth()
    gx, gy = 2, 2
    tile_h = tile_w = 256
    overlap = 0.30
    jitter = 8
    tiles_arrays, truth = _cut_grid(master, gx, gy, tile_h, tile_w,
                                    overlap, jitter, seed=3)

    tiles = grid_positions(gx, gy, tile_w, tile_h,
                           overlap=overlap, order="row-by-row")
    for t, img in zip(tiles, tiles_arrays):
        t.image = img

    ndim = tiles[0].ndim
    initial = [t.position.copy() for t in tiles]
    shifts = compute_pairwise_shifts(tiles, ndim, n_peaks=5,
                                     regression_threshold=0.3)
    positions = optimize_positions(len(tiles), shifts, initial, ndim)

    truth_rel = truth - truth[0]
    pos_rel = positions - positions[0]
    max_err = np.abs(pos_rel - truth_rel).max()

    assert len(shifts) > 0
    assert max_err <= 3.0, f"max position error too high: {max_err}"


def test_vendored_import_pulls_in_no_file_io_or_qt():
    """Importing the vendored core must not add tifffile/skimage/scipy/qtpy/h5py.

    Run in a fresh subprocess: other tests in this process may already have
    imported those modules, so an in-process sys.modules check is unreliable.

    Subtlety: the ``percell4`` package root unconditionally imports
    ``hdf5plugin`` (to register the Blosc HDF5 filter), which transitively
    imports ``h5py`` — for *every* percell4 submodule, vendored or not. So the
    honest assertion is the *delta*: importing the vendored core must add none
    of the forbidden modules beyond the percell4-package-root baseline. The
    vendored core itself imports numpy + stdlib only.
    """
    forbidden = ("tifffile", "skimage", "scipy", "qtpy", "h5py")
    code = (
        "import sys\n"
        "import percell4  # package root (registers hdf5plugin -> h5py)\n"
        f"forbidden = {forbidden!r}\n"
        "def present():\n"
        "    return {m for m in forbidden\n"
        "            if any(k == m or k.startswith(m + '.') for k in sys.modules)}\n"
        "baseline = present()\n"
        "import percell4.domain._vendor.grid_stitching\n"
        "added = present() - baseline\n"
        "assert not added, 'vendored core added forbidden imports: ' + repr(added)\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "OK" in result.stdout


def test_vendored_all_exports_only_computational_core():
    """__all__ must re-export only the computational core, no file-I/O/Stitcher."""
    import percell4.domain._vendor.grid_stitching as g

    assert set(g.__all__) == {
        "register_pair", "phase_correlation_matrix", "PeakResult",
        "optimize_positions", "PairwiseShift",
        "compute_pairwise_shifts",
        "Tile", "grid_positions",
    }
    for forbidden_name in ("Stitcher", "fuse", "load_tile_images",
                           "parse_tile_configuration",
                           "write_tile_configuration"):
        assert not hasattr(g, forbidden_name), (
            f"{forbidden_name} must not be exported from the vendored core"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
