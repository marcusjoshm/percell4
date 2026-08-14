---
status: pending
priority: p2
issue_id: "024"
tags: [code-review, architecture, multi-select, window-coupling]
dependencies: ["023"]
---

# `ViewerWindow.launch_multi_select_tool(launcher)` takes the launcher directly — violates "windows never talk directly"

## Problem Statement

`viewer.py:537` signature:

```python
def launch_multi_select_tool(self, launcher) -> bool:
```

The launcher is then threaded straight into `MultiLabelSelectController(...)` as its `ToolLock` collaborator. Two structural issues:

1. **Top-level `CLAUDE.md` rule violation.** Project architecture: "windows never talk to each other directly — they communicate through the shared `CellDataModel`." The viewer now holds and forwards a concrete reference to the launcher. The viewer has knowledge that a launcher-typed collaborator exists.
2. **Parameter is untyped.** No annotation at `viewer.py:537`; nothing constrains the argument to a narrow protocol.

The *ownership* decision (viewer owns the controller's lifetime because the tool is meaningless without the viewer) is correct. The *pass-through* of a raw launcher is what's wrong.

## Findings

- **architecture-strategist (P2):** "Viewer now knows the launcher type — violates 'windows never talk directly' rule from top-level `CLAUDE.md`. Fix: type that argument as the `ToolLock` Protocol; the launcher already conforms structurally."

## Proposed Solutions

### Option A — Type the parameter as `ToolLock` Protocol (Recommended)

```python
# viewer.py (under TYPE_CHECKING)
from percell4.gui.multi_select import ToolLock

def launch_multi_select_tool(self, tool_lock: "ToolLock") -> bool:
    ...
```

Call site in `main_window.py:637-651` already passes `self` — `LauncherWindow` already structurally conforms (it defines `is_workflow_locked` / `set_workflow_locked` at `main_window.py:1167-1193`).

After todo #023 lands, the Protocol becomes `is_viewer_tool_active` / `set_viewer_tool_active` — the structural conformance story is the same.

This mirrors the existing `WorkflowHost` Protocol pattern (`workflows/host.py:24-60`).

- **Pros:** Viewer only depends on the Protocol; launcher and viewer remain structurally independent.
- **Cons:** Requires TYPE_CHECKING import dance.
- **Effort:** Small (10 min).
- **Risk:** None.

### Option B — Move the controller construction to the launcher

Launcher owns the controller, hands the viewer a ref to drive rendering.

- **Pros:** Eliminates the cross-window reference entirely.
- **Cons:** Breaks viewer-lifetime coupling; if the viewer dies, the controller outlives it.
- **Effort:** Medium.
- **Risk:** Medium — harder teardown story.

## Recommended Action

Option A. Cheapest fix, matches existing `WorkflowHost` pattern.

## Technical Details

**Affected files:**
- `src/percell4/gui/viewer.py:537-566` (signature + forward-ref import)
- `src/percell4/interfaces/gui/main_window.py:637-651` (no call-site change needed)

## Acceptance Criteria

- [ ] `viewer.py:537` signature types its parameter as `ToolLock`
- [ ] No `from percell4.interfaces.gui.main_window import LauncherWindow` anywhere in `viewer.py`
- [ ] Existing tests pass

## Work Log

- 2026-04-23 — Flagged by architecture-strategist.

## Resources

- `~/percell4/CLAUDE.md` — "windows never talk to each other directly" rule
- `src/percell4/workflows/host.py:24-60` — `WorkflowHost` Protocol precedent
