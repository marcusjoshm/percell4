#!/usr/bin/env python3
"""
Per-particle donut background subtraction analysis of fluorescence intensities
in masked subcellular regions (P-bodies and/or Stress Granules).

Modes (determined automatically per image set by available files):
  - P-body only:  requires P-body_mask, Cap, pnorm
  - SG only:      requires SG_mask, Cap, sgnorm
  - Both:         requires all five files. SG analysis runs normally;
                  P-body analysis first excludes Cap pixels inside SG_mask
                  (set to NaN) because P-bodies embedded in stress granules
                  cannot be reliably quantified.

This CLI is the thin I/O wrapper around the pure analysis core at
``percell4.domain.analysis._impl.per_particle_donut.run_one_image_set``.
The framework's registered ``PerParticleDonut`` analysis (U5) calls
the same pure core; both paths share one source of truth for the math.
"""
from __future__ import annotations

import sys
if sys.version_info < (3, 9):
    sys.exit("Error: Python 3.9 or higher is required (for argparse.BooleanOptionalAction).")

import argparse
import glob
import os
from collections import defaultdict

import numpy as np
import pandas as pd
import tifffile

# Make sure the percell4 package is importable when invoking the script
# from the repo root without an editable install.
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from percell4.domain.analysis._impl.per_particle_donut import (  # noqa: E402
    run_one_image_set,
)


CHANNELS = ['P-body_mask', 'SG_mask', 'Cap', 'pnorm', 'sgnorm', 'cp_mask']


def parse_filename(filepath):
    """Parse a .tif/.tiff filename by detecting a known channel keyword."""
    basename = os.path.basename(filepath)
    name_no_ext = os.path.splitext(basename)[0]
    # Try longer keywords first to avoid partial matches (e.g. SG_mask before Cap)
    for ch in sorted(CHANNELS, key=len, reverse=True):
        idx = name_no_ext.find(ch)
        if idx != -1:
            group_key = name_no_ext[:idx].strip('_')
            return {'group_key': group_key, 'channel': ch}
    return None


def group_image_sets(data_dir):
    """Group .tif/.tiff files by shared name (everything except the channel keyword)."""
    files = glob.glob(os.path.join(data_dir, '*.tif')) + glob.glob(os.path.join(data_dir, '*.tiff'))
    groups = defaultdict(dict)
    for f in files:
        parsed = parse_filename(f)
        if parsed is None:
            print(f"Warning: could not parse filename (no channel keyword found): {os.path.basename(f)}")
            continue
        groups[parsed['group_key']][parsed['channel']] = f
    return groups


def _walk_groups_from_dir(data_dir):
    """Discover image sets by directory/file conventions.

    Thin wrapper over ``group_image_sets`` that returns a plain dict so
    callers don't have to depend on the ``defaultdict`` import.
    """
    return dict(group_image_sets(data_dir))


def save_results(all_results, output_path, region_label, norm_label):
    """Save results to CSV and print summary."""
    if not all_results:
        print(f"No {region_label}s found.")
        return

    rl = region_label
    nl = norm_label
    df = pd.DataFrame(all_results)
    col_order = ['group', f'{rl}_id', f'{rl}_area_px',
                 'donut_area_px', 'bg_value',
                 f'cap_{rl}_raw_mean', 'cap_dilute_raw_mean',
                 f'cap_{rl}_raw_integ', 'cap_dilute_raw_integ',
                 f'cap_{rl}_mean', 'cap_dilute_mean',
                 f'cap_{rl}_integ', 'cap_dilute_integ',
                 f'{nl}_{rl}_mean', f'{nl}_dilute_mean',
                 f'{nl}_{rl}_integ', f'{nl}_dilute_integ',
                 f'cap_{rl}_over_dilute', f'{nl}_{rl}_over_dilute',
                 f'cap_over_{nl}_{rl}', f'cap_over_{nl}_dilute']
    df = df[col_order]
    df.to_csv(output_path, index=False)
    print(f"\nResults saved to {output_path}")
    print(f"Total {region_label}s analyzed: {len(df)}")
    print(f"\nSummary by group:")
    print(df.groupby('group')[[f'cap_{rl}_mean', 'cap_dilute_mean',
                                f'{nl}_{rl}_mean', f'{nl}_dilute_mean']].mean().to_string())


