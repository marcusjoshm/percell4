---
status: pending
priority: p2
issue_id: "027"
tags: [code-review, sibling-convention, multi-select]
dependencies: []
---

# `MultiLabelSelectController` is a plain object; sibling controllers subclass `QObject`

## Problem Statement

`multi_select.py:131` — `class MultiLabelSelectController:` — plain object.

Siblings explicitly subclass `QObject`:
- `gui/threshold_qc.py:71` — `class ThresholdQCController(QObject):`
- `gui/workflows/single_cell/seg_qc.py:69` — `class SegmentationQCController(QObject):`

The `seg_qc.py:1-7` docstring names this as the established pattern ("long-running interactive flows in PerCell4 are `QObject` controllers that build their own `QMainWindow` on the shared `ViewerWindow`").

Why `QObject` matters for this controller:
- Holds a `QTimer` child (`multi_select.py:403` parents it to `self._window`).
- Takes Qt slots (`accept`, `cancel` connected as slots at lines 379/384).
- Retained by `ViewerWindow._multi_select_controller` (`viewer.py:80`).

Works today because the `QMainWindow` owns the `QTimer`, but:
- Silently breaks the sibling convention.
- Cannot add signals (e.g., `completed: Signal`) without a refactor.

## Findings

- **pattern-recognition-specialist (P1 #1):** Convention drift vs two siblings. Downgraded here to P2 because no current functional regression — future-proofing + readability.

## Proposed Solutions

### Option A — Subclass `QObject` (Recommended)

```python
class MultiLabelSelectController(QObject):
    def __init__(self, ..., parent: "QObject | None" = None) -> None:
        super().__init__(parent)
        ...
```

- **Pros:** Matches siblings; enables future `Signal`s; cleanup via parent ownership.
- **Cons:** Minor — slight test-construction change if the tests pass a parent.
- **Effort:** Small (15 min).
- **Risk:** None — no current callers rely on it being non-QObject.

### Option B — Document the divergence in the class docstring

If the controller genuinely doesn't need Qt inheritance (no signals planned), say so explicitly.

- **Pros:** Zero code change.
- **Cons:** Still inconsistent.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/gui/multi_select.py:131` (class declaration)
- Possibly `tests/test_gui_workflows/test_multi_select.py` construction fixtures

## Acceptance Criteria

- [ ] `MultiLabelSelectController(QObject)` declaration matches siblings
- [ ] Existing tests still pass
- [ ] `super().__init__(parent)` called in `__init__`

## Work Log

- 2026-04-23 — Flagged by pattern-recognition-specialist.

## Resources

- `src/percell4/gui/workflows/single_cell/seg_qc.py:69` — sibling
- `src/percell4/gui/threshold_qc.py:71` — sibling
