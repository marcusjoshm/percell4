# Large-File Load Time — Requirements

**Date:** 2026-06-06
**Status:** Superseded — diagnosis partly wrong (see banner)
**Scope:** Deep — feature (optimizes the dataset load path; viewer interaction unchanged)

> **⚠️ The "decode-bound" framing below was incomplete.** Real-app profiling
> (2026-06-07) found the actual bottleneck was `DataPanel.refresh_dataset_info()`
> decoding the full 12.6GB intensity ~4× per load just to read its shape (~160s),
> plus a double-decode. Fixed via metadata-only `array_shape` + a single-decode
> guard + multiprocessing parallel decode of the eager display read
> (~250s → ~20s). Lazy-first loading was implemented and reverted. See
> `docs/solutions/logic-errors/large-file-load-metadata-read-full-decode-2026-06-07.md`.

## Problem

Opening very large stitched `.h5` files takes **minutes** from "Load Dataset…" to a usable
viewer. The wait — not viewability — is the entire problem. Once loaded, the napari viewer
(zoom, pan, hotkeys, scrubbing) is fast and pleasant, and the user wants to **keep** that
resident-everything interaction.

Trigger class: whole-slide live timecourses, e.g. `Nutlin3a_Merged.h5` / `Nutlin3a_batch.h5`
(3.89 GB on disk, `/Volumes/<lab-server>/<experiments>/datasets_h5/`):

| Dataset | Shape | dtype | Uncompressed |
|---|---|---|---|
| `/intensity` | `(36, 2, 6686, 6567)` | float32 | ~12.6 GB |
| `/labels/cellpose_382` | `(36, 6686, 6567)` | int32 | ~6.3 GB |

## Measured root cause (2026-06-06, profiled on the real file)

The slowness is **almost entirely one thing: single-threaded gzip decompression of ~19 GB.**

| Factor | Cost | Evidence |
|---|---|---|
| Decompress all 19 GB (gzip, 1 core) | **~57–90s — ~90% of the time** | per-frame timing: intensity ~43s, labels ~15s; pure zlib ~80s/19GB |
| Disk read of compressed bytes | **negligible** | user confirms reopening is the *same speed* warm as cold → not I/O-bound |
| RAM / swap | **not a factor** | machine has 68.7 GB (45 GB free); 19 GB resident fits easily |
| Contrast scan in `add_image` | **~2–5s (minor)** | `np.nanmin/nanmax` runs at ~10 GB/s (memory-bandwidth bound) |
| napari layer/texture setup | ~5–15s (secondary) | 3 × 44 MP layers |
| Redundant `.astype(np.float32)` | **~0 for GUI users** | it lives in `build_view` (use-case path), which the GUI button does not call |
| Synchronous on main thread | — | UI is frozen throughout; makes the wait feel worse and blocks early use |

**Two corrections to earlier assumptions:** (1) the contrast scan and the `.astype` copy are
*not* meaningful overhead — fixing them would save ~3s on a ~90s problem. (2) RAM/swap and disk
are not factors. **The only thing worth attacking is the decompression itself.**

**Critical code-path fact:** the GUI "Load Dataset…" button goes through
`src/percell4/interfaces/gui/main_window.py` → `_load_h5_into_viewer` →
`_populate_viewer_from_store` (full synchronous `read_array` on the main thread). It does **not**
use `LoadDataset.execute` / `build_view` (CLI/batch/alt-window only). Optimization must target
the GUI path.

## Validated levers (measured)

- **Multiprocessing decode: ~4.6× speedup** — 8 worker processes decoding disjoint timepoints
  brought intensity decode from ~43s → ~9s. (Threads give **zero** speedup; HDF5 serializes.)
- **float32 intensity is integer-valued in this file** (all integral, max 534) — *but* uint16
  storage is **rejected**: the pipeline relies on **NaN** (masked/background pixels) and other
  float features, which uint16 cannot represent. **float32 is retained universally.**

## Goal

Cut time-to-usable from minutes to **~1–2 seconds** for existing files (no re-export), while
preserving the resident-everything interaction the user values. Make new files born fast.

## Users

Microscopy researchers (Lee Lab) opening large stitched live-cell timecourses from the headless
batch exporter (`percell4-batch-cellpose-laptrack`).

## Strategy (chosen): L1 + L2 now, L4 for new files

### L1 — Lazy-first display (existing files)
Decode only **timepoint 0** at open (~0.5s intensity + ~0.2s labels), show it immediately, and
fill remaining timepoints in the background. Time-to-usable → **~1–2s**. Works on all existing
files, no re-export.

### L2 — Multiprocessing background decode (existing files)
Decode the remaining timepoints across all cores via worker **processes** (not threads), filling
a **shared-memory** buffer that backs the napari layer (workers write their timepoint slices;
main process wraps the same buffer). Full residency in **~12s** instead of ~57s. After fill,
scrubbing is instant (current behavior preserved). Visiting a not-yet-filled frame falls through
to an on-demand decode (~1.5s) until prefetch catches up.

### L4 — Faster codec for new exports (write-side, keep float32)
Change new-file intensity/label compression from gzip to a faster-decoding codec (blosc+lz4/zstd,
which decodes multithreaded at ~GB/s; or lzf). **Keep float32 dtype** — codecs are lossless byte
compression and handle NaN fine. New files decode in ~seconds even on a single eager pass.
Existing gzip files remain readable unchanged (no forced migration).