def save_single_cell_results(all_results, output_path, region_label, norm_label):
    """Save single-cell aggregated results to CSV and print summary."""
    if not all_results:
        print(f"No cells found for {region_label} analysis.")
        return

    rl = region_label
    nl = norm_label
    df = pd.DataFrame(all_results)
    col_order = ['group', 'cell_id', 'cell_area_px',
                 f'n_{rl}s', f'total_{rl}_area_px', 'total_donut_area_px',
                 'mean_bg_value',
                 f'cap_{rl}_raw_mean', 'cap_dilute_raw_mean',
                 f'cap_{rl}_raw_integ', 'cap_dilute_raw_integ',
                 f'cap_{rl}_mean', 'cap_dilute_mean',
                 f'cap_{rl}_integ', 'cap_dilute_integ',
                 f'{nl}_{rl}_mean', f'{nl}_dilute_mean',
                 f'{nl}_{rl}_integ', f'{nl}_dilute_integ',
                 f'cap_{rl}_over_dilute', f'{nl}_{rl}_over_dilute',
                 f'cap_over_{nl}_{rl}', f'cap_over_{nl}_dilute']
    df = df[col_order]
    df.to_csv(output_path, index=False)
    print(f"\nResults saved to {output_path}")
    print(f"Total cells analyzed: {len(df)}")
    print(f"\nSummary by group:")
    print(df.groupby('group')[[f'cap_{rl}_mean', 'cap_dilute_mean',
                                f'{nl}_{rl}_mean', f'{nl}_dilute_mean']].mean().to_string())


# IMPORTANT: Never modify an existing preset. Create a new version instead.
# This ensures published results citing a preset version remain reproducible.
PRESETS = {
    'm7g-cap-v1': {
        'buffer': 5,
        'donut': 5,
        'bg_mode': 'donut-mean',
        'min_size': 4,
        'bgsub_k': 2.5,
        'no_bgsub': False,
        'bg_value': 1,
        'exclude_cap_zero': True,
        'export_donuts': False,
    },
}

ORIGINAL_DEFAULTS = {
    'buffer': 4,
    'donut': 5,
    'bg_mode': 'donut',
    'min_size': 10,
    'bgsub_k': 2.5,
    'no_bgsub': False,
    'bg_value': 1,
    'exclude_cap_zero': True,
    'export_donuts': False,
}

PRESET_CONTROLLED_ARGS = set(ORIGINAL_DEFAULTS.keys())


def validate_presets(presets, parser):
    """Verify that all preset keys correspond to valid argparse destinations."""
    valid_dests = {action.dest for action in parser._actions}
    for name, vals in presets.items():
        bad_keys = set(vals.keys()) - valid_dests
        if bad_keys:
            raise ValueError(f"Preset '{name}' references unknown parameters: {bad_keys}")


def resolve_args(args, presets, original_defaults, preset_controlled):
    """Apply preset or original defaults. Error on conflicts with preset."""
    if args.preset:
        preset_name = args.preset
        if preset_name not in presets:
            sys.exit(f"Error: Unknown preset '{preset_name}'. "
                     f"Available presets: {', '.join(presets)}")
        preset_vals = presets[preset_name]
        for key, val in preset_vals.items():
            current = getattr(args, key)
            if current is not None:
                sys.exit(f"Error: --{key.replace('_', '-')} conflicts with "
                         f"--preset {preset_name}. Presets lock all analysis "
                         f"parameters; only --data-dir, --output, and "
                         f"--single-cell can be specified alongside a preset.")
            setattr(args, key, val)
        # Print active preset parameters
        param_str = ', '.join(f'{k}={v}' for k, v in preset_vals.items())
        print(f"[preset: {preset_name}] {param_str}")
    else:
        for key, val in original_defaults.items():
            if getattr(args, key) is None:
                setattr(args, key, val)

    # Defensive assertion: args that should have a non-None value must be set
    for attr in preset_controlled:
        if original_defaults.get(attr) is not None:
            assert getattr(args, attr) is not None, \
                f"Bug: {attr} is None after default/preset application"

    return args


def derive_output_paths(base_output):
    """Derive P-body and SG output paths from a base output path.

    Inserts '_p-body' and '_sg' before the file extension.
    Example: '/path/to/results.csv' -> '/path/to/results_p-body.csv', '/path/to/results_sg.csv'
    """
    from pathlib import Path
    p = Path(base_output)
    return (
        str(p.parent / f"{p.stem}_p-body{p.suffix}"),
        str(p.parent / f"{p.stem}_sg{p.suffix}"),
    )


