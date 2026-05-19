---
status: pending
priority: p3
issue_id: "031"
tags: [code-review, simplicity, multi-select, judgment-call]
dependencies: []
---

# Split verdict: in-module Protocols + `StagingBuffer` — keep or inline?

## Problem Statement

Two reviewers disagreed on whether `multi_select.py`'s abstractions earn their keep. Both viewpoints are defensible — this is a design judgment that deserves to be made deliberately rather than drifted into.

## Findings

**code-simplicity-reviewer (P1 — delete both):**
- Three in-module Protocols (`StagedRenderer`, `SelectionSink`, `ToolLock` at `multi_select.py:65-98`) each have exactly one real implementor. Tests use `MagicMock`, not typed fakes — `Protocol` only pays off with multiple real implementors or typed fakes, neither of which holds. Saves ~40 lines.
- `StagingBuffer` dataclass (`multi_select.py:101-128`) wraps three trivial operations with one caller. Inline: `self._initial_ids: frozenset[int]`, `self._staged: set[int]`, toggle = `self._staged ^= {x}`, dirty = `self._staged != self._initial_ids`, snapshot = `frozenset(self._staged)`. Saves ~28 source + ~60 test lines.

**kieran-python-reviewer (ship as-is):**
- "`StagingBuffer` is genuinely Qt-free and clean. The pattern is textbook protocol-based DI."
- "10 tests exercise `StagingBuffer` directly. This is the pattern I want more of in this codebase."
- Protocols enable the test suite to run without napari or QApplication overhead.

**pattern-recognition-specialist (P3 #15):** The Protocols exist in the consumer module. If kept, they'd read better in `gui/protocols.py` or next to the thing they abstract (`viewer.py` for `StagedRenderer`, `application/session.py` for `SelectionSink`).

## Proposed Solutions

### Option A — Keep both, relocate Protocols (Recommended)

1. Keep `StagingBuffer` as-is — the Qt-free testability win is real even with one caller.
2. Move `StagedRenderer` to `gui/viewer.py` (next to its implementor).
3. Move `SelectionSink` to `application/session.py` or `model.py` (next to its implementor).
4. Keep `ToolLock` in `multi_select.py` until a second tool needs it.

- **Pros:** Preserves the clean seam; Protocols live where their contract is defined; tests still run fast.
- **Cons:** Three-file diff.
- **Effort:** Small (30 min).
- **Risk:** None.

### Option B — Delete all three Protocols; keep `StagingBuffer`

Type controller parameters as concrete forward-ref strings (`"ViewerWindow"`, `"CellDataModel"`, `"LauncherWindow"`). Tests keep using `MagicMock`.

- **Pros:** ~40 lines saved; fewer concepts to track.
- **Cons:** Loses explicit contract documentation; adding a second consumer reintroduces the Protocol.
- **Effort:** Small.
- **Risk:** Low.

### Option C — Delete both Protocols and `StagingBuffer`

Follows simplicity reviewer's full recommendation.

- **Pros:** ~130 fewer lines total.
- **Cons:** Loses pure-Python state isolation that makes 10 headless tests trivial.
- **Effort:** Small.
- **Risk:** Medium — readability regression on `accept()` and the refresh path.

## Recommended Action

Option A. The test-speed argument is load-bearing; the Protocol-location argument is a real nit. Relocation is cheap.

## Technical Details

If Option A:
- `src/percell4/gui/viewer.py` — add `StagedRenderer` Protocol near `add_staged_overlay`.
- `src/percell4/application/session.py` or `src/percell4/model.py` — add `SelectionSink` Protocol.
- `src/percell4/gui/multi_select.py:65-98` — delete two Protocols, keep `ToolLock`, update imports.

If Option B or C: see simplicity reviewer's breakdown for the inlining pattern.

## Acceptance Criteria

- [ ] Decision recorded with rationale (Option A, B, or C)
- [ ] If A: Protocols live alongside their implementors; `multi_select.py` imports them
- [ ] All 29 multi_select tests pass
- [ ] Diff size matches the chosen option

## Work Log

- 2026-04-23 — Split verdict surfaced by code-simplicity-reviewer (P1) vs kieran-python-reviewer (ship as-is) vs pattern-recognition-specialist (P3).

## Resources

- `src/percell4/gui/multi_select.py:65-128`
- `tests/test_gui_workflows/test_multi_select.py:21-78` (StagingBuffer tests)
