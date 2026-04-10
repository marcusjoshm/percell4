"""Tests for the channel intersection helper."""

from __future__ import annotations

from percell4.workflows.channels import intersect_channels


def test_empty_sources() -> None:
    inter, outliers = intersect_channels([])
    assert inter == []
    assert outliers == []


def test_single_source() -> None:
    inter, outliers = intersect_channels([("DS1", ["GFP", "RFP"])])
    assert inter == ["GFP", "RFP"]
    assert outliers == []


def test_single_source_dedupes() -> None:
    inter, _ = intersect_channels([("DS1", ["GFP", "GFP", "RFP"])])
    assert inter == ["GFP", "RFP"]


def test_all_datasets_identical() -> None:
    sources = [
        ("DS1", ["GFP", "RFP", "DAPI"]),
        ("DS2", ["GFP", "RFP", "DAPI"]),
    ]
    inter, outliers = intersect_channels(sources)
    assert inter == ["GFP", "RFP", "DAPI"]
    assert outliers == []


def test_partial_overlap_preserves_first_order() -> None:
    # Brainstorm example: every dataset has GFP and RFP; only DAPI is dropped.
    sources = [
        ("DS1", ["GFP", "RFP", "DAPI"]),
        ("DS2", ["GFP", "RFP", "DAPI", "Cy5"]),
        ("DS3", ["GFP", "RFP", "Cy5"]),
    ]
    inter, outliers = intersect_channels(sources)
    assert inter == ["GFP", "RFP"]
    assert outliers == []


def test_one_outlier_dataset() -> None:
    sources = [
        ("DS1", ["GFP", "RFP"]),
        ("DS2", ["GFP", "RFP"]),
        ("DS3", ["DAPI", "Cy5"]),  # zero overlap with DS1/DS2
    ]
    inter, outliers = intersect_channels(sources)
    assert outliers == ["DS3"]
    assert inter == ["GFP", "RFP"]


def test_no_common_channels_at_all() -> None:
    # Every source overlaps *some* other source, but nothing common to all.
    sources = [
        ("DS1", ["A", "B"]),
        ("DS2", ["B", "C"]),
        ("DS3", ["C", "A"]),
    ]
    inter, outliers = intersect_channels(sources)
    assert inter == []
    # Each dataset contributes to the mismatch, so all are reported as outliers.
    assert set(outliers) == {"DS1", "DS2", "DS3"}


def test_intersection_preserves_first_source_order() -> None:
    sources = [
        ("DS1", ["RFP", "GFP", "Cy5"]),
        ("DS2", ["GFP", "Cy5", "RFP"]),
    ]
    inter, _ = intersect_channels(sources)
    # Order should follow DS1's ordering, not DS2's.
    assert inter == ["RFP", "GFP", "Cy5"]
