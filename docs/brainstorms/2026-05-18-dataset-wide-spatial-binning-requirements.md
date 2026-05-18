# Dataset-wide Spatial Binning — Requirements

**Status:** Draft (post-brainstorm, ready for `/ce-plan`)
**Date:** 2026-05-18
**Author:** Joshua M Marcus (brainstormed with Claude)

## Problem

PerCell4 currently supports a spatial bin factor `k` (1..16) only for TCSPC
`.bin` import (commits `4367a10`, `a3a1d0a`). The factor is baked in at import
time — the resulting decay array is permanently stored at the binned shape.

If a user wants to work with both binned and unbinned views of the same
acquisition, they must create two `.h5` files and import every layer into
each. That doubles disk usage, doubles import labor, and makes it impossible
to keep raw and binned analyses in sync as the rest of the dataset evolves.

The user wants binning to be a **first-class, dataset-wide property** that
can be toggled at will, with a single source of truth on disk.

## Goals

- One `.h5` per acquisition. No more "raw vs. binned" file duplication.
- Binning is a **session-level view setting** (k=1..16), toggleable at any time.
- Derived data (segmentations, masks, measurements) produced at a given view
  bin records that fact and remains usable when the view bin changes.
- Every layer type benefits: intensity / channels, TCSPC decay, masks, labels.
- Surfaced in `/metadata` and visible in the SessionWindow.

## Non-goals (deferred)

- Per-layer independent bin factors. The dataset has **one active view bin**.
- Mixed-resolution storage (one layer at k=1, another at k=3 on disk). All
  stored data is at native resolution; bin is applied on read.
- Anisotropic binning (e.g., `kx≠ky`). Single integer `k` only.
- A "promote a binned view to a new .h5" export. Out of scope; revisit only
  if the toggle workflow ends up insufficient.
- Z-axis or T-axis binning. Spatial only (H, W).

## Core invariants

These are the constitutional rules. Every design detail below must respect them.

1. **Every array stored in the `.h5` is at the dataset's native (k=1)
   resolution.** Period. No `/labels_bin3/`, no `/intensity@k=3` group.
2. **The dataset declares a single `native_shape` in `/metadata`**, fixed at
   compress time. Subsequent imports must match exactly.
3. **The session has one active view bin** (`session.active_bin`,
   k ∈ {1..16}). It is a runtime lens, not a persistent property of any layer.
4. **Derived results record the bin they were produced at.** A segmentation
   created at k=3 carries an attribute `created_at_bin=3`, and (per naming
   rule below) its auto-name carries `_bin3`. The pixels themselves are
   nearest-neighbor-upsampled back to native and stored at native.

## User-facing behavior

### The session view bin control

- Lives in **SessionWindow** as a SpinBox or dropdown, range 1..16, default 1.
- Changing it triggers a `StateChange` (new field: `bin`) so subscribers
  (ViewerWindow, DataPanel, plots, measurement, phasor) can re-read.
- Persisted to the `Session` object only — not to the `.h5`. Closing and
  reopening a dataset resets the view bin to 1.
- The current value is shown in the DataPanel `_info_label` for clarity
  (read-only mirror).

### What changes when the user toggles `k`

| Layer type        | On read at k>1                                                | On write at k>1                                                                 |
| ----------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Intensity / channels | Sum-bin H, W in memory; residual rows/cols truncated.       | n/a — channels are only written at import (always native).                      |
| TCSPC decay       | Sum-bin (H, W); T axis untouched; residuals truncated.        | n/a — decay is only written at import (always native).                          |
| Masks (uint8)     | Majority-vote within each k×k block (≥⌈k²/2⌉ pixels set).     | Produced at binned res, **majority-vote upsampled** (nearest neighbor) to native.|
| Labels (int32)    | Mode (most-frequent label) within each k×k block; ties → 0.   | Produced at binned res, nearest-neighbor upsampled to native.                    |
| Measurements      | Run against the binned arrays; numbers stored in **k=1-equivalent units** (a pixel area at k=3 contributes k² to "area_pix"; sum-intensity at k=3 is already photon-equivalent to k=1). | Each measurement row carries `bin_at_measure=3`. |

### Result naming and provenance

- When the active bin is k>1, the auto-namer suffixes new results with
  `_bin<k>`. Example: a Cellpose run at k=3 produces `cellpose_bin3` instead
  of `cellpose`.
