"""Cross-format token matching: bind ``.bin`` (TCSPC) files to intensity channels.

Pure-domain function — no Qt, no h5py, no I/O. Called by both the initial-import
path (``adapters/importer.py``) and the append path (``add_decay_to_dataset`` use
case) so the same matching logic governs both flows. This is principle 5
("Batch ≡ Incremental") expressed structurally.

The matcher dispatches on a ``CrossFormatRule`` variant — see
``domain/io/models.py``:

- ``ZeroPadOffsetRule`` reconciles padding and 1-vs-0 indexing differences in
  channel tokens (e.g. TIFF ``_ch00`` ↔ `.bin` ``_ch1``).
- ``BaseStemRule`` matches by TIFF-stem-with-channel-stripped vs `.bin` stem.
- ``ExplicitRule`` is a user-supplied path → channel mapping (used when the
  dialog lets the user override per-row).
- ``CompositeRule`` runs sub-rules in order and takes the first match.

The function never raises on a missing or ambiguous match; results live on
``MatchResult.unmatched`` / ``MatchResult.ambiguous`` so callers can decide
how to surface them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from percell4.domain.io.models import (
    BaseStemRule,
    CompositeRule,
    CrossFormatRule,
    ExplicitRule,
    TokenConfig,
    ZeroPadOffsetRule,
)

# ── Rule serialization (HDF5 attr round-trip) ──────────────────────────


def _rule_to_dict(rule: CrossFormatRule) -> dict:
    if isinstance(rule, ZeroPadOffsetRule):
        return {"type": "ZeroPadOffsetRule", "pad_width": rule.pad_width, "offset": rule.offset}
    if isinstance(rule, BaseStemRule):
        return {"type": "BaseStemRule"}
    if isinstance(rule, ExplicitRule):
        return {"type": "ExplicitRule", "mapping": list(rule.mapping)}
    if isinstance(rule, CompositeRule):
        return {"type": "CompositeRule", "rules": [_rule_to_dict(r) for r in rule.rules]}
    raise TypeError(f"unknown CrossFormatRule variant: {type(rule).__name__}")


def _dict_to_rule(d: dict) -> CrossFormatRule:
    t = d["type"]
    if t == "ZeroPadOffsetRule":
        return ZeroPadOffsetRule(pad_width=d["pad_width"], offset=d["offset"])
    if t == "BaseStemRule":
        return BaseStemRule()
    if t == "ExplicitRule":
        return ExplicitRule(mapping=tuple(tuple(pair) for pair in d.get("mapping", [])))
    if t == "CompositeRule":
        return CompositeRule(rules=tuple(_dict_to_rule(r) for r in d.get("rules", [])))
    raise ValueError(f"unknown serialized rule type: {t!r}")


def serialize_rule(rule: CrossFormatRule) -> str:
    """Serialize a CrossFormatRule to a JSON string (for HDF5 attr storage)."""
    return json.dumps(_rule_to_dict(rule))


def deserialize_rule(s: str) -> CrossFormatRule:
    """Deserialize a JSON string back into a CrossFormatRule. Inverse of serialize_rule."""
    return _dict_to_rule(json.loads(s))


@dataclass(frozen=True)
class IntensityChannel:
    """A target channel that ``.bin`` files can bind to.

    ``name`` is the canonical channel name in the store (e.g. ``"ch00"``).
    ``token`` is the raw token parsed from the TIFF filename (e.g. ``"00"``)
    — used by ``ZeroPadOffsetRule`` for re-padding/offsetting comparisons.
    ``base_stem`` is the TIFF filename stem with the channel token stripped
    (e.g. ``"Dataset_A"``) — used by ``BaseStemRule``. Optional because
    callers may not always need stem-based matching.
    """

    name: str
    token: str
    base_stem: str | None = None


@dataclass(frozen=True)
class MatchEvidence:
    """Why a particular bin → channel binding was made.

    ``kind`` is ``"tokens"`` (token-based rule), ``"stem"`` (stem rule),
    or ``"explicit"`` (user override). The other fields populate based on
    ``kind`` — see ``to_dict`` for the canonical serialization shape.
    """

    kind: str
    bin_token: str | None = None
    intensity_token: str | None = None
    matched_stem: str | None = None

    def to_dict(self) -> dict:
        out = {"kind": self.kind}
        if self.bin_token is not None:
            out["bin_token"] = self.bin_token
        if self.intensity_token is not None:
            out["intensity_token"] = self.intensity_token
        if self.matched_stem is not None:
            out["matched_stem"] = self.matched_stem
        return out


@dataclass(frozen=True)
class BindingResult:
    """One concrete bin → channel binding plus evidence."""

    bin_path: Path
    channel_name: str
    evidence: MatchEvidence


@dataclass(frozen=True)
class MatchResult:
    """Result of running ``match_bin_to_intensity``.

    ``bindings`` are the resolved bin → channel pairs.
    ``unmatched`` are bins the rule could not bind.
    ``ambiguous`` are bins that matched more than one channel — caller
    must disambiguate (typically via an ExplicitRule override).
    """

    bindings: tuple[BindingResult, ...] = ()
    unmatched: tuple[Path, ...] = ()
    ambiguous: tuple[tuple[Path, tuple[str, ...]], ...] = ()


# ── Public entry point ──────────────────────────────────────────────────


def match_bin_to_intensity(
    bin_files: list[Path],
    intensity_channels: list[IntensityChannel],
    rule: CrossFormatRule,
    token_config: TokenConfig,
) -> MatchResult:
    """Bind ``.bin`` files to intensity channels per ``rule``.

    Returns a ``MatchResult``. Never raises on unmatched or ambiguous bins;
    callers must inspect ``.unmatched`` and ``.ambiguous``.
    """
    if isinstance(rule, CompositeRule):
        return _match_composite(bin_files, intensity_channels, rule, token_config)
    if isinstance(rule, ExplicitRule):
        return _match_explicit(bin_files, intensity_channels, rule)
    if isinstance(rule, ZeroPadOffsetRule):
        return _match_zero_pad_offset(bin_files, intensity_channels, rule, token_config)
    if isinstance(rule, BaseStemRule):
        return _match_base_stem(bin_files, intensity_channels)
    raise TypeError(f"unknown CrossFormatRule variant: {type(rule).__name__}")


# ── Composite ───────────────────────────────────────────────────────────


def _match_composite(
    bin_files: list[Path],
    intensity_channels: list[IntensityChannel],
    rule: CompositeRule,
    token_config: TokenConfig,
) -> MatchResult:
    """Run sub-rules in order; first match wins; later rules see only the rest."""
    bindings: list[BindingResult] = []
    ambiguous: list[tuple[Path, tuple[str, ...]]] = []
    remaining = list(bin_files)
    for sub in rule.rules:
        if not remaining:
            break
        sub_result = match_bin_to_intensity(remaining, intensity_channels, sub, token_config)
        bindings.extend(sub_result.bindings)
        # Bins that became ambiguous in a sub-rule do NOT fall through —
        # ambiguity needs explicit disambiguation, not a more permissive rule.
        ambiguous.extend(sub_result.ambiguous)
        consumed = {b.bin_path for b in sub_result.bindings}
        consumed.update(p for p, _ in sub_result.ambiguous)
        remaining = [p for p in remaining if p not in consumed]
    return MatchResult(
        bindings=tuple(bindings),
        unmatched=tuple(remaining),
        ambiguous=tuple(ambiguous),
    )


# ── ZeroPadOffsetRule ───────────────────────────────────────────────────


def _match_zero_pad_offset(
    bin_files: list[Path],
    intensity_channels: list[IntensityChannel],
    rule: ZeroPadOffsetRule,
    token_config: TokenConfig,
) -> MatchResult:
    """Match by re-padding and offsetting the bin's channel token.

    With pad_width=2, offset=1: bin token "1" → "00" → match TIFF "00".
    With pad_width=0, offset=0: exact-equality match (no transformation).
    """
    if not token_config.channel:
        return MatchResult(unmatched=tuple(bin_files))

    by_token: dict[str, list[IntensityChannel]] = {}
    for ch in intensity_channels:
        by_token.setdefault(ch.token, []).append(ch)

    bindings: list[BindingResult] = []
    unmatched: list[Path] = []
    ambiguous: list[tuple[Path, tuple[str, ...]]] = []
    for bin_path in bin_files:
        bin_token = _extract_token(bin_path.stem, token_config.channel)
        if bin_token is None:
            unmatched.append(bin_path)
            continue
        target_token = _transform_token(bin_token, rule.pad_width, rule.offset)
        if target_token is None:
            unmatched.append(bin_path)
            continue
        candidates = by_token.get(target_token, [])
        if not candidates:
            unmatched.append(bin_path)
        elif len(candidates) > 1:
            ambiguous.append((bin_path, tuple(c.name for c in candidates)))
        else:
            bindings.append(BindingResult(
                bin_path=bin_path,
                channel_name=candidates[0].name,
                evidence=MatchEvidence(
                    kind="tokens",
                    bin_token=bin_token,
                    intensity_token=target_token,
                ),
            ))
    return MatchResult(
        bindings=tuple(bindings),
        unmatched=tuple(unmatched),
        ambiguous=tuple(ambiguous),
    )


def _extract_token(stem: str, pattern: str) -> str | None:
    m = re.search(pattern, stem)
    return m.group(1) if m else None


def _transform_token(bin_token: str, pad_width: int, offset: int) -> str | None:
    """Apply (token - offset, zero-padded to pad_width). Returns None on parse failure."""
    if pad_width == 0 and offset == 0:
        # No transformation — exact-equality match
        return bin_token
    try:
        n = int(bin_token) - offset
    except ValueError:
        return None
    if n < 0:
        return None
    if pad_width > 0:
        return f"{n:0{pad_width}d}"
    return str(n)


# ── BaseStemRule ────────────────────────────────────────────────────────


def _match_base_stem(
    bin_files: list[Path],
    intensity_channels: list[IntensityChannel],
) -> MatchResult:
    """Match by base-stem equality / either-direction prefix.

    Mirrors the behavior at ``adapters/importer.py:223-231`` (the existing
    cascade's stem-fallback step): exact base-stem equality first, then
    prefix/reverse-prefix match.
    """
    candidates_with_stem = [c for c in intensity_channels if c.base_stem]
    bindings: list[BindingResult] = []
    unmatched: list[Path] = []
    ambiguous: list[tuple[Path, tuple[str, ...]]] = []

    for bin_path in bin_files:
        stem = bin_path.stem
        # First pass: exact equality
        exact = [c for c in candidates_with_stem if c.base_stem == stem]
        if len(exact) == 1:
            bindings.append(BindingResult(
                bin_path=bin_path,
                channel_name=exact[0].name,
                evidence=MatchEvidence(kind="stem", matched_stem=exact[0].base_stem),
            ))
            continue
        if len(exact) > 1:
            ambiguous.append((bin_path, tuple(c.name for c in exact)))
            continue
        # Second pass: prefix in either direction
        prefix = [
            c for c in candidates_with_stem
            if stem.startswith(c.base_stem) or c.base_stem.startswith(stem)
        ]
        if len(prefix) == 1:
            bindings.append(BindingResult(
                bin_path=bin_path,
                channel_name=prefix[0].name,
                evidence=MatchEvidence(kind="stem", matched_stem=prefix[0].base_stem),
            ))
        elif len(prefix) > 1:
            ambiguous.append((bin_path, tuple(c.name for c in prefix)))
        else:
            unmatched.append(bin_path)
    return MatchResult(
        bindings=tuple(bindings),
        unmatched=tuple(unmatched),
        ambiguous=tuple(ambiguous),
    )


# ── ExplicitRule ────────────────────────────────────────────────────────


def _match_explicit(
    bin_files: list[Path],
    intensity_channels: list[IntensityChannel],
    rule: ExplicitRule,
) -> MatchResult:
    """Look up each bin in the user-supplied mapping (resolved-path keys)."""
    mapping = rule.as_dict()
    valid_names = {c.name for c in intensity_channels}
    bindings: list[BindingResult] = []
    unmatched: list[Path] = []
    for bin_path in bin_files:
        key = str(bin_path.resolve()) if bin_path.exists() else str(bin_path)
        target = mapping.get(key)
        if target is None and bin_path.is_absolute():
            target = mapping.get(str(bin_path))
        if target is not None and (not valid_names or target in valid_names):
            bindings.append(BindingResult(
                bin_path=bin_path,
                channel_name=target,
                evidence=MatchEvidence(kind="explicit"),
            ))
        else:
            unmatched.append(bin_path)
    return MatchResult(
        bindings=tuple(bindings),
        unmatched=tuple(unmatched),
    )
