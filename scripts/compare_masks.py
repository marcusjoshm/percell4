"""Overlay one or more /masks from a dataset in napari for visual comparison.

Opens the chosen channel in grayscale and lays each mask over it in a distinct
color with additive blending, so where two masks overlap they blend toward white
and where only one fires you see its pure color. Prints per-mask pixel /
component counts to stdout.

Usage:
    python scripts/compare_masks.py DS.h5 mNG --masks SG_auto_log SG_mask --seg cp_mask
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import napari
import numpy as np
from skimage import measure

from percell4.store import DatasetStore

# new mask first (green), old/reference second (magenta) -> overlap reads white.
_COLORS = ["green", "magenta", "yellow", "cyan", "red", "blue"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Overlay /masks in napari for comparison.")
    ap.add_argument("dataset")
    ap.add_argument("channel")
    ap.add_argument("--masks", nargs="+", required=True, help="Mask layer names to overlay.")
    ap.add_argument("--seg", default=None, help="Optional /labels set to show faintly underneath.")
    ap.add_argument(
        "--solo",
        action="store_true",
        help="Show only the first mask initially (toggle the rest by eye, one at a time).",
    )
    args = ap.parse_args(argv)

    store = DatasetStore(args.dataset)
    names = list(store.metadata.get("channel_names", []))
    if args.channel not in names:
        print(f"error: channel {args.channel!r} not in {names}", file=sys.stderr)
        return 1
    img = store.read_channel("intensity", names.index(args.channel))

    viewer = napari.Viewer(title=f"compare masks — {Path(args.dataset).stem}")
    viewer.add_image(img, name=args.channel, colormap="gray")
    if args.seg:
        try:
            viewer.add_labels(store.read_labels(args.seg), name=args.seg, opacity=0.12)
        except Exception:
            pass

    for i, name in enumerate(args.masks):
        try:
            mask = np.asarray(store.read_mask(name)) > 0
        except Exception as e:
            print(f"skip {name}: {e}", file=sys.stderr)
            continue
        if mask.ndim == 3:  # time-stacked -> union the frames
            mask = mask.any(axis=0)
        ncomp = int(measure.label(mask).max())
        print(f"{name}: {int(mask.sum())} px, {ncomp} components -> {_COLORS[i % len(_COLORS)]}")
        viewer.add_image(
            mask.astype(np.float32),
            name=name,
            colormap=_COLORS[i % len(_COLORS)],
            blending="additive",
            contrast_limits=[0.0, 1.0],
            opacity=0.7,
            visible=(i == 0) if args.solo else True,
        )

    legend = "   ".join(f"{n}={_COLORS[i % len(_COLORS)]}" for i, n in enumerate(args.masks))
    print("legend:  " + legend)
    napari.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
