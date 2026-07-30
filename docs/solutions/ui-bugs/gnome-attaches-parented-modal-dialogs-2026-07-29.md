---
title: GNOME glues parented modal dialogs to their parent — give popups a non-attaching window type
date: 2026-07-29
category: ui-bugs
module: gui
problem_type: ui_bug
component: frontend_stimulus
severity: high
canonical_source: src/percell4/gui/_dialog_utils.py
applies_to:
  - "src/percell4/gui/*.py"
  - "src/percell4/gui/workflows/**/*.py"
  - "src/percell4/interfaces/gui/**/*.py"
symptoms:
  - "A dialog opens partly off-screen when the launcher is docked to a screen edge"
  - "Dragging the dialog moves the launcher instead, or does nothing"
  - "The dialog cannot be separated from the window that opened it"
  - "widget.move() on a dialog is silently ignored — pos() reports the requested value but the window does not move"
  - "Dialog controls are unreachable because they sit beyond the screen edge"
root_cause: environment_interaction
resolution_type: code_fix
related_components:
  - qt
  - gnome
  - mutter
tags:
  - qt
  - pyqt
  - qtpy
  - gnome
  - mutter
  - wayland
  - xwayland
  - window-manager
  - modal
  - attach-modal-dialogs
  - regression-test
---

# GNOME glues parented modal dialogs to their parent

## Problem

The researcher docks the launcher against the left or right edge of the
screen so the launcher and the napari viewer are usable side by side. Every
PerCell4 popup was a parented, application-modal `QDialog`. Opening one —
Compress TIFF Dataset, for example — put it partly off the screen with its
controls unreachable, and it could not be dragged back: dragging moved the
launcher instead.

## Root cause

Three conditions must **all** hold for mutter to attach a window to its
parent. Break any one and the popup is free.

1. The `attach-modal-dialogs` preference is on.
2. The window is a DIALOG type carrying `_NET_WM_STATE_MODAL`.
3. It has a `WM_TRANSIENT_FOR` parent whose own type is NORMAL, DIALOG, or
   MODAL\_DIALOG.

Every PerCell4 popup satisfied all three. Once attached, mutter rewrites the
window's position on every constraint pass, which is why the application's
own `move()` was discarded.

Check the preference with:

```bash
gsettings get org.gnome.mutter attach-modal-dialogs
```

Note that mutter's own schema default is `false`, but GNOME Shell ships an
override setting it `true`, so reasoning from the schema default gives the
wrong answer. Whether every stock GNOME session has it on was not
established — the bug is confirmed on the reporter's machine.

## Solution

`src/percell4/gui/_dialog_utils.py` gained two helpers, both **no-ops off
Linux** and both required to run **before the first `show()`**:

- `detach_window(popup)` replaces the window-type nibble with `Qt.Tool`,
  which maps to `_NET_WM_WINDOW_TYPE_UTILITY` and breaks condition 2.
- `center_on_screen(popup)` places the popup at the centre of its screen's
  work area, clamped so an oversized popup keeps its title bar reachable.

Popups built by a Qt static (`QMessageBox.warning`, `QInputDialog.getText`)
have no handle on which to set flags before showing, so they go through
`message_box()`, `progress_dialog()`, and `text_input()` in the same module.

### Why `Qt.Tool` and not `Qt.Window`

Both detach. `Qt.Window` also **deletes** `WM_TRANSIENT_FOR`, losing the
stay-above-parent relationship and gaining a taskbar entry, and no call to
`QWindow.setTransientParent()` restores it — the gate in
`qxcbwindow.cpp`'s `isTransient()` is the window *type*, and `Qt::Window`
is not in its list. `Qt.Tool` is, so the transient parent survives.

Measured on GNOME/XWayland, launcher docked to the right edge, a 780×737
dialog on a 1920×1080 work area:

| Window type set | `_NET_WM_WINDOW_TYPE` | `_NET_WM_STATE` | `WM_TRANSIENT_FOR` | `move()` honoured |
|---|---|---|---|---|
| default (`Qt.Dialog`) | `DIALOG, NORMAL` | `MODAL, SKIP_TASKBAR` | parent | no |
| `Qt.Window` | `NORMAL` | `MODAL` | **absent** | yes |
| `Qt.Tool` | `UTILITY, NORMAL` | `MODAL, SKIP_PAGER, SKIP_TASKBAR` | parent | yes |

`Qt.Popup` and `Qt.ToolTip` are also in `isTransient()` but unusable:
`QXcbWindow::setWindowFlags` force-adds `X11BypassWindowManagerHint`, taking
the window out of mutter's management entirely.

### Condition 3 does most of the work

Because mutter checks the **parent's** type, converting a dialog frees every
popup parented to it, with no edit to those call sites. Verified with an
entirely unmodified modal child:

| Child | Parent's type | `move()` honoured |
|---|---|---|
| stock modal `QDialog` | NORMAL | no |
| stock modal `QDialog` | UTILITY | **yes** |

That is why roughly forty nested `QMessageBox` calls inside the converted
dialog classes are compliant precisely by being left alone, and why the
compliance test deliberately exempts them.

## What didn't work

**`setWindowFlag(Qt.Window, True)`.** A measured no-op on a `QDialog`:
`Qt.Dialog` is `0x3` and already contains the `Qt.Window` bit `0x1`, so the
flags come back byte-identical. The type nibble has to be *replaced*
(`(flags & ~Qt.WindowType_Mask) | Qt.Tool`). A test pins this explicitly so
a naive implementation cannot pass.

**Repositioning without detaching.** `move()` to screen centre on an
attached dialog is silently discarded — `constrain_modal_dialog` in mutter's
`constraints.c` rewrites x/y to the parent's centre and, unlike every other
constraint, has no priority guard, so it cannot be outvoted.

