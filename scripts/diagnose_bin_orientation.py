"""Diagnose .bin tile stitch + rotate + flip alignment against existing /intensity.

Given an .h5 file (with TIFF /intensity already imported) and a directory of
.bin files for the same FOV, this script:

  1. Reports whether /decay/<ch> already in the file is in post-stitch state
     vs post-rotate+flip state (compared against the in-memory expected
     post-stitch grid for the user's chosen TileConfig).

  2. Exhaustively tries every (start corner × grid_type × rotate_k ×
     flip_axis) combination, stitches the .bin intensity sums, applies the
     transforms, and compares against /intensity[<ch>] via normalized
     cross-correlation. Prints the top 5 matching configurations.

Run:
    python scripts/diagnose_bin_orientation.py \\
        --h5 /path/to/dataset.h5 \\
        --bin-dir /path/to/bin/files \\
        --channel ch00 \\
        --grid-rows 4 --grid-cols 4 \\
        --tile-h 512 --tile-w 512 --t-dim 132 \\
        --dtype uint16

Pure-stdlib + numpy + h5py. No Qt.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import h5py
import numpy as np

# Add src/ to path for importing percell4 helpers
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from percell4.adapters.readers import read_flim_bin
from percell4.domain.io.assembler import _tile_positions

CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")
GRID_TYPES = ("row_by_row", "column_by_column", "snake_by_row", "snake_by_column")
ROT_KS = (0, 1, 2, 3)
FLIP_AXES = (None, 0, 1)


def parse_tile_index(stem: str) -> int | None:
    """Match LASX-ish ``_s\\d+`` tile token in the bin filename."""
    m = re.search(r"_s(\d+)", stem)
    return int(m.group(1)) if m else None


def build_intensity_tiles(bin_dir: Path, channel_token: str, tile_kwargs: dict) -> dict[int, np.ndarray]:
    """Read .bin files for the given channel token and return tile_idx -> 2D intensity (sum over T)."""
    bins = sorted(p for p in bin_dir.rglob("*.bin") if p.is_file())
    matched = [p for p in bins if f"_ch{channel_token}" in p.stem or f"_C{channel_token}" in p.stem]
    if not matched:
        # Fall back to all .bin files (single-channel case)
        matched = bins
    tiles: dict[int, np.ndarray] = {}
    for p in matched:
        idx = parse_tile_index(p.stem)
        if idx is None:
            continue
        result = read_flim_bin(p, **tile_kwargs)
        tiles[idx] = result["intensity"].astype(np.float32)
    if tiles:
        min_idx = min(tiles)
        if min_idx > 0:
            tiles = {k - min_idx: v for k, v in tiles.items()}
    return tiles


def stitch(
    tiles: dict[int, np.ndarray],
    grid_rows: int,
    grid_cols: int,
    grid_type: str,
    order: str,
) -> np.ndarray:
    first = next(iter(tiles.values()))
    th, tw = first.shape[:2]
    out = np.zeros((grid_rows * th, grid_cols * tw), dtype=np.float32)
    positions = _tile_positions(grid_rows, grid_cols, grid_type, order)
    for idx, (r, c) in positions.items():
        if idx not in tiles:
            continue
        out[r * th:(r + 1) * th, c * tw:(c + 1) * tw] = tiles[idx]
    return out


def normalized_xcorr(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation between two same-shape 2D arrays."""
    if a.shape != b.shape:
        return float("nan")
    af = a.astype(np.float64).ravel()
    bf = b.astype(np.float64).ravel()
    af -= af.mean()
    bf -= bf.mean()
    denom = np.linalg.norm(af) * np.linalg.norm(bf)
    if denom == 0:
        return 0.0
    return float(np.dot(af, bf) / denom)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--h5", required=True, type=Path)
    p.add_argument("--bin-dir", required=True, type=Path)
    p.add_argument("--channel", required=True, help="e.g. ch00 or CA-SiR")
    p.add_argument("--channel-token", default=None,
                   help="Token used in .bin filenames (defaults to digits in --channel)")
    p.add_argument("--grid-rows", type=int, required=True)
    p.add_argument("--grid-cols", type=int, required=True)
    p.add_argument("--tile-h", type=int, default=512)
    p.add_argument("--tile-w", type=int, default=512)
    p.add_argument("--t-dim", type=int, default=132)
    p.add_argument("--dtype", default="uint16")
    p.add_argument("--dim-order", default="YXT")
    p.add_argument("--header-bytes", type=int, default=0)
    args = p.parse_args()

    token = args.channel_token
    if token is None:
        m = re.search(r"(\d+)$", args.channel)
        token = m.group(1) if m else ""

    tile_kwargs = {
        "x_dim": args.tile_w, "y_dim": args.tile_h, "t_dim": args.t_dim,
        "dtype": args.dtype, "dim_order": args.dim_order,
        "header_bytes": args.header_bytes,
    }

    print(f"Reading .bin tiles from {args.bin_dir} (channel token '{token}')...")
    tiles = build_intensity_tiles(args.bin_dir, token, tile_kwargs)
    if not tiles:
        print("ERROR: no .bin tiles matched")
        return 1
    print(f"  Found {len(tiles)} tile(s)")

    print(f"\nReading /intensity from {args.h5}...")
    with h5py.File(args.h5, "r") as f:
        meta = dict(f["metadata"].attrs) if "metadata" in f else {}
        names = list(meta.get("channel_names", []))
        intensity = f["intensity"][...]
        if intensity.ndim == 3:
            if args.channel in names:
                ch_idx = names.index(args.channel)
            else:
                ch_idx = 0
                print(f"  WARN: channel {args.channel} not in {names}, using index 0")
            target = intensity[ch_idx].astype(np.float32)
        else:
            target = intensity.astype(np.float32)
        decay_present = f"decay/{args.channel}" in f
        if decay_present:
            decay_intensity = f[f"decay/{args.channel}"][...].astype(np.float64).sum(axis=-1).astype(np.float32)
        else:
            decay_intensity = None

    print(f"  /intensity[{args.channel}] shape: {target.shape}")
    if decay_intensity is not None:
        print(f"  /decay/{args.channel} sum-over-T shape: {decay_intensity.shape}")

    print("\nStep 1 — Is the existing /decay rotated, or still in post-stitch state?")
    if decay_intensity is None:
        print("  /decay/<ch> not present — skipping")
    else:
        # Compute every transform of the post-stitch and find which matches existing /decay
        best_score = -2.0
        best_label = None
        for corner in CORNERS:
            for gtype in GRID_TYPES:
                base = stitch(tiles, args.grid_rows, args.grid_cols, gtype, corner)
                for k in ROT_KS:
                    rot = base if k == 0 else np.rot90(base, k=k, axes=(0, 1))
                    for ax in FLIP_AXES:
                        out = rot if ax is None else np.flip(rot, axis=ax)
                        if out.shape != decay_intensity.shape:
                            continue
                        s = normalized_xcorr(out, decay_intensity)
                        if s > best_score:
                            best_score = s
                            best_label = (corner, gtype, k, ax)
        print(f"  /decay matches: corner={best_label[0]} grid_type={best_label[1]} "
              f"rotate_k={best_label[2]} flip_axis={best_label[3]} (xcorr={best_score:.4f})")
        if best_label[2] == 0 and best_label[3] is None:
            print("  → /decay is in PURE POST-STITCH state — Phase 2 rot/flip did NOT persist.")
        else:
            print(f"  → /decay reflects rot+flip applied at the global stitched-array level.")

    print("\nStep 2 — Top 10 (corner × grid_type × rotate_k × flip_axis) matches against TIFF /intensity:")
    results = []
    for corner in CORNERS:
        for gtype in GRID_TYPES:
            try:
                base = stitch(tiles, args.grid_rows, args.grid_cols, gtype, corner)
            except Exception:
                continue
            for k in ROT_KS:
                rot = base if k == 0 else np.rot90(base, k=k, axes=(0, 1))
                for ax in FLIP_AXES:
                    out = rot if ax is None else np.flip(rot, axis=ax)
                    if out.shape != target.shape:
                        continue
                    s = normalized_xcorr(out, target)
                    results.append((s, corner, gtype, k, ax))
    results.sort(reverse=True, key=lambda r: r[0])
    print(f"  {'xcorr':>7}  corner        grid_type           rot  flip")
    for s, corner, gtype, k, ax in results[:10]:
        print(f"  {s:7.4f}  {corner:<13} {gtype:<19} k={k}  axis={ax}")

    if results:
        s, corner, gtype, k, ax = results[0]
        flip_label = "None" if ax is None else ("Vertical" if ax == 0 else "Horizontal")
        rot_label = ["None", "90° CCW", "180°", "90° CW"][k]
        print("\nRecommended dialog settings:")
        print(f"  Stitch start corner:   {corner}")
        print(f"  Stitch grid type:      {gtype}")
        print(f"  Rotate stitched array: {rot_label}")
        print(f"  Flip:                  {flip_label}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
