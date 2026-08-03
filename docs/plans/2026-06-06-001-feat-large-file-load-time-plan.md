---
title: "feat: Fast load for large .h5 datasets (lazy-first + parallel decode)"
type: feat
status: superseded
date: 2026-06-06
origin: docs/brainstorms/2026-06-06-large-file-load-time-requirements.md
---

> **⚠️ SUPERSEDED — this plan's diagnosis was wrong; do not implement it.**
> Real-app profiling (2026-06-07) showed the load bottleneck was **not** the
> decode this plan targets. It was `DataPanel.refresh_dataset_info()` reading
> the full 12.6GB intensity array ~4× per load *just to display its shape*
> (~160s), plus a double `_populate` call. The lazy-first work below (U1–U4)
> was implemented and **reverted**.
>
> **What actually shipped** (~250s → ~20s, ~12×): (1) `DatasetStore.array_shape`
> metadata-only shape read; (2) single-decode guard on the load path;
> (3) `adapters/parallel_decode.py` multiprocessing + shared-memory decode for
> the eager display read (5.3×). See
> `docs/solutions/logic-errors/large-file-load-metadata-read-full-decode-2026-06-07.md`.
> The rest of this document is retained only as a record of the explored-and-rejected approach.

# feat: Fast load for large .h5 datasets (lazy-first + parallel decode)

## Overview

Opening large stitched `.h5` files takes minutes. Profiling proved the cost is **single-threaded
gzip decompression of ~19 GB** done synchronously on the Qt main thread before the viewer shows
anything — not disk, not RAM, not the contrast scan. This plan makes the GUI load **lazy-first**
(decode only timepoint 0, show it in ~1–2s) and fills the remaining timepoints into a resident
buffer in the background — first via a single worker (Phase 1), then **parallelized across cores**
(Phase 2, measured ~4.6× → full residency in ~12s). Phase 3 changes the exporter to a
faster-decoding codec so new files are born fast, keeping **float32** (NaN support is required).

The displayed/interaction behavior after load is unchanged: data is fully resident, scrubbing and
analysis work exactly as today.

---

## Problem Frame

See origin: `docs/brainstorms/2026-06-06-large-file-load-time-requirements.md`.

Trigger class: `(36, 2, 6686, 6567)` float32 intensity (~12.6 GB) + `(36, 6686, 6567)` int32
labels (~6.3 GB), 3.89 GB gzip on disk. Measured: ~57–90s pure decode; threads give **zero**
speedup (HDF5 serializes); multiprocessing gives **~4.6×**; disk and RAM are not factors (user
confirmed warm == cold; 68 GB RAM).

**Critical path fact:** the GUI "Load Dataset…" button uses
`interfaces/gui/main_window.py::_populate_viewer_from_store` (synchronous, main thread) — **not**
`LoadDataset.execute` / `build_view`. All optimization targets the GUI path.

---

## Requirements Trace

- R1. (FR1) Viewer usable on timepoint 0 within ~1–2s of "Load Dataset…", off the main thread, with visible progress.
- R2. (FR2) Remaining timepoints fill resident arrays in the background; after fill, behavior == today; not-yet-filled frames resolve correctly on demand.
- R3. (FR2/L2) Background decode parallelized across cores via worker processes writing into shared memory (no bulk IPC).
- R4. (FR3) Contrast limits computed nan-aware from timepoint 0 and passed explicitly; no full-stack scan.
- R5. (FR4/L4) New exports use a faster-decoding codec, dtype stays float32, existing gzip files remain readable.
- R6. No regression in images, overlays, measurements, selection/filtering, mask editing; NaN-bearing data renders identically.

**Origin actors:** single user (microscopy researcher) — no multi-actor concerns.

---

## Scope Boundaries

- Not changing the napari pan/zoom/hotkey interaction (already fast).
- Not introducing multiscale / zoom pyramids (rejected in origin).
- Not converting intensity to uint16 (rejected — breaks NaN and float features).
- Not changing FLIM/decay load behavior.

