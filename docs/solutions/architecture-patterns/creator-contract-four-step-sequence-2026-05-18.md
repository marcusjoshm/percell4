---
title: "Creator contract is a four-step sequence — store, viewer, refresh, set_active"
date: 2026-05-18
last_updated: 2026-05-25
category: architecture-patterns
module: percell4.interfaces.gui, percell4.gui, percell4.application.use_cases
problem_type: architecture_pattern
component: tooling
canonical_source: src/percell4/gui/threshold_qc.py
applies_to:
  - "src/percell4/gui/**/*.py"
  - "src/percell4/interfaces/gui/**/*.py"
  - "src/percell4/application/use_cases/accept_*.py"
  - "src/percell4/application/use_cases/segment_cells.py"
  - "src/percell4/application/use_cases/track_cells.py"
duplicates_at: []
status: canonical
tags:
  - gui
  - state-ownership
  - creator-contract
  - selector-creator-action
  - audit
  - mask-write
  - napari-layer-add
  - classification-drift
related_components: [gui, application, viewer]
symptoms:
  - "New mask/segmentation appears in SessionWindow combo but the napari viewer is empty until dataset reload."
  - "Layer appears in napari and combo, but session.active_<resource> stays empty/None — downstream readers report \"X '' not found in viewer\"."
  - "Mask written to /masks/<name> but no UI surface shows it."
  - "Auto-selection 'works' but combos haven't refreshed yet, leading to flicker or wrong-name lookups."
  - "Segmentation persisted to HDF5 but SessionWindow's segmentation combo never lists it until the dataset is closed and reopened."
---

# Creator contract is a four-step sequence

> **Status: canonical.** Companion to `gui-action-contract-exhaustiveness.md` (Selector / Creator / Action classification). This doc pins what a **Creator** specifically must do. Promoted from `pre_canonical` to `canonical` on 2026-05-25 after a third independent witness (Bug C) landed cleanly — the contract is now grep-checkable across three distinct skipped steps with concrete fixes in place.

## Rule

Every code path that creates a new dataset resource (mask, segmentation, channel, decay, phasor map, …) must run **all four** of these steps:

| Step | Call                                            | What it does                           |
|------|-------------------------------------------------|----------------------------------------|
| 1    | `store.write_<resource>(name, data)`            | Persist to HDF5                        |
| 2    | `viewer_win.add_<resource>(data, name=name)`    | Add the napari layer                   |
| 3    | `session.refresh_resource_lists(...)`           | Refresh SessionWindow combos           |
| 4    | `session.set_active_<resource>(name)`           | Auto-select the new resource           |

