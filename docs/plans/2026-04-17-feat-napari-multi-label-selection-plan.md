---
title: "feat: Napari multi-label selection mode"
type: feat
date: 2026-04-17
---

# Napari Multi-Label Selection Mode

## Enhancement Summary

**Deepened on:** 2026-04-17

**Major structural changes from the initial draft:**

1. **Switched to a dedicated overlay Labels layer for staging visualization.**
   Two agents independently flagged this: the architect (cleaner, lets the
   tool own its visual artifact, no `events.colormap.blocker()` dance) and
   the prior-art research (napari's `DirectLabelColormap.use_selection`
   can't cleanly highlight an arbitrary *set* of labels; community plugins
   like `napari-manual-split-and-merge-labels` work around this with
   secondary layers). No more modification to `_update_label_display` at
   all — the existing 3-branch builder stays untouched.

2. **Parent the tool to `ViewerWindow`, not `LauncherWindow`.** Every
   reference (labels layer, `mouse_drag_callbacks`, mode restore, overlay
   layer, `set_staged_ids`) lives on `ViewerWindow`. Parenting to the
   launcher creates a hidden cycle; parenting to the viewer mirrors how
   the existing `ThresholdQCController` (`gui/threshold_qc.py`) and
   `SegmentationQCController` (`gui/workflows/single_cell/seg_qc.py`)
   are organized.

3. **Reuse the existing `set_workflow_locked` / `is_workflow_locked`
   mechanism on `LauncherWindow` (`main_window.py:1141-1157`)** instead of
   inventing a parallel `_active_tool` flag. The coordination primitive
   already exists — the initial plan just hadn't noticed it.

4. **Flattened to a single file: `src/percell4/gui/multi_select.py`** —
   matches the sibling `gui/threshold_qc.py` layout. No subpackage.
   `tests/test_gui_workflows/test_multi_select_*.py` is the established
   test home; there is no `tests/test_gui/` tier in this repo.

5. **Two phases, not five.** State logic, Qt dock, and mouse wiring land
   together; overlay layer is its own commit. No gratuitous Phase-1
   "pure-Python extraction" when the logic is a three-method dataclass.

6. **Button label "Accept" (not "Commit")** — matches the house convention
   used in `seg_qc.py:297` ("Accept & Next →") and
   `threshold_qc.py:509` ("Accept"). "Commit" would be deliberate
   differentiation with no reason.

7. **Dock is a `QMainWindow` with `setCentralWidget(QWidget)`, NOT a
   `QDockWidget`.** Both existing controllers use the plain
   `setCentralWidget` shape; no in-repo precedent uses `addDockWidget`.

**Critical race-condition fixes identified:**

8. **Mode-switch race eliminated.** Between `layer.mode = "pan_zoom"` and
   the napari `selected_label` signal flowing to `_on_label_selected` at
   `viewer.py:247-264`, a click in flight can wipe the prior selection
   *that we just pre-filled staging from*. Mitigation: temporarily
   disconnect `_on_label_selected` from `layer.events.selected_label` for
   the tool's lifetime (reconnect on exit). The existing `_is_originator`
   guard does not cover this case.

9. **Commit-during-refresh race eliminated.** A coalesced `QTimer` can
   fire *after* `_uninstall_and_close()`, stomping the colormap with
   stale cyan over freshly-painted committed yellow. Mitigation:
   `_torn_down` flag checked at the top of `_do_refresh`; the teardown
   path synchronously stops the timer before yielding control.

10. **Pan-button filter added.** Middle-click, Alt+drag, and Space+drag
    all deliver `mouse_press` events in pan_zoom mode. Callback must
    guard with `if event.button != 1: return` — otherwise users trying
    to pan accidentally toggle labels.

**Python design refinements applied:**

11. `@dataclass class StagingBuffer` holds the pure-Python state
    (`initial_ids`, `current: set[int]`, `toggle`, `snapshot() -> frozenset[int]`,
    `is_dirty`). Controller becomes a thin Qt shell around it. Makes the
    Commit-enabled predicate (`is_dirty()`) explicit.
12. `set[int]` internal (mutable), emit `frozenset` at the boundary. The
    initial plan advertised immutability while rebuilding on every click.
