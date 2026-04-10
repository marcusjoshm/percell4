---
status: pending
priority: p2
issue_id: "006"
tags: [code-review, bug, launcher, workflows]
dependencies: []
---

# `close_child_windows` calls `.hide()`, does not actually stop signal thrash

## Problem Statement

`LauncherWindow.close_child_windows()` at `launcher.py` is named "close" but actually calls `win.hide()` on each child window (`cell_table`, `data_plot`, `phasor_plot`). The windows remain alive in `self._windows` and **remain connected to `CellDataModel.state_changed`** — they process every signal emitted during the workflow run, exactly the "thrash" the plan said closing them was meant to prevent.

From the plan rationale (plan.md:116):

> Close `CellTableWindow`, `DataPlotWindow`, `PhasorPlotWindow` during a workflow. [...] Locking only the launcher panels is insufficient because those child windows listen to `CellDataModel` signals and will thrash on every dataset swap.

But the implementation just hides the Qt widgets, which:
1. Does not disconnect their signal handlers
2. Does not release the DataFrame references they hold
3. Means every `data_model.set_measurements(df)` call during the workflow still fires N child handlers that rebuild their models

## Findings

- **kieran-python-reviewer** (S13): name is misleading — either rename to `hide_child_windows` or actually close.
- **architecture-strategist** (S6): the stated rationale ("prevent signal thrash") is not achieved by `hide()`. Child windows still tick on every state change.

## Proposed Solutions

### Option A — Actually `close()` and re-instantiate on restore (Recommended)

```python
def close_child_windows(self) -> None:
    child_keys = ("cell_table", "data_plot", "phasor_plot")
    self._child_windows_to_restore = set()
    for key in child_keys:
        win = self._windows.get(key)
        if win is None:
            continue
        if win.isVisible():
            self._child_windows_to_restore.add(key)
        if win is not None:
            win.close()
            # Remove from registry so _show_window reinstantiates fresh
            del self._windows[key]

def restore_child_windows(self) -> None:
    for key in sorted(self._child_windows_to_restore):
        self._show_window(key)  # lazy-create via existing factories
    self._child_windows_to_restore = set()
```

- **Pros**: Matches the plan's stated rationale. Completely detaches from `CellDataModel` signals for the duration of the run. No thrash.
- **Cons**: The reopen cost is a fresh window construction (includes napari viewer wiring for phasor, pyqtgraph for data_plot, table model rebuild for cell_table). Noticeable but acceptable — runs are long-lived, reopening happens at most twice.
- **Effort**: Small.
- **Risk**: Low — verify the factories in `_get_or_create_window` are idempotent.

### Option B — Keep `hide()` but explicitly disconnect signal handlers

```python
def close_child_windows(self) -> None:
    for key in ("cell_table", "data_plot", "phasor_plot"):
        win = self._windows.get(key)
        if win is None:
            continue
        if win.isVisible():
            self._child_windows_to_restore.add(key)
        win.hide()
        # Disconnect from data_model signals
        try:
            self.data_model.state_changed.disconnect(win._on_state_changed)
        except (TypeError, RuntimeError):
            pass
```

- **Pros**: Avoids reconstruction cost.
- **Cons**: Requires knowing each child window's handler name. Brittle — a new child window type will be silently broken. Re-entrance on restore needs matching `.connect()`.
- **Effort**: Medium.
- **Risk**: Medium — easy to leak connections or double-connect.

### Option C — Rename to `hide_child_windows` and accept the thrash

- **Pros**: Matches current behavior.
- **Cons**: Doesn't fix the actual problem the plan called out. Still wastes CPU on every signal during the run.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/gui/launcher.py` — `close_child_windows`, `restore_child_windows`
- Verify `_get_or_create_window` at `launcher.py:693-714` handles re-creation correctly after a `del self._windows[key]` (it should, since it checks `if key not in self._windows`).

**Note:** The `restore_child_windows` sorted iteration is arbitrary; the original plan promised to reopen in the order they were closed. The `set` loses ordering. Consider switching `_child_windows_to_restore` from `set[str]` to `list[str]` to preserve order. Minor — affects which window gets keyboard focus first.

## Acceptance Criteria

- [ ] During a workflow run, `cell_table`/`data_plot`/`phasor_plot` do not receive `state_changed` signals
- [ ] On `restore_child_windows`, previously-open child windows reappear (visibility restored)
- [ ] No signal handler leaks (verified by checking `data_model.state_changed.receivers()` count before and after a run)
- [ ] Launcher still conforms to `WorkflowHost` protocol after change

## Work Log

- 2026-04-10 — Identified by kieran-python-reviewer and architecture-strategist.

## Resources

- Plan line 116 (rationale)
- `src/percell4/gui/launcher.py` — `close_child_windows` implementation
- `src/percell4/model.py:45` — `CellDataModel.state_changed`
