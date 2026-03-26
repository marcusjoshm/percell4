"""Tests for per-cell particle analysis."""

from __future__ import annotations

import numpy as np
import pytest

from percell4.measure.particle import analyze_particles


def _make_test_data():
    """Create test data: 2 cells, one with particles, one without.

    Cell 1 (label=1): region (10:30, 10:30), 20x20 = 400 pixels
        Has 2 bright spots (particles) in the mask
    Cell 2 (label=2): region (50:70, 50:70), 20x20 = 400 pixels
        No particles (mask is zero in this region)
    """
    labels = np.zeros((100, 100), dtype=np.int32)
    labels[10:30, 10:30] = 1
    labels[50:70, 50:70] = 2

    image = np.full((100, 100), 10.0, dtype=np.float32)
    image[10:30, 10:30] = 50.0  # cell 1 background
    image[15:18, 15:18] = 200.0  # bright spot 1 in cell 1
    image[22:25, 22:25] = 200.0  # bright spot 2 in cell 1

    # Mask: threshold-like binary mask covering the bright spots
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[15:18, 15:18] = 1  # spot 1 (9 pixels)
    mask[22:25, 22:25] = 1  # spot 2 (9 pixels)

    return image, labels, mask


def test_basic_particle_analysis():
    """Cell 1 has 2 particles, cell 2 has 0."""
    image, labels, mask = _make_test_data()
    df = analyze_particles(image, labels, mask)

    assert len(df) == 2
    assert "particle_count" in df.columns

    cell1 = df[df["label"] == 1].iloc[0]
    assert cell1["particle_count"] == 2
    assert cell1["total_particle_area"] == 18.0  # 9 + 9

    cell2 = df[df["label"] == 2].iloc[0]
    assert cell2["particle_count"] == 0
    assert cell2["total_particle_area"] == 0.0


def test_particle_coverage_fraction():
    """Coverage fraction = total_particle_area / cell_area."""
    image, labels, mask = _make_test_data()
    df = analyze_particles(image, labels, mask)

    cell1 = df[df["label"] == 1].iloc[0]
    # 18 pixels of particles / 400 pixels of cell
    assert abs(cell1["particle_coverage_fraction"] - 18.0 / 400.0) < 0.001


def test_particle_mean_area():
    """Mean area = total / count."""
    image, labels, mask = _make_test_data()
    df = analyze_particles(image, labels, mask)

    cell1 = df[df["label"] == 1].iloc[0]
    assert cell1["mean_particle_area"] == 9.0  # 18 / 2


def test_particle_intensity():
    """Particle intensity metrics are computed from the image."""
    image, labels, mask = _make_test_data()
    df = analyze_particles(image, labels, mask)

    cell1 = df[df["label"] == 1].iloc[0]
    # Both particles are in regions with intensity 200
    assert cell1["mean_particle_mean_intensity"] == 200.0


def test_min_area_filter():
    """Particles below min_area are filtered out."""
    image, labels, mask = _make_test_data()
    df = analyze_particles(image, labels, mask, min_area=10)

    cell1 = df[df["label"] == 1].iloc[0]
    # Each particle is 9 pixels, both below min_area=10
    assert cell1["particle_count"] == 0


def test_empty_labels():
    """Empty label array returns empty DataFrame."""
    image = np.ones((10, 10), dtype=np.float32)
    labels = np.zeros((10, 10), dtype=np.int32)
    mask = np.ones((10, 10), dtype=np.uint8)

    df = analyze_particles(image, labels, mask)
    assert len(df) == 0
    assert "particle_count" in df.columns


def test_no_mask_overlap():
    """All cells get zero counts when mask doesn't overlap any cell."""
    labels = np.zeros((50, 50), dtype=np.int32)
    labels[10:20, 10:20] = 1

    image = np.ones((50, 50), dtype=np.float32)
    mask = np.zeros((50, 50), dtype=np.uint8)
    mask[30:40, 30:40] = 1  # mask is outside the cell

    df = analyze_particles(image, labels, mask)
    assert len(df) == 1
    assert df.iloc[0]["particle_count"] == 0


def test_single_large_particle():
    """A cell that is entirely covered by the mask = 1 large particle."""
    labels = np.zeros((30, 30), dtype=np.int32)
    labels[5:25, 5:25] = 1  # 20x20 = 400 pixels

    image = np.full((30, 30), 100.0, dtype=np.float32)
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[5:25, 5:25] = 1  # mask covers entire cell

    df = analyze_particles(image, labels, mask)
    cell = df.iloc[0]
    assert cell["particle_count"] == 1
    assert cell["total_particle_area"] == 400.0
    assert cell["particle_coverage_fraction"] == 1.0


def test_multiple_cells_multiple_particles():
    """Test with N>=2 cells, each having different particle counts."""
    labels = np.zeros((50, 50), dtype=np.int32)
    labels[5:15, 5:15] = 1   # cell 1
    labels[5:15, 25:35] = 2  # cell 2
    labels[25:35, 5:15] = 3  # cell 3

    image = np.full((50, 50), 50.0, dtype=np.float32)
    mask = np.zeros((50, 50), dtype=np.uint8)

    # Cell 1: 1 particle
    mask[7:9, 7:9] = 1
    # Cell 2: 0 particles
    # Cell 3: 3 small particles
    mask[27:28, 7:8] = 1
    mask[30:31, 7:8] = 1
    mask[33:34, 7:8] = 1

    df = analyze_particles(image, labels, mask)
    assert len(df) == 3

    c1 = df[df["label"] == 1].iloc[0]
    c2 = df[df["label"] == 2].iloc[0]
    c3 = df[df["label"] == 3].iloc[0]

    assert c1["particle_count"] == 1
    assert c2["particle_count"] == 0
    assert c3["particle_count"] == 3
