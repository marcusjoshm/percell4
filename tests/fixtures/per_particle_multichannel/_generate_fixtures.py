"""Generate synthetic input TIFFs for the per_particle_multichannel regression.

The committed TIFFs are the source of truth; this script documents how they
were produced (fixed shapes/values, no randomness) so they can be regenerated
deterministically. After (re)generating the inputs, the committed expected
CSVs under ``group_*_expected/`` are produced by running the UNMODIFIED
original CLI (``per_particle_multichannel.py``) against each group directory:

    python per_particle_multichannel.py --data-dir group_a \
        --output group_a_expected/combined.csv
    python per_particle_multichannel.py --data-dir group_b \
        --output group_b_expected/combined.csv          # per-particle (cp_mask present -> cell_id col)
    python per_particle_multichannel.py --data-dir group_b --single-cell \
        --output group_b_expected/sc.csv

Run from this directory: ``python _generate_fixtures.py``.
"""
from __future__ import annotations

import os

import numpy as np
import tifffile

HERE = os.path.dirname(os.path.abspath(__file__))
H = W = 64


def _square(arr: np.ndarray, r: int, c: int, size: int, value) -> None:
    arr[r:r + size, c:c + size] = value


def _make_group_a() -> None:
    """Per-particle set: 1 mask + 2 channels (mNG, CA-SiR). No cellpose."""
    d = os.path.join(HERE, "group_a")
    os.makedirs(d, exist_ok=True)

    mask = np.zeros((H, W), dtype=np.uint8)
    _square(mask, 8, 8, 6, 1)     # particle 1 (36 px)
    _square(mask, 40, 40, 5, 1)   # particle 2 (25 px)
    _square(mask, 8, 44, 3, 1)    # particle 3 (9 px)

    mng = np.full((H, W), 100.0, dtype=np.float32)
    _square(mng, 8, 8, 6, 900.0)
    _square(mng, 40, 40, 5, 600.0)
    _square(mng, 8, 44, 3, 400.0)
    # Donut-ring signal differs from background so dilute means are nontrivial.
    mng[6:18, 6:18] += 30.0

    casir = np.full((H, W), 50.0, dtype=np.float32)
    _square(casir, 8, 8, 6, 300.0)
    _square(casir, 40, 40, 5, 450.0)
    _square(casir, 8, 44, 3, 200.0)
    casir[38:52, 38:52] += 20.0

    tifffile.imwrite(os.path.join(d, "a1_mask.tif"), mask)
    tifffile.imwrite(os.path.join(d, "a1_mNG.tif"), mng)
    tifffile.imwrite(os.path.join(d, "a1_CA-SiR.tif"), casir)


def _make_group_b() -> None:
    """Single-cell set: 1 mask + 2 channels (mNG, mTQ2) + cellpose."""
    d = os.path.join(HERE, "group_b")
    os.makedirs(d, exist_ok=True)

    mask = np.zeros((H, W), dtype=np.uint8)
    _square(mask, 8, 8, 6, 1)     # particle -> cell 1
    _square(mask, 18, 10, 5, 1)   # particle -> cell 1
    _square(mask, 40, 42, 6, 1)   # particle -> cell 2

    cp = np.zeros((H, W), dtype=np.int32)
    cp[:32, :] = 1   # cell 1 (top half)
    cp[32:, :] = 2   # cell 2 (bottom half)
    # leave a thin background border so not every pixel is a cell
    cp[:, :2] = 0
    cp[:, -2:] = 0

    mng = np.full((H, W), 120.0, dtype=np.float32)
    _square(mng, 8, 8, 6, 800.0)
    _square(mng, 18, 10, 5, 700.0)
    _square(mng, 40, 42, 6, 500.0)

    mtq2 = np.full((H, W), 60.0, dtype=np.float32)
    _square(mtq2, 8, 8, 6, 350.0)
    _square(mtq2, 18, 10, 5, 300.0)
    _square(mtq2, 40, 42, 6, 250.0)

    tifffile.imwrite(os.path.join(d, "b1_mask.tif"), mask)
    tifffile.imwrite(os.path.join(d, "b1_mNG.tif"), mng)
    tifffile.imwrite(os.path.join(d, "b1_mTQ2.tif"), mtq2)
    tifffile.imwrite(os.path.join(d, "b1_cellpose.tif"), cp)


if __name__ == "__main__":
    _make_group_a()
    _make_group_b()
    print("Wrote synthetic TIFFs to group_a/ and group_b/")
