---
status: pending
priority: p1
issue_id: "001"
tags: [code-review, quality, tests]
dependencies: []
---

# Rename `tests/workflows/` → `tests/test_workflows/`

## Problem Statement

Every other subpackage in the repo follows the `tests/test_<subpackage>/` naming convention:

- `tests/test_io/`
- `tests/test_measure/`
- `tests/test_segment/`
- `tests/test_flim/`

Phase 1 added `tests/workflows/` — the only structural convention violation in the entire test tree. pytest collection still works (because the files inside are named `test_*.py`), but:

- Inconsistency breaks `grep -r tests/test_` scans
- Tab-completion hits the wrong directory
- Every new contributor will mentally stub over the mismatch
- The longer it stays, the more test files land inside, and the harder the rename gets

This is cheap to fix now and impossible to unsee later.

## Findings

- **pattern-recognition-specialist**: flagged as Critical. The only structural convention violation in the commit.
- `tests/workflows/__init__.py` + 6 test files all need to move together.
- No imports need to change — the test files import from `percell4.workflows.*`, not relative to the test package.

## Proposed Solutions

### Option A — Rename the directory (Recommended)

```bash
git mv tests/workflows tests/test_workflows
pytest tests/test_workflows/  # verify
```

- **Pros**: One-line fix, perfectly matches house style, git history preserved.
- **Cons**: None meaningful.
- **Effort**: Small (5 min).
- **Risk**: None — pytest re-discovers from `test_*.py` file patterns.

### Option B — Leave as is and document the exception

- **Pros**: Zero diff.
- **Cons**: Cements a permanent inconsistency; sets precedent for future subpackages.
- **Effort**: Trivial.
- **Risk**: Low churn, high long-term confusion.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `tests/workflows/` (directory) → `tests/test_workflows/`
- `tests/workflows/__init__.py`
- `tests/workflows/test_artifacts.py`
- `tests/workflows/test_channels.py`
- `tests/workflows/test_measurer_with_masks.py`
- `tests/workflows/test_models.py`
- `tests/workflows/test_qt_free_imports.py`
- `tests/workflows/test_read_channel.py`

**Follow-up:** `test_qt_free_imports.py:test_no_qt_imports_in_workflows_source` computes `here.parents[2] / "src" / "percell4" / "workflows"` — verify the rename doesn't affect the parent-count arithmetic. It should not (tests at `tests/test_workflows/test_qt_free_imports.py` still have `parents[2]` as the repo root).

## Acceptance Criteria

- [ ] `tests/workflows/` no longer exists
- [ ] `tests/test_workflows/` exists with all original test files
- [ ] `pytest tests/test_workflows/` passes all 52 tests
- [ ] `test_qt_free_imports.py` source-grep test still finds `src/percell4/workflows/`

## Work Log

- 2026-04-10 — Found by pattern-recognition-specialist during Phase 1 review.

## Resources

- Existing precedent: `tests/test_io/`, `tests/test_measure/`, `tests/test_segment/`, `tests/test_flim/`
- Review source: pattern-recognition-specialist Critical finding #1
