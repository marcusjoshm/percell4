"""Tests for the puncta-detection validation harness (plan U5).

Small synthetic fields with GT known by construction. The harness reuses the
real detection path (``_apply_threshold_frame``), so these also exercise the
LoG/DoG detectors end-to-end through the scoring layer.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from percell4.domain.measure.grouper import GroupingResult
from percell4.workflows.models import PunctaDetectorSettings, ThresholdingRound
from percell4.workflows.puncta_validation import (
    GridPoint,
    LabeledField,
    ValidationReport,
    load_tier_a_csv,
    run_validation,
)

H = W = 64


def _field_image(spots, *, bg=100.0, noise=2.0, seed=0):
    rng = np.random.RandomState(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    img = np.full((H, W), bg) + rng.normal(0, noise, (H, W))
    for y, x, amp, s in spots:
        img += amp * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * s * s))
    return img


def _one_cell_labels():
    labels = np.zeros((H, W), dtype=np.int32)
    labels[8:56, 8:56] = 1
    return labels


def _one_group():
    return GroupingResult(group_assignments=pd.Series({1: 1}), n_groups=1, group_means=[1.0])


def _make_field(name, spots, **kw):
    return LabeledField(
        name=name,
        image=_field_image(spots, **kw),
        labels=_one_cell_labels(),
        grouping=_one_group(),
    )


# Four well-separated foci of mixed brightness; LoG at tr=0.05 recovers all.
_SPOTS = [(20, 20, 200, 2.0), (20, 44, 120, 1.6), (44, 20, 150, 2.5), (44, 44, 90, 1.4)]
_GT = np.array([[y, x] for y, x, *_ in _SPOTS], dtype=np.float64)


def _qualifying_grid():
    return [
        GridPoint(
            detector_name="log",
            background_estimator_name="gaussian-peak",
            k=2.5,
            tol=4.0,
            scale_range=(1.0, 4.0),
            min_spot_px=2,
            extra_params=(("threshold_rel", 0.05),),
        )
    ]


# ── Ranked table + lock ─────────────────────────────────────────────────────


def test_run_validation_returns_ranked_table_and_locks():
    fields = [_make_field("f1", _SPOTS)]
    gt = {"f1": _GT}
    report = run_validation(fields, gt, _qualifying_grid(), precision_floor=0.9)
    assert isinstance(report, ValidationReport)
    assert len(report.ranked) == 1
    row = report.ranked[0]
    assert row["recall"] == pytest.approx(1.0)
    assert row["precision"] == pytest.approx(1.0)
    assert row["stable"] is True
    assert report.keep_qc is False
    assert report.locked_settings is not None


def test_locked_settings_validate_against_thresholding_round():
    fields = [_make_field("f1", _SPOTS)]
    report = run_validation(fields, {"f1": _GT}, _qualifying_grid())
    locked = report.locked_settings
    assert isinstance(locked, PunctaDetectorSettings)
    # Must be a valid PunctaDetectorSettings on a ThresholdingRound (no raise).
    rnd = ThresholdingRound(
        name="r1",
        channel="GFP",
        metric="mean_intensity",
        algorithm="kmeans",
        puncta=locked,
    )
    assert rnd.puncta is locked


# ── No method clears the bar -> keep QC ─────────────────────────────────────


def test_no_method_clears_precision_floor_keeps_qc():
    # GT has only 2 real foci; a permissive DoG floods extra noise components,
    # so precision drops below the 0.9 floor and nothing qualifies.
    spots = [(20, 20, 200, 2.0), (44, 44, 90, 1.4)]
    gt = {"f1": np.array([[20, 20], [44, 44]], float)}
    fields = [
        LabeledField(
            name="f1",
            image=_field_image(spots),
            labels=_one_cell_labels(),
            grouping=_one_group(),
        )
    ]
    grid = [
        GridPoint(
            detector_name="dog",
            background_estimator_name="gaussian-peak",
            k=1.0,
            tol=4.0,
            scale_range=(1.0, 4.0),
            min_spot_px=1,
            extra_params=(("threshold_rel", 0.01),),
        )
    ]
    report = run_validation(fields, gt, grid, precision_floor=0.9)
    assert report.locked_settings is None
    assert report.keep_qc is True
    assert report.ranked[0]["precision"] < 0.9
    assert "keep interactive QC" in report.reason


def test_tier_b_recall_floor_unreachable_keeps_qc():
    fields = [_make_field("f1", _SPOTS)]
    # A floor above 1.0 can never be met -> keep QC even with perfect detection.
    report = run_validation(fields, {"f1": _GT}, _qualifying_grid(), tier_b_recall=1.5)
    assert report.locked_settings is None
    assert report.keep_qc is True
    assert "Tier-B floor" in report.reason


# ── New-true-positive not penalized (scored vs Tier A, not Tier B) ──────────


def test_new_true_positive_not_penalized():
    # The candidate finds all 4 foci. Tier-A GT confirms all 4. There is no
    # Tier-B mask here, but the point is: a focus the old mask might lack but
    # Tier A confirms still scores as a TP (precision stays 1.0). The harness
    # only ever scores precision/FP against Tier A.
    fields = [_make_field("f1", _SPOTS)]
    report = run_validation(fields, {"f1": _GT}, _qualifying_grid())
    assert report.ranked[0]["precision"] == pytest.approx(1.0)
    assert report.ranked[0]["recall"] == pytest.approx(1.0)


# ── Determinism: shuffled field order -> identical micro scores ─────────────


def test_determinism_field_order_invariant():
    f1 = _make_field("f1", _SPOTS, seed=1)
    f2 = _make_field("f2", _SPOTS, seed=2)
    gt = {"f1": _GT, "f2": _GT}
    grid = _qualifying_grid()

    r_forward = run_validation([f1, f2], gt, grid)
    r_reverse = run_validation([f2, f1], gt, grid)

    a = r_forward.ranked[0]
    b = r_reverse.ranked[0]
    assert a["recall"] == b["recall"]
    assert a["precision"] == b["precision"]
    assert a["f_beta"] == b["f_beta"]
    assert r_forward.locked_settings == r_reverse.locked_settings


# ── Loader: per-field CSVs grouped by stem ──────────────────────────────────


def test_load_tier_a_csv_by_stem(tmp_path):
    p1 = tmp_path / "fieldA.csv"
    p1.write_text("y,x\n10,20\n30,40\n", encoding="utf-8")
    p2 = tmp_path / "fieldB.csv"
    p2.write_text("y,x\n5,5\n", encoding="utf-8")
    gt = load_tier_a_csv([p1, p2])
    assert set(gt) == {"fieldA", "fieldB"}
    assert gt["fieldA"].shape == (2, 2)
    assert np.allclose(gt["fieldA"][0], [10, 20])
    assert gt["fieldB"].shape == (1, 2)


def test_load_tier_a_csv_with_field_column(tmp_path):
    p = tmp_path / "all_points.csv"
    p.write_text("field,y,x\nf1,10,20\nf1,30,40\nf2,5,5\n", encoding="utf-8")
    gt = load_tier_a_csv([p])
    assert set(gt) == {"f1", "f2"}
    assert gt["f1"].shape == (2, 2)
    assert gt["f2"].shape == (1, 2)


def test_load_tier_a_csv_requires_y_x(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("row,col\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must have 'y' and 'x'"):
        load_tier_a_csv([p])
