---
status: pending
priority: p3
issue_id: "035"
tags: [code-review, types, multi-select]
dependencies: []
---

# Type annotation polish bundle

## Problem Statement

Several type-annotation gaps in otherwise strictly-typed code. None is functional; each is a small regression from the module's general annotation discipline.

## Findings

**kieran-python-reviewer (P3-2, P3-3, P3-4, P3-5, P3-1):**

1. **`active_labels_layer_or_none` lacks return type annotation.**
   - `multi_select.py:80` (Protocol) and `viewer.py:448` (impl) both bare.
   - **Fix:** `def active_labels_layer_or_none(self) -> "napari.layers.Labels | None": ...` (forward-ref string because napari is a lazy import).

2. **`self._layer = None` has no annotation.**
   - `multi_select.py:154`.
   - **Fix:** `self._layer: "napari.layers.Labels | None" = None`.

3. **`self._mouse_cb: Callable | None = None` is bare `Callable`.**
   - `multi_select.py:153`.
   - **Fix:** `Callable[..., None]` (or the napari event signature if worth pinning).

4. **`assert self._window is not None` in `show()`.**
   - `multi_select.py:182`. Runs in prod; `-O` would strip it.
   - **Fix:** `if self._window is None: raise RuntimeError("show() called before _build_window")` — or trust the type checker.

5. **`# noqa: BLE001` appears four times.**
   - `multi_select.py:253, 323, 504` (last is `viewer.py`). Either move to a file-level noqa with a single justifying comment, or narrow the catches (pairs with todo #029).

## Proposed Solutions

### Option A — Apply all five fixes as one polish commit (Recommended)

- **Pros:** Zero behavioral change; all annotation-only.
- **Cons:** None.
- **Effort:** Small (20 min).
- **Risk:** None — pure typing additions.

### Option B — Skip #4 (`assert` → `RuntimeError`) as a judgment call

Some teams prefer the `assert`; it documents a postcondition.

## Recommended Action

Option A, but `assert` (#4) can stay if the team prefers — it's aesthetic.

## Technical Details

**Affected files:**
- `src/percell4/gui/multi_select.py:80, 153, 154, 182, 253, 323`
- `src/percell4/gui/viewer.py:448, 504`

## Acceptance Criteria

- [ ] `active_labels_layer_or_none` has a return type on Protocol and impl
- [ ] `_layer` has a type annotation
- [ ] `_mouse_cb` has a specific `Callable[...]` signature
- [ ] `BLE001` noqa's are either consolidated at file level or narrowed per callsite
- [ ] (Optional) `assert self._window is not None` replaced with explicit raise

## Work Log

- 2026-04-23 — Flagged by kieran-python-reviewer.
