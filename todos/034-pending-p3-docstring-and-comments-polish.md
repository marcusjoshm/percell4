---
status: pending
priority: p3
issue_id: "034"
tags: [code-review, docs, multi-select]
dependencies: ["021"]
---

# Module docstring and inline comments: trim and align with actual behavior

## Problem Statement

`multi_select.py:1-32` — 32-line module docstring. Most of it is architectural narration; half is self-evident from reading the class. Two concrete problems:

1. **Factual inaccuracy.** Lines 8-9 claim "commits via `Session.set_selection(frozenset)`" but the code at line 203 calls `self._data_model.set_selection(list(snap))`. Will be resolved by todo #021 if Option A is chosen; otherwise the docstring needs a direct correction.

2. **Narrating-WHAT comments scattered through the file:**
   - Line 114: `# Initialize current from initial_ids without sharing state.` — followed by the obvious `self.current = set(self.initial_ids)`.
   - Line 202: `# Domain state moves here — the one and only place.` — dramatic but non-actionable.
   - Line 214: `"""Enter tool mode. Order matters — see plan critical race notes."""` — the reference to an external plan doc is forever-fragile.

3. **Comments worth keeping (the race/ordering ones):**
   - Lines 220-225 (suspend-forwarding rationale)
   - Lines 286-287 (QTimer single-shot restart semantics)
   - Line 246 (cancel-pending-refresh before touching renderer state)

## Findings

- **code-simplicity-reviewer (P3):** Narrating-WHAT comments; over-verbose module docstring.
- **kieran-python-reviewer (P3-8):** `_layer` field deserves a one-line reason in class docstring.
- **pattern-recognition-specialist (P3 #13):** Docstring frozenset-vs-list inaccuracy.
- **architecture-strategist (P3):** Same docstring mismatch.
- **agent-native-reviewer (P2):** Module docstring should include a 2-3 line "Scripting note" pointing headless callers at `Session.set_selection(frozenset)` directly.

## Proposed Solutions

### Option A — Trim to ~8-10 lines; preserve race comments; add scripting note (Recommended)

Replace the 32-line module docstring with something like:

```python
"""Modal multi-label selection tool for the napari viewer.

Open with Selection → Multi-select… (or Ctrl+M), click labels in the
viewer to stage them (cyan overlay), Ctrl+Return to commit or Esc to cancel.

Staging state lives in `StagingBuffer` (pure Python, no Qt) so headless
tests and scripts can build selections without a QApplication.

Scripting note: if you don't need the modal tool, call
`Session.set_selection(frozenset(ids))` directly — that's the canonical
selection API; this module is the GUI shell around building the frozenset.

See docs/plans/2026-04-17-feat-napari-multi-label-selection-plan.md for
race notes and design rationale.
"""
```

Also:
- Delete narrating-WHAT comments at 114, 202.
- Keep 220-225, 246, 286-287 (real race/idempotency explanations).
- Add a one-line class docstring note on why `_layer` is retained (fallback for uninstall if `active_labels_layer_or_none()` returns None mid-teardown).

- **Pros:** Easier to read; factually accurate; honors the agent-native "scripts should skip this file" insight.
- **Cons:** None.
- **Effort:** Small (15 min).
- **Risk:** None.

### Option B — Fix only the frozenset-vs-list inaccuracy

- **Pros:** Minimal diff.
- **Cons:** Leaves the other polish items.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/gui/multi_select.py:1-32` (module docstring)
- `src/percell4/gui/multi_select.py:114, 202` (delete decorative comments)
- `src/percell4/gui/multi_select.py:154` (annotate `_layer`; see also todo #037)
- `src/percell4/gui/multi_select.py` class docstring (one-line `_layer` rationale)

## Acceptance Criteria

- [ ] Module docstring is ≤ 12 lines and accurate
- [ ] Scripting note points at `Session.set_selection` directly
- [ ] Race/ordering comments preserved verbatim
- [ ] Narrating-WHAT comments removed

## Work Log

- 2026-04-23 — Consolidated from multiple reviewers.

## Resources

- `src/percell4/application/session.py:144` — canonical selection API
