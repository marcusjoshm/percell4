---
status: pending
priority: p2
issue_id: "023"
tags: [code-review, performance, adapters]
dependencies: []
---

# HDF5 adapter opens/closes file per operation

## Problem Statement

`Hdf5DatasetRepository._store()` creates a new `DatasetStore` instance on every call. Read methods like `read_labels`, `read_mask`, `read_array` each open and close the HDF5 file independently. `MeasureCells.execute()` triggers 4 separate file open/close cycles. For 3072x3072 datasets, each HDF5 open involves reading B-tree metadata: ~50-200ms of unnecessary I/O overhead per measurement.

## Findings

- **Source**: Performance oracle
- **Location**: `src/percell4/adapters/hdf5_store.py`, `_store()` method (line 25)

## Proposed Solutions

### Option A: Cache DatasetStore per path (Recommended)
Keep a `dict[Path, DatasetStore]` in the repository. Reuse store instances across method calls. Invalidate on dataset close.
- Effort: Small
- Risk: Low

### Option B: Add session/unit-of-work context manager
Use cases call `with repo.session(handle):` to batch multiple reads under one HDF5 open.
- Effort: Medium
- Risk: Low

## Acceptance Criteria

- [ ] Multiple reads within a use case share a single HDF5 file handle
- [ ] No measurable regression in I/O latency