### Deferred to Follow-Up Work

- Migrating existing gzip files to the new codec: separate one-off tool / re-export, not this plan.
- Unifying the GUI load path onto `LoadDataset.execute` / `build_view`: separate refactor; this plan optimizes the GUI path in place. (The redundant `.astype(np.float32)` in `adapters/hdf5_store.py::build_view` is in that unused-by-GUI path and is left alone here.)

---

## Context & Research

### Relevant Code and Patterns

- GUI load path: `interfaces/gui/main_window.py::_load_h5_into_viewer` → `::_populate_viewer_from_store` (the synchronous full-`read_array` site to rework).
- Viewer API: `gui/viewer.py::ViewerWindow` — `add_image` (computes nan-aware contrast over the full array unless `contrast_limits` passed), `add_labels`, `add_mask`, `clear`. Layer `.data` reassignment + in-place mutation is already done at `gui/viewer.py::add_staged_overlay`.
- Timepoint Selector: `gui/viewer.py` dims `current_step` ↔ `session.set_active_timepoint`, guarded by `_timepoint_originator` (see `src/percell4/gui/CLAUDE.md`).
- Per-frame read: `store.py::read_array_frame` slices one timepoint efficiently (chunks `(1,1,256,256)`).
- Worker pattern: `gui/workers.py::Worker(QThread)` with `finished(object)`/`progress(str)`/`error(WorkerError)`; caller must hold a reference. Canonical use: `gui/segmentation_panel.py`.
- Codec knob: `store.py::_compression_kwargs` / `::_choose_chunks` — single source of truth; whole-file writes go through `DatasetStore.create_atomic`.
- Interactive reads of layer `.data`: `main_window.py::_get_active_seg_labels`; GUI Cellpose reads active intensity layer `.data` in `gui/segmentation_panel.py`.

### Institutional Learnings

