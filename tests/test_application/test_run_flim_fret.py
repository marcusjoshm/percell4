"""Tests for the FLIM-FRET orchestrator ``run_flim_fret``.

Math correctness is the highest-stakes property of this unit, so tests
build handcrafted ``.h5`` fixtures with known per-pixel lifetimes and
assert exact CSV-row values, not approximations.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock

import h5py
import numpy as np
import pytest

from percell4.application.use_cases.run_flim_fret import run_flim_fret
from percell4.workflows.models import (
    FlimFretConfig,
    FlimFretPair,
    FlimFretPairResult,
    FlimFretStatus,
)
from percell4.workflows.run_log import RunLog


# ── Fixture builders ────────────────────────────────────────


def _write_h5(
    path: Path,
    *,
    intensity: np.ndarray,
    channel_names: list[str],
    masks: dict[str, np.ndarray] | None = None,
    labels: dict[str, np.ndarray] | None = None,
) -> Path:
    """Write an h5 dataset honoring DatasetStore conventions.

    ``intensity`` is the full ``(C, H, W)`` (or ``(H, W)`` for single-channel)
    array. ``channel_names`` indexes it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channel_names
        f.create_dataset("intensity", data=intensity.astype(np.float32))
        if masks:
            mg = f.create_group("masks")
            for name, arr in masks.items():
                mg.create_dataset(name, data=arr.astype(np.uint8))
        if labels:
            lg = f.create_group("labels")
            for name, arr in labels.items():
                lg.create_dataset(name, data=arr.astype(np.int32))
    return path


def _ones(shape: tuple[int, ...]) -> np.ndarray:
    return np.ones(shape, dtype=np.uint8)


def _pair_factory(
    tmp_path: Path,
    *,
    donor_lifetime_arr: np.ndarray,
    da_lifetime_arr: np.ndarray,
    donor_mask: np.ndarray | None = None,
    donor_phasor: np.ndarray | None = None,
    da_mask: np.ndarray | None = None,
    da_phasor: np.ndarray | None = None,
    donor_seg: np.ndarray | None = None,
    da_seg: np.ndarray | None = None,
    name: str = "pair_1",
) -> FlimFretPair:
    """Build a pair where /intensity has one channel: the lifetime channel."""
    H, W = donor_lifetime_arr.shape
    donor_mask = donor_mask if donor_mask is not None else _ones((H, W))
    donor_phasor = donor_phasor if donor_phasor is not None else _ones((H, W))
    da_mask = da_mask if da_mask is not None else _ones((H, W))
    da_phasor = da_phasor if da_phasor is not None else _ones((H, W))

    donor_labels = {"cellpose_qc": donor_seg} if donor_seg is not None else None
    da_labels = {"cellpose_qc": da_seg} if da_seg is not None else None

    donor_h5 = _write_h5(
        tmp_path / f"{name}_donor.h5",
        intensity=donor_lifetime_arr[np.newaxis, ...],
        channel_names=["ch0_unfiltered_lifetime"],
        masks={
            "cells_mask": donor_mask,
            "phasor_ch0_1_phasor": donor_phasor,
        },
        labels=donor_labels,
    )
    da_h5 = _write_h5(
        tmp_path / f"{name}_da.h5",
        intensity=da_lifetime_arr[np.newaxis, ...],
        channel_names=["ch0_unfiltered_lifetime"],
        masks={
            "cells_mask": da_mask,
            "phasor_ch0_1_phasor": da_phasor,
        },
        labels=da_labels,
    )
    return FlimFretPair(
        name=name,
        donor_h5=donor_h5,
        da_h5=da_h5,
        donor_mask="cells_mask",
        donor_phasor="phasor_ch0_1_phasor",
        donor_lifetime="ch0_unfiltered_lifetime",
        da_mask="cells_mask",
        da_phasor="phasor_ch0_1_phasor",
        da_lifetime="ch0_unfiltered_lifetime",
        donor_segmentation="cellpose_qc" if donor_seg is not None else None,
        da_segmentation="cellpose_qc" if da_seg is not None else None,
    )


