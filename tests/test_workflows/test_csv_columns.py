"""Tests for the shared CSV column-selection helper (GUI + CLI source of truth)."""

from __future__ import annotations

from percell4.workflows.csv_columns import (
    DEFAULT_CSV_METRICS,
    DEFAULT_CSV_PARTICLE_PER_CELL,
    DEFAULT_CSV_PARTICLE_PER_CHANNEL,
    build_selected_csv_columns,
)


def test_build_columns_terminates_with_area_in_columns():
    """Regression: `_area_in_<round>` columns also match the area-sibling
    test, so iterating the live list while extending it looped forever.
    The builder must snapshot and terminate."""
    cols = build_selected_csv_columns(["GFP", "RFP"], ["pbody"])
    assert isinstance(cols, list)
    # Whole-cell, overlap, group, particle, and um2 sibling columns present.
    assert "GFP_mean_intensity" in cols
    assert "GFP_area_in_pbody" in cols
    assert "group_pbody" in cols
    assert "pbody_particle_count" in cols
    assert "pbody_GFP_particle_mean_intensity" in cols
    assert "GFP_area_in_pbody_um2" in cols
    # No double-`_um2` runaway from the infinite loop.
    assert not any(c.endswith("_um2_um2") for c in cols)


def test_build_columns_default_selection_shape():
    cols = build_selected_csv_columns(
        ["GFP"],
        ["m"],
        metrics=DEFAULT_CSV_METRICS,
        particle_per_cell=DEFAULT_CSV_PARTICLE_PER_CELL,
        particle_per_channel=DEFAULT_CSV_PARTICLE_PER_CHANNEL,
    )
    # area (core) + GFP_{area,integrated,mean} + group_m + GFP_{...}_in_m
    # + m_{particle_count,total_particle_area} + m_GFP_particle_mean_intensity
    assert "m_particle_count" in cols
    assert "m_total_particle_area" in cols
    assert "m_GFP_particle_mean_intensity" in cols
    # No duplicate entries.
    assert len(cols) == len(set(cols))


def test_build_columns_empty_rounds_and_channels():
    assert build_selected_csv_columns([], []) == [
        "centroid_y", "centroid_x", "bbox_y", "bbox_x", "bbox_h", "bbox_w",
        "area", "area_um2",
    ]
