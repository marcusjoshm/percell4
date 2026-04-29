---
title: Napari modal tool — overlay-layer pattern, mouse-callback wiring, and teardown races
date: 2026-04-29
category: architecture-patterns
module: percell4.gui.multi_select
problem_type: architecture_pattern
component: frontend_stimulus
severity: medium
applies_when:
  - "Adding a modal mouse tool over an embedded napari viewer in a Qt app"
  - "Staging selections before committing to the shared CellDataModel"
  - "Visualizing transient state without mutating a primary Labels layer's data or colormap"
  - "Coordinating QTimer-coalesced refreshes with explicit teardown"
  - "Reusing the existing workflow-lock primitive instead of inventing a new active-tool flag"
related_components: [tooling, testing_framework]
tags: [napari, qt, pyqt5, labels-layer, mouse-callbacks, overlay-pattern, race-conditions, multi-select]
---

# Napari modal tool — overlay-layer pattern, mouse-callback wiring, and teardown races

## Context

PerCell4's `ViewerWindow` previously supported only single-click selection: napari's `layer.events.selected_label` flowed into `_on_label_selected`, which called `CellDataModel.set_selection([single_id])` — every click replaced the prior selection. Curated visual subsets (export, "filter from selection", "delete selected labels" in seg-QC) had no path that started *from the image*. The `feat/napari-multi-label-selection` branch added the first modal Qt tool that intercepts napari layer mouse events, accumulates state in a pure-Python buffer, renders staging on top of the labels layer, and commits through the canonical state path. Because it is the first `mouse_drag_callbacks.append(...)` in the repo and the first tool that needs to *suppress* napari's own selection forwarding, the lifecycle and overlay choices established here are load-bearing for every future modal tool.

## Guidance

**1. Use a dedicated overlay Labels layer for transient visualization — never mutate the primary layer's data or colormap.**
The existing `_update_label_display` already has 3-branch (filter × selection) combinatorics; adding a staged tier would push it to 2×2×2 and force `events.colormap.blocker()` dances. Instead, `add_staged_overlay` adds a second Labels layer named `_multi_select_staged` whose `data` is a *view* (not copy) of the primary array, with a `DirectLabelColormap` mapping only staged IDs → cyan and everything else → transparent. `update_staged_overlay` rebuilds *just the overlay's color_dict* — O(|staged|), no GPU thrash on the primary texture.

**2. Disconnect — don't just override — built-in napari signal forwarding for the tool's lifetime.**
Setting `layer.mode = "pan_zoom"` makes napari's built-in pick a no-op, but a click *in flight* during the mode switch can still flow `selected_label` → `_on_label_selected` → `set_selection([single_id])` and wipe the prior selection that was just pre-filled into staging. The fix is `ViewerWindow.suspend_selected_label_forwarding()` flipping a `_selected_label_forwarding_suspended` flag that `_on_label_selected` checks at entry. Resume on teardown. The existing `_is_originator` guard handles the round-trip loop (viewer originates a selection change, then the same change comes back through the model); it does not cover the in-flight-click case — these are separate concerns.

**3. Append to `mouse_drag_callbacks`, not `mouse_callbacks`, and gate with `event.button != 1`.**
The drag callbacks list still fires on plain clicks (a non-generator function = single fire on `mouse_press`), but the documented idiom for click-vs-drag detection lives there. Filter middle-click, right-click, Alt+drag, and Space+drag explicitly — without the gate, users panning the canvas accidentally toggle labels.

**4. Coalesce UI refreshes via a single-shot `QTimer.start(0)` + a `_torn_down` flag.**
Restarting a single-shot timer is idempotent; Qt naturally collapses N clicks into one fire per event-loop iteration. Critically, the timer can fire *after* teardown — `_do_refresh` must early-return on `_torn_down`, and `_uninstall` must call `_refresh_timer.stop()` synchronously **before** any other teardown step. Otherwise a stale fire stomps the just-committed state.

**5. Pure-Python state in a `@dataclass` with a thin Qt shell around it.**
`StagingBuffer` holds `initial_ids: frozenset`, `current: set[int]` (mutable internal), and exposes `toggle` / `snapshot() -> frozenset` / `is_dirty()`. Use `set` internally for O(1) toggles; emit `frozenset` only at the boundary. `is_dirty()` becomes the trivially testable predicate that drives Accept-button enabled state. The whole buffer has zero Qt imports and tests in plain pytest.

