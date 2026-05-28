#!/usr/bin/env python3
"""Generate the per_particle regression fixture TIFFs.

This script is committed alongside the produced TIFFs to document how the
inputs were constructed. It does NOT run automatically — the committed
TIFFs are the source of truth. Re-running this script should reproduce
the committed inputs byte-for-byte (fixed seeds, fixed dtypes).

Two fixture groups:
  - group_a: P-body-only branch (Cap.tif, P-body_mask.tif, pnorm.tif).
  - group_b: full inputs (Cap.tif, P-body_mask.tif, pnorm.tif, SG_mask.tif,
             sgnorm.tif, cp_mask.tif). Pbody and SG masks have at least
             one spatial overlap so the SG-exclusion side effect is
             exercised. cp_mask covers all particle regions so single_cell
             mode produces non-empty rows.

Usage:
    python tests/fixtures/per_particle/_generate_fixtures.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile
from skimage.draw import disk


HERE = Path(__file__).resolve().parent
GROUP_A = HERE / "group_a"
GROUP_B = HERE / "group_b"


def _make_cap(rng: np.random.Generator, shape: tuple[int, int],
              bright_centers: list[tuple[int, int]], radius: int,
              base_floor: float = 1000.0, blob_intensity: float = 6000.0
              ) -> np.ndarray:
    """Cap image: realistic noise floor + several bright blobs.

    Floor ~1000 puts the background well above the bg-subtraction threshold
    that the CLI computes from the histogram, so donut regions retain signal.
    """
    h, w = shape
    img = rng.normal(loc=base_floor, scale=40.0, size=(h, w))
    img = np.clip(img, 0, None)
    for (r, c) in bright_centers:
        rr, cc = disk((r, c), radius, shape=shape)
        img[rr, cc] += blob_intensity + rng.normal(scale=80.0, size=rr.shape)
    img = np.clip(img, 0, None)
    return img.astype(np.uint16)


def _make_mask(shape: tuple[int, int],
               centers: list[tuple[int, int]], radius: int) -> np.ndarray:
    h, w = shape
    mask = np.zeros((h, w), dtype=np.uint8)
    for (r, c) in centers:
        rr, cc = disk((r, c), radius, shape=shape)
        mask[rr, cc] = 255
    return mask


def _make_pnorm(rng: np.random.Generator, shape: tuple[int, int],
                bright_centers: list[tuple[int, int]], radius: int
                ) -> np.ndarray:
    """Normalization channel: similar structure + scale to Cap."""
    h, w = shape
    img = rng.normal(loc=1200.0, scale=40.0, size=(h, w))
    img = np.clip(img, 0, None)
    for (r, c) in bright_centers:
        rr, cc = disk((r, c), radius, shape=shape)
        img[rr, cc] += 4500.0 + rng.normal(scale=80.0, size=rr.shape)
    img = np.clip(img, 0, None)
    return img.astype(np.uint16)


def _make_cp_mask(shape: tuple[int, int]) -> np.ndarray:
    """cp_mask: uint16 labels image with two cells (top/bottom split).

    Cell 1: rows 0-47. Cell 2: rows 48+. This covers all particle regions
    in group_b so single_cell mode produces non-empty rows.
    """
    h, w = shape
    cp = np.zeros((h, w), dtype=np.uint16)
    cp[0:48, :] = 1
    cp[48:, :] = 2
    return cp


def _write(path: Path, arr: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), arr, photometric='minisblack')


def generate_group_a() -> None:
    """P-body-only image set."""
    rng = np.random.default_rng(seed=0)
    shape = (96, 96)

    # P-body blobs (the mask) — bright in Cap
    pbody_centers = [(20, 20), (20, 70), (70, 50)]
    pbody_radius = 5

    cap = _make_cap(rng, shape, pbody_centers, pbody_radius)
    pnorm = _make_pnorm(rng, shape, pbody_centers, pbody_radius)
    pbody_mask = _make_mask(shape, pbody_centers, pbody_radius)

    _write(GROUP_A / "sample1_Cap.tif", cap)
    _write(GROUP_A / "sample1_P-body_mask.tif", pbody_mask)
    _write(GROUP_A / "sample1_pnorm.tif", pnorm)


def generate_group_b() -> None:
    """Full image set: P-body + SG + cp_mask with deliberate overlap."""
    rng = np.random.default_rng(seed=1)
    shape = (96, 96)

    # P-body blobs (small)
    pbody_centers = [(20, 20), (20, 70), (70, 50)]
    pbody_radius = 5

    # SG blobs (larger). One SG (centered at (20, 20)) overlaps the first
    # P-body, so the SG-exclusion side effect is exercised.
    sg_centers = [(20, 20), (75, 25)]
    sg_radius = 10

    # Combined "bright" centers for the Cap image (union)
    all_bright = pbody_centers + sg_centers

    cap = _make_cap(rng, shape, all_bright, max(pbody_radius, sg_radius))
    pnorm = _make_pnorm(rng, shape, pbody_centers, pbody_radius)
    sgnorm = _make_pnorm(rng, shape, sg_centers, sg_radius)
    pbody_mask = _make_mask(shape, pbody_centers, pbody_radius)
    sg_mask = _make_mask(shape, sg_centers, sg_radius)
    cp_mask = _make_cp_mask(shape)

    _write(GROUP_B / "sample2_Cap.tif", cap)
    _write(GROUP_B / "sample2_P-body_mask.tif", pbody_mask)
    _write(GROUP_B / "sample2_pnorm.tif", pnorm)
    _write(GROUP_B / "sample2_SG_mask.tif", sg_mask)
    _write(GROUP_B / "sample2_sgnorm.tif", sgnorm)
    _write(GROUP_B / "sample2_cp_mask.tif", cp_mask)


def main() -> None:
    generate_group_a()
    generate_group_b()
    print(f"Wrote fixtures to {HERE}")


if __name__ == "__main__":
    main()
