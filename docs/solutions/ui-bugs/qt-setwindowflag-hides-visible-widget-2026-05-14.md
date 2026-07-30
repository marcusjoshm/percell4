---
title: Qt setWindowFlag hides a visible widget — capture visibility BEFORE the flag change
date: 2026-05-14
category: ui-bugs
module: gui
problem_type: ui_bug
component: frontend_stimulus
severity: medium
canonical_source: src/percell4/interfaces/gui/peer_views/session_window.py
applies_to:
  - "src/percell4/interfaces/gui/peer_views/*.py"
  - "src/percell4/gui/*.py"
symptoms:
  - "Window vanishes when the user toggles a window-flag checkbox (e.g., Always on top)"
  - "Reopening the app shows the window again, but the next toggle hides it again"
  - "isVisible() returns False inside the toggle handler even though the widget was visible just before"
  - "The widget's windowFlags() correctly reflects the new flag, but the widget is no longer on screen"
root_cause: wrong_api
resolution_type: code_fix
related_components:
  - qt
  - peer_views
tags:
  - qt
  - pyqt
  - qtpy
  - setwindowflag
  - windowstaysontophint
  - visibility
  - regression-test
---

# Qt setWindowFlag hides a visible widget — capture visibility BEFORE the flag change

## Problem

In a Qt application, toggling a window flag (e.g.,
`Qt.WindowStaysOnTopHint`) on a visible `QMainWindow` via
`self.setWindowFlag(flag, on_or_off)` silently hides the widget as a side
effect. If the toggle handler checks `self.isVisible()` *after* the
`setWindowFlag` call to decide whether to re-show, the check returns
`False` (the widget was just hidden by Qt), so `show()` is never called
and the window disappears for the user. The flag itself is set
correctly; the visible state is what's broken.

## Symptoms

In PerCell4's `SessionWindow`
(`src/percell4/interfaces/gui/peer_views/session_window.py`), the bug
manifested as:

- User unchecks "Always on top" → window vanishes.
- User must close and reopen the app to recover.
- On relaunch the checkbox state is correctly restored from `QSettings`
  (e.g., persists as unchecked).
- User re-checks "Always on top" → window vanishes again.

Programmatically, `windowFlags() & Qt.WindowStaysOnTopHint` reflects the
new flag value correctly, but `isVisible()` is `False`.

## What Didn't Work

**Checking `isVisible()` after the flag change.** The original handler
was:

```python
def _apply_pin_on_top(self, pinned: bool) -> None:
    self.setWindowFlag(Qt.WindowStaysOnTopHint, pinned)
    # Flipping a window flag after show() requires show() again on most
    # platforms. If the window has never been shown yet, this is a no-op.
    if self.isVisible():
        self.show()
```

The intent was correct — re-show only if the widget was visible — but
the `isVisible()` check ran after `setWindowFlag` had already hidden the
widget, so it always evaluated `False` for an actively-visible window
and `show()` was never called.

A unit test that asserted the flag was set passed
(`assert bool(win.windowFlags() & Qt.WindowStaysOnTopHint)`); the flag
*was* set. The test didn't catch the bug because it didn't assert
visibility.

## Solution

Capture visibility **before** the `setWindowFlag` call:

```python
def _apply_pin_on_top(self, pinned: bool) -> None:
    # ``setWindowFlag`` hides the widget as a side effect when called
    # on a visible window, so we capture visibility BEFORE the flag
    # change and re-show after. Reading ``isVisible()`` after the call
    # always returns False and would silently drop the window for the
    # user every time they toggle the pin.
    was_visible = self.isVisible()
    self.setWindowFlag(Qt.WindowStaysOnTopHint, pinned)
    if was_visible:
        self.show()
        if pinned:
            # macOS doesn't always re-stack the window on flag change.
            # Force it forward so the toggle has a visible effect.
            self.raise_()
```

The `raise_()` call is a secondary fix: on macOS, even after `show()`,
the window doesn't always rise above siblings when the
`WindowStaysOnTopHint` flag is newly added. Forcing the raise makes the
toggle have a visible effect.

## Why This Works

Qt's `setWindowFlag` (and the underlying `setWindowFlags`) reparent the
widget when window flags change. The native window has to be torn down
and recreated with the new flags, which on most platforms (and notably
macOS) requires the widget to be hidden first. Calling `show()`
re-creates the native window with the new flags and makes it visible.

The bug is a state-capture timing issue: the original code asked
`isVisible()` of a widget that Qt had *just* hidden as part of the call
we were responding to. The fix is to read the predicate before the
mutation, not after.

## Prevention

**Regression test.** Add a test that exercises the toggle round-trip on
an actively-visible window. The existing unit test that only checked
the flag was set was insufficient. The catching test:

```python
def test_pin_on_top_toggle_keeps_window_visible(qtbot, isolated_settings):
    """Toggling pin-on-top must NOT hide the window.

    Regression: QWidget.setWindowFlag hides the widget as a side effect.
    The toggle handler must re-show after flipping the flag so the user
    doesn't lose the window every time they toggle the pin.
    """
    model = CellDataModel()
    win = SessionWindow(data_model=model)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    assert win.isVisible()

    # Toggle off
    win._pin_check.setChecked(False)
    assert win.isVisible(), "Window vanished after toggling Pin-on-top OFF"

    # Toggle back on
    win._pin_check.setChecked(True)
    assert win.isVisible(), "Window vanished after toggling Pin-on-top ON"
```

This test failed against the original code and passes against the fix.

**Code-review heuristic.** Any call to `setWindowFlag` or
`setWindowFlags` on a `QWidget` that was previously shown is a smell.
Look for the pattern:

```python
some_predicate = self.isVisible()  # or similar visible-state read
self.setWindowFlag(...)            # mutation
if some_predicate:                  # read captured BEFORE, not after
    self.show()
```

**UX label hygiene.** The original checkbox label was "Pin on top",
which the user reasonably interpreted as "snap to the top of the
screen" (position) rather than "stay above other windows" (Z-order).
Renamed to "Always on top" with an explicit tooltip:

```python
self._pin_check = QCheckBox("Always on top")
self._pin_check.setToolTip(
    "Keep this window above other PerCell4 windows even when they "
    "have focus. Does not move the window — drag it where you want."
)
```

## Related

- [`docs/solutions/architecture-patterns/consolidate-canonical-state-over-per-module-overrides-2026-05-14.md`](../architecture-patterns/consolidate-canonical-state-over-per-module-overrides-2026-05-14.md)
  — the larger architectural learning from PR #12 that introduced this
  window in the first place.
- `src/percell4/interfaces/gui/peer_views/session_window.py` — the
  canonical implementation site for the fix.
- `tests/test_gui_workflows/test_session_window.py` — contains
  `test_pin_on_top_toggle_keeps_window_visible` as the regression
  guard.
- Qt documentation note: `QWidget.setWindowFlag` "will not be applied
  unless show() is called after the change has been made." This is
  documented but easy to read past — the side-effect-hides-the-widget
  behavior is not on the same docs page.
- [`gnome-attaches-parented-modal-dialogs-2026-07-29.md`](gnome-attaches-parented-modal-dialogs-2026-07-29.md)
  — the popup-window-independence convention, whose two helpers must run
  before the first `show()` for exactly the reason documented here.