- `docs/solutions/architecture-patterns/session-to-napari-one-way-push.md` — programmatic layer mutation must not trip layer-list subscribers into writing `session.active_*`; wrap pushes under the originator guard.
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md` — the resident buffer is a new cache vector; bind invalidation to a single event (dataset switch / write boundary); never validate via a fresh subprocess.
- `docs/solutions/architecture-patterns/decay-write-path.md` + `atomic-write-contract.md` — route codec changes through `_compression_kwargs`/`_choose_chunks`; write whole files via `create_atomic`; do not hardcode `compression=` at new sites.
- `docs/solutions/runtime-errors/multi-channel-dataset-load-numpy-array-truth-value-2026-05-22.md` — on the load path, never use `x or default` on a possible numpy array; normalize h5py attrs at the read boundary; test single- AND multi-channel.
- `gui/workers.py` docstring: "no HDF5/GUI from the worker thread." This plan **deliberately departs** — background decode reads HDF5 off the main thread; each worker/process opens its **own** handle (h5py handles are not shareable across threads/processes). Documented as an explicit exception.

### External References

- `multiprocessing.shared_memory.SharedMemory` (stdlib) for the resident buffer; `concurrent.futures.ProcessPoolExecutor` for the pool (spawn start method on macOS — decode function must be a top-level, importable callable).
- Codec for L4 (Phase 3): `hdf5plugin` (Blosc/Blosc2 with lz4/zstd + bitshuffle) or built-in `lzf`. Decision deferred to U6.

---

## Key Technical Decisions

- **Optimize the GUI path in place** (`_populate_viewer_from_store`), not unify onto the use case — lowest risk for a perf change (see origin).
- **Allocate the full-shape resident buffer up front** (per-channel `(T,H,W)` intensity + `(T,H,W)` labels) so napari's dims range is correct immediately; **fill frames in place** rather than swapping a `(1,H,W)`→`(T,H,W)` array (avoids churning the dims/timepoint Selector). Confirmed viable: napari supports in-place `.data` mutation + `refresh()`.
- **Back the buffer with `shared_memory` from the start** (Phase 1), even though Phase 1 fills it with a single QThread. This keeps the buffer type stable so Phase 2 only swaps the *fill strategy* (serial → process pool) without re-architecting the layer backing.
- **Correctness via a per-frame `ready` flag + on-demand decode**: scrubbing to a not-yet-filled frame decodes that frame synchronously (~1.5s) and refreshes. Background fill only reduces how often that happens. This guarantees correct data regardless of background progress.
- **Contrast from timepoint 0**, nan-aware, passed explicitly to `add_image` (required because the full stack isn't decoded at display time — also eliminates the scan).
- **Buffer lifecycle bound to dataset switch / viewer clear**: allocate on load, `close()`+`unlink()` the shared memory on dataset switch or window close — single invalidation point.

---

## Open Questions

### Resolved During Planning

- Where to optimize? → GUI path `_populate_viewer_from_store` in place.
- Swap vs in-place fill? → in-place into a pre-allocated full-shape buffer.
- How to keep correctness during background fill? → `ready` flags + synchronous on-demand decode on scrub.
- Thread vs process for parallel decode? → process pool (threads measured at 0× speedup).

### Deferred to Implementation

- Exact `SharedMemory` sizing/segmentation for multi-channel intensity (one block vs per-channel blocks) — settle when wiring U1 against real multi-channel fixtures.
- Whether to pre-empt flicker on scrub-to-unready (fill-before-paint) or accept a brief blank then fill — measure during U3.
- L4 codec choice (Blosc2+zstd+bitshuffle via `hdf5plugin` vs `lzf`) and whether to keep `shuffle` — benchmark in U6.
- Optimal pool worker count (measured best ≈ 8 on a 10-core machine) — make it `min(os.cpu_count()-2, T)` and confirm.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
"Load Dataset…" (main thread)
  └─ _populate_viewer_from_store
       1. read metadata (T, C, H, W, channel_names, n_timepoints)   # normalized at read boundary
       2. ResidentBuffer.allocate(shape, dtype)        # shared_memory, ready[t]=False
       3. decode frame 0 -> buffer[:,0]; ready[0]=True # ~1s  (read_array_frame)
       4. contrast = nan-aware (lo,hi) from frame 0
       5. viewer.add_image(buffer_view[ch], contrast_limits=contrast)   # full dims, frame 0 shown
          viewer.add_labels(labels_buffer)             # under originator guard
       6. start BackgroundFiller(buffer, frames=1..T-1)  # Phase 1: 1 QThread; Phase 2: process pool
            └─ per frame done -> Qt signal -> main thread: ready[t]=True; refresh if current

dims.current_step -> t:                                # existing Selector, +new pre-paint hook
   if not ready[t]: decode frame t synchronously -> buffer; ready[t]=True; refresh

dataset switch / viewer.clear():
   BackgroundFiller.cancel(); ResidentBuffer.close()+unlink()   # single invalidation point
```

Phase 2 replaces step-6's filler internals only:

```
BackgroundFiller (Phase 2): ProcessPoolExecutor(max_workers=N)
  submit decode_frame_into_shm(shm_name, shape, dtype, path, t) for t in frames
  each process: opens its OWN h5py handle, decodes timepoint t, writes buffer[...,t,...]
  as_completed -> emit frame_done(t)   # data already in shared memory; only the index is marshaled
```

---

## Implementation Units

### Phase 1 — Lazy-first display (the headline win: usable in ~1–2s)

- U1. **Resident shared-memory buffer + frame-decode primitive**

**Goal:** A reusable abstraction that allocates a full-shape shared-memory buffer for intensity
(per channel) and labels, exposes numpy views, tracks per-frame `ready` state, decodes a single
timepoint into the buffer, and cleans up deterministically.

**Requirements:** R1, R2, R3 (enables)

