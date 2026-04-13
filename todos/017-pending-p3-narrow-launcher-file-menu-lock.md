---
status: pending
priority: p3
issue_id: "017"
tags: [code-review, launcher, workflows]
dependencies: []
---

# `set_workflow_locked` disables the entire File menu; plan said only Open/Close items

## Problem Statement

The plan at line 135 says:

> enumerate disabled widgets: `_import_panel`, `_segmentation_panel`, `_analysis_panel`, Workflows tab Start button, File menu Open/Close dataset

But the launcher implementation calls `self.menuBar().setEnabled(not locked)`, which disables **every** menu bar action — including Help, About, Quit, and any future menu entries that would reasonably stay active during a run (e.g. a Logs menu).

Two consequences:
1. Users cannot quit the app from the menu during a long-running workflow. The window X still works (and via todo #006 will also trap as Cancel with a confirmation).
2. When Phase 2 adds more menu entries (e.g. a View > Logs menu), they are silently locked out with no warning.

## Findings

- **architecture-strategist** (S5): the lock is wider than the plan promised. Either narrow the implementation or update the plan.

## Proposed Solutions

### Option A — Narrow the lock to specific QActions (Recommended if file menu grows)

Track the specific actions that should be locked:

```python
def _create_menu_bar(self) -> None:
    menu = self.menuBar()
    file_menu = menu.addMenu("&File")

    self._open_project_action = QAction("&Open Project...", self)
    self._open_project_action.triggered.connect(self._on_open_project)
    file_menu.addAction(self._open_project_action)

    file_menu.addSeparator()

    quit_action = QAction("&Quit", self)
    quit_action.triggered.connect(QApplication.quit)
    file_menu.addAction(quit_action)

def set_workflow_locked(self, locked: bool) -> None:
    ...
    # Lock specific actions, not the whole menu bar
    self._open_project_action.setEnabled(not locked)
    # Quit and Help remain available
```

- **Pros**: Matches the plan. Quit stays accessible during a long run.
- **Cons**: Requires tracking specific QActions on `self`. Fine for a few actions.
- **Effort**: Small.

### Option B — Update the plan to acknowledge the wider lock

Edit `docs/plans/2026-04-10-feat-single-cell-thresholding-workflow-plan.md:135` to say "disable the entire File menu via `menuBar().setEnabled(False)`" and document the Quit-via-X-only behavior.

- **Pros**: Zero code change.
- **Cons**: Users lose Quit menu access for the sake of implementation convenience. Phase 2 menu additions are silently locked.

### Option C — Leave as is, no documentation

- **Cons**: Plan/implementation drift; the drift will be noticed at code review in Phase 2.

## Recommended Action

Option A if you plan to grow the menu bar. Option B is acceptable if the menu bar is and will remain just Open Project + Quit — in that case, add a comment in `set_workflow_locked` explaining why the wide lock is intentional ("keeps quit accessible only via window X, which is trapped as Cancel").

## Technical Details

**Affected files:**
- `src/percell4/gui/launcher.py` — `_create_menu_bar`, `set_workflow_locked`

## Acceptance Criteria

- [ ] Either the plan is updated to match the implementation, OR the implementation disables only `_open_project_action` + any future Close Dataset action
- [ ] Quit menu action remains enabled during a locked workflow (if Option A)
- [ ] No silent drift: the plan and the code agree

## Work Log

- 2026-04-10 — Flagged by architecture-strategist.

## Resources

- Plan line 135
- `src/percell4/gui/launcher.py:97-111` (_create_menu_bar)
- Review source: architecture-strategist Should #5
