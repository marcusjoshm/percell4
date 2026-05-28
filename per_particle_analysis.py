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
from scipy.ndimage import distance_transform_edt
from scipy.optimize import curve_fit
from skimage import measure


CHANNELS = ['P-body_mask', 'SG_mask', 'Cap', 'pnorm', 'sgnorm', 'cp_mask']


def _estimate_bg_hwhm(flat, k_sigma):
    """Fallback background estimator for low-signal images.

    Uses unit-width histogram bins to find the mode, then derives sigma from
    the half-width at half-maximum (HWHM) of the background peak.
    """
    upper = max(np.percentile(flat, 75), 20)
    hist, edges = np.histogram(flat, bins=np.arange(0, upper + 1, 1))
    centers = (edges[:-1] + edges[1:]) / 2

    mode_idx = np.argmax(hist)
    mode_val = centers[mode_idx]
    half_max = hist[mode_idx] / 2.0

    # Walk right from mode until histogram drops below half-max
    right_side = hist[mode_idx:]
    hwhm_idx = np.argmax(right_side < half_max)
    if hwhm_idx == 0:
        # Never dropped below half-max; use distance to end
        hwhm_idx = len(right_side) - 1
    # HWHM -> sigma:  FWHM = 2.355*sigma, so HWHM = 1.177*sigma
    sigma = hwhm_idx / 1.177

    mu = mode_val
    threshold = mu + k_sigma * sigma
    print(f"  [bg-fallback HWHM] mode={mu:.1f}, sigma={sigma:.1f}, "
          f"threshold={threshold:.1f}")
    return threshold, mu, sigma


