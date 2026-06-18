---
status: pending
priority: p2
issue_id: "025"
tags: [code-review, ux, multi-select, menu-taxonomy]
dependencies: []
---

# Top-level `&Selection` menu for a single action breaks launcher taxonomy

## Problem Statement

`main_window.py:126-135` adds a whole top-level `&Selection` menu to the launcher menubar containing only `&Multi-select…`.

Before this PR the menubar held a single `&File` menu. The rest of the app is reached through the sidebar's eight panels: I/O, Viewer, Segment, Analysis, FLIM, Scripts, Workflows, Data (`main_window.py:182-191`).

A second top-level menu for a single action is disproportionate to the feature and inconsistent with every other feature's reach path. Users scanning for multi-select are trained to look in the sidebar; users scanning the menubar see two inconsistent taxonomies.

## Findings

- **pattern-recognition-specialist (P1 #9):** "Disproportionate and inconsistent with how every other feature is reached."
- **architecture-strategist (P3):** Hotkey `M` concern relates to this — if the viewer has focus, the launcher's menu-mounted shortcut may not fire.

## Proposed Solutions

### Option A — Put the action on the sidebar's Viewer panel (Recommended)

The tool operates inside the napari viewer; the Viewer panel (`main_window.py:242-244`) already has "Open Viewer". Add a "Multi-select…" button there. Keep keyboard shortcut via `QShortcut` installed on the `ViewerWindow` directly (where focus usually lives).

- **Pros:** Matches the "all features live in the sidebar" convention; shortcut fires in viewer context; remove the whole `&Selection` menu.
- **Cons:** Button is only reachable when launcher is visible (but same is true for the menu).
- **Effort:** Small (20 min).
- **Risk:** None.

### Option B — Nest under `&File` or create an `&Edit` menu

"Edit → Select Labels…" is conventional for selection operations in desktop apps.

- **Pros:** Keeps the menubar route; fits convention (Edit menus commonly hold selection actions).
- **Cons:** Still two top-level menus; doesn't solve the focus/shortcut problem.
- **Effort:** Small.
- **Risk:** None.

### Option C — Install shortcut on both launcher and viewer; keep the menu

- **Pros:** Keeps current UI.
- **Cons:** Doesn't address the taxonomy complaint; duplicated shortcut registration.

## Recommended Action

Option A. Sidebar is the house convention; users learn one place.

## Technical Details

**Affected files:**
- `src/percell4/interfaces/gui/main_window.py:126-135` (delete `&Selection` menu creation)
- `src/percell4/interfaces/gui/main_window.py:242-244` (add button to Viewer panel)
- Consider adding shortcut on `ViewerWindow` itself so `M` / `Ctrl+M` works when the viewer has focus (see todo #030).

## Acceptance Criteria

- [ ] No new top-level menu added by this PR
- [ ] Multi-select reachable from the sidebar's Viewer panel
- [ ] Keyboard shortcut (see #030) still opens the tool when the viewer has focus

## Work Log

- 2026-04-23 — Flagged by pattern-recognition-specialist.

## Resources

- Sidebar structure: `src/percell4/interfaces/gui/main_window.py:182-191`
- Viewer panel: `src/percell4/interfaces/gui/main_window.py:242-244`
