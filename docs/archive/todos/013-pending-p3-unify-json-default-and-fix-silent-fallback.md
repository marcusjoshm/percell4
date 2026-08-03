---
status: pending
priority: p3
issue_id: "013"
tags: [code-review, quality, workflows]
dependencies: []
---

# Unify `_json_default` between `artifacts.py` and `run_log.py`; fix silent `str()` fallback

## Problem Statement

Two near-identical helpers exist:

- `src/percell4/workflows/artifacts.py:95-103` — `_json_default` raises `TypeError` on unknown types
- `src/percell4/workflows/run_log.py:73-80` — `_json_default` returns `str(obj)` as a fallback

The silent `str()` fallback in `run_log.py` is a bug magnet: a numpy scalar, a custom exception, a Path-with-typo — all get coerced to a quoted string and hide the bug in the calling code.

Once the fallback is fixed to match artifacts.py's `raise TypeError`, the two helpers are byte-for-byte identical and should share one implementation.

## Findings

- **kieran-python-reviewer** (N2): duplication + silent fallback asymmetry.
- **code-simplicity-reviewer** (Simplify #8, #9): same — unify and raise on unknown.

## Proposed Solutions

### Option A — Consolidate into `artifacts.py`, import from `run_log.py` (Recommended)

Make `artifacts._json_default` public (drop the underscore) or keep private and do `from percell4.workflows.artifacts import _json_default as _json_default`. Replace `run_log.py`'s local implementation with the import. Fix the fallback at the same time.

```python
# run_log.py
from percell4.workflows.artifacts import _json_default as _json_default
```

- **Pros**: Single source of truth. Fixes the silent-fallback bug. ~10 LOC removed.
- **Cons**: Creates a within-subpackage dependency. Fine since both live under `workflows/`.
- **Effort**: Trivial.

### Option B — Keep duplicated helpers, only fix the fallback

- **Pros**: Preserves module independence.
- **Cons**: Two places to keep in sync. A future `datetime` subclass change needs two edits.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/workflows/run_log.py` — remove `_json_default`, import from artifacts
- Consider promoting the helper to a public name if Phase 2 also needs it

## Acceptance Criteria

- [ ] `run_log._json_default` is gone; `run_log.py` imports from `artifacts`
- [ ] Logging an unknown type (e.g. a custom class) raises `TypeError` instead of silently coercing to `str`
- [ ] Existing run_log write paths (Path, datetime, StrEnum) still work
- [ ] Adding a test to verify the new error behavior

## Work Log

- 2026-04-10 — Duplication + silent-fallback footgun flagged by two reviewers.

## Resources

- Review source: kieran-python-reviewer N2, code-simplicity-reviewer Simplify #8/#9
