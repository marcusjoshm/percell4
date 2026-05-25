---
title: "Single-channel LASX .bin exports omit _chN — parser must fall back to token '0'"
date: 2026-05-25
category: logic-errors
module: percell4.domain.io.cross_format
problem_type: logic_error
component: tooling
canonical_source: src/percell4/domain/io/cross_format.py
applies_to:
  - "src/percell4/domain/io/cross_format.py"
  - "src/percell4/gui/batch_tcspc_dialog.py"
duplicates_at: []
status: pre_canonical
severity: medium
symptoms:
  - "LASX single-channel `.bin` exports (no `_chN` suffix) are silently dropped to `MatchResult.unmatched`."
  - "Batch TCSPC Append Section 4 token dropdown shows no entry for folders that contain only token-less `.bin` files."
  - "`_extract_token` returns `None` on filenames that lack `_ch(\\d+)`."
  - "Users worked around the issue by manually renaming `decay.bin` → `decay_ch1.bin` outside the app."
root_cause: logic_error
resolution_type: code_fix
related_components:
  - percell4.gui.batch_tcspc_dialog
tags:
  - flim
  - lasx
  - leica
  - tcspc
  - single-channel
  - parser-fallback
  - batch-append
  - bin-files
  - token-matching
  - cross-format
---

# Single-channel LASX `.bin` exports omit `_chN` — parser must fall back to token `"0"`

## Problem

