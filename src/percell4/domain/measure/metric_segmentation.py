"""Per-particle metric measurement for the interactive Segment-by-Metric tool.

The interactive splitter (``gui/cnr_segmenter.py`` generalized) needs, for a
chosen metric, one scalar value per particle keyed to a label image that
:func:`percell4.domain.measure.cnr_classification.segment_label_image` can
scatter into low/high masks.

Crucially, the particle population must be the SAME one the validated
``particles.csv`` uses — otherwise a threshold a researcher reads off the CSV
would not transfer to the in-app histogram. So this labels particles over the
**per-cell substrate** (``cell_mask & mask``, labeled per cell), identical to
:func:`percell4.domain.measure.particle._iter_particles`, and reuses that
module's shared helpers so the values are identical by construction. Particles
are then relabeled to globally-unique ids to produce the ``component_labels``
image the split pipeline consumes.

Pure numpy / scipy / skimage — no Qt, h5py, or store.

CNR is intentionally not handled here (it has its own per-cell-vs-global
substrate question and its own interactive tool); this module covers the
sharpness/size/intensity family the eye-validation identified.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import find_objects
from scipy.ndimage import label as ndlabel
from skimage.measure import regionprops

from percell4.domain.measure.metrics import BUILTIN_METRICS
from percell4.domain.measure.particle import (
    _sharpness_for_particle,
    sharpness_buffers,
    skirt_and_boundary,
)

# Metrics this emitter can measure (a subset of the per-particle families,
# offered by the Segment-by-Metric picker). CNR is served by its own tool.
SEGMENT_METRICS = (
    "edge_skirt_ratio",     # sharpness: low = in-focus, high = out-of-focus
    "area",                 # size (µm² when pixel_size_um given, else px²)
    "mean_intensity",
    "max_intensity",
    "integrated_intensity",
)


def _is_valid(metric: str, value: float) -> bool:
    """Per-metric drop rule deciding histogram + saved-mask membership.

    A particle whose value is uncomputable for the measured channel (NaN — e.g.
    a peak-normalized sharpness/intensity metric on a channel with no signal at
    the particle) is excluded from the histogram and from BOTH output masks. It
    is still painted into the label image (so the image is complete) but omitted
    from ``records`` — ``segment_label_image`` then leaves it as background.
    ``area`` is always finite and positive, so it never drops.
    """
    return bool(np.isfinite(value))


def measure_metric_per_particle(
    image: NDArray,
    feature_mask: NDArray,
    cell_labels: NDArray[np.int32],
    metric: str,
    *,
    pixel_size_um: float | None = None,
    min_area: int = 1,
) -> tuple[list[dict], NDArray[np.int32], int]:
    """Measure ``metric`` per particle over the per-cell substrate.

    Parameters
    ----------
    image : (H, W) detection-channel intensity (the channel the mask was found on)
    feature_mask : (H, W) 0/1 mask to split
    cell_labels : (H, W) int32 cell labels (0 = background)
    metric : one of :data:`SEGMENT_METRICS`
    pixel_size_um : µm per pixel; when given, ``area`` is reported in µm²
    min_area : minimum particle area in pixels (matches particle-analysis filter)

    Returns
    -------
    (records, global_labels, excluded_count)
        ``records`` : ``[{"label": <global-unique id>, "value": <float>}]`` for
            valid particles only, in per-cell iteration order.
        ``global_labels`` : (H, W) int32 image where each particle (valid or
            excluded) carries a unique id 1..N — the ``component_labels`` the
            split pipeline scatters over.
        ``excluded_count`` : particles dropped by the metric's validity rule.
    """
    if metric not in SEGMENT_METRICS:
        raise ValueError(
            f"unknown segment metric {metric!r}; expected one of {SEGMENT_METRICS}"
        )

    global_labels = np.zeros(image.shape, dtype=np.int32)
    records: list[dict] = []
    excluded = 0
    gid = 0

    if cell_labels.max() == 0:
        return records, global_labels, excluded

    slices = find_objects(cell_labels)
    mask_bool = feature_mask > 0
    px_area = (pixel_size_um * pixel_size_um) if pixel_size_um else 1.0

    for label_val in range(1, int(cell_labels.max()) + 1):
        sl = slices[label_val - 1]
        if sl is None:
            continue
        label_crop = cell_labels[sl]
        cell_mask = label_crop == label_val
        if not cell_mask.any():
            continue
        particle_mask = cell_mask & mask_bool[sl]
        particle_labels, n_components = ndlabel(particle_mask)
        if n_components == 0:
            continue

        crop = image[sl]
        label_view = global_labels[sl]
        grad = lap = None
        if metric == "edge_skirt_ratio":
            grad, lap = sharpness_buffers(crop)

        for pid, prop in enumerate(regionprops(particle_labels), start=1):
            if prop.area < min_area:
                continue
            this_particle = particle_labels == pid
            gid += 1
            label_view[this_particle] = gid  # write-through view; complete image

            if metric == "area":
                value = float(this_particle.sum()) * px_area
            elif metric == "edge_skirt_ratio":
                other = (particle_labels > 0) & ~this_particle
                skirt, boundary = skirt_and_boundary(this_particle, cell_mask, other)
                value = _sharpness_for_particle(
                    crop, grad, lap, this_particle, skirt, boundary
                )["edge_skirt_ratio"]
            else:  # intensity family
                try:
                    value = float(BUILTIN_METRICS[metric](crop, this_particle))
                except Exception:
                    value = float("nan")

            if _is_valid(metric, value):
                records.append({"label": gid, "value": value})
            else:
                excluded += 1

    return records, global_labels, excluded
