"""Tests for the Segment-by-Metric per-particle emitter."""

from __future__ import annotations

import numpy as np
import pytest

from percell4.domain.measure.cnr_classification import (
    segment_label_image,
)
from percell4.domain.measure.metric_segmentation import (
    SEGMENT_METRICS,
    measure_metric_per_particle,
)
from percell4.domain.measure.particle import analyze_particles_detail


def _two_cell_fixture():
    """Two cells, each with a couple of compact bright particles on background.

    All particles are >=2px with a non-empty skirt, so every edge_skirt_ratio is
    finite — lets the parity test compare 1:1 with analyze_particles_detail.
    """
    labels = np.zeros((40, 60), dtype=np.int32)
    labels[5:35, 5:28] = 1
    labels[5:35, 32:55] = 2
    img = np.full((40, 60), 8.0, dtype=np.float32)
    mask = np.zeros((40, 60), dtype=np.uint8)
    for (cy, cx) in [(14, 12), (24, 20), (14, 42), (26, 48)]:
        img[cy : cy + 3, cx : cx + 3] = 200.0
        mask[cy : cy + 3, cx : cx + 3] = 1
    return img, labels, mask


def test_edge_skirt_parity_with_analyze_particles_detail():
    """The emitter's per-particle edge_skirt_ratio equals the CSV path's values,
    in the same per-cell iteration order (the correctness anchor for R3)."""
    img, labels, mask = _two_cell_fixture()
    df = analyze_particles_detail({"C0": img}, labels, mask, min_area=1)
    records, global_labels, excluded = measure_metric_per_particle(
        img, mask, labels, "edge_skirt_ratio", min_area=1
    )
    assert excluded == 0
    assert len(records) == len(df)
    np.testing.assert_allclose(
        [r["value"] for r in records],
        df["edge_skirt_ratio"].to_numpy(),
        rtol=1e-6,
        equal_nan=True,
    )


def test_area_matches_per_cell_particle_pixel_counts():
    """Area is per-cell particle pixel count (× px_area), matching the CSV's
    per-cell substrate — a blob straddling two cells is two particles, not one
    global component."""
    labels = np.zeros((20, 40), dtype=np.int32)
    labels[2:18, 2:20] = 1
    labels[2:18, 20:38] = 2
    img = np.full((20, 40), 10.0, dtype=np.float32)
    mask = np.zeros((20, 40), dtype=np.uint8)
    mask[9:12, 18:22] = 1  # 3x4 blob straddling the cell-1/cell-2 border at col 20
    records, global_labels, excluded = measure_metric_per_particle(
        img, mask, labels, "area", pixel_size_um=0.1, min_area=1
    )
    # Two per-cell particles (cols 18-19 in cell 1, cols 20-21 in cell 2).
    assert len(records) == 2
    total_px = sum(r["value"] for r in records) / (0.1 * 0.1)
    assert round(total_px) == 12  # 3 rows x 4 cols total, split across cells


def test_records_align_with_global_labels():
    """Every record label indexes a real region of global_labels (no orphans)."""
    img, labels, mask = _two_cell_fixture()
    records, global_labels, _ = measure_metric_per_particle(
        img, mask, labels, "edge_skirt_ratio", min_area=1
    )
    present = set(np.unique(global_labels)) - {0}
    assert {r["label"] for r in records} <= present
    assert len(present) == len(records)  # 4 particles, all valid


def test_split_pipeline_produces_low_and_high():
    """Feeding the emitter's labels + a threshold through the split pipeline
    yields a valid 0/1/2 image with both populations (using varied values so a
    threshold actually separates)."""
    img, labels, mask = _two_cell_fixture()
    _, global_labels, _ = measure_metric_per_particle(
        img, mask, labels, "area", min_area=1
    )
    present = sorted(set(np.unique(global_labels)) - {0})
    assert len(present) == 4
    # Assign the four particles distinct synthetic values and split at the median.
    values = np.array([0.1, 0.2, 0.3, 0.4])
    seg = segment_label_image(global_labels, present, values, [0.25])
    assert set(np.unique(seg)) <= {0, 1, 2}
    assert (seg == 1).any() and (seg == 2).any()  # low + high both present


def test_low_signal_particle_gives_finite_not_excluded():
    """A dim particle yields a real finite edge_skirt (out-of-focus region), not
    an exclusion."""
    labels = np.zeros((30, 30), dtype=np.int32)
    labels[2:28, 2:28] = 1
    img = np.full((30, 30), 8.0, dtype=np.float32)
    img[13:16, 13:16] = 12.0  # low contrast
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[13:16, 13:16] = 1
    records, _, excluded = measure_metric_per_particle(
        img, mask, labels, "edge_skirt_ratio"
    )
    assert excluded == 0
    assert len(records) == 1 and np.isfinite(records[0]["value"])


def test_dark_channel_excludes_sharpness_but_not_area():
    """On a signal-free channel, edge_skirt records drop (excluded>0) while area
    survives — the per-metric validity rule (R8)."""
    labels = np.zeros((30, 30), dtype=np.int32)
    labels[2:28, 2:28] = 1
    dark = np.zeros((30, 30), dtype=np.float32)  # no signal
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[13:16, 13:16] = 1
    es_records, _, es_excluded = measure_metric_per_particle(
        dark, mask, labels, "edge_skirt_ratio"
    )
    assert es_records == [] and es_excluded == 1
    ar_records, _, ar_excluded = measure_metric_per_particle(
        dark, mask, labels, "area", pixel_size_um=0.12
    )
    assert len(ar_records) == 1 and ar_excluded == 0


def test_empty_mask_returns_empty():
    labels = np.zeros((10, 10), dtype=np.int32)
    labels[2:8, 2:8] = 1
    img = np.ones((10, 10), dtype=np.float32)
    mask = np.zeros((10, 10), dtype=np.uint8)
    records, global_labels, excluded = measure_metric_per_particle(
        img, mask, labels, "area"
    )
    assert records == [] and excluded == 0 and not global_labels.any()


def test_unknown_metric_raises():
    img = np.ones((5, 5), dtype=np.float32)
    labels = np.ones((5, 5), dtype=np.int32)
    mask = np.zeros((5, 5), dtype=np.uint8)
    with pytest.raises(ValueError, match="unknown segment metric"):
        measure_metric_per_particle(img, mask, labels, "not_a_metric")


def test_intensity_metric_parity():
    """mean/max/integrated intensity match analyze_particles_detail per particle."""
    img, labels, mask = _two_cell_fixture()
    df = analyze_particles_detail({"C0": img}, labels, mask, min_area=1)
    for metric in ("mean_intensity", "max_intensity", "integrated_intensity"):
        records, _, _ = measure_metric_per_particle(img, mask, labels, metric)
        np.testing.assert_allclose(
            [r["value"] for r in records], df[metric].to_numpy(), rtol=1e-6
        )


def test_segment_metrics_excludes_weak_and_cnr():
    """The offered set is the validated family; no boundary_gradient / laplacian
    / cnr (served elsewhere)."""
    assert "edge_skirt_ratio" in SEGMENT_METRICS and "area" in SEGMENT_METRICS
    assert "boundary_gradient" not in SEGMENT_METRICS
    assert "laplacian_variance" not in SEGMENT_METRICS
    assert "cnr" not in SEGMENT_METRICS