13. Narrow `Protocol`s in-module: `StagedRenderer` (one method:
    `set_staged_ids(frozenset[int])`) and `SelectionSink` (one method:
    `set_selection(list[int])`). Documents the surface; enables trivial
    mocks.
14. **Single `QTimer(setSingleShot=True)`** constructed in `__init__` and
    `.start(0)` on each click — replaces the manual `_refresh_pending` bool.
    Qt coalesces for free.
15. `contextlib.suppress(ValueError)` around
    `layer.mouse_drag_callbacks.remove(self._mouse_cb)` — the membership
    pre-check in the initial plan was redundant TOCTOU theater.
16. `LabelId: TypeAlias = int` in a shared types module. `Final` on the
    cyan RGBA tuple and `_PAN_ZOOM = "pan_zoom"`.

**Napari API facts confirmed (from source in `.venv/lib/.../napari/`):**

- Callback signature is `def cb(layer, event)` (not `(viewer, event)`).
  Plain non-generator functions fire once on `mouse_press` — the
  `event.type != "mouse_press"` guard is defensive but not required.
- `layer.get_value(position, *, view_direction, dims_displayed, world=True)`
  returns `None` outside bounds, `0` for background pixels. Kwargs are
  keyword-only (the `*` separator matters).
- `layer.mode = "pan_zoom"` makes napari's built-in `pick` handler a
  `no_op` via `_drag_modes`; appended callbacks still run.
- In napari ≥ 0.4.18, `layer.mouse_pan = False` / `layer.mouse_zoom = False`
  is the documented way to suppress pan/zoom while your callback owns
  the cursor (replaces the deprecated `interactive` attribute). Not
  required for us since `pan_zoom` doesn't actually interfere with a
  single-click toggle, but worth knowing.
- All mouse callbacks run on the Qt main thread — no new threading hazards.

## Overview

Add a modal interactive tool to the napari viewer that lets the user
accumulate multiple segmentation-label picks into a **staged selection**,
then commit the staged set as the ground-truth `CellDataModel.selection`
via `Session.set_selection(frozenset)`. Matches the Cellpose-GUI pattern:
click a button, click labels one at a time (each click accumulates), press
`Ctrl+Return` (or click Accept) to commit.

All UX decisions settled — see
[`docs/brainstorms/2026-04-17-napari-multi-label-selection-brainstorm.md`](../brainstorms/2026-04-17-napari-multi-label-selection-brainstorm.md).

## Problem Statement

Today the napari viewer only supports single-click-replaces-selection
(`viewer.py:247-264` wiring `layer.events.selected_label`). Multi-cell
selection today requires DataPlot's Ctrl+click toggle, CellTable's
row multi-select, or ThresholdQC's group buttons — none of which let the
user pick cells *visually from the image*. For curated subsets defined by
image-level judgment (suspicious segmentations, a manually-chosen
subpopulation for export, cells to feed into "Filter from selection" or
the existing "Delete selected labels" action in `SegmentationQCController`),
there is no workflow.

## Proposed Solution

A new `MultiLabelSelectController` that:

1. Mounts a small `QMainWindow` with `setCentralWidget(QWidget)` — same
   shape as the existing QC controllers (`seg_qc.py:171-215`,
   `threshold_qc.py:193-202`). No `QDockWidget`.
2. Owns a pure-Python `StagingBuffer` (initial IDs, current `set[int]`,
   `toggle`, `snapshot() -> frozenset[int]`, `is_dirty`). Never touches
   `Session` until commit.
3. **Disconnects `ViewerWindow._on_label_selected` from
   `layer.events.selected_label`** for its lifetime, then forces
   `layer.mode = "pan_zoom"` so napari's built-in pick becomes a no-op.
4. Intercepts clicks by appending a callback to
   `layer.mouse_drag_callbacks`; gated on `event.button == 1` (left click
   only) so middle-click / Alt-drag panning is untouched.
5. Renders staged cells via a **dedicated overlay Labels layer**
   (cyan `DirectLabelColormap`, read-only) added to the viewer on
   `_install`, removed on `_uninstall`. The existing
   `_update_label_display` function is **not modified**.
