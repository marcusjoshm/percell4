"""Per-cell particle analysis using connected components.

Counts and measures particles (connected components from a threshold mask)
within each cell boundary. Supports multi-channel intensity measurement.

Functions:
    analyze_particles() — per-cell summary (one row per cell)
    analyze_particles_detail() — per-particle detail (one row per particle)

Both accept multi-channel images as dict[str, NDArray] and share the
internal _iter_particles() iterator to avoid redundant computation.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.ndimage import find_objects
from scipy.ndimage import label as ndlabel
from skimage.measure import regionprops

from percell4.domain.measure.metrics import BUILTIN_METRICS

# Per-particle intensity metrics — the BUILTIN_METRICS set minus
# ``area`` (the particle's own area is already a first-class field
# on _ParticleRecord). For each channel × metric we compute the
# stat over the particle's pixel set, then aggregate across particles
# per cell with the natural reducer documented in
# ``_PARTICLE_AGGREGATORS`` below.
_PARTICLE_INTENSITY_METRICS = tuple(
    m for m in BUILTIN_METRICS.keys() if m != "area"
)


def _agg_mean(values: list[float]) -> float:
    return float(np.nanmean(values)) if values else 0.0


def _agg_sum(values: list[float]) -> float:
    return float(np.nansum(values)) if values else 0.0


def _agg_min(values: list[float]) -> float:
    return float(np.nanmin(values)) if values else 0.0


def _agg_max(values: list[float]) -> float:
    return float(np.nanmax(values)) if values else 0.0


# Per-metric aggregator when rolling per-particle values up to per-cell.
# Chosen for biological intuition:
#   - min/max: the dimmest/brightest pixel across any particle in this cell
#   - integrated: total signal across all particles
#   - mean / median / std / mode / sg_ratio: typical (mean across particles)
_PARTICLE_AGGREGATORS = {
    "mean_intensity": _agg_mean,
    "max_intensity": _agg_max,
    "min_intensity": _agg_min,
    "integrated_intensity": _agg_sum,
    "std_intensity": _agg_mean,
    "median_intensity": _agg_mean,
    "mode_intensity": _agg_mean,
    "sg_ratio": _agg_mean,
}


@dataclass
class _ParticleRecord:
    """Single particle measurement from one cell.

    ``metric_values`` holds per-channel intensity stats for the particle.
    Shape: ``{channel_name: {metric_name: value, ...}, ...}``.
    The metrics computed for each channel are listed in
    :data:`_PARTICLE_INTENSITY_METRICS`.
    """

    cell_id: int
    particle_id: int
    area: float
    centroid_y: float
    centroid_x: float
    metric_values: dict[str, dict[str, float]] = field(default_factory=dict)


def _iter_particles(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    min_area: int = 1,
) -> Iterator[_ParticleRecord]:
    """Yield per-particle records across all channels.

    For each cell, runs connected-component labeling on the cell × mask
    intersection, then computes the full :data:`_PARTICLE_INTENSITY_METRICS`
    set on each particle's pixel set per channel.
    """
    if labels.max() == 0:
        return

    slices = find_objects(labels)
    mask_bool = mask > 0
    channel_names = list(images.keys())

    for label_val in range(1, labels.max() + 1):
        sl = slices[label_val - 1]
        if sl is None:
            continue

        label_crop = labels[sl]
        cell_mask = label_crop == label_val
        mask_crop = mask_bool[sl]
        particle_mask = cell_mask & mask_crop

        cell_area = float(np.sum(cell_mask))
        if cell_area == 0:
            continue

        particle_labels, n_components = ndlabel(particle_mask)
        if n_components == 0:
            continue

        # regionprops once to get particle areas + centroids cheaply.
        first_channel = channel_names[0]
        first_props = regionprops(
            particle_labels, intensity_image=images[first_channel][sl]
        )

        # Pre-crop each channel image to this cell's bbox so per-particle
        # metric calls don't re-slice the full image every time.
        channel_crops = {ch: images[ch][sl] for ch in channel_names}

        for pid, prop in enumerate(first_props, start=1):
            if prop.area < min_area:
                continue
            cy, cx = prop.centroid

            # Build a boolean mask of just this particle's pixels.
            this_particle = particle_labels == pid

            # Compute the full BUILTIN_METRICS set per channel.
            metric_values: dict[str, dict[str, float]] = {}
            for ch_name in channel_names:
                img_crop = channel_crops[ch_name]
                ch_metrics: dict[str, float] = {}
                for metric_name in _PARTICLE_INTENSITY_METRICS:
                    fn = BUILTIN_METRICS[metric_name]
                    try:
                        ch_metrics[metric_name] = float(
                            fn(img_crop, this_particle)
                        )
                    except Exception:
                        # Particularly mode / sg_ratio can fail on
                        # degenerate inputs; default to 0 so the per-cell
                        # aggregation stays well-defined.
                        ch_metrics[metric_name] = 0.0
                metric_values[ch_name] = ch_metrics

            yield _ParticleRecord(
                cell_id=int(label_val),
                particle_id=pid,
                area=float(prop.area),
                centroid_y=float(sl[0].start + cy),
                centroid_x=float(sl[1].start + cx),
                metric_values=metric_values,
            )


def analyze_particles(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    min_area: int = 1,
) -> pd.DataFrame:
    """Analyze particles within each cell (multi-channel).

    Parameters
    ----------
    images : dict mapping channel name to (H, W) intensity array
    labels : (H, W) int32 cell label array (0 = background)
    mask : (H, W) uint8 binary threshold mask (0/1)
    min_area : minimum particle area in pixels

    Returns
    -------
    DataFrame with one row per cell. Columns:
        label, particle_count, total_particle_area, mean_particle_area,
        max_particle_area, particle_coverage_fraction,
        {channel}_particle_mean, {channel}_particle_integrated_total
    """
    if labels.max() == 0:
        return _empty_summary(list(images.keys()))

    # Collect particles per cell for aggregation
    slices = find_objects(labels)
    cell_areas: dict[int, float] = {}
    cell_particles: dict[int, list[_ParticleRecord]] = {}
    channel_names = list(images.keys())

    # Pre-compute cell areas for all cells (including those with 0 particles)
    for label_val in range(1, labels.max() + 1):
        sl = slices[label_val - 1]
        if sl is None:
            continue
        cell_mask = labels[sl] == label_val
        area = float(np.sum(cell_mask))
        if area > 0:
            cell_areas[label_val] = area
            cell_particles[label_val] = []

    for rec in _iter_particles(images, labels, mask, min_area):
        cell_particles[rec.cell_id].append(rec)

    rows: list[dict] = []
    for label_val in sorted(cell_areas.keys()):
        particles = cell_particles[label_val]
        cell_area = cell_areas[label_val]

        if not particles:
            row = _zero_summary_row(label_val, cell_area, channel_names)
        else:
            n = len(particles)
            areas = [p.area for p in particles]
            total_area = sum(areas)
            row: dict = {
                "label": int(label_val),
                "particle_count": n,
                "total_particle_area": total_area,
                "mean_particle_area": total_area / n,
                "max_particle_area": max(areas),
                "particle_coverage_fraction": total_area / cell_area,
            }
            # Per-channel intensity aggregates. For each base metric M,
            # produce one column `<channel>_particle_<M>` using M's
            # natural aggregator (see _PARTICLE_AGGREGATORS).
            for ch in channel_names:
                prefix = f"{ch}_" if len(channel_names) > 1 else ""
                for metric_name in _PARTICLE_INTENSITY_METRICS:
                    values = [p.metric_values[ch][metric_name] for p in particles]
                    aggregator = _PARTICLE_AGGREGATORS[metric_name]
                    row[f"{prefix}particle_{metric_name}"] = aggregator(values)
        rows.append(row)

    if not rows:
        return _empty_summary(channel_names)

    df = pd.DataFrame(rows)
    df["label"] = df["label"].astype(np.int32)
    return df


def analyze_particles_detail(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    min_area: int = 1,
) -> pd.DataFrame:
    """Per-particle detail rows for CSV export.

    Parameters
    ----------
    images : dict mapping channel name to (H, W) intensity array
    labels : (H, W) int32 cell label array (0 = background)
    mask : (H, W) uint8 binary threshold mask (0/1)
    min_area : minimum particle area in pixels

    Returns
    -------
    DataFrame with one row per particle. Columns:
        cell_id, particle_id, area, centroid_y, centroid_x,
        plus one ``{channel}_<metric>`` column per channel × metric in
        :data:`_PARTICLE_INTENSITY_METRICS` (mean_intensity,
        max_intensity, min_intensity, integrated_intensity, std_intensity,
        median_intensity, mode_intensity, sg_ratio).
    """
    channel_names = list(images.keys())
    rows: list[dict] = []

    for rec in _iter_particles(images, labels, mask, min_area):
        row: dict = {
            "cell_id": rec.cell_id,
            "particle_id": rec.particle_id,
            "area": rec.area,
            "centroid_y": rec.centroid_y,
            "centroid_x": rec.centroid_x,
        }
        for ch in channel_names:
            prefix = f"{ch}_" if len(channel_names) > 1 else ""
            for metric_name in _PARTICLE_INTENSITY_METRICS:
                row[f"{prefix}{metric_name}"] = rec.metric_values[ch][metric_name]
        rows.append(row)

    if not rows:
        cols = ["cell_id", "particle_id", "area", "centroid_y", "centroid_x"]
        for ch in channel_names:
            prefix = f"{ch}_" if len(channel_names) > 1 else ""
            for metric_name in _PARTICLE_INTENSITY_METRICS:
                cols.append(f"{prefix}{metric_name}")
        return pd.DataFrame(columns=cols)

    return pd.DataFrame(rows)


def _zero_summary_row(
    label_val: int, cell_area: float, channel_names: list[str]
) -> dict:
    """Row for a cell with no particles."""
    row: dict = {
        "label": int(label_val),
        "particle_count": 0,
        "total_particle_area": 0.0,
        "mean_particle_area": 0.0,
        "max_particle_area": 0.0,
        "particle_coverage_fraction": 0.0,
    }
    for ch in channel_names:
        prefix = f"{ch}_" if len(channel_names) > 1 else ""
        for metric_name in _PARTICLE_INTENSITY_METRICS:
            row[f"{prefix}particle_{metric_name}"] = 0.0
    return row


def _empty_summary(channel_names: list[str]) -> pd.DataFrame:
    """Return an empty summary DataFrame with correct columns."""
    cols = [
        "label", "particle_count", "total_particle_area", "mean_particle_area",
        "max_particle_area", "particle_coverage_fraction",
    ]
    for ch in channel_names:
        prefix = f"{ch}_" if len(channel_names) > 1 else ""
        for metric_name in _PARTICLE_INTENSITY_METRICS:
            cols.append(f"{prefix}particle_{metric_name}")
    return pd.DataFrame(columns=cols)
