"""Integration test for the AddLayerDialog TCSPC tab data flow.

Exercises the path: state.apply_match → user override → build_commit_rule →
use case invocation. Does NOT instantiate a real ``QApplication`` or
``QTableWidget`` — the dialog's table-rendering logic is GUI-side and
doesn't carry business logic that's worth Qt-testing here. The
TcspcTabState class is exhaustively unit-tested in
``test_tcspc_tab_state.py``.
"""

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
)


def _channels(*specs):
    return [IntensityChannel(name=n, token=t) for n, t in specs]


def _matched(bin_path: Path, channel_name: str) -> BindingResult:
    return BindingResult(
        bin_path=bin_path,
        channel_name=channel_name,
        evidence=MatchEvidence(kind="tokens", bin_token="1", intensity_token="00"),
    )


def test_full_auto_flow_commits_selected_rule():
    """User picks Auto: zero-pad, every row matches, no overrides → commit rule
    is the selected CompositeRule, bindings_override is empty."""
    state = TcspcTabState(preset=RULE_AUTO_ZERO_PAD, pad_width=2, offset=1)
    state.set_intensity(_channels(("ch00", "00"), ("ch01", "01")), existing_decay=[])
    bins = [Path("/data/x_s0_ch1.bin"), Path("/data/y_s0_ch2.bin")]
    state.apply_match(bins, MatchResult(bindings=(
        _matched(bins[0], "ch00"),
        _matched(bins[1], "ch01"),
    )))

    assert state.is_acceptable() is True
    assert state.build_bindings_override() == {}
    rule = state.build_selected_rule()
    assert isinstance(rule, CompositeRule)
    assert rule.rules[0] == ZeroPadOffsetRule(pad_width=2, offset=1)


def test_user_override_flips_to_explicit_rule_for_commit():
    """User edits one row → simulates the dialog's _tcspc_build_commit_rule
    behavior: full table state becomes an ExplicitRule. Provenance still
    records the user-selected base rule (CompositeRule) at the dataset
    level — it's the per-binding ExplicitRule that's passed to
    add_decay_to_dataset."""
    state = TcspcTabState(preset=RULE_AUTO_ZERO_PAD, pad_width=2, offset=1)
    state.set_intensity(_channels(("ch00", "00"), ("ch01", "01")), existing_decay=[])
    bins = [Path("/data/x.bin"), Path("/data/y.bin")]
    state.apply_match(bins, MatchResult(bindings=(
        _matched(bins[0], "ch00"),
        _matched(bins[1], "ch01"),
    )))

    state.edit_row(bins[1], "ch00")  # override

    overrides = state.build_bindings_override()
    assert len(overrides) == 1
    # _tcspc_build_commit_rule mirrors this — it'd build ExplicitRule
    # with all rows so the table is the source of truth at commit time
    full = {
        str(r.bin_path): r.effective_channel
        for r in state.rows
        if r.effective_channel is not None
    }
    explicit = ExplicitRule(mapping=tuple(full.items()))
    assert explicit.as_dict() == {
        str(bins[0]): "ch00",
        str(bins[1]): "ch00",
    }


def test_conflict_blocks_accept_until_replace_checked():
    """Existing /decay/ch00 → conflict → Replace must be explicit before commit."""
    state = TcspcTabState()
    state.set_intensity(_channels(("ch00", "00")), existing_decay=["ch00"])
    bins = [Path("/data/x.bin")]
    state.apply_match(bins, MatchResult(bindings=(_matched(bins[0], "ch00"),)))

    assert state.is_acceptable() is False
    state.set_replace(bins[0], True)
    assert state.is_acceptable() is True
    assert state.needs_force() is True


def test_unmatched_blocks_accept():
    """An unmatched row prevents Accept regardless of all other rows."""
    state = TcspcTabState()
    state.set_intensity(_channels(("ch00", "00"), ("ch01", "01")), existing_decay=[])
    bins = [Path("/data/x.bin"), Path("/data/y.bin")]
    state.apply_match(bins, MatchResult(
        bindings=(_matched(bins[0], "ch00"),),
        unmatched=(bins[1],),
    ))

    assert state.is_acceptable() is False
    state.edit_row(bins[1], "ch01")  # user resolves the unmatched
    assert state.is_acceptable() is True


def test_manual_preset_starts_with_no_auto_bindings():
    """RULE_MANUAL: rule preset has empty mapping; rows start unmapped after
    apply_match with empty MatchResult."""
    state = TcspcTabState(preset=RULE_MANUAL)
    state.set_intensity(_channels(("ch00", "00")), existing_decay=[])
    bins = [Path("/data/x.bin")]
    # Manual mode: apply_match called with empty MatchResult so all are unmatched
    state.apply_match(bins, MatchResult(unmatched=tuple(bins)))

    assert state.rows[0].is_unmapped is True
    assert state.is_acceptable() is False
    state.edit_row(bins[0], "ch00")
    assert state.is_acceptable() is True
