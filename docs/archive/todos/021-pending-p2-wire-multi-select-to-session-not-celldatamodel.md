---
status: pending
priority: p2
issue_id: "021"
tags: [code-review, architecture, multi-select, migration]
dependencies: []
---

# Route multi-select commit through `Session.set_selection(frozenset)`, not the legacy `CellDataModel` bridge

## Problem Statement

`MultiLabelSelectController.accept()` commits the staged selection via the legacy Qt-facing bridge rather than the canonical application seam. Two concrete consequences:

1. **Type contract mismatch with the PR description.** PR #1's body states "commits via `Session.set_selection(frozenset)`", but the code at `src/percell4/gui/multi_select.py:203` calls `self._data_model.set_selection(list(snap))` — passing a `list`, on `CellDataModel`, not a `frozenset` on `Session`.
2. **Regression vs. peer views.** `peer_views/data_plot.py:314-339` and `peer_views/cell_table.py:332` already talk to `Session` directly with `frozenset`. The multi-select controller is the only new selection mutator that re-introduces the `CellDataModel.set_selection(list[int])` round-trip. `model.py:9` declares the bridge is scheduled for deletion once all consumers migrate — this PR lands a fresh consumer.

Wasteful `frozenset → list → frozenset` round-trip: `multi_select.py:203` → `model.py:138-139` → `session.py:144`.

## Findings

- **kieran-python-reviewer (P2-1):** PR body overstates the type contract at the boundary; `SelectionSink` correctly matches `CellDataModel`, not `Session`.
- **architecture-strategist (P2):** "Will break when `model.py` is deleted per its own migration note."
- **pattern-recognition-specialist (P3 #13):** Module docstring says frozenset, code passes list.

## Proposed Solutions

### Option A — Swap the target to `Session` (Recommended)

1. Change `MultiLabelSelectController.__init__` to take a `session: Session` parameter instead of (or in addition to) `data_model: CellDataModel`.
2. Change the `SelectionSink` Protocol at `multi_select.py:83-89` to `def set_selection(self, ids: frozenset[int]) -> None: ...` with a `selection: frozenset[int]` read property.
3. At `multi_select.py:203`, call `self._session.set_selection(self._buffer.snapshot())` — no `list()` wrapper.
4. Update the launcher wiring in `main_window.py:637-651` to pass the session.
5. Fix `multi_select.py:8-9` module docstring.

- **Pros:** Matches peer_views migration, survives `CellDataModel` deletion, removes redundant conversion, honors the PR body's stated contract.
- **Cons:** Touches the Protocol and call site; adjust ~6 tests that mock `selected_ids`.
- **Effort:** Small (30 min).
- **Risk:** Low — tests cover both ends.

### Option B — Keep `CellDataModel` target, fix only the docstring

- **Pros:** Minimal diff.
- **Cons:** Accumulates tech debt; future deletion of `model.py` cascades to this file.
- **Effort:** Trivial.
- **Risk:** Bait for future maintainer confusion.

### Option C — Tighten `CellDataModel.set_selection` to accept `Iterable[int]`

- Halves the allocation cost but doesn't address the "wrong seam" concern.
- **Pros:** Cheapest perf win.
- **Cons:** Doesn't fix the migration story.
- **Effort:** Small.

## Recommended Action

Option A. This is the migration direction already in motion; adding one more `CellDataModel` consumer now creates more to undo later.

## Technical Details

**Affected files:**
- `src/percell4/gui/multi_select.py` (lines 83-89 Protocol, 150, 203, docstring lines 8-9)
- `src/percell4/gui/viewer.py:537` (`launch_multi_select_tool` signature may need `session`)
- `src/percell4/interfaces/gui/main_window.py:637-651` (pass session into launcher wiring)
- `tests/test_gui_workflows/test_multi_select.py:102-107` (mock shape change)

## Acceptance Criteria

- [ ] `multi_select.py:203` calls `session.set_selection(frozenset_snap)` — no `list()` wrapper
- [ ] `SelectionSink` Protocol takes/returns `frozenset[int]`
- [ ] Module docstring matches the actual call
- [ ] All tests in `test_multi_select.py` pass unchanged in behavior
- [ ] End-to-end: stage cells, accept, observe DataPlot + CellTable update correctly

## Work Log

- 2026-04-23 — Surfaced by kieran-python-reviewer + architecture-strategist + pattern-recognition-specialist during PR #1 review.

## Resources

- `src/percell4/application/session.py:144` — canonical selection API
- `src/percell4/interfaces/gui/peer_views/data_plot.py:314-339` — peer that already migrated
- `src/percell4/interfaces/gui/peer_views/cell_table.py:332` — peer that already migrated
- `src/percell4/model.py:9` — migration-pending docstring
