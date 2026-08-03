---
status: pending
priority: p2
issue_id: "029"
tags: [code-review, defensiveness, multi-select]
dependencies: []
---

# Narrow the bare `contextlib.suppress(Exception)` blocks and the click-callback `try/except`

## Problem Statement

`_uninstall()` (`multi_select.py:240-279`) uses six bare `contextlib.suppress(Exception)` blocks. Several guard against failure modes that don't exist:

- `:265` `remove_staged_overlay()` — callee at `viewer.py:528-535` already checks `_is_alive` + `if _OVERLAY_LAYER_NAME in self._viewer.layers`. Redundant outer suppression.
- `:269` `resume_selected_label_forwarding()` — callee is literally `self._selected_label_forwarding_suspended = False` (`viewer.py:461-463`). Cannot raise.
- `:273` `set_workflow_locked(False)` — pure Qt widget manipulation on `self` (`main_window.py:1172`). Raises only if the launcher Qt object is gone — bigger problem than muting.
- `:261` `layer.mode = self._prior_mode` — defensible (torn-down C++ Qt object in napari can raise).
- `:278` `self._window.close()` — defensible (deleted C++ object).
- `:258` `mouse_drag_callbacks.remove()` — the PR explicitly suppresses `ValueError`, which fits: controlled narrow catch. Keep.

Additionally, the click callback at `multi_select.py:316-324` swallows bare `Exception` on `layer.get_value()` with `# noqa: BLE001`:

```python
try:
    value = layer_.get_value(...)
except Exception:  # noqa: BLE001
    return
```

There's a dedicated test (`test_get_value_raising_does_not_crash_the_callback` at `test_multi_select.py:384-393`). But no cited napari bug/issue documents when `get_value` raises — the test is protecting the try/except from itself. Either cite a specific napari failure mode in a comment, or delete both the try/except and the test.

## Findings

- **kieran-python-reviewer (P2-2):** "Bare `suppress(Exception)` is a smell — siblings let exceptions propagate and log. Three sites can't raise."
- **code-simplicity-reviewer (P2):** "Five `suppress(Exception)` in `_uninstall` — defense-in-depth against what?"
- **code-simplicity-reviewer (P2):** "Cite the napari bug the `try` catches or delete."

## Proposed Solutions

### Option A — Narrow or delete the ones that can't raise (Recommended)

1. Delete `contextlib.suppress(Exception)` at `:265`, `:269`, `:273`. Let exceptions propagate.
2. Keep `:261` and `:278` but narrow to `suppress(RuntimeError)` (the canonical "Qt C++ object deleted" exception).
3. Keep `:258` as-is (`suppress(ValueError)` is already narrow and correct).
4. For the click callback at `:316-324`: add a one-line comment citing the napari failure mode, OR delete the try/except and the corresponding test. Default to delete if no known failure mode exists.

- **Pros:** Real bugs become visible; aligns with sibling style; reduces over-defensive noise.
- **Cons:** A crash you weren't seeing might appear — that's the point.
- **Effort:** Small (30 min).
- **Risk:** Low — you'll see bugs faster.

### Option B — File-level `# noqa: BLE001` with justifying comment

If the click callback's broad catch is intentional (e.g., napari's coordinate math can surprise), move the noqa to a file-level header and document the reason once.

- **Pros:** Avoids per-line ceremony.
- **Cons:** Coarser; doesn't address the unused suppressors.

## Recommended Action

Option A. Decide per-site; do not ship blanket `Exception` swallows where the callee can't raise.

## Technical Details

**Affected files:**
- `src/percell4/gui/multi_select.py:240-279` (six suppress blocks; review each)
- `src/percell4/gui/multi_select.py:316-324` (click callback try/except)
- `tests/test_gui_workflows/test_multi_select.py:384-393` (possibly delete `test_get_value_raising_does_not_crash_the_callback`)

## Acceptance Criteria

- [ ] No `contextlib.suppress(Exception)` remains for callees that cannot raise
- [ ] Remaining suppressions are narrowed (`RuntimeError` / `ValueError`)
- [ ] Click-callback try/except either has an explanatory comment OR is deleted
- [ ] All existing tests still pass

## Work Log

- 2026-04-23 — Flagged by kieran-python-reviewer + code-simplicity-reviewer.