# ── Happy path: whole-field mode ────────────────────────────


def test_whole_field_single_pair_math(tmp_path):
    """donor=2.5 everywhere, DA=2.0 everywhere → FRET = 0.2."""
    donor = np.full((4, 4), 2.5, dtype=np.float32)
    da = np.full((4, 4), 2.0, dtype=np.float32)
    pair = _pair_factory(tmp_path, donor_lifetime_arr=donor, da_lifetime_arr=da)
    config = FlimFretConfig(
        pairs=[pair],
        single_cell=False,
        output_parent=tmp_path,
    )

    report = run_flim_fret(config)

    assert len(report.results) == 1
    result = report.results[0]
    assert result.status is FlimFretStatus.SUCCEEDED
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["pair_name"] == "pair_1"
    assert row["cell_id"] == ""
    assert row["donor_mean_lifetime"] == pytest.approx(2.5)
    assert row["da_mean_lifetime"] == pytest.approx(2.0)
    assert row["fret_efficiency"] == pytest.approx(0.2)
    assert row["n_pixels_donor"] == 16
    assert row["n_pixels_da"] == 16
    assert row["donor_dataset"] == pair.donor_h5.name
    assert row["da_dataset"] == pair.da_h5.name


def test_whole_field_intersection_restricts_mean(tmp_path):
    """Only pixels in (mask AND phasor) contribute to the mean."""
    # 4x4 lifetime: increasing 0..15. Mask covers left half (cols 0-1);
    # phasor covers top half (rows 0-1). Intersection: top-left 2x2 quadrant
    # with values 0, 1, 4, 5 → mean = 2.5.
    donor = np.arange(16, dtype=np.float32).reshape(4, 4)
    da = donor.copy()
    mask = np.zeros((4, 4), dtype=np.uint8)
    mask[:, :2] = 1  # left half
    phasor = np.zeros((4, 4), dtype=np.uint8)
    phasor[:2, :] = 1  # top half
    pair = _pair_factory(
        tmp_path,
        donor_lifetime_arr=donor,
        da_lifetime_arr=da,
        donor_mask=mask,
        donor_phasor=phasor,
        da_mask=mask,
        da_phasor=phasor,
    )
    config = FlimFretConfig(
        pairs=[pair], single_cell=False, output_parent=tmp_path
    )

    report = run_flim_fret(config)

    row = report.results[0].rows[0]
    assert row["donor_mean_lifetime"] == pytest.approx(2.5)  # mean(0,1,4,5)
    assert row["n_pixels_donor"] == 4
    assert row["n_pixels_da"] == 4


def test_whole_field_empty_intersection(tmp_path):
    """Disjoint mask and phasor → empty intersection → NaN means + NaN FRET."""
    donor = np.full((4, 4), 2.5, dtype=np.float32)
    mask = np.zeros((4, 4), dtype=np.uint8)
    mask[:, :2] = 1
    phasor = np.zeros((4, 4), dtype=np.uint8)
    phasor[:, 2:] = 1  # disjoint from mask
    pair = _pair_factory(
        tmp_path,
        donor_lifetime_arr=donor,
        da_lifetime_arr=donor.copy(),
        donor_mask=mask,
        donor_phasor=phasor,
        da_mask=mask,
        da_phasor=phasor,
    )
    config = FlimFretConfig(
        pairs=[pair], single_cell=False, output_parent=tmp_path
    )

    report = run_flim_fret(config)
    row = report.results[0].rows[0]
    assert math.isnan(row["donor_mean_lifetime"])
    assert math.isnan(row["fret_efficiency"])
    assert row["n_pixels_donor"] == 0