- Every label, mask, and measurement-row carries a `created_at_bin` attr
  (or column, for measurements).
- The DataPanel layer lists show the bin as a small annotation, e.g.
  `cellpose_bin3 [k=3]`, so it's never ambiguous which results came from
  which view.

### Loading an existing result at a different view bin

- Selecting `cellpose_bin3` while view bin is k=1 displays the stored
  (native, nearest-neighbor-upsampled) labels with visible blockiness — no
  warning, no error. The user accepted this asymmetry: keeping all storage
  at native is worth the visual artifact.
- Selecting `cellpose_bin3` while view bin is k=3 reads the labels and
  mode-downsamples to k=3 on the fly — appears non-blocky.
- Selecting `cellpose_bin3` while view bin is k=5 (a value it was not
  produced at) mode-downsamples. Acceptable; user can re-run if needed.

### Compress dialog: defining native at creation

- Adds a **"Creation spatial bin"** SpinBox (k=1..16) to `CompressDialog`,
  alongside the existing z-projection and channel selection.
- At k>1, every source TIFF and every source `.bin` is sum-binned k×k before
  being written. `native_shape` in the resulting `.h5` is
  `(H_src // k, W_src // k)`.
- `/metadata.native_shape = (H, W)` and `/metadata.creation_bin = k` are
  written once per dataset.
- All source TIFFs / `.bin` files referenced by one compress run **must agree
  on source shape** (after their respective z-projects). The compress dialog
  validates this up front — first-shape-wins is rejected as unsafe; a single
  bad source must produce a clear error, not corrupt the rest.

### Add-Layer dialog: post-creation imports

- The existing TCSPC spatial-bin spinner on `add_layer_dialog.py` and
  `_stitching_flim_form.py` is **removed**. The k=1..16 control moves
  exclusively to SessionWindow as the view bin.
- A new layer imported via Add Layer must match `native_shape` exactly.
  Mismatches fail with an explicit error: *"Layer source is 512×512;
  dataset native is 170×170. Re-import via Compress dialog with creation_bin,
  or pre-bin externally."*
- **Open question:** the user said "repurpose as session view bin only,"
  which deletes the import-time match-to-native path. If round-trip
  pre-binning outside the app turns out to be friction-heavy, we can add
  back a "match-to-native bin k×" control in the Add Layer dialog later.
  Flagging this so it's a conscious tradeoff, not an accidental regression.

## `/metadata` schema additions

```
/metadata.attrs:
  native_shape      : tuple[int, int]   # (H, W) at k=1. Fixed at compress.
  creation_bin      : int               # k applied at compress time. 1 if none.
```

Per-layer attrs:

```
/labels/<name>.attrs:
  created_at_bin    : int               # The active view bin when produced.
/masks/<name>.attrs:
  created_at_bin    : int
```

Per-measurement column:

```
measurements DataFrame:
  bin_at_measure    : int               # The view bin at the time of measure.
```

## Behavior matrix: what survives, what doesn't

| Action                                            | Effect on stored data                              | Effect on view |
| ------------------------------------------------- | -------------------------------------------------- | -------------- |
| Toggle view bin                                   | None.                                              | All layers re-read at new k. |
| Run segmentation at k=3                           | Labels nearest-neighbor upsampled to native and stored as `cellpose_bin3`. | New layer visible immediately. |
| Toggle to k=1 after segmenting at k=3             | None. Labels are at native, view shows blockiness. | Visual artifact only. |
| Measure cells at k=3                              | Rows tagged `bin_at_measure=3` written to table.   | Plots reflect those rows. |
| Re-measure same cells at k=1                      | New rows tagged `bin_at_measure=1` are appended.   | Plots can group by bin. |
| Close and re-open dataset                         | None.                                              | View bin resets to 1. |

## Performance considerations

- **Intensity channels:** typical (H, W) ~ 512–2048. Sum-binning is cheap
  (numpy `add.reduceat` or `reshape().sum(axis)`). Acceptable on every read.
- **TCSPC decay:** the heavy case. A 1024×1024×256 decay is ~1 GB float32.
  Sum-binning on every read is potentially seconds. Mitigation: ViewerWindow
  (and phasor/lifetime compute paths) maintain an **LRU-1 in-memory cache**
  keyed by `(dataset_path, channel, k)`. Cache is per-process, dropped on
  dataset close. Decision is *not* to materialize binned decay back into
  the `.h5` — the toggle stays clean, but the user pays first-read latency.
