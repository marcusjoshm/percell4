---
title: "Large-file 'slow to open' was a shape read decompressing the whole stack (x4), not the display decode"
date: 2026-06-07
category: logic-errors
module: percell4.store, percell4.interfaces.gui.task_panels.data_panel, percell4.interfaces.gui.main_window
problem_type: logic_error
component: tooling
applies_to:
  - "src/percell4/store.py"
  - "src/percell4/interfaces/gui/task_panels/data_panel.py"
  - "src/percell4/interfaces/gui/main_window.py"
  - "src/percell4/adapters/parallel_decode.py"
canonical_source: src/percell4/adapters/parallel_decode.py
symptoms:
  - "Opening a large stitched .h5 (e.g. (36,2,6686,6567) float32, 3.9GB gzip) took ~250s ('minutes, forever to open')."
  - "Two separate 'Loading' progress windows appeared per open; RAM showed a sawtooth (climb to ~12.6GB, drop, climb again) = the full intensity decoded multiple times."
  - "Profiling the read primitive in isolation (read_array ~60-90s) did NOT explain the minutes — the GUI cascade did."
root_cause: scope_issue
resolution_type: code_fix
severity: high
related_components: [hdf5, h5py, gui, performance, dataset-load, napari]
tags:
  - hdf5
  - h5py
  - performance
  - dataset-load
  - metadata-read
  - full-decode
  - multiprocessing
  - shared-memory
  - profile-the-real-path
  - gzip
---

# Large-file "slow to open" was a metadata read decompressing the whole stack

## Problem

A user reported large stitched `.h5` files took minutes to open in the GUI. The
obvious hypothesis — "the eager display decode of ~19GB is slow" — was **wrong**,
and optimizing it (a lazy-first viewer) wasted effort and was reverted. Measuring
the **real GUI load path** (not the read primitive in isolation) showed:

| Stage | Time |
|---|---|
| `session.set_dataset()` cascade | **~211s** |
| `_update_data_tab_from_store()` | **~42s** |
| actual display decode | ~4s (lazy frame-0) / ~60s (full eager) |

## Root cause

`DataPanel.refresh_dataset_info()` did this just to display the shape string:

```python
with store.open_read() as s:
    intensity = s.read_array("intensity")   # decodes the ENTIRE ~12.6GB stack
    shape = intensity.shape
```

`refresh_dataset_info()` fires **~3 times** per `set_dataset` (on `segmentation_list`,
`mask_list`, and `segmentation/mask` StateChange flags — see
`data_panel.py::_on_state_changed`) **plus** once via `_update_data_tab_from_store`
= ~4 full decodes of a 12.6GB array (~40s each) ≈ ~160s of pure waste, purely to
read a shape tuple HDF5 already stores as metadata.

A second, independent waste: `_load_h5_into_viewer` called `_show_window("viewer")`
(which auto-populates an empty viewer) **and then** `_populate_viewer_from_store()`
explicitly → the display data was decoded **twice** (two progress dialogs, ~2x).

## Resolution

1. **Read shape from metadata, never data.** Added `DatasetStore.array_shape(path)`
   (HDF5 `obj.shape`, ~1ms vs ~40s) and used it in `refresh_dataset_info`. This one
   change took load ~250s → ~60s (~4x), user-confirmed.
2. **Decode the display data once.** Guarded `_show_window`'s auto-populate with a
   `_loading_dataset` flag so `_load_h5_into_viewer` does a single populate.
3. **Parallelize the necessary decode.** gzip decode does NOT parallelize across
   threads (HDF5 serializes its calls) but DOES across processes. Added
   `adapters/parallel_decode.py`: worker processes decode frames straight into one
   `multiprocessing.shared_memory` block (only frame indices cross the boundary,
   never pixels), driven from `_populate_viewer_from_store` behind a modal progress
   dialog at `view_bin==1`. Measured **59.6s → 11.2s (5.3x)**, byte-identical to
   `read_array`. Net end state: **~250s → ~20s (~12x)**.

## Lessons (the compounding part)

- **Profile the REAL entry path, not the primitive in isolation.** `read_array`
  measured ~60-90s alone; the actual GUI open was ~250s because of cascade handlers.
  Instrument the top-level user action (`_load_h5_into_viewer`) end to end first.
  Don't build an optimization on an isolated micro-benchmark.
- **A "shape"/"exists"/"count" read must never decompress data.** Use
  `array_shape` / a metadata-only existence check (see also `flim_panel.py:632`
  reading `phasor/<ch>/g_filtered` just to toggle a combo — same anti-pattern).
- **Event cascades multiply cost.** One `set_dataset` fans out to many
  subscribers; an expensive handler called "once" may run 3-4x per load. Count the
  fan-out before assuming a handler is cheap.
- **For HDF5 gzip decode, processes beat threads.** Threads give ~0x speedup (HDF5
  global lock); a `ProcessPoolExecutor` + `shared_memory` gives ~Ncores×. h5py
  DOES release the GIL (a background h5py thread leaves the Qt main thread ~96%
  responsive), but that buys responsiveness, not decode throughput.
- **macOS `spawn` gotchas** (baked into `parallel_decode.py`): worker fn must be
  module-level and the module must import only h5py/numpy/stdlib (no Qt/napari);
  `main.py` must import the GUI under `if __name__ == "__main__"` so re-imported
  workers start fast; the parent owns the shared-memory block (`close()` +
  `unlink()` once), workers only `close()`.
- **A faster codec (Blosc/lz4) would cut decode further but only for NEW files**
  (existing files stay gzip); deferred, not done.