**Dependencies:** None

**Files:**
- Create: `src/percell4/adapters/parallel_loader.py`
- Test: `tests/test_adapters/test_parallel_loader.py`

**Approach:**
- `ResidentBuffer`: allocates `SharedMemory` sized for `(C,T,H,W)` float32 intensity + `(T,H,W)` int32 labels (or per-channel blocks — see deferred); numpy views via `np.ndarray(..., buffer=shm.buf)`; `ready: list[bool]` per timepoint; `close()`/`unlink()` lifecycle.
- `decode_frame_into_buffer(path, hdf5_path, t, shm_name, shape, dtype, slot)`: **top-level, picklable** function — opens its **own** `DatasetStore`/h5py handle, reads timepoint `t` via the existing `read_array_frame` logic, writes into the shared view. Used by a single thread in Phase 1 and by the process pool in Phase 2 unchanged.
- Normalize metadata (channel_names→list, shapes→tuple) at the read boundary; never `x or default` on arrays.

**Patterns to follow:** `store.py::read_array_frame` for per-frame slicing; metadata normalization per the multi-channel-load learning.

**Test scenarios:**
- Happy path: allocate `(2,4,64,64)` float32 buffer; decode each frame from a real `tmp_h5`; buffer matches `read_array` whole-array result frame-by-frame.
- Edge: single-channel `(T,H,W)` dataset; single-timepoint dataset (`T=1`).
- Edge: NaN-bearing intensity round-trips identically (no nan-to-num).
- Edge: multi-channel (`C=2`) — guards against the `or`-on-array bug.
- Error: decode with out-of-range `t` raises cleanly; `close()`/`unlink()` releases the segment (no leaked `/dev/shm` entry).
- Integration: two `decode_frame_into_buffer` calls from separate processes writing disjoint frames into the same `shm_name` produce a correct full buffer.

**Verification:** Buffer reconstructed frame-by-frame equals the eager `read_array` output for single- and multi-channel real files, including NaNs; shared segment is freed after `close()`/`unlink()`.

---

- U2. **Nan-aware contrast from timepoint 0, passed explicitly**