def test_whole_field_multi_label_mask_treated_as_boolean(tmp_path):
    """Multi-label mask values > 0 all count as 'true'."""
    donor = np.full((4, 4), 2.0, dtype=np.float32)
    mask = np.zeros((4, 4), dtype=np.uint8)
    mask[0, :] = 1
    mask[1, :] = 2
    mask[2, :] = 3
    # row 3 stays 0 (background)
    phasor = _ones((4, 4))
    pair = _pair_factory(
        tmp_path,
        donor_lifetime_arr=donor,
        da_lifetime_arr=donor.copy(),
        donor_mask=mask,
        donor_phasor=phasor,
        da_mask=mask,
        da_phasor=phasor,
    )
    config = FlimFretConfig(
        pairs=[pair], single_cell=False, output_parent=tmp_path
    )

    row = run_flim_fret(config).results[0].rows[0]
    assert row["n_pixels_donor"] == 12  # rows 0, 1, 2 all included; row 3 excluded


# ── Happy path: single-cell mode ────────────────────────────


def test_single_cell_pooled_donor_reference(tmp_path):
    """Donor has 2 cells (means 2.5, 2.4) → reference 2.45.

    DA has 3 cells (means 2.0, 2.1, 1.9). Each DA row gets the same
    donor_mean_lifetime (2.45) and a per-cell da_mean_lifetime.
    """
    donor = np.zeros((4, 4), dtype=np.float32)
    donor[:2, :] = 2.5  # cell 1
    donor[2:, :] = 2.4  # cell 2
    donor_seg = np.zeros((4, 4), dtype=np.int32)
    donor_seg[:2, :] = 1
    donor_seg[2:, :] = 2

    da = np.zeros((4, 4), dtype=np.float32)
    da[0, :] = 2.0  # cell 1
    da[1:3, :] = 2.1  # cell 2
    da[3, :] = 1.9  # cell 3
    da_seg = np.zeros((4, 4), dtype=np.int32)
    da_seg[0, :] = 1
    da_seg[1:3, :] = 2
    da_seg[3, :] = 3

    pair = _pair_factory(
        tmp_path,
        donor_lifetime_arr=donor,
        da_lifetime_arr=da,
        donor_seg=donor_seg,
        da_seg=da_seg,
    )
    config = FlimFretConfig(
        pairs=[pair], single_cell=True, output_parent=tmp_path
    )

    report = run_flim_fret(config)
    result = report.results[0]
    assert result.status is FlimFretStatus.SUCCEEDED
    assert len(result.rows) == 3  # one row per DA cell

    expected_ref = 2.45  # mean(2.5, 2.4)
    expected_da_means = {1: 2.0, 2: 2.1, 3: 1.9}
    for row in result.rows:
        cid = row["cell_id"]
        assert row["donor_mean_lifetime"] == pytest.approx(expected_ref)
        assert row["da_mean_lifetime"] == pytest.approx(
            expected_da_means[cid]
        )
        expected_fret = 1 - (expected_da_means[cid] / expected_ref)
        assert row["fret_efficiency"] == pytest.approx(expected_fret)

    # n_cells_donor_reference == 2 on every row of this pair
    for row in result.rows:
        assert row["n_cells_donor_reference"] == 2


def test_single_cell_excludes_background_label_zero(tmp_path):
    """Label 0 is background and never gets its own row.

    Donor has cell 1 (mean 2.5) and a lot of background. DA has cell 1
    (mean 2.0) and background. Expect exactly one DA row.
    """
    donor = np.full((4, 4), 99.0, dtype=np.float32)  # background junk
    donor[0, :] = 2.5  # cell 1 only
    donor_seg = np.zeros((4, 4), dtype=np.int32)
    donor_seg[0, :] = 1

    da = np.full((4, 4), 99.0, dtype=np.float32)
    da[0, :] = 2.0
    da_seg = np.zeros((4, 4), dtype=np.int32)
    da_seg[0, :] = 1

    pair = _pair_factory(
        tmp_path,
        donor_lifetime_arr=donor,
        da_lifetime_arr=da,
        donor_seg=donor_seg,
        da_seg=da_seg,
    )
    config = FlimFretConfig(
        pairs=[pair], single_cell=True, output_parent=tmp_path
    )

    rows = run_flim_fret(config).results[0].rows
    assert len(rows) == 1
    assert rows[0]["cell_id"] == 1
    assert rows[0]["donor_mean_lifetime"] == pytest.approx(2.5)
    assert rows[0]["da_mean_lifetime"] == pytest.approx(2.0)


