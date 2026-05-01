---
date: 2026-05-01
topic: dataset-lifecycle-and-resource-list-events
status: requirements
follows: docs/brainstorms/2026-05-01-gui-state-handling-audit-requirements.md
---

# Dataset Lifecycle and Resource-List Events — Requirements

## Problem Frame

Three reproducible bugs share a root cause: PerCell4's session emits events
when the *selection* of a channel / segmentation / mask changes, but not when
the *underlying list* of available resources changes. Combo populators and
peer-view caches are wired to selection events, so they go stale whenever a
Creator writes a new resource or a new dataset is loaded with overlapping
names.

### Anchor bugs

- **C1 — `set_dataset` only auto-selects the first channel.** Active
  Segmentation and Active Mask are nulled (`session.py:143-144`). The audit's
  default-state rule (origin requirements doc) says all three should
  auto-select. Today only channel does.
- **C2 — `Filter by active mask` checkbox becomes unclickable after loading a
  second dataset whose mask name happens to match the first.** Repro: load
  Dish_1, set channel/seg/mask, compute phasor, engage `Filter by active
  mask` → load Dish_2 (which also contains a layer named `SG_mask`), compute
  phasor → checkbox refuses to engage; Active Mask combo shows `SG_mask`
  while the Layer Management dropdown is empty (image #15). The session has
  cleared its slots; the UI state and PhasorPlotWindow caches are stale.
- **C3 — New layers from Creators (Cellpose, threshold accept, add-layer
  imports, ROI-to-mask) don't appear in Data-tab combos until app restart.**
  No live event fires when a new resource is written into the dataset.

### Common structural cause

`Session` (`src/percell4/application/session.py:19-28`) emits seven events
today, all keyed off *selection* changes or dataset/measurement updates.
There is no event for "the list of available channels, segmentations, or
masks just changed within the current dataset." Subscribers that need to
re-list (Data-tab combos, Layer Management dropdowns, anything reading
`handle.metadata`) cannot react to mid-session list mutations.

The damage is not just C3: it compounds with C2, because the Data-tab
combo populator runs on `DATASET_CHANGED` but the combo's stale
`currentText` survives if the new dataset has overlapping names. And it
explains the broader observation that "new layers don't show up until I
restart" — every Creator's write-side fires the right `ACTIVE_*_CHANGED`
event when it auto-selects, but no event tells the combo populator to
re-enumerate.

## Goals

- Loading a new dataset is a coherent fresh start: every combo, every cache
  in every open peer view, and every active selection reflects the new
  dataset's actual state. No stale UI, no unclickable controls.
- Creators that add a new resource immediately make it visible in the Data
  tab combos and the Layer Management dropdowns, without app restart.
- The default-state rule from the prior audit holds in code: dataset load
  auto-selects first available channel, segmentation, and mask.
- The Selector / Creator / Action taxonomy and Invariants I1–I5 from the
  prior audit are preserved unchanged.

## Non-goals

- Adding new Selectors or per-module dropdowns (still deferred per OQ-3 of
  the prior audit).
- Restructuring the `Session` ↔ `CellDataModel` bridge.
- Persisting phasor ROIs across dataset loads. ROIs are spatial regions on
  a specific dataset's phasor space; carrying them across loads has no
  user-validated semantics. Resetting on load is the assumed default and
  flagged as an Open Question for planning to confirm.
- Refactoring the `metadata` shape on `DatasetHandle`.
- Performance / UI-responsiveness of the combo refresh.

## Anchor scenarios (acceptance examples)

- **AE1 — Auto-select on load.** Load `Dish_1_WT_As_60min.h5`. Active Channel,
  Active Segmentation, and Active Mask combos all show the first available
  resource (e.g., `mNG`, `cellpose`, `SG_mask`). The user does not have to
  manually pick segmentation or mask before computing phasor.
- **AE2 — Clean slate on dataset switch.** With `Dish_1` loaded and a phasor
  computed with `Filter by active mask` engaged, load `Dish_2_TAOK2_KO_As_60min.h5`.
  Phasor window stays open. After loading: Active Channel/Segmentation/Mask
  combos show `Dish_2`'s first resources; Layer Management dropdowns list
  `Dish_2`'s resources; phasor window's `Filter by active mask` checkbox is
  unchecked-and-clickable; computing phasor + re-engaging the checkbox works
  on the first try without restart.
- **AE3 — Live combo refresh on Creator write.** With a dataset loaded,
  run Cellpose. The new segmentation appears in the Active Segmentation
  combo and the Layer Management Segmentations dropdown immediately,
  without dataset reload or restart. Same for: threshold accept (creates
  mask), add-layer flows (creates channel/segmentation/mask), ROI-to-mask
  save (creates mask).
- **AE4 — Creator auto-select.** When a Creator writes a new resource, the
  newly written resource is auto-selected per the prior audit's Creator
  contract. AE3's combo refresh and the auto-select are coherent: the user
  sees the new entry appear AND become active.

## Domain rules (extending the prior audit)

### Rule R1 — Default-state rule, fully implemented

`Session.set_dataset(handle)` auto-selects:

- The first entry in `handle.metadata["channel_names"]` (already implemented).
- The first entry in `handle.metadata["segmentation_names"]` (or equivalent
  inventory) — **new**.
- The first entry in `handle.metadata["mask_names"]` (or equivalent
  inventory) — **new**.

If a list is empty or missing, the corresponding active slot is `None`.

### Rule R2 — Resource-list event

A new Session event family fires whenever the list of available resources
changes within the current dataset (additions or removals). This is
distinct from selection events:

- **Selection events** (existing): `ACTIVE_CHANNEL_CHANGED`,
  `ACTIVE_SEGMENTATION_CHANGED`, `ACTIVE_MASK_CHANGED`. Fired when *which
  one is selected* changes.
- **List events** (new): fired when *which ones exist* changes.

Whether this is one event with a payload (kind=channel|segmentation|mask)
or three separate events is a planning-time decision. The product
contract is: subscribers can re-enumerate the resource list on a
fine-grained signal.

### Rule R3 — Dataset reload is a coherent fresh start

`set_dataset` triggers (in this conceptual order):

1. Clear all session slots: `_active_*`, `_filter_ids`, `_selection`,
   `_measurements`. (Already does this.)
2. Apply R1 (auto-select first available of each kind).
3. Emit `DATASET_CHANGED`.
4. Emit per-slot `ACTIVE_*_CHANGED` for slots whose value transitioned
   (today: only emitted for slots whose previous value was non-None;
   this needs to also fire when the previous value was None and the
   new value is the auto-selected one — see Rule R1).
5. Emit the new resource-list event(s) per Rule R2 with the new
   inventory.
6. Open peer-view windows reset their internal caches (`_active_mask_array`,
   `_active_mask_flat`, G/S maps, ROI lists, scatter coordinates, table
   data) on `DATASET_CHANGED`. Already a subscriber rule from the prior
   audit's `subscriber-rebind-matrix.md`; the gap is implementation.

### Rule R4 — Creator emission contract (extension of OQ-2)

Every Creator that writes a new channel, segmentation, or mask:

1. Writes the resource to disk (existing).
2. Refreshes the in-memory dataset inventory.
3. Emits the new resource-list event for that kind (Rule R2).
4. Auto-selects the new resource via the appropriate `set_active_*`,
   which emits the existing `ACTIVE_*_CHANGED` event.

Order matters: list event first, then active-changed. Subscribers that
populate combos must list before any subscribers that look up the
just-written name in the (now-current) list.

### Rule R5 — Subscriber rebind contract (no carryover from prior dataset)

Any widget displaying dataset-derived state must rebind on
`DATASET_CHANGED`. Specifically:

- Combo populators clear their items and re-list from the new dataset's
  metadata. They do not retain `currentText` from the previous dataset.
- Peer-view caches (`PhasorPlotWindow._active_mask_array` /
  `_active_mask_flat`, ROI cached masks, G/S maps; `DataPlotWindow`'s
  scatter arrays; `CellTableWindow`'s pandas df) are explicitly invalidated.
- Phasor ROIs are cleared (see Open Question OQ-1).
- Open peer-view windows stay open; only their content resets.

## Scope boundaries

**In scope.**
- `src/percell4/application/session.py` — Event enum, `set_dataset`,
  per-slot setters as needed for R4.
- `src/percell4/interfaces/gui/task_panels/data_panel.py` — combo
  populator subscription and content reset.
- `src/percell4/interfaces/gui/peer_views/phasor_plot.py` — cache
  invalidation on `DATASET_CHANGED` (and ROI list reset per OQ-1).
- `src/percell4/interfaces/gui/peer_views/data_plot.py`,
  `cell_table.py` — same cache-invalidation pass.
- Every Creator entry point that writes a new resource: Cellpose,
  threshold accept, add-layer (TIFF / cellpose-seg / ROI / TCSPC tabs),
  channel rename, channel delete, ROI-to-mask. These need to fire
  the new list event (R4).
- `Session` constructor and `clear()` — for symmetry.
- Tests under `tests/test_application/` for session events; tests under
  `tests/test_gui_workflows/` for combo refresh and dataset-switch
  behavior.

**Deferred for later.**
- Per-module Selector dropdowns (still OQ-3 of the prior audit).
- A "recent datasets" persistence layer.
- Multi-dataset comparison mode.

**Outside this initiative's identity.**
- Storage-layer changes to `DatasetHandle.metadata` shape.
- napari → session coupling (closed in the prior audit; not reopened).
- The Selector / Creator / Action taxonomy itself (inherited).

## Open questions

- **OQ-1 — Phasor ROI persistence across dataset loads.** Default
  assumption: ROIs reset when a new dataset is loaded (a fresh phasor on
  a new dataset is a new context). Confirm with the user during planning;
  if the answer is "preserve ROIs," the requirements expand to include
  ROI re-validation against the new G/S coordinate range.
- **OQ-2 — Single resource-list event with payload, or three separate
  events?** Implementation choice; defer to planning. The product
  contract (R2) is the same either way.
- **OQ-3 — `metadata` inventory key names for segmentation and mask
  lists.** Confirm whether `DatasetHandle.metadata` exposes
  `segmentation_names` / `mask_names` keys today, or whether the
  inventory needs to be derived from the HDF5 store at session-update
  time. Verified during planning.
- **OQ-4 — `clear()` and resource-list event.** Should
  `Session.clear()` (called on dataset close) emit an empty resource-list
  event so combos clear cleanly? Default yes.

## Dependencies / assumptions

- The prior audit's invariants I1–I5 hold. Selectors and Creators remain
  the only writers of `session.active_*` / `filter_ids` / `selection`.
- `CellDataModel` continues to bridge Session events to Qt's
  `state_changed`. New events are bridged the same way (per
  `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`'s
  5-step rule).
- `DataPanel`'s combo populator is the canonical Selector pattern (per
  `docs/audits/gui-element-classification.yaml`). Its `blockSignals(True/False)`
  guard around `setCurrentText` is preserved.

## Success criteria

- AE1, AE2, AE3, AE4 all work.
- A regression test under `tests/test_gui_workflows/` reproduces the C2
  dataset-switch sequence and asserts the phasor checkbox is clickable
  after switching to a second dataset with overlapping mask names.
- A regression test asserts AE3 — running a Creator (e.g., a fake threshold
  accept) refreshes the Data-tab Active-Mask combo without needing a
  dataset reload.
- `docs/audits/subscriber-rebind-matrix.md` is updated to add a
  "DATASET_CHANGED rebinds" column for every subscriber.
- `docs/audits/session-mutation-graph.md` is updated to list every
  Creator that fires the new resource-list event.

## Next step

Hand off to `/ce-plan` to sequence the implementation. The chain is
already lined up: this requirements doc → planning doc → execution.
