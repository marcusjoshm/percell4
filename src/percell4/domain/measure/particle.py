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
from scipy.ndimage import (
    binary_dilation,
    binary_erosion,
    find_objects,
    gaussian_filter,
    laplace,
    sobel,
)
from scipy.ndimage import label as ndlabel
from skimage.measure import regionprops

from percell4.domain.measure.metrics import BUILTIN_METRICS

# ── Per-particle focus/sharpness metrics ────────────────────────────────
# A dedicated metric family, deliberately kept OUT of BUILTIN_METRICS so it
# never leaks into per-cell measurement, threshold-metric validation, or any
# BUILTIN_METRICS-driven GUI picker (grouping / threshold-config / per-cell CSV
# selectors). Computed inside _iter_particles (which alone has the cell mask
# needed to keep the edge-skirt annulus inside the owning cell) and emitted by
# analyze_particles_detail. The interactive Segment-by-Metric tool
# (metric_segmentation.py) deliberately exposes `edge_skirt_ratio` in its OWN,
# separate picker — that is consistent with this invariant because it reads
# through the dedicated per-cell emitter, not through BUILTIN_METRICS. See
# docs/plans/2026-07-14-001-feat-particle-sharpness-metrics-plan.md and
# docs/plans/2026-07-14-002-feat-interactive-metric-segmenter-plan.md.
_PARTICLE_SHARPNESS_METRICS = (
    "edge_skirt_ratio",   # raw skirt mean / peak — high = haze past the edge (out of focus)
    "boundary_gradient",  # mean edge gradient / peak — high = steep edge (in focus)
    "laplacian_variance", # var(Laplacian) / mean^2 — high = sharp high-freq content (in focus)
    "tenengrad",          # mean(Sobel grad^2) / mean^2 — high = strong edges (in focus)
)
# Per-particle geometric-validity flag column (channel-agnostic). True when the
# particle geometry permits the skirt/derivative measurement at all; lets the
# researcher count particles whose metrics are NaN for degeneracy rather than
# having those rows silently vanish from eye-gated distributions.
#
# IMPORTANT: this flag is GEOMETRY ONLY. It says nothing about per-channel
# signal. A particle that is geometrically valid (True) but has no signal in a
# given channel (peak <= 0 there — the normal case when the mask was detected on
# a different channel) still yields NaN for that channel's metrics. So
# `sharpness_computable == True` does NOT imply "no NaN in this row": a
# downstream filter that needs finite values for channel X must AND this flag
# with that channel's own `notna()`. For the detection channel (which has signal
# by construction on a detected particle), the flag and finite metrics coincide.
_SHARPNESS_COMPUTABLE_COL = "sharpness_computable"

# Buffer + geometry knobs (tunable; validated by eye — see plan "Deferred").
# The derivative operators run on a LIGHTLY presmoothed crop, deliberately
# lighter than the detection pipeline's sigma=1px so pixel noise is tamed
# without erasing the edge-sharpness signal the metrics rely on.
_SHARPNESS_PRESMOOTH_SIGMA = 0.5
# 2px annulus: a slightly more robust skirt mean than 1px, and — because the
# connected-component labels are 4-connected — the only width at which a
# *separate* neighbouring particle can actually land in the skirt (at 1px, any
# orthogonal neighbour would be part of the same component), so the neighbour
# exclusion below is genuinely active.
_SKIRT_DILATION_PX = 2

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
    # Per-channel focus/sharpness metrics ({channel: {metric: value}}), only
    # populated when _iter_particles is called with compute_sharpness=True.
    sharpness_values: dict[str, dict[str, float]] = field(default_factory=dict)
    # Geometric-validity flag (channel-agnostic); True unless the particle is
    # too small / too enclosed for the skirt + derivative measurement to exist.
    sharpness_computable: bool = True