**Rejected:**
- **L3 (uint16 storage)** — breaks NaN support and other float features the pipeline depends on.
- **Multiscale / zoom pyramids** — optimize pan/zoom, which is already fast. Irrelevant here.
- **Thread-parallel decode** — measured zero speedup (HDF5 global lock).

## Functional Requirements

### FR1 — Near-instant time-to-usable (L1)
- The viewer becomes usable on timepoint 0 within ~1–2s of "Load Dataset…", regardless of total
  file size, for trigger-class files. Load runs off the main thread; UI never freezes; progress
  is visible.

### FR2 — Multiprocessing background residency (L2)
- Remaining timepoints decode across all available cores into the resident arrays the app already
  uses, so once fill completes, scrubbing/analysis behave exactly as today.
- Decoded data reaches the main process via shared memory (no bulk IPC pickling).
- Visiting a not-yet-filled frame returns correct data via on-demand decode; no errors, no blanks.

### FR3 — Correct display without full-stack scans (L1/L2)
- Contrast limits are computed from timepoint 0 (nan-aware) and passed explicitly to napari, so
  neither `ViewerWindow.add_image` nor napari scans the full stack. Appearance matches today
  within tolerance.

### FR4 — Faster new-file codec, float32 preserved (L4)
- The exporter writes new intensity/label datasets with a faster-decoding codec via the canonical
  `store._compression_kwargs` knob. dtype stays float32. Existing files remain readable.

## Success Criteria

- `Nutlin3a_Merged.h5`-class files reach a usable viewer in ~1–2s (L1), fully resident in ~12s
  (L2), vs. minutes today.
- No regression in displayed images, segmentation overlays, measurements, selection/filtering, or
  mask editing once data is resident; NaN-bearing data renders identically.
- New exports (L4) open in seconds and remain readable as standard `.h5`.

## Scope Boundaries

**In scope:** GUI load-path performance — L1 (lazy-first + worker + progress), L2 (multiprocessing
shared-memory background decode), L4 (write-side codec, float32 retained).

**Deferred for later:**
- Re-exporting / migrating existing gzip files to the new codec.
- Unifying the GUI load path onto the `LoadDataset` use case (separate refactor; not required for
  the speedup).
- FLIM/decay load and runtime performance.

**Outside this product's identity:** PerCell4 stays single-`.h5`-per-experiment, h5py-backed,
resident-data viewer. No tile servers, databases, or separate viewer product. Zoom pyramids and
uint16 down-conversion are explicitly rejected (see Rejected).

## Open Questions (for planning)

1. **Shared-memory mechanism for L2:** `multiprocessing.shared_memory` buffer wrapped as the
   napari layer array, vs. a memmap; how workers are pooled and how the buffer is allocated at
   full `(T,C,H,W)` shape up front (so napari's dims range is correct immediately and frames fill
   in place — avoids `(1,H,W)→(T,H,W)` swap churning the dims/timepoint Selector).
2. **L1 ↔ napari timepoint Selector:** filling frames must not trip the `_timepoint_originator`
   guard into spurious `session.set_active_timepoint` writes (see
   `docs/solutions/architecture-patterns/session-to-napari-one-way-push.md` and `gui/CLAUDE.md`).
3. **Resident-array cache invalidation:** prefetched arrays are a new cache vector — bind reset to
   a single event (dataset switch / write boundary) per
   `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`.
4. **L4 codec choice & portability:** blosc/lz4/zstd (needs `hdf5plugin` to read; not portable to
   non-h5py readers) vs. lzf (h5py-only, weaker ratio). Confirm files stay in-ecosystem. Route
   through `store._compression_kwargs` / `_choose_chunks` (canonical knob); write via
   `DatasetStore.create_atomic`.
5. **Interactive ops before fill completes:** `_get_active_seg_labels` and the GUI Cellpose run
   read the napari layer's `.data` directly — guard against not-yet-filled frames.
6. **Worker convention departure:** load workers reading h5py off the main thread breaks the
   "no HDF5 in worker" rule in `src/percell4/gui/workers.py` — each worker must open its own file
   handle (h5py handles are not shareable across processes/threads).

## Dependencies / Assumptions

- Measured on `Nutlin3a_Merged.h5` (2026-06-06): decode-bound (~57–90s), single-threaded;
  multiprocessing ~4.6×; disk and RAM not factors; threads give no speedup.
- Existing files are chunked `(1,1,256,256)` + gzip — efficient for per-timepoint reads, so L1's
  timepoint-0 read and L2's per-frame workers are cheap.
- Cross-timepoint analysis lives in the per-cell measurements/tracks tables (already lazy), so
  deferred pixel residency does not block table-driven analysis.
- T1 I/O changes (`src/percell4/store.py`, `src/percell4/adapters/`,
  `src/percell4/interfaces/gui/main_window.py`, `src/percell4/gui/viewer.py`,
  `src/percell4/gui/workers.py`) must follow the I/O principles audit and the canonical patterns
  surfaced (`decay-write-path`, `atomic-write-contract`, `session-to-napari-one-way-push`,
  in-session staleness, QThread worker). Consult `compound-engineering:ce-learnings-researcher`
  and `docs/audits/canonical-sources-matrix.yaml` before editing.