6. Commits with `Ctrl+Return` / Accept → calls
   `self._data_model.set_selection(list(staged))` then tears down. Cancels
   with `Esc` / Cancel → tears down with no domain-state change.
7. Acquires `LauncherWindow.set_workflow_locked(True)` for the tool's
   lifetime, reusing the existing coordination mechanism
   (`main_window.py:1141-1157`); released on exit. Entry-point button is
   disabled when `is_workflow_locked()` already returns True.

## Technical Approach

### Module layout — one file

```
src/percell4/gui/multi_select.py        # StagingBuffer + Controller + Protocols
tests/test_gui_workflows/test_multi_select.py
```

Sibling of `src/percell4/gui/threshold_qc.py` (same shape, same
neighborhood). No subpackage.

### State model

```python
# multi_select.py — shape only
from dataclasses import dataclass, field
from typing import Final, Protocol, TypeAlias

LabelId: TypeAlias = int
_PAN_ZOOM: Final = "pan_zoom"
_STAGED_COLOR: Final = (0.0, 0.9, 0.9, 0.6)    # cyan 0.6α
_OVERLAY_LAYER_NAME: Final = "_multi_select_staged"


class StagedRenderer(Protocol):
    def add_staged_overlay(self, ids: frozenset[LabelId]) -> None: ...
    def update_staged_overlay(self, ids: frozenset[LabelId]) -> None: ...
    def remove_staged_overlay(self) -> None: ...
    def set_tool_click_handler(self, handler_or_none) -> None: ...


class SelectionSink(Protocol):
    def set_selection(self, label_ids: list[LabelId]) -> None: ...


@dataclass
class StagingBuffer:
    initial_ids: frozenset[LabelId]
    current: set[LabelId] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.current = set(self.initial_ids)

    def toggle(self, label_id: LabelId) -> None:
        if label_id in self.current:
            self.current.remove(label_id)
        else:
            self.current.add(label_id)

    def snapshot(self) -> frozenset[LabelId]:
        return frozenset(self.current)

    def is_dirty(self) -> bool:
        return frozenset(self.current) != self.initial_ids
```

### Controller shape

```python
# multi_select.py — sketch
class MultiLabelSelectController:
    def __init__(self, viewer_win: "ViewerWindow", data_model) -> None:
        self._viewer_win = viewer_win
        self._data_model = data_model
        self._buffer = StagingBuffer(
            initial_ids=frozenset(data_model.selected_ids)
        )
        self._prior_mode: str | None = None
        self._mouse_cb: Callable | None = None
        self._torn_down: bool = False
        self._refresh_timer = QTimer(self._window)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._do_refresh)

    def toggle(self, label_id: LabelId) -> None: ...
    def accept(self) -> None: ...      # renamed from commit for house style
    def cancel(self) -> None: ...
    def _install(self) -> None: ...
    def _uninstall(self) -> None: ...
    def _schedule_refresh(self) -> None: ...
    def _do_refresh(self) -> None: ...
```

### Install: mode switch + signal disconnect + callback + overlay

```python
# Install order matters — prevent the mode-switch race
def _install(self) -> None:
    layer = self._viewer_win.active_labels_layer()

    # 1. Silence napari's selected_label → CellDataModel path for the
    #    tool's lifetime. Prevents a click-in-flight from wiping the
    #    prior selection we just pre-filled staging from.
    self._viewer_win.suspend_selected_label_forwarding()

    # 2. Save and force mode.
    self._prior_mode = str(layer.mode)
    layer.mode = _PAN_ZOOM

    # 3. Add the staging overlay layer.
    self._viewer_win.add_staged_overlay(self._buffer.snapshot())

    # 4. Append click callback (left button only).
    def _on_click(layer_, event):
        if self._torn_down:
            return
        if event.button != 1:              # middle/right-click → pan / no-op
            return
        value = layer_.get_value(
            event.position,
            view_direction=event.view_direction,
            dims_displayed=event.dims_displayed,
            world=True,
        )
        if value is None:
            return
        label_id = int(value)
        if label_id == 0:                  # background
            return
        self.toggle(label_id)

    self._mouse_cb = _on_click
    layer.mouse_drag_callbacks.append(self._mouse_cb)

    # 5. Acquire workflow lock (reuses existing coordination primitive).
    self._launcher.set_workflow_locked(True)
```