def test_single_cell_rows_ordered_by_ascending_label(tmp_path):
    """Per the plan: CSV rows ordered by (pair_index, cell_id ascending)."""
    donor = np.full((4, 4), 2.5, dtype=np.float32)
    donor_seg = np.zeros((4, 4), dtype=np.int32)
    donor_seg[0, :] = 1

    da = np.full((4, 4), 2.0, dtype=np.float32)
    # Layout cells out-of-order in the array: label 5 first, then 2, then 7
    da_seg = np.zeros((4, 4), dtype=np.int32)
    da_seg[0, :] = 5
    da_seg[1, :] = 2
    da_seg[2, :] = 7

    pair = _pair_factory(
        tmp_path,
        donor_lifetime_arr=donor,
        da_lifetime_arr=da,
        donor_seg=donor_seg,
        da_seg=da_seg,
    )
    config = FlimFretConfig(
        pairs=[pair], single_cell=True, output_parent=tmp_path
    )

    cell_ids = [r["cell_id"] for r in run_flim_fret(config).results[0].rows]
    assert cell_ids == [2, 5, 7]


def test_single_cell_skips_da_cell_with_empty_intersection(tmp_path):
    """DA cell entirely outside (mask AND phasor) emits NaN row, increments
    n_da_cells_skipped on every row of that pair."""
    donor = np.full((4, 4), 2.5, dtype=np.float32)
    donor_seg = np.zeros((4, 4), dtype=np.int32)
    donor_seg[0, :] = 1

    da = np.full((4, 4), 2.0, dtype=np.float32)
    da_seg = np.zeros((4, 4), dtype=np.int32)
    da_seg[0, :] = 1  # cell 1 — overlaps with mask
    da_seg[3, :] = 2  # cell 2 — outside mask intersection

    da_mask = np.zeros((4, 4), dtype=np.uint8)
    da_mask[:2, :] = 1  # restricts mask to top half
    phasor = _ones((4, 4))

    pair = _pair_factory(
        tmp_path,
        donor_lifetime_arr=donor,
        da_lifetime_arr=da,
        donor_seg=donor_seg,
        da_seg=da_seg,
        da_mask=da_mask,
        donor_phasor=phasor,
        da_phasor=phasor,
    )
    config = FlimFretConfig(
        pairs=[pair], single_cell=True, output_parent=tmp_path
    )

    result = run_flim_fret(config).results[0]
    assert len(result.rows) == 2  # one valid, one NaN
    rows_by_cell = {r["cell_id"]: r for r in result.rows}
    assert rows_by_cell[1]["da_mean_lifetime"] == pytest.approx(2.0)
    assert math.isnan(rows_by_cell[2]["da_mean_lifetime"])
    assert math.isnan(rows_by_cell[2]["fret_efficiency"])
    assert rows_by_cell[2]["n_pixels_da"] == 0
    # n_da_cells_skipped is per-pair; same value on every row of this pair
    assert rows_by_cell[1]["n_da_cells_skipped"] == 1
    assert rows_by_cell[2]["n_da_cells_skipped"] == 1


