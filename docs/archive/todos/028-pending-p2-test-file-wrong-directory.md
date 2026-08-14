---
status: pending
priority: p2
issue_id: "028"
tags: [code-review, tests, quality]
dependencies: []
---

# `test_multi_select.py` is in `test_gui_workflows/` but multi-select is not a workflow

## Problem Statement

`tests/test_gui_workflows/test_multi_select.py` lives under the `test_gui_workflows/` package. From `tests/test_gui_workflows/conftest.py:1-8`:

> "This folder is for tests that need `BaseWorkflowRunner` fixtures (`fake_host`, `sample_config`, `run_folder`, `sample_metadata`, `collect_events`)."

`test_multi_select.py` uses **none** of those fixtures — only `qtbot`. Keeping it here trains future maintainers to think multi-select is workflow infrastructure.

Two secondary issues:

1. **Class-based tests in flat-function directory.** `test_multi_select.py` uses six test classes (`TestStagingBuffer`, `TestControllerConstruction`, `TestShowGuards`, `TestInstallTeardown`, `TestToggleRefresh`, `TestClickCallback`). Every other file in `tests/test_gui_workflows/` uses flat `def test_...` functions (see `test_config_dialog.py`, `test_interactive_runner.py`, `test_single_cell_runner.py`, `test_base_runner_*.py`). `grep -c "^class Test"` across the directory: only `test_multi_select.py` has any.

2. **Unused `qtbot` fixture in some tests.** `TestControllerConstruction.test_empty_selection_pre_fill` (`test_multi_select.py:127`) accepts `qtbot` and never uses it.

## Findings

- **pattern-recognition-specialist (P2 #10, #11):** Directory mismatch + class style divergence.

## Proposed Solutions

### Option A — Move to `tests/test_gui/test_multi_select.py` (Recommended)

1. Create `tests/test_gui/` if it doesn't exist (other `gui/` tests currently live alongside — check first).
2. `git mv tests/test_gui_workflows/test_multi_select.py tests/test_gui/test_multi_select.py`
3. Flatten the six test classes into prefixed flat functions (`test_buffer_empty_pre_fill`, `test_buffer_toggle_adds`, etc.).
4. Remove `qtbot` from test functions that don't use it.

- **Pros:** Correct taxonomy; matches sibling test style; unambiguous fixture expectations.
- **Cons:** Touches ~600 lines of test re-indent; mechanical but real.
- **Effort:** Medium (1-2 hr).
- **Risk:** Low — tests are the work, not production behavior.

### Option B — Move only; keep class structure

- **Pros:** Smaller diff.
- **Cons:** Still inconsistent with directory convention.

### Option C — Stay in `test_gui_workflows/`; flatten classes

Works if you argue the directory naming is imprecise. But the conftest explicitly declares its fixture scope.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `tests/test_gui_workflows/test_multi_select.py` → `tests/test_gui/test_multi_select.py` (rename)
- Internal restructure: 6 classes → ~29 flat functions

Look at `tests/test_config_dialog.py` for flat-function style with `qtbot`.

## Acceptance Criteria

- [ ] Test file no longer lives in `test_gui_workflows/`
- [ ] Test functions are flat (no `class Test…`)
- [ ] Unused `qtbot` parameter is removed from tests that don't need it
- [ ] `pytest` discovers and runs all 29 tests in the new location

## Work Log

- 2026-04-23 — Flagged by pattern-recognition-specialist.

## Resources

- Directory convention: `tests/test_gui_workflows/conftest.py:1-8`
- Flat-function style example: `tests/test_gui_workflows/test_config_dialog.py` (30+ flat tests)
