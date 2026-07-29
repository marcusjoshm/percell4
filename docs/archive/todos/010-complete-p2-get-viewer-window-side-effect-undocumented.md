---
status: pending
priority: p2
issue_id: "010"
tags: [code-review, launcher, workflows, host]
dependencies: []
---

# `WorkflowHost.get_viewer_window` lazily creates the viewer — undocumented side effect

## Problem Statement

`WorkflowHost.get_viewer_window` (`src/percell4/workflows/host.py:34-35`) says:

> Return the host's shared napari viewer window.

The launcher's implementation at `launcher.py` is:

```python
def get_viewer_window(self):
    """Return the shared ``ViewerWindow``, creating it if needed."""
    return self._get_or_create_window("viewer")
```

This **lazily constructs** the viewer if it does not yet exist (`launcher.py:693-714`). Two consequences:

1. **A `FakeHost` in unit tests will not replicate the lazy-create semantics.** Bugs that depend on "was the viewer already open when the workflow started?" won't surface until integration.
2. **`close_child_windows` explicitly excludes the viewer** (plan: "The viewer window is NOT closed — the workflow uses it to host QC sessions."), and `restore_child_windows` never touches the viewer either. So if a user starts a workflow on a project with no viewer ever opened, the workflow will create one, use it, and **leave it open after the run** — they'll finish their run and find an extra napari window they never asked for.

The protocol's contract does not mention the side effect, so runners have no way to know they should clean up after themselves.

## Findings

- **architecture-strategist** (S2): side effect contradicts docstring; leaves dangling viewer window after run.

## Proposed Solutions

### Option A — Track viewer-was-created-by-workflow and close it on restore (Recommended)

Add state to `close_child_windows` that records whether the viewer existed before the workflow started. On `restore_child_windows`, if the viewer was created by the workflow (i.e., did not exist before), close it.

```python
def close_child_windows(self) -> None:
    child_keys = ("cell_table", "data_plot", "phasor_plot")
    self._child_windows_to_restore = []
    for key in child_keys:
        win = self._windows.get(key)
        if win is None:
            continue
        if win.isVisible():
            self._child_windows_to_restore.append(key)
            win.close()  # see todo #006
            del self._windows[key]
    # Track whether viewer existed before the run; if not and the runner
    # calls get_viewer_window, we'll close the viewer on restore.
    self._viewer_created_by_workflow = "viewer" not in self._windows

def restore_child_windows(self) -> None:
    for key in self._child_windows_to_restore:
        self._show_window(key)
    self._child_windows_to_restore = []
    if getattr(self, "_viewer_created_by_workflow", False):
        viewer = self._windows.get("viewer")
        if viewer is not None:
            viewer.close()
            # Leave in self._windows so future _show_window re-creates cleanly
```

And update `host.py`:

```python
def get_viewer_window(self) -> "ViewerWindow":
    """Return the host's shared napari viewer window.

    Lazily created on first access. If the workflow creates the viewer
    (i.e., none existed before ``close_child_windows`` was called), the
    host will close it automatically in ``restore_child_windows``.
    """
```

- **Pros**: Matches the plan's no-surprise goal. Test-observable via `host._viewer_created_by_workflow`. Protocol contract is explicit.
- **Cons**: Adds one state bit and a conditional close path. Minor.
- **Effort**: Small.
- **Risk**: Low.

### Option B — Rename to `get_or_create_viewer_window` and document the side effect

```python
def get_or_create_viewer_window(self) -> "ViewerWindow":
    """Return the host's viewer window, creating and wiring it if needed.
    The runner is responsible for cleanup if it was the first to call this.
    """
```

- **Pros**: Explicit in the name. No cleanup logic in the host.
- **Cons**: Shifts the cleanup burden onto every runner. Easy to forget.

### Option C — Split into `get_viewer_window` (returns None if missing) + `ensure_viewer_window` (creates)

- **Pros**: Most explicit.
- **Cons**: Two methods where one will do.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/gui/launcher.py` — `close_child_windows` / `restore_child_windows`
- `src/percell4/workflows/host.py` — add contract note to docstring

**Pairs well with todo #006** (the `close_child_windows` hide vs close fix) — do them in the same commit.

## Acceptance Criteria

- [ ] If the viewer did NOT exist before `close_child_windows`, it is closed by `restore_child_windows`
- [ ] If the viewer DID exist before, it is left alone
- [ ] Host docstring documents the create/cleanup contract
- [ ] Regression test with a `FakeHost` verifies both branches

## Work Log

- 2026-04-10 — Identified by architecture-strategist.

## Resources

- `src/percell4/gui/launcher.py` — current implementation
- `src/percell4/workflows/host.py` — Protocol definition
- Review source: architecture-strategist Should #2
