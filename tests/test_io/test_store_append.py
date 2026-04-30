"""Tests for DatasetStore.append_decay_layers (U2 of TCSPC append thread)."""

from __future__ import annotations

import h5py
import numpy as np
import pytest

from percell4.domain.io.cross_format import deserialize_rule, serialize_rule
from percell4.domain.io.models import (
    BaseStemRule,
    CompositeRule,
    ExplicitRule,
    ProvenanceRecord,
    ZeroPadOffsetRule,
)
from percell4.store import (
    CrossFormatRuleConflict,
    DatasetStore,
    LayerAlreadyExists,
)


def _make_store_with_intensity(tmp_path):
    """Create an .h5 with /intensity and channel_names metadata."""
    store = DatasetStore(tmp_path / "experiment.h5")
    store.create(metadata={"channel_names": ["ch00", "ch01"]})
    intensity = np.zeros((2, 32, 32), dtype=np.uint16)
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    return store


def _provenance(channel_name: str, rule_type: str = "ZeroPadOffsetRule") -> ProvenanceRecord:
    return ProvenanceRecord(
        source_path=f"/abs/{channel_name}.bin",
        cross_format_rule=rule_type,
        match_evidence='{"kind": "tokens", "bin_token": "1", "intensity_token": "00"}',
        manually_overridden=False,
        importer_version="0.1.0",
        timestamp_utc="2026-04-29T20:00:00+00:00",
        content_sha256="0" * 64,
    )


# ── Happy paths ─────────────────────────────────────────────────────────


def test_append_creates_decay_and_provenance(tmp_path):
    """Single channel append creates /decay/<name> and /provenance/decay/<name>."""
    store = _make_store_with_intensity(tmp_path)
    decay = np.ones((32, 32, 16), dtype=np.uint16)

    n = store.append_decay_layers(
        layers={"ch00": decay},
        provenance={"ch00": _provenance("ch00")},
    )

    assert n == 1
    with h5py.File(store.path, "r") as f:
        assert "decay/ch00" in f
        assert f["decay/ch00"].shape == (32, 32, 16)
        assert f["decay/ch00"].dtype == np.uint16
        prov = f["provenance/decay/ch00"].attrs
        assert prov["source_path"] == "/abs/ch00.bin"
        assert prov["cross_format_rule"] == "ZeroPadOffsetRule"
        assert prov["manually_overridden"] is np.False_ or prov["manually_overridden"] is False
        assert "match_evidence" in prov
        assert "content_sha256" in prov


def test_append_writes_two_channels_in_one_call(tmp_path):
    """Multiple channels in a single call all land with their provenance."""
    store = _make_store_with_intensity(tmp_path)
    decay0 = np.zeros((32, 32, 16), dtype=np.uint16)
    decay1 = np.ones((32, 32, 16), dtype=np.uint16)

    n = store.append_decay_layers(
        layers={"ch00": decay0, "ch01": decay1},
        provenance={"ch00": _provenance("ch00"), "ch01": _provenance("ch01")},
    )

    assert n == 2
    with h5py.File(store.path, "r") as f:
        assert "decay/ch00" in f and "decay/ch01" in f
        assert "provenance/decay/ch00" in f and "provenance/decay/ch01" in f


def test_append_empty_layers_is_noop(tmp_path):
    """Empty layers dict writes nothing, returns 0."""
    store = _make_store_with_intensity(tmp_path)

    n = store.append_decay_layers(layers={}, provenance={})

    assert n == 0
    with h5py.File(store.path, "r") as f:
        assert "decay" not in f


# ── Idempotency ─────────────────────────────────────────────────────────


def test_append_raises_on_existing_decay_without_force(tmp_path):
    """Re-append to an existing /decay/<name> raises LayerAlreadyExists."""
    store = _make_store_with_intensity(tmp_path)
    decay = np.zeros((32, 32, 16), dtype=np.uint16)
    store.append_decay_layers({"ch00": decay}, {"ch00": _provenance("ch00")})

    with pytest.raises(LayerAlreadyExists):
        store.append_decay_layers({"ch00": decay}, {"ch00": _provenance("ch00")})


def test_append_force_overwrites(tmp_path):
    """force=True replaces an existing /decay/<name>."""
    store = _make_store_with_intensity(tmp_path)
    old = np.zeros((32, 32, 16), dtype=np.uint16)
    new = np.full((32, 32, 16), 7, dtype=np.uint16)
    store.append_decay_layers({"ch00": old}, {"ch00": _provenance("ch00")})

    store.append_decay_layers(
        {"ch00": new}, {"ch00": _provenance("ch00")}, force=True
    )

    with h5py.File(store.path, "r") as f:
        assert int(f["decay/ch00"][0, 0, 0]) == 7


def test_append_provenance_missing_channel_raises(tmp_path):
    """layers and provenance dicts must agree on channel names."""
    store = _make_store_with_intensity(tmp_path)
    decay = np.zeros((32, 32, 16), dtype=np.uint16)

    with pytest.raises(ValueError, match="provenance"):
        store.append_decay_layers({"ch00": decay}, provenance={})


