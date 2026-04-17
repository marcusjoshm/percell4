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

from dataclasses import dataclass, field
from typing import Iterator

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.ndimage import find_objects
from scipy.ndimage import label as ndlabel
from skimage.measure import regionprops


@dataclass
class _ParticleRecord:
    """Single particle measurement from one cell."""

    cell_id: int
    particle_id: int
    area: float
    centroid_y: float
    centroid_x: float
    intensities: dict[str, float] = field(default_factory=dict)
    integrated: dict[str, float] = field(default_factory=dict)


def _iter_particles(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    min_area: int = 1,
) -> Iterator[_ParticleRecord]:
    """Yield per-particle records across all channels.

    Single find_objects call shared across channels. For each cell, runs
    connected-component labeling once, then measures intensity per channel.
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

        # Run regionprops per channel (sharing the same particle labels)
        props_by_channel: dict[str, list] = {}
        for ch_name in channel_names:
            props_by_channel[ch_name] = regionprops(
                particle_labels, intensity_image=images[ch_name][sl]
            )

        # Iterate particles using first channel for geometry
        first_props = props_by_channel[channel_names[0]]
        for pid, prop in enumerate(first_props, start=1):
            if prop.area < min_area:
                continue
            cy, cx = prop.centroid
            yield _ParticleRecord(
                cell_id=int(label_val),
                particle_id=pid,
                area=float(prop.area),
                centroid_y=float(sl[0].start + cy),
                centroid_x=float(sl[1].start + cx),
                intensities={
                    ch: float(props_by_channel[ch][pid - 1].intensity_mean)
                    for ch in channel_names
                },
                integrated={
                    ch: float(
                        props_by_channel[ch][pid - 1].intensity_mean * prop.area
                    )
                    for ch in channel_names
                },
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
            # Per-channel intensity aggregates
            for ch in channel_names:
                means = [p.intensities[ch] for p in particles]
                integ = [p.integrated[ch] for p in particles]
                prefix = f"{ch}_" if len(channel_names) > 1 else ""
                row[f"{prefix}particle_mean"] = sum(means) / n
                row[f"{prefix}particle_integrated_total"] = sum(integ)
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
        {channel}_mean_intensity, {channel}_integrated_intensity
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
            row[f"{prefix}mean_intensity"] = rec.intensities[ch]
            row[f"{prefix}integrated_intensity"] = rec.integrated[ch]
        rows.append(row)

    if not rows:
        cols = ["cell_id", "particle_id", "area", "centroid_y", "centroid_x"]
        for ch in channel_names:
            prefix = f"{ch}_" if len(channel_names) > 1 else ""
            cols.extend([f"{prefix}mean_intensity", f"{prefix}integrated_intensity"])
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
        row[f"{prefix}particle_mean"] = 0.0
        row[f"{prefix}particle_integrated_total"] = 0.0
    return row


def _empty_summary(channel_names: list[str]) -> pd.DataFrame:
    """Return an empty summary DataFrame with correct columns."""
    cols = [
        "label", "particle_count", "total_particle_area", "mean_particle_area",
        "max_particle_area", "particle_coverage_fraction",
    ]
    for ch in channel_names:
        prefix = f"{ch}_" if len(channel_names) > 1 else ""
        cols.extend([f"{prefix}particle_mean", f"{prefix}particle_integrated_total"])
    return pd.DataFrame(columns=cols)
