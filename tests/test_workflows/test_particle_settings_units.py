"""ParticleSettings carries a min_area unit alongside the value.

The workflow phase resolves µm² → per-dataset px at runtime using each
dataset's ``pixel_size_um``; the model layer only validates schema. JSON
round-trip preserves both fields, and legacy ``run_config.json`` files
written before the unit selector (carrying only ``"min_area": <int>``)
load cleanly as px mode.
"""

from __future__ import annotations

import pytest

from percell4.workflows.artifacts import (
    _particle_from_dict,
    _particle_to_dict,
)
from percell4.workflows.models import ParticleSettings

# ── Defaults ──────────────────────────────────────────────────────────


def test_default_min_area_is_zero_px():
    s = ParticleSettings()
    assert s.min_area == 0.0
    assert s.min_area_unit == "px"


# ── Constructor validation ────────────────────────────────────────────


def test_negative_min_area_rejected():
    with pytest.raises(ValueError, match="min_area must be >= 0"):
        ParticleSettings(min_area=-1.0)


def test_unknown_unit_rejected():
    with pytest.raises(ValueError, match="min_area_unit"):
        ParticleSettings(min_area=1.0, min_area_unit="pixels")


def test_um2_unit_accepted():
    s = ParticleSettings(min_area=0.5, min_area_unit="um2")
    assert s.min_area_unit == "um2"
    assert s.min_area == pytest.approx(0.5)


# ── JSON round-trip (current schema) ──────────────────────────────────


def test_to_dict_includes_both_fields():
    s = ParticleSettings(min_area=2.5, min_area_unit="um2")
    d = _particle_to_dict(s)
    assert d == {"min_area": 2.5, "min_area_unit": "um2"}


def test_dict_round_trip_um2():
    s = ParticleSettings(min_area=0.5, min_area_unit="um2")
    out = _particle_from_dict(_particle_to_dict(s))
    assert out == s


def test_dict_round_trip_px():
    s = ParticleSettings(min_area=10.0, min_area_unit="px")
    out = _particle_from_dict(_particle_to_dict(s))
    assert out == s


# ── Backwards-compat: legacy schema with bare int min_area ────────────


def test_loads_legacy_min_area_only_as_px():
    """Legacy run_config.json predates the unit field; treat it as px."""
    legacy = {"min_area": 10}
    out = _particle_from_dict(legacy)
    assert out == ParticleSettings(min_area=10.0, min_area_unit="px")


def test_loads_legacy_with_float_min_area():
    """Hand-edited legacy config might already use a float."""
    legacy = {"min_area": 12.5}
    out = _particle_from_dict(legacy)
    assert out == ParticleSettings(min_area=12.5, min_area_unit="px")


def test_loads_empty_dict_as_defaults():
    out = _particle_from_dict({})
    assert out == ParticleSettings()


def test_malformed_unit_raises_at_load():
    """A hand-edited run_config with an invalid unit string fails at load
    rather than silently coercing to px or running the workflow with
    undefined semantics."""
    with pytest.raises(ValueError, match="min_area_unit"):
        _particle_from_dict({"min_area": 5, "min_area_unit": "pixels"})
