---
status: pending
priority: p2
issue_id: "005"
tags: [code-review, consistency, workflows]
dependencies: []
---

# Align `workflows/models.py` dataclass style with `io/models.py`

## Problem Statement

The workflows subpackage adopts a decorator style nowhere else in the codebase uses:

- `workflows/models.py` — `@dataclass(kw_only=True, slots=True, frozen=True)` uniformly
- `io/models.py` — bare `@dataclass(frozen=True)` or `@dataclass`
- `model.py` — bare `@dataclass` for `StateChange`
- `measure/` dataclasses — bare `@dataclass`

So PerCell4's established style is: `frozen=True` where immutability matters, **no** `slots`, **no** `kw_only`, positional/default args allowed. The `kw_only=True, slots=True` choice in `workflows/` is technically defensible (slots save memory, kw_only forces callsite clarity) but gives two different readers of the codebase two different mental models.

## Findings

- **pattern-recognition-specialist** (S3): Should-level inconsistency. Cites `io/models.py:13,49,74,85,124,138,146,184,192,200` as the established pattern.
- The `workflows/` choice is self-consistent (every dataclass uses the same decorator triple) but cross-subpackage inconsistent.

## Proposed Solutions

### Option A — Match `io/models.py` style (Recommended for consistency)

Drop `slots=True, kw_only=True` from all `workflows/` dataclasses. Keep `frozen=True` on `CellposeSettings`, `ThresholdingRound`, `WorkflowConfig` (the "recipe" types). Leave `WorkflowDatasetEntry`, `RunMetadata`, `FailureRecord` as bare `@dataclass` since they are mutable runtime state.

- **Pros**: Single codebase style. New contributors see one pattern. Matches ~20 existing dataclasses.
- **Cons**: Loses `slots` memory savings (tiny — these dataclasses have O(10) instances per run). Loses `kw_only` callsite discipline (but the tests already construct by keyword).
- **Effort**: Small.
- **Risk**: Low — `__post_init__` still runs, field defaults still apply, test assertions don't change.

### Option B — Retrofit `io/models.py` to match `workflows/` style

Adopt `kw_only=True, slots=True, frozen=True` as the new house style and update `io/models.py`, `model.py`, etc.

- **Pros**: Locks in the modern Python 3.12 style.
- **Cons**: Much larger change. Touches shipped pipeline code. Risks breaking positional callers in existing code. Out of scope for a Phase 1 follow-up.
- **Effort**: Large.
- **Risk**: Medium.

### Option C — Leave as-is and document the divergence

- **Pros**: Zero diff.
- **Cons**: Permanent inconsistency. Future subpackages won't know which convention to follow.

## Recommended Action

Option A.

## Technical Details

**Files to change:**
- `src/percell4/workflows/models.py`:
  - `CellposeSettings` line 52: `@dataclass(frozen=True)`
  - `ThresholdingRound` line 83: `@dataclass(frozen=True)`
  - `WorkflowDatasetEntry` line 122: `@dataclass`
  - `WorkflowConfig` line 148: `@dataclass(frozen=True)`
  - `RunMetadata` line 172: `@dataclass`
- `src/percell4/workflows/failures.py`:
  - `FailureRecord` line 26: `@dataclass`
- `tests/workflows/test_models.py`:
  - The `test_round_is_frozen` / `test_config_is_frozen` tests still work.
  - Call sites that construct dataclasses by keyword continue to work; any positional construction in tests is already by keyword.

**Verify:** Run `pytest tests/workflows/` after the change to confirm nothing breaks.

## Acceptance Criteria

- [ ] All `workflows/` dataclass decorators match `io/models.py` conventions
- [ ] `test_round_is_frozen` and `test_config_is_frozen` still pass (frozen preserved)
- [ ] `test_artifacts.py` round-trip test still passes
- [ ] `WorkflowDatasetEntry` can still be constructed with all its Phase 1 fields
- [ ] No `slots=True` or `kw_only=True` remain in `workflows/`

## Work Log

- 2026-04-10 — Flagged by pattern-recognition-specialist.

## Resources

- Reference: `src/percell4/io/models.py` (the existing convention)
- Review source: pattern-recognition-specialist Should #3