def _sharpness_for_particle(
    raw_crop: NDArray,
    grad: NDArray,
    lap: NDArray,
    this_particle: NDArray[np.bool_],
    skirt: NDArray[np.bool_],
    boundary: NDArray[np.bool_],
) -> dict[str, float]:
    """Compute the three focus/sharpness metrics for one particle × channel.

    ``raw_crop`` is the un-presmoothed cell crop; ``grad``/``lap`` are the
    Sobel-magnitude and Laplacian of the *lightly presmoothed* crop, computed
    once per (cell, channel) by the caller. ``skirt`` is the cell-restricted,
    neighbour-excluded annulus just outside the particle; ``boundary`` is the
    particle's inner rim.

    NaN is returned per metric only when that specific metric is genuinely
    uncomputable (empty skirt, non-positive peak, <2px area). A dim / hazy /
    low-contrast particle is NOT uncomputable — it yields real finite values
    (high skirt ratio, low gradient, low Laplacian variance) that place it in
    the out-of-focus region, so it is never silently dropped.
    """
    peak = float(raw_crop[this_particle].max()) if this_particle.any() else 0.0
    mean = float(raw_crop[this_particle].mean()) if this_particle.any() else 0.0

    # Edge-skirt ratio (raw pixels): mean intensity just outside ÷ peak.
    if skirt.any() and peak > 0.0:
        edge_skirt_ratio = float(raw_crop[skirt].mean()) / peak
    else:
        edge_skirt_ratio = float("nan")

    # Boundary gradient: mean edge gradient ÷ peak (NOT ÷ (peak-background) —
    # that collapses/inverts on the low-contrast target population and would
    # re-import the CNR contrast confound this axis exists to avoid).
    if boundary.any() and peak > 0.0:
        boundary_gradient = float(grad[boundary].mean()) / peak
    else:
        boundary_gradient = float("nan")

    # Laplacian variance, normalised by mean^2 for intensity-scale invariance.
    if int(this_particle.sum()) >= 2 and mean > 0.0:
        laplacian_variance = float(lap[this_particle].var()) / (mean * mean)
    else:
        laplacian_variance = float("nan")

    # Tenengrad: mean squared Sobel gradient over the particle (``grad`` is the
    # Sobel magnitude, so grad^2 is the Tenengrad response). Mean (not sum) makes
    # it size-independent; ÷ mean^2 makes it intensity-scale invariant like the
    # Laplacian variance. High = strong in-particle edges = in focus.
    if this_particle.any() and mean > 0.0:
        tenengrad = float((grad[this_particle] ** 2).mean()) / (mean * mean)
    else:
        tenengrad = float("nan")

    return {
        "edge_skirt_ratio": edge_skirt_ratio,
        "boundary_gradient": boundary_gradient,
        "laplacian_variance": laplacian_variance,
        "tenengrad": tenengrad,
    }


def sharpness_buffers(crop: NDArray) -> tuple[NDArray, NDArray]:
    """Sobel-magnitude and Laplacian of a lightly-presmoothed cell crop.

    Computed once per (cell, channel); shared by :func:`_iter_particles` and
    the segment-by-metric emitter so both derive identical sharpness values.
    """
    work = gaussian_filter(crop.astype(np.float64), sigma=_SHARPNESS_PRESMOOTH_SIGMA)
    gy = sobel(work, axis=0)
    gx = sobel(work, axis=1)
    return np.hypot(gy, gx), laplace(work)


def skirt_and_boundary(
    this_particle: NDArray[np.bool_],
    cell_mask: NDArray[np.bool_],
    other_particles: NDArray[np.bool_],
) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    """The cell-restricted, neighbour-excluded skirt annulus and the inner rim.

    Single source of truth for the edge-skirt geometry, shared by
    :func:`_iter_particles` and the segment-by-metric emitter.
    """
    dilated = binary_dilation(this_particle, iterations=_SKIRT_DILATION_PX)
    skirt = dilated & ~this_particle & cell_mask & ~other_particles
    boundary = this_particle & ~binary_erosion(this_particle)
    return skirt, boundary


