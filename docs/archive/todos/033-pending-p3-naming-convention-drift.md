---
status: pending
priority: p3
issue_id: "033"
tags: [code-review, sibling-convention, multi-select]
dependencies: []
---

# Naming drift: `_torn_down` / `_install` / `_uninstall` / `show` vs sibling `_finished` / `start` / `_cleanup_all`

## Problem Statement

`multi_select.py` introduces new vocabulary for patterns the sibling controllers already have names for.

Sibling patterns:
- `seg_qc.py:119` — `_finished` flag (checked at 518, 566, 585, 590, 597; set at 599).
- `seg_qc.py:123` — `start()` public entry.
- `seg_qc.py:309` — `_hide_existing_layers`.
- `seg_qc.py:332` — `_load_into_viewer`.
- `seg_qc.py:595` — `_finish`.
- `threshold_qc.py:146` — `start()`.
- `threshold_qc.py:772` — `_cleanup_all`.
- `threshold_qc.py:799` — `_close_preview_window`.

Multi-select uses:
- `multi_select.py:155` — `_torn_down` (sibling: `_finished`).
- `multi_select.py:163` — `show()` (sibling: `start()`).
- `multi_select.py:213` — `_install` (no sibling analog; closest is `_load_into_viewer`).
- `multi_select.py:240` — `_uninstall` (sibling: `_cleanup_all` / `_finish`).

Module docstring at `multi_select.py:27` advertises the new name as intentional ("Teardown is strict: set `_torn_down` flag…"). Either adopt the new vocabulary repo-wide (rename siblings to match) or rename multi-select to match siblings.

## Findings

- **pattern-recognition-specialist (P2 #2, #3):** Three-way convention drift; two siblings already agree.

## Proposed Solutions

### Option A — Align multi-select with siblings (Recommended)

Renames in `multi_select.py`:
- `_torn_down` → `_finished`
- `show()` → `start()`
- `_install` → `_load_into_viewer` (or `_enter_tool_mode`)
- `_uninstall` → `_cleanup_all`

- **Pros:** Three controllers read the same way; no sibling churn.
- **Cons:** Diff in multi_select + tests referencing these names.
- **Effort:** Small (30 min, mostly mechanical rename).
- **Risk:** Low.

### Option B — Rename siblings to match multi-select

Advertise the new vocabulary by updating `seg_qc.py` and `threshold_qc.py`.

- **Pros:** If the new names are genuinely better (e.g., `_torn_down` is more specific than `_finished`), adopt them everywhere.
- **Cons:** Larger diff; no clear evidence the new names ARE better.
- **Effort:** Medium.

### Option C — Document the divergence

- **Pros:** Zero code change.
- **Cons:** Three conventions of one is churn.

## Recommended Action

Option A. Siblings are the majority; multi-select is the newcomer.

## Technical Details

**Affected files:**
- `src/percell4/gui/multi_select.py:155, 163, 213, 240` + all call sites within the module
- `tests/test_gui_workflows/test_multi_select.py` — test fixtures/helpers that reference these names

## Acceptance Criteria

- [ ] Three controllers use the same vocabulary for lifecycle
- [ ] Module docstring updated to match the chosen names
- [ ] All tests pass

## Work Log

- 2026-04-23 — Flagged by pattern-recognition-specialist.

## Resources

- Sibling: `src/percell4/gui/workflows/single_cell/seg_qc.py`
- Sibling: `src/percell4/gui/threshold_qc.py`
