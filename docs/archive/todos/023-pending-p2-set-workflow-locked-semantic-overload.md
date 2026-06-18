---
status: pending
priority: p2
issue_id: "023"
tags: [code-review, architecture, multi-select, workflow-lock]
dependencies: []
---

# `set_workflow_locked` is overloaded: batch-workflow lock AND viewer-tool exclusion

## Problem Statement

The multi-select controller reuses `LauncherWindow.set_workflow_locked` / `is_workflow_locked` as the tool-exclusion primitive (`multi_select.py:170, 238, 274`). That primitive has a different, specific contract, documented at `workflows/host.py:33-34` and `main_window.py:1172-1193`: it is the **batch-workflow UI lock** — disables the entire launcher central widget + menu bar, overwrites the status bar with "Workflow running..." / "Ready". Part of the `WorkflowHost` Protocol used by `BaseWorkflowRunner`.

Observable side effects when multi-select reuses it:
- Opening multi-select disables the launcher's entire sidebar / analysis panel / file menu — as if a batch workflow were running.
- Accepting/cancelling the tool resets the status bar to "Ready", **clobbering any previous status message** (e.g., "Loaded: foo.h5").
- Cross-contract interference: if a `BaseWorkflowRunner.set_workflow_locked(True)` fires while the tool's lock is held, `main_window.py:1182` short-circuits on equal values; the tool's later `set_workflow_locked(False)` then releases the workflow's lock out from under it.

The PR plan defended *avoiding* a new `_active_tool` flag as a win. In this case that's actually a **misfeature** — a distinct flag is what would prevent the semantic conflation.

## Findings

- **architecture-strategist (P2):** "Semantic overload. Side effects are workflow-specific and bleed into tool UX. Introduce a separate `is_viewer_tool_active`/`set_viewer_tool_active` or a reason-scoped lock."

## Proposed Solutions

### Option A — Add a sibling `is_viewer_tool_active` / `set_viewer_tool_active` on the launcher (Recommended)

1. Add two new methods on `LauncherWindow`:
   ```python
   def is_viewer_tool_active(self) -> bool: ...
   def set_viewer_tool_active(self, active: bool) -> None: ...
   ```
2. Implementation can still disable the sidebar (or not — UX call), but **must not** touch the status bar.
3. Add a single combined `_is_ui_locked()` helper that returns `self._workflow_locked or self._viewer_tool_active` for any caller that needs the OR.
4. Change the `ToolLock` Protocol in `multi_select.py:92-98` to call the new methods.

- **Pros:** Clean semantic separation; no cross-contract interference; status bar preserved.
- **Cons:** One extra flag + two methods on launcher.
- **Effort:** Small (30-60 min).
- **Risk:** Low.

### Option B — Reason-scoped lock (`set_ui_locked(reason: str)`)

Introduce a reason stack on the launcher. `set_workflow_locked` and `set_viewer_tool_active` both route through it.

- **Pros:** Extensible to future exclusive modes.
- **Cons:** Heavier refactor; more API surface.
- **Effort:** Medium.

### Option C — Accept the overload; document the cross-contract hazard

- **Pros:** Zero diff.
- **Cons:** Future regression magnet.

## Recommended Action

Option A. The cost is two launcher methods; the benefit is a clean contract and no status-bar clobber.

## Technical Details

**Affected files:**
- `src/percell4/interfaces/gui/main_window.py:1167-1193` (add sibling methods)
- `src/percell4/gui/multi_select.py:92-98` (rename the `ToolLock` Protocol surface, 170, 238, 274)

## Acceptance Criteria

- [ ] Opening multi-select does not overwrite the status bar
- [ ] Cancel/accept does not reset status bar to "Ready"
- [ ] Multi-select and a mock batch workflow can claim their locks independently (test)
- [ ] `set_workflow_locked` contract is back to workflow-only

## Work Log

- 2026-04-23 — Surfaced by architecture-strategist during PR #1 review.

## Resources

- `src/percell4/interfaces/gui/main_window.py:1167-1193`
- `src/percell4/workflows/host.py:24-60` — the workflow-lock contract
- `src/percell4/gui/workflows/base_runner.py:250, 494`
