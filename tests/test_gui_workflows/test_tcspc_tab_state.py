"""Tests for the pure-Python TCSPC tab state (U4 of TCSPC append thread)."""

from __future__ import annotations

from pathlib import Path

from percell4.domain.io.cross_format import (
    BindingResult,
    IntensityChannel,
    MatchEvidence,
    MatchResult,
)
from percell4.domain.io.models import (
    BaseStemRule,
    CompositeRule,
    ExplicitRule,
    ZeroPadOffsetRule,
)
from percell4.gui.tcspc_tab_state import (
    RULE_AUTO_BASE_STEM,
    RULE_AUTO_ZERO_PAD,
    RULE_MANUAL,
    TcspcTabState,
    build_rule_from_preset,
)


def _channels(*specs):
    return [IntensityChannel(name=n, token=t) for n, t in specs]


def _matched(bin_path: Path, channel_name: str) -> BindingResult:
    return BindingResult(
        bin_path=bin_path,
        channel_name=channel_name,
        evidence=MatchEvidence(kind="tokens", bin_token="1", intensity_token="00"),
    )


# ── Rule preset construction ────────────────────────────────────────────


def test_preset_zero_pad_offset_builds_composite():
    """RULE_AUTO_ZERO_PAD → CompositeRule(ZeroPadOffsetRule(2,1), BaseStemRule)."""
    rule = build_rule_from_preset(RULE_AUTO_ZERO_PAD, pad_width=2, offset=1)
    assert isinstance(rule, CompositeRule)
    assert len(rule.rules) == 2
    assert rule.rules[0] == ZeroPadOffsetRule(pad_width=2, offset=1)
    assert isinstance(rule.rules[1], BaseStemRule)


def test_preset_zero_pad_offset_with_custom_params():
    """Custom pad/offset gets passed through."""
    rule = build_rule_from_preset(RULE_AUTO_ZERO_PAD, pad_width=3, offset=0)
    assert rule.rules[0] == ZeroPadOffsetRule(pad_width=3, offset=0)


def test_preset_base_stem_returns_base_stem_rule():
    rule = build_rule_from_preset(RULE_AUTO_BASE_STEM)
    assert isinstance(rule, BaseStemRule)


def test_preset_manual_returns_empty_explicit_rule():
    rule = build_rule_from_preset(RULE_MANUAL)
    assert isinstance(rule, ExplicitRule)
    assert rule.mapping == ()


# ── State.apply_match ───────────────────────────────────────────────────


def test_apply_match_creates_row_per_bin():
    state = TcspcTabState()
    state.set_intensity(_channels(("ch00", "00")), existing_decay=[])
    bins = [Path("/data/exp_s0_ch1.bin")]
    result = MatchResult(bindings=(_matched(bins[0], "ch00"),))

    state.apply_match(bins, result)

    assert len(state.rows) == 1
    assert state.rows[0].auto_channel == "ch00"
    assert state.rows[0].user_channel is None
    assert state.rows[0].effective_channel == "ch00"


def test_apply_match_marks_unmatched_rows():
    state = TcspcTabState()
    state.set_intensity(_channels(("ch00", "00")), existing_decay=[])
    bins = [Path("/data/no_match.bin")]
    result = MatchResult(unmatched=tuple(bins))

    state.apply_match(bins, result)

    assert state.rows[0].auto_channel is None
    assert state.rows[0].is_unmapped is True


def test_apply_match_marks_ambiguous_with_candidates():
    state = TcspcTabState()
    state.set_intensity(
        _channels(("chA", "0A"), ("chB", "0B")),
        existing_decay=[],
    )
    bins = [Path("/data/Dataset.bin")]
    result = MatchResult(ambiguous=((bins[0], ("chA", "chB")),))

    state.apply_match(bins, result)

    assert state.rows[0].is_ambiguous is True
    assert state.rows[0].candidates == ("chA", "chB")
    assert state.rows[0].effective_channel is None


def test_apply_match_preserves_user_edits_across_rule_change():
    """Changing the rule and re-running keeps user-edited cells intact."""
    state = TcspcTabState()
    state.set_intensity(_channels(("ch00", "00"), ("ch01", "01")), existing_decay=[])
    bins = [Path("/data/x.bin")]
    state.apply_match(bins, MatchResult(bindings=(_matched(bins[0], "ch00"),)))
    state.edit_row(bins[0], "ch01")  # user override

    # Re-apply a different match — user's choice should win
    state.apply_match(bins, MatchResult(bindings=(_matched(bins[0], "ch00"),)))

    assert state.rows[0].user_channel == "ch01"
    assert state.rows[0].effective_channel == "ch01"


# ── Conflict detection ─────────────────────────────────────────────────


