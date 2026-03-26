"""Tests for per-cell measurement engine."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from percell4.measure.measurer import measure_cells, measure_multichannel


# ── Basic measurement ─────────────────────────────────────────


def test_measure_basic(sample_labels, sample_image):
    """Measure 5 cells with known intensities."""
    df = measure_cells(sample_image, sample_labels)

    assert len(df) == 5
    assert "label" in df.columns
    assert "centroid_y" in df.columns
    assert "mean_intensity" in df.columns
    assert "area" in df.columns

    # Cell 1: region (10:30, 10:30) = 20x20 = 400 pixels, intensity 100
    cell1 = df[df["label"] == 1].iloc[0]
    assert cell1["area"] == 400.0
    assert cell1["mean_intensity"] == 100.0

    # Cell 4: intensity 250
    cell4 = df[df["label"] == 4].iloc[0]
    assert cell4["mean_intensity"] == 250.0


def test_measure_bbox_columns(sample_labels, sample_image):
    """Bounding box columns are present and correct."""
    df = measure_cells(sample_image, sample_labels)

    cell1 = df[df["label"] == 1].iloc[0]
    assert cell1["bbox_y"] == 10
    assert cell1["bbox_x"] == 10
    assert cell1["bbox_h"] == 20
    assert cell1["bbox_w"] == 20


def test_measure_centroid(sample_labels, sample_image):
    """Centroid is roughly at the center of each cell."""
    df = measure_cells(sample_image, sample_labels)

    cell1 = df[df["label"] == 1].iloc[0]
    # Cell 1 spans rows 10-29, cols 10-29 → centroid ~ (19.5, 19.5)
    assert abs(cell1["centroid_y"] - 19.5) < 1.0
    assert abs(cell1["centroid_x"] - 19.5) < 1.0


def test_measure_specific_metrics(sample_labels, sample_image):
    """Measure only a subset of metrics."""
    df = measure_cells(
        sample_image, sample_labels, metrics=["mean_intensity", "area"]
    )

    assert "mean_intensity" in df.columns
    assert "area" in df.columns
    assert "max_intensity" not in df.columns
    assert "std_intensity" not in df.columns


def test_measure_unknown_metric_raises(sample_labels, sample_image):
    """Unknown metric name raises ValueError."""
    with pytest.raises(ValueError, match="Unknown metric"):
        measure_cells(sample_image, sample_labels, metrics=["nonexistent"])


# ── Empty / edge cases ────────────────────────────────────────


def test_measure_empty_labels():
    """Empty label array returns empty DataFrame with correct columns."""
    image = np.ones((50, 50), dtype=np.float32)
    labels = np.zeros((50, 50), dtype=np.int32)

    df = measure_cells(image, labels)
    assert len(df) == 0
    assert "label" in df.columns
    assert "mean_intensity" in df.columns


def test_measure_single_cell():
    """Measure a single cell."""
    image = np.full((20, 20), 42.0, dtype=np.float32)
    labels = np.zeros((20, 20), dtype=np.int32)
    labels[5:15, 5:15] = 1

    df = measure_cells(image, labels)
    assert len(df) == 1
    assert df.iloc[0]["mean_intensity"] == 42.0
    assert df.iloc[0]["area"] == 100.0


# ── Masked measurements ──────────────────────────────────────


def test_measure_with_mask(sample_labels, sample_image):
    """Masked measurement produces inside/outside columns."""
    # Mask: top-left quadrant of image
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[:50, :50] = 1

    df = measure_cells(sample_image, sample_labels, mask=mask)

    assert "mean_intensity_mask_inside" in df.columns
    assert "mean_intensity_mask_outside" in df.columns

    # Cell 1 (10:30, 10:30) is entirely inside the mask
    cell1 = df[df["label"] == 1].iloc[0]
    assert cell1["mean_intensity_mask_inside"] == 100.0
    assert cell1["area_mask_inside"] == 400.0
    assert cell1["area_mask_outside"] == 0.0

    # Cell 4 (50:70, 60:80) is entirely outside the mask
    cell4 = df[df["label"] == 4].iloc[0]
    assert cell4["mean_intensity_mask_inside"] == 0.0
    assert cell4["area_mask_outside"] == 400.0


def test_measure_mask_partial_overlap():
    """Cell partially overlapping mask gets correct inside/outside values."""
    image = np.full((20, 20), 100.0, dtype=np.float32)
    labels = np.zeros((20, 20), dtype=np.int32)
    labels[5:15, 5:15] = 1  # 10x10 cell

    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[5:10, 5:15] = 1  # top half of cell

    df = measure_cells(image, labels, mask=mask)

    cell = df.iloc[0]
    assert cell["area_mask_inside"] == 50.0
    assert cell["area_mask_outside"] == 50.0


# ── Multi-channel ─────────────────────────────────────────────


def test_measure_multichannel(sample_labels):
    """Multi-channel measurement merges channel columns."""
    ch1 = np.full((100, 100), 100.0, dtype=np.float32)
    ch2 = np.full((100, 100), 200.0, dtype=np.float32)

    df = measure_multichannel(
        {"DAPI": ch1, "GFP": ch2},
        sample_labels,
        metrics=["mean_intensity"],
    )

    assert len(df) == 5
    assert "DAPI_mean_intensity" in df.columns
    assert "GFP_mean_intensity" in df.columns
    assert df.iloc[0]["DAPI_mean_intensity"] == 100.0
    assert df.iloc[0]["GFP_mean_intensity"] == 200.0

    # Core columns present once (not duplicated)
    assert "label" in df.columns
    assert "centroid_y" in df.columns
    assert "area" in df.columns


def test_measure_multichannel_empty():
    """Empty channel dict raises ValueError."""
    labels = np.zeros((10, 10), dtype=np.int32)
    with pytest.raises(ValueError, match="No channel"):
        measure_multichannel({}, labels)


# ── NaN handling ──────────────────────────────────────────────


def test_measure_with_nan_image():
    """NaN pixels in image are handled gracefully."""
    image = np.full((20, 20), np.nan, dtype=np.float32)
    image[5:15, 5:15] = 42.0

    labels = np.zeros((20, 20), dtype=np.int32)
    labels[3:17, 3:17] = 1  # cell extends beyond the non-NaN region

    df = measure_cells(image, labels, metrics=["mean_intensity"])
    # nanmean of mixed NaN and 42.0 within the cell
    assert df.iloc[0]["mean_intensity"] == 42.0