Step 1 must precede step 3 (store-before-list invariant — observers should not see the active fire on a name that isn't in their list). Step 2 should precede step 4 when the session→napari push relies on the layer existing (the push is a *selector* for existing layers, not a creator).

Skipping any single step leaves the system in a different visibly-broken state — see Failure modes below.

## Split-responsibility variant (use-case-encapsulated Creator)

When the Creator is encapsulated as a Qt-free use case under `src/percell4/application/use_cases/accept_*.py`:

- **The use case** owns steps 1, 3, 4. It takes a `DatasetRepository` port and a `Session`, NOT a viewer port. This keeps it testable, Qt-free, and makes `grep -rn "session\.set_active_" src/percell4/application/use_cases/` find every auto-select callsite.
- **The caller** (panel, dialog, or workflow controller) owns step 2. It calls `viewer_win.add_<resource>(data, name=name)` after `use_case.execute(...)` returns.

The viewer add does NOT live inside the use case. Two reasons: (a) Qt coupling would force every use-case test to spin up a ViewerPort; (b) the audit grep for `session.set_active_` would have noise from viewer-imports that aren't about the canonical state mutation.

## Failure modes (which step you skipped → what the user sees)

| Skipped step | Symptom                                                                                       |
|--------------|------------------------------------------------------------------------------------------------|
| 1            | No persistence. Usually caught fast — the resource literally doesn't exist on the next session. |
| **2**        | **Mask/segmentation in /masks/<name> AND in the SessionWindow combo, but napari viewer empty until dataset reload.** (This is the `dilute_phase` bug — commit `f2cb964`, 2026-05-18.) |
| **3**        | **Resource persisted to HDF5 (and napari layer present, and `session.active_<resource>` set), but SessionWindow's combo never lists the new name until the dataset is closed and reopened.** Subscribers to `*_LIST_CHANGED` (combos, peer-view caches) only refresh when `refresh_resource_lists` fires — skipping step 3 silently desyncs the inventory from the store. (This is Bug C — `_on_create_empty_labels` "manual" segmentation — commit `1386d831`, 2026-05-25.) |
| **4**        | **Layer in napari, combo refreshed, but `session.active_<resource>` stays at prior value — downstream readers that do `self._session.active_* or ""` get empty string and raise "X '' not found in viewer".** (This is Cycle 5's `Add Layer → Grouped Threshold` bug — commit `bef67b0`, 2026-05-15.) |

Each step is silent if skipped — no error, just a state-divergent UI. That's why the contract must be grep-checkable.

## Detection

Mechanical greps to verify the contract holds across the codebase:

```bash
# Every Qt-free Creator auto-select callsite (steps 1, 3, 4).
grep -rn "session\.set_active_\|data_model\.set_active_" \
  src/percell4/application/use_cases/

# Every viewer-side step-2 callsite (panel-owned, caller's responsibility).
grep -rn "viewer_win\.add_mask\|viewer_win\.add_labels\|viewer_win\.add_image" \
  src/percell4/

# Every store write that should be a Creator's step 1.
grep -rn "store\.write_mask\|store\.write_labels\|repo\.write_mask\|repo\.write_labels" \
  src/percell4/
```

When a new `accept_<resource>.py` use case is added, every Qt-aware caller of it must also call `viewer_win.add_<resource>(...)` after `.execute()`. Likewise, every inline-Creator path (no use case) must have all four steps in the same function.

## Established correct callsites

| File | Steps owned | Notes |
|------|-------------|-------|
| `src/percell4/gui/threshold_qc.py::_finalize` | **All 4 inline** | The reference implementation; the four calls are colocated in the finalize path. |
| `src/percell4/gui/add_layer_dialog.py::_write_layer` | **All 4 inline** | Cycle 5's fix site (the `set_active_*` call was missing here). Channel branch deliberately skips step 4 — adding a new channel does NOT yank focus from the user's active channel. |
| `src/percell4/gui/segmentation_panel.py::_on_create_empty_labels` | **All 4 inline** | Bug C's fix site (step 3 was missing). The handler creates an in-memory napari Labels layer, persists it to HDF5, then reads `store.list_labels() − store.list_masks()` and calls `session.refresh_resource_lists(segmentation_names=...)`. |
| `src/percell4/application/use_cases/segment_cells.py::SegmentCells.finalize` (line 209) | **Split**: SegmentCells = 1+3+4, caller = 2 | The canonical pattern for split-responsibility Creators. Computes `seg_names = list_labels() − list_masks()` before refreshing. The fix for Bug C mirrors this exact derivation. |
| `src/percell4/application/use_cases/track_cells.py::TrackCells` (line 87) | **Split**: TrackCells = 1+3+4, caller = 2 | Second use-case-encapsulated Creator using the same `list_labels() − list_masks()` derivation. |
| `src/percell4/interfaces/gui/task_panels/analysis_panel.py:494-508` | **Split**: AcceptThreshold = 1+3+4, panel = 2 | First codified split site. The panel calls `viewer_win.add_mask(...)` after the use case returns. |
| `src/percell4/gui/workflows/dilute_phase/controller.py::finish` | **Split**: AcceptDiluteMask = 1+3+4, controller = 2 | Bug B's fix site. The controller calls `self._viewer_win.add_mask(dilute_mask.astype(np.uint8), name=self._final_mask_name)` after the use case returns. |

## Examples — the three recent bugs as evidence

### Bug A — Cycle 5 (commit bef67b0, 2026-05-15): step 4 skipped

`AddLayerDialog._write_layer` ran steps 1, 2, 3 for the Segmentation and Mask branches but did not call `session.set_active_segmentation` / `session.set_active_mask`. The layer appeared in napari, the SessionWindow combo updated, but `session.active_segmentation` stayed `None`. `GroupedSegPanel._on_run` read `self._session.active_segmentation or ""` and raised `Segmentation '' not found in viewer`.

Fix: add the missing `set_active_segmentation` / `set_active_mask` calls. Pinned by `tests/test_gui/test_add_layer_write_layer_sets_active.py`.

### Bug B — Dilute phase (commit f2cb964, 2026-05-18): step 2 skipped

`DilutePhaseMaskController.finish()` called `AcceptDiluteMask.execute(...)` which ran steps 1, 3, 4 cleanly. But the controller did not follow up with `viewer_win.add_mask(...)`, so napari never received the layer. The mask was in `/masks/dilute_phase` and selected on the session, but the viewer was empty until dataset reload.

Fix: add `self._viewer_win.add_mask(dilute_mask.astype(np.uint8), name=self._final_mask_name)` after the use case returns. Pinned by `tests/test_gui/test_dilute_phase_controller.py::test_finish_calls_viewer_add_mask_so_layer_appears_in_napari`.

### Bug C — Create Empty Labels (commit 1386d831, 2026-05-25): step 3 skipped

`SegmentationPanel._on_create_empty_labels` ran step 1 (persisted "manual" to HDF5 via `_persist_labels_layer`), step 2 (added the labels to napari), and step 4 (called `data_model.set_active_segmentation("manual")`) — but skipped step 3. SessionWindow's segmentation combo subscribes only to `Event.SEGMENTATION_LIST_CHANGED`, and that event is emitted exclusively by `Session.refresh_resource_lists`. Without it, `dataset.metadata["segmentation_names"]` stayed stale and the combo never listed "manual" until the user closed and reopened the dataset. The HDF5 group existed, the napari layer was visible, and `session.active_segmentation` correctly held `"manual"` — but the inventory was silently desynced from the store. (session history)

Fix: after `_persist_labels_layer(new_layer)` returns truthy, read `store.list_labels() − store.list_masks()` (the same derivation `SegmentCells.finalize` uses) and call `self.data_model.session.refresh_resource_lists(segmentation_names=...)`. Pinned by `tests/test_gui/test_segmentation_panel_create_empty_refresh.py::test_create_empty_labels_calls_refresh_resource_lists` and `::test_create_empty_labels_updates_dataset_metadata`.

#### Why Bug C slipped through the original audit (session history)

The May 1 GUI-state audit (U7 of `feat/learnings-retrieval-automation`, commit `d0b1b2fe`) explicitly wired `refresh_resource_lists` into 6 Creator entry points and deliberately excluded `_on_create_empty_labels`. The U7 commit message rationalized the exclusion: *"In-memory-only Creators (Create Empty Labels) stay as-is — DataPanel's `_on_model_active_*_changed` gained an addItem fallback in U4 that covers the in-memory case without requiring a list event."* At that point the handler genuinely was in-memory-only.

Six days later (commit `7b803969`, 2026-05-07) added `_persist_labels_layer` for manual-edit autosave. The same commit wired `_on_create_empty_labels` to call `_persist_labels_layer` immediately so the `/labels/manual` group would exist in HDF5 even if the user closed the dataset before painting anything. That side effect **silently promoted the handler from an in-memory Creator to an HDF5-writing Creator** — but the Creator-contract audit was not revisited, and `docs/audits/gui-element-classification.yaml` still records the handler at its pre-autosave line number. The classification drift is the root cause; the missing `refresh_resource_lists` call is the visible symptom.

## When to apply

- Adding any new code path that writes a mask, segmentation, channel, decay, phasor map, or other dataset resource.
- Adding a new `accept_<resource>.py` use case under `src/percell4/application/use_cases/` (every Qt-aware caller must own step 2).
- Reviewing a PR that touches `store.write_*`, `viewer_win.add_*`, `session.refresh_resource_lists`, or `session.set_active_*`.
- Investigating any bug where a resource exists in storage or session but doesn't render, OR renders but isn't selected.

## Prevention

- **Test convention**: when introducing a new Creator path (inline or use-case-split), the regression test must assert all four steps. For the split variant, assert `viewer_win.add_<resource>.call_count == 1` in the caller's success-path test specifically. The Bug-A / Bug-B / Bug-C regression tests above are templates — Bug C's test subscribes a counter to `Event.SEGMENTATION_LIST_CHANGED` and asserts it fired exactly once, which is the strongest signal for step 3 compliance.
- **Code review checklist**: when reviewing a `src/percell4/application/use_cases/accept_*.py` file, verify the caller adds the layer to the viewer. The use case alone is not enough.
- **Asymmetric carve-out**: channels and other "ancillary" resources may legitimately skip step 4 (adding a channel doesn't yank focus from the active channel). Pin the asymmetry in the test (see `test_write_layer_channel_does_not_set_active_channel`).
- **Classification drift is a thing.** A Creator that is correctly classified as in-memory-only today can be silently promoted to HDF5-writing by a future commit that adds persistence as a side effect (this is exactly how Bug C slipped through — see "Why Bug C slipped through" above). Treat any commit that adds `store.write_<resource>` to an existing handler — even as part of an autosave or "persist immediately" addition — as a Creator-contract change. Re-run the Creator-contract checklist for that handler and update `docs/audits/gui-element-classification.yaml`.
- **Backfill test coverage for every `*_LIST_CHANGED` event**, not just the active-change events. Bug C survived because `test_session_window.py` covered `MASK_LIST_CHANGED` and `CHANNEL_LIST_CHANGED` refresh but had no equivalent test for `SEGMENTATION_LIST_CHANGED`. The asymmetric coverage was the silent enabler.
- **Mechanical derivation for `seg_names`**: when calling `session.refresh_resource_lists(segmentation_names=...)` from a Creator that writes labels, compute `seg_names = list_labels() − list_masks()` (set difference). `SegmentCells.finalize`, `TrackCells`, and `_on_create_empty_labels` all use this exact derivation — masks under `/masks/` can shadow names with labels under `/labels/` in legacy datasets, so the filter is load-bearing.

## Related

- `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md` — companion: the three-class Selector/Creator/Action classification and the Action canonical example. Read first for the broader rule.
- `docs/solutions/ui-bugs/add-mask-name-collision-image-layer-crash-2026-05-15.md` — adjacent: name validation before reaching step 1.
- `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md` — adjacent: store-before-viewer (step 1 before step 2) for type-classification correctness.
- `docs/audits/gui-element-classification.yaml` — full inventory of Selectors / Creators / Actions.
- `docs/audits/session-mutation-graph.md` — every writer of the five session fields.