def test_conflict_flagged_when_target_channel_already_has_decay():
    state = TcspcTabState()
    state.set_intensity(
        _channels(("ch00", "00")),
        existing_decay=["ch00"],  # /decay/ch00 already exists
    )
    bins = [Path("/data/x.bin")]
    state.apply_match(bins, MatchResult(bindings=(_matched(bins[0], "ch00"),)))

    assert state.rows[0].has_conflict is True
    assert state.rows[0].needs_replace is True
    assert state.is_acceptable() is False  # Replace not yet checked


def test_replace_check_unblocks_accept():
    state = TcspcTabState()
    state.set_intensity(_channels(("ch00", "00")), existing_decay=["ch00"])
    bins = [Path("/data/x.bin")]
    state.apply_match(bins, MatchResult(bindings=(_matched(bins[0], "ch00"),)))

    state.set_replace(bins[0], True)

    assert state.rows[0].needs_replace is False
    assert state.is_acceptable() is True
    assert state.needs_force() is True


def test_edit_row_clears_replace_when_new_target_has_no_conflict():
    state = TcspcTabState()
    state.set_intensity(
        _channels(("ch00", "00"), ("ch01", "01")),
        existing_decay=["ch00"],  # only ch00 is taken
    )
    bins = [Path("/data/x.bin")]
    state.apply_match(bins, MatchResult(bindings=(_matched(bins[0], "ch00"),)))
    state.set_replace(bins[0], True)

    state.edit_row(bins[0], "ch01")  # move to a free channel

    assert state.rows[0].has_conflict is False
    assert state.rows[0].replace_checked is False


# ── Acceptability ──────────────────────────────────────────────────────


def test_accept_disabled_when_any_row_unmapped():
    state = TcspcTabState()
    state.set_intensity(_channels(("ch00", "00")), existing_decay=[])
    bins = [Path("/data/x.bin")]
    state.apply_match(bins, MatchResult(unmatched=tuple(bins)))

    assert state.is_acceptable() is False


def test_accept_disabled_when_no_rows():
    state = TcspcTabState()
    assert state.is_acceptable() is False


def test_accept_enabled_when_all_rows_resolved_no_conflicts():
    state = TcspcTabState()
    state.set_intensity(_channels(("ch00", "00"), ("ch01", "01")), existing_decay=[])
    bins = [Path("/data/x.bin"), Path("/data/y.bin")]
    state.apply_match(bins, MatchResult(bindings=(
        _matched(bins[0], "ch00"),
        _matched(bins[1], "ch01"),
    )))

    assert state.is_acceptable() is True


# ── Override detection ──────────────────────────────────────────────────


def test_user_edit_to_same_channel_is_not_override():
    """Picking the same auto-suggested channel is not an override."""
    state = TcspcTabState()
    state.set_intensity(_channels(("ch00", "00")), existing_decay=[])
    bins = [Path("/data/x.bin")]
    state.apply_match(bins, MatchResult(bindings=(_matched(bins[0], "ch00"),)))

    state.edit_row(bins[0], "ch00")

    assert state.rows[0].is_overridden is False
    assert state.has_user_edits() is False


def test_user_edit_to_different_channel_is_override():
    state = TcspcTabState()
    state.set_intensity(_channels(("ch00", "00"), ("ch01", "01")), existing_decay=[])
    bins = [Path("/data/x.bin")]
    state.apply_match(bins, MatchResult(bindings=(_matched(bins[0], "ch00"),)))

    state.edit_row(bins[0], "ch01")

    assert state.rows[0].is_overridden is True
    assert state.has_user_edits() is True


# ── Commit-time outputs ────────────────────────────────────────────────


def test_build_selected_rule_uses_current_preset():
    state = TcspcTabState(preset=RULE_AUTO_ZERO_PAD, pad_width=2, offset=1)
    rule = state.build_selected_rule()
    assert isinstance(rule, CompositeRule)
    assert rule.rules[0] == ZeroPadOffsetRule(pad_width=2, offset=1)


def test_build_bindings_override_only_user_edits():
    state = TcspcTabState()
    state.set_intensity(_channels(("ch00", "00"), ("ch01", "01")), existing_decay=[])
    bins = [Path("/data/x.bin"), Path("/data/y.bin")]
    state.apply_match(bins, MatchResult(bindings=(
        _matched(bins[0], "ch00"),
        _matched(bins[1], "ch01"),
    )))
    state.edit_row(bins[1], "ch00")  # user override; ch00 is now double-bound, but state allows it

    overrides = state.build_bindings_override()

    # Only the edited row appears
    assert len(overrides) == 1
    assert any("y.bin" in k for k in overrides)
    assert list(overrides.values()) == ["ch00"]


def test_build_bindings_override_empty_when_all_auto():
    state = TcspcTabState()
    state.set_intensity(_channels(("ch00", "00")), existing_decay=[])
    bins = [Path("/data/x.bin")]
    state.apply_match(bins, MatchResult(bindings=(_matched(bins[0], "ch00"),)))

    assert state.build_bindings_override() == {}
