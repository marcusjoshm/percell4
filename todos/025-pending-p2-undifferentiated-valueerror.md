---
status: pending
priority: p2
issue_id: "025"
tags: [code-review, error-handling, application]
dependencies: []
---

# Use cases raise bare ValueError for all failure modes

## Problem Statement

Every use case raises `ValueError` for "no dataset loaded", "no segmentation", "no mask", and domain-specific failures. Callers cannot distinguish failure modes without string matching. `ComputePhasor.execute` also loses the original traceback (missing `from exc`).

## Findings

- **Source**: Kieran Python reviewer
- **Locations**: All 10 use case files in `application/use_cases/`

## Proposed Solutions

### Option A: Define exception hierarchy in domain (Recommended)
Create `domain/errors.py` with `NoActiveDatasetError`, `MissingSegmentationError`, `MissingMaskError`. Use cases raise the specific type. Callers catch the specific type.
- Effort: Small
- Risk: Low

## Acceptance Criteria

- [ ] Each use case raises a specific exception type for each failure mode
- [ ] `raise ValueError(...) from exc` used where exceptions are re-raised
- [ ] Callers can catch specific exceptions without string matching