def _iter_particles(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    mask: NDArray[np.uint8],
    min_area: int = 1,
    compute_sharpness: bool = False,
) -> Iterator[_ParticleRecord]:
    """Yield per-particle records across all channels.

    For each cell, runs connected-component labeling on the cell × mask
    intersection, then computes the full :data:`_PARTICLE_INTENSITY_METRICS`
    set on each particle's pixel set per channel.

    When ``compute_sharpness`` is True, also computes the
    :data:`_PARTICLE_SHARPNESS_METRICS` focus metrics per channel (the
    Sobel/Laplacian buffers are built once per cell, not per particle). The
    per-cell summary path (:func:`analyze_particles`) leaves it False so it
    pays no derivative-filter cost; only :func:`analyze_particles_detail`
    enables it.
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

        # Sharpness buffers: presmooth + Sobel-magnitude + Laplacian, computed
        # ONCE per (cell, channel) on the raw crop and indexed per particle
        # below — avoids O(particles) re-convolution of the same crop.
        grad_crops: dict[str, NDArray] = {}
        lap_crops: dict[str, NDArray] = {}
        if compute_sharpness:
            for ch_name in channel_names:
                grad_crops[ch_name], lap_crops[ch_name] = sharpness_buffers(
                    channel_crops[ch_name]
                )

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

            sharpness_values: dict[str, dict[str, float]] = {}
            sharpness_computable = True
            if compute_sharpness:
                # Skirt = thin annulus just outside the particle, restricted to
                # the owning cell and excluding other particles (so haze from a
                # neighbour never leaks into this particle's skirt).
                other_particles = (particle_labels > 0) & ~this_particle
                skirt, boundary = skirt_and_boundary(
                    this_particle, cell_mask, other_particles
                )
                # Geometric-validity flag (channel-agnostic): the particle must
                # have >=2px and a non-empty skirt for the measurement to exist.
                sharpness_computable = bool(
                    int(this_particle.sum()) >= 2 and skirt.any()
                )
                for ch_name in channel_names:
                    sharpness_values[ch_name] = _sharpness_for_particle(
                        channel_crops[ch_name],
                        grad_crops[ch_name],
                        lap_crops[ch_name],
                        this_particle,
                        skirt,
                        boundary,
                    )

            yield _ParticleRecord(
                cell_id=int(label_val),
                particle_id=pid,
                area=float(prop.area),
                centroid_y=float(sl[0].start + cy),
                centroid_x=float(sl[1].start + cx),
                metric_values=metric_values,
                sharpness_values=sharpness_values,
                sharpness_computable=sharpness_computable,
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


def _detail_columns(channel_names: list[str]) -> list[str]:
    """Ordered column schema for the per-particle detail frame.

    Single source of truth for :func:`analyze_particles_detail`: base fields,
    then per-channel intensity metrics, then per-channel sharpness metrics, then
    the channel-agnostic validity flag (appended last). Both the populated-row
    and empty-frame paths build from this so the schema cannot drift.
    """
    cols = ["cell_id", "particle_id", "area", "centroid_y", "centroid_x"]
    for ch in channel_names:
        prefix = f"{ch}_" if len(channel_names) > 1 else ""
        cols.extend(f"{prefix}{m}" for m in _PARTICLE_INTENSITY_METRICS)
    for ch in channel_names:
        prefix = f"{ch}_" if len(channel_names) > 1 else ""
        cols.extend(f"{prefix}{m}" for m in _PARTICLE_SHARPNESS_METRICS)
    cols.append(_SHARPNESS_COMPUTABLE_COL)
    return cols


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
        one ``{channel}_<metric>`` column per channel × metric in
        :data:`_PARTICLE_INTENSITY_METRICS` (mean_intensity,
        max_intensity, min_intensity, integrated_intensity, std_intensity,
        median_intensity, mode_intensity, sg_ratio),
        then one ``{channel}_<metric>`` column per channel × metric in
        :data:`_PARTICLE_SHARPNESS_METRICS` (edge_skirt_ratio,
        boundary_gradient, laplacian_variance, tenengrad), and finally the
        channel-agnostic :data:`_SHARPNESS_COMPUTABLE_COL` flag. The sharpness
        columns are appended last so existing column order is unchanged.
    """
    channel_names = list(images.keys())
    cols = _detail_columns(channel_names)
    rows: list[dict] = []

    for rec in _iter_particles(
        images, labels, mask, min_area, compute_sharpness=True
    ):
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
        for ch in channel_names:
            prefix = f"{ch}_" if len(channel_names) > 1 else ""
            for metric_name in _PARTICLE_SHARPNESS_METRICS:
                row[f"{prefix}{metric_name}"] = rec.sharpness_values[ch][metric_name]
        row[_SHARPNESS_COMPUTABLE_COL] = rec.sharpness_computable
        rows.append(row)

    # `columns=cols` on both branches makes _detail_columns the single source of
    # truth for the schema — the empty frame and the populated frame cannot drift.
    return pd.DataFrame(rows, columns=cols)


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