**Turning the preference off as the fix.** It stops the clipping but leaves
dialogs centred on the *launcher*: mutter's `place.c` centres any
dialog-type window over its transient parent at initial placement,
independent of the attach preference. Still a useful one-line workaround for
an affected user, but not a substitute.

**`setTransientParent()` after switching to `Qt.Window`.** Tried after
`show()`, after `create()`, and after `winId()`. The property is never
written, because the gate is the window type.

**Relaxing modality instead.** mutter latches its notion of modality when it
first manages a window and Qt never clears `_NET_WM_STATE_MODAL`, so runtime
modality flips leave the two out of sync. Modality was left untouched.

## Known limitation: vertical centring is approximate

Horizontal centring is exact. Vertical is biased low by half the title-bar
height (~18px under GNOME; measured frame margins were 37px top, 0px sides).
Before a window is mapped its `frameGeometry` equals its `geometry` — the
frame is the window manager's decision and is unmeasurable until show — and
centring must happen before show to set `Qt.WA_Moved`, which is what
suppresses `QDialog.adjustPosition`. Correcting it would require re-centring
after show, at the cost of a visible jump, for under 2% of a 1080px screen.

## Platform scope

Gated on `sys.platform`, deliberately **not** on `platformName()` or a
Wayland environment variable — the shape `opengl_platform.py` uses for a
different problem would be wrong here.

- **Not mutter-specific.** metacity (GNOME Flashback) and muffin (Cinnamon)
  carry the same `should_attach_to_parent` logic. KWin does not attach.
- **Not X11-specific.** mutter ≥47 implements `xdg-dialog-v1`, and QtWayland
  sends it as of Qt 6.8. Native Wayland is safe here only because Qt 5.15's
  QtWayland cannot mark a surface modal — a Qt-version artifact, not a
  platform guarantee. A `qtpy` codebase is one `QT_API` change from PyQt6.
- **macOS has a related defect.** `QCocoaWindow::setVisible` turns any
  parented `Qt::WindowModal` window into a native `NSWindow` sheet, glued to
  the parent's title area and unmovable. This repo has nine parented
  `WindowModal` progress dialogs, i.e. exactly that case. Out of scope here
  because the reporter sees no problem on macOS.
- **Centring is X11/XWayland-only by construction.** xdg-shell gives clients
  no absolute-positioning request, so Qt ignores `move()` for top-levels on
  native Wayland regardless of modality.

## Prevention

`tests/test_gui/test_popup_window_compliance.py` asserts the invariant by
inspection across both GUI trees. **No automated tier in this repo can catch
a regression at runtime** — the offscreen platform has no window manager and
the GL tier runs without one either — so this inspection test is the only
mechanised guard, and the check below is manual.

### Manual repro

1. Confirm the preference: `gsettings get org.gnome.mutter attach-modal-dialogs`
   (set it `true` explicitly if reproducing on a machine where it is off).
2. Dock the launcher against the left or right screen edge.
3. Open a workflow dialog. It should appear centred on the screen, fully
   visible, and drag freely.
4. Inspect it: `xprop -id <winid> _NET_WM_WINDOW_TYPE _NET_WM_STATE WM_TRANSIENT_FOR`.
   Expect `UTILITY`, `MODAL` still present, and `WM_TRANSIENT_FOR` **equal to
   the launcher's window id**. Compare the value, not merely the property's
   presence: a transient-type window with no transient parent still gets
   `WM_TRANSIENT_FOR` pointing at the client leader.

## Open: file pickers are affected too

`QFileDialog.get*` was assumed to resolve to the desktop portal and
therefore to be out of scope. **Measured on this machine, it does not.** The
static returns a Qt widget `QFileDialog` that meets all three attach
conditions and lands off-screen:

```
QFileDialog.getOpenFileName(launcher, …) with the launcher docked right
  _NET_WM_WINDOW_TYPE = DIALOG, NORMAL
  _NET_WM_STATE       = MODAL, SKIP_TASKBAR
  WM_TRANSIENT_FOR    = <launcher>
  frame               = (1416, 274, 629, 452)   right edge 2045 of 1920
  fully on screen     = False
```

`testOption(DontUseNativeDialog)` is `False`, so this is Qt falling back to
its own widget dialog for want of a platform-theme plugin, not an explicit
choice. The repo sets neither `DontUseNativeDialog`, `AA_DontUseNativeDialogs`,
nor `QT_QPA_PLATFORMTHEME`.

Roughly 15 `QFileDialog.get*` call sites remain unconverted across the
launcher, the peer views, and the task panels. Fixing them needs a fourth
`_dialog_utils` wrapper on the same pattern; a wrapper that constructs a
`QFileDialog` without setting `DontUseNativeDialog` still uses the platform's
native chooser where one exists, so the fix is inert rather than a
regression on machines that do have a portal.

## Related

- [`qt-setwindowflag-hides-visible-widget-2026-05-14.md`](qt-setwindowflag-hides-visible-widget-2026-05-14.md)
  — why both helpers must run before the first `show()`.
- [`dialog-scroll-when-tall.md`](dialog-scroll-when-tall.md) — the sibling
  convention in the same module, and the argument for adding a function to
  `_dialog_utils.py` rather than introducing a dialog base class.
- `docs/plans/2026-07-29-001-fix-popup-window-independence-plan.md` — the
  plan, whose appendix carries the full probe matrix.
- mutter `src/core/window.c` (`meta_window_should_attach_to_parent`),
  `src/core/constraints.c` (`constrain_modal_dialog`), `src/core/place.c`.
- qtbase `src/plugins/platforms/xcb/qxcbwindow.cpp` (`isTransient`,
  `updateWmTransientFor`).
