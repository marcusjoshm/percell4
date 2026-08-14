---
title: Seg-QC recovery UX tweaks — scroll bar + Modify Channel Re-run button
type: feat
status: active
date: 2026-05-26
---

# Seg-QC recovery UX tweaks — scroll bar + Modify Channel Re-run button

## Overview

Two small UX improvements to the segmentation QC window that shipped
in `docs/plans/2026-05-26-001-feat-seg-qc-recovery-options-plan.md`:

1. **Scroll bar.** The QC dock now has four vertical groups (Label
   Tools, Cleanup, Re-run Cellpose, Modify Channel) plus a hint label
   and nav bar. On a 520-px-tall window with all groups expanded the
   content overflows and the user can't reach the bottom buttons.
   Wrap the central widget in a `QScrollArea` so the content scrolls
   vertically when it exceeds the window height.

2. **Re-run button inside Modify Channel.** After tuning the LUT
   handles, the user has to navigate to the Re-run Cellpose group to
   actually trigger segmentation against the modified preview.
   Add a `▶ Run Cellpose` button at the bottom of the Modify Channel
   group that calls the existing `_on_rerun_clicked` handler — same
   worker, same labels replacement, same knobs from the Re-run group.

Both changes are bounded to a single file
(`src/percell4/gui/workflows/single_cell/seg_qc.py`) plus matching
tests, and have no interaction with the runner, the use-case layer,
or the on-disk store.

---

## Problem Frame

The previously-shipped seg-QC recovery feature added Re-run Cellpose
and Modify Channel groups to the QC dock. Two UX gaps surfaced on
first use:

- With both new groups expanded, the dock content exceeds the QC
  window's 520-px default height. Without a scroll bar the user can
  reach the top groups but the nav bar (Accept / Cancel) and the
  Modify Channel Auto / handles are clipped.
- The Modify Channel group sets up the clipped/stretched preview, but
  triggering Cellpose against that preview requires moving focus to
  the separate Re-run Cellpose group below. The user expects the
  preview group to "own" the re-run since that's where they're
  iterating.

Related context:
`docs/brainstorms/2026-05-26-seg-qc-recovery-options-requirements.md`
(origin requirements for the recently-shipped seg-QC recovery feature).

---

## Requirements Trace

- R1. The QC dock scrolls vertically when its content exceeds the
  window height. Horizontal scrolling is not introduced — the dock's
  fixed width remains 320 px.
- R2. A `▶ Run Cellpose` button appears at the bottom of the Modify
  Channel group. Clicking it triggers the same Cellpose worker the
  Re-run Cellpose group's button triggers, using the same current
  values of diameter / channel / flow / cellprob / model / min_size
  from the Re-run group widgets, and replaces the in-QC labels layer
  on success.
- R3. The new button is enabled / disabled in lockstep with the
  Re-run group's button — disabled while a worker is in flight, both
  re-enabled when it finishes or errors. No two workers can run
  concurrently from these two buttons.
- R4. No on-disk side effects: the `/intensity` and `/labels`
  on-disk state semantics are unchanged. Persistence still only
  happens on Accept.

---

## Scope Boundaries

- The scroll bar is vertical-only. The dock's fixed 320-px width is
  preserved; horizontal scrolling would imply the user can hide
  controls behind a horizontal bar, which is worse UX than a
  consistent dock width.
- The Modify Channel Re-run button reuses the Re-run group's knob
  values as-is. It does **not** duplicate the diameter / channel /
  thresholds / model / min_size controls inside the Modify Channel
  group.
- No changes to the QC window's default size (`window.resize(320, 520)`).
  The scroll bar exists to handle the overflow case; the default
  window remains compact for users who collapse the new groups.
- No changes to the runner, base_runner, or any use-case in
  `src/percell4/application/`. This plan is GUI-only.
- No new keyboard shortcuts for the Modify Channel Re-run. Ctrl+Enter
  still means Accept; Esc still means Cancel.