**6. Declare narrow `Protocol`s in-module for renderer + sink (+ tool-lock).**
`StagedRenderer`, `SelectionSink`, and `ToolLock` document the controller's surface and let tests pass minimal fakes instead of `MagicMock(spec=ViewerWindow)`. The test file is 29 tests and never imports napari.

**7. Reuse `LauncherWindow.set_workflow_locked` / `is_workflow_locked` — do not invent a parallel `_active_tool` flag.**
Threshold-QC, seg-QC, and now multi-select all share one coordination primitive. Action enable/disable rides on the launcher's existing menu-bar disable in `set_workflow_locked`. Adding a parallel mechanism is exactly the kind of context-poisoning the project guards against.

**8. Parent the tool window to whichever window owns its interaction surface.**
Multi-select parents to `ViewerWindow` (it owns the labels layer, the mouse callbacks, the overlay), not to `LauncherWindow`. This mirrors `ThresholdQCController` and `SegmentationQCController`. Parenting to the launcher creates a hidden cycle.

**9. `contextlib.suppress(ValueError)` on `mouse_drag_callbacks.remove`; `suppress(Exception)` on every other teardown step.**
TOCTOU membership pre-checks are theater. During teardown, any single step may have already happened (viewer torn down, layer gone, lock already released, window already closed) — wrap each step independently and accept partial failure rather than aborting the whole teardown.

**10. Window-scoped shortcuts (`Qt.WindowShortcut`, the QAction/QShortcut default) for tool-window keys.**
Do not promote to `Qt.ApplicationShortcut` without a real cross-window invocation use case. The `M` shortcut on the launcher's `QAction` is correct as window-scoped — invoking from the napari viewer is not a use case anyone asked for. Within the tool dock, `Ctrl+Return` / `Esc` are also window-scoped and that is correct because the dock is focus-isolated.

**11. Commit through `CellDataModel.set_selection` only.**
Never fan out to per-consumer wiring. The signal ripple (`StateChange(selection=True)`) reaches DataPlot, CellTable, filter-from-selection, "Delete selected labels", and export-selected with no extra code — this is the architectural contract from `model.py`.

## Why This Matters

PerCell4 explicitly fights context poisoning: every tool reuses the same coordination primitives, so the codebase doesn't grow N parallel locking schemes that future tools subtly disagree about. The two race conditions above (`selected_label` wipe during mode-switch; coalesced timer fires after teardown) are *silent* failures — they only surface with fast clicking or window close mid-tool, exactly the cases manual smoke-testing skips. Napari's API stability has been verified across 0.5 → 0.7 for the low-level surface (`mouse_drag_callbacks`, `get_value` kwargs, `pan_zoom` mode) but not for higher-level abstractions; staying close to the documented primitives is what survives version drift. And the overlay-layer choice — instead of extending the primary colormap — keeps the existing 3-branch builder inviolate, so threshold-QC and seg-QC don't accidentally break the next time someone touches selection rendering.

## When to Apply

- Adding a new modal Qt tool that intercepts napari Labels-layer (or Image-layer) input.
- Accumulating selection or annotation state from clicks before a single commit.
- Visualizing transient state on top of an existing layer without mutating it.
- Any tool that needs to *temporarily suppress* a built-in viewer signal handler.
- Any flow where "user gesture → buffered state → canonical-state commit" is the right shape (vs. each gesture writing through immediately).

## Examples

### 1. Strict install / uninstall order

From `src/percell4/gui/multi_select.py:213-279`:

```python
def _install(self) -> None:
    layer = self._viewer_win.active_labels_layer_or_none()
    assert layer is not None  # Guarded in show()
    self._layer = layer

    # 1. Silence selected_label → CellDataModel BEFORE mode change.
    self._viewer_win.suspend_selected_label_forwarding()
    # 2. Save and force pan_zoom (built-in pick → no_op).
    self._prior_mode = str(layer.mode)
    layer.mode = _PAN_ZOOM
    # 3. Add the staging overlay.
    self._viewer_win.add_staged_overlay(self._buffer.snapshot())
    # 4. Append the click callback.
    self._mouse_cb = self._make_click_callback()
    layer.mouse_drag_callbacks.append(self._mouse_cb)
    # 5. Acquire the workflow lock LAST.
    self._launcher.set_workflow_locked(True)

def _uninstall(self) -> None:
    if self._torn_down:
        return
    self._torn_down = True                # gate first
    if self._refresh_timer is not None:
        self._refresh_timer.stop()        # cancel pending fire synchronously
    # ... remove callback, restore mode, remove overlay,
    # resume forwarding, release lock — each in suppress(Exception).
```

