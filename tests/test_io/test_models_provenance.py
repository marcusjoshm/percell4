"""Tests for provenance records that round-trip as HDF5 attrs (U2)."""

from __future__ import annotations

import json

from percell4.domain.io.models import StitchProvenanceRecord


def _make_record() -> StitchProvenanceRecord:
    quality = {
        "pair_correlations": [0.98, 0.95, 0.97],
        "disconnected": [],
        "accepted_pair_fraction": 1.0,
        "coverage_fraction": 0.987,
        "regression_threshold": 0.3,
        "n_peaks": 5,
    }
    return StitchProvenanceRecord(
        reference_channel="DAPI",
        overlap="0.1",
        library="grid_stitching@vendored-2026-06-24",
        quality_json=json.dumps(quality),
        n_tiles="4",
        importer_version="percell4-import-1",
        timestamp_utc="2026-06-24T00:00:00Z",
    )


def test_to_attrs_is_all_str_or_bool() -> None:
    """to_attrs() returns only str/bool values (HDF5 attr-safe)."""
    attrs = _make_record().to_attrs()
    assert attrs  # non-empty
    assert all(isinstance(v, (str, bool)) for v in attrs.values())


def test_to_attrs_contains_expected_keys() -> None:
    attrs = _make_record().to_attrs()
    assert set(attrs) == {
        "reference_channel",
        "overlap",
        "library",
        "quality_json",
        "n_tiles",
        "importer_version",
        "timestamp_utc",
    }


def test_quality_json_parses_with_coverage_fraction() -> None:
    """quality_json parses as JSON and carries a coverage_fraction key."""
    attrs = _make_record().to_attrs()
    quality = json.loads(attrs["quality_json"])
    assert "coverage_fraction" in quality
    assert quality["coverage_fraction"] == 0.987
