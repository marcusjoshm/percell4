"""Tests for cross-format token matching (U1 of TCSPC append thread).

Covers all four CrossFormatRule variants and the pure function
``match_bin_to_intensity``.
"""

from __future__ import annotations

from pathlib import Path

from percell4.domain.io.cross_format import (
    IntensityChannel,
    MatchEvidence,
    MatchResult,
    match_bin_to_intensity,
)
from percell4.domain.io.models import (
    BaseStemRule,
    CompositeRule,
    ExplicitRule,
    TokenConfig,
    ZeroPadOffsetRule,
)


def _channels(*specs):
    """Build IntensityChannel list from (name, token) or (name, token, base_stem) tuples."""
    out = []
    for spec in specs:
        if len(spec) == 2:
            name, token = spec
            base_stem = None
        else:
            name, token, base_stem = spec
        out.append(IntensityChannel(name=name, token=token, base_stem=base_stem))
    return out


# ── ZeroPadOffsetRule ───────────────────────────────────────────────────


def test_zero_pad_offset_user_microscope():
    """User's microscope: TIFF s00_ch00 ↔ BIN s0_ch1 (pad_width=2, offset=1)."""
    rule = ZeroPadOffsetRule(pad_width=2, offset=1)
    bins = [Path("/data/exp_s0_ch1.bin"), Path("/data/exp_s0_ch2.bin")]
    intensity = _channels(("ch00", "00"), ("ch01", "01"))

    result = match_bin_to_intensity(bins, intensity, rule, TokenConfig())

    bindings = {b.bin_path: b.channel_name for b in result.bindings}
    assert bindings == {bins[0]: "ch00", bins[1]: "ch01"}
    assert result.unmatched == ()
    assert result.ambiguous == ()
    # Evidence captures the token-level transformation
    for b in result.bindings:
        assert b.evidence.kind == "tokens"
        assert b.evidence.bin_token is not None
        assert b.evidence.intensity_token is not None


def test_zero_pad_offset_zero_zero_is_exact_match():
    """ZeroPadOffsetRule(0, 0) is exact-token equality with no transformation."""
    rule = ZeroPadOffsetRule(pad_width=0, offset=0)
    bins = [Path("/data/exp_ch00.bin")]
    intensity = _channels(("ch00", "00"))

    result = match_bin_to_intensity(bins, intensity, rule, TokenConfig())

    assert {b.bin_path: b.channel_name for b in result.bindings} == {bins[0]: "ch00"}


def test_zero_pad_offset_no_bin_match_yields_unmatched():
    """When no .bin file matches, bindings empty + bins go to unmatched."""
    rule = ZeroPadOffsetRule(pad_width=2, offset=1)
    bins = [Path("/data/exp_s0_ch99.bin")]  # token 99 → 98, "98" — no TIFF
    intensity = _channels(("ch00", "00"), ("ch01", "01"))

    result = match_bin_to_intensity(bins, intensity, rule, TokenConfig())

    assert result.bindings == ()
    assert result.unmatched == (bins[0],)


def test_zero_pad_offset_bin_without_channel_token_unmatched():
    """A .bin file whose stem doesn't carry a channel token is unmatched (under pure ZeroPad)."""
    rule = ZeroPadOffsetRule(pad_width=2, offset=1)
    bins = [Path("/data/Dataset_A.bin")]  # no _ch token
    intensity = _channels(("ch00", "00"))

    result = match_bin_to_intensity(bins, intensity, rule, TokenConfig())

    assert result.bindings == ()
    assert result.unmatched == (bins[0],)


# ── BaseStemRule ────────────────────────────────────────────────────────


def test_base_stem_unambiguous():
    """BaseStemRule: DatasetA.bin matches when one channel's base stem equals it."""
    rule = BaseStemRule()
    bins = [Path("/data/Dataset_A.bin")]
    intensity = _channels(("ch00", "00", "Dataset_A"))

    result = match_bin_to_intensity(bins, intensity, rule, TokenConfig())

    assert {b.bin_path: b.channel_name for b in result.bindings} == {bins[0]: "ch00"}
    assert result.bindings[0].evidence.kind == "stem"


def test_base_stem_prefix_match():
    """BaseStemRule: a .bin stem that prefixes a TIFF base stem matches."""
    rule = BaseStemRule()
    bins = [Path("/data/Dataset_A.bin")]
    intensity = _channels(("ch00", "00", "Dataset_A_extra"))

    result = match_bin_to_intensity(bins, intensity, rule, TokenConfig())

    # Either-direction prefix match (matches existing importer behavior)
    assert len(result.bindings) == 1
    assert result.bindings[0].channel_name == "ch00"


def test_base_stem_ambiguous_when_two_channels_share_prefix():
    """BaseStemRule: a .bin stem matching multiple base stems → ambiguous, not a binding."""
    rule = BaseStemRule()
    bins = [Path("/data/Dataset.bin")]
    intensity = _channels(
        ("ch00", "00", "Dataset_A"),
        ("ch01", "01", "Dataset_B"),
    )

    result = match_bin_to_intensity(bins, intensity, rule, TokenConfig())

    # Both channels prefix-match "Dataset" → ambiguous
    assert result.bindings == ()
    assert len(result.ambiguous) == 1
    bin_path, candidates = result.ambiguous[0]
    assert bin_path == bins[0]
    assert set(candidates) == {"ch00", "ch01"}