### Uninstall: strict teardown order

```python
def _uninstall(self) -> None:
    self._torn_down = True                        # gates in-flight timer
    self._refresh_timer.stop()                    # cancel pending refresh

    layer = self._viewer_win.active_labels_layer_or_none()
    if layer is not None:
        import contextlib
        with contextlib.suppress(ValueError):
            if self._mouse_cb is not None:
                layer.mouse_drag_callbacks.remove(self._mouse_cb)
        if self._prior_mode is not None:
            layer.mode = self._prior_mode

    self._viewer_win.remove_staged_overlay()
    self._viewer_win.resume_selected_label_forwarding()
    self._launcher.set_workflow_locked(False)
    if self._window is not None:
        self._window.close()
```

### Coalesced refresh — single QTimer

```python
def toggle(self, label_id: LabelId) -> None:
    self._buffer.toggle(label_id)
    self._schedule_refresh()

def _schedule_refresh(self) -> None:
    if self._torn_down:
        return
    self._refresh_timer.start(0)     # Qt coalesces — restart is idempotent

def _do_refresh(self) -> None:
    if self._torn_down:              # guard against stale fire after teardown
        return
    if not self._viewer_win.isVisible():
        return
    snap = self._buffer.snapshot()
    self._viewer_win.update_staged_overlay(snap)
    self._window.update_counter(len(snap))
    self._window.set_accept_enabled(self._buffer.is_dirty())
```

### Commit / cancel — synchronous teardown

```python
def accept(self) -> None:
    snap = self._buffer.snapshot()
    self._uninstall()                             # strict order; timer stopped
    self._data_model.set_selection(list(snap))    # fires SELECTION_CHANGED

def cancel(self) -> None:
    self._uninstall()
    # Domain selection never touched — no explicit restore needed.
```

Commit flows through the canonical `CellDataModel.set_selection` →
`Session.set_selection(frozenset)` → `SELECTION_CHANGED` event →
`StateChange(selection=True)` ripple. All existing consumers (DataPlot,
CellTable, filter-from-selection, "Delete selected labels" in seg-QC,
export-selected) react without per-consumer wiring.

### ViewerWindow extensions — small and additive

New methods on `ViewerWindow` (no modification to `_update_label_display`):

```python
# viewer.py additions (sketch)

def active_labels_layer(self) -> "Labels":
    """Return the currently-selected Labels layer; raise if none."""

def active_labels_layer_or_none(self) -> "Labels | None":
    """Same but tolerant of viewer teardown."""

def add_staged_overlay(self, ids: frozenset[int]) -> None:
    """Add a read-only Labels layer named _multi_select_staged that
    renders only `ids` in cyan via DirectLabelColormap. Layer is hidden
    from the napari layer list if possible."""

def update_staged_overlay(self, ids: frozenset[int]) -> None:
    """Rebuild the overlay layer's colormap to render `ids`. Called from
    the coalesced refresh timer in the controller."""

def remove_staged_overlay(self) -> None:
    """Remove the overlay layer. Idempotent."""

def suspend_selected_label_forwarding(self) -> None:
    """Disconnect _on_label_selected from layer.events.selected_label.
    Sets a _forwarding_suspended flag. Idempotent."""

def resume_selected_label_forwarding(self) -> None:
    """Reverse of suspend_selected_label_forwarding. Idempotent."""
```

Implementation note: the overlay is a Labels layer whose `data` is a
**view** of the primary layer's label array (no copy). `DirectLabelColormap`
on the overlay maps only the staged IDs to cyan; everything else
(including label 0) is transparent. On `update_staged_overlay`, we rebuild
just the colormap `color_dict`, not the data — O(|staged|).

### UI (dock window)

`QMainWindow` parented to `ViewerWindow`, `setCentralWidget(QWidget)`:

- Title: "Multi-select"
- Counter label: "N cells staged"
- **Accept** button (disabled when `is_dirty() == False`)
- **Cancel** button
- Keyboard shortcuts via `QShortcut(QKeySequence(...), self._window,
  activated=...)` — `Ctrl+Return` → accept, `Esc` → cancel. Window-scoped
  (default) is correct because the dock is focus-isolated.

### LauncherWindow entry point

- `QAction` on the Selection-related toolbar area; text "Multi-select",
  shortcut `M`. Shortcut context: `Qt.WindowShortcut` (default) — the
  viewer window does NOT need to see the shortcut because the user always
  invokes this from the launcher UI or keyboard focus on the launcher.
  `Qt.ApplicationShortcut` would make the shortcut fire from any window;
  we don't need or want that.
- Action is `setEnabled(not launcher.is_workflow_locked())`; the
  launcher's existing workflow-lock signal flips it when seg-QC or
  threshold-QC (or another multi-select) is active.
- Handler: `launcher.viewer_win.launch_multi_select_tool(data_model)` —
  ViewerWindow constructs and owns the controller.

## Implementation Phases

Collapsed from five to two. The cut was the right call: state is 20 lines,
UI wiring plus mouse handling is tightly coupled, overlay is its own
self-contained surface.

### Phase 1 — Controller + dock + mouse handling + tests

**Files:**
- `src/percell4/gui/multi_select.py` — `StagingBuffer`, Protocols,
  `MultiLabelSelectController`, the QMainWindow/QWidget dock.
- `tests/test_gui_workflows/test_multi_select.py` — unit tests for
  `StagingBuffer` (toggle / snapshot / is_dirty / pre-fill) and for the
  controller using mock `StagedRenderer` + mock `SelectionSink`.
  `qtbot` for the shortcut-firing path only.

### Phase 2 — ViewerWindow overlay + launcher wiring + manual-test checklist

**Files:**
- `src/percell4/gui/viewer.py` — add the six helper methods listed above.
  Unit-test them where the logic is pure (overlay-layer add/remove
  idempotency, colormap rebuild shape) without a running QApplication.
- `src/percell4/interfaces/gui/main_window.py` — `QAction`, toolbar
  entry, keyboard `M`, enable/disable wired to `is_workflow_locked`.
- Manual-test checklist appended to the plan at end of Phase 2.

No automated tests for the napari mouse-callback dispatch — the codebase
pattern is `MagicMock(spec=ViewerWindow)` in `test_gui_workflows/conftest.py:31-46`,
and `test_interactive_runner.py:117-150` patches controllers wholesale.
Manual-test checklist suffices for integration.

## Acceptance Criteria

### Functional

- [ ] `StagingBuffer` dataclass exists in
      `src/percell4/gui/multi_select.py` with `toggle`, `snapshot`,
      `is_dirty`.
- [ ] `MultiLabelSelectController` class exists in the same file with the
      Protocols and method surface above.
- [ ] `LauncherWindow` gains a **Multi-select** toolbar action with keyboard
      shortcut `M` (Qt.WindowShortcut).
- [ ] Clicking the action opens a QMainWindow dock parented to
      `ViewerWindow`; dock has Accept / Cancel buttons and a live counter.
- [ ] Left-click on a label while the tool is active toggles that label
      in staging.
- [ ] Middle-click / right-click / Alt-drag / Space-drag are
      **not** intercepted (event.button filter).
- [ ] Clicks on background (label 0) and clicks outside the layer bounds
      (label value None) are no-ops.
- [ ] Staged cells render in cyan via a **dedicated overlay Labels layer**
      named `_multi_select_staged`. The existing `_update_label_display`
      function is untouched.
- [ ] `Ctrl+Return` or Accept button → domain selection replaced via
      `Session.set_selection(frozenset)`; overlay removed; layer mode
      restored to prior; `_on_label_selected` forwarding restored;
      workflow lock released.
- [ ] `Esc` or Cancel button → same teardown, domain selection unchanged.
- [ ] Accept button is **disabled** when `StagingBuffer.is_dirty()` is
      False (no net change to pre-filled set).
- [ ] Multi-select toolbar action is **disabled** when
      `launcher.is_workflow_locked()` returns True — reusing existing
      coordination; no new `_active_tool` flag.