def estimate_bg_threshold(cap_img, k_sigma=2.5):
    """Estimate a background threshold by fitting a Gaussian to the background peak.

    Builds a histogram of the Cap image, finds the mode (background peak),
    fits a Gaussian to the low-intensity population, and returns mu + k*sigma
    as the threshold. Pixels below this are considered background (nucleus,
    cell borders, outside cell).

    Falls back to a HWHM-based estimator when the image is too low-signal
    for reliable Gaussian fitting.

    Returns
    -------
    threshold : float
        The estimated background threshold.
    mu : float
        Center of the fitted Gaussian.
    sigma : float
        Width of the fitted Gaussian.
    """
    flat = cap_img[~np.isnan(cap_img)]
    bins = np.arange(0, np.percentile(flat, 50), 5)
    if len(bins) < 10:
        bins = np.arange(0, np.percentile(flat, 75), 5)

    # Not enough bins for a meaningful histogram — go straight to fallback
    if len(bins) < 3:
        return _estimate_bg_hwhm(flat, k_sigma)

    hist, edges = np.histogram(flat, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2

    mode_idx = np.argmax(hist)
    mode_val = centers[mode_idx]

    def _gaussian(x, amp, mu, sigma):
        return amp * np.exp(-(x - mu) ** 2 / (2 * sigma ** 2))

    fit_range = centers <= mode_val * 2.5
    if np.sum(fit_range) < 4:
        return _estimate_bg_hwhm(flat, k_sigma)

    try:
        popt, _ = curve_fit(
            _gaussian, centers[fit_range], hist[fit_range],
            p0=[hist[mode_idx], mode_val, mode_val * 0.5],
            maxfev=10000,
        )
    except (TypeError, RuntimeError):
        return _estimate_bg_hwhm(flat, k_sigma)

    mu, sigma = popt[1], abs(popt[2])
    threshold = mu + k_sigma * sigma
    return threshold, mu, sigma


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


def analyze_regions(mask_path, cap_img, norm_img, buffer_px, donut_px,
                    bg_mode, flat_bg_value, min_size,
                    exclude_cap_zero=True, region_label='pbody', norm_label='pnorm',
                    export_donut_path=None):
    """Analyze one set of regions (P-bodies or SGs) and return per-particle measurements.

    Parameters
    ----------
    mask_path : str
        Path to the binary mask TIFF (P-body_mask or SG_mask).
    cap_img : ndarray
        Cap intensity image (float64), potentially with NaN exclusions already applied.
    norm_img : ndarray
        Normalization channel image (float64) — pnorm or sgnorm.
    region_label : str
        Label for output columns — 'pbody' or 'sg'.
    norm_label : str
        Label for normalization channel in output columns — 'pnorm' or 'sgnorm'.
    """
    mask_img = tifffile.imread(mask_path)

    # Label individual regions
    binary_mask = mask_img > 0
    label_mask = measure.label(binary_mask)
    region_ids = np.unique(label_mask)
    region_ids = region_ids[region_ids != 0]

    # Precompute regionprops for bounding boxes and sizes
    props = measure.regionprops(label_mask)
    props_by_id = {p.label: p for p in props}

    # Filter by size
    if min_size > 0:
        region_ids = np.array([rid for rid in region_ids if props_by_id[rid].area > min_size])

    if bg_mode == 'flat':
        bg_label_str = f"flat={flat_bg_value}"
    elif bg_mode == 'donut-mean':
        bg_label_str = "donut-mean-estimated"
    else:
        bg_label_str = "donut-median-estimated"
    print(f"  Found {len(region_ids)} {region_label}s > {min_size} px "
          f"(bg: {bg_label_str})")

    h, w = mask_img.shape
    pad = buffer_px + donut_px + 1

    # Accumulate donut masks for export
    if export_donut_path is not None:
        donut_export = np.zeros((h, w), dtype=np.uint8)

    results = []
    for region_id in region_ids:
        # Crop to bounding box + padding for speed
        bbox = props_by_id[region_id].bbox
        r0 = max(bbox[0] - pad, 0)
        c0 = max(bbox[1] - pad, 0)
        r1 = min(bbox[2] + pad, h)
        c1 = min(bbox[3] + pad, w)

        crop_label = label_mask[r0:r1, c0:c1]
        crop_binary = binary_mask[r0:r1, c0:c1]
        crop_region = crop_label == region_id

        # Distance transform on small crop
        dist_from_this = distance_transform_edt(~crop_region)
        in_donut_range = (dist_from_this > buffer_px) & (dist_from_this <= buffer_px + donut_px)
        crop_donut = in_donut_range & ~crop_binary

        # Map back to full-image coordinates
        region_mask = label_mask == region_id
        donut_mask = np.zeros_like(binary_mask)
        donut_mask[r0:r1, c0:c1] = crop_donut

        if donut_mask.sum() == 0:
            continue

        if export_donut_path is not None:
            donut_export[donut_mask] = 255

        cap_donut_raw = cap_img[donut_mask]

        # Determine bg value (optionally exclude zeros for estimation only)
        cap_donut_for_bg = cap_donut_raw[~np.isnan(cap_donut_raw)]
        if exclude_cap_zero and len(cap_donut_for_bg) > 0:
            nonzero = cap_donut_for_bg[cap_donut_for_bg != 0]
            if len(nonzero) > 0:
                cap_donut_for_bg = nonzero
        if len(cap_donut_for_bg) == 0:
            cap_donut_for_bg = cap_donut_raw[~np.isnan(cap_donut_raw)]
        if len(cap_donut_for_bg) == 0:
            continue

        if bg_mode == 'donut':
            bg_value = int(np.round(np.median(cap_donut_for_bg)))
        elif bg_mode == 'donut-mean':
            bg_value = int(np.round(np.mean(cap_donut_for_bg)))
        else:
            bg_value = flat_bg_value

        # Background-subtracted Cap (capped at zero)
        cap_region_sub = np.maximum(cap_img[region_mask] - bg_value, 0)
        cap_donut_sub = np.maximum(cap_donut_raw - bg_value, 0)

        # Preserve NaN from exclusion masking
        cap_region_raw = cap_img[region_mask]
        cap_region_sub[np.isnan(cap_region_raw)] = np.nan
        cap_donut_sub[np.isnan(cap_donut_raw)] = np.nan

        # Raw (pre-subtraction) means and integrated intensities
        cap_region_raw_mean = np.nanmean(cap_region_raw) if np.any(~np.isnan(cap_region_raw)) else np.nan
        cap_donut_raw_mean = np.nanmean(cap_donut_raw) if np.any(~np.isnan(cap_donut_raw)) else np.nan
        cap_region_raw_integ = np.nansum(cap_region_raw) if np.any(~np.isnan(cap_region_raw)) else np.nan
        cap_donut_raw_integ = np.nansum(cap_donut_raw) if np.any(~np.isnan(cap_donut_raw)) else np.nan

        cap_region_mean = np.nanmean(cap_region_sub) if np.any(~np.isnan(cap_region_sub)) else np.nan
        cap_donut_mean = np.nanmean(cap_donut_sub) if np.any(~np.isnan(cap_donut_sub)) else np.nan
        cap_region_integ = np.nansum(cap_region_sub) if np.any(~np.isnan(cap_region_sub)) else np.nan
        cap_donut_integ = np.nansum(cap_donut_sub) if np.any(~np.isnan(cap_donut_sub)) else np.nan
        norm_region_mean = np.mean(norm_img[region_mask]) if region_mask.sum() > 0 else np.nan
        norm_donut_mean = np.mean(norm_img[donut_mask]) if donut_mask.sum() > 0 else np.nan
        norm_region_integ = np.sum(norm_img[region_mask]) if region_mask.sum() > 0 else np.nan
        norm_donut_integ = np.sum(norm_img[donut_mask]) if donut_mask.sum() > 0 else np.nan

        # Ratios (safe division)
        def _ratio(a, b):
            return a / b if (b and not np.isnan(a) and not np.isnan(b) and b != 0) else np.nan

        rl = region_label  # shorthand for column names
        nl = norm_label
        results.append({
            f'{rl}_id': int(region_id),
            f'{rl}_area_px': int(region_mask.sum()),
            'donut_area_px': int(donut_mask.sum()),
            'bg_value': bg_value,
            f'cap_{rl}_raw_mean': cap_region_raw_mean,
            'cap_dilute_raw_mean': cap_donut_raw_mean,
            f'cap_{rl}_raw_integ': cap_region_raw_integ,
            'cap_dilute_raw_integ': cap_donut_raw_integ,
            f'cap_{rl}_mean': cap_region_mean,
            'cap_dilute_mean': cap_donut_mean,
            f'cap_{rl}_integ': cap_region_integ,
            'cap_dilute_integ': cap_donut_integ,
            f'{nl}_{rl}_mean': norm_region_mean,
            f'{nl}_dilute_mean': norm_donut_mean,
            f'{nl}_{rl}_integ': norm_region_integ,
            f'{nl}_dilute_integ': norm_donut_integ,
            f'cap_{rl}_over_dilute': _ratio(cap_region_mean, cap_donut_mean),
            f'{nl}_{rl}_over_dilute': _ratio(norm_region_mean, norm_donut_mean),
            f'cap_over_{nl}_{rl}': _ratio(cap_region_mean, norm_region_mean),
            f'cap_over_{nl}_dilute': _ratio(cap_donut_mean, norm_donut_mean),
        })

    if export_donut_path is not None:
        tifffile.imwrite(export_donut_path, donut_export)
        print(f"  Donut mask exported to {export_donut_path}")

    return results


def assign_particles_to_cells(mask_path, cp_mask_img, min_size):
    """Return dict mapping particle label ID -> cell ID (majority of pixels)."""
    mask_img = tifffile.imread(mask_path)
    label_mask = measure.label(mask_img > 0)
    props = measure.regionprops(label_mask)

    mapping = {}
    for p in props:
        if min_size > 0 and p.area <= min_size:
            continue
        particle_pixels = label_mask == p.label
        cell_values = cp_mask_img[particle_pixels]
        cell_values = cell_values[cell_values != 0]
        if len(cell_values) == 0:
            continue
        mapping[p.label] = int(np.bincount(cell_values).argmax())
    return mapping


def aggregate_by_cell(particle_results, particle_to_cell, cp_mask_img,
                      region_label, norm_label):
    """Aggregate per-particle results into per-cell rows.

    Particles are grouped by assigned cell ID. Means are area-weighted,
    integrated intensities are summed, and ratios are recomputed from
    the aggregated means. Cells with no particles get NaN for all metrics.
    """
    rl = region_label
    nl = norm_label

    # Group particles by cell
    cell_particles = defaultdict(list)
    for result in particle_results:
        pid = result[f'{rl}_id']
        if pid in particle_to_cell:
            cell_particles[particle_to_cell[pid]].append(result)

    all_cell_ids = np.unique(cp_mask_img)
    all_cell_ids = all_cell_ids[all_cell_ids != 0]

    metric_cols = [
        f'cap_{rl}_raw_mean', 'cap_dilute_raw_mean',
        f'cap_{rl}_raw_integ', 'cap_dilute_raw_integ',
        f'cap_{rl}_mean', 'cap_dilute_mean',
        f'cap_{rl}_integ', 'cap_dilute_integ',
        f'{nl}_{rl}_mean', f'{nl}_dilute_mean',
        f'{nl}_{rl}_integ', f'{nl}_dilute_integ',
        f'cap_{rl}_over_dilute', f'{nl}_{rl}_over_dilute',
        f'cap_over_{nl}_{rl}', f'cap_over_{nl}_dilute',
    ]

    aggregated = []
    for cell_id in all_cell_ids:
        cell_id = int(cell_id)
        cell_area = int((cp_mask_img == cell_id).sum())
        particles = cell_particles.get(cell_id, [])

        if not particles:
            row = {
                'cell_id': cell_id,
                'cell_area_px': cell_area,
                f'n_{rl}s': 0,
                f'total_{rl}_area_px': 0,
                'total_donut_area_px': 0,
                'mean_bg_value': np.nan,
            }
            for col in metric_cols:
                row[col] = np.nan
            aggregated.append(row)
            continue

        df_p = pd.DataFrame(particles)
        total_region_area = df_p[f'{rl}_area_px'].sum()
        total_donut_area = df_p['donut_area_px'].sum()

        def _weighted_mean(col, weights_col):
            weights = df_p[weights_col].values.astype(float)
            values = df_p[col].values.astype(float)
            valid = ~(np.isnan(values) | np.isnan(weights)) & (weights > 0)
            if not np.any(valid):
                return np.nan
            return float((values[valid] * weights[valid]).sum() / weights[valid].sum())

        def _ratio(a, b):
            if pd.notna(a) and pd.notna(b) and b != 0:
                return a / b
            return np.nan

        row = {
            'cell_id': cell_id,
            'cell_area_px': cell_area,
            f'n_{rl}s': len(particles),
            f'total_{rl}_area_px': int(total_region_area),
            'total_donut_area_px': int(total_donut_area),
            'mean_bg_value': float(df_p['bg_value'].mean()),
            # Area-weighted means
            f'cap_{rl}_raw_mean': _weighted_mean(f'cap_{rl}_raw_mean', f'{rl}_area_px'),
            'cap_dilute_raw_mean': _weighted_mean('cap_dilute_raw_mean', 'donut_area_px'),
            f'cap_{rl}_mean': _weighted_mean(f'cap_{rl}_mean', f'{rl}_area_px'),
            'cap_dilute_mean': _weighted_mean('cap_dilute_mean', 'donut_area_px'),
            f'{nl}_{rl}_mean': _weighted_mean(f'{nl}_{rl}_mean', f'{rl}_area_px'),
            f'{nl}_dilute_mean': _weighted_mean(f'{nl}_dilute_mean', 'donut_area_px'),
            # Summed integrated intensities
            f'cap_{rl}_raw_integ': float(df_p[f'cap_{rl}_raw_integ'].sum()),
            'cap_dilute_raw_integ': float(df_p['cap_dilute_raw_integ'].sum()),
            f'cap_{rl}_integ': float(df_p[f'cap_{rl}_integ'].sum()),
            'cap_dilute_integ': float(df_p['cap_dilute_integ'].sum()),
            f'{nl}_{rl}_integ': float(df_p[f'{nl}_{rl}_integ'].sum()),
            f'{nl}_dilute_integ': float(df_p[f'{nl}_dilute_integ'].sum()),
        }

        # Recompute ratios from aggregated means
        row[f'cap_{rl}_over_dilute'] = _ratio(row[f'cap_{rl}_mean'], row['cap_dilute_mean'])
        row[f'{nl}_{rl}_over_dilute'] = _ratio(row[f'{nl}_{rl}_mean'], row[f'{nl}_dilute_mean'])
        row[f'cap_over_{nl}_{rl}'] = _ratio(row[f'cap_{rl}_mean'], row[f'{nl}_{rl}_mean'])
        row[f'cap_over_{nl}_dilute'] = _ratio(row['cap_dilute_mean'], row[f'{nl}_dilute_mean'])

        aggregated.append(row)

    return aggregated


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

    groups = group_image_sets(args.data_dir)
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

        cap_img = tifffile.imread(channels['Cap']).astype(np.float64)

        # Load cp_mask once per group if in single-cell mode
        cp_mask_img = tifffile.imread(channels['cp_mask']) if args.single_cell else None

        # --- Background subtraction: NaN out low-signal pixels ---
        if not args.no_bgsub:
            bg_thresh, bg_mu, bg_sig = estimate_bg_threshold(cap_img, args.bgsub_k)
            n_masked = int(np.sum(cap_img < bg_thresh))
            pct_masked = n_masked / cap_img.size * 100
            print(f"  Background subtraction for {group_key}: "
                  f"mu={bg_mu:.1f}, sigma={bg_sig:.1f}, "
                  f"threshold={bg_thresh:.0f} (mu+{args.bgsub_k}*sigma), "
                  f"{n_masked} px ({pct_masked:.1f}%) set to NaN")
            cap_img[cap_img < bg_thresh] = np.nan

        # --- SG analysis (uses bg-subtracted Cap, no SG exclusion) ---
        if has_sg:
            print(f"Analyzing SGs: {group_key}")
            sgnorm_img = tifffile.imread(channels['sgnorm']).astype(np.float64)
            sg_donut_path = os.path.join(args.data_dir, f'{group_key}_sg_donut_mask.tif') if args.export_donuts else None
            results = analyze_regions(
                channels['SG_mask'], cap_img, sgnorm_img,
                args.buffer, args.donut, args.bg_mode, args.bg_value,
                args.min_size, args.exclude_cap_zero,
                region_label='sg', norm_label='sgnorm',
                export_donut_path=sg_donut_path,
            )
            for r in results:
                r['group'] = group_key

            if args.single_cell:
                particle_to_cell = assign_particles_to_cells(
                    channels['SG_mask'], cp_mask_img, args.min_size)
                cell_results = aggregate_by_cell(
                    results, particle_to_cell, cp_mask_img, 'sg', 'sgnorm')
                for r in cell_results:
                    r['group'] = group_key
                sg_results.extend(cell_results)
                print(f"  Single-cell SG: {len(cell_results)} cells aggregated")
            else:
                sg_results.extend(results)

        # --- P-body analysis (SG exclusion first, then uses bg-subtracted Cap) ---
        if has_pbody:
            cap_for_pbody = cap_img.copy()
            if has_sg:
                sg_mask_img = tifffile.imread(channels['SG_mask'])
                sg_inside = sg_mask_img > 0
                n_excluded = int(sg_inside.sum())
                cap_for_pbody[sg_inside] = np.nan
                print(f"Analyzing P-bodies (SG-excluded): {group_key} "
                      f"({n_excluded} Cap pixels inside SG_mask set to NaN)")
            else:
                print(f"Analyzing P-bodies: {group_key}")

            pnorm_img = tifffile.imread(channels['pnorm']).astype(np.float64)
            pbody_donut_path = os.path.join(args.data_dir, f'{group_key}_pbody_donut_mask.tif') if args.export_donuts else None
            results = analyze_regions(
                channels['P-body_mask'], cap_for_pbody, pnorm_img,
                args.buffer, args.donut, args.bg_mode, args.bg_value,
                args.min_size, args.exclude_cap_zero,
                region_label='pbody', norm_label='pnorm',
                export_donut_path=pbody_donut_path,
            )
            for r in results:
                r['group'] = group_key

            if args.single_cell:
                particle_to_cell = assign_particles_to_cells(
                    channels['P-body_mask'], cp_mask_img, args.min_size)
                cell_results = aggregate_by_cell(
                    results, particle_to_cell, cp_mask_img, 'pbody', 'pnorm')
                for r in cell_results:
                    r['group'] = group_key
                pbody_results.extend(cell_results)
                print(f"  Single-cell P-body: {len(cell_results)} cells aggregated")
            else:
                pbody_results.extend(results)

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
