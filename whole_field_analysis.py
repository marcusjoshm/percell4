#!/usr/bin/env python3
"""
Whole-field decapping-sensor intensity analysis.

Whole-field (aggregate, not per-particle) quantification of a Halo
(decapping-sensor) channel vs an mNG normalization channel across masked
compartments (P-body vs dilute, plus an optional v4/v5 intermediate
compartment). Background subtraction is computed per field from the
dilute mask region. Optional single-cell mode emits one row per cell.

This CLI is the thin I/O wrapper around the pure analysis core at
``percell4.domain.analysis._impl.whole_field_intensity.run_one_image_set``.
The framework's registered ``WholeFieldIntensity`` analysis calls the same
pure core; both paths share one source of truth for the math.

Note: preset ``decapping-sensor-v1`` (cross-dataset ``SiR_mean`` Halo
background) and ``--save-processed`` are intentionally not supported here —
the analysis is strictly per-dataset and emits CSV/HDF5 outputs only.
"""
from __future__ import annotations

import sys
if sys.version_info < (3, 9):
    sys.exit("Error: Python 3.9 or higher is required "
             "(for argparse.BooleanOptionalAction).")

import argparse
import glob
import os
from collections import defaultdict

import pandas as pd
import tifffile

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from percell4.domain.analysis._impl.whole_field_intensity import (  # noqa: E402
    parse_bg_mode,
    run_one_image_set,
)

CHANNELS = ['P-body_mask', 'dilute_mask', 'interaction_mask',
            'interaction_mask_2', 'Dcp2_mask', 'Dcp2_mask_2', 'SiR_mask',
            'Halo', 'mNG', 'cp_mask']


def parse_filename(filepath):
    """Parse a .tif/.tiff filename by detecting a known channel keyword."""
    basename = os.path.basename(filepath)
    name_no_ext = os.path.splitext(basename)[0]
    for ch in sorted(CHANNELS, key=len, reverse=True):
        idx = name_no_ext.find(ch)
        if idx != -1:
            group_key = name_no_ext[:idx].strip('_')
            return {'group_key': group_key, 'channel': ch}
    return None


def group_image_sets(data_dir):
    """Group .tif/.tiff files by shared name prefix."""
    files = (glob.glob(os.path.join(data_dir, '*.tif'))
             + glob.glob(os.path.join(data_dir, '*.tiff')))
    groups = defaultdict(dict)
    for f in files:
        parsed = parse_filename(f)
        if parsed is None:
            print(f"Warning: could not parse filename (no channel keyword "
                  f"found): {os.path.basename(f)}")
            continue
        groups[parsed['group_key']][parsed['channel']] = f
    return groups


# IMPORTANT: Never modify an existing preset. Create a new version instead.
PRESETS = {
    'decapping-sensor-v2': {
        'min_size': 2, 'mng_bg_mode': 0, 'halo_bg_mode': 0,
        'mNG_filter': 'NaN', 'percent': False, 'exclude_halo_zero': True,
        'exclude_halo_one': False, 'SiR_subtract': None, 'SiR_filter': True,
        'FLIM_filter': None, 'mNG_in_FLIM': 'no',
        'intermediate_assemblies': False, 'intermediate_zero_fill': False,
    },
    'decapping-sensor-v3': {
        'min_size': 2, 'mng_bg_mode': 0, 'halo_bg_mode': 0,
        'mNG_filter': 'NaN', 'percent': True, 'exclude_halo_zero': True,
        'exclude_halo_one': False, 'SiR_subtract': None, 'SiR_filter': False,
        'FLIM_filter': 'zero', 'mNG_in_FLIM': 'no',
        'intermediate_assemblies': False, 'intermediate_zero_fill': False,
    },
    'decapping-sensor-v4': {
        'min_size': 2, 'mng_bg_mode': 0, 'halo_bg_mode': 0,
        'mNG_filter': 'NaN', 'percent': True, 'exclude_halo_zero': True,
        'exclude_halo_one': False, 'SiR_subtract': None, 'SiR_filter': False,
        'FLIM_filter': 'zero', 'mNG_in_FLIM': 'no',
        'intermediate_assemblies': True, 'intermediate_zero_fill': False,
    },
    'decapping-sensor-v5': {
        'min_size': 2, 'mng_bg_mode': 0, 'halo_bg_mode': 0,
        'mNG_filter': 'NaN', 'percent': True, 'exclude_halo_zero': True,
        'exclude_halo_one': False, 'SiR_subtract': None, 'SiR_filter': False,
        'FLIM_filter': 'zero', 'mNG_in_FLIM': 'no',
        'intermediate_assemblies': True, 'intermediate_zero_fill': True,
    },
}

