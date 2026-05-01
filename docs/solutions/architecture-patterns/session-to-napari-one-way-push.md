---
title: "Session → napari is a one-way controlled push; napari → session for layer-list events is forbidden"
date: 2026-05-01
category: architecture-patterns
module: percell4.gui.viewer, percell4.interfaces.gui.main_window
problem_type: architecture_pattern
component: tooling
canonical_source: src/percell4/gui/viewer.py
applies_to:
  - "src/percell4/gui/viewer.py"
  - "src/percell4/interfaces/gui/main_window.py"
duplicates_at: []
status: pre_canonical
tags:
  - napari
  - session
  - state-ownership
  - layer-selection
  - is-originator
  - re-entrancy
related_components: [gui, application]
symptoms:
  - "Clicking a layer in napari's layer list silently rewrites session.active_mask / active_segmentation."
  - "Subscribers downstream (panels, status bar, peer views) read divergent stale slices because napari-side jitter writes session at unpredictable times."
  - "An unrelated button (e.g., Clear Selection) accidentally enables a feature (e.g., Multi-select) by flipping napari's active layer."
---

# Session → napari one-way push

> **Status: pre_canonical.** Single canonical site today (`ViewerWindow._on_state_changed`); promote after one more session-coupled napari surface adopts the same pattern.

## Rule

**Forbidden:** any subscription to `viewer.layers.selection.events.*` that writes `session.active_*`. napari's event loop must not write PerCell4 domain state.

**Allowed:** PerCell4-controlled writes *to* napari (`viewer.layers.selection.active = layer`), driven by session changes and guarded by an `_is_originator`-style re-entrancy flag.

**Allowed:** napari **canvas mouse callbacks** that write `session.selection` when the user clicks a cell. The canvas mouse callback IS the user genuinely picking a cell; this is not the napari event loop reflecting layer-list state. Selectors for `session.selection` live here (`gui/viewer.py`'s canvas-click forwarding chain).

## Canonical example — `_on_state_changed` push

`src/percell4/gui/viewer.py`:

`ViewerWindow._on_state_changed` (subscribed to `data_model.state_changed`) dispatches on `StateChange` flags. Branches for `change.mask` and `change.segmentation` call `_push_active_layer_to_napari(name, expected_type)`, which:

1. Reads the new active name from session.
2. Calls `_find_layer_by_name_and_type(name, expected_type)` — strict matching on `name + metadata["percell_type"]`.
3. If found: sets `viewer.layers.selection.active = layer` while `_is_originator = True`.
4. If not found: silent no-op (debug-logged); does not crash.

Use a strict helper, not the existing `_get_active_labels_layer` — that has segmentation-specific fallback semantics ("first non-mask Labels layer") that would silently mis-resolve a missing-mask push to whatever segmentation layer happens to be present.

## Re-entrancy

`_is_originator` flips True before the napari write and back to False after. Any napari event subscriber that runs during the write (none should, since `viewer.layers.selection.events.active` no longer subscribes to anything that writes session) sees the flag and bails.

## Anti-pattern (the Bug B context)

Pre-fix `interfaces/gui/main_window.py:594-605` connected the `events.active` closure to `_sync_active_layers_from_viewer`, which read layer metadata and called `set_active_mask` / `set_active_segmentation`. Side effects:

- napari-side jitter (any layer-list selection change) rewrote PerCell4 domain state.
- An Action button that incidentally flipped napari's active layer (e.g., Clear Selection switching `SG_mask` → `cellpose`) cascaded into `set_active_mask`/`set_active_segmentation` writes.
- Bug B's "Clear Selection enables M" symptom was the user noticing this: clearing the napari active label happened to flip the active layer, which fired the sync, which re-derived state in a way that allowed Multi-select to open.

The closure stayed connected for legitimate side effects (seg-panel and grouped-seg-panel channel-label updates); only the `_sync_active_layers_from_viewer()` call inside it was removed.

## Detection

```bash
grep -rn "viewer\.layers\.selection\.events" src/percell4/
```

Every hit must NOT reach `session.set_active_*`. Cross-check against `docs/audits/session-mutation-graph.md`, which lists every writer of the five session selection fields.

## When to apply

- Adding a new napari event subscription.
- Adding a new session subscriber that wants to push state into napari.
- Reviewing a PR that touches `viewer.py` or any `events.active` / `events.connected` callback.

## Related

- `docs/audits/session-mutation-graph.md` — every writer of the five session fields.
- `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md` — the `_is_originator` re-entrancy convention.
- `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md` — companion (Action contracts).
- `docs/solutions/architecture-patterns/keystroke-binding-on-napari-viewer.md` — companion (keystroke routing).
