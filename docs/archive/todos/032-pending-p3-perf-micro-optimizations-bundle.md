---
status: pending
priority: p3
issue_id: "032"
tags: [code-review, performance, multi-select]
dependencies: []
---

# Performance micro-optimizations bundle

## Problem Statement

The performance reviewer quantified multi-select at ~1-2 ms per refresh at 500 staged cells — well within budget. But three trivial allocations can be removed with zero risk and no behavioral change. Bundled here so they can be done together or dropped together.

## Findings

**performance-oracle (P2-1 / P3-1 / P3-3 / P2-3):**

1. **`StagingBuffer.is_dirty()` allocates a fresh frozenset every refresh.**
   - `multi_select.py:128` — `return frozenset(self.current) != self.initial_ids`.
   - `set` compares elementwise with `frozenset` without needing the copy.
   - **Fix:** `return self.current != self.initial_ids`.

2. **`update_staged_overlay` allocates `list(_STAGED_COLOR)` per staged id.**
   - `viewer.py:507-526` — per-toggle dict rebuild.
   - **Fix:** Hoist `_STAGED_COLOR_LIST = list(_STAGED_COLOR)` as a module constant; reuse. Shaves ~20-30%.

3. **`set_selection(list(snap))` round-trips frozenset → list → frozenset.**
   - `multi_select.py:203` passes `list(snap)`; `CellDataModel.set_selection` then does `frozenset(label_ids)`.
   - **Fix (if todo #021 lands):** `session.set_selection(snap)` directly. Otherwise tighten the `SelectionSink` Protocol to `Iterable[int]` and pass the frozenset.

4. **Pre-existing: `CellTable._highlight_selected_rows` calls `selectRow` N times (pre-existing, this PR elevates visibility).**
   - `peer_views/cell_table.py:286-297`.
   - **Fix:** accumulate a `QItemSelection` and call `selectionModel().select(sel, ClearAndSelect | Rows)` once.
   - Note: pre-existing; defer if this PR doesn't actually commit 500-cell selections in practice.

## Proposed Solutions

### Option A — Apply fixes 1 and 2 unconditionally; 3 after todo #021; 4 as a follow-up (Recommended)

- **Pros:** Zero-risk wins; 2 is the largest (~30%); 1 is free; 3 falls out naturally after #021.
- **Cons:** None.
- **Effort:** Trivial (10 min for 1 + 2; 4 is Medium).
- **Risk:** None for 1, 2, 3. 4 touches a pre-existing selection flow — defer.

### Option B — Ship all four as one bundle

- **Pros:** Single commit.
- **Cons:** #4 is pre-existing and benefits from profiling first.

## Recommended Action

Option A. Do 1 + 2 now. Do 3 alongside #021. Defer 4 until actual slowdown observed.

## Technical Details

**Affected files:**
- `src/percell4/gui/multi_select.py:128` (is_dirty)
- `src/percell4/gui/viewer.py` (module constant + usage in `add_staged_overlay`, `update_staged_overlay`)
- `src/percell4/gui/multi_select.py:203` (depends on #021)
- `src/percell4/interfaces/gui/peer_views/cell_table.py:286-297` (pre-existing; follow-up)

## Acceptance Criteria

- [ ] `is_dirty()` does not allocate a frozenset
- [ ] `_STAGED_COLOR_LIST` is module-level; no per-id list allocation
- [ ] (Post-#021) `session.set_selection(frozenset_snap)` — no list() wrapper
- [ ] (Optional) `CellTable` uses single `QItemSelection.select(...)` call

## Work Log

- 2026-04-23 — Surfaced by performance-oracle.

## Resources

- Scale projection from performance-oracle: at 500 staged, refresh ~1-2 ms, commit ~20-50 ms.
