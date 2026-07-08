"""Pure-Python state for the TCSPC append tab in AddLayerDialog.

The Qt tab is a thin shell around this state object — same shape as
``StagingBuffer`` for the multi-select tool. Lets the data-flow logic be
tested without a real ``QApplication`` or ``QTableWidget``.

State tracks:
  - the current rule the user selected from the dropdown
  - the current bindings the matcher proposed (auto)
  - any per-row edits the user made (manual override)
  - which channels already have ``/decay/<name>`` (conflict highlight)
  - whether Accept can be enabled

Derives at commit time:
  - ``selected_rule`` (the dropdown choice — passed to the use case)
  - ``bindings_override`` (only the cells the user manually edited)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from percell4.domain.io.cross_format import IntensityChannel, MatchResult
from percell4.domain.io.models import (
    BaseStemRule,
    CompositeRule,
    CrossFormatRule,
    ZeroPadOffsetRule,
)

# Kept for backwards-compatibility with test imports — no longer wired to UI.
RULE_AUTO_ZERO_PAD = "direct_token_match"
RULE_AUTO_BASE_STEM = "direct_token_match"
RULE_MANUAL = "direct_token_match"


def build_rule_from_preset(preset: str = "direct_token_match", **_) -> CrossFormatRule:
    """The dialog now uses one matching rule: direct token equality, with
    base-stem fallback for .bin files that carry no channel token. The
    pad/offset transformation logic was removed at user request — easier
    to rename .bin files than to troubleshoot off-by-one seeding errors.
    Per-channel token overrides via the dropdown handle the remaining
    edge cases (e.g., semantic channel names like ``CA-SiR``).
    """
    return CompositeRule(rules=(
        ZeroPadOffsetRule(pad_width=0, offset=0),  # direct equality
        BaseStemRule(),
    ))


# ── Per-row state ───────────────────────────────────────────────────────


@dataclass
class TcspcRow:
    """One row in the binding table — a single .bin file."""

    bin_path: Path
    auto_channel: str | None = None  # what the matcher produced
    user_channel: str | None = None  # user override (None = no edit)
    candidates: tuple[str, ...] = ()  # ambiguous: channel_names that matched
    has_conflict: bool = False  # /decay/<channel> already exists in store
    replace_checked: bool = False  # user explicitly checked Replace

    @property
    def effective_channel(self) -> str | None:
        """The channel this row will commit to. None means unmapped."""
        return self.user_channel if self.user_channel is not None else self.auto_channel

    @property
    def is_overridden(self) -> bool:
        """True when the user manually edited this row (vs auto-matched)."""
        return self.user_channel is not None and self.user_channel != self.auto_channel

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.candidates) and self.effective_channel is None

    @property
    def is_unmapped(self) -> bool:
        return self.effective_channel is None

    @property
    def needs_replace(self) -> bool:
        """True when committing this row would require force=True (conflict, no replace)."""
        return self.has_conflict and not self.replace_checked


# ── Tab state ───────────────────────────────────────────────────────────


@dataclass
class TcspcTabState:
    """Aggregate state for the TCSPC append tab."""

    intensity_channels: tuple[IntensityChannel, ...] = ()
    existing_decay_channels: frozenset[str] = frozenset()
    rows: list[TcspcRow] = field(default_factory=list)
    # Kept for backwards-compatibility with older tests; not used by the UI.
    preset: str = "direct_token_match"
    pad_width: int = 0
    offset: int = 0

    # ── Loading ─────────────────────────────────────────────────────────

    def set_intensity(
        self,
        channels: list[IntensityChannel],
        existing_decay: list[str],
    ) -> None:
        self.intensity_channels = tuple(channels)
        self.existing_decay_channels = frozenset(existing_decay)
        # Re-evaluate conflicts on existing rows
        for row in self.rows:
            ch = row.effective_channel
            row.has_conflict = ch is not None and ch in self.existing_decay_channels

    def apply_match(self, bin_files: list[Path], result: MatchResult) -> None:
        """Replace the rows from a fresh match result. Preserves user edits
        for bin paths the user already overrode (so changing the rule and
        re-running doesn't silently discard their manual changes)."""
        prior = {row.bin_path: row for row in self.rows}
        new_rows: list[TcspcRow] = []
        match_by_path = {b.bin_path: b.channel_name for b in result.bindings}
        ambig_by_path = {p: candidates for p, candidates in result.ambiguous}
        for bin_path in bin_files:
            auto = match_by_path.get(bin_path)
            candidates = ambig_by_path.get(bin_path, ())
            row = TcspcRow(
                bin_path=bin_path,
                auto_channel=auto,
                candidates=candidates,
            )
            # Preserve user override if same path was previously edited
            prior_row = prior.get(bin_path)
            if prior_row is not None and prior_row.user_channel is not None:
                row.user_channel = prior_row.user_channel
                row.replace_checked = prior_row.replace_checked
            ch = row.effective_channel
            row.has_conflict = ch is not None and ch in self.existing_decay_channels
            new_rows.append(row)
        self.rows = new_rows

    # ── Editing ─────────────────────────────────────────────────────────

    def edit_row(self, bin_path: Path, channel_name: str | None) -> None:
        """Set the user-assigned channel for a row (None to clear override)."""
        for row in self.rows:
            if row.bin_path == bin_path:
                row.user_channel = channel_name
                ch = row.effective_channel
                row.has_conflict = ch is not None and ch in self.existing_decay_channels
                if not row.has_conflict:
                    row.replace_checked = False
                return

    def set_replace(self, bin_path: Path, replace: bool) -> None:
        for row in self.rows:
            if row.bin_path == bin_path:
                row.replace_checked = replace
                return

    # ── Derived predicates ──────────────────────────────────────────────

    def has_user_edits(self) -> bool:
        return any(r.is_overridden for r in self.rows)

    def is_acceptable(self) -> bool:
        """Accept enabled iff every row maps to a channel AND every conflict has Replace checked."""
        if not self.rows:
            return False
        for row in self.rows:
            if row.is_unmapped:
                return False
            if row.needs_replace:
                return False
        return True

    def unmatched_paths(self) -> list[Path]:
        return [r.bin_path for r in self.rows if r.is_unmapped and not r.candidates]

    def ambiguous_paths(self) -> list[Path]:
        return [r.bin_path for r in self.rows if r.is_ambiguous]

    def conflict_channels(self) -> list[str]:
        return [
            r.effective_channel for r in self.rows
            if r.has_conflict and r.effective_channel is not None
        ]

    # ── Commit-time outputs ─────────────────────────────────────────────

    def build_selected_rule(self) -> CrossFormatRule:
        # Always direct token equality + base-stem fallback. Pad/offset
        # transformation was removed at user request.
        return build_rule_from_preset()

    def build_bindings_override(self) -> dict[str, str]:
        """Return the {bin_path_str: channel_name} mapping for cells the user edited."""
        out: dict[str, str] = {}
        for row in self.rows:
            if row.is_overridden and row.user_channel is not None:
                key = (
                    str(row.bin_path.resolve())
                    if row.bin_path.exists()
                    else str(row.bin_path)
                )
                out[key] = row.user_channel
        return out

    def needs_force(self) -> bool:
        """True when at least one row is replacing an existing decay layer."""
        return any(r.has_conflict and r.replace_checked for r in self.rows)
