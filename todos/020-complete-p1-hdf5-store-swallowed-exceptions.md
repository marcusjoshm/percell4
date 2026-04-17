---
status: complete
priority: p1
issue_id: "020"
tags: [code-review, error-handling, adapters]
dependencies: []
---

# HDF5 store swallows real exceptions with bare except

## Problem Statement

In `src/percell4/adapters/hdf5_store.py` line 164, `except (KeyError, Exception):` makes the `KeyError` clause redundant and silently swallows every error during group column merging. This masks bugs in the merge logic — if a DataFrame is corrupt or a column name collision occurs, the error disappears silently.

## Findings

- **Source**: Kieran Python reviewer
- **Location**: `src/percell4/adapters/hdf5_store.py`, `read_group_columns` method
- **Impact**: Silent data corruption — group columns may be silently dropped without any log entry
- **Learnings reference**: docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md documented that swallowed errors are a recurring issue class

## Proposed Solutions

### Option A: Narrow the except clause (Recommended)
Catch only `KeyError` (for missing /groups/ path). Let other exceptions propagate.
- Effort: Small
- Risk: Low

### Option B: Catch + log
Catch `Exception`, log the error, re-raise or return partial results.
- Effort: Small
- Risk: Low

## Acceptance Criteria

- [ ] `except (KeyError, Exception)` replaced with `except KeyError`
- [ ] Other exceptions from group merging propagate to callers
- [ ] Existing tests still pass