def test_base_stem_no_match_unmatched():
    """BaseStemRule: a .bin stem with no shared prefix is unmatched."""
    rule = BaseStemRule()
    bins = [Path("/data/Other.bin")]
    intensity = _channels(("ch00", "00", "Dataset_A"))

    result = match_bin_to_intensity(bins, intensity, rule, TokenConfig())

    assert result.bindings == ()
    assert result.unmatched == (bins[0],)


# ── ExplicitRule ────────────────────────────────────────────────────────


def test_explicit_rule_returns_mapping_verbatim():
    """ExplicitRule: bindings come directly from the user-supplied mapping."""
    bin_path = Path("/data/x.bin")
    rule = ExplicitRule(mapping=((str(bin_path.resolve()), "ch07"),))
    intensity = _channels(("ch07", "07"))

    result = match_bin_to_intensity([bin_path], intensity, rule, TokenConfig())

    assert {b.bin_path: b.channel_name for b in result.bindings} == {bin_path: "ch07"}
    assert result.bindings[0].evidence.kind == "explicit"


def test_explicit_rule_ignores_tokens():
    """ExplicitRule: even if filename has different tokens, mapping wins."""
    bin_path = Path("/data/exp_ch99.bin")
    rule = ExplicitRule(mapping=((str(bin_path.resolve()), "ch00"),))
    intensity = _channels(("ch00", "00"))

    result = match_bin_to_intensity([bin_path], intensity, rule, TokenConfig())

    assert result.bindings[0].channel_name == "ch00"


def test_explicit_rule_unmapped_bin_unmatched():
    """ExplicitRule: a .bin file not in the mapping is unmatched."""
    bin_path = Path("/data/x.bin")
    other = Path("/data/y.bin")
    rule = ExplicitRule(mapping=((str(bin_path.resolve()), "ch00"),))
    intensity = _channels(("ch00", "00"))

    result = match_bin_to_intensity([bin_path, other], intensity, rule, TokenConfig())

    assert {b.bin_path for b in result.bindings} == {bin_path}
    assert result.unmatched == (other,)


# ── CompositeRule ───────────────────────────────────────────────────────


def test_composite_first_rule_wins():
    """CompositeRule: a .bin matched by the first rule does NOT fall through."""
    rule = CompositeRule(rules=(
        ZeroPadOffsetRule(pad_width=2, offset=1),
        BaseStemRule(),
    ))
    bins = [Path("/data/exp_s0_ch1.bin")]
    intensity = _channels(("ch00", "00", "exp_s0"))

    result = match_bin_to_intensity(bins, intensity, rule, TokenConfig())

    assert result.bindings[0].channel_name == "ch00"
    # Evidence carries token-kind, not stem-kind, because first rule won
    assert result.bindings[0].evidence.kind == "tokens"


def test_composite_falls_through_on_no_match():
    """CompositeRule: when first rule doesn't match, second rule is tried."""
    rule = CompositeRule(rules=(
        ZeroPadOffsetRule(pad_width=2, offset=1),
        BaseStemRule(),
    ))
    bins = [Path("/data/Dataset_A.bin")]  # no _ch token, but stem matches
    intensity = _channels(("ch00", "00", "Dataset_A"))

    result = match_bin_to_intensity(bins, intensity, rule, TokenConfig())

    assert result.bindings[0].channel_name == "ch00"
    assert result.bindings[0].evidence.kind == "stem"


def test_composite_with_no_rules_returns_all_unmatched():
    """CompositeRule with empty rule tuple: every bin is unmatched."""
    rule = CompositeRule(rules=())
    bins = [Path("/data/x.bin")]
    intensity = _channels(("ch00", "00"))

    result = match_bin_to_intensity(bins, intensity, rule, TokenConfig())

    assert result.bindings == ()
    assert result.unmatched == (bins[0],)


# ── Edge cases ──────────────────────────────────────────────────────────


def test_empty_bin_files_returns_empty_result():
    """No .bin files → empty bindings, no error."""
    result = match_bin_to_intensity(
        [],
        _channels(("ch00", "00")),
        ZeroPadOffsetRule(pad_width=2, offset=1),
        TokenConfig(),
    )
    assert result.bindings == ()
    assert result.unmatched == ()
    assert result.ambiguous == ()


def test_empty_intensity_channels_all_unmatched():
    """No intensity channels → every .bin is unmatched."""
    bins = [Path("/data/x.bin"), Path("/data/y.bin")]
    result = match_bin_to_intensity(
        bins,
        [],
        ZeroPadOffsetRule(pad_width=2, offset=1),
        TokenConfig(),
    )
    assert result.bindings == ()
    assert set(result.unmatched) == set(bins)


def test_matcher_does_not_raise_on_unmatched_or_ambiguous():
    """Matcher must NEVER raise — unmatched/ambiguous live in the result."""
    rule = ZeroPadOffsetRule(pad_width=2, offset=1)
    bins = [Path("/data/no_match_here.bin")]
    intensity = _channels(("ch00", "00"))

    # Must not raise
    result = match_bin_to_intensity(bins, intensity, rule, TokenConfig())

    assert isinstance(result, MatchResult)


def test_match_result_serialization_roundtrip():
    """MatchEvidence.to_dict produces a round-trip-friendly schema."""
    ev = MatchEvidence(
        kind="tokens",
        bin_token="1",
        intensity_token="00",
    )
    d = ev.to_dict()
    assert d["kind"] == "tokens"
    assert d["bin_token"] == "1"
    assert d["intensity_token"] == "00"
