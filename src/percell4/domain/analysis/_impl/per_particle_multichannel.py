"""Per-particle multi-channel dilute-vs-condensed analysis — pure core.

Single source of truth for the multichannel donut math, shared by:

* The repo-root CLI ``per_particle_multichannel.py`` (thin I/O wrapper).
* The registered framework analysis ``PerParticleMultichannel`` (U4).

For each particle in a binary mask it measures mean gray value and
integrated intensity inside the particle (condensed phase) and in a
donut ring around it (dilute phase) for every supplied channel, plus
the per-channel condensed/dilute ratio. **No background subtraction,
no normalization channel** — raw intensities over float64 pixels.

``run_one_image_set`` is the public entry point. It performs no file
I/O: the caller (CLI or framework) reads the TIFFs / ``.h5`` layers and
passes pre-loaded numpy arrays. The donut geometry, particle→cell
assignment, area-weighted aggregation, and nan-safe ratio come from
:mod:`percell4.domain.analysis._impl._shared`; ``_whole_cell_stats``
(the single-cell whole-cell statistics) is unique to this analysis.

References:
* Plan: ``docs/plans/2026-05-28-001-feat-incorporate-whole-field-multichannel-analyses-plan.md``
  (unit U3).
* Pattern: ``src/percell4/domain/analysis/_impl/per_particle_donut.py``.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from percell4.domain.analysis._impl._shared import (
    assign_particles_to_cells,
    label_and_filter,
    nan_safe_ratio,
    region_and_donut_masks,
    weighted_mean,
)

# ---------------------------------------------------------------------------
# Per-particle measurement
# ---------------------------------------------------------------------------


def analyze_particles(
    *,
    mask_img: np.ndarray,
    channel_images: dict[str, np.ndarray],
    buffer_px: int,
    donut_px: int,
    min_size: int,
    capture_donut_mask: bool = False,
    log: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], np.ndarray | None]:
    """Measure mean/integ in the condensed and dilute zones for each channel.

    Returns ``(rows, donut_export)`` — one row dict per particle that
    survives the ``min_size`` filter and has a non-empty donut, and the
    uint8 union donut mask (255 in donut, 0 elsewhere) when
    ``capture_donut_mask`` is True (else ``None``). ``channel_images``
    is iterated in its given order; the caller is responsible for
    ordering (the CLI/module pass channels sorted by name).
    """
    h, w = mask_img.shape
    for name, img in channel_images.items():
        if img.shape != (h, w):
            raise ValueError(
                f"Channel '{name}' shape {img.shape} does not match mask "
                f"shape {(h, w)}"
            )

    label_mask, binary_mask, region_ids, props_by_id = label_and_filter(
        mask_img, min_size
    )
    if log is not None:
        log(f"  Found {len(region_ids)} particles > {min_size} px")

    donut_export = (
        np.zeros((h, w), dtype=np.uint8) if capture_donut_mask else None
    )

    results: list[dict[str, Any]] = []
    for region_id in region_ids:
        region_mask, donut_mask = region_and_donut_masks(
            label_mask, binary_mask, region_id, props_by_id,
            buffer_px, donut_px,
        )

        if donut_mask.sum() == 0:
            continue

        if donut_export is not None:
            donut_export[donut_mask] = 255

        row: dict[str, Any] = {
            'particle_id': int(region_id),
            'particle_area_px': int(region_mask.sum()),
            'donut_area_px': int(donut_mask.sum()),
        }

        for ch_name, ch_img in channel_images.items():
            region_vals = ch_img[region_mask]
            donut_vals = ch_img[donut_mask]
            c_mean = (
                float(np.mean(region_vals)) if region_vals.size else np.nan
            )
            d_mean = (
                float(np.mean(donut_vals)) if donut_vals.size else np.nan
            )
            row[f'condensed_{ch_name}_mean'] = c_mean
            row[f'dilute_{ch_name}_mean'] = d_mean
            row[f'condensed_{ch_name}_integ'] = float(np.sum(region_vals))
            row[f'dilute_{ch_name}_integ'] = float(np.sum(donut_vals))
            row[f'{ch_name}_condensed_over_dilute'] = nan_safe_ratio(
                c_mean, d_mean
            )

        results.append(row)

    return results, donut_export


# ---------------------------------------------------------------------------
# Single-cell aggregation (whole-cell stats are unique to this analysis)
# ---------------------------------------------------------------------------


def _whole_cell_stats(values: np.ndarray) -> tuple[float, ...]:
    """Return ``(mean, median, mode, min, max, integ)`` for a 1-D array.

    The mode is the most frequent value after rounding to nearest int
    (``np.rint`` + ``np.bincount``), matching the original CLI exactly.
    """
    if values.size == 0:
        return (np.nan,) * 6
    vals_int = np.rint(values).astype(np.int64)
    vmin = int(vals_int.min())
    counts = np.bincount(vals_int - vmin)
    mode_val = float(int(counts.argmax()) + vmin)
    return (
        float(np.mean(values)),
        float(np.median(values)),
        mode_val,
        float(values.min()),
        float(values.max()),
        float(values.sum()),
    )


def aggregate_by_cell(
    particle_results: list[dict[str, Any]],
    particle_to_cell: dict[int, int],
    cp_mask_img: np.ndarray,
    channel_names: list[str],
    channel_images: dict[str, np.ndarray],
) -> list[dict[str, Any]]:
    """Aggregate per-particle rows into per-cell rows.

    Particle-derived means are area-weighted, integrated intensities are
    summed, ratios are recomputed from the aggregated means. Whole-cell
    intensity stats are computed directly from the pixels inside each
    cell mask. Every non-zero cell id gets a row — cells with no
    particles still get whole-cell stats with NaN particle metrics.
    """
    cell_particles: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in particle_results:
        pid = r['particle_id']
        if pid in particle_to_cell:
            cell_particles[particle_to_cell[pid]].append(r)

    all_cell_ids = np.unique(cp_mask_img)
    all_cell_ids = all_cell_ids[all_cell_ids != 0]

    aggregated: list[dict[str, Any]] = []
    for cell_id in all_cell_ids:
        cell_id = int(cell_id)
        cell_pixels = cp_mask_img == cell_id
        cell_area = int(cell_pixels.sum())
        particles = cell_particles.get(cell_id, [])

        row: dict[str, Any] = {
            'cell_id': cell_id,
            'cell_area_px': cell_area,
            'n_particles': len(particles),
            'total_particle_area_px': 0,
            'total_donut_area_px': 0,
        }

        for ch in channel_names:
            ch_img = channel_images[ch]
            wc_mean, wc_med, wc_mode, wc_min, wc_max, wc_integ = (
                _whole_cell_stats(ch_img[cell_pixels])
            )
            row[f'cell_{ch}_mean'] = wc_mean
            row[f'cell_{ch}_median'] = wc_med
            row[f'cell_{ch}_mode'] = wc_mode
            row[f'cell_{ch}_min'] = wc_min
            row[f'cell_{ch}_max'] = wc_max
            row[f'cell_{ch}_integ'] = wc_integ

        if not particles:
            for ch in channel_names:
                row[f'condensed_{ch}_mean'] = np.nan
                row[f'dilute_{ch}_mean'] = np.nan
                row[f'condensed_{ch}_integ'] = np.nan
                row[f'dilute_{ch}_integ'] = np.nan
                row[f'{ch}_condensed_over_dilute'] = np.nan
            aggregated.append(row)
            continue

        df_p = pd.DataFrame(particles)
        row['total_particle_area_px'] = int(df_p['particle_area_px'].sum())
        row['total_donut_area_px'] = int(df_p['donut_area_px'].sum())

        for ch in channel_names:
            cmean = weighted_mean(
                df_p, f'condensed_{ch}_mean', 'particle_area_px'
            )
            dmean = weighted_mean(df_p, f'dilute_{ch}_mean', 'donut_area_px')
            row[f'condensed_{ch}_mean'] = cmean
            row[f'dilute_{ch}_mean'] = dmean
            row[f'condensed_{ch}_integ'] = float(
                df_p[f'condensed_{ch}_integ'].sum()
            )
            row[f'dilute_{ch}_integ'] = float(df_p[f'dilute_{ch}_integ'].sum())
            row[f'{ch}_condensed_over_dilute'] = nan_safe_ratio(cmean, dmean)

        aggregated.append(row)

    return aggregated


# ---------------------------------------------------------------------------
# Top-level entry: one image set
# ---------------------------------------------------------------------------


def run_one_image_set(
    *,
    mask: np.ndarray,
    channels: dict[str, np.ndarray],
    cp_mask: np.ndarray | None = None,
    buffer: int,
    donut: int,
    min_size: int,
    single_cell: bool,
    export_donuts: bool,
    set_label: str = "",
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the multichannel donut analysis on one image set.

    Pure function: no file I/O. The caller reads the TIFFs (CLI) or
    pulls arrays from the ``.h5`` (framework) and writes CSV / donut
    TIFFs / HDF5 layers.

    Parameters
    ----------
    mask : ndarray
        2D binary particle mask.
    channels : dict[str, ndarray]
        Ordered ``{channel_name: 2D array}``. Channel names become the
        output column infixes (``condensed_<name>_mean`` …). Coerced to
        float64 internally. The caller decides the order (CLI/module
        pass channels sorted by name).
    cp_mask : ndarray | None
        Per-cell label image. Required when ``single_cell=True``. When
        supplied with ``single_cell=False``, each particle row still
        gains a ``cell_id`` column (majority-pixel assignment, unmatched
        particles → 0).
    buffer, donut, min_size, single_cell, export_donuts
        See the CLI's argparse help; semantics preserved verbatim.

    Returns
    -------
    dict
        ``{"particle_rows": list | None, "cell_rows": list | None,
           "donut_mask": ndarray | None}``. Exactly one of
        ``particle_rows`` / ``cell_rows`` is a list (the other is
        ``None``) depending on ``single_cell``. ``donut_mask`` is the
        uint8 union of donut pixels when ``export_donuts`` else ``None``.
    """
    if single_cell and cp_mask is None:
        raise ValueError(
            "run_one_image_set: single_cell=True requires cp_mask"
        )

    channel_images = {
        name: arr.astype(np.float64, copy=False)
        for name, arr in channels.items()
    }
    channel_names = list(channel_images.keys())

    particle_rows, donut_export = analyze_particles(
        mask_img=mask, channel_images=channel_images,
        buffer_px=buffer, donut_px=donut, min_size=min_size,
        capture_donut_mask=export_donuts, log=log,
    )

    particle_to_cell: dict[int, int] | None = None
    if cp_mask is not None:
        particle_to_cell = assign_particles_to_cells(mask, cp_mask, min_size)
        for r in particle_rows:
            r['cell_id'] = particle_to_cell.get(r['particle_id'], 0)

    if single_cell:
        cell_rows = aggregate_by_cell(
            particle_rows, particle_to_cell, cp_mask,
            channel_names, channel_images,
        )
        if log is not None:
            log(f"  Single-cell: {len(cell_rows)} cells aggregated")
        return {
            "particle_rows": None,
            "cell_rows": cell_rows,
            "donut_mask": donut_export,
        }

    return {
        "particle_rows": particle_rows,
        "cell_rows": None,
        "donut_mask": donut_export,
    }