# ── Cross-format rule persistence ───────────────────────────────────────


def test_append_persists_cross_format_rule(tmp_path):
    """First append writes /metadata.attrs[cross_format_rule] from cross_format_rule arg."""
    store = _make_store_with_intensity(tmp_path)
    decay = np.zeros((32, 32, 16), dtype=np.uint16)
    rule = CompositeRule(rules=(ZeroPadOffsetRule(2, 1), BaseStemRule()))

    store.append_decay_layers(
        {"ch00": decay},
        {"ch00": _provenance("ch00")},
        cross_format_rule=rule,
    )

    persisted = store.metadata.get("cross_format_rule")
    assert persisted is not None
    rt = deserialize_rule(persisted)
    assert rt == rule


def test_append_same_rule_does_not_overwrite_metadata(tmp_path):
    """Second call with the same rule is fine — /metadata.attrs unchanged."""
    store = _make_store_with_intensity(tmp_path)
    decay = np.zeros((32, 32, 16), dtype=np.uint16)
    rule = ZeroPadOffsetRule(pad_width=2, offset=1)

    store.append_decay_layers(
        {"ch00": decay}, {"ch00": _provenance("ch00")}, cross_format_rule=rule
    )
    # Second append with a different channel and the SAME rule
    store.append_decay_layers(
        {"ch01": decay}, {"ch01": _provenance("ch01")}, cross_format_rule=rule
    )

    rt = deserialize_rule(store.metadata["cross_format_rule"])
    assert rt == rule


def test_append_different_rule_raises_conflict(tmp_path):
    """Second call with a DIFFERENT rule raises CrossFormatRuleConflict."""
    store = _make_store_with_intensity(tmp_path)
    decay = np.zeros((32, 32, 16), dtype=np.uint16)
    rule_a = ZeroPadOffsetRule(pad_width=2, offset=1)
    rule_b = ZeroPadOffsetRule(pad_width=0, offset=0)
    store.append_decay_layers(
        {"ch00": decay}, {"ch00": _provenance("ch00")}, cross_format_rule=rule_a
    )

    with pytest.raises(CrossFormatRuleConflict):
        store.append_decay_layers(
            {"ch01": decay},
            {"ch01": _provenance("ch01")},
            cross_format_rule=rule_b,
        )


def test_append_force_overwrites_rule_metadata(tmp_path):
    """force=True allows the persisted rule to be replaced."""
    store = _make_store_with_intensity(tmp_path)
    decay = np.zeros((32, 32, 16), dtype=np.uint16)
    rule_a = ZeroPadOffsetRule(pad_width=2, offset=1)
    rule_b = ZeroPadOffsetRule(pad_width=0, offset=0)
    store.append_decay_layers(
        {"ch00": decay}, {"ch00": _provenance("ch00")}, cross_format_rule=rule_a
    )

    store.append_decay_layers(
        {"ch00": decay},
        {"ch00": _provenance("ch00")},
        cross_format_rule=rule_b,
        force=True,
    )

    rt = deserialize_rule(store.metadata["cross_format_rule"])
    assert rt == rule_b


# ── ExplicitRule does not trigger conflict ──────────────────────────────


def test_explicit_rule_does_not_conflict_with_persisted(tmp_path):
    """Per the plan: ExplicitRule (per-binding override) does NOT conflict
    with a persisted dropdown-level rule. The dropdown rule stays
    authoritative; ExplicitRule signals the user manually adjusted bindings,
    not that they chose a different base rule."""
    store = _make_store_with_intensity(tmp_path)
    decay = np.zeros((32, 32, 16), dtype=np.uint16)
    base_rule = ZeroPadOffsetRule(pad_width=2, offset=1)
    explicit = ExplicitRule(mapping=(("/abs/x.bin", "ch00"),))
    store.append_decay_layers(
        {"ch00": decay}, {"ch00": _provenance("ch00")}, cross_format_rule=base_rule
    )

    # Should NOT raise
    store.append_decay_layers(
        {"ch01": decay},
        {"ch01": _provenance("ch01")},
        cross_format_rule=explicit,
    )

    # Persisted rule unchanged
    rt = deserialize_rule(store.metadata["cross_format_rule"])
    assert rt == base_rule


# ── Rule serialization roundtrip ────────────────────────────────────────


@pytest.mark.parametrize("rule", [
    ZeroPadOffsetRule(pad_width=2, offset=1),
    ZeroPadOffsetRule(pad_width=0, offset=0),
    BaseStemRule(),
    ExplicitRule(mapping=(("/abs/x.bin", "ch00"), ("/abs/y.bin", "ch01"))),
    CompositeRule(rules=(ZeroPadOffsetRule(2, 1), BaseStemRule())),
])
def test_rule_serialization_roundtrip(rule):
    """serialize_rule + deserialize_rule preserves equality for every variant."""
    s = serialize_rule(rule)
    assert isinstance(s, str)
    rt = deserialize_rule(s)
    assert rt == rule