- [ ] Closing the ViewerWindow while the tool is active does not raise a
      `RuntimeError` — controller's `isVisible()` guard and `_torn_down`
      flag cover the teardown-after-timer path.

### Non-functional

- [ ] No mutation of `layer.data` on the primary labels layer. Overlay
      layer is read-only.
- [ ] Fast clicking (≥ 5/s) coalesced via a single `QTimer(setSingleShot=True).start(0)`;
      no per-click GPU texture rebuild on the primary layer.
- [ ] `ruff` + `lint-imports` pass. Existing `tests/test_workflows/test_qt_free_imports.py`
      stays green — `multi_select.py` lives under `gui/` so Qt imports are
      allowed there.

### Quality gates

- [ ] StagingBuffer tests: pre-fill, toggle add, toggle remove, snapshot
      returns frozenset, is_dirty True/False cases. ≥ 6 tests.
- [ ] Controller tests with mock Protocols: install calls all six viewer
      helpers in the documented order; accept calls `SelectionSink.set_selection`
      with a list of the snapshot; cancel does not call set_selection.
      Timer cancellation on teardown verified via a fake `QTimer`.
- [ ] Manual-test checklist executed on macOS dev machine before merge;
      Windows-box checklist after Phase 2 lands.

## Technical Considerations

- **Net-new `mouse_drag_callbacks.append(...)` pattern** in this repo.
  Kept to one install/uninstall pair, symmetric, with `contextlib.suppress(ValueError)`
  on removal. Callback reference stored as `self._mouse_cb` so `remove`
  can find it.
- **Signal disconnect for the tool's lifetime** is the key fix for the
  mode-switch race. `ViewerWindow.suspend_selected_label_forwarding` sets
  a flag that `_on_label_selected` checks; `_is_originator` is a separate
  concern (prevents the *round-trip* loop when the viewer originates a
  selection change) and stays unchanged.
- **Overlay Labels layer**, not colormap extension. Rationale: the
  existing `_update_label_display` builder at `viewer.py:273-343` already
  has a 3-branch (filter ± selection) combinatorics; adding a staged tier
  makes it 2×2×2. A second Labels layer with its own `DirectLabelColormap`
  (staged IDs → cyan, everything else → transparent) is cleaner,
  owned-by-tool, and does not thrash the primary layer's GPU texture.
  No need for `events.colormap.blocker()` — the overlay is transient and
  tool-owned.
- **`mouse_pan` / `mouse_zoom` toggles** — napari ≥ 0.4.18 exposes these
  per-layer booleans as the documented replacement for the deprecated
  `interactive` attribute. We don't currently need them (`pan_zoom` mode
  doesn't interfere with a single-click toggle), but note them for future
  maintainers who might add drag-select.
- **Multi-layer edge case**: tool operates on the currently-active Labels
  layer (`viewer.layers.selection.active` if Labels, else first Labels
  layer). Documented behavior; not multi-layer.
- **Napari API drift risk** (0.5 → 0.7): callback signature, event
  attributes, `get_value` kwargs, and the `mouse_drag_callbacks` list
  plumbing have all been stable across this range — confirmed against
  source at `napari/utils/interactions.py:99-137`,
  `napari/_vispy/mouse_event.py:35-67`,
  `napari/layers/base/base.py:517-519,757-769`.

## Dependencies & Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Prior selection wiped during mode switch by an in-flight click | HIGH (critical path) | `suspend_selected_label_forwarding()` called **before** mode change in `_install` |
| Coalesced refresh fires after teardown, stomps colormap with stale cyan | HIGH (critical path) | `_torn_down` flag checked at top of `_do_refresh`; `_refresh_timer.stop()` synchronous in `_uninstall` |
| Pan/middle-click accidentally toggles a label | MEDIUM | `if event.button != 1: return` guard in callback |
| Overlay layer leaks if viewer closed mid-session | MEDIUM | `_uninstall` calls `remove_staged_overlay()` idempotently; `active_labels_layer_or_none()` tolerates torn-down viewer |
| `mouse_drag_callbacks.remove` raises on napari-wrapped callback | LOW (stable across 0.5-0.7 per source) | `contextlib.suppress(ValueError)` |
| `M` shortcut swallowed when napari has focus | LOW (by design — action invoked from launcher) | `Qt.WindowShortcut` is correct; do NOT promote to `Qt.ApplicationShortcut` without a real use case |
| LauncherWindow already has a keyboard shortcut using `M` | LOW (grep confirmed no existing single-letter shortcuts on LauncherWindow) | Phase 2 regression-check |
| User opens tool again while one is tearing down | LOW (serialized by workflow lock) | Toolbar action disabled while `is_workflow_locked()` is True |