Install order: suppress incoming signals → change mode → add overlay → append callback → take lock. Teardown reverses it. Any other order leaves a window where a click can do the wrong thing.

### 2. Coalesced refresh + torn-down guard

From the same file (`multi_select.py:283-298`):

```python
def toggle(self, label_id: LabelId) -> None:
    if self._torn_down:
        return
    self._buffer.toggle(label_id)
    self._schedule_refresh()

def _schedule_refresh(self) -> None:
    if self._torn_down or self._refresh_timer is None:
        return
    # Restarting a single-shot timer is idempotent — one fire per loop iter.
    self._refresh_timer.start(0)

def _do_refresh(self) -> None:
    if self._torn_down:                   # guard against stale fire
        return
    if not self._viewer_win.is_viewer_alive():
        return
    snap = self._buffer.snapshot()
    with contextlib.suppress(Exception):
        self._viewer_win.update_staged_overlay(snap)
    self._refresh_dock()
```

The `_torn_down` early-return is the load-bearing line. Without it, a click that schedules at T+0ms followed by `accept()` at T+1ms produces a `_do_refresh` fire after `_uninstall` already removed the overlay — which then reappears in cyan over the freshly-committed selection. With the guard, the timer fires, sees the flag, and returns. The pattern generalizes: any debounced or coalesced callback in a window that can be torn down must check a teardown flag at entry, and teardown must stop the timer synchronously before yielding control.

### 3. Click callback — left-button gate + safe `get_value`

From `multi_select.py:309-335`:

```python
def _make_click_callback(self) -> Callable:
    def _on_click(layer_, event) -> None:
        if self._torn_down:
            return
        if event.button != 1:           # middle/right/Alt+drag → pan untouched
            return
        try:
            value = layer_.get_value(
                event.position,
                view_direction=event.view_direction,
                dims_displayed=event.dims_displayed,
                world=True,
            )
        except Exception:               # noqa: BLE001
            return
        if value is None:               # outside layer bounds
            return
        try:
            label_id = int(value)
        except (TypeError, ValueError):
            return
        if label_id == 0:               # background pixel
            return
        self.toggle(label_id)
    return _on_click
```

`get_value` kwargs are **keyword-only** (the napari signature uses a `*` separator); call them by name. `None` means click outside the layer; `0` means the background pixel — both are no-ops, never errors.

## Related

- [`docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`](../ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md) — why we never reach for the primary layer's colormap; the overlay layer sidesteps the `events.colormap.blocker()` flicker entirely.
- [`docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`](../ui-bugs/percell4-selection-filtering-multi-roi-patterns.md) — Pattern 1 ("don't mutate `layer.data`, use `DirectLabelColormap`"). The overlay-layer approach inherits this rule and adds the modal-tool axis on top.
- [`docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`](../architecture-decisions/session-bridge-event-forwarding.md) — why commit must go through `CellDataModel.set_selection`; this doc reuses that rule rather than re-deriving it.
- [`docs/solutions/ui-bugs/percell4-phases-0-6-napari-qt-learnings.md`](../ui-bugs/percell4-phases-0-6-napari-qt-learnings.md) — earlier napari + Qt lifecycle learnings; the `_torn_down` + `is_viewer_alive()` guards extend the same discipline.
- Plan: [`docs/plans/2026-04-17-feat-napari-multi-label-selection-plan.md`](../../plans/2026-04-17-feat-napari-multi-label-selection-plan.md)

### Files this pattern lives in

- `src/percell4/gui/multi_select.py` — `StagingBuffer`, Protocols, `MultiLabelSelectController`.
- `src/percell4/gui/viewer.py:444-540` — `is_viewer_alive`, `active_labels_layer_or_none`, `suspend/resume_selected_label_forwarding`, `add/update/remove_staged_overlay`, plus the `_selected_label_forwarding_suspended` gate at line 254.
- `src/percell4/interfaces/gui/main_window.py:128-135,638-650` — Selection menu, `M` `QAction`, lock-gated enable, launch wiring.
- `tests/test_gui_workflows/test_multi_select.py` — 29 tests on mock Protocols (no real napari import).
