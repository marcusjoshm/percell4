"""Pure dataclass + JSON tests for PhasorROI / GMMFit (U4).

No Qt fixtures — these tests exercise the dataclass round-trip path
(``to_dict``/``from_dict``) and the schema_version handling. The
``_on_load_rois`` Qt path is exercised separately in U5's GUI workflow
test suite.
"""

from __future__ import annotations

import json

import pytest

from percell4.interfaces.gui.peer_views.phasor_plot import (
    GMMFit,
    PhasorROI,
    ROI_JSON_SCHEMA_VERSION,
)


def _make_gmm_fit() -> GMMFit:
    return GMMFit(
        mean_g=0.4, mean_s=0.3,
        lambda_major=0.05, lambda_minor=0.01,
        principal_angle_rad=0.524,  # ~30°
        cov_f=2.0, shift=0.0,
        shape="ellipse",
        criterion="BIC",
        sampled_pixels=100_000,
    )


# ── Round-trip happy paths ────────────────────────────────────────────


def test_manual_roi_round_trip_preserves_origin_and_no_gmm_fit():
    roi = PhasorROI(
        name="A", center=(0.4, 0.3), radii=(0.05, 0.05),
        angle_deg=0.0, label=1, color="#3498db",
    )
    d = roi.to_dict()
    out = PhasorROI.from_dict(d, label=1, default_color="#3498db")
    assert out.name == "A"
    assert out.origin == "manual"
    assert out.gmm_fit is None


def test_gmm_roi_round_trip_preserves_full_gmm_fit():
    fit = _make_gmm_fit()
    roi = PhasorROI(
        name="GMM_1", center=(0.4, 0.3), radii=(0.05, 0.01),
        angle_deg=30.0, label=1, color="#e74c3c",
        origin="gmm", gmm_fit=fit,
    )
    d = roi.to_dict()
    # JSON-encode then decode to ensure no Python-only objects survive
    blob = json.loads(json.dumps(d))
    out = PhasorROI.from_dict(blob, label=1, default_color="#e74c3c")

    assert out.origin == "gmm"
    assert out.gmm_fit is not None
    assert out.gmm_fit.mean_g == pytest.approx(fit.mean_g)
    assert out.gmm_fit.mean_s == pytest.approx(fit.mean_s)
    assert out.gmm_fit.lambda_major == pytest.approx(fit.lambda_major)
    assert out.gmm_fit.lambda_minor == pytest.approx(fit.lambda_minor)
    assert out.gmm_fit.principal_angle_rad == pytest.approx(fit.principal_angle_rad)
    assert out.gmm_fit.cov_f == pytest.approx(fit.cov_f)
    assert out.gmm_fit.shift == pytest.approx(fit.shift)
    assert out.gmm_fit.shape == fit.shape
    assert out.gmm_fit.criterion == fit.criterion
    assert out.gmm_fit.sampled_pixels == fit.sampled_pixels


def test_schema_version_constant_is_two():
    """Sanity check that the constant matches the plan."""
    assert ROI_JSON_SCHEMA_VERSION == 2


# ── Backward-compat: v1 JSON (no schema_version, no origin) ───────────


def test_v1_json_with_no_origin_loads_as_manual():
    """Pre-feature JSON: no origin, no gmm_fit, no schema_version."""
    v1_blob = {
        "name": "A",
        "center": [0.4, 0.3],
        "radii": [0.05, 0.05],
        "angle_deg": 0.0,
        "color": "#3498db",
    }
    roi = PhasorROI.from_dict(v1_blob, label=1, default_color="#000000")
    assert roi.origin == "manual"
    assert roi.gmm_fit is None
    assert roi.color == "#3498db"


def test_origin_gmm_with_no_gmm_fit_is_tolerant():
    """origin=gmm but gmm_fit absent — UI must defend by hiding cov_f/shift."""
    blob = {
        "name": "GMM_1",
        "center": [0.4, 0.3],
        "radii": [0.05, 0.01],
        "angle_deg": 30.0,
        "color": "#e74c3c",
        "origin": "gmm",
        # gmm_fit field absent
    }
    roi = PhasorROI.from_dict(blob, label=1, default_color="#000000")
    assert roi.origin == "gmm"
    assert roi.gmm_fit is None


def test_malformed_gmm_fit_demoted_to_none_without_failing_load():
    """A bad gmm_fit payload should not poison the rest of the ROI."""
    blob = {
        "name": "GMM_1",
        "center": [0.4, 0.3],
        "radii": [0.05, 0.01],
        "angle_deg": 30.0,
        "color": "#e74c3c",
        "origin": "gmm",
        "gmm_fit": {"mean_g": 0.4},  # missing required fields
    }
    roi = PhasorROI.from_dict(blob, label=1, default_color="#000000")
    assert roi.origin == "gmm"
    assert roi.gmm_fit is None
    assert roi.name == "GMM_1"


def test_missing_required_fields_raises():
    """Missing center or radii is a hard error (existing behavior preserved)."""
    with pytest.raises(ValueError, match="Invalid ROI data"):
        PhasorROI.from_dict({"name": "X"}, label=1, default_color="#000000")


# ── GMMFit standalone round-trip ──────────────────────────────────────


def test_gmm_fit_standalone_round_trip():
    fit = _make_gmm_fit()
    out = GMMFit.from_dict(fit.to_dict())
    assert out == fit


def test_gmm_fit_missing_field_raises():
    bad = {"mean_g": 0.4, "mean_s": 0.3}
    with pytest.raises(ValueError, match="Invalid gmm_fit data"):
        GMMFit.from_dict(bad)


def test_gmm_fit_criterion_none_round_trips():
    fit = GMMFit(
        mean_g=0.4, mean_s=0.3,
        lambda_major=0.05, lambda_minor=0.01,
        principal_angle_rad=0.0,
        cov_f=2.0, shift=0.0,
        shape="ellipse",
        criterion=None,
        sampled_pixels=50_000,
    )
    out = GMMFit.from_dict(json.loads(json.dumps(fit.to_dict())))
    assert out.criterion is None