- No new hotkey for "Re-run Cellpose" — deliberately deferred until a
  separate accelerators pass that handles all dock actions
  consistently.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/gui/workflows/single_cell/seg_qc.py:_build_window` —
  constructs the QC window and currently does `window.setCentralWidget(central)`
  with no scrolling. Insertion point for the QScrollArea wrap is
  immediately before that call.
- `src/percell4/gui/workflows/single_cell/seg_qc.py:_build_modify_channel_group` —
  builds the Modify Channel group; new Re-run button appends to its
  body layout after the Saturation/Auto row.
- `src/percell4/gui/workflows/single_cell/seg_qc.py:_on_rerun_clicked` —
  existing handler for the Re-run Cellpose group's button. Spawns
  a `Worker(run_cellpose, ...)`, disables `self._rerun_button` while
  in flight, re-enables in `_on_rerun_finished` / `_on_rerun_error`.
  This handler is the single source of truth for the in-QC Cellpose
  call — the new button hooks the same method.
- `src/percell4/gui/workflows/single_cell/seg_qc.py:_build_rerun_group` —
  the established pattern for adding a `▶`-prefixed action button at
  the bottom of a group. The Modify Channel Re-run button mirrors
  that styling.
- `tests/test_gui_workflows/test_seg_qc_modify_channel.py` and
  `tests/test_gui_workflows/test_seg_qc_modify_and_rerun.py` — fixture
  pattern (`controller` + `viewer_win`) for testing the controller
  with a real DatasetStore on tmp_path. New tests reuse the same
  fixtures.

### Institutional Learnings

- `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md` —
  controller lifecycle (`_finish`, `_torn_down`) is already correct in
  the QC controller; the scroll-area wrap is purely a parent-widget
  swap and does not affect teardown ordering.

### External References

None — Qt's `QScrollArea` + a layout-managed widget is a one-method
wrap, well-documented in the Qt docs and used elsewhere in PerCell4
(e.g. `add_layer_dialog.py`, `compress_dialog.py`).

---

## Key Technical Decisions

- **`QScrollArea.setWidgetResizable(True)`** so the inner widget
  grows to match the scroll-area viewport's width — without this the
  inner widget defaults to its size hint and the dock looks narrower
  than the window.
- **Vertical scroll only.** Set the horizontal scroll bar policy to
  `Qt.ScrollBarAlwaysOff` so a too-wide row (e.g. a long status
  message) cannot introduce horizontal scrolling. The dock width is
  a layout invariant.
- **Modify Channel Re-run button calls `_on_rerun_clicked` directly**
  rather than duplicating the Cellpose-worker plumbing. The button is
  a re-entrant entry point into the existing handler. Pros: zero
  duplication, automatically inherits future Re-run fixes. Cons: the
  button is "tied to" the Re-run group's knobs — but the alternative
  (duplicate controls) is much larger surface area for marginal
  benefit (user can just expand the Re-run group once).
- **Both buttons stay enabled/disabled in lockstep.** When
  `_on_rerun_clicked` disables `self._rerun_button`, we also disable
  the Modify Channel button. When `_on_rerun_finished` /
  `_on_rerun_error` re-enables `self._rerun_button`, we re-enable
  the Modify Channel button. Implementation: hold a reference to
  the new button (`self._modify_rerun_button`) and toggle it
  alongside the existing one in the three sites that touch the
  Re-run button enabled state.

---

## Open Questions

### Resolved During Planning

- **Where the Modify Channel Re-run button lives:** at the bottom of
  the Modify Channel group (resolved with the user in the planning
  question).
- **Whether to duplicate the Re-run knobs inline:** no — reuse the
  Re-run group's knobs to avoid divergent state.
- **Scroll direction:** vertical only — horizontal scroll would hide
  controls behind a non-obvious bar.

### Deferred to Implementation

- **Exact default size of the QScrollArea.** Probably the scroll-area
  inherits the window's central-widget sizing; if not, set a sensible
  `minimumWidth`/`sizeHint`. Verify visually in implementation.
- **Whether to nudge the default window height up slightly** (e.g.
  520 → 600) given the new groups. Strictly out of scope unless the
  default still feels cramped after the scroll bar lands — the
  scroll bar already addresses the blocking issue.

---

## Implementation Units

- U1. **Wrap the QC dock in a QScrollArea**

**Goal:** The QC window's central widget becomes a `QScrollArea`
whose inner widget is the existing tool dock. Content scrolls
vertically when it overflows the window height. Horizontal scrolling
is disabled. No change to default window size or fixed dock width.

**Requirements:** R1

**Dependencies:** None

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/seg_qc.py`
- Test: `tests/test_gui_workflows/test_seg_qc_scroll.py`