def test_single_cell_donor_reference_empty(tmp_path):
    """Donor has cells but none survive mask intersection → empty pool.

    Expected: status DONOR_REFERENCE_EMPTY, every DA cell row gets NaN
    donor and NaN FRET, run continues.
    """
    donor = np.full((4, 4), 2.5, dtype=np.float32)
    donor_seg = np.zeros((4, 4), dtype=np.int32)
    donor_seg[3, :] = 1  # cell 1 in row 3 only
    # Mask excludes row 3 entirely.
    donor_mask = np.zeros((4, 4), dtype=np.uint8)
    donor_mask[:3, :] = 1

    da = np.full((4, 4), 2.0, dtype=np.float32)
    da_seg = np.zeros((4, 4), dtype=np.int32)
    da_seg[0, :] = 1

    pair = _pair_factory(
        tmp_path,
        donor_lifetime_arr=donor,
        da_lifetime_arr=da,
        donor_seg=donor_seg,
        da_seg=da_seg,
        donor_mask=donor_mask,
    )
    config = FlimFretConfig(
        pairs=[pair], single_cell=True, output_parent=tmp_path
    )

    result = run_flim_fret(config).results[0]
    assert result.status is FlimFretStatus.DONOR_REFERENCE_EMPTY
    assert len(result.rows) == 1  # one DA cell
    assert math.isnan(result.rows[0]["donor_mean_lifetime"])
    assert math.isnan(result.rows[0]["fret_efficiency"])
    assert result.rows[0]["n_cells_donor_reference"] == 0


# ── Math edge cases ────────────────────────────────────────


def test_negative_da_yields_fret_above_one(tmp_path):
    """No clamping: da < 0 with donor > 0 gives FRET > 1, reported as-is."""
    donor = np.full((4, 4), 2.0, dtype=np.float32)
    da = np.full((4, 4), -1.0, dtype=np.float32)
    pair = _pair_factory(tmp_path, donor_lifetime_arr=donor, da_lifetime_arr=da)
    config = FlimFretConfig(
        pairs=[pair], single_cell=False, output_parent=tmp_path
    )

    row = run_flim_fret(config).results[0].rows[0]
    assert row["fret_efficiency"] == pytest.approx(1.5)  # 1 - (-1/2)


def test_negative_donor_yields_fret_as_is_not_nan(tmp_path):
    """Negative donor with positive DA → FRET = 1 - (DA/D), no clamping.

    Plan decision: only NaN-gate on donor == 0 or NaN, not on donor < 0.
    """
    donor = np.full((4, 4), -2.0, dtype=np.float32)
    da = np.full((4, 4), 1.0, dtype=np.float32)
    pair = _pair_factory(tmp_path, donor_lifetime_arr=donor, da_lifetime_arr=da)
    config = FlimFretConfig(
        pairs=[pair], single_cell=False, output_parent=tmp_path
    )

    row = run_flim_fret(config).results[0].rows[0]
    assert not math.isnan(row["fret_efficiency"])
    assert row["fret_efficiency"] == pytest.approx(1.5)  # 1 - (1/-2)


def test_zero_donor_yields_nan_fret(tmp_path):
    donor = np.zeros((4, 4), dtype=np.float32)
    da = np.full((4, 4), 1.0, dtype=np.float32)
    pair = _pair_factory(tmp_path, donor_lifetime_arr=donor, da_lifetime_arr=da)
    config = FlimFretConfig(
        pairs=[pair], single_cell=False, output_parent=tmp_path
    )

    row = run_flim_fret(config).results[0].rows[0]
    assert math.isnan(row["fret_efficiency"])
    assert row["donor_mean_lifetime"] == pytest.approx(0.0)


# ── Error paths ────────────────────────────────────────────


