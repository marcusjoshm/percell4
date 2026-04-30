---
title: "Multi-vector in-session staleness after HDF5 writes (Qt + h5py)"
date: 2026-04-30
category: logic-errors
module: percell4.application, percell4.gui, percell4.interfaces.gui.peer_views
problem_type: logic_error
component: tooling
symptoms:
  - "Compute Phasor produced wrong (g, s) values in-session but became correct after app restart on the same disk file."
  - "User had to run Compute Phasor multiple times before fresh calibration finally took effect."
  - "Phasor plot 'Filter by active mask' looked broken in-session — broad smear of ~75k pixels along the universal semicircle — but produced a tight ~86k cluster after restart."
  - "Peer views (mask overlay, segmentation badge) kept showing the previous dataset's state after a dataset switch."
  - "Toggling 'Filtered' (wavelet) on the phasor plot displayed an old wavelet cloud that didn't match the freshly recomputed (g, s)."
root_cause: scope_issue
resolution_type: code_fix
severity: high
related_components: [flim, phasor, hdf5, gui, session]
tags:
  - hdf5
  - h5py
  - staleness
  - cache-invalidation
  - in-session-state
  - phasor
  - flim
  - session
  - snapshot
  - derived-layers
---

# Multi-vector in-session staleness after HDF5 writes (Qt + h5py)

## Problem

After an in-session write that mutates the experiment file (here: TCSPC import writing new `/decay/<ch>` and `/metadata` calibration), downstream reads can return stale values through **multiple independent caching layers stacked on top of each other**. Fixing one layer reveals the next. The user experiences a debugging journey where every "fix" appears to *almost* solve it — and the only thing that consistently makes the symptom disappear is restarting the app, because process restart resets all five layers simultaneously.

This bug surfaced visibly in the percell4 phasor plot after we shipped a "Filter by active mask" feature: the masked phasor looked broken in-session and only became correct after restart on the same disk file. The masked view made stale data visually obvious in a way the unmasked view had been hiding for at least a week.

## Symptoms (round by round)

