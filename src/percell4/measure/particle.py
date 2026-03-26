"""Per-cell particle analysis using connected components.

Counts and measures particles (connected components from a threshold mask)
within each cell boundary. Returns a DataFrame with one row per cell and
11 summary metrics. Adapted from PerCell3 measure/particle_analyzer.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.ndimage import find_objects
from scipy.ndimage import label as ndlabel
from skimage.measure import regionprops


def analyze_particles(
    image: NDArray,
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    min_area: int = 1,
) -> pd.DataFrame:
    """Analyze particles within each cell.

    For each cell, finds connected components where the threshold mask
    overlaps the cell boundary, then computes per-cell summary metrics.

    Parameters
    ----------
    image : (H, W) intensity image
    labels : (H, W) int32 cell label array (0 = background)
    mask : (H, W) uint8 binary threshold mask (0/1)
    min_area : minimum particle area in pixels (filter smaller particles)

    Returns
    -------
    DataFrame with one row per cell. Columns:
        label, particle_count, total_particle_area, mean_particle_area,
        max_particle_area, total_particle_area_pixels,
        particle_coverage_fraction, mean_particle_mean_intensity,
        mean_particle_integrated_intensity, total_particle_integrated_intensity,
        max_particle_area_pixels
    """
    if labels.max() == 0:
        return _empty_result()

    slices = find_objects(labels)
    mask_bool = mask > 0
    rows: list[dict] = []

    for label_val in range(1, labels.max() + 1):
        sl = slices[label_val - 1]
        if sl is None:
            continue

        # Crop to bounding box
        label_crop = labels[sl]
        image_crop = image[sl]
        mask_crop = mask_bool[sl]
        cell_mask = label_crop == label_val

        # Particles within this cell
        particle_mask = cell_mask & mask_crop

        cell_area = float(np.sum(cell_mask))
        if cell_area == 0:
            continue

        # Label connected components within the cell
        particle_labels, n_components = ndlabel(particle_mask)

        if n_components == 0:
            rows.append(_zero_row(label_val, cell_area))
            continue

        # Measure each particle
        props = regionprops(particle_labels, intensity_image=image_crop)

        areas: list[float] = []
        mean_intensities: list[float] = []
        integrated_intensities: list[float] = []

        for prop in props:
            if prop.area < min_area:
                continue
            areas.append(float(prop.area))
            mean_intensities.append(float(prop.intensity_mean))
            integrated_intensities.append(float(prop.intensity_mean * prop.area))

        n_particles = len(areas)

        if n_particles == 0:
            rows.append(_zero_row(label_val, cell_area))
            continue

        total_area = sum(areas)
        rows.append({
            "label": int(label_val),
            "particle_count": n_particles,
            "total_particle_area": total_area,
            "mean_particle_area": total_area / n_particles,
            "max_particle_area": max(areas),
            "total_particle_area_pixels": total_area,
            "max_particle_area_pixels": max(areas),
            "particle_coverage_fraction": total_area / cell_area,
            "mean_particle_mean_intensity": (
                sum(mean_intensities) / n_particles
            ),
            "mean_particle_integrated_intensity": (
                sum(integrated_intensities) / n_particles
            ),
            "total_particle_integrated_intensity": sum(integrated_intensities),
        })

    if not rows:
        return _empty_result()

    df = pd.DataFrame(rows)
    df["label"] = df["label"].astype(np.int32)
    return df


def _zero_row(label_val: int, cell_area: float) -> dict:
    """Create a row with zero particle metrics for a cell with no particles."""
    return {
        "label": int(label_val),
        "particle_count": 0,
        "total_particle_area": 0.0,
        "mean_particle_area": 0.0,
        "max_particle_area": 0.0,
        "total_particle_area_pixels": 0.0,
        "max_particle_area_pixels": 0.0,
        "particle_coverage_fraction": 0.0,
        "mean_particle_mean_intensity": 0.0,
        "mean_particle_integrated_intensity": 0.0,
        "total_particle_integrated_intensity": 0.0,
    }


def _empty_result() -> pd.DataFrame:
    """Return an empty DataFrame with the correct columns."""
    cols = [
        "label", "particle_count", "total_particle_area", "mean_particle_area",
        "max_particle_area", "total_particle_area_pixels",
        "max_particle_area_pixels", "particle_coverage_fraction",
        "mean_particle_mean_intensity", "mean_particle_integrated_intensity",
        "total_particle_integrated_intensity",
    ]
    return pd.DataFrame(columns=cols)
