---
title: "Phasor window auto-load skipped on dataset switch when channel name is unchanged"
date: 2026-05-06
category: ui-bugs
module: percell4.interfaces.gui.peer_views, percell4.application
problem_type: ui_bug
component: tooling
symptoms:
  - "After loading a new dataset, the phasor window stayed empty (status: 'No phasor computed') even though the new dataset's HDF5 already contained cached phasor and wavelet maps."
  - "User had to click 'Compute Phasor' and then 'Apply Wavelet Filter' on every dataset switch to re-populate the window."
  - "Reproduced only when the new and previous dataset's first channel name matched (the common case in microscopy: 'ch1', 'ch2', or repeating biological channel names like 'mNG'/'CA-SiR')."
  - "Loading a dataset whose first channel name differed from the previous one auto-populated correctly, hiding the bug from quick testing."
  - "Auto-load on initial app start (showEvent) and on intra-dataset channel switches (ACTIVE_CHANNEL_CHANGED) both worked — only dataset-to-dataset switches with shared channel names regressed."
root_cause: missing_workflow_step
resolution_type: code_fix
severity: medium
related_components: [phasor, session, lifecycle, hdf5-cache, peer-view]
tags:
  - phasor
  - dataset-switch
  - auto-load
  - active-channel-changed
  - event-suppression
  - lifecycle
  - peer-view
  - subscriber-rebind
applies_to:
  - src/percell4/interfaces/gui/peer_views/phasor_plot.py
canonical_source: src/percell4/interfaces/gui/peer_views/phasor_plot.py
related_learnings:
  - docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md
  - docs/solutions/architecture-decisions/session-bridge-event-forwarding.md
---

# Phasor window auto-load skipped on dataset switch when channel name is unchanged

## Problem

Switching between two datasets that share an active channel name left the
phasor window stuck on the previous dataset's view — the new dataset's
HDF5-cached phasor and wavelet maps did not auto-populate. The user was
forced to click "Compute Phasor" and then "Apply Wavelet Filter" on every
dataset switch, even though both caches already existed in the new
dataset's `.h5` file.

## Symptoms

- Phasor window status reverted to "No phasor computed" after every
  `set_dataset` even when the new dataset had complete cached phasor +
  wavelet data.
- 2D histogram, ROI overlays, and the wavelet-filtered display were all
  empty.
- Reproduced only when `prev_active_channel == new_active_channel` by
  string equality. Different channel names worked correctly because
  `ACTIVE_CHANNEL_CHANGED` did fire.
- The bug was silent: no error, no warning, just a UI that needed two
  manual clicks per dataset switch.

## What Didn't Work

**Initial hypothesis** (rejected): no auto-load mechanism existed at all
in the phasor window's dataset-switch path, so the fix would require a
new `DATASET_CHANGED` subscriber in
[`src/percell4/interfaces/gui/task_panels/flim_panel.py`](../../../src/percell4/interfaces/gui/task_panels/flim_panel.py)
that loaded cached phasor data via the existing
`load_cached_phasor` use case.

**Why it was wrong**: a closer read of
[`src/percell4/interfaces/gui/peer_views/phasor_plot.py`](../../../src/percell4/interfaces/gui/peer_views/phasor_plot.py)
revealed that `_try_auto_load_cached` already existed (line 1937) and was
already wired to two triggers — `showEvent` (line 1911) and
`_on_active_channel_changed` (line 1925). The infrastructure was complete;
only one wiring edge was missing in `_on_dataset_changed` (line 1508).

**Lesson**: when a feature appears absent, search for the existing
primitive (`grep` for plausible function names like `_try_auto_load`,
`_load_cached`, `_auto_*`) before designing a new subsystem. The bug was
a wiring gap, not missing infrastructure — and the wrong hypothesis would
have introduced a duplicate auto-load path in a second window, doubling
the surface area for the same suppression problem.

## Solution

One explicit call added at the end of `_on_dataset_changed` in
`src/percell4/interfaces/gui/peer_views/phasor_plot.py` (commit
`0237a2a`):

```python
def _on_dataset_changed(self) -> None:
    # ... clear _g_map, _s_map, ROIs, histogram, status ...

    # Re-derive checkbox state from current session.active_mask. (Existing
    # mirror-handler call for the same Session-emit suppression case
    # applied to the mask-filter checkbox.)
    self._on_active_mask_changed()

    # Same suppression pattern for ACTIVE_CHANNEL_CHANGED: when the
    # two datasets share a channel name (typical in microscopy —
    # every dataset has "ch1"/"ch2"/... or biological channel names
    # that repeat), Session.set_dataset omits the channel-changed
    # emit, so the existing _on_active_channel_changed auto-load
    # path never fires. Trigger the cache-load directly here so the
    # new dataset's HDF5-cached phasor (and wavelet, if present)
    # lands without forcing the user to click Compute Phasor and
    # Apply Wavelet Filter on every dataset switch.
    self._try_auto_load_cached()
```

**Test coverage** (`tests/test_gui_workflows/test_phasor_window_auto_load.py`):

1. `test_dataset_switch_same_channel_name_auto_loads_new_cache` — the bug
   case. Asserts that after `Session.set_dataset` to a dataset with
   cached phasor + wavelet under the same channel name, the phasor
   window's `_g_map`, `_s_map`, and `_g_map_unfiltered` populate without
   any user click.
2. `test_dataset_switch_different_channel_name_still_auto_loads` —
   idempotency. Confirms the explicit `_try_auto_load_cached()` call plus
   the existing `ACTIVE_CHANNEL_CHANGED` handler don't double-load.
