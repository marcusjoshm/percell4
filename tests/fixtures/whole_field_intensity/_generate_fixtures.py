"""Generate synthetic input TIFFs for the whole_field_intensity regression.

One rich image set (prefix ``fieldA``) carrying every channel the v2-v5
presets and single-cell mode can consume, with nested masks so the
two-region, three-region (v4/v5), and percent code paths all exercise
non-empty regions. Committed TIFFs are the source of truth; this script
documents how they were produced (fixed values, no randomness).

After (re)generating, the committed expected CSVs under ``expected/`` are
produced by the UNMODIFIED original CLI (``whole_field_analysis.py``):

    for v in v2 v3 v4 v5: \
        whole_field_analysis.py --preset decapping-sensor-$v \
            --data-dir . --output expected/$v.csv
    whole_field_analysis.py --preset decapping-sensor-v2 --single-cell \
        --data-dir . --output expected/v2_sc.csv
    whole_field_analysis.py --preset decapping-sensor-v4 --single-cell \
        --data-dir . --output expected/v4_sc.csv

Run from this directory: ``python _generate_fixtures.py``.
"""
from __future__ import annotations

import os

import numpy as np
import tifffile

HERE = os.path.dirname(os.path.abspath(__file__))
H = W = 48
PFX = "fieldA"


def _block(r0, r1, c0, c1) -> np.ndarray:
    m = np.zeros((H, W), dtype=np.uint8)
    m[r0:r1, c0:c1] = 1
    return m


def _write(name: str, arr: np.ndarray) -> None:
    tifffile.imwrite(os.path.join(HERE, f"{PFX}_{name}.tif"), arr)


def main() -> None:
    # Dilute cytoplasm region (also the background-estimation region).
    dilute = _block(4, 44, 4, 44)

    # P-body particles inside the dilute region.
    pbody = np.zeros((H, W), dtype=np.uint8)
    pbody[8:12, 8:12] = 1     # 16 px (top half -> cell 1)
    pbody[30:34, 30:34] = 1   # 16 px (bottom half -> cell 2)
    pbody[6:8, 20:22] = 1     # 4 px

    # mNG (Dcp2) mask + inner; FLIM (interaction) mask + inner; SiR mask.
    dcp2 = _block(10, 30, 10, 30)
    dcp2_2 = _block(14, 20, 14, 20)
    interaction = _block(8, 32, 8, 32)
    interaction_2 = _block(15, 19, 15, 19)
    sir = _block(6, 40, 6, 40)

    # Cell segmentation: two cells split top/bottom within the dilute area.
    cp = np.zeros((H, W), dtype=np.int32)
    cp[4:24, 4:44] = 1
    cp[24:44, 4:44] = 2

    # Float intensity channels: background + elevated signal in masks.
    mng = np.full((H, W), 50.0, dtype=np.float32)
    mng[dcp2 > 0] = 400.0
    mng[dcp2_2 > 0] = 900.0
    mng[pbody > 0] += 100.0

    halo = np.full((H, W), 30.0, dtype=np.float32)
    halo[interaction > 0] = 300.0
    halo[interaction_2 > 0] = 700.0
    halo[pbody > 0] += 80.0

    _write("P-body_mask", pbody)
    _write("dilute_mask", dilute)
    _write("Dcp2_mask", dcp2)
    _write("Dcp2_mask_2", dcp2_2)
    _write("interaction_mask", interaction)
    _write("interaction_mask_2", interaction_2)
    _write("SiR_mask", sir)
    _write("cp_mask", cp)
    _write("Halo", halo)
    _write("mNG", mng)
    print(f"Wrote synthetic TIFFs for prefix {PFX!r} to {HERE}")


if __name__ == "__main__":
    main()
