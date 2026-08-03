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
from scipy.ndimage import find_objects
from scipy.ndimage import label as ndlabel
from skimage.measure import regionprops

from percell4.domain.analysis._impl._shared import (
    donut_for_region_mask,
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


def analyze_particles_per_cell(
    *,
    mask_img: np.ndarray,
    cp_mask_img: np.ndarray,
    channel_images: dict[str, np.ndarray],
    buffer_px: int,
    donut_px: int,
    min_size: int,
    capture_donut_mask: bool = False,
    log: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], np.ndarray | None]:
    """Per-cell particle measurement matching the single-cell thresholding table.

    A *particle* here is a connected component of ``(cp_mask == cell) & mask``
    — i.e. the particle mask **clipped to each cell**, labeled independently
    inside every cell. This reproduces the particle identity and area of
    :func:`percell4.domain.measure.particle.analyze_particles_detail` exactly,
    so the two analyses share ``cell_id`` / ``particle_id`` / area as a join
    key:

    * labeling uses ``scipy.ndimage.label`` (4-connectivity), **not**
      skimage's 8-connected ``measure.label``;
    * ``particle_id`` is a per-cell sequential index (``1..n`` within each
      cell, in raster order, assigned **before** the size filter — so a
      surviving particle keeps the index it would have had with the dropped
      ones present);
    * ``particle_area_px`` is the cell-clipped component area;
    * the size filter keeps components with ``area >= min_size`` (matching
      single-cell's ``prop.area < min_area → skip``), unlike the whole-image
      path's strict ``area > min_size``.

    The donut ring is then measured around each **clipped** particle (so a
    particle touching a cell edge has its dilute ring computed from the
    clipped shape). Unlike the whole-image path, a particle whose donut is
    empty is **still emitted** (with ``donut_area_px == 0`` and NaN dilute
    metrics) so the row set matches single-cell one-for-one. Particles
    outside every cell are absent, exactly as in single-cell.

    Returns ``(rows, donut_export)`` — one row dict per particle and the
    uint8 union donut mask when ``capture_donut_mask`` is True (else None).
    """
    h, w = mask_img.shape
    for name, img in channel_images.items():
        if img.shape != (h, w):
            raise ValueError(
                f"Channel '{name}' shape {img.shape} does not match mask "
                f"shape {(h, w)}"
            )

    binary_mask = mask_img > 0
    labels = cp_mask_img
    donut_export = (
        np.zeros((h, w), dtype=np.uint8) if capture_donut_mask else None
    )

    results: list[dict[str, Any]] = []
    max_label = int(labels.max())
    if max_label == 0:
        if log is not None:
            log("  No cells in cp_mask — 0 particles")
        return results, donut_export

    slices = find_objects(labels)
    n_particles = 0
    for cell_val in range(1, max_label + 1):
        sl = slices[cell_val - 1]
        if sl is None:
            continue
        label_crop = labels[sl]
        cell_mask = label_crop == cell_val
        if not cell_mask.any():
            continue
        particle_mask = cell_mask & binary_mask[sl]
        particle_labels, n_components = ndlabel(particle_mask)
        if n_components == 0:
            continue

        props = regionprops(particle_labels)
        row0, col0 = sl[0].start, sl[1].start
        for pid, prop in enumerate(props, start=1):
            # pid assigned before the size filter so a surviving particle
            # keeps the same index it would have had with dropped ones
            # present — identical to single-cell's enumerate-then-skip.
            if prop.area < min_size:
                continue

            region_mask = np.zeros((h, w), dtype=bool)
            region_view = region_mask[sl]
            region_view[particle_labels == prop.label] = True

            full_bbox = (
                row0 + prop.bbox[0],
                col0 + prop.bbox[1],
                row0 + prop.bbox[2],
                col0 + prop.bbox[3],
            )
            donut_mask = donut_for_region_mask(
                region_mask, binary_mask, full_bbox, buffer_px, donut_px
            )
            if donut_export is not None:
                donut_export[donut_mask] = 255

            row: dict[str, Any] = {
                'particle_id': int(pid),
                'cell_id': int(cell_val),
                'particle_area_px': int(prop.area),
                'donut_area_px': int(donut_mask.sum()),
            }
            for ch_name, ch_img in channel_images.items():
                region_vals = ch_img[region_mask]
                donut_vals = ch_img[donut_mask]
                c_mean = (
                    float(np.mean(region_vals))
                    if region_vals.size else np.nan
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
            n_particles += 1

    if log is not None:
        log(f"  Found {n_particles} particles >= {min_size} px (per-cell)")
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

    Rows are grouped by the ``cell_id`` each particle already carries
    (set by :func:`analyze_particles_per_cell`), so particle identity and
    cell assignment stay consistent with the per-particle table.
    """
    cell_particles: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in particle_results:
        cell_particles[int(r['cell_id'])].append(r)

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
    cell_mean_channels: list[str] | None = None,
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
    cell_mean_channels : list[str] | None
        Channel names (keys of ``channels``) for which each particle row
        gains a ``cell_<name>_mean`` column — the whole-cell mean of that
        channel over the cell the particle is assigned to. No-op unless
        ``cp_mask`` is supplied. A particle with ``cell_id == 0``
        (unassigned) gets ``NaN``. The mean uses the same definition as
        the single-cell ``cell_<name>_mean`` (``float(np.mean(...))`` over
        the float64-coerced channel), so values match across both tables.
        Names not present in ``channels`` are silently skipped.

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

    # With a cp_mask, particles are labeled per-cell (clipped to each cell)
    # so cell_id / particle_id / area match the single-cell thresholding
    # table exactly. Without one there are no cells to clip to, so the
    # legacy whole-image labeling stands (no cell_id column).
    if cp_mask is not None:
        particle_rows, donut_export = analyze_particles_per_cell(
            mask_img=mask, cp_mask_img=cp_mask,
            channel_images=channel_images,
            buffer_px=buffer, donut_px=donut, min_size=min_size,
            capture_donut_mask=export_donuts, log=log,
        )
    else:
        particle_rows, donut_export = analyze_particles(
            mask_img=mask, channel_images=channel_images,
            buffer_px=buffer, donut_px=donut, min_size=min_size,
            capture_donut_mask=export_donuts, log=log,
        )

    # Per-particle whole-cell means for the requested channels. Joined by
    # cell_id; uses the float64-coerced channel_images and the same mean
    # definition as aggregate_by_cell so particle_table and cell_table
    # agree. Only meaningful when a cp_mask was supplied. Shows up only in
    # the per-particle output (in single_cell mode particle_rows are
    # consumed by aggregate_by_cell, which ignores these extra keys).
    if cp_mask is not None and cell_mean_channels:
        requested = [ch for ch in cell_mean_channels if ch in channel_images]
        cell_mean_cache: dict[tuple[str, int], float] = {}
        for r in particle_rows:
            cid = int(r['cell_id'])
            for ch in requested:
                key = (ch, cid)
                if key not in cell_mean_cache:
                    if cid == 0:
                        cell_mean_cache[key] = np.nan
                    else:
                        pix = channel_images[ch][cp_mask == cid]
                        cell_mean_cache[key] = (
                            float(np.mean(pix)) if pix.size else np.nan
                        )
                r[f'cell_{ch}_mean'] = cell_mean_cache[key]

    if single_cell:
        cell_rows = aggregate_by_cell(
            particle_rows, cp_mask, channel_names, channel_images,
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
