---
status: pending
priority: p2
issue_id: "030"
tags: [code-review, ux, shortcuts, multi-select]
dependencies: []
---

# Unmodified `M` shortcut may silently not fire when the napari viewer has focus

## Problem Statement

`main_window.py:129` — `self._multi_select_action.setShortcut("M")` — plain `M` with no modifier, bound to a `QAction` on the launcher's menu bar.

Two distinct risks:

1. **Focus context.** When the user is looking at labels, the napari viewer has focus. Shortcuts mounted on the launcher's `QAction` may not fire — Qt's shortcut context defaults to `WindowShortcut` (active window only). If the launcher is behind the viewer, the shortcut never reaches the action.
2. **Napari built-in collision.** Plain `M` is a common single-character key — napari's default shortcuts may bind it to a layer or tool mode (e.g., some napari versions map single letters to layer ops). Needs verification.

Inside the tool dock itself, modifier keys are used consistently (`Ctrl+Return` accept, `Esc` cancel). The outer `M` is the odd one out.

## Findings

- **pattern-recognition-specialist (P2 #17):** "Unmodified single-letter shortcut on a launcher menu action. Prefer `Ctrl+M` / `Cmd+M` to match the `Ctrl+Return` / `Esc` convention used inside the tool."
- **architecture-strategist (P3):** "`M` installed only on the launcher menu. Likely dead when napari has focus; also may clash with napari built-ins."

## Proposed Solutions

### Option A — Change to `Ctrl+M` and install on both launcher and viewer (Recommended)

1. `main_window.py:129` → `setShortcut("Ctrl+M")`.
2. Add a `QShortcut(QKeySequence("Ctrl+M"), viewer_window)` on `ViewerWindow` that invokes the same handler.
3. Verify manually that `Ctrl+M` works with launcher focus *and* with viewer focus.

- **Pros:** Consistent modifier convention; explicit cross-window registration; survives focus changes; unlikely to collide with napari built-ins.
- **Cons:** Dual registration surface to maintain.
- **Effort:** Small (20 min).
- **Risk:** None.

### Option B — Keep `M` but install on both windows

- **Pros:** Fewer keystrokes.
- **Cons:** Still collision-risk with napari single-letter shortcuts.

### Option C — Remove the shortcut entirely; menu/sidebar-button only (paired with todo #025)

- **Pros:** Zero surface area.
- **Cons:** Power users lose the keyboard path.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/interfaces/gui/main_window.py:129` (change sequence)
- `src/percell4/gui/viewer.py` (add `QShortcut` registration)

## Acceptance Criteria

- [ ] Shortcut is `Ctrl+M` (or `Cmd+M` on macOS per Qt's platform mapping)
- [ ] Tool opens when launcher has focus and `Ctrl+M` is pressed
- [ ] Tool opens when viewer has focus and `Ctrl+M` is pressed
- [ ] No napari built-in shortcut is shadowed (manual check via napari's keybindings panel)

## Work Log

- 2026-04-23 — Flagged by pattern-recognition-specialist + architecture-strategist.

## Resources

- Qt docs on `Qt.ShortcutContext`
- In-tool shortcuts for convention: `multi_select.py:391-396`
