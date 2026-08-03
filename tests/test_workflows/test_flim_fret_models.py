"""Tests for FLIM-FRET workflow config dataclasses and their validation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from percell4.workflows.models import (
    FlimFretConfig,
    FlimFretPair,
    FlimFretPairResult,
    FlimFretReport,
    FlimFretStatus,
)


def _valid_pair(**overrides) -> FlimFretPair:
    defaults = {
        "name": "pair_1",
        "donor_h5": Path("/tmp/donor.h5"),
        "da_h5": Path("/tmp/da.h5"),
        "donor_mask": "cells_mask",
        "donor_phasor": "phasor_ch0_1_phasor",
        "donor_lifetime": "ch0_unfiltered_lifetime",
        "da_mask": "cells_mask",
        "da_phasor": "phasor_ch0_1_phasor",
        "da_lifetime": "ch0_unfiltered_lifetime",
    }
    defaults.update(overrides)
    return FlimFretPair(**defaults)


# ── FlimFretPair ────────────────────────────────────────────


def test_pair_accepts_valid_inputs():
    p = _valid_pair()
    assert p.name == "pair_1"
    assert p.donor_segmentation is None
    assert p.da_segmentation is None


def test_pair_accepts_segmentations():
    p = _valid_pair(donor_segmentation="cellpose_qc", da_segmentation="cellpose_qc")
    assert p.donor_segmentation == "cellpose_qc"
    assert p.da_segmentation == "cellpose_qc"


def test_pair_rejects_empty_name():
    with pytest.raises(ValueError, match="pair name must be non-empty"):
        _valid_pair(name="")


def test_pair_rejects_whitespace_only_name():
    with pytest.raises(ValueError, match="pair name must be non-empty"):
        _valid_pair(name="   ")


@pytest.mark.parametrize(
    "field_name",
    [
        "donor_mask",
        "donor_phasor",
        "donor_lifetime",
        "da_mask",
        "da_phasor",
        "da_lifetime",
    ],
)
def test_pair_rejects_empty_layer_name(field_name):
    with pytest.raises(ValueError, match=f"{field_name} must be non-empty"):
        _valid_pair(**{field_name: ""})


def test_pair_is_frozen():
    p = _valid_pair()
    with pytest.raises((AttributeError, FrozenInstanceError, TypeError)):
        p.name = "mutated"  # type: ignore[misc]


# ── FlimFretConfig ──────────────────────────────────────────


def test_config_accepts_one_pair_whole_field():
    c = FlimFretConfig(
        pairs=[_valid_pair()],
        single_cell=False,
        output_parent=Path("/tmp/out"),
    )
    assert c.single_cell is False
    assert len(c.pairs) == 1


def test_config_accepts_multiple_pairs():
    c = FlimFretConfig(
        pairs=[
            _valid_pair(name="pair_1"),
            _valid_pair(name="pair_2", donor_h5=Path("/tmp/donor2.h5")),
        ],
        single_cell=False,
        output_parent=Path("/tmp/out"),
    )
    assert len(c.pairs) == 2


def test_config_rejects_empty_pairs():
    with pytest.raises(ValueError, match="at least one FLIM-FRET pair"):
        FlimFretConfig(
            pairs=[],
            single_cell=False,
            output_parent=Path("/tmp/out"),
        )


def test_config_rejects_duplicate_pair_names():
    with pytest.raises(ValueError, match="pair names must be unique"):
        FlimFretConfig(
            pairs=[
                _valid_pair(name="dupe"),
                _valid_pair(name="dupe", donor_h5=Path("/tmp/donor2.h5")),
            ],
            single_cell=False,
            output_parent=Path("/tmp/out"),
        )


def test_config_rejects_donor_eq_da_within_pair(tmp_path):
    same = tmp_path / "shared.h5"
    same.touch()
    with pytest.raises(ValueError, match="donor and DA must be different"):
        FlimFretConfig(
            pairs=[_valid_pair(donor_h5=same, da_h5=same)],
            single_cell=False,
            output_parent=tmp_path,
        )


def test_config_rejects_donor_eq_da_via_resolved_path(tmp_path):
    # Two different string paths that resolve to the same file.
    target = tmp_path / "actual.h5"
    target.touch()
    link = tmp_path / "alias.h5"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="donor and DA must be different"):
        FlimFretConfig(
            pairs=[_valid_pair(donor_h5=target, da_h5=link)],
            single_cell=False,
            output_parent=tmp_path,
        )


def test_config_allows_same_h5_across_different_pairs():
    # Cross-pair reuse is legitimate (one donor reference vs multiple DAs).
    shared_donor = Path("/tmp/donor_reference.h5")
    c = FlimFretConfig(
        pairs=[
            _valid_pair(name="vs_DA1", donor_h5=shared_donor, da_h5=Path("/tmp/da1.h5")),
            _valid_pair(name="vs_DA2", donor_h5=shared_donor, da_h5=Path("/tmp/da2.h5")),
        ],
        single_cell=False,
        output_parent=Path("/tmp/out"),
    )
    assert len(c.pairs) == 2


def test_config_rejects_single_cell_without_donor_segmentation():
    with pytest.raises(ValueError, match="donor_segmentation is required"):
        FlimFretConfig(
            pairs=[_valid_pair(da_segmentation="cellpose_qc")],
            single_cell=True,
            output_parent=Path("/tmp/out"),
        )


def test_config_rejects_single_cell_without_da_segmentation():
    with pytest.raises(ValueError, match="da_segmentation is required"):
        FlimFretConfig(
            pairs=[_valid_pair(donor_segmentation="cellpose_qc")],
            single_cell=True,
            output_parent=Path("/tmp/out"),
        )


def test_config_accepts_single_cell_with_both_segmentations():
    c = FlimFretConfig(
        pairs=[
            _valid_pair(
                donor_segmentation="cellpose_qc",
                da_segmentation="cellpose_qc",
            )
        ],
        single_cell=True,
        output_parent=Path("/tmp/out"),
    )
    assert c.single_cell is True


def test_config_is_frozen():
    c = FlimFretConfig(
        pairs=[_valid_pair()],
        single_cell=False,
        output_parent=Path("/tmp/out"),
    )
    with pytest.raises((AttributeError, FrozenInstanceError, TypeError)):
        c.single_cell = True  # type: ignore[misc]


# ── FlimFretPairResult / FlimFretReport ─────────────────────


def test_pair_result_constructs():
    p = _valid_pair()
    r = FlimFretPairResult(
        pair=p,
        status=FlimFretStatus.SUCCEEDED,
        reason=None,
        rows=[{"pair_name": "pair_1", "fret_efficiency": 0.2}],
        n_pixels_donor=100,
        n_cells_donor_reference=0,
        n_da_cells_skipped=0,
    )
    assert r.status is FlimFretStatus.SUCCEEDED
    assert r.rows[0]["fret_efficiency"] == 0.2


def test_pair_result_is_frozen():
    p = _valid_pair()
    r = FlimFretPairResult(
        pair=p,
        status=FlimFretStatus.SUCCEEDED,
        reason=None,
        rows=[],
        n_pixels_donor=0,
        n_cells_donor_reference=0,
        n_da_cells_skipped=0,
    )
    with pytest.raises((AttributeError, FrozenInstanceError, TypeError)):
        r.status = FlimFretStatus.ERROR  # type: ignore[misc]


def test_report_constructs_with_no_run_folder():
    rep = FlimFretReport(results=[])
    assert rep.results == []
    assert rep.run_folder is None


def test_report_constructs_with_run_folder():
    rep = FlimFretReport(results=[], run_folder=Path("/tmp/run_x"))
    assert rep.run_folder == Path("/tmp/run_x")


# ── FlimFretStatus ──────────────────────────────────────────


def test_status_values_are_stable_strings():
    # The values are written into run_log.jsonl and the orchestrator's
    # status column. Pinning the literal strings prevents accidental
    # drift in a future refactor.
    assert FlimFretStatus.SUCCEEDED == "succeeded"
    assert FlimFretStatus.CANCELLED == "cancelled"
    assert FlimFretStatus.MISSING_LAYER == "missing_layer"
    assert FlimFretStatus.DATASET_OPEN_FAILED == "dataset_open_failed"
    assert FlimFretStatus.DONOR_REFERENCE_EMPTY == "donor_reference_empty"
    assert FlimFretStatus.ERROR == "error"
