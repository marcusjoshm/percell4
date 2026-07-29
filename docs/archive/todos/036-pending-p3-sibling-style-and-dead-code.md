---
status: pending
priority: p3
issue_id: "036"
tags: [code-review, sibling-convention, multi-select, dead-code]
dependencies: []
---

# Sibling-style nits and dead-code removal

## Problem Statement

Bundle of small convention drifts and genuinely dead code flagged across reviewers.

## Findings

### Sibling-style drift

- **Button handler indirection** (pattern-recognition-specialist P3 #4): siblings route through `_on_<verb>_clicked` private handlers (`seg_qc.py:302, 292`, `threshold_qc.py:313, 317, 321`). Multi-select connects directly to public `accept`/`cancel` (`multi_select.py:379, 384`). Defensible since `accept`/`cancel` are the public API and tests need them; documenting the deviation is enough.

- **`contextlib.suppress` vs `try/except Exception: pass`** (pattern-recognition-specialist P3 #8): Multi-select uses `contextlib.suppress` eight times; siblings use `try/except Exception: pass/logger.debug(...)` throughout. First use of `contextlib.suppress` anywhere in `src/percell4/gui/`. Pick one idiom package-wide; pairs with narrowing work in todo #029.

- **QTimer instance vs `QTimer.singleShot`** (pattern-recognition-specialist P3 #7): Multi-select parents a `QTimer(window)` instance with `setSingleShot(True)` (`multi_select.py:403`); siblings use stateless `QTimer.singleShot(0, ...)` gated by a `_refresh_pending: bool` (`seg_qc.py:499-504`, `threshold_qc.py:565, 572`). Kieran actually prefers the new instance-based pattern because Qt cleans the timer via parent ownership. **If the new pattern is preferred, migrate siblings to match it** rather than reverting this one.

- **Menu ellipsis** (pattern-recognition-specialist P3 #18): Menu action text `"&Multi-select..."` (three ASCII dots) should be `"&Multi-select…"` (U+2026) per Qt/macOS convention. Window title at `multi_select.py:341` correctly has no ellipsis.

- **PEP 695 `type` alias** (pattern-recognition-specialist P3 #14): `multi_select.py:58` uses `type LabelId = int`. Only `type X = Y` alias in the repo. Either adopt repo-wide (document in `gui/CLAUDE.md`) or change to plain `LabelId = int`. Prefer plain assignment for now to match repo norm; reopen if team wants PEP 695 adoption.

### Dead / redundant code

- **`_layer` fallback may be dead** (code-simplicity-reviewer P2): If the viewer is dead, the captured `self._layer` is also dead — you can't safely operate on it. Options: (a) annotate it properly (see todo #035) and rely on it with `try/except RuntimeError`; (b) delete it and rely on `active_labels_layer_or_none()` returning None. Pick one.

- **`test_numpy_integer_label_coerces_to_int`** (code-simplicity-reviewer P2): Overspecified — tests implementation (storage type), not behavior. Either delete, or collapse into `test_left_click_on_label_toggles_into_staging` by passing a `np.int64` and asserting the final selection contains the right id (downstream consumers don't care about Python-vs-numpy int storage).

- **Duplicate `test_middle_click_is_ignored` and `test_right_click_is_ignored`** (code-simplicity-reviewer P3): Both cover the `if event.button != 1` branch. Merge into a parametrized test, or keep one.

### Agent-native docs note (already partially covered in todo #034)

- **`launch_multi_select_tool` returns a collapsed `bool`** (agent-native P3): Three distinct failure modes (workflow locked, viewer dead, no labels layer) collapse into one `False`. For a lab desktop app with no scripting entry point yet, this is over-engineering to address now. Keep for later.

## Proposed Solutions

### Option A — Apply drift fixes and delete dead code (Recommended)

1. Change menu label to `"&Multi-select…"` (U+2026).
2. Change `type LabelId = int` → `LabelId = int` unless team wants PEP 695.
3. Delete `self._layer` field OR give it a `try/except RuntimeError` wrapper at the fallback site (`multi_select.py:256`). Prefer delete.
4. Delete `test_numpy_integer_label_coerces_to_int` or merge with a toggle test.
5. Merge button-test duplicates (`test_multi_select.py:350-364`).
6. Either: migrate siblings' QTimers to the instance+parent pattern **OR** revert multi-select's timer to `QTimer.singleShot`. Decide in one follow-up, not this one.

- **Pros:** Consistent codebase; less code.
- **Cons:** Diff touches several files for small wins.
- **Effort:** Small (30-45 min).
- **Risk:** None.

### Option B — Accept the drifts; delete only truly dead code

- **Pros:** Smaller diff.
- **Cons:** Consistency nits linger.

## Recommended Action

Option A, excepting the QTimer decision (separate tracking if pursued).

## Technical Details

**Affected files:**
- `src/percell4/interfaces/gui/main_window.py:128` (ellipsis)
- `src/percell4/gui/multi_select.py:58` (type alias)
- `src/percell4/gui/multi_select.py:154, 256` (`_layer` fallback)
- `tests/test_gui_workflows/test_multi_select.py:350-364, 406-417` (tests)

## Acceptance Criteria

- [ ] Menu label uses `…` (U+2026)
- [ ] `LabelId = int` (no `type` keyword) unless PEP 695 adopted repo-wide
- [ ] `_layer` fallback either has explicit `RuntimeError` handling or is removed
- [ ] Duplicate/over-specific tests consolidated
- [ ] All existing tests still pass

## Work Log

- 2026-04-23 — Bundled from pattern-recognition-specialist, code-simplicity-reviewer, agent-native-reviewer.