3. `test_dataset_switch_no_cache_leaves_window_empty` — no-op
   verification. Confirms the explicit call is safe when the new dataset
   has no cached phasor: window stays empty (`_g_map is None`) and the
   status remains "No phasor computed".

## Why This Works

The root cause is an **event suppression pattern** in `Session.set_dataset`
(`src/percell4/application/session.py:129`, emit block at lines 162-175).
After `DATASET_CHANGED`, the per-slot `ACTIVE_*_CHANGED` events emit only
when the slot value actually transitioned:

```python
# session.py:162-175
self._emit(Event.DATASET_CHANGED)
self._emit(Event.CHANNEL_LIST_CHANGED)
self._emit(Event.SEGMENTATION_LIST_CHANGED)
self._emit(Event.MASK_LIST_CHANGED)
if prev_channel != self._active_channel:
    self._emit(Event.ACTIVE_CHANNEL_CHANGED)
if prev_segmentation != self._active_segmentation:
    self._emit(Event.ACTIVE_SEGMENTATION_CHANGED)
if prev_mask != self._active_mask:
    self._emit(Event.ACTIVE_MASK_CHANGED)
```

When both datasets carry a channel literally named `ch1`, the comparison
is equal-by-value and the event is suppressed — even though the
*underlying data* behind that name is entirely different.

`PhasorPlotWindow` had two auto-load triggers, both blocked in this case:

- `showEvent` (`phasor_plot.py:1911`) only fires when the window
  transitions to visible. Across a dataset switch the phasor window
  typically stays open, so this never re-fires.
- `_on_active_channel_changed` (`phasor_plot.py:1925`) is the per-channel
  auto-load — gated on the very event that gets suppressed.

`_on_dataset_changed` (`phasor_plot.py:1508`) did fire on every switch
and cleared per-dataset caches, but never re-populated.

The fix mirrors an **existing precedent in the same handler** at
`phasor_plot.py:1572-1578`, where `_on_dataset_changed` already calls
`self._on_active_mask_changed()` explicitly to handle the same
equal-by-name suppression for the mask-filter checkbox state. The
original author had encountered and solved this pattern for masks but
didn't propagate it to the phasor auto-load path. Adding
`self._try_auto_load_cached()` at the end of `_on_dataset_changed` is
the structurally identical mitigation: when the upstream emit is
suppressed, the dataset-switch handler invokes the per-slot handler's
effect directly.

## Prevention

**Recurrence rule**: any GUI handler that subscribes to
`ACTIVE_CHANNEL_CHANGED`, `ACTIVE_SEGMENTATION_CHANGED`,
`ACTIVE_MASK_CHANGED`, or any other `ACTIVE_*_CHANGED` event for refresh
side-effects (loading cached data, repainting, syncing widget state)
must also be invoked from the same window's `_on_dataset_changed` (or
its equivalent `DATASET_CHANGED` subscriber). `Session.set_dataset`
suppresses per-slot events whenever the slot value is equal-by-name
across datasets — the *common case* in microscopy data, not a corner
case.

**Code-review checklist line**:

> For every `subscribe(Event.ACTIVE_*_CHANGED, handler)` in a window,
> confirm the same window's `_on_dataset_changed` calls `handler()`
> explicitly at the end. `Session.set_dataset` suppresses
> `ACTIVE_*_CHANGED` when the slot's identifier is equal-by-name across
> datasets — a downstream handler that depended on the event for
> refresh logic will silently no-op.

**Audit grep**: in any peer view or task panel under
`src/percell4/interfaces/gui/`, find every
`subscribe(Event.ACTIVE_*_CHANGED, ...)` call and verify the linked
handler is also called from the same file's `_on_dataset_changed` (or
`DATASET_CHANGED` subscriber):

```bash
# Find ACTIVE_*_CHANGED subscriptions
grep -rn "Event\.ACTIVE_.*_CHANGED" src/percell4/interfaces/gui/

# For each result, open the file and verify _on_dataset_changed
# explicitly calls the linked handler.
```

**Parallel precedent in PRs**: cite the mask-checkbox handling at
`src/percell4/interfaces/gui/peer_views/phasor_plot.py:1572-1578`. New
auto-load / refresh logic in any window should follow the same template
— `_on_dataset_changed` ends by explicitly invoking every
`ACTIVE_*_CHANGED` handler whose side-effect is dataset-relevant.

**Architectural alternative considered, not chosen**: change
`Session.set_dataset` to always emit `ACTIVE_*_CHANGED` after
`DATASET_CHANGED`, since semantically a dataset switch invalidates the
*binding* of every active-slot identifier even when the identifier
string is unchanged. This would remove the suppression-pattern footgun
globally but risks redundant handler invocations in subscribers that
have spent effort optimizing for the equal-by-name no-op. Tracked as a
future-cleanup candidate.

## Related learnings

- [`logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`](../logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md)
  — complementary pair. Multi-vector doc handles "stale state from
  previous dataset wasn't cleared on switch"; this doc handles "fresh
  state for new dataset wasn't loaded on switch." Together they bracket
  the `_on_dataset_changed` lifecycle: every cache-invalidation step
  has a mirror auto-load step.
- [`architecture-decisions/session-bridge-event-forwarding.md`](../architecture-decisions/session-bridge-event-forwarding.md)
  — codifies forwarding and per-slot emission rules for Session events.
  Its Prevention rules currently cover (a) "new Session events must be
  bridged" and (b) "Session.set_* methods that clear multiple slots
  must emit per-slot events." A natural third rule from this learning:
  "`Session.set_*` no-op-equality suppression of per-slot events can
  silently break downstream side-effect chains in subscribers — handle
  via explicit re-invocation in `_on_dataset_changed`."
