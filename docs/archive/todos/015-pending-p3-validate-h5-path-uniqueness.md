---
status: pending
priority: p3
issue_id: "015"
tags: [code-review, correctness, workflows]
dependencies: []
---

# `WorkflowConfig.__post_init__` validates dataset-name uniqueness but not `h5_path` uniqueness

## Problem Statement

`src/percell4/workflows/models.py:159-169` validates that dataset **names** are unique across the config, but not that `h5_path` values are unique. Two entries with different names but the same `h5_path` silently pass validation — and Phase 2 compression (plan line 446) will clobber the same file twice.

Example:

```python
WorkflowConfig(
    datasets=[
        WorkflowDatasetEntry(name="A", source=H5_EXISTING, h5_path=Path("/tmp/same.h5"), channel_names=[...]),
        WorkflowDatasetEntry(name="B", source=H5_EXISTING, h5_path=Path("/tmp/same.h5"), channel_names=[...]),
    ],
    ...
)
# Accepted. Phase 1 segments, writes /labels/cellpose_qc to /tmp/same.h5.
# Phase 2 segments, writes /labels/cellpose_qc to /tmp/same.h5 — overwrite.
# Phase 7 measures DS A and DS B from the same h5 — two rows in the
# output DataFrame with the same cell data but different `dataset` values.
```

## Findings

- **kieran-python-reviewer** (N24): missing uniqueness check on `h5_path`.

## Proposed Solutions

### Option A — Add h5_path uniqueness check (Recommended)

```python
def __post_init__(self) -> None:
    if not self.datasets:
        raise ValueError("at least one dataset is required")
    if not self.thresholding_rounds:
        raise ValueError("at least one thresholding round is required")
    names = [r.name for r in self.thresholding_rounds]
    if len(set(names)) != len(names):
        raise ValueError(f"thresholding round names must be unique: {names}")
    ds_names = [d.name for d in self.datasets]
    if len(set(ds_names)) != len(ds_names):
        raise ValueError(f"dataset names must be unique: {ds_names}")
    paths = [str(d.h5_path.resolve()) for d in self.datasets]
    if len(set(paths)) != len(paths):
        raise ValueError(f"dataset h5_path values must be unique: {paths}")
```

Note `.resolve()` to catch relative-vs-absolute aliasing (`./foo.h5` vs `/tmp/foo.h5`).

- **Pros**: Fails fast at config time instead of mysterious Phase 7 duplication.
- **Cons**: `.resolve()` touches the filesystem — for `tiff_pending` entries where the h5 doesn't yet exist, `resolve()` still works because it doesn't require the file to exist (just resolves symlinks in existing parts of the path).
- **Effort**: Trivial.
- **Risk**: Low.

### Option B — Check only stringified paths, not resolved

- **Pros**: No filesystem calls.
- **Cons**: Misses `./foo.h5` vs `/abs/path/foo.h5` aliasing.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/workflows/models.py:169` — append the new check
- `tests/test_workflows/test_models.py` — add a test that constructs two entries with the same h5_path and asserts ValueError

## Acceptance Criteria

- [ ] `WorkflowConfig(..., datasets=[A, B])` with `A.h5_path == B.h5_path` raises ValueError
- [ ] Symlink / relative-path aliasing is caught via `.resolve()`
- [ ] Existing tests still pass
- [ ] New test `test_config_rejects_duplicate_h5_paths` added

## Work Log

- 2026-04-10 — Flagged by kieran-python-reviewer.

## Resources

- `src/percell4/workflows/models.py:159-169`
- Review source: kieran-python-reviewer N24