ORIGINAL_DEFAULTS = {
    'min_size': 10, 'mng_bg_mode': 'mean', 'halo_bg_mode': 'median',
    'mNG_filter': None, 'percent': False, 'exclude_halo_zero': True,
    'exclude_halo_one': False, 'SiR_subtract': None, 'SiR_filter': False,
    'FLIM_filter': None, 'mNG_in_FLIM': 'no',
    'intermediate_assemblies': False, 'intermediate_zero_fill': False,
}

PRESET_CONTROLLED_ARGS = set(ORIGINAL_DEFAULTS.keys())


def validate_presets(presets, parser):
    valid_dests = {action.dest for action in parser._actions}
    for name, vals in presets.items():
        bad_keys = set(vals.keys()) - valid_dests
        if bad_keys:
            raise ValueError(
                f"Preset '{name}' references unknown parameters: {bad_keys}"
            )


def resolve_args(args, presets, original_defaults, preset_controlled):
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
        param_str = ', '.join(f'{k}={v}' for k, v in preset_vals.items())
        print(f"[preset: {preset_name}] {param_str}")
    else:
        for key, val in original_defaults.items():
            if getattr(args, key) is None:
                setattr(args, key, val)

    for attr in preset_controlled:
        if original_defaults.get(attr) is not None:
            assert getattr(args, attr) is not None, \
                f"Bug: {attr} is None after default/preset application"
    return args


# Column orderings per output mode (verbatim from the original CLI).
def _col_order(single_cell, intermediate, percent):
    if single_cell and intermediate:
        cols = ['group', 'cell_id', 'cell_area_px', 'particle_count',
                'pbody_area_px', 'intermediate_area_px', 'dilute_area_px',
                'mng_bg_value', 'halo_bg_value',
                'mNG_pbody_mean', 'mNG_intermediate_mean', 'mNG_dilute_mean',
                'mNG_pbody_integ', 'mNG_intermediate_integ', 'mNG_dilute_integ',
                'halo_pbody_mean', 'halo_intermediate_mean', 'halo_dilute_mean',
                'halo_pbody_integ', 'halo_intermediate_integ',
                'halo_dilute_integ',
                'mNG_pbody_over_dilute', 'mNG_intermediate_over_dilute',
                'halo_pbody_over_dilute', 'halo_intermediate_over_dilute',
                'halo_over_mNG_pbody', 'halo_over_mNG_intermediate',
                'halo_over_mNG_dilute', 'mNG_cell_mean']
        if percent:
            cols += ['pct_halo_in_mNG_pbody', 'pct_halo_in_mNG_intermediate',
                     'pct_halo_in_mNG_dilute']
    elif single_cell:
        cols = ['group', 'cell_id', 'cell_area_px', 'particle_count',
                'pbody_area_px', 'dilute_area_px',
                'mng_bg_value', 'halo_bg_value',
                'mNG_pbody_mean', 'mNG_dilute_mean',
                'mNG_pbody_integ', 'mNG_dilute_integ',
                'halo_pbody_mean', 'halo_dilute_mean',
                'halo_pbody_integ', 'halo_dilute_integ',
                'mNG_pbody_over_dilute', 'halo_pbody_over_dilute',
                'halo_over_mNG_pbody', 'halo_over_mNG_dilute', 'mNG_cell_mean']
        if percent:
            cols += ['pct_halo_in_mNG_pbody', 'pct_halo_in_mNG_dilute']
    elif intermediate:
        cols = ['group', 'pbody_area_px', 'intermediate_area_px',
                'dilute_area_px', 'mng_bg_value', 'halo_bg_value',
                'mNG_pbody_mean', 'mNG_intermediate_mean', 'mNG_dilute_mean',
                'mNG_pbody_integ', 'mNG_intermediate_integ', 'mNG_dilute_integ',
                'halo_pbody_mean', 'halo_intermediate_mean', 'halo_dilute_mean',
                'halo_pbody_integ', 'halo_intermediate_integ',
                'halo_dilute_integ',
                'mNG_pbody_over_dilute', 'mNG_intermediate_over_dilute',
                'halo_pbody_over_dilute', 'halo_intermediate_over_dilute',
                'halo_over_mNG_pbody', 'halo_over_mNG_intermediate',
                'halo_over_mNG_dilute']
        if percent:
            cols += ['pct_halo_in_mNG_pbody', 'pct_halo_in_mNG_intermediate',
                     'pct_halo_in_mNG_dilute']
    else:
        cols = ['group', 'pbody_area_px', 'dilute_area_px',
                'mng_bg_value', 'halo_bg_value',
                'mNG_pbody_mean', 'mNG_dilute_mean',
                'mNG_pbody_integ', 'mNG_dilute_integ',
                'halo_pbody_mean', 'halo_dilute_mean',
                'halo_pbody_integ', 'halo_dilute_integ',
                'mNG_pbody_over_dilute', 'halo_pbody_over_dilute',
                'halo_over_mNG_pbody', 'halo_over_mNG_dilute']
        if percent:
            cols += ['pct_halo_in_mNG_pbody', 'pct_halo_in_mNG_dilute']
    return cols


