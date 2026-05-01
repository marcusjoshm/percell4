---
title: "fix: Dataset lifecycle and resource-list events — coherent reload + live combo refresh"
type: fix
status: active
date: 2026-05-01
origin: docs/brainstorms/2026-05-01-dataset-lifecycle-and-resource-list-events-requirements.md
---

# fix: Dataset lifecycle and resource-list events

## Overview

Fix three reproducible bugs that share one root cause: PerCell4's session
emits events when the *selection* of a channel/segmentation/mask changes,
but not when the *underlying inventory* changes. Combo populators and
peer-view caches go stale on dataset reload (especially when names overlap
across datasets) and on Creator writes (new resources don't appear until
restart).

The fix introduces three new Session events
(`CHANNEL_LIST_CHANGED`, `SEGMENTATION_LIST_CHANGED`, `MASK_LIST_CHANGED`),
extends `Session.set_dataset` to auto-select first seg/mask (closing the
gap with the audit's default-state rule), tightens DataPanel's combo
subscriber to fully repopulate on each list event, invalidates peer-view
caches on `DATASET_CHANGED`, and threads the new event through every
Creator entry point.

---

## Problem Frame

Three user-observed bugs (origin §"Anchor bugs"):

- **C1** — `Session.set_dataset` only auto-selects the first channel; seg
  and mask are nulled (`src/percell4/application/session.py:143-144`).
- **C2** — `Filter by active mask` becomes unclickable after loading a
  second dataset whose mask name happens to overlap with the first.
  DataPanel's `_on_model_active_mask_changed` skips its update branch
  when the new name is `None`, leaving stale combo text on top of a
  cleared session (`data_panel.py:198-206`). PhasorPlotWindow's
  `_active_mask_array` / `_active_mask_flat` caches survive the
  dataset switch.
- **C3** — Creator writes (Cellpose, threshold accept, add-layer,
  ROI-to-mask) don't refresh the Data tab combos until app restart.
  No live event fires when the inventory changes mid-session.

The plan addresses all three with one cohesive change to the
event surface, with downstream subscriber and Creator updates that
fall out of the new contract.

---

## Requirements Trace

- R1. Loading a new dataset is a coherent fresh start: every combo, every
  cache in every open peer view, and every active selection reflects the
  new dataset's actual state. *(origin: Goals)*
- R2. Creators that add a new resource immediately make it visible in the
  Data tab combos and the Layer Management dropdowns, without app restart.
  *(origin: Goals)*
- R3. The default-state rule from the prior audit holds in code: dataset
  load auto-selects first available channel, segmentation, and mask.
  *(origin: Goals; prior audit's Default-state rule)*
- R4. The Selector / Creator / Action taxonomy and Invariants I1–I5 from
  the prior audit are preserved unchanged. *(origin: Goals)*

**Origin acceptance examples:**
- AE1 — Auto-select on load. `Dish_1` → all three Active combos populate.
- AE2 — Clean slate on dataset switch. Dish_1 → Dish_2; phasor checkbox
  re-engages on first try, no restart.
- AE3 — Live combo refresh on Creator write. Cellpose / threshold accept /
  add-layer / ROI-to-mask all refresh combos immediately.
- AE4 — Creator auto-select. New resource is auto-selected per the prior
  audit's Creator contract.

---

## Scope Boundaries

- Adding new Selectors or per-module dropdowns. *(origin: Non-goals)*
- Restructuring the `Session` ↔ `CellDataModel` bridge. The 5-step
  rule from `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`
  is followed without change.
- Persisting phasor ROIs across dataset loads. ROIs reset on dataset
  load (see Key Technical Decisions, OQ-1 resolution).
- Refactoring the `metadata` shape on `DatasetHandle` beyond adding two
  new keys. *(origin: Non-goals)*
- Performance / UI-responsiveness of the combo refresh.

---

## Context & Research

### Relevant Code and Patterns

- **Session events** (`src/percell4/application/session.py:19-28`) — seven
  events today; the new three are added to the same enum, follow the same
  emission pattern (`_emit(Event.X)` from setters/lifecycle methods).
- **`set_dataset` lifecycle** (`session.py:126-165`) — already resets all
  slots, auto-selects first channel, emits per-slot events for prior
  non-None values. The fix extends it; structure stays the same.
- **DatasetHandle.metadata** (`src/percell4/domain/dataset.py:26`) —
  `dict[str, Any]`. Today contains `channel_names`. The fix adds
  `segmentation_names` and `mask_names` populated at handle-creation
  time from store APIs.
- **Store inventory APIs** — `src/percell4/store.py:270`
  (`list_masks`), `list_labels()`. Source of truth for the new
  metadata keys.
- **DataPanel state-change subscriber** (`task_panels/data_panel.py:51, 164-172`)
  — current handler dispatches on `change.segmentation`, `change.mask`,
  `change.data`. The fix adds the three new list flags and tightens the
  combo-refresh logic.
- **Combo populator pattern** — `data_panel.py:282` (`_populate_channel_combo`),
  `data_panel.py:210-249` (`refresh_management_combos`, `refresh_active_combos`).
  Already use `blockSignals(True/False)` to prevent feedback loops. The
  fix calls these from the new list-event handlers.
- **Session→CellDataModel bridge** — the 5-step rule from
  `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`:
  Event enum → StateChange field → subscribe → handler → panel
  `_on_state_changed` update.
- **Per-slot emission rule** (2026-04-30 companion rule) — `Session.set_*`
  methods that change input semantics must invalidate dependent caches
  and emit per-slot events. The new list events follow this rule for
  the inventory side.

### Institutional Learnings

- `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`
  — the 5-step rule for adding any new event. Directly applicable.
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  — the 5-vector staleness compound. C2's PhasorPlotWindow cache
  staleness is in the same family; treat it the same way (invalidate
  on `DATASET_CHANGED` plus on the new `MASK_LIST_CHANGED` if the
  current active mask name was removed).
- `docs/audits/subscriber-rebind-matrix.md` (just written) — defines the
  rebind contract. The plan extends the matrix to add a "list-event"
  column.
- `docs/audits/session-mutation-graph.md` (just written) — every Creator
  is already enumerated. The plan adds a "fires which list event" column.
- `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`
  — Creators auto-select after writing. The fix preserves this; the
  list event fires *before* the auto-select so subscribers re-list
  before they look up the new name.

### External References

None. Codebase-internal change; all patterns already exist locally.

---

## Key Technical Decisions

- **OQ-1 — ROIs reset on dataset load.** ROIs are spatial regions on a
  specific dataset's phasor coordinate range; carrying them across loads
  has no user-validated semantics. PhasorPlotWindow clears its ROI list
  in its `DATASET_CHANGED` handler. Reversible later if user feedback
  asks for it.
- **OQ-2 — Three separate list events, not one with payload.** Mirrors
  the existing `ACTIVE_*_CHANGED` pattern; subscribers that only care
  about one kind don't have to filter on a payload field. Three events,
  three new `StateChange` flags, three Qt signals — mechanical.
- **OQ-3 — `DatasetHandle.metadata` gains `segmentation_names` and
  `mask_names`.** Populated at handle-creation time from
  `store.list_labels()` and `store.list_masks()`. Session reads them
  during `set_dataset` for auto-selection. Refresh API
  re-derives from store on Creator writes.
- **OQ-4 — `Session.clear()` emits empty list events.** For symmetry —
  combos clear cleanly when a dataset is closed.
- **List event fires before auto-select.** Order in `set_dataset` and in
  Creator paths: (1) refresh inventory, (2) emit list event, (3) call
  `set_active_*` which fires the existing selection event. Subscribers
  that populate the combo's items list run before subscribers that
  look up the just-selected name.
- **`Session` exposes a `refresh_resource_lists()` method.** Creators
  call it after writing a new resource. The method re-derives the
  inventory from the store and emits the relevant list event(s).
  Centralizes the inventory-refresh logic so each Creator stays a
  one-line change.
- **DataPanel's `_on_model_active_*_changed` no longer mutates the combo
  list.** That's now the list-event handler's job. The active-changed
  handler only sets `currentText` to the (now-known) new active name.
  Cleaner separation of concerns.

---

## Open Questions

### Resolved During Planning

- OQ-1: ROIs reset on dataset load. *(see Key Technical Decisions)*
- OQ-2: Three separate list events. *(see Key Technical Decisions)*
- OQ-3: Add `segmentation_names` and `mask_names` to `DatasetHandle.metadata`.
  *(see Key Technical Decisions)*
- OQ-4: `Session.clear()` emits empty list events. *(see Key Technical Decisions)*
- Event-firing order in `set_dataset`: list events fire before
  selection events. *(see Key Technical Decisions)*

### Deferred to Implementation

- Whether `refresh_resource_lists()` should also accept a hint (e.g.,
  `kinds={"mask"}`) to fire only the relevant subset of events, or
  always re-derive all three. Defer until the Creator-update pass —
  the simpler "emit all three on every refresh" is the starting point;
  if it produces noticeable redundant work, narrow.
- Whether Cellpose's "Create Empty Labels" (in-memory only) should
  fire `SEGMENTATION_LIST_CHANGED`. The audit classified this as a
  Creator over an in-memory-only resource; if the in-memory layer is
  not in `store.list_labels()`, the event would be a no-op for the
  store-backed combo populator. Verify during U5.
- Whether channel-rename and channel-delete (already classified as
  Creator-cleanup in the prior audit's mutation graph) need to fire
  `CHANNEL_LIST_CHANGED` in addition to the existing
  `ACTIVE_CHANNEL_CHANGED`. Likely yes (the inventory changed).
  Confirm during U5.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance
> for review, not implementation specification. The implementing agent
> should treat it as context, not code to reproduce.*

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          Creator (e.g. Cellpose)                          │
│                                                                            │
│  1. write resource to store (existing)                                     │
│  2. session.refresh_resource_lists()      ──► emits SEGMENTATION_LIST_CHANGED
│  3. session.set_active_segmentation(name) ──► emits ACTIVE_SEGMENTATION_CHANGED
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                              Session                                      │
│                                                                            │
│   _emit(SEGMENTATION_LIST_CHANGED) ──► CellDataModel bridge re-emits as   │
│                                         StateChange(segmentation_list=True) │
│   _emit(ACTIVE_SEGMENTATION_CHANGED) ──► StateChange(segmentation=True)   │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                ┌─────────────────┼─────────────────┐
                ▼                 ▼                 ▼
        ┌─────────────┐    ┌─────────────┐   ┌─────────────┐
        │ DataPanel   │    │ PhasorPlot  │   │ DataPlot /  │
        │             │    │             │   │ CellTable   │
        │ list flag:  │    │ list flag:  │   │ list flag:  │
        │ re-list     │    │ (no-op for  │   │ (no-op for  │
        │ combos      │    │  list)      │   │  list)      │
        │             │    │ DATASET:    │   │ DATASET:    │
        │ active flag:│    │ invalidate  │   │ invalidate  │
        │ setText     │    │ caches +    │   │ content     │
        │             │    │ reset ROIs  │   │             │
        └─────────────┘    └─────────────┘   └─────────────┘
```

Dataset-load sequence (set_dataset):

```
prev = (active_seg, active_mask, filter_ids, selection)
clear all slots
auto-select first channel from new metadata
auto-select first segmentation from new metadata  ← R3 / U2
auto-select first mask from new metadata          ← R3 / U2
refresh inventory cache from store
emit DATASET_CHANGED
emit CHANNEL_LIST_CHANGED       ← always (new dataset = new list)
emit SEGMENTATION_LIST_CHANGED  ← always
emit MASK_LIST_CHANGED          ← always
emit ACTIVE_*_CHANGED for each slot whose value transitioned
  (covers both: prev was non-None now None, and prev was None now
   auto-selected)
```

---

## Implementation Units

### Phase 1 — Domain and application changes

- U1. **Add three list events end-to-end (Event enum + StateChange flag + bridge)**

**Goal:** Introduce `CHANNEL_LIST_CHANGED`, `SEGMENTATION_LIST_CHANGED`,
`MASK_LIST_CHANGED` to the Session event surface, bridge them into
`CellDataModel.state_changed` per the 5-step rule, and surface them as
new `StateChange` flags (`channel_list`, `segmentation_list`,
`mask_list`).

**Requirements:** R1, R2 (foundation); enables R3 and downstream subscribers.

**Dependencies:** None.

**Files:**
- Modify: `src/percell4/application/session.py` — add three Event enum
  members.
- Modify: `src/percell4/model.py` — add three boolean fields to
  `StateChange`; subscribe `CellDataModel` to the new Session events;
  re-emit each as the corresponding `StateChange` flag.
- Test: `tests/test_application/test_session_events.py` (or extend
  the existing test if one already exists; verify with grep).
- Test: `tests/test_application/test_celldatamodel_bridge.py` (same — extend
  if exists).

**Approach:**
- Three separate Event enum members. Three separate StateChange flags.
- The bridge code in CellDataModel follows the existing pattern: subscribe
  in `__init__`, the handler emits `state_changed` with a `StateChange`
  carrying the relevant flag set to True. Use the existing helper if one
  exists; otherwise mirror the existing `ACTIVE_*_CHANGED` bridges line
  for line.
- This unit produces no user-visible behavior change. It's pure plumbing
  that downstream units consume.

**Patterns to follow:**
- `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`
  — the 5-step rule.
- The existing `ACTIVE_CHANNEL_CHANGED` → `StateChange(segmentation=True)`
  bridge in `CellDataModel`.

**Test scenarios:**
- Happy path: subscribing to each new event via `Session.subscribe(...)`
  receives a callback when the event is `_emit`ted directly.
- Happy path: `CellDataModel.state_changed` fires with the matching flag
  set when each new Session event fires.
- Edge case: subscribing and immediately unsubscribing does not break
  later emissions to other subscribers.

**Verification:**
- All three new events compile, fire, and bridge through to `state_changed`.
- Existing event tests still pass.

---

- U2. **`DatasetHandle.metadata` gains seg/mask name lists; `Session.set_dataset` auto-selects all three; `Session.clear()` emits empty list events**

**Goal:** Implement R3 (auto-select first available seg and mask on
load) and OQ-4 (clear() emits empty list events). Populate
`DatasetHandle.metadata["segmentation_names"]` and `["mask_names"]` at
handle-creation time. Update `set_dataset` to read these and auto-select
first available; emit the three list events with the new inventory.

**Requirements:** R1, R3.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/domain/dataset.py` — document the new metadata
  keys (no schema change since `metadata` is `dict[str, Any]`; this is
  a contract change captured in docstring + downstream readers).
- Modify: `src/percell4/adapters/hdf5_store.py` and / or wherever
  `DatasetHandle` is constructed during dataset load — populate
  `segmentation_names` and `mask_names` from `store.list_labels()` and
  `store.list_masks()`. Verify with grep where `DatasetHandle(...)` is
  instantiated.
- Modify: `src/percell4/application/session.py` — `set_dataset` reads
  the two new metadata keys, auto-selects first entry of each (or `None`
  if empty); fires the three list events; `clear()` emits empty list
  events.
- Test: `tests/test_application/test_session_set_dataset.py` (or
  extend the existing).

**Approach:**
- Auto-select logic: same shape as the existing channel auto-select
  at `session.py:151-155`. Three blocks, one per kind.
- Emit ordering inside `set_dataset` per the High-Level Technical
  Design above: clear slots → auto-select → emit `DATASET_CHANGED` →
  emit three list events → emit per-slot `ACTIVE_*_CHANGED` for
  transitions.
- The transition emission for `ACTIVE_*_CHANGED` needs a small
  refinement: today (lines 158-165) it only emits when prev was
  non-None. The fix needs to also emit when prev was None and new
  is non-None (the auto-selected case). Otherwise the DataPanel won't
  hear about the new auto-selection.
- `clear()` extension: emit three empty list events plus the existing
  `DATASET_CHANGED`.
- The metadata-population side lives in adapters; the core change is
  ~5 lines per kind. Keep the existing `channel_names` population as
  the template.

**Patterns to follow:**
- Existing `channel_names` at adapter boundary (find with grep:
  `grep -rn "channel_names" src/percell4/adapters/`).
- The existing `set_dataset` lifecycle ordering and transition-only
  emit pattern.

**Test scenarios:**
- Happy path: Covers AE1. Load a dataset whose metadata has
  `["seg1", "seg2"]` and `["mask1"]`; assert
  `session.active_segmentation == "seg1"`, `session.active_mask == "mask1"`,
  `session.active_channel == <first channel>`.
- Edge case: Empty `segmentation_names` → `active_segmentation` is `None`.
- Edge case: Empty `mask_names` → `active_mask` is `None`.
- Edge case: Missing keys (older handle without the new metadata) →
  treated as empty lists; auto-select is `None`; no exception.
- Happy path: list events fire on `set_dataset`. Subscribe to all three
  before calling; assert each fires exactly once.
- Edge case: `set_dataset(None)` — clears all slots; emits three empty
  list events.
- Happy path: `clear()` emits three empty list events plus
  `DATASET_CHANGED`.
- Edge case: Loading dataset 2 after dataset 1 (overlap). Asserts
  `ACTIVE_MASK_CHANGED` fires after the auto-selection transition,
  not just when prev was non-None.

**Verification:**
- Test U2 passes.
- Existing tests for `set_dataset` and `clear()` still pass.
- `DatasetHandle(...)` instantiation sites all populate the two new
  metadata keys.

---

- U3. **`Session.refresh_resource_lists()` API for Creator-driven inventory updates**

**Goal:** Add a method on Session that re-derives the inventory from
the store and emits the three list events. Creators call this after
writing a new resource (U5 wires them up).

**Requirements:** R2 (foundation for live combo refresh).

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/application/session.py` — add
  `refresh_resource_lists()` method.
- Test: `tests/test_application/test_session_refresh_resource_lists.py`
  (or merge into the existing session tests).

**Approach:**
- The method needs access to a store. Two options:
  - (a) The Session caches a store reference (set during `set_dataset`).
    Cleanest if the application layer already has access; check
    `set_dataset`'s caller chain.
  - (b) The method takes the store as a parameter; the caller passes it.
    Simpler from an architectural standpoint (Session stays Qt-free
    and store-free), but every caller has to know the store.
  - **Recommended: (a) if the Session already has store access via the
    handle's metadata or an injected port, otherwise (b).** Verify
    during U3 — the existing `set_dataset` path may give the answer.
- The method re-reads `list_labels()` / `list_masks()` from the store,
  updates `metadata["segmentation_names"]` and `["mask_names"]` on the
  current `DatasetHandle`, and emits the three list events.
- For now, always emit all three events. The "narrow to specific kinds"
  optimization is in Deferred-to-Implementation.

**Patterns to follow:**
- `set_dataset`'s metadata-read pattern (after U2 lands).

**Test scenarios:**
- Happy path: With a session and a fake store, write a new mask name
  to the store, call `refresh_resource_lists()`, assert
  `MASK_LIST_CHANGED` fires and `metadata["mask_names"]` reflects the
  new name.
- Happy path: Three list events fire on each call, even if only one
  kind changed.
- Edge case: `refresh_resource_lists()` with no dataset loaded
  (`_dataset is None`) is a no-op — no events fire, no exception.

**Verification:**
- Method exists; tests pass; the event ordering is correct (list events
  fire before any caller invokes `set_active_*`).

---

### Phase 2 — Subscriber rebinds

- U4. **DataPanel: full repopulate of all combos on each list event**

**Goal:** Closes C2 and C3 for the Data tab. The list-event handlers
clear and re-list both Active combos (channel/seg/mask) and Layer
Management combos. The `_on_model_active_*_changed` handlers no longer
mutate the combo's items — they only set `currentText`.

**Requirements:** R1, R2.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/data_panel.py` —
  extend `_on_state_changed` to handle the three new flags. The
  channel-list flag calls the existing `_populate_channel_combo` (or
  a small refactor of it); the seg-list and mask-list flags call
  `refresh_active_combos` and `refresh_management_combos`. Strip the
  `addItem` side effect from `_on_model_active_*_changed`.
- Test: `tests/test_gui_workflows/test_data_panel_combo_refresh.py`
  (new).

**Approach:**
- The current `_on_state_changed` (`data_panel.py:164-172`) dispatches
  on `change.segmentation`, `change.mask`, `change.data`. Add
  `change.channel_list`, `change.segmentation_list`, `change.mask_list`
  branches.
- The list-event handlers do the full clear + re-list using the
  existing `refresh_active_combos` and `refresh_management_combos`
  methods (`data_panel.py:210-249`). Both already use `blockSignals`
  correctly.
- The existing `_on_model_active_seg_changed` /
  `_on_model_active_mask_changed` handlers (lines 188-206) currently
  do two things: (1) `addItem` if name is missing in combo, (2)
  `setCurrentText(name)`. After this unit, they only do (2). The
  list-event handler is responsible for (1).
- Specifically address C2's symptom: when name is `None`, the combo
  text must clear. Today (line 198: `if name:`) this branch is
  skipped, leaving stale text. Either invert the guard or always run
  the handler; pick whichever leaves the combo with `currentText("")`
  cleanly.
- `change.data` keeps refreshing the channel combo for backwards
  compatibility (other paths may still rely on it); the
  `change.channel_list` branch is the canonical refresh trigger.

**Patterns to follow:**
- Existing `blockSignals(True/False)` pattern around `setCurrentText`
  in the `_on_model_active_*_changed` handlers.
- `refresh_active_combos` and `refresh_management_combos` are already
  the right helpers; reuse, do not write parallel logic.

**Test scenarios:**
- Happy path: Covers AE3. With dataset loaded and combos populated,
  call `session.refresh_resource_lists()` after a fake mask-write to
  the store. Assert the Active Mask combo and Layer Management
  Masks dropdown contain the new mask name.
- Happy path: Covers AE2. Load dataset 1, set
  `active_mask = "SG_mask"`, then load dataset 2 (which also has
  `SG_mask`). Assert combo `currentText` matches the auto-selected
  mask from dataset 2 (per U2's auto-select), not the carryover.
  Assert Layer Management combos list dataset 2's resources, not
  dataset 1's.
- Edge case: Load dataset 2 with no masks at all. Active Mask combo
  is empty (no items, no currentText carryover from dataset 1).
- Edge case: Cellpose runs and writes a new segmentation. Active
  Segmentation combo gains the new entry, currentText becomes the
  new name (because Cellpose auto-selects per the Creator contract).
- Integration: After U5 wires Cellpose, the full path Cellpose → store
  write → `refresh_resource_lists()` → list event → DataPanel
  repopulate works end-to-end.

**Verification:**
- Tests pass. Manual repro of C2 (Dish_1 → Dish_2 with overlapping
  mask names) no longer reproduces.

---

- U5. **PhasorPlotWindow: invalidate caches and reset ROIs on `DATASET_CHANGED`; rebind on list events**

**Goal:** PhasorPlotWindow's `_active_mask_array`, `_active_mask_flat`,
G/S maps, intensity, ROI list, and per-ROI `cached_mask` all invalidate
on `DATASET_CHANGED`. ROIs reset (per OQ-1). The mask-filter checkbox
disables/un-checks if the new active mask doesn't exist or has a
shape mismatch with the new G/S maps.

**Requirements:** R1.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py` —
  add a `DATASET_CHANGED` handler (or extend the existing
  `_on_active_mask_changed`-style subscriber chain). Clear all
  per-dataset caches; clear ROI widgets via the existing
  `_on_remove_roi`-shaped path or a dedicated reset.
- Test: `tests/test_gui_workflows/test_phasor_dataset_switch.py` (new).

**Approach:**
- Subscribe to the new bridge flag for `DATASET_CHANGED` (the existing
  bridge already maps it; check `model.py`).
- Clear all per-dataset caches: `_active_mask_array`, `_active_mask_flat`,
  `_g_map`, `_s_map`, `_g_map_unfiltered`, `_s_map_unfiltered`,
  `_intensity`, `_labels`, `_labels_flat`, `_total_valid_pixels`,
  `_preview_colormap`, `_colormap_dirty`.
- Clear ROI widgets: iterate `_roi_widgets` and remove each from the
  plot; clear the list; clear `_selected_roi_index`; clear the Selected
  ROI panel widgets (already a subscriber-driven path post-U8 of the
  prior audit).
- Reset checkbox state: `_filtered_check.setChecked(False)`,
  `_filtered_check.setEnabled(False)`, `_mask_filter_check.setChecked(False)`,
  `_mask_filter_check.setEnabled(False)`. The existing `_on_active_mask_changed`
  path (lines 631-651) already handles the disable-on-None case;
  verify it's invoked correctly post-reset.
- This unit is the structural fix for C2's symptom #1 (checkbox
  unclickable). Combined with U2's auto-select and U4's combo
  repopulate, the full C2 repro is closed.

**Patterns to follow:**
- The existing `_on_active_mask_changed` cache invalidation
  (`phasor_plot.py:631-651`).
- The post-U8 (prior audit) ROI removal subscriber-driven panel
  reset.
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  Vector 4 (per-ROI cached_mask invalidation) — apply on dataset
  switch, not just on G/S change.

**Test scenarios:**
- Happy path: Covers AE2. Load dataset 1, compute phasor, add an
  ROI, engage `Filter by active mask`. Load dataset 2. Assert:
  - `_active_mask_array` is `None` (cache invalidated).
  - `_g_map` is `None`.
  - `_roi_widgets` is empty.
  - `_filtered_check` and `_mask_filter_check` are unchecked +
    disabled (or re-enabled when the new dataset's data is computed).
  - Selected ROI panel widgets are empty.
  - Status bar shows the no-data state.
- Edge case: `set_dataset(None)` (close dataset). All caches clear;
  ROIs gone; checkboxes unchecked + disabled.
- Integration: After U2 + U4 + U5 land, the C2 manual repro no longer
  reproduces — checkbox engages on first try.

**Verification:**
- Tests pass. Manual repro of C2 closes.

---

- U6. **DataPlotWindow + CellTableWindow: invalidate content caches on `DATASET_CHANGED`**

**Goal:** Two more peer views with cached state. Same shape as U5
but smaller in scope — these don't have ROIs to reset.

**Requirements:** R1.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/interfaces/gui/peer_views/data_plot.py` —
  invalidate scatter x/y arrays, label index, two-layer rendering
  state on `DATASET_CHANGED`.
- Modify: `src/percell4/interfaces/gui/peer_views/cell_table.py` —
  reset `PandasTableModel`'s df, the proxy's filter set, the label
  index, on `DATASET_CHANGED`.
- Test: `tests/test_gui_workflows/test_peer_views_dataset_switch.py`
  (new — covers both peer views).

**Approach:**
- Both peer views already subscribe to `Session.subscribe` events per
  `docs/audits/subscriber-rebind-matrix.md`. Add a `DATASET_CHANGED`
  handler if not present.
- DataPlot: clear `_x`, `_y`, `_labels` arrays; clear scatter items
  on the plot.
- CellTable: replace `PandasTableModel`'s df with an empty one; clear
  `FilterableProxyModel`'s filter; reset label index.
- Both: clear axis combo selections (if any) so the user gets a
  fresh-start UX.

**Patterns to follow:**
- U5's invalidation pattern.
- Existing `set_dataframe(...)` / `clear()`-shaped methods on each
  peer view (verify with grep).

**Test scenarios:**
- Happy path: Load dataset 1, scatter shows points. Load dataset 2.
  Assert DataPlot's `_x` / `_y` are empty, scatter is empty, table
  is empty.
- Edge case: `set_dataset(None)` — same outcome.

**Verification:**
- Tests pass. No regressions in existing peer-view tests.

---

### Phase 3 — Creator updates

- U7. **Wire `refresh_resource_lists()` into every Creator that writes a new resource**

**Goal:** Every Creator listed in the prior audit's `session-mutation-graph.md`
that writes a new channel/segmentation/mask now calls
`session.refresh_resource_lists()` between (1) writing the resource and
(2) calling `set_active_*`. Closes C3 / AE3 end-to-end.

**Requirements:** R2.

**Dependencies:** U3, U4, U5, U6.

**Files:**
- Modify: `src/percell4/application/use_cases/segment_cells.py` —
  after Cellpose write (~line 95).
- Modify: `src/percell4/application/use_cases/accept_threshold.py` —
  after threshold-mask write (~line 70).
- Modify: `src/percell4/gui/segmentation_panel.py` — Create Empty
  Labels (~line 335). See Deferred-to-Implementation re: in-memory only.
- Modify: `src/percell4/gui/threshold_qc.py` — final accept (~line 760).
- Modify: `src/percell4/gui/add_layer_dialog.py` — TIFF tab import
  (~line 637), cellpose-seg .npy import (~line 700), TCSPC append, ROI
  import paths (find via grep — there are 5 tabs per the prior audit's
  YAML).
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py` — the
  Apply Visible as Mask path (and the launcher's `Apply Phasor Mask`
  callback at `main_window.py:1083`).
- Modify: `src/percell4/interfaces/gui/task_panels/data_panel.py` —
  channel rename (~line 408-409), channel delete (~line 545-546).
  Per the prior audit's mutation graph, these are Creator-cleanup;
  they should fire `CHANNEL_LIST_CHANGED` after the rename/delete
  changes the inventory.
- Test: extend the per-creator existing tests where they exist (e.g.,
  `tests/test_application/test_segment_cells.py`,
  `tests/test_application/test_accept_threshold.py`); add new tests
  where they don't.

**Approach:**
- Each Creator's update is one new line: `session.refresh_resource_lists()`
  inserted between the write and the existing `set_active_*` call.
- For Creator paths that currently don't auto-select (the borderline
  cases from the prior audit — derived/auxiliary resource Creators
  like `compute_phasor_button`), this unit does not add an auto-select.
  But if the Creator writes a *session-selectable* resource (channel,
  seg, mask) and currently doesn't auto-select, this unit may need
  to either add the auto-select (to honor the Creator contract) or
  leave it. Decide per-Creator during implementation; defer the
  individual judgments to the implementer.
- Channel rename and channel delete: today they call
  `set_active_channel(new_name)` / `set_active_channel(None)`. After
  this unit they also call `refresh_resource_lists()` first. The
  ordering matters: list event → active event so subscribers see the
  new list before they look up the new active.

**Patterns to follow:**
- The existing pattern in each Creator (`set_active_*` after write).
- `docs/solutions/architecture-patterns/channel-deletion-permanence.md`'s
  7-step pattern for channel delete.

**Test scenarios:**
- Happy path: Covers AE3 + AE4. Each Creator-specific test asserts:
  - Resource exists in store after the operation.
  - The list event for the relevant kind fires exactly once.
  - The active-changed event fires after the list event.
  - The combo (in a real DataPanel under qtbot) shows the new entry.
- Edge case (Cellpose Create Empty Labels): if in-memory-only,
  decide whether the list event still fires or not. Test both
  behaviors and pick.
- Integration: full Cellpose → DataPanel combo refresh under
  `qtbot` end-to-end.
- Regression: existing per-Creator tests still pass.

**Verification:**
- Tests pass. Manual repro of AE3 (run Cellpose, see new segmentation
  in Data tab without restart) succeeds.

---

### Phase 4 — Tests and documentation

- U8. **Anchor regression tests — AE1, AE2, AE3, AE4**

**Goal:** Live regression coverage for the four origin acceptance
examples. Specifically the C2 dataset-switch sequence (the bug the
prior audit's regression tests didn't catch).

**Requirements:** R1, R2, R3.

**Dependencies:** U2, U4, U5, U7.

**Files:**
- Create: `tests/test_gui_workflows/test_dataset_switch_lifecycle.py`
  — covers AE1, AE2.
- Create: `tests/test_gui_workflows/test_creator_live_combo_refresh.py`
  — covers AE3, AE4 across Creator entry points.

**Execution note:** Test-first for the AE2 regression — it must fail
on `main` before U2/U4/U5 land and pass after.

**Approach:**
- Mirror the `viewer_harness` fixture style from
  `tests/test_gui_workflows/test_multi_select_e2e.py:43-69` (real
  Session + CellDataModel + ViewerWindow + a test dataset with
  layers).
- AE1: synthetic dataset with `[seg1, seg2]` and `[mask1]`; load it;
  assert all three Active combos show the right value.
- AE2: load dataset 1 with mask `SG_mask`; engage Filter by active
  mask; load dataset 2 also with `SG_mask` but different shape;
  assert checkbox is clickable, combo populated, phasor recomputable.
- AE3: load dataset; run a fake Cellpose write that calls
  `refresh_resource_lists()`; assert combo gains the new entry without
  reload.
- AE4: same as AE3 but assert auto-select to the new resource.

**Test scenarios:**
- Covers AE1: dataset load auto-selects all three.
- Covers AE2: dataset switch with overlapping mask names; checkbox
  re-engages; phasor recomputes.
- Covers AE3: Creator write → combo refresh.
- Covers AE4: Creator auto-select after write.
- Edge case: dataset with no segmentations (mask still auto-selects).
- Edge case: dataset with no masks (segmentation still auto-selects).

**Verification:**
- All four AEs are covered by named tests. Tests fail on `main`
  pre-U2/U4/U5/U7 and pass after.

---

- U9. **Update audit deliverables — subscriber-rebind matrix and mutation graph**

**Goal:** Keep the audit deliverables current with the new event surface.

**Requirements:** R4 (the prior audit's living artifacts must reflect
post-fix state).

**Dependencies:** U1, U2, U3, U4, U5, U6, U7.

**Files:**
- Modify: `docs/audits/subscriber-rebind-matrix.md` — add a column for
  list-event subscriptions; add new rows for any subscribers that
  need to respond to the list events.
- Modify: `docs/audits/session-mutation-graph.md` — add a "fires which
  list event" column for Creators; document `refresh_resource_lists()`
  as the new central API.
- Modify: `docs/audits/gui-element-classification.yaml` — no change
  expected unless a Creator's classification shifts; verify and update
  if needed.

**Approach:**
- Pure documentation pass after the code lands.
- Treat as the "audit refresh" for this fix.

**Patterns to follow:**
- The existing audit doc structure.

**Test scenarios:**
- Test expectation: none — documentation artifact.

**Verification:**
- Each existing subscriber and Creator listed in the audit docs is
  reviewed for the new list-event contract.
- The docs accurately describe the post-fix system.

---

## System-Wide Impact

- **Interaction graph.**
  - Three new Session events propagate through the bridge to
    `state_changed`. DataPanel and (optionally, in the future) other
    panels subscribe.
  - `refresh_resource_lists()` is a new central API that every Creator
    calls before `set_active_*`. The ordering contract (list event
    first, then active event) is part of the API design.
  - PhasorPlotWindow / DataPlotWindow / CellTableWindow add
    `DATASET_CHANGED` handlers for cache invalidation.
- **Error propagation.** No new error classes. `refresh_resource_lists()`
  with no dataset is a no-op (silent). Combo populators with empty
  inventories show empty combos (existing behavior).
- **State lifecycle risks.**
  - Risk: a Creator writes a resource but forgets to call
    `refresh_resource_lists()` — symptom is "C3 again, but for that
    one Creator." Mitigated by U9's audit-doc update making the
    contract visible, and by per-Creator regression tests in U8.
  - Risk: emit ordering is wrong (active event before list event) —
    subscribers look up the new active name in a stale list.
    Mitigated by the explicit ordering contract in `set_dataset`
    and `refresh_resource_lists()`.
- **API surface parity.** `Session` gets one new public method
  (`refresh_resource_lists`). `DatasetHandle.metadata` gets two new
  documented keys. `StateChange` gets three new boolean fields.
- **Integration coverage.** U8's regression tests are real-fixture
  e2e tests covering the full subscriber chain.
- **Unchanged invariants.**
  - Selector / Creator / Action taxonomy from the prior audit.
  - Invariants I1–I5 from the prior audit.
  - The single-`state_changed` signaling channel.
  - The Session ↔ CellDataModel bridge shape.
  - napari → session decoupling (closed in the prior audit).

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| A Creator path is missed in U7's enumeration; C3 still partially reproduces. | Cross-reference U7's file list against `docs/audits/session-mutation-graph.md`'s Creator inventory; U8 includes a per-Creator integration test that fails if `refresh_resource_lists()` isn't called. |
| `DatasetHandle(...)` is constructed at multiple sites (loader, importer, in tests) and not all populate the new metadata keys. | Grep for every instantiation in U2; add the keys at each. Test U2 covers missing-keys defensive path (treated as empty lists). |
| `refresh_resource_lists()` fires three events even when only one kind changed, producing redundant combo work. | Acceptable starting point; combo populators are cheap (clear + re-list of small lists). Narrow to specific kinds via a `kinds=` parameter only if profiling shows real cost. |
| Auto-select-on-load (R3) surprises users who expect the prior session's selection to carry over. | The audit's default-state rule explicitly says auto-select; the user has confirmed this preference (origin C1). No mitigation needed. |
| ROI reset on dataset load (OQ-1) is too aggressive — user wanted ROIs to persist. | Default decision is reset; reversible later. The plan flags this as an OQ resolution, not a structural commitment. |
| The existing transition-only `ACTIVE_*_CHANGED` emission rule (`session.py:158-165`) doesn't fire when prev was None and new is the auto-selected name; downstream subscribers don't update. | U2 explicitly addresses this — the emission logic broadens to cover the None → non-None transition. Test scenario covers this case. |
| Channel rename / delete now firing `CHANNEL_LIST_CHANGED` introduces extra events that downstream subscribers may not expect. | Both already fire `ACTIVE_CHANNEL_CHANGED` today; adding the list event is additive. No subscriber should regress. Verify with `tests/test_gui_workflows/`. |

---

## Documentation / Operational Notes

- After this lands, update `CLAUDE.md`'s GUI state ownership section to
  mention the three list events alongside the existing five selection
  fields. (Defer to U9 or a follow-up touch-up commit.)
- No rollout/migration concerns — this is a refactor + bug fix with
  regression tests.
- No user-data implications.

---

## Sources & References

- **Origin document:** `docs/brainstorms/2026-05-01-dataset-lifecycle-and-resource-list-events-requirements.md`
- **Prior audit:**
  `docs/brainstorms/2026-05-01-gui-state-handling-audit-requirements.md`
  +
  `docs/plans/2026-05-01-refactor-gui-state-handling-audit-plan.md`
- **Audit deliverables:**
  `docs/audits/gui-element-classification.yaml`,
  `docs/audits/session-mutation-graph.md`,
  `docs/audits/subscriber-rebind-matrix.md`,
  `docs/audits/keystroke-binding-audit.md`
- **Related code:**
  - `src/percell4/application/session.py`
  - `src/percell4/model.py`
  - `src/percell4/domain/dataset.py`
  - `src/percell4/store.py`
  - `src/percell4/adapters/hdf5_store.py`
  - `src/percell4/interfaces/gui/task_panels/data_panel.py`
  - `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
  - `src/percell4/interfaces/gui/peer_views/data_plot.py`
  - `src/percell4/interfaces/gui/peer_views/cell_table.py`
- **Related learnings:**
  - `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`
  - `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  - `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`
  - `docs/solutions/architecture-patterns/channel-deletion-permanence.md`