1. **Round 1.** First `compute_phasor` after TCSPC import produced phasor coordinates as if calibration was never applied. Re-running it sometimes helped, sometimes not.
2. **Round 2.** Switching active dataset left peer views (mask overlay, active-segmentation indicator) showing the previous dataset's state until a manual refresh.
3. **Round 3.** "I had to run Compute Phasor multiple times before the calibration applied." Same process, same file — values eventually caught up.
4. **Round 4.** After enabling "Filter by active mask", the phasor restricted to a known coherent ROI looked **broader** than the unmasked phasor — physically impossible. Pixel counts were inconsistent across `Filtered=ON/OFF` toggles.
5. **Round 5.** App restart on the **same `.h5`** produced the correct tight masked cluster (~86k pixels). In-session pipeline produced the broad ~75k smear (Image #3 in the bug report). Disk state was identical between the two flows; the difference lived entirely in process memory and in *derived* on-disk arrays that were never invalidated.

## What Didn't Work

Each of these fixes was *necessary*; none was *sufficient* on its own.

- **Mask-filter feature alone.** The new opt-in checkbox, AND-composition helper (`compute_valid_phasor_pixels`), and lazy mask-flat loading were all correct — but assumed the inputs (`g`, `s`, mask) were fresh. They weren't.
- **Per-state event emission alone (vector 1).** Fixed peer-view drift on dataset switch but left use cases reading stale calibration.
- **Fresh `read_metadata` port alone (vector 2).** `compute_phasor` / `apply_wavelet` / `compute_lifetime` could now bypass the frozen `handle.metadata` snapshot, but the user still saw "had to compute multiple times" because of the next vector.
- **In-place metadata mutation alone (vector 3).** Fixed the H5 library-cache problem but left the in-memory mask-flat cache (vector 4) and on-disk derived layers (vector 5) able to produce wrong visuals.
- **Mask-cache invalidation alone (vector 4).** Forced a fresh mask-flat read but left the stale wavelet output displayed when the user toggled "Filtered=ON".
- **A single workflow validation that spawned a fresh subprocess.** The April 27 calibration round-trip was verified by `python -c "..."` in a child process — every subprocess starts with a fresh `handle.metadata`, fresh h5py library state, and no in-memory caches. This is exactly the "works after restart" pattern, and it masked the in-session staleness for a week. (session history)

## Solution — the 5-vector fix chain

### Vector 1 — `Session.set_dataset` must emit per-state events

`Session.set_dataset` cleared `_active_mask`, `_active_segmentation`, `_filter_ids`, `_selection` but emitted only `DATASET_CHANGED`. Peer views subscribing to `ACTIVE_MASK_CHANGED` (the phasor plot's mask filter) never got the reset.

```python
# src/percell4/application/session.py
def set_dataset(self, handle: DatasetHandle | None) -> None:
    prev_segmentation = self._active_segmentation
    prev_mask = self._active_mask
    prev_filter = self._filter_ids
    prev_selection = self._selection

    self._dataset = handle
    self._active_segmentation = None
    self._active_mask = None
    self._selection = frozenset()
    self._filter_ids = None
    ...
    self._emit(Event.DATASET_CHANGED)
    if prev_segmentation is not None:
        self._emit(Event.ACTIVE_SEGMENTATION_CHANGED)
    if prev_mask is not None:
        self._emit(Event.ACTIVE_MASK_CHANGED)
    if prev_filter is not None:
        self._emit(Event.FILTER_CHANGED)
    if prev_selection:
        self._emit(Event.SELECTION_CHANGED)
```

This is a second occurrence of the rule from [`session-bridge-event-forwarding.md`](../architecture-decisions/session-bridge-event-forwarding.md): coarse `DATASET_CHANGED` is not a substitute for per-slot events when peer views subscribe to specific slots.

### Vector 2 — `read_metadata(handle)` port for fresh disk reads

`DatasetHandle` is frozen and `handle.metadata` is a dict snapshot taken at `set_dataset` time. Use cases (`compute_phasor`, `apply_wavelet`, `compute_lifetime`) read calibration via `handle.metadata.get(...)` → got pre-import defaults (`cal_phase=0.0`, `cal_mod=1.0`).

```python
# src/percell4/ports/dataset_repository.py — new port method
def read_metadata(self, handle: DatasetHandle) -> dict[str, Any]:
    """Read /metadata attrs FRESH from disk. handle.metadata is a snapshot."""
    ...

# src/percell4/application/use_cases/compute_phasor.py — caller side
def _read_fresh_metadata(self, handle) -> dict:
    reader = getattr(self._repo, "read_metadata", None)
    if reader is not None:
        try:
            return reader(handle)
        except Exception:
            logger.warning(...)
    return dict(handle.metadata)  # fallback for older test stubs

meta = self._read_fresh_metadata(handle)
cal_phase = float(meta.get(f"flim_cal_phase_{channel}", 0.0))
cal_mod = float(meta.get(f"flim_cal_mod_{channel}", 1.0))
```

### Vector 3 — In-place metadata mutation to bypass HDF5's process cache

Even with the fresh-read port, the user still reported "had to run Compute Phasor multiple times". h5py / HDF5 maintain a per-process metadata cache; a new `h5py.File(path, "r")` handle opened *after* a previous handle wrote-then-closed in the same process can serve a stale view. (session history confirms vector 3 has no precedent in prior session logs — it's novel to this writeup.)

The fix bypasses the disk cache entirely by mutating the in-memory snapshot with the values the dialog already has:

```python
# src/percell4/gui/add_layer_dialog.py — _tcspc_persist_flim_metadata
self._store.set_metadata(attrs)  # write to disk
# DatasetHandle is frozen, but handle.metadata is a mutable dict.
handle = self._data_model.session.dataset
if handle is not None:
    handle.metadata.update(attrs)  # known-good values from spinboxes
```

### Vector 4 — Invalidate in-memory mask-flat cache on phasor refresh

`PhasorPlotWindow.set_phasor_data` invalidated the ROI cache but not `_active_mask_array` / `_active_mask_flat`. Each `compute_phasor` produces a new `(g, s)` frame; the cached mask flat could be misaligned even when shapes match (rotation, flip, channel/dataset switch).

```python
# src/percell4/interfaces/gui/peer_views/phasor_plot.py — set_phasor_data
for w in self._roi_widgets:
    w.cached_mask = None
# New: also invalidate active-mask filter cache
self._active_mask_array = None
self._active_mask_flat = None
```

### Vector 5 — Invalidate derived on-disk layers on phasor recompute

`compute_phasor` rewrote `/phasor/<ch>/g` and `/s` but left `g_filtered`, `s_filtered`, `lifetime_filtered` from a *previous* `apply_wavelet` run untouched — those had been computed from uncalibrated `(g, s)` *before* vector 2's fix took effect. Toggling "Filtered=ON" displayed the stale wavelet cloud filtered by the mask, producing the broad ~75k-pixel smear (Image #3).

```python
# src/percell4/application/use_cases/compute_phasor.py — after writing g, s
for stale in ("g_filtered", "s_filtered", "lifetime_filtered"):
    deleter = getattr(self._repo, "delete_path", None)
    if deleter is not None:
        deleter(handle, f"phasor/{channel}/{stale}")
```

A `delete_path(handle, path)` method was added to the `DatasetRepository` port and `Hdf5DatasetRepository` (delegating to existing `DatasetStore.delete_item`).

## Why This Works — the named pattern

**"After in-session writes, multiple staleness vectors hide each other."** A single read path can pass through 4–5 caching layers between bytes-on-disk and pixels-on-screen:

1. **Frozen domain handles** holding metadata snapshots from open-time
2. **HDF5 library's per-process metadata cache** across file handles
3. **In-memory derived caches** in the session/store (mask flat arrays, phasor maps)
4. **On-disk *derived* datasets** computed from now-stale inputs (wavelet output)
5. **Qt-signal-driven UI caches** on peer windows that receive only coarse events

Fixing the outermost layer makes the bug retreat one step inward, where it manifests differently and looks like a *new* bug. Each vector has a plausible alternative explanation (race condition, async signal, file corruption, GPU caching) that diverts attention. The cure is to enumerate every layer between disk and screen and invalidate or refresh each one explicitly at the write boundary.

The reason this took five rounds: each fix made the symptom *mutate* rather than *vanish*. "Had to run it multiple times" is the canonical signature of a stacked-cache bug, not a flaky test.

## Prevention

1. **Enumerate cache layers at every in-session write site.** Before merging code that mutates the `.h5` mid-session, list every cache that touches the written keys: handle snapshots, HDF5 library cache, session-level numpy caches, derived on-disk datasets, peer-view UI caches. Invalidate or refresh each one in the same commit as the write.

2. **Treat `DatasetHandle.metadata` as a snapshot, never as live state.** Any use case that reads metadata after potential in-session writes must go through `DatasetRepository.read_metadata(handle)`. Lint rule: `grep handle.metadata.get src/percell4/application/use_cases/` should return zero matches; flag any new occurrence in code review.

3. **When a primary dataset is written, invalidate its derived datasets in the same function.** `compute_phasor` writes `(g, s)` → must delete `(g_filtered, s_filtered, lifetime_filtered)`. Keep an explicit derivation map per group so this is mechanical, not from-memory. This generalizes the rule already documented in [`flim-phasor-cross-layer-alignment-2026-04-29.md`](flim-phasor-cross-layer-alignment-2026-04-29.md) Prevention #2 ("invalidate stale `/phasor/<ch>` when `/decay/<ch>` is rewritten") to all primary→derived relationships.

4. **`Session.set_*` methods that change input semantics must invalidate dependent caches.** `set_phasor_data` invalidates ROI cache *and* mask-flat cache. `set_active_mask` invalidates phasor pixel-validity cache. Treat session caches as a coherent set — when one input changes, list every cache derived from it.

5. **Per-state events on every state slot.** Any `Session.set_*` that clears multiple slots must emit per-slot events, not just a coarse `DATASET_CHANGED`. Peer views subscribe to specific slots; coarse events leave them stale. (Second occurrence of this rule — see [`session-bridge-event-forwarding.md`](../architecture-decisions/session-bridge-event-forwarding.md).)

6. **For HDF5 in particular, prefer in-place mutation of in-memory dicts after disk writes when the values are known.** Don't rely on a re-open to clear the library cache in the same process. If the dialog/use case knows the values it just wrote, push them into `session.dataset.metadata` directly — `DatasetHandle` is frozen but the dict is mutable.

7. **Add a "post-write read-back" smoke test** for any new write path: write → read via use case → assert value matches. This would have caught vectors 2 and 3 before user testing.

8. **Validate in-session, not via subprocess.** Subprocess smoke tests start with fresh handles, fresh h5py state, and no in-memory caches — they cannot detect any of vectors 1–5. Tests that write to `.h5` and then immediately read should run **in the same process**. (session history: the April 27 calibration round-trip was validated only via `python -c`, which is exactly why this entire bug class slipped through.)

9. **When a "fix" only reduces symptom frequency rather than eliminating it, suspect a second vector underneath.** "Had to run it multiple times" is not a flaky test — it is the canonical signature of a stacked-cache bug.

## Related Issues

- [`flim-phasor-cross-layer-alignment-2026-04-29.md`](flim-phasor-cross-layer-alignment-2026-04-29.md) — sibling axis on the same pipeline. That doc covers per-pixel alignment between `/intensity[ch_idx]` and `/decay/<ch>` (consumer-side derivation). Its Prevention rule #2 (invalidate stale `/phasor` when `/decay` is rewritten) is now **one of the five vectors** documented here. The two docs are complementary: cross-layer alignment is "where reads should source from"; this doc is "what to invalidate after writes."
- [`session-bridge-event-forwarding.md`](../architecture-decisions/session-bridge-event-forwarding.md) — precedent for vector 1. The rule there ("new Session events must be forwarded as `StateChange`") now has a second occurrence: `Session.set_dataset` clearing per-state slots must emit per-state events, not just `DATASET_CHANGED`. Worth promoting to "all `Session.set_*` mutations must emit per-state events."
- [`percell4-flim-phasor-troubleshooting.md`](../ui-bugs/percell4-flim-phasor-troubleshooting.md) — foundational FLIM pipeline correctness work. The math from that doc is correct; this doc explains why correct math can still produce wrong-looking output when the *inputs* are stale. (session history: an April 23 BOE wavelet session reported "BOE filter looks unfiltered, levels do nothing" — concluded at the time to be inherent algorithm mildness, but in retrospect that symptom shape matches vector 5 and may have been an earlier brush with this class.)
- [`napari-mask-layer-misclassified-as-segmentation.md`](../ui-bugs/napari-mask-layer-misclassified-as-segmentation.md) — Item 6 ("Stale HDF5 data") is a micro-precedent for vector 5 from a different module.
- [`docs/brainstorms/2026-04-30-phasor-mask-filter-requirements.md`](../../brainstorms/2026-04-30-phasor-mask-filter-requirements.md) — feature requirements for the mask-filter checkbox that exposed this bug class. The feature itself is straightforward; the staleness chain it surfaced is the durable learning.
