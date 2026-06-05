"""Interactive napari helper to produce Tier-A ground-truth CSVs for the
puncta validation harness (``percell4-batch-validate-puncta``).

Opens a dataset's detection channel in napari with an empty Points layer.
Click every focus you can see (including dim ones — that exhaustiveness is the
recall ceiling), then close the window. The points are written as a
``y, x`` CSV the harness reads directly (no rename needed), named after the
dataset stem by default.

Usage:
    python scripts/label_foci.py DS1.h5 GFP
    python scripts/label_foci.py DS1.h5 GFP --out labels/DS1.csv --seg-name cellpose_qc
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import napari
import pandas as pd

from percell4.store import DatasetStore


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Label puncta foci in napari -> Tier-A y,x CSV.")
    ap.add_argument("dataset", help="Path to the dataset .h5 file.")
    ap.add_argument(
        "channel",
        nargs="?",
        default="all",
        help="Channel name to label on, or 'all' to show every channel (default: all).",
    )
    ap.add_argument("--seg-name", default="cellpose_qc", help="Label set to overlay (cells).")
    ap.add_argument("--out", default=None, help="Output CSV (default: labels/<stem>.csv).")
    ap.add_argument(
        "--load",
        default=None,
        help="Resume: pre-load an existing y,x CSV into the foci layer to keep adding.",
    )
    ap.add_argument("--point-size", type=float, default=8.0, help="Point marker size.")
    args = ap.parse_args(argv)

    store = DatasetStore(args.dataset)
    names = list(store.metadata.get("channel_names", []))
    if args.channel != "all" and args.channel not in names:
        print(f"error: channel {args.channel!r} not in {names}", file=sys.stderr)
        return 1
    show = names if args.channel == "all" else [args.channel]

    out = Path(args.out) if args.out else Path("labels") / f"{Path(args.dataset).stem}.csv"

    viewer = napari.Viewer(title=f"label foci — {Path(args.dataset).stem}")
    for name in show:
        ch = store.read_channel("intensity", names.index(name))
        # Show only the first channel by default; toggle others on in the layer list.
        viewer.add_image(
            ch, name=name, colormap="gray", visible=(name == show[0]), blending="additive"
        )
    try:
        viewer.add_labels(store.read_labels(args.seg_name), name="cells", opacity=0.25)
    except Exception:
        pass  # no labels yet — fine, label on the raw channel
    initial = None
    if args.load and Path(args.load).is_file():
        prev = pd.read_csv(args.load)
        initial = prev[["y", "x"]].to_numpy(dtype=float)
        print(f"resuming from {len(initial)} previously-saved foci")
    foci = (
        viewer.add_points(initial, name="foci", size=args.point_size, face_color="red")
        if initial is not None and len(initial)
        else viewer.add_points(name="foci", size=args.point_size, face_color="red")
    )
    foci.mode = "add"  # start in add mode: click to drop a point
    viewer.layers.selection = {foci}  # keep the points layer active for clicking

    print("napari is open. Click every focus, then CLOSE the window to save.")
    napari.run()

    out.parent.mkdir(parents=True, exist_ok=True)
    pts = foci.data  # (N, 2) in (row, col) = (y, x) pixel coordinates
    pd.DataFrame(pts, columns=["y", "x"]).to_csv(out, index=False)
    print(f"wrote {len(pts)} foci -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