- **Masks and labels:** small arrays, downsampling is microseconds.
  No cache.

## UI surfaces touched

- `interfaces/gui/peer_views/session_window.py` — add view-bin SpinBox.
- `interfaces/gui/task_panels/data_panel.py` — show active bin in
  `_info_label`; annotate layer-list entries with `[k=N]`.
- `gui/compress_dialog.py` — add "Creation spatial bin" spinner; thread
  through to `import_dataset` for both TIFFs and `.bin`.
- `gui/add_layer_dialog.py` — remove the existing TCSPC spinner; validate
  source shape against `/metadata.native_shape`.
- `gui/_stitching_flim_form.py` — remove the spatial-bin spinner.
- `gui/batch_tcspc_dialog.py` — drop the spinner read.
- `model.py` / `application/session.py` — add `active_bin` field;
  extend `StateChange` with `bin` flag.

## Architecture / data-layer touches

- `store.py` — read methods (`read_array`, `read_labels`, `read_mask`,
  decay reads) accept an optional `view_bin: int = 1` and downsample on the
  way out. Write methods unchanged (all writes are at native). Native shape
  and creation bin are stored in `/metadata`.
- `adapters/importer.py` — `write_decay_streaming`'s `spatial_bin` param
  becomes the **creation_bin** path; called once at compress with the
  dialog-chosen k. The post-import-mismatch path (current TCSPC use case)
  is removed.
- `application/use_cases/add_decay_to_dataset.py` /
  `application/use_cases/batch_add_decay.py` — `spatial_bin` parameter is
  removed; validate source shape against native.
- `measure/` — every measurement helper takes `bin: int = 1`, applies it to
  the labels and intensity arrays before measuring, and records
  `bin_at_measure` on the output rows.
- `flim/` (phasor, lifetime) — reads decay through the view-bin path; phasor
  caches are keyed by `(channel, bin)`.
- `segment/cellpose` — runs on the read-binned intensity; output labels are
  nearest-neighbor upsampled to native before writing.

## Open questions / decisions to revisit at plan time

1. **Strict native match vs. soft auto-bin on Add Layer.** Current decision:
   strict match, fail on mismatch. Risk: friction if users routinely import
   higher-res ancillary TIFFs. Watch usage.
2. **Mask binarization rule:** majority-vote chosen. If artifacts appear at
   high k, we can revisit (sum-then-threshold-at-1 as a per-mask attribute).
3. **Label downsample tie-breaking:** mode-with-ties→0 chosen. Watch for
   cells that get reduced to 0 pixels at high k — they should drop out of
   measurements gracefully, not crash.
4. **Decay cache size:** LRU-1 is the minimum that handles "flip back and
   forth between two bins." If users routinely cycle through 3+ values, may
   need LRU-N.
5. **What about pre-existing `.h5` files without `native_shape`?** Backward
   compat: on open, if `native_shape` is absent, infer from the current
   `/intensity` shape and write it on next save. `creation_bin` defaults to 1.

## Success criteria

- I can import one acquisition into one `.h5` and analyze it at any
  k ∈ {1..16} from the SessionWindow toggle, without re-importing anything.
- All four layer types (channels, decay, masks, labels) respect the toggle.
- Cell measurements at k=1 and k=3 are stored in the same DataFrame, both
  tagged with the bin they came from, and plots can compare them directly
  in physical units.
- The Data tab shows the active bin and the native shape; every result
  layer is annotated with the bin it was produced at.
- Round-trip identity: with no derived data and view bin = 1, reads
  return byte-identical arrays to what `import_dataset` wrote.
- No layer name collisions: a k=1 Cellpose run produces `cellpose`,
  a k=3 run produces `cellpose_bin3`, and they coexist.

## Out-of-scope sanity check

- Z and T binning are explicitly out of scope. Don't add knobs for them.
- "Promote bin=3 view to a separate .h5" is out of scope. The toggle is the
  promotion.
- Cross-dataset comparison at different native_shape values is out of scope;
  every dataset declares its own native, and inter-dataset comparison
  remains the user's responsibility.