**Approach:**
- In `_build_window`, after constructing `central` + its layout and
  populating it, wrap it: build a `QScrollArea`, set its widget to
  `central`, set `setWidgetResizable(True)`, set
  `setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)`, then call
  `window.setCentralWidget(scroll)` (instead of `central`).
- Import `QScrollArea` from `qtpy.QtWidgets`.
- Hold a reference to the scroll area (`self._scroll_area`) only if
  tests need it. Otherwise the inner `central` widget remains
  accessible via the scroll area's widget; tests can navigate
  `window.centralWidget().widget()` to assert structure.

**Patterns to follow:**
- `src/percell4/gui/compress_dialog.py` for a comparable
  `QScrollArea` wrap on a vertical form (already in the codebase).

**Test scenarios:**
- Happy path — `QC window's central widget is a QScrollArea`: open
  a controller, assert `isinstance(ctrl._window.centralWidget(), QScrollArea)`.
- Happy path — `inner widget is widget-resizable`: assert
  `ctrl._window.centralWidget().widgetResizable() is True`.
- Edge case — `horizontal scroll never appears`: assert the
  horizontal scrollbar policy is `Qt.ScrollBarAlwaysOff`.
- Edge case — `existing Edit / Cleanup / Re-run / Modify groups
  remain reachable`: walk the scroll area's inner widget and assert
  each of the four `QGroupBox` titles is found. Regression guard
  against accidentally dropping a group during the wrap.

**Verification:**
- Resizing the QC window smaller than the dock content shows a
  vertical scroll bar; horizontal never appears; all four groups
  remain reachable by scrolling.

---

- U2. **Re-run button inside the Modify Channel group**

**Goal:** Add a `▶ Run Cellpose` button at the bottom of the Modify
Channel group. Clicking it triggers the existing `_on_rerun_clicked`
handler. The button is enabled/disabled in lockstep with the Re-run
Cellpose group's button so concurrent worker spawns are impossible.

**Requirements:** R2, R3, R4