When FLIM is acquired with only one channel, LASX (Leica's microscope software) exports decay `.bin` files **without** the `_chN` token (e.g. `decay.bin` instead of `decay_ch1.bin`). PerCell4's Batch TCSPC Append workflow rejected these files: the cross-format token parser returned `None`, the matcher routed them silently to `MatchResult.unmatched`, and the GUI's Section 4 channel-token dropdown offered no entry the user could pair against. Users had to manually rename their files before the workflow would accept them.

## Symptoms

- Token-less `.bin` files exported by LASX in single-channel FLIM mode rejected by Batch TCSPC Append with no path to recovery inside the dialog.
- The Section 4 channel-token dropdown in `batch_tcspc_dialog.py` showed no entries for folders containing only token-less `.bin` files, even though the files were physically present in the paired group folder.
- `MatchResult.unmatched` silently accumulated these `.bin`s with no UI signal to the user.
- Workaround: users renamed `decay.bin` → `decay_ch1.bin` outside the app before importing.

## What didn't work

The parser at `src/percell4/domain/io/cross_format.py:254` (`_extract_token`) ran `re.search(r"_ch(\d+)", stem)` and returned `None` on no-match. The matcher at `cross_format.py:223-226` (`_match_zero_pad_offset`) already had unmatched-handling and dutifully appended the file to `unmatched` when the token came back `None`. The GUI at `src/percell4/gui/batch_tcspc_dialog.py:599-615` (`_discover_bin_tokens`) reused the same hardcoded `r"_ch(\d+)"` regex and silently skipped non-matching files. All three layers were behaving exactly as designed; the design itself assumed the `_chN` suffix would always be present — an assumption LASX violates for single-channel acquisitions.

No amount of UI affordance on existing tokens helps when no token is ever produced. The token-extraction layer needed a fallback, and the GUI surface needed to expose that fallback as a selectable option.

### Pre-existing parallel fallback in the importer (session history)

A separate single-channel fallback **already existed** in the single-dataset importer path. Session `1ad75d33` (2026-05-05) surfaced this code in `application/use_cases/add_decay_to_dataset.py` during a tile-stitching debug:

```python
if not matched:
    # Fall back to all .bin files (single-channel case)
    matched = bins
```

That fallback lives in the importer's internal tile-collection logic, never in `cross_format.py`. The batch dialog (`batch_tcspc_dialog.py`, built in session `2229d9c8`, 2026-05-12) was designed and tested against multi-channel LASX exports and never cross-referenced the importer's existing single-channel handling. Two paths through the same file format, one with a fallback, one without — a classic parallel-implementation drift.

## Solution

The fix lives in **both** the parser (matching path) AND the dialog (UX path), because either alone is insufficient: a parser fallback the UI never surfaces is invisible to users, and a UI token the parser refuses to honor is a dead end.

### Parser — `src/percell4/domain/io/cross_format.py`

BEFORE:

```python
def _extract_token(stem: str, pattern: str) -> str | None:
    m = re.search(pattern, stem)
    return m.group(1) if m else None
```

AFTER:

```python
def _extract_token(stem: str, pattern: str) -> str | None:
    """Extract a channel token from a ``.bin`` file stem.

    Returns the captured group when the pattern matches. When the pattern
    does NOT match — a single-channel FLIM acquisition where LASX omits
    the ``_chN`` suffix from the exported decay file — fall back to "0"
    so the user can pair the single channel with their intensity channel
    via Section 4 of the Batch TCSPC dialog.

    Returns None only when there is no pattern to match against (i.e.,
    ``TokenConfig.channel`` is empty / None).
    """
    if not pattern:
        return None
    m = re.search(pattern, stem)
    if m:
        return m.group(1)
    # Single-channel fallback: assume the .bin belongs to "channel 0".
    return "0"
```

### GUI — `src/percell4/gui/batch_tcspc_dialog.py` (`_discover_bin_tokens`)

Added the fallback branch — when the regex does NOT match, add `"0"` to the discovered-tokens set:

```python
        m = re.search(r"_ch(\d+)", p.stem)
        if m:
            tokens.add(m.group(1))
        else:
            # Single-channel fallback: surface "0" so the user can pair
            # a token-less .bin with their intensity channel.
            tokens.add("0")
```

The user still drives the actual pairing in Section 4 — the parser change just lets the matcher honor a `"0"` pairing, and the GUI change just makes `"0"` visible as an option in the dropdown.

## Why this works

The original `r"_ch(\d+)"` regex was designed for the common case: multi-channel LASX exports where every filename is decorated with `_s{tile}_ch{channel}`. LASX violates that assumption for single-channel exports by omitting the (redundant) channel decoration entirely.

Falling back to the synthetic token `"0"` is **intent-preserving**: it does not auto-pair the `.bin` with anything; it merely surfaces an addressable token that the user can choose to pair with their intensity channel via the existing Section 4 UI. If no channel has token `"0"`, the `.bin` stays unmatched. The fix expands the vocabulary the user has available; it does not change who decides.

The fix is also conceptually aligned with the importer's pre-existing single-channel fallback (see "Pre-existing parallel fallback" above) — both paths now treat token-less `.bin` files as belonging to "channel 0" by default.

## Prevention

- **Vendor-specific export formats need single-instance edge-case tests.** When designing parsers for LASX, Becker&Hickl SDT, MetaMorph, etc., document and test the degenerate cases explicitly — single channel, single timepoint, single tile. Vendors routinely omit redundant decorations in degenerate cases, and a regex that requires the decoration will silently exclude the data.
- **Coordinate parser fallbacks with the UI surfaces that expose them.** A fallback that only lives in the parser is invisible to users; a token that only lives in the UI dropdown is a dead end at the matcher. Every fallback needs both paths or it isn't a fallback. The fix here required coordinated changes in `cross_format._extract_token` and `batch_tcspc_dialog._discover_bin_tokens`.
- **Audit for parallel implementations of file-format parsing.** Before adding a fallback to one path, grep for sibling parsers that handle the same format. In this case, `application/use_cases/add_decay_to_dataset.py` already had `if not matched: matched = bins` for the single-channel case; the batch dialog's separate parser drifted away from that convention. Run `grep -rn "_ch(\\\\d+)" src/percell4/` to find every callsite that needs coordinated fallback. Sibling dialog `add_layer_dialog.py` may have a similar `_tcspc_discover_bin_tokens` that needs the same fallback — verify before assuming the fix is complete.
- **Test the conservative side of the fallback.** Add a test asserting the change is intent-preserving, not auto-pairing magic. The test `test_bin_without_token_does_not_pair_when_no_zero_channel` proves the parser still refuses to bind when no channel has token `"0"` — the user is still in control.
- **Concrete test patterns from this fix** (`tests/test_io/test_cross_format.py`):
  - `test_bin_without_token_defaults_to_zero_and_pairs_under_no_transform` — fallback token "0" pairs under `ZeroPadOffsetRule(0, 0)`.
  - `test_multiple_bins_without_token_all_collapse_to_zero` — multiple token-less `.bin`s collapse to one channel-"0" (correct for stitched tiles).
  - `test_bin_without_token_does_not_pair_when_no_zero_channel` — conservative guarantee: no channel with token "0" means the `.bin` stays unmatched.
- **Concrete UI-side test patterns** (`tests/test_gui/test_batch_tcspc_dialog.py`):
  - `test_channel_token_section_surfaces_zero_for_single_channel_bins` — pure single-channel folder produces `_available_bin_tokens == ["0"]`.
  - `test_channel_token_section_mixes_zero_with_real_tokens` — mixed folder produces `_available_bin_tokens == ["0", "2"]`.
- **When updating an existing test whose assumption no longer holds, rename it.** The old `test_zero_pad_offset_bin_without_channel_token_unmatched` was renamed to `test_zero_pad_offset_bin_without_channel_token_unmatched_when_transform_invalid` with a docstring explaining that the token is now `"0"` but the `pad_width=2, offset=1` transform yields `-1` (invalid), so the `.bin` is still unmatched. Don't silently mutate test docstrings — the new reasoning needs to be captured.

## When to apply

- Adding a new vendor-specific file-format parser to the codebase, especially for microscope software (LASX, Becker&Hickl, MetaMorph, Andor, Zeiss).
- Investigating a bug where a recognizable input file is silently rejected by a parser with no error path the user can recover from.
- Reviewing a PR that adds or modifies a regex over filenames where one captured group represents an instance index (channel number, tile number, timepoint).
- Auditing `domain/io/` and `gui/` for parallel implementations of the same file-format conventions.

## Related

- `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md` — parent FLIM-alignment story. The "Adjacent fixes / Bin-only import regression" bullet (line 134) introduced the per-`.bin` token parser the new fix extends; that bullet should now back-reference this doc since the "explicit bin-only fallback that parses each `.bin`'s `_ch(\d+)` token directly" sentence is incomplete without the single-channel case.
- `docs/solutions/logic-errors/tiff-pending-channel-name-prefix-mismatch-2026-05-21.md` — sister "producer and consumer must agree on a token-derived name" pattern. That fix enforced `f"ch{ch_id}"` symmetry across `importer.py` and the workflow config dialog; this fix enforces `"0"` symmetry across `cross_format._extract_token` and `batch_tcspc_dialog._discover_bin_tokens`. Same anti-pattern (two files duplicate a parsing convention; one site falls back, the other doesn't), different specific contract.
- `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md` — structural rationale for why `batch_tcspc_dialog._discover_bin_tokens` and `add_layer_dialog._tcspc_discover_bin_tokens` (if it exists) must share the same fallback.
- `docs/solutions/logic-errors/add-layer-flat-discovery-duplicate-import.md` — sibling dialog `add_layer_dialog.py` that also parses `_ch` tokens; worth a follow-up code check that any equivalent token-discovery helper there mirrors the same `"0"` fallback.
- `docs/solutions/architecture-patterns/decay-write-path.md` — downstream consumer of whatever channel-name the parser emits.
- `docs/solutions/runtime-errors/multi-channel-dataset-load-numpy-array-truth-value-2026-05-22.md` — inverse single-channel edge case (multi-channel-only crash that single-channel datasets masked). Both illustrate that channel-count edge cases must be tested in both directions.
- `docs/solutions/logic-errors/batch-compress-development-lessons.md` — background on the "matcher refactor silently collapses per-input scope" pattern family.
