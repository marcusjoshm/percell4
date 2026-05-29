#!/usr/bin/env python3
"""
Per-particle multi-channel dilute-vs-condensed phase analysis.

For each image set in --data-dir, measures mean gray value and integrated
intensity inside the particle mask (condensed phase) and in a donut ring
around each particle (dilute phase) for every detected fluorescence
channel. No background subtraction; no normalization channel.

File naming convention (multiple sets per directory, shared prefix):
  {prefix}_mask.tif       particle mask (required)
  {prefix}_<channel>.tif  measurement channel (any suffix, >=1 required)
  {prefix}_cellpose.tif   cell segmentation mask (optional, --single-cell)

Channel names are auto-detected as the suffix after the shared prefix
(e.g. CA-SiR, mNG, mTQ2). Output is a wide CSV with one row per particle
(or per cell with --single-cell) and one block of columns per channel.

This CLI is the thin I/O wrapper around the pure analysis core at
``percell4.domain.analysis._impl.per_particle_multichannel.run_one_image_set``.
The framework's registered ``PerParticleMultichannel`` analysis calls the
same pure core; both paths share one source of truth for the math.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 9):
    sys.exit("Error: Python 3.9 or higher is required.")

import argparse
import glob
import os

import numpy as np
import pandas as pd
import tifffile

# Make sure the percell4 package is importable when invoking the script
# from the repo root without an editable install.
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from percell4.domain.analysis._impl.per_particle_multichannel import (  # noqa: E402
    run_one_image_set,
)

MASK_SUFFIX = '_mask'
CP_MASK_SUFFIX = '_cellpose'


def group_image_sets(data_dir):
    """Group .tif/.tiff files in data_dir by shared prefix.

    Returns {prefix: {'mask': path, 'cp_mask': path|None, 'channels': {name: path}}}.
    A file is a particle mask if its basename ends with `_mask`. A file
    is a cell mask if its basename ends with `_cellpose`. All other .tif
    files are treated as measurement channels; each is matched to the
    longest prefix that matches its filename.
    """
    files = (glob.glob(os.path.join(data_dir, '*.tif'))
             + glob.glob(os.path.join(data_dir, '*.tiff')))

    mask_files = {}
    cp_mask_files = {}
    channel_candidates = []

    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        if name.endswith(CP_MASK_SUFFIX):
            prefix = name[:-len(CP_MASK_SUFFIX)]
            if prefix:
                cp_mask_files[prefix] = f
        elif name.endswith(MASK_SUFFIX):
            prefix = name[:-len(MASK_SUFFIX)]
            if prefix:
                mask_files[prefix] = f
        else:
            channel_candidates.append((name, f))

    if not mask_files:
        return {}

    prefixes_by_length = sorted(mask_files.keys(), key=len, reverse=True)

    groups = {}
    for prefix, path in mask_files.items():
        groups[prefix] = {
            'mask': path,
            'cp_mask': cp_mask_files.get(prefix),
            'channels': {},
        }

    for name, path in channel_candidates:
        matched_prefix = None
        for prefix in prefixes_by_length:
            if name.startswith(prefix + '_'):
                matched_prefix = prefix
                break
        if matched_prefix is None:
            print(f"Warning: {os.path.basename(path)} does not match any "
                  f"mask prefix; skipping")
            continue
        channel_name = name[len(matched_prefix) + 1:]
        groups[matched_prefix]['channels'][channel_name] = path

    return groups


def save_results(rows, output_path, channel_names, single_cell=False):
    if not rows:
        print("No results to save.")
        return

    df = pd.DataFrame(rows)

    if single_cell:
        base_cols = ['group', 'cell_id', 'cell_area_px', 'n_particles',
                     'total_particle_area_px', 'total_donut_area_px']
    else:
        base_cols = ['group', 'particle_id', 'particle_area_px', 'donut_area_px']
        if 'cell_id' in df.columns:
            base_cols.insert(2, 'cell_id')

    ch_cols = []
    for ch in channel_names:
        if single_cell:
            ch_cols.extend([
                f'cell_{ch}_mean',
                f'cell_{ch}_median',
                f'cell_{ch}_mode',
                f'cell_{ch}_min',
                f'cell_{ch}_max',
                f'cell_{ch}_integ',
            ])
        ch_cols.extend([
            f'condensed_{ch}_mean',
            f'dilute_{ch}_mean',
            f'{ch}_condensed_over_dilute',
            f'condensed_{ch}_integ',
            f'dilute_{ch}_integ',
        ])

    # DataFrame(..list of dicts..) fills missing keys with NaN, so groups
    # that don't contain every channel still produce a complete column set.
    for col in base_cols + ch_cols:
        if col not in df.columns:
            df[col] = np.nan

    df = df[base_cols + ch_cols]
    df.to_csv(output_path, index=False)
    print(f"\nResults saved to {output_path}")
    print(f"Total rows: {len(df)}")
    mean_cols = [f'condensed_{ch}_mean' for ch in channel_names] + \
                [f'dilute_{ch}_mean' for ch in channel_names]
    print("\nMean of per-channel means by group:")
    print(df.groupby('group')[mean_cols].mean().to_string())


def main():
    parser = argparse.ArgumentParser(
        description='Per-particle multi-channel dilute-vs-condensed phase '
                    'analysis. No background subtraction; no normalization '
                    'channel. Measurement channels are auto-detected as any '
                    '.tif file in the directory that does not end in _mask '
                    'or _cellpose.'
    )
    parser.add_argument('--data-dir', required=True,
                        help='Directory containing .tif/.tiff images')
    parser.add_argument('--output', required=True, help='Output CSV path')
    parser.add_argument('--buffer', type=int, default=5,
                        help='Buffer zone dilation in pixels (default: 5)')
    parser.add_argument('--donut', type=int, default=5,
                        help='Donut ring width in pixels (default: 5)')
    parser.add_argument('--min-size', type=int, default=4,
                        help='Skip particles <= this many pixels (default: 4)')
    parser.add_argument('--single-cell', action='store_true',
                        help='Aggregate particles per cell using a '
                             '{prefix}_cellpose.tif segmentation file. One row '
                             'per cell per image set.')
    parser.add_argument('--export-donuts', action='store_true',
                        help='Export a binary donut mask TIFF per image set '
                             'for overlay visualization.')
    args = parser.parse_args()

    groups = group_image_sets(args.data_dir)
    if not groups:
        print(f"No image sets (no *_mask.tif files) found in {args.data_dir}")
        return

    all_channel_names = set()
    all_results = []

    for group_key, g in sorted(groups.items()):
        channels = g['channels']
        if not channels:
            print(f"Skipping {group_key}: no measurement channels found")
            continue

        if args.single_cell and g['cp_mask'] is None:
            print(f"Skipping {group_key}: --single-cell requires "
                  f"{group_key}_cellpose.tif")
            continue

        print(f"\nAnalyzing {group_key}")
        print(f"  Channels: {', '.join(sorted(channels.keys()))}")

        # Build the ordered channel-name -> array dict (sorted by name).
        channel_images = {name: tifffile.imread(path).astype(np.float64)
                          for name, path in sorted(channels.items())}
        all_channel_names.update(channel_images.keys())

        cp_mask_img = (tifffile.imread(g['cp_mask'])
                       if g['cp_mask'] is not None else None)
        mask_img = tifffile.imread(g['mask'])

        result = run_one_image_set(
            mask=mask_img, channels=channel_images, cp_mask=cp_mask_img,
            buffer=args.buffer, donut=args.donut, min_size=args.min_size,
            single_cell=args.single_cell, export_donuts=args.export_donuts,
            set_label=group_key, log=print,
        )

        if args.export_donuts and result["donut_mask"] is not None:
            out_path = os.path.join(
                args.data_dir, f'{group_key}_donut_mask.tif'
            )
            tifffile.imwrite(out_path, result["donut_mask"])
            print(f"  Donut mask exported to {out_path}")

        rows = (result["cell_rows"] if args.single_cell
                else result["particle_rows"])
        for r in rows:
            r['group'] = group_key
        all_results.extend(rows)

    if not all_results:
        print("No particles/cells found in any image set.")
        return

    ordered_channels = sorted(all_channel_names)
    save_results(all_results, args.output, ordered_channels,
                 single_cell=args.single_cell)


if __name__ == '__main__':
    main()