def main():
    parser = argparse.ArgumentParser(
        description='Whole-field decapping-sensor intensity analysis. '
                    'Background subtraction is computed per field from the '
                    'dilute mask region.'
    )
    parser.add_argument('--data-dir', required=True,
                        help='Directory containing .tif/.tiff images')
    parser.add_argument('--output', required=True, help='Output CSV path')
    parser.add_argument('--preset', choices=list(PRESETS.keys()), default=None,
                        help='Named parameter preset (decapping-sensor-v2..v5). '
                             'Locks all analysis parameters.')
    parser.add_argument('--mng-bg-mode', type=parse_bg_mode, default=None)
    parser.add_argument('--halo-bg-mode', type=parse_bg_mode, default=None)
    parser.add_argument('--exclude-halo-zero',
                        action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--exclude-halo-one',
                        action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--min-size', type=int, default=None)
    parser.add_argument('--SiR-subtract', choices=['zero', 'NaN'],
                        default=None)
    parser.add_argument('--SiR-filter', action='store_true', default=None)
    parser.add_argument('--FLIM-filter', choices=['zero', 'NaN'], default=None)
    parser.add_argument('--mNG-filter', choices=['zero', 'NaN'], default=None)
    parser.add_argument('--mNG-in-FLIM', choices=['yes', 'no'], default=None)
    parser.add_argument('--percent', action='store_true', default=None)
    parser.add_argument('--intermediate-assemblies', action='store_true',
                        default=None)
    parser.add_argument('--intermediate-zero-fill', action='store_true',
                        default=None)
    parser.add_argument('--single-cell', action='store_true', default=False)
    args = parser.parse_args()

    validate_presets(PRESETS, parser)
    args = resolve_args(args, PRESETS, ORIGINAL_DEFAULTS, PRESET_CONTROLLED_ARGS)

    if args.halo_bg_mode == 'SiR_mean':
        sys.exit("Error: --halo-bg-mode SiR_mean (preset v1) is not supported; "
                 "the analysis is strictly per-dataset.")

    groups = group_image_sets(args.data_dir)
    if not groups:
        print(f"No image sets found in {args.data_dir}")
        return

    sir_subtract_mode = getattr(args, 'SiR_subtract', None)
    sir_filter = bool(getattr(args, 'SiR_filter', False))
    flim_filter_mode = getattr(args, 'FLIM_filter', None)
    mng_filter_mode = getattr(args, 'mNG_filter', None)
    mng_in_flim = getattr(args, 'mNG_in_FLIM', 'no') == 'yes'
    intermediate_assemblies = bool(getattr(args, 'intermediate_assemblies',
                                           False))
    intermediate_zero_fill = bool(getattr(args, 'intermediate_zero_fill',
                                          False))

    if sir_filter and sir_subtract_mode is not None:
        print("WARNING: --SiR-filter cannot be combined with --SiR-subtract.")
        return
    if intermediate_zero_fill and not intermediate_assemblies:
        print("WARNING: --intermediate-zero-fill requires "
              "--intermediate-assemblies.")
        return
    if intermediate_assemblies:
        if sir_subtract_mode is not None:
            print("WARNING: --intermediate-assemblies cannot be combined with "
                  "--SiR-subtract.")
            return
        if sir_filter:
            print("WARNING: --intermediate-assemblies cannot be combined with "
                  "--SiR-filter.")
            return
        if mng_filter_mode is None:
            print("WARNING: --intermediate-assemblies requires --mNG-filter.")
            return
        if flim_filter_mode is None:
            print("WARNING: --intermediate-assemblies requires --FLIM-filter.")
            return

    def _check_channels(group_key, channels):
        required = {'P-body_mask', 'dilute_mask', 'Halo', 'mNG'}
        if not required.issubset(channels.keys()):
            print(f"Skipping {group_key}: missing channels "
                  f"{required - channels.keys()}")
            return False
        if sir_subtract_mode is not None and 'SiR_mask' not in channels:
            print(f"Skipping {group_key}: --SiR-subtract requires SiR_mask")
            return False
        if sir_filter and 'SiR_mask' not in channels:
            print(f"Skipping {group_key}: --SiR-filter requires a SiR_mask")
            return False
        if flim_filter_mode is not None and 'interaction_mask' not in channels:
            print(f"Skipping {group_key}: --FLIM-filter requires "
                  f"interaction_mask")
            return False
        if mng_filter_mode is not None and 'Dcp2_mask' not in channels:
            print(f"Skipping {group_key}: --mNG-filter requires Dcp2_mask")
            return False
        if mng_in_flim and ('interaction_mask' not in channels
                            or 'Dcp2_mask' not in channels):
            print(f"Skipping {group_key}: --mNG-in-FLIM yes requires both "
                  f"interaction_mask and Dcp2_mask")
            return False
        if args.single_cell and 'cp_mask' not in channels:
            print(f"Skipping {group_key}: --single-cell requires cp_mask")
            return False
        if intermediate_assemblies:
            missing = [m for m in ('Dcp2_mask', 'interaction_mask',
                                   'Dcp2_mask_2', 'interaction_mask_2')
                       if m not in channels]
            if missing:
                print(f"Skipping {group_key}: --intermediate-assemblies "
                      f"requires {', '.join(missing)}")
                return False
        return True

    def _read(channels, key):
        return tifffile.imread(channels[key]) if key in channels else None

    def _analyze_group(group_key, channels):
        print(f"Analyzing: {group_key}")
        result = run_one_image_set(
            pbody_mask=tifffile.imread(channels['P-body_mask']),
            dilute_mask=tifffile.imread(channels['dilute_mask']),
            halo=tifffile.imread(channels['Halo']),
            mng=tifffile.imread(channels['mNG']),
            cp_mask=_read(channels, 'cp_mask'),
            dcp2_mask=_read(channels, 'Dcp2_mask'),
            interaction_mask=_read(channels, 'interaction_mask'),
            sir_mask=_read(channels, 'SiR_mask'),
            dcp2_mask_2=_read(channels, 'Dcp2_mask_2'),
            interaction_mask_2=_read(channels, 'interaction_mask_2'),
            mng_bg_mode=args.mng_bg_mode, halo_bg_mode=args.halo_bg_mode,
            min_size=args.min_size,
            exclude_halo_zero=args.exclude_halo_zero,
            exclude_halo_one=args.exclude_halo_one,
            sir_subtract_mode=sir_subtract_mode,
            flim_filter_mode=flim_filter_mode,
            mng_in_flim=mng_in_flim,
            mng_filter_mode=mng_filter_mode,
            compute_percent=args.percent,
            single_cell=args.single_cell,
            sir_filter=sir_filter,
            intermediate_assemblies=intermediate_assemblies,
            intermediate_zero_fill=intermediate_zero_fill,
            set_label=group_key, log=print,
        )
        rows = result["rows"]
        for r in rows:
            r['group'] = group_key
        return rows

    all_results = []
    for group_key, channels in sorted(groups.items()):
        if not _check_channels(group_key, channels):
            continue
        all_results.extend(_analyze_group(group_key, channels))

    if not all_results:
        print("No complete image sets found.")
        return

    df = pd.DataFrame(all_results)
    df = df[_col_order(args.single_cell, intermediate_assemblies, args.percent)]
    df.to_csv(args.output, index=False)
    print(f"\nResults saved to {args.output}")
    print(f"Total rows: {len(df)}")


if __name__ == '__main__':
    main()