def test_missing_layer_logged_and_pair_skipped(tmp_path):
    pair = _pair_factory(
        tmp_path,
        donor_lifetime_arr=np.full((4, 4), 2.5, dtype=np.float32),
        da_lifetime_arr=np.full((4, 4), 2.0, dtype=np.float32),
    )
    # Mutate the pair config to reference a layer that doesn't exist.
    bad_pair = FlimFretPair(
        name=pair.name,
        donor_h5=pair.donor_h5,
        da_h5=pair.da_h5,
        donor_mask="not_there_mask",
        donor_phasor=pair.donor_phasor,
        donor_lifetime=pair.donor_lifetime,
        da_mask=pair.da_mask,
        da_phasor=pair.da_phasor,
        da_lifetime=pair.da_lifetime,
    )
    config = FlimFretConfig(
        pairs=[bad_pair], single_cell=False, output_parent=tmp_path
    )

    result = run_flim_fret(config).results[0]
    assert result.status is FlimFretStatus.MISSING_LAYER
    assert "not_there_mask" in (result.reason or "")
    assert result.rows == []  # no rows emitted for missing-layer pair


def test_dataset_open_failure_logged(tmp_path):
    # Donor exists, DA does not.
    pair = FlimFretPair(
        name="pair_1",
        donor_h5=_write_h5(
            tmp_path / "donor.h5",
            intensity=np.ones((1, 4, 4), dtype=np.float32),
            channel_names=["ch0_unfiltered_lifetime"],
            masks={
                "cells_mask": _ones((4, 4)),
                "phasor_ch0_1_phasor": _ones((4, 4)),
            },
        ),
        da_h5=tmp_path / "missing.h5",
        donor_mask="cells_mask",
        donor_phasor="phasor_ch0_1_phasor",
        donor_lifetime="ch0_unfiltered_lifetime",
        da_mask="cells_mask",
        da_phasor="phasor_ch0_1_phasor",
        da_lifetime="ch0_unfiltered_lifetime",
    )
    config = FlimFretConfig(
        pairs=[pair], single_cell=False, output_parent=tmp_path
    )

    result = run_flim_fret(config).results[0]
    # validate_pair_layers catches it first as a generic open failure → MISSING_LAYER
    assert result.status is FlimFretStatus.MISSING_LAYER


def test_time_lapse_intensity_rejected_at_runtime(tmp_path):
    """A 4D /intensity at the donor side → status=missing_layer (caught by validation)."""
    donor = _write_h5(
        tmp_path / "donor.h5",
        intensity=np.ones((2, 1, 4, 4), dtype=np.float32),  # (T, C, H, W)
        channel_names=["ch0_unfiltered_lifetime"],
        masks={
            "cells_mask": _ones((4, 4)),
            "phasor_ch0_1_phasor": _ones((4, 4)),
        },
    )
    da = _write_h5(
        tmp_path / "da.h5",
        intensity=np.ones((1, 4, 4), dtype=np.float32),
        channel_names=["ch0_unfiltered_lifetime"],
        masks={
            "cells_mask": _ones((4, 4)),
            "phasor_ch0_1_phasor": _ones((4, 4)),
        },
    )
    pair = FlimFretPair(
        name="pair_1",
        donor_h5=donor,
        da_h5=da,
        donor_mask="cells_mask",
        donor_phasor="phasor_ch0_1_phasor",
        donor_lifetime="ch0_unfiltered_lifetime",
        da_mask="cells_mask",
        da_phasor="phasor_ch0_1_phasor",
        da_lifetime="ch0_unfiltered_lifetime",
    )
    config = FlimFretConfig(
        pairs=[pair], single_cell=False, output_parent=tmp_path
    )

    result = run_flim_fret(config).results[0]
    assert result.status is FlimFretStatus.MISSING_LAYER
    assert "time-lapse" in (result.reason or "")


# ── Cancellation and progress callbacks ─────────────────────


