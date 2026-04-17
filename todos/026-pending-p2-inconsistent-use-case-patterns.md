---
status: pending
priority: p2
issue_id: "026"
tags: [code-review, patterns, application]
dependencies: []
---

# Inconsistent patterns across use cases and task panels

## Problem Statement

Use cases have 3 different return conventions (None, domain object, DataFrame, Result dataclass). Task panels have 3 different data access patterns (_get_repo, _get_store, delegate to launcher). SegmentCells deviates from execute() pattern. DataPanel bypasses DatasetRepository port and accesses raw DatasetStore.

## Findings

- **Source**: Pattern recognition specialist
- **Key inconsistencies**:
  - Return types: CloseDataset→None, LoadDataset→DatasetHandle, MeasureCells→DataFrame, others→Result dataclass
  - Panel data access: AnalysisPanel uses _get_repo(), DataPanel uses _get_store(), IoPanel delegates everything
  - DataPanel bypasses the DatasetRepository port, accessing self._launcher._current_store directly

## Proposed Solutions

### Option A: Standardize use case returns + panel access (Recommended)
All use cases return a Result dataclass (even if minimal). All panels use _get_repo() for data access through the port. DataPanel switches from _get_store() to _get_repo().
- Effort: Medium
- Risk: Low

## Acceptance Criteria

- [ ] All use cases return a typed Result dataclass
- [ ] All task panels access data through the same pattern
- [ ] DataPanel uses DatasetRepository, not raw DatasetStore