**Dependencies:** None (U1 and U2 are independent; can land in
either order, but typical commit order is U1 → U2)

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/seg_qc.py`
- Test: `tests/test_gui_workflows/test_seg_qc_modify_channel_rerun.py`

**Approach:**
- In `_build_modify_channel_group`, append a new `QPushButton`
  ("▶ Run Cellpose") to the body layout below the Saturation / Auto
  row. Tooltip: "Re-run Cellpose against the modified channel
  preview. Uses the parameters set in the Re-run Cellpose group."
- Connect the button's `clicked` signal to `self._on_rerun_clicked`
  (the same handler the Re-run group's button uses).
- Store the new button on `self._modify_rerun_button` so the existing
  enable/disable sites in `_on_rerun_clicked` / `_on_rerun_finished`
  / `_on_rerun_error` can toggle both buttons together. Update those
  three call sites to toggle `self._modify_rerun_button` alongside
  `self._rerun_button` (guarded by `is not None` since the Modify
  Channel group is built after the Re-run group; safe even if the
  Modify Channel button hasn't been constructed yet).

**Patterns to follow:**
- `_build_rerun_group` for the `▶`-prefixed action button styling
  and tooltip pattern.
- The existing enable/disable triad in `_on_rerun_clicked`,
  `_on_rerun_finished`, `_on_rerun_error` — extend, don't fork.

**Test scenarios:**
- Happy path — `clicking Modify Channel Re-run triggers run_cellpose`:
  monkeypatch `run_cellpose` to record its calls, click the new
  button, await the worker, assert exactly one call recorded and the
  labels layer was replaced.
- Happy path — `Modify Channel Re-run uses Re-run group's knob
  values`: set diameter=42 in the Re-run group's spinbox, click the
  Modify Channel button, assert `run_cellpose` was called with
  `diameter=42`.
- Edge case — `both buttons disabled while worker in flight`: click
  the Modify Channel button, assert `ctrl._rerun_button.isEnabled()`
  AND `ctrl._modify_rerun_button.isEnabled()` are both False before
  the worker finishes.
- Edge case — `both buttons re-enabled after success`: await the
  worker, assert both are True again.
- Edge case — `both buttons re-enabled after error`: monkeypatch
  `run_cellpose` to raise, click the Modify Channel button, await,
  assert both buttons re-enabled.
- Integration — `Modify Channel Re-run with active LUT feeds the
  modified image`: expand the Modify Channel group (this installs the
  clipped/stretched preview into the napari channel layer), click the
  Modify Channel button, assert `run_cellpose` received the clipped
  image (not the raw on-disk channel). This is the load-bearing
  behavior — the whole point of the button is that the user just
  tuned the LUT and wants to segment that view.

**Verification:**
- With the Modify Channel group expanded and the Re-run group
  collapsed, clicking the Modify Channel Re-run button kicks off a
  Cellpose run against the clipped preview and replaces the in-QC
  labels on success — without expanding or focusing the Re-run
  group.

---

## System-Wide Impact

- **Interaction graph:** Both new affordances live inside
  `SegmentationQCController`. The scroll area is purely a parent
  widget; the Modify Channel Re-run button is a re-entrant call into
  the existing `_on_rerun_clicked` path. No new signals, no new
  module touched.
- **Error propagation:** Worker error path is unchanged — `_on_rerun_error`
  already shows a status message and re-enables the button(s). The
  only new piece is that two buttons now flip together.
- **State lifecycle risks:** None. `_finish` already tears down the
  whole window; the QScrollArea + Modify Channel button are inside
  the central-widget subtree and get freed with it. The torn-down
  guard on the Modify Channel preview still works because the
  preview revert + timer stop happen at the same `_finish` point.
- **API surface parity:** No public API change.
- **Integration coverage:** The Modify Channel + Re-run integration
  is already covered end-to-end by
  `tests/test_gui_workflows/test_seg_qc_modify_and_rerun.py`. U2's
  new tests add the alternative entry point (Modify Channel button
  vs. Re-run group button) but the underlying integration is the
  same path.
- **Unchanged invariants:**
  - On-disk `/intensity` and `/labels/cellpose_qc` semantics unchanged.
  - `_on_rerun_clicked` always-replace behavior unchanged.
  - Cancel cancels the whole workflow run — unchanged.
  - Worker can only run one at a time — preserved by the
    button-disable lockstep.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `QScrollArea.setWidgetResizable(True)` fights with napari's window-management or causes layout flicker on first show | Smoke-tested via U1 happy-path tests. If the dock paints oddly, the fallback is `setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)` on the inner widget; record as a deferred follow-up if observed. |
| Modify Channel button click races with Re-run group button click | Both call the same `_on_rerun_clicked`, which already gates on `self._rerun_button.setEnabled(False)` on first entry. Extending that to also disable `self._modify_rerun_button` makes the second click a no-op at the Qt level. |
| Adding the Modify Channel button below the Saturation row clips it on narrow windows | Resolved by U1's scroll bar — the QC dock is now scrollable, so the new button can't be permanently hidden. |
| Future Re-run handler change forgets to toggle the new button | The three call sites are co-located in one file. Tests assert both buttons flip together on each path (in-flight, success, error). |

---

## Documentation / Operational Notes

- No user docs to update — the seg-QC dock's behavior is described
  briefly in `src/percell4/gui/workflows/CLAUDE.md` and that file
  describes the controller in general terms that remain accurate.
- No solutions-doc entry warranted; both changes are local UI tweaks
  with no reusable insight beyond what the existing
  `creator-contract-four-step-sequence` / `napari-modal-tool-overlay-pattern`
  learnings already cover.

---

## Sources & References

- Related plan: `docs/plans/2026-05-26-001-feat-seg-qc-recovery-options-plan.md`
  (the just-shipped seg-QC recovery options work that U1 / U2 build on).
- Related requirements doc: `docs/brainstorms/2026-05-26-seg-qc-recovery-options-requirements.md`.
- Related code: `src/percell4/gui/workflows/single_cell/seg_qc.py`
  (`_build_window`, `_build_modify_channel_group`, `_on_rerun_clicked`,
  `_on_rerun_finished`, `_on_rerun_error`).
- Related tests: `tests/test_gui_workflows/test_seg_qc_modify_channel.py`,
  `tests/test_gui_workflows/test_seg_qc_modify_and_rerun.py` (fixture
  patterns to mirror).