def test_cancel_check_aborts_remaining_pairs(tmp_path):
    """Cancel returns True after pair 1 → pair 2 and 3 marked cancelled."""
    pairs = []
    for i in range(3):
        pairs.append(
            _pair_factory(
                tmp_path,
                donor_lifetime_arr=np.full((4, 4), 2.5, dtype=np.float32),
                da_lifetime_arr=np.full((4, 4), 2.0, dtype=np.float32),
                name=f"pair_{i + 1}",
            )
        )
    config = FlimFretConfig(
        pairs=pairs, single_cell=False, output_parent=tmp_path
    )

    n_calls = {"count": 0}

    def cancel_after_first() -> bool:
        n_calls["count"] += 1
        return n_calls["count"] > 1  # cancel after pair_1's pre-loop check

    report = run_flim_fret(config, cancel_check=cancel_after_first)
    statuses = [r.status for r in report.results]
    assert statuses[0] is FlimFretStatus.SUCCEEDED
    assert statuses[1] is FlimFretStatus.CANCELLED
    assert statuses[2] is FlimFretStatus.CANCELLED


def test_progress_callback_invoked_once_per_pair(tmp_path):
    pairs = [
        _pair_factory(
            tmp_path,
            donor_lifetime_arr=np.full((4, 4), 2.5, dtype=np.float32),
            da_lifetime_arr=np.full((4, 4), 2.0, dtype=np.float32),
            name=f"pair_{i + 1}",
        )
        for i in range(2)
    ]
    config = FlimFretConfig(
        pairs=pairs, single_cell=False, output_parent=tmp_path
    )

    received: list[tuple[str, FlimFretStatus]] = []

    def cb(pair, result: FlimFretPairResult) -> None:
        received.append((pair.name, result.status))

    run_flim_fret(config, progress_callback=cb)
    assert received == [
        ("pair_1", FlimFretStatus.SUCCEEDED),
        ("pair_2", FlimFretStatus.SUCCEEDED),
    ]


def test_run_log_records_events(tmp_path):
    pair = _pair_factory(
        tmp_path,
        donor_lifetime_arr=np.full((4, 4), 2.5, dtype=np.float32),
        da_lifetime_arr=np.full((4, 4), 2.0, dtype=np.float32),
    )
    config = FlimFretConfig(
        pairs=[pair], single_cell=False, output_parent=tmp_path
    )
    log_folder = tmp_path / "log_folder"
    log_folder.mkdir()
    log = RunLog(log_folder)

    run_flim_fret(config, run_log=log)

    # Read the jsonl back and assert key events are present.
    import json

    events = [
        json.loads(line)
        for line in log.path.read_text().splitlines()
        if line.strip()
    ]
    event_names = [e["event"] for e in events]
    assert "run_started" in event_names
    assert "pair_started" in event_names
    assert "pair_done" in event_names
    assert "run_finished" in event_names


# ── Multiple pairs / report shape ───────────────────────────


def test_multiple_pairs_concatenate_rows(tmp_path):
    """Whole-field, two pairs → two rows total, one per pair."""
    pairs = [
        _pair_factory(
            tmp_path,
            donor_lifetime_arr=np.full((4, 4), 2.5, dtype=np.float32),
            da_lifetime_arr=np.full((4, 4), 2.0, dtype=np.float32),
            name=f"pair_{i + 1}",
        )
        for i in range(2)
    ]
    config = FlimFretConfig(
        pairs=pairs, single_cell=False, output_parent=tmp_path
    )

    report = run_flim_fret(config)
    assert len(report.results) == 2
    all_pair_names = [r.rows[0]["pair_name"] for r in report.results]
    assert all_pair_names == ["pair_1", "pair_2"]


def test_orchestrator_is_qt_free(tmp_path):
    """Smoke test: importing the orchestrator does not import Qt."""
    import sys

    import percell4.application.use_cases.run_flim_fret  # noqa: F401

    qt_modules = [
        m for m in sys.modules if m.startswith(("qtpy", "PyQt5", "napari"))
    ]
    # Other tests may have imported Qt indirectly. Just assert the module
    # itself has no Qt imports.
    src = Path(percell4.application.use_cases.run_flim_fret.__file__).read_text()
    assert "qtpy" not in src
    assert "PyQt5" not in src
    assert "napari" not in src