def main():
    parser = argparse.ArgumentParser(
        description='Per-particle donut background subtraction analysis of fluorescence '
                    'intensities in masked subcellular regions (P-bodies and/or Stress Granules).',
        epilog='The script auto-detects which analyses to run based on available files per image set. '
               'When both P-body and SG files are present, Cap pixels inside SG_mask are set to NaN '
               'before P-body analysis (P-bodies embedded in stress granules cannot be reliably quantified).'
    )
    parser.add_argument('--data-dir', required=True,
                        help='Directory containing .tif/.tiff images')

    # --- Preset ---
    parser.add_argument('--preset', choices=list(PRESETS.keys()), default=None,
                        help='Named parameter preset for a specific assay. Locks all analysis '
                             'parameters; only --data-dir, --output/--output-pbody/--output-sg, '
                             'and --single-cell can be specified alongside a preset. '
                             f'Available: {", ".join(PRESETS.keys())}')

    # --- Analysis parameters (defaults=None; resolved by preset or ORIGINAL_DEFAULTS) ---
    parser.add_argument('--buffer', type=int, default=None,
                        help='Buffer zone dilation in pixels (default without preset: 4)')
    parser.add_argument('--donut', type=int, default=None,
                        help='Donut ring width in pixels (default without preset: 5)')
    parser.add_argument('--bg-mode', choices=['donut', 'donut-mean', 'flat'], default=None,
                        help='Background subtraction mode: "donut" estimates per-particle from '
                             'donut median, "donut-mean" from donut mean, '
                             '"flat" uses a single fixed value (default without preset: donut)')
    parser.add_argument('--bg-value', type=int, default=None,
                        help='Flat background value when --bg-mode=flat (default without preset: 1)')
    parser.add_argument('--exclude-cap-zero', action=argparse.BooleanOptionalAction, default=None,
                        help='Exclude zero values from Cap channel before estimating background '
                             '(default without preset: True, use --no-exclude-cap-zero to disable)')
    parser.add_argument('--min-size', type=int, default=None,
                        help='Only analyze particles larger than this many pixels (default without preset: 10)')
    parser.add_argument('--bgsub-k', type=float, default=None,
                        help='Background subtraction threshold expressed as mu + k*sigma of the '
                             'background Gaussian fit. Higher k is more permissive (default without preset: 2.5)')
    parser.add_argument('--no-bgsub', action='store_true', default=None,
                        help='Disable automatic background subtraction of the Cap image')
    parser.add_argument('--export-donuts', action='store_true', default=None,
                        help='Export binary donut mask TIFFs (one per image set) for overlay visualization')

    # --- Output (mutually exclusive: --output for presets, --output-pbody/--output-sg otherwise) ---
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument('--output', default=None,
                              help='Base output CSV path (use with --preset). Generates '
                                   '<name>_p-body.csv and <name>_sg.csv automatically.')
    output_group.add_argument('--output-pbody', default=None,
                              help='Output CSV for P-body results (use without --preset, '
                                   'requires --output-sg)')
    parser.add_argument('--output-sg', default=None,
                        help='Output CSV for Stress Granule results (use without --preset, '
                             'requires --output-pbody)')

    # --- Other ---
    parser.add_argument('--single-cell', action='store_true', default=False,
                        help='Aggregate all particle measurements by single cell using a _cp_mask.tiff '
                             'segmentation file. Each integer value in the mask represents one cell. '
                             'Particles are assigned to the cell containing the majority of their pixels. '
                             'Output will have one row per cell per image set.')
    args = parser.parse_args()

    # --- Validate presets ---
    validate_presets(PRESETS, parser)
    args = resolve_args(args, PRESETS, ORIGINAL_DEFAULTS, PRESET_CONTROLLED_ARGS)

    # --- Resolve output paths ---
    if args.preset:
        if args.output is None:
            parser.error('--output is required when using --preset')
        if args.output_sg is not None:
            parser.error('--output-sg cannot be used with --preset (use --output instead)')
        args.output_pbody, args.output_sg = derive_output_paths(args.output)
        print(f"  Output P-body: {args.output_pbody}")
        print(f"  Output SG:     {args.output_sg}")
    else:
        if args.output is not None:
            parser.error('--output can only be used with --preset (use --output-pbody and --output-sg instead)')
        if args.output_pbody is None or args.output_sg is None:
            parser.error('--output-pbody and --output-sg are both required when not using --preset')

    groups = _walk_groups_from_dir(args.data_dir)
    if not groups:
        print(f"No image sets found in {args.data_dir}")
        return

    pbody_results = []
    sg_results = []

    for group_key, channels in sorted(groups.items()):
        if 'Cap' not in channels:
            print(f"Skipping {group_key}: missing Cap channel")
            continue

        has_pbody = 'P-body_mask' in channels and 'pnorm' in channels
        has_sg = 'SG_mask' in channels and 'sgnorm' in channels

        if not has_pbody and not has_sg:
            missing = []
            if 'P-body_mask' not in channels:
                missing.append('P-body_mask')
            if 'pnorm' not in channels:
                missing.append('pnorm')
            if 'SG_mask' not in channels:
                missing.append('SG_mask')
            if 'sgnorm' not in channels:
                missing.append('sgnorm')
            print(f"Skipping {group_key}: need (P-body_mask + pnorm) and/or "
                  f"(SG_mask + sgnorm), missing {missing}")
            continue

        if args.single_cell and 'cp_mask' not in channels:
            print(f"Skipping {group_key}: --single-cell requires cp_mask file but none found")
            continue

        # ---- Read input TIFFs ----
        cap_img = tifffile.imread(channels['Cap'])
        pbody_mask_img = tifffile.imread(channels['P-body_mask']) if has_pbody else None
        pnorm_img = tifffile.imread(channels['pnorm']) if has_pbody else None
        sg_mask_img = tifffile.imread(channels['SG_mask']) if has_sg else None
        sgnorm_img = tifffile.imread(channels['sgnorm']) if has_sg else None
        cp_mask_img = tifffile.imread(channels['cp_mask']) if args.single_cell else None

        result = run_one_image_set(
            cap=cap_img,
            pbody_mask=pbody_mask_img, pnorm=pnorm_img,
            sg_mask=sg_mask_img, sgnorm=sgnorm_img,
            cp_mask=cp_mask_img,
            buffer=args.buffer, donut=args.donut,
            bg_mode=args.bg_mode, bg_value=args.bg_value,
            exclude_cap_zero=args.exclude_cap_zero,
            min_size=args.min_size,
            bgsub_k=args.bgsub_k, no_bgsub=args.no_bgsub,
            single_cell=args.single_cell,
            export_donuts=args.export_donuts,
            set_label=group_key, log=print,
        )

        # ---- Write optional donut TIFFs ----
        if args.export_donuts:
            if has_pbody and result["pbody_donut_mask"] is not None:
                out_path = os.path.join(
                    args.data_dir, f'{group_key}_pbody_donut_mask.tif'
                )
                tifffile.imwrite(out_path, result["pbody_donut_mask"])
                print(f"  Donut mask exported to {out_path}")
            if has_sg and result["sg_donut_mask"] is not None:
                out_path = os.path.join(
                    args.data_dir, f'{group_key}_sg_donut_mask.tif'
                )
                tifffile.imwrite(out_path, result["sg_donut_mask"])
                print(f"  Donut mask exported to {out_path}")

        # ---- Attach group_key and accumulate ----
        if result["pbody_rows"] is not None:
            for r in result["pbody_rows"]:
                r['group'] = group_key
            pbody_results.extend(result["pbody_rows"])
        if result["sg_rows"] is not None:
            for r in result["sg_rows"]:
                r['group'] = group_key
            sg_results.extend(result["sg_rows"])

    # --- Save results ---
    if args.single_cell:
        if pbody_results:
            save_single_cell_results(pbody_results, args.output_pbody, 'pbody', 'pnorm')
        if sg_results:
            save_single_cell_results(sg_results, args.output_sg, 'sg', 'sgnorm')
        if not pbody_results and not sg_results:
            print("No cells found in any image set.")
    else:
        if pbody_results:
            save_results(pbody_results, args.output_pbody, 'pbody', 'pnorm')
        if sg_results:
            save_results(sg_results, args.output_sg, 'sg', 'sgnorm')
        if not pbody_results and not sg_results:
            print("No particles found in any image set.")


if __name__ == '__main__':
    main()