**Goal:** Compute display contrast from frame 0 only and pass `contrast_limits` to `add_image`,
eliminating the full-stack scan and making lazy display correct (the full stack isn't present yet).

**Requirements:** R4

**Dependencies:** None

**Files:**
- Modify: `src/percell4/interfaces/gui/main_window.py` (`_populate_viewer_from_store` contrast computation)
- Modify: `src/percell4/gui/viewer.py` (only if a helper is needed; `add_image` already accepts `contrast_limits`)
- Test: `tests/test_gui/test_viewer_contrast.py`

**Approach:**
- Compute `(nanmin, nanmax)` over the timepoint-0 plane per channel; if all-NaN/degenerate, fall back to a safe default (mirror existing `IMAGE_DEFAULT_CONTRAST_OVERRIDE` behavior). Pass explicit `contrast_limits` so neither `ViewerWindow.add_image` nor napari scans.

**Patterns to follow:** `gui/viewer.py::add_image` existing contrast branch.

**Test scenarios:**
- Happy path: 2-channel frame-0 → per-channel `contrast_limits` equal `nanmin/nanmax` of frame 0; `add_image` called with explicit limits (assert no full-stack scan via a spy/large-array sentinel).
- Edge: frame 0 contains NaN → limits ignore NaN; all-NaN frame → default fallback, no crash.

**Verification:** `add_image` always receives `contrast_limits`; displayed contrast matches the previous full-stack result within tolerance on a no-NaN file.

---

- U3. **Rewire `_populate_viewer_from_store` to lazy-first + single-worker background fill**

**Goal:** Replace the synchronous full-load with: allocate buffer → decode frame 0 → create
layers (contrast from U2) → start a background QThread that fills frames 1..T-1 → wire progress,
per-frame refresh, on-demand scrub fallback, and cleanup.

**Requirements:** R1, R2, R4, R6

**Dependencies:** U1, U2

**Files:**
- Modify: `src/percell4/interfaces/gui/main_window.py` (`_populate_viewer_from_store`, `_load_h5_into_viewer`, dataset-switch/close cleanup)
- Modify: `src/percell4/gui/viewer.py` (create layers from pre-allocated buffer arrays; expose a `refresh_layer(name)` / current-view refresh; ensure programmatic layer adds run under the originator guard)
- Modify: `src/percell4/gui/workers.py` (a `BackgroundFiller` QThread wrapping serial `decode_frame_into_buffer` calls, emitting `progress`/per-frame `frame_done`/`finished`/`error`)
- Test: `tests/test_gui/test_lazy_load_populate.py`

**Approach:**
- Hold a reference to the filler worker (GC safety). Route `progress` → status bar (`statusBar().showMessage`).
- `frame_done(t)` → main-thread slot sets `ready[t]=True`; if `t == current_step`, refresh.
- Hook dims `current_step`: if `not ready[t]`, decode frame `t` synchronously, set ready, refresh — **without** writing session (respect `_timepoint_originator`).
- On dataset switch / `viewer.clear()`: cancel filler, `close()`/`unlink()` buffer (single invalidation point).
- **Execution note:** Start with a failing integration test that asserts the viewer is populated with timepoint 0 and returns control before background fill completes.

**Patterns to follow:** `gui/segmentation_panel.py` worker wiring (`finished`/`error` connect, ref held); `gui/viewer.py::add_staged_overlay` for in-place `.data`; `session-to-napari-one-way-push.md` guard.

**Test scenarios:**
- Happy path (FakeWorker): load a real small `tmp_h5`; assert frame 0 visible and layer dims == `(T,H,W)` before fill; then drive `frame_done` callbacks and assert `ready` flips and refresh is requested for the current frame.
- Integration: scrub `current_step` to an un-filled frame → synchronous decode fills it; displayed data equals `read_array_frame`; no `session.set_active_timepoint` recursion (originator guard holds).
- Edge: single-timepoint file → no background filler started; behaves like today.
- Edge: multi-channel file populates all channel layers from the buffer.
- Error: a frame-decode error surfaces via `error`/status without crashing the UI; partial buffer still navigable.
- Lifecycle: switching datasets mid-fill cancels the filler and frees the buffer (no leaked segment, no stale frames from the prior dataset — guards the in-session-staleness vector).

**Verification:** Opening a trigger-class file shows timepoint 0 within ~1–2s with a responsive UI and a progress indicator; scrubbing always shows correct frames; switching datasets leaves no leaked shared memory.

---

- U4. **Guard interactive ops against not-yet-filled frames**

**Goal:** Operations that read a layer's `.data` for the active timepoint must see filled data.

**Requirements:** R2, R6

**Dependencies:** U3

**Files:**
- Modify: `src/percell4/interfaces/gui/main_window.py` (`_get_active_seg_labels`)
- Modify: `src/percell4/gui/segmentation_panel.py` (active intensity layer read before Cellpose)
- Test: `tests/test_gui/test_interactive_ops_unfilled_frame.py`

**Approach:**
- Before reading `layer.data[t]` for the active timepoint, ensure `ready[t]` (trigger on-demand decode from U1/U3 if needed). Prefer reading via the buffer/`ready` path or re-reading from the store, consistent with how compute paths already re-read from disk (`application/analysis/loader.py`, `workflows/phases.py`).

**Patterns to follow:** existing disk re-read pattern in `application/analysis/loader.py::read_channel`.

**Test scenarios:**
- Happy path: `_get_active_seg_labels` on a filled frame returns correct labels.
- Integration: request active seg labels / run Cellpose on a frame not yet filled → on-demand fill occurs (or store read), result equals eager data; no zeros/blank-frame leakage.

**Verification:** Interactive seg/Cellpose on any timepoint during background fill produces the same result as on a fully-loaded file.

---

### Phase 2 — Parallelize the background fill (~4.6× → resident in ~12s)

- U5. **Process-pool background filler writing into shared memory**

**Goal:** Replace U3's serial filler with a `ProcessPoolExecutor` that decodes frames concurrently
into the shared buffer; only frame indices cross the process boundary (data lands in shared mem).

**Requirements:** R3

**Dependencies:** U1, U3

**Files:**
- Modify: `src/percell4/gui/workers.py` (`BackgroundFiller` gains a process-pool mode) or Create: `src/percell4/gui/parallel_filler.py`
- Modify: `src/percell4/adapters/parallel_loader.py` (ensure `decode_frame_into_buffer` is spawn-safe / top-level)
- Test: `tests/test_adapters/test_parallel_filler.py`

**Approach:**
- A coordinator QThread submits `decode_frame_into_buffer` per frame to `ProcessPoolExecutor(max_workers=min(os.cpu_count()-2, T))`; iterate `as_completed`, emit `frame_done(t)`. Pass `shm_name`+shape+dtype+path+`t`; workers open their own handle. Cancellation requests pool shutdown (best-effort; in-flight frames may finish).
- Keep the Phase-1 on-demand scrub fallback as the correctness backstop.

**Patterns to follow:** the measured benchmark harness (per-frame `decode_frame_into_buffer` across processes, ~4.6× at 8 workers); existing worker signal shape in `gui/workers.py`.

**Test scenarios:**
- Happy path: fill an `(2,8,64,64)` real file via the pool; full buffer equals eager `read_array`; `frame_done` emitted once per frame.
- Integration: concurrent writers to disjoint frame slots produce no corruption (compare to serial result).
- Edge: `T=1` / fewer frames than workers — no deadlock, correct result.
- Error: a worker exception for one frame is reported and does not corrupt other frames; that frame still resolves via on-demand fallback.
- Lifecycle: cancel mid-fill shuts down the pool and frees the buffer without orphaned processes/segments.

**Verification:** A trigger-class file reaches full residency in ~10–15s (vs ~57s) with correct data and no leaked processes or shared-memory segments.

---

### Phase 3 — Faster codec for new exports (float32 kept)

- U6. **Faster-decoding codec for new intensity/label writes**

**Goal:** New files decode in seconds; existing gzip files remain readable; dtype stays float32.

**Requirements:** R5

**Dependencies:** None (independent of Phase 1/2)

**Files:**
- Modify: `src/percell4/store.py` (`_compression_kwargs` / `_choose_chunks`)
- Modify: `pyproject.toml` (`[project.dependencies]` — add `hdf5plugin` if Blosc is chosen)
- Test: `tests/test_store.py` (codec round-trip + backward-read)

**Approach:**
- Extend the canonical `_compression_kwargs` to select a faster codec for non-decay image/label writes (Blosc2+zstd+bitshuffle via `hdf5plugin`, or `lzf`). Keep float32. Whole-file writes continue through `DatasetStore.create_atomic`. Do **not** add hardcoded `compression=` at any new `create_dataset` site (drift violation).
- Benchmark codec/level/`shuffle` choice; record decode time and file size vs gzip. Confirm portability expectations (Blosc requires `hdf5plugin` to read).

**Patterns to follow:** `decay-write-path.md`, `atomic-write-contract.md`; the existing decay `lzf` precedent in `_compression_kwargs`.

**Test scenarios:**
- Happy path: write intensity+labels with the new codec; read back equals input (float32), including NaN values.
- Backward-compat: an existing gzip fixture still reads correctly after the change.
- Integration: a file produced by the importer/batch exporter (`import_dataset`) uses the new codec and is readable by the lazy loader (U1) and the eager `read_array`.
- Perf (non-asserting log): record decode time + size vs gzip on a representative array.

**Verification:** New exports open markedly faster and round-trip float32+NaN losslessly; old gzip files open unchanged.

---

## System-Wide Impact

- **Interaction graph:** new background filler (QThread → process pool) emits Qt signals to the main thread; dims `current_step` gains a pre-paint readiness check. Programmatic layer adds/refreshes must stay under the `_timepoint_originator` / one-way-push guard so they don't write `session.active_*`.
- **Error propagation:** per-frame decode errors surface via the worker `error`/status path; a single bad frame must not abort the whole load — on-demand fallback covers it.
- **State lifecycle risks:** the resident shared-memory buffer is a new cache vector — must be freed and `ready` reset on dataset switch / viewer clear; otherwise stale frames or leaked `/dev/shm` segments (the in-session-staleness failure mode).
- **API surface parity:** the `LoadDataset`/`build_view` path is intentionally not changed; CLI/batch keep eager load (batch uses `NullViewerAdapter`, no display cost).
- **Integration coverage:** measurements/segmentation already re-read from disk, so lazy display does not affect them; the only layer-`.data` readers (`_get_active_seg_labels`, Cellpose) are handled in U4.
- **Unchanged invariants:** post-load resident behavior, the store-before-layer ordering, the dims/timepoint Selector contract, and the `_compression_kwargs` decay branch all remain as-is.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| IPC overhead erases the 4.6× if decoded arrays are returned through the pool | Workers write into **shared memory**; only frame indices cross the boundary (U1/U5). |
| `spawn` (macOS) can't pickle closures/locals | `decode_frame_into_buffer` is a top-level importable function taking only primitives + `shm_name` (U1). |
| Leaked shared-memory segments / orphaned processes on switch/close | Single invalidation point: cancel filler + `close()`/`unlink()` on dataset switch / `viewer.clear()`; lifecycle tests (U3/U5). |
| Programmatic layer fill trips session writes (recursion/flicker) | Run under `_timepoint_originator`/one-way-push guard; on-demand fill mutates data only, not session (U3). |
| Scrub-to-unfilled frame shows a blank flash | `ready` flag + synchronous on-demand decode; optionally fill-before-paint (deferred measurement). |
| Multi-channel load regressions (historic `or`-on-array bug) | Normalize metadata at read boundary; single- AND multi-channel tests in U1/U3. |
| Blosc codec not portable to non-h5py readers | Confirm files stay in-ecosystem; `lzf` fallback; route through `_compression_kwargs` only (U6). |
| Departing the "no HDF5 off main thread" worker convention | Each worker/process opens its **own** handle; never share an open handle; documented exception. |

---

## Phased Delivery

### Phase 1 (U1–U4) — Lazy-first display
Headline UX win: usable in ~1–2s, non-blocking, correct on scrub. Single-threaded background fill
(full residency in ~57s, but invisible). Ships independently.

### Phase 2 (U5) — Parallel fill
Swap the fill strategy to a process pool → full residency in ~12s. Pure performance; correctness
already guaranteed by Phase 1's on-demand fallback.

### Phase 3 (U6) — Faster new-file codec
Exporter writes a faster-decoding codec (float32 kept) so new files are born fast. Independent of
Phase 1/2; can land anytime.

---

## Sources & References

- **Origin document:** `docs/brainstorms/2026-06-06-large-file-load-time-requirements.md`
- Load path: `src/percell4/interfaces/gui/main_window.py`, `src/percell4/gui/viewer.py`
- Read primitives: `src/percell4/store.py` (`read_array_frame`, `_compression_kwargs`, `_choose_chunks`)
- Worker pattern: `src/percell4/gui/workers.py`, `src/percell4/gui/segmentation_panel.py`
- Learnings: `docs/solutions/architecture-patterns/{session-to-napari-one-way-push,decay-write-path,atomic-write-contract}.md`, `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`, `docs/solutions/runtime-errors/multi-channel-dataset-load-numpy-array-truth-value-2026-05-22.md`
