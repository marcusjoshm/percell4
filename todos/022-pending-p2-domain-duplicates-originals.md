---
status: pending
priority: p2
issue_id: "022"
tags: [code-review, architecture, duplication]
dependencies: []
---

# domain/ duplicates original modules (2,680 LOC)

## Problem Statement

Every file in `domain/` is identical to its original in `measure/`, `flim/`, `io/`, `segment/`. The originals are replaced with re-export shims. This is pure directory reshuffling — the originals were already pure functions with no GUI or HDF5 coupling. The duplication adds 12 shim files, 12 duplicated files, and 12 `__init__.py` files.

## Findings

- **Source**: Code simplicity reviewer
- **Impact**: ~2,680 LOC of duplication. Any fix to a domain function must be applied in two places until shims are removed.

## Proposed Solutions

### Option A: Delete domain/, keep originals as canonical (Recommended)
The existing `measure/`, `flim/`, `io/`, `segment/` ARE the domain — they were already clean. Update import-linter contracts and application/ imports to reference the original locations.
- Effort: Medium
- Risk: Medium — many import path changes

### Option B: Delete originals, keep domain/ as canonical
Remove the original packages and make all imports go through `domain/`. Fix all tests and GUI code to use new paths.
- Effort: Large
- Risk: Medium

## Acceptance Criteria

- [ ] No duplicate module pairs exist (one canonical location per module)
- [ ] Import-linter contracts still pass
- [ ] All tests pass with the canonical import paths