## References

### Internal

- Brainstorm: [`docs/brainstorms/2026-04-17-napari-multi-label-selection-brainstorm.md`](../brainstorms/2026-04-17-napari-multi-label-selection-brainstorm.md)
- Selection flow: `src/percell4/application/session.py:50,120-122,144-148,162-167`; `src/percell4/model.py:22-35,82-84,138-139`
- Viewer integration: `src/percell4/gui/viewer.py:59-69,75,247-264,268,273-343,355-388`
- Workflow-lock primitive (reused): `src/percell4/interfaces/gui/main_window.py:1141-1157,326,1263`; Protocol at `src/percell4/workflows/host.py:25`
- Prior-art tool-window pattern: `src/percell4/gui/workflows/single_cell/seg_qc.py:69,171-215,205-210,297,399-403,499-513`
- Sibling top-level tool: `src/percell4/gui/threshold_qc.py:71,193-202,320,509`
- Prior-art toggle semantics (for docs cross-ref): `src/percell4/gui/data_plot.py:307-330`
- Test patterns (MagicMock viewer, no real napari): `tests/test_gui_workflows/conftest.py:31-46`; `tests/test_gui_workflows/test_interactive_runner.py:117-150`
- Shortcut precedent: `seg_qc.py:205-210` uses `QShortcut("Ctrl+Return")` window-scoped; `interfaces/gui/main_window.py:116-124` uses `QAction` without shortcut — this plan is the first `QAction.setShortcut` in the repo

### Institutional learnings to apply

- `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md` — why we choose overlay layer + occasional `events.colormap.blocker()` over repeated primary-layer colormap rebuilds (with overlay, blocker may not be needed at all, but keep in mind for the overlay path too if flicker appears).
- `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md` — never mutate `layer.data`; overlay approach inherits this.
- `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md` — commit must go through `CellDataModel.set_selection` so panels get the `state_changed` ripple. Plan already honors this.
- `docs/solutions/ui-bugs/percell4-phases-0-6-napari-qt-learnings.md` — guard debounced callbacks against closed/dead Qt windows; hence `_torn_down` + `isVisible()`.

### External

- napari callback source (0.6.6): `napari/utils/interactions.py:99-137`,
  `napari/_vispy/mouse_event.py:35-67`, `napari/layers/base/base.py:517-519,757-769`.
- napari example: `napari/examples/custom_mouse_functions.py` — the
  documented idiom for `mouse_drag_callbacks` with yield/click-vs-drag.
- `napari/napari#7054` — mouse-click public API removed without deprecation;
  plugins must detect click-vs-drag manually.
- `napari/napari#2189` — vispy events lack `stop_propagation`; mode must
  be `pan_zoom` before appending a custom callback (otherwise native
  PAINT/FILL/PICK still runs).
- `napari/napari#4837` — `layer.mouse_pan` / `layer.mouse_zoom` per-layer
  toggles replace the deprecated `interactive` attribute.
- Prior art (community): `haesleinhuepf/napari-manual-split-and-merge-labels`
  uses a **Points layer as staging container** instead of click
  accumulation — different UX trade-off; our modal-button + overlay-layer
  approach is better-discovered but novel in the napari ecosystem.
- Qt: [QAction.setShortcutContext](https://doc.qt.io/qt-5/qaction.html),
  [QShortcut](https://doc.qt.io/qt-5/qshortcut.html).
- napari DirectLabelColormap:
  [API ref](https://napari.org/dev/api/napari.utils.DirectLabelColormap.html) —
  `use_selection` only highlights one label; not useful for arbitrary sets.
