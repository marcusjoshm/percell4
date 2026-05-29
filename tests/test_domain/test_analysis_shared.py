"""Unit tests for the shared per-particle/donut helpers (U1)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from percell4.domain.analysis._impl._shared import (
    assign_particles_to_cells,
    label_and_filter,
    nan_safe_ratio,
    nanmean_or_nan,
    nansum_or_nan,
    region_and_donut_masks,
    weighted_mean,
)


def _two_blob_mask() -> np.ndarray:
    m = np.zeros((40, 40), dtype=np.uint8)
    m[5:9, 5:9] = 1      # 16 px blob
    m[25:35, 25:35] = 1  # 100 px blob
    return m


# ── label_and_filter ──────────────────────────────────────────────


def test_label_and_filter_keeps_only_above_min_size():
    label_mask, binary_mask, region_ids, props_by_id = label_and_filter(
        _two_blob_mask(), min_size=20
    )
    # Only the 100px blob survives the strict > 20 filter.
    assert len(region_ids) == 1
    surviving = region_ids[0]
    assert props_by_id[surviving].area == 100
    assert binary_mask.dtype == bool


def test_label_and_filter_min_size_zero_keeps_all():
    _, _, region_ids, _ = label_and_filter(_two_blob_mask(), min_size=0)
    assert len(region_ids) == 2


def test_label_and_filter_strict_greater_than():
    # A blob of exactly 16 px is dropped when min_size == 16 (strict >).
    m = np.zeros((20, 20), np.uint8)
    m[2:6, 2:6] = 1  # 16 px
    _, _, region_ids, _ = label_and_filter(m, min_size=16)
    assert len(region_ids) == 0


# ── region_and_donut_masks ────────────────────────────────────────


def test_region_and_donut_masks_ring_excludes_region_and_mask():
    mask = np.zeros((30, 30), np.uint8)
    mask[12:18, 12:18] = 1
    label_mask, binary_mask, region_ids, props_by_id = label_and_filter(
        mask, min_size=0
    )
    rid = region_ids[0]
    region_mask, donut_mask = region_and_donut_masks(
        label_mask, binary_mask, rid, props_by_id, buffer_px=1, donut_px=3
    )
    # Region and donut are disjoint; donut never overlaps the binary mask.
    assert not np.any(region_mask & donut_mask)
    assert not np.any(donut_mask & binary_mask)
    assert region_mask.sum() == 36
    assert donut_mask.sum() > 0


# ── assign_particles_to_cells ─────────────────────────────────────


def test_assign_particles_to_cells_majority_vote():
    mask = np.zeros((10, 20), np.uint8)
    mask[2:6, 2:6] = 1   # particle A
    mask[2:6, 12:16] = 1  # particle B
    cp = np.zeros((10, 20), np.int32)
    cp[:, :10] = 1   # cell 1 covers left half (particle A)
    cp[:, 10:] = 2   # cell 2 covers right half (particle B)
    mapping = assign_particles_to_cells(mask, cp, min_size=0)
    # Two particles map to two distinct cells.
    assert set(mapping.values()) == {1, 2}


def test_assign_particles_to_cells_unmatched_omitted():
    mask = np.zeros((10, 10), np.uint8)
    mask[2:5, 2:5] = 1
    cp = np.zeros((10, 10), np.int32)  # no cells anywhere
    mapping = assign_particles_to_cells(mask, cp, min_size=0)
    assert mapping == {}  # particle overlapping no cell is dropped


# ── nan_safe_ratio ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (6.0, 2.0, 3.0),
        (1.0, 0.0, np.nan),
        (np.nan, 2.0, np.nan),
        (2.0, np.nan, np.nan),
    ],
)
def test_nan_safe_ratio(a, b, expected):
    result = nan_safe_ratio(a, b)
    if np.isnan(expected):
        assert np.isnan(result)
    else:
        assert result == expected


# ── weighted_mean ─────────────────────────────────────────────────


def test_weighted_mean_area_weighted():
    df = pd.DataFrame({"v": [10.0, 20.0], "w": [1.0, 3.0]})
    # (10*1 + 20*3) / 4 = 17.5
    assert weighted_mean(df, "v", "w") == 17.5


def test_weighted_mean_drops_nan_and_zero_weight():
    df = pd.DataFrame({"v": [10.0, np.nan, 99.0], "w": [2.0, 5.0, 0.0]})
    # only the first row is valid → mean == 10.0
    assert weighted_mean(df, "v", "w") == 10.0


def test_weighted_mean_all_invalid_is_nan():
    df = pd.DataFrame({"v": [np.nan], "w": [0.0]})
    assert np.isnan(weighted_mean(df, "v", "w"))


# ── nanmean_or_nan / nansum_or_nan ────────────────────────────────


def test_nan_stats_all_nan_returns_nan():
    arr = np.array([np.nan, np.nan])
    assert np.isnan(nanmean_or_nan(arr))
    assert np.isnan(nansum_or_nan(arr))


def test_nan_stats_partial_nan():
    arr = np.array([2.0, np.nan, 4.0])
    assert nanmean_or_nan(arr) == 3.0
    assert nansum_or_nan(arr) == 6.0
