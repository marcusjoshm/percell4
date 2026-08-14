---
title: "feat: Time-lapse support with cell tracking and lineage"
type: feat
status: completed
date: 2026-05-21
deepened: 2026-05-21
---

# feat: Time-lapse support with cell tracking and lineage

## Overview

Add end-to-end time-lapse microscopy support to PerCell4: import `.tiff` series whose filenames carry a `_tN`/`_tNN` token as a multi-timepoint dataset, store the timepoint axis in HDF5, scroll through timepoints with napari's native dims slider, segment every timepoint, and run **laptrack** overlap-based tracking so the same physical cell carries the same label across timepoints. Tracking writes a new track-consistent segmentation resource (label value == track id, stable across time) and a lineage table linking each dividing parent to its daughter cells. Cells that die or leave the field of view simply end their track; cells that divide spawn two new daughter tracks linked back to the parent.

The token layer already exists — `TokenConfig.timepoint = r"_t(\d+)"` is parsed by the scanner today (`ScanResult.timepoints`) but silently discarded at import. Everything downstream (storage layout, session state, viewer, segmentation, tracking, lineage, measurement) is greenfield.

**Backward-compatibility invariant (load-bearing):** the leading time axis is added *only* when a dataset has more than one timepoint. Single-timepoint datasets keep today's exact `(H,W)` / `(C,H,W)` / 2D-label layout and behavior (no slider, no tracking surfaces, no `timepoint` measurement column). This preserves all existing `.h5` files and tests.

**Terminology:** "timepoint" is the canonical internal/domain/session term for the acquisition-time axis. laptrack's `track_df` MultiIndex names this axis `frame` — wherever this plan says "frame" near laptrack structures, `frame == timepoint index`. Domain/use-case/test code uses "timepoint"; only the laptrack adapter boundary uses "frame". Do not conflate either with the `/decay` photon-histogram axis (also internally a "T" dimension but unrelated to acquisition time).

---

## Problem Frame

PerCell4's core value is tracking individual cells across analysis steps, timepoints, and conditions, but the application currently has no temporal dimension at all: import collapses files that differ only by `_tN` into a single plane, the store enforces 2D labels/masks, the session has no active-timepoint concept, and segmentation/measurement operate on a single 2D frame. A researcher with a time-lapse acquisition cannot view it as a movie, cannot get consistent cell identities over time, and has no way to follow a cell's fate (survival, death, division) across frames. This plan delivers the temporal axis plus the tracking and lineage that turn a stack of independent segmentations into followable single-cell trajectories.

---

## Requirements Trace

- R1. Detect the `_tN`/`_tNN` token in `.tiff` filenames and treat the varying token as a timepoint axis, grouping those files into one multi-timepoint dataset (wire through the already-parsed token).
- R2. Store the timepoint axis in the HDF5 file with a leading `T` axis (`/intensity` `(T,C,H,W)`; `/labels/<name>`, `/masks/<name>` `(T,H,W)`), one resource per name, while preserving the single-timepoint 2D layout unchanged.
- R3. Make timepoints viewable in the napari viewer as a scroll-bar (dims slider).
- R4. Produce cell IDs that match across timepoints — tracking relabels into a new segmentation resource whose label value equals the track id and is stable across all timepoints.
- R5. Account for cells that die or move off the field of view (track ends) and cells that newly appear, so per-timepoint label counts may legitimately differ.
- R6. Detect cell division and link the parent cell to its (typically two) daughter cells as a lineage relationship.
- R7. Extend per-cell measurements with `timepoint`, `track_id`, `parent_track_id`, and `tree_id`, and make `(timepoint, label)` the cell identity threaded through session selection/filter.
- R8. Visualize tracks and lineage in napari (Tracks layer with the division graph), with selection following a tracked cell across the slider.

---

## Scope Boundaries

- Manual track correction / editing (splitting, merging, joining tracks by hand in the UI) is out of scope — tracking is computed, reviewed, and re-run, not hand-edited.
- 3D (z-stack) time-lapse is out of scope; this plan covers 2D + time. Existing z-projection runs before the time axis as it does today.
- Motion-model / Bayesian tracking (btrack) and deep-learning tracking (trackastra) are not adopted now; laptrack overlap tracking is the chosen engine. The tracking port is kept narrow enough that a second engine could be added later without re-architecting.
- Tracking across separate FOVs/tiles or stitching cells across stage positions is out of scope — tracking is within a single assembled field over time.
- Phasor/FLIM time-lapse (a `_tN` series of `.bin`/decay files) is out of scope for this plan; the `/decay` axis is the photon-decay histogram, not acquisition time, and cross-format timepoint binding is deferred.

### Deferred to Follow-Up Work

- Cross-format timepoint binding (matching `.bin` TCSPC files to intensity by timepoint as well as channel): a future extension of `src/percell4/domain/io/cross_format.py`.
- Lineage-tree panel (arboretum-style dendrogram view): a separate plan; this plan delivers lineage data + napari Tracks-graph visualization only.

---

## Context & Research

### Relevant Code and Patterns

- **Token layer already parses timepoints** — `src/percell4/domain/io/models.py` (`TokenConfig.timepoint = r"_t(\d+)"`, `ScanResult.timepoints: set[str]`), `src/percell4/domain/io/scanner.py` (`FileScanner._parse_tokens`, `scan()`), `src/percell4/domain/io/discovery.py` (already iterates `("channel","timepoint","z_slice","tile")` and strips the timepoint token when deriving dataset/FOV names, so `_tN`-varying files already group into one dataset name).
- **Import assembly** — `src/percell4/adapters/importer.py::import_dataset` has `_group_by_channel` / `_group_by_z` but no `_group_by_timepoint`; `src/percell4/domain/io/assembler.py` (`assemble_channels` → `(C,H,W)`, `project_z`, `assemble_tiles`) is pure-numpy and is where a time-stacking path is added. `importer.py` is canonical for `decay-write-path` and `channel-name-default-ch-prefix` (T1).
- **Store invariants** — `src/percell4/store.py`: `write_labels`/`write_mask` enforce `ndim == 2` (lines ~405, ~444) — the single biggest blocker; `_infer_native_dims` derives `native_shape` from the last two dims of `/intensity`; `_choose_chunks` (2D→`(256,256)`, decay-3D→`(64,64,T)`, other-3D→`(1,256,256)`); `read_array`/`read_channel`/`read_labels`/`read_mask` apply `view_bin` downsampling (`/intensity`→sum, `/labels`→mode, `/masks`→majority). T1; canonical for `atomic-write-contract`, `decay-write-path`, `one-payload-type-per-h5-group`.
- **Session + bin pattern** — `src/percell4/application/session.py`: five selection fields plus `_active_bin`; `set_active_bin` + `Event.ACTIVE_BIN_CHANGED` is the exact template for `active_timepoint`. `set_dataset` auto-selects/reset sequence. `filtered_df` filters `_measurements` by `label`. Canonical for `session-state-event-emission`.
- **Model bridge** — `src/percell4/model.py::CellDataModel` re-emits session events as one `state_changed` Qt signal carrying `StateChange` flags (`data/selection/filter/segmentation/mask/channel/bin/*_list`); add a `timepoint` flag mirroring `bin`.
- **napari adapter** — `src/percell4/adapters/napari_viewer.py` (`show_dataset`, `add_image/add_labels/add_mask`), `src/percell4/gui/viewer.py` (`ViewerWindow`, `_on_state_changed`, `_push_active_layer_to_napari`, `_update_label_display` via `DirectLabelColormap`). napari auto-creates a dims slider for any extra leading axis, so a `(T,...)` array yields the slider for free. Canonical for `session-to-napari-one-way-push` and `keystroke-binding-on-napari-viewer`.
- **Segmentation** — `src/percell4/application/use_cases/segment_cells.py` (`SegmentCells.finalize`: post-process → `write_labels` → refresh → set_active, the Creator four-step), `src/percell4/adapters/cellpose.py::run_cellpose` (single 2D image → 2D int32). Port: `src/percell4/ports/segmenter.py`.
- **Measurement** — `src/percell4/domain/measure/measurer.py::measure_cells` (pure: `(H,W)` image + `(H,W)` labels → DataFrame, cell identity = `label` int), `src/percell4/application/use_cases/measure_cells.py` (reads channel + active segmentation + optional mask, writes `/measurements`, stamps `bin_at_measure`, calls `session.set_measurements`).
- **Resource model** — named resources are HDF5 group children (`/labels/<name>`, `/masks/<name>`) + `/metadata` name-lists; new resource kinds go through `DatasetStore` write/list + `src/percell4/ports/dataset_repository.py` + `Session.refresh_resource_lists`. A `/tracks/<name>` lineage table follows this pattern.

### Institutional Learnings

- `docs/solutions/logic-errors/tiff-pending-channel-name-prefix-mismatch-2026-05-21.md` — a token-derived id (`"02"`) diverged from the stored name (`"ch02"`) and blew up after expensive segmentation. **Mirror:** one canonical timepoint-name derivation helper used on both writer and reader sides so `"3"` never diverges from a stored `"t03"` / index `3`. Dialog-driven paths aren't covered by pytest — smoke-test with `python main.py`.
- `one-payload-type-per-h5-group` (`docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`) — keep one payload type + fixed dtype per group; tag layers so masks are never read as segmentations. Adding a T axis must not weaken this.
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md` — new session state (`active_timepoint`) and a new derived dimension must route invalidation through the single canonical session-event path, not ad hoc.
- `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md` — highlight selected cells via `DirectLabelColormap`, never by mutating `labels_layer.data`. Selection following a tracked cell across the slider reuses this.
- `docs/solutions/logic-errors/numpy-isin-fails-with-python-sets.md` — `np.isin(array, python_set)` silently returns all-False on NumPy 2.x; use a list/array at every membership test against label arrays (tracking/lineage will have many).
- `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md` — tracking output (new tracked segmentation + lineage) is a Creator: store → viewer → refresh → set_active.
- **T1 audit:** `domain/io/{scanner,discovery,models,assembler,cross_format}.py`, `adapters/importer.py`, `store.py`, and any new `application/use_cases/` are T1. Run `python3 scripts/learnings_applicability.py <path>` per file before editing; invoke `compound-engineering:ce-learnings-researcher` for non-trivial edits; PreToolUse hook is warn-only. Update `docs/audits/gui-element-classification.yaml`, `session-mutation-graph.md`, `subscriber-rebind-matrix.md`, and `keystroke-binding-audit.md` for the new `active_timepoint` Selector and any new session field/event. New patterns (timepoint HDF5 layout, tracking/lineage) should be captured via `/ce-compound` after landing.

### External References

- **laptrack** (BSD-3, pandas-native): `OverLapTrack(cutoff=..., splitting_cutoff=...).predict_overlap_dataframe(labels)` where `labels` is a list/stack of integer label masks → `(track_df, split_df, merge_df)`. `track_df` has MultiIndex `(frame, label)` with columns `tree_id`, `track_id`; `split_df` has columns `parent_track_id`, `child_track_id` (the division records). `convert_split_merge_df_to_napari_graph(split_df, merge_df)` → the napari `graph` dict. https://laptrack.readthedocs.io/en/stable/examples/overlap_tracking.html , https://github.com/yfukai/laptrack
- **napari Tracks layer** — `viewer.add_tracks(data, graph=...)`; `data` shape `(N,4)` columns `[track_id, t, y, x]`; `graph` is `{child_track_id: [parent_track_id]}`. https://napari.org/dev/howtos/layers/tracks.html
- **CTC `man_track` format** (`L B E P` = label, begin frame, end frame, parent) — an interchange shape worth mirroring in the `/tracks` table for portability.
- Jaqaman et al. 2008 (LAP framework) — algorithmic basis for overlap/gap-closing/splitting that laptrack implements.

---

## Key Technical Decisions

- **Tracking engine: laptrack `OverLapTrack`.** Pure-Python, BSD-3, zero config, no GPU; takes Cellpose label masks directly and returns pandas frames that map onto the existing per-cell DataFrame and napari graph. (User-confirmed; alternatives btrack/trackastra deferred.)
- **HDF5 layout: leading `T` axis on existing datasets**, added only when `n_timepoints > 1`. `native_shape` stays `(H,W)`; a new `n_timepoints` metadata attr records `T`. Single-timepoint datasets are byte-for-byte unchanged. (User-confirmed.)
- **Track identity: relabel into a new segmentation resource** where label value == `track_id`, stable across all timepoints; raw per-frame Cellpose labels are preserved as a separate resource. Measurements gain `timepoint`/`track_id`/`parent_track_id`/`tree_id`; a `/tracks/<name>` lineage table stores parent→daughter links. (User-confirmed.)
- **Slider as a Selector.** A new `session.active_timepoint` field is mutated only by the timepoint Selector; napari `dims.current_step` ↔ session sync is a controlled one-way push (session→napari), with the napari→session direction allowed *only* through the Selector discipline, never via raw layer/dims events.
- **Tracking is a Creator step run after all timepoints are segmented**, not automatically per frame. Segmentation gains a "segment all timepoints" mode that produces a `(T,H,W)` raw-label resource; tracking consumes that resource.
- **Single canonical timepoint-name helper** (`t{index:02d}` ⇄ index ⇄ token) used on both writer and reader sides, mirroring the channel-name-prefix fix.
- **Death/exit/appearance are emergent, not enforced.** Differing per-timepoint label counts are expected output (a track that ends = death/exit; a track that begins mid-series = appearance/division daughter). No invariant forces equal counts; R5 is satisfied by the track records, not a check.

---

## Open Questions

### Resolved During Planning

- Which tracking library — laptrack (user-confirmed).
- HDF5 storage shape — leading T axis, gated on `n_timepoints > 1` (user-confirmed).
- How "matching IDs across timepoints" is expressed — relabel into a new tracked segmentation + measurement columns + lineage table (user-confirmed).
- Backward compatibility — single-timepoint datasets keep the current 2D layout and skip all time-lapse surfaces, including no `timepoint` measurement column.
- Cell-selection identity — keep `CellId` a scalar int; for the tracked resource the label value equals `track_id` (stable across time), so existing selection plumbing follows a cell across the slider unchanged; raw-resource selection is frame-scoped. The `(timepoint, label)` tuple-identity option was rejected (excessive churn, no benefit). (Resolved during deepening — see U8.)
- Display dispatch — the three `ndim`-based dispatch sites (`interfaces/gui/main_window.py`, `hdf5_store.py` `build_view` + `read_channel_images`) must disambiguate a leading T axis from a leading C axis via `n_timepoints`; they cannot infer it from shape alone. (Resolved during deepening — see U4.)

### Deferred to Implementation

- Exact laptrack cost coefficients / `cutoff` / `splitting_cutoff` defaults — tune against a real time-lapse sample during implementation; expose as config with sensible defaults rather than hard-coding final values now.
- Whether `read_channel`/`read_array` should return the full `(T,...)` stack and let the viewer slice, or accept a `timepoint` argument and return one frame — decide when wiring the viewer push against napari's dims behavior (napari prefers the full stack so the slider works natively; per-frame reads matter for measurement/segmentation). Likely both: full-stack read for the viewer, per-frame slice for compute.
- `view_bin` interaction with the time axis for `/labels`/`/masks` (mode/majority per frame) — confirm the per-frame application path once the `(T,...)` read shape is settled.
- Final `/tracks/<name>` on-disk encoding (CSV string like `/measurements` vs structured table) — choose during U7 to match the existing `/measurements` serialization convention.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

Data flow, import → view → segment → track → measure → visualize:

```
.tiff files (..._t00_ch00.tif, ..._t01_ch00.tif, ...)
        │  scanner already parses tokens["timepoint"]
        ▼
import_dataset:  _group_by_timepoint  →  per-t assemble_channels
        │                                   (each t → (C,H,W))
        ▼  stack over t  (only if n_timepoints > 1)
HDF5:  /intensity (T,C,H,W)   attrs: native_shape=(H,W), n_timepoints=T
        ▼
napari viewer:  add_image((T,C,H,W))  → dims slider (R3, free)
        │  session.active_timepoint  ◄── timepoint Selector (one-way push)
        ▼
SegmentCells (all timepoints):  run_cellpose per frame
        ▼
HDF5:  /labels/cellpose_raw (T,H,W)   (per-frame Cellpose ids)
        ▼
TrackCells (Creator, laptrack OverLapTrack):
   predict_overlap_dataframe([labels[t] for t in T])
        →  track_df (frame,label → track_id, tree_id),  split_df (parent→child)
   relabel each frame so pixel value == track_id
        ▼
HDF5:  /labels/tracked (T,H,W)   (label == track_id, stable across T)   ← R4
       /tracks/<name>  lineage table (track_id, begin_t, end_t, parent_track_id, tree_id)  ← R6
        ▼
MeasureCells (per timepoint, on /labels/tracked):
   rows tagged with timepoint, joined to track_id/parent_track_id/tree_id   ← R7
        ▼
napari:  add_tracks(data[track_id,t,y,x], graph={child:[parent]})         ← R8
         selection follows track_id across the slider (DirectLabelColormap)
```

Lineage at a division (track 1 → daughters 4, 5 at frame f):

```
track_df rows:          split_df:                 napari graph:
(f-1, ..)=track 1       parent=1, child=4         {4: [1], 5: [1]}
(f,   ..)=track 4       parent=1, child=5
(f,   ..)=track 5       (track 1 ends at f-1)
```

---

## Implementation Units

### Phase 1 — Timepoint axis: import, store, session, view

- U1. **Time-aware import: group files by timepoint and assemble a leading-T stack**

**Goal:** Make `import_dataset` consume the already-parsed `tokens["timepoint"]`, group files by timepoint, assemble each timepoint into its `(C,H,W)` (or `(H,W)`) plane, and stack into `(T,C,H,W)` when more than one timepoint exists. Establish the canonical timepoint-name/index helper.

**Requirements:** R1, R2

**Dependencies:** None

**Files:**
- Modify: `src/percell4/adapters/importer.py` (add `_group_by_timepoint`, thread timepoint through the assembly path, stack frames, pass `n_timepoints`)
- Modify: `src/percell4/domain/io/assembler.py` (add a pure time-stacking helper, e.g. `stack_timepoints(frames) -> (T, ...)`)
- Create: `src/percell4/domain/io/timepoints.py` (canonical helpers: `timepoint_label(index) -> "t{NN}"`, `parse_timepoint_token(token) -> index`, sorted-unique ordering of `ScanResult.timepoints`)
- Test: `tests/test_io/test_importer.py` (extend), `tests/test_io/test_timepoints.py` (new), `tests/test_io/test_assembler.py` (extend if present, else add)

**Approach:**
- Sort timepoint tokens numerically (not lexically) so `_t2` < `_t10`; the canonical helper owns this ordering and the `index ⇄ "tNN"` mapping used everywhere downstream.
- For each timepoint, reuse the existing per-channel assembly (`assemble_channels`) so multi-channel + multi-timepoint composes as `(T,C,H,W)`; single channel → `(T,H,W)`.
- Gate the time axis on `n_timepoints > 1`: a lone timepoint (or files with no `_t` token) produces today's exact 2D/`(C,H,W)` output.
- Pass `n_timepoints` to the store create call (U2 consumes it).

**Execution note:** Characterization-first — capture the current single-timepoint import output shape in a test before adding the time path, to prove backward compatibility is preserved. T1 file: run `python3 scripts/learnings_applicability.py src/percell4/adapters/importer.py` and consult `decay-write-path` / `channel-name-default-ch-prefix` before editing.

**Patterns to follow:** `_group_by_channel` / `_group_by_z` in `importer.py`; numeric token sorting; the channel-name canonical-helper pattern from `tiff-pending-channel-name-prefix-mismatch`.

**Test scenarios:**
- Covers R1. Happy path: files `a_t00_ch00.tif … a_t02_ch00.tif` import as one dataset with `n_timepoints == 3` and `/intensity` shape `(3,H,W)` (or `(3,C,H,W)` multi-channel).
- Happy path: two channels × three timepoints → `(3,2,H,W)` with channels in stable order.
- Edge case: numeric ordering — `_t2` and `_t10` order as 2 then 10, not lexical.
- Edge case (backward compat): single timepoint / no `_t` token → output is byte-identical shape to current behavior (`(H,W)` or `(C,H,W)`), no T axis, `n_timepoints == 1`.
- Edge case: a timepoint missing one channel → surfaced (raise or recorded), never silently mis-stacked (mirror `cross_format` never-crash-but-report contract).
- `timepoints.py`: round-trip `parse_timepoint_token(timepoint_label(i)) == i`; sorted ordering of an unsorted token set.

**Verification:** Importing a `_tN` series yields a `(T,...)` `/intensity`; a single-timepoint folder is unchanged; the timepoint helper is the only place that maps token↔index↔name.

---

- U2. **HDF5 store: time-aware layout for intensity, labels, and masks**

**Goal:** Allow a leading time axis in the store: relax the 2D-only enforcement in `write_labels`/`write_mask` to also accept `(T,H,W)`, persist `n_timepoints`, keep `native_shape` as `(H,W)`, choose sane chunks for time-stacked arrays, and make `view_bin` apply per frame. Provide read paths that return either the full stack (for the viewer) or a single timepoint slice (for compute).

**Requirements:** R2

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/store.py` (`write_labels`, `write_mask`, `_infer_native_dims`/`_infer_bin_metadata`, `_choose_chunks`, `read_array`/`read_channel`/`read_labels`/`read_mask`, metadata attrs)
- Modify: `src/percell4/domain/io/view_bin.py` (**`mode_labels` is currently strictly 2D** — it reshapes to `(h_b, k, w_b, k)` and allocates a 2D `out`; it must loop/vectorize over any leading axes so `(T,H,W)` labels survive `view_bin > 1`. `sum_bin_2d` and `majority_vote_mask` already handle leading dims via `arr.shape[-2:]`; only `mode_labels` is the blocker)
- Modify: `src/percell4/adapters/hdf5_store.py` (handle metadata: surface `n_timepoints`; `_build_handle_metadata`)
- Test: `tests/test_store.py` (extend), `tests/test_io/test_view_bin.py` (extend/add — `mode_labels` on `(T,H,W)`), `tests/test_io/test_store_append.py` (regression — ensure single-timepoint invariants hold)

**Approach:**
- `write_labels`/`write_mask`: accept `ndim == 2` (unchanged) **or** `ndim == 3` where the trailing two dims equal `native_shape` **and** `shape[0] == n_timepoints` (cross-check the persisted attr, not just the trailing dims — a `(3,H,W)` label write on a 5-timepoint dataset must be rejected, not silently accepted as a malformed `(C,H,W)`). Keep dtype enforcement (int32 / uint8) and one-payload-per-group intact; extend the existing `MetadataConsistencyError`/`LayerSizeMismatchError` machinery to the `n_timepoints` invariant.
- `native_shape` continues to come from the last two dims; add `n_timepoints` as a metadata attr. **Read it with the `setdefault`-on-read pattern already used for `creation_bin` (default 1 when absent)** so existing `.h5` files without the attr load correctly. `_infer_native_dims` must treat `(T,C,H,W)` and `(T,H,W)` correctly (last two dims). Note: newly-imported single-timepoint files gain an `n_timepoints=1` attr, so "byte-for-byte unchanged" applies to *array layout*, not literally every metadata byte — the array shapes and read behavior are unchanged.
- `_choose_chunks`: for time-stacked non-decay arrays, chunk one frame at a time (`(1,...,256,256)`) so frame reads are cheap.
- `view_bin`: apply the existing per-rule downsampling **per frame** along T (`/intensity`→sum already leading-dim-safe, `/labels`→mode via the fixed `mode_labels`, `/masks`→majority already leading-dim-safe), preserving the T axis.
- Add a `timepoint: int | None` parameter (or a sibling `read_*_frame`) to the label/mask/array reads so compute can pull one frame; the viewer uses the full-stack read.

**Execution note:** T1 canonical file — run `learnings_applicability.py src/percell4/store.py`; honor `atomic-write-contract`, `decay-write-path`, `one-payload-type-per-h5-group`. Do not conflate the `/decay` histogram T with acquisition T.

**Patterns to follow:** existing `write_labels`/`write_mask` validation; `_apply_view_bin` dispatch; `read_channel`'s ndim branching.

**Test scenarios:**
- Covers R2. Happy path: `write_labels(name, (T,H,W) int32)` then `read_labels` returns the same stack; full-stack and single-frame reads agree.
- Happy path: `/intensity (T,C,H,W)` round-trips; `native_shape == (H,W)`, `n_timepoints == T`.
- Edge case (backward compat): 2D label/mask writes/reads behave exactly as before; `n_timepoints` defaults to 1; existing `test_store_append` regressions pass.
- Error path: `(T,H,W)` with trailing dims ≠ `native_shape` raises `LayerSizeMismatchError`/`ValueError`; `(T,H,W)` with `T ≠ n_timepoints` is rejected; a 4D label array (not a valid label shape) is rejected.
- Edge case: `view_bin=2` on `(T,H,W)` labels downsamples each frame by mode and keeps T (regression-guards the `mode_labels` 2D→leading-dim fix); intensity sums per frame; masks majority-vote per frame.
- Edge case: chunks for `(T,H,W)` are per-frame.
- Edge case (backward compat): an existing `.h5` with no `n_timepoints` attr reads as `n_timepoints == 1` (setdefault-on-read).

**Verification:** Time-stacked intensity/labels/masks persist and read back per-frame and whole-stack; single-timepoint datasets are bit-unchanged; mask/label payload separation and dtypes are preserved.

---

- U3. **Session `active_timepoint` field + StateChange flag**

**Goal:** Add a sixth selection field, `active_timepoint`, to `Session`, mutated only by a Selector, with its own event; bridge it through `CellDataModel` as a `timepoint` `StateChange` flag. Wire it into `set_dataset` reset/auto-select (default to timepoint 0, clamp to `[0, n_timepoints-1]`).

**Requirements:** R3 (and prerequisite for R7 — this unit adds the `active_timepoint` field; U8 is where measurement identity actually changes)

**Dependencies:** U2

**Files:**
- Modify: `src/percell4/application/session.py` (`_active_timepoint`, property, `set_active_timepoint`, `Event.ACTIVE_TIMEPOINT_CHANGED`, `set_dataset` reset)
- Modify: `src/percell4/model.py` (add `timepoint` to `StateChange`, map the new event)
- Modify: `docs/audits/session-mutation-graph.md`, `docs/audits/gui-element-classification.yaml` (record the new field + Selector)
- Test: `tests/test_session.py` (extend)

**Approach:**
- Mirror `set_active_bin` / `Event.ACTIVE_BIN_CHANGED` exactly: store int, validate range against the dataset's `n_timepoints`, emit only on change.
- `set_dataset` resets `active_timepoint` to 0 and emits the change event when nonzero previously; auto-select sequence ordering matches the documented bin handling.
- Route all invalidation through the canonical session-event path (per the staleness learning) — no ad-hoc mutation.

**Patterns to follow:** `set_active_bin`, `Event.ACTIVE_BIN_CHANGED`, `StateChange.bin`.

**Test scenarios:**
- Covers R3. Happy path: `set_active_timepoint(2)` emits `ACTIVE_TIMEPOINT_CHANGED`; re-setting the same value emits nothing.
- Edge case: out-of-range index clamps or raises (match `active_bin` policy); single-timepoint dataset keeps `active_timepoint == 0`.
- Edge case: `set_dataset` resets `active_timepoint` to 0 and emits appropriately.
- Integration: `CellDataModel` emits `state_changed` with `StateChange.timepoint == True` and other flags False when only the timepoint changes.

**Verification:** `active_timepoint` behaves like `active_bin`; the model surfaces a clean `timepoint` flag; audits reflect the new Selector field.

---

- U4. **napari viewer: fix the ndim display dispatch + time-stacked layers + timepoint slider as a Selector**

**Goal:** Make time-stacked arrays display as a single layer per channel with a napari dims slider (R3), bind the slider through the session discipline (session→napari sets `dims.current_step`; the user moving the slider updates `session.active_timepoint` via a Selector path), and — critically — fix the **existing `ndim`-based display dispatch that currently splits any leading axis into separate channel layers**, which would otherwise turn a `(T,H,W)` stack into T bogus channel layers and a `(T,C,H,W)` stack into one mislabeled 4D "Intensity" layer.

**Requirements:** R3

**Dependencies:** U3

**Files:**
- Modify: `src/percell4/interfaces/gui/main_window.py` (lines ~1047–1059 — **the real GUI display path**; reads `/intensity` and dispatches on `ndim`. Currently `ndim==3 and shape[0]<=20` loops axis 0 as channels and `else` pushes the whole array. Must use `n_timepoints`/`channel_names` to distinguish a leading **T** axis from a leading **C** axis and hand napari the stacked `(T,...)` array per channel)
- Modify: `src/percell4/adapters/hdf5_store.py` (`build_view` lines ~74–82 and `read_channel_images` lines ~111–119 — the **two other copies** of the same `ndim` dispatch; `read_channel_images` splits *any* 3D regardless of `shape[0]`. Same disambiguation fix)
- Modify: `src/percell4/adapters/napari_viewer.py` (`show_dataset`/`add_image`/`add_labels`/`add_mask` pass full `(T,...)` arrays per channel)
- Modify: `src/percell4/gui/viewer.py` (`_on_state_changed` handles `timepoint`; subscribe to napari `dims.events.current_step` and route to `session.set_active_timepoint` guarded by a **dedicated** originator flag; one-way push of `active_timepoint` → `dims.current_step`)
- Modify: `docs/audits/subscriber-rebind-matrix.md`, `docs/audits/keystroke-binding-audit.md` (if a scrub keystroke is added)
- Test: `tests/test_adapters/test_hdf5_store_view.py` (new — dispatch on `(T,H,W)`/`(T,C,H,W)`/`(C,H,W)`/`(H,W)`), `tests/test_gui_workflows/test_session_to_napari_push.py` (extend), new `tests/test_gui_workflows/test_timepoint_slider_sync.py`

**Approach:**
- **Disambiguation is load-bearing and cannot be done from `ndim`/`shape` alone:** `(T,H,W)` (one channel, T frames) and `(C,H,W)` (C channels) are shape-indistinguishable. All three dispatch sites must consult `n_timepoints` (and `len(channel_names)`) to decide: leading axis is C → split into per-channel layers (today's behavior); leading axis is T → keep stacked and hand napari one layer (`(T,H,W)`); both present `(T,C,H,W)` → split on C, each channel layer is `(T,H,W)` so napari shows a single T slider.
- Hand napari the full per-channel stack; do not slice in the adapter — napari owns the slider. The active-timepoint push sets `viewer.dims.current_step` on the time axis.
- **The dims→session write uses a SEPARATE originator guard (e.g. `_timepoint_originator`), not the shared `_is_originator`.** The shared flag is already in use across label-selection forwarding, `_push_active_layer_to_napari`, and `_update_label_display`; reusing it would let an in-flight selection/segmentation push and the timepoint echo-suppression stomp each other, producing intermittent feedback loops or dropped updates. This is a `dims.events.current_step` source, which is genuinely distinct from the forbidden "napari→session **layer-list selection** events" rule — so the Selector path is permitted; document the distinction in the gui audit.
- Selection highlighting (`_update_label_display`, `DirectLabelColormap`) continues to operate per displayed frame; never mutate `labels_layer.data`.

**Execution note:** `viewer.py` is canonical for `session-to-napari-one-way-push` and `keystroke-binding-on-napari-viewer`; keep the push one-way and bind any scrub key above the conflict in napari's keymap chain. The three dispatch sites are easy to fix in one and miss the others — change all three together and cover them with the new adapter test.

**Patterns to follow:** `_push_active_layer_to_napari`, `_on_state_changed` channel/bin handling, the originator-guard pattern (but a fresh flag).

**Test scenarios:**
- Covers R3. Integration: loading a `(T,H,W)` single-channel dataset shows **one** image layer with a napari dims slider of `T` steps — not T separate channel layers.
- Covers R3. Integration: a `(T,C,H,W)` dataset shows **C** image layers each with a single T slider, channel-named correctly — not one 4D "Intensity" layer.
- Edge case (backward compat): `(C,H,W)` multichannel still splits into C channel layers; `(H,W)` single image unchanged; `read_channel_images` no longer splits a `(T,H,W)` stack into frames.
- Integration: `session.set_active_timepoint(k)` moves `viewer.dims.current_step` to `k` (one-way push), with the dedicated guard preventing a feedback loop and not interfering with a concurrent label-selection push.
- Integration: moving the napari slider updates `session.active_timepoint` exactly once (Selector path), no echo back.
- Edge case: single-timepoint dataset shows no time slider and never emits timepoint changes.

**Verification:** A time-lapse displays as stacked per-channel layers with a working slider across all three dispatch sites; multichannel/single-timepoint display is unchanged; slider↔`active_timepoint` stay in sync without loops and without disturbing selection state.

---

### Phase 2 — Per-timepoint segmentation

- U5. **Segment all timepoints into a raw `(T,H,W)` label resource**

**Goal:** Extend segmentation to run Cellpose on every timepoint of the active channel stack and write a single `(T,H,W)` raw-label resource, following the Creator four-step. Single-timepoint datasets keep today's 2D behavior.

**Requirements:** R1 (consumes the timepoint metadata from U1), prerequisite for R4

**Dependencies:** U1, U2, U4

**Files:**
- Modify: `src/percell4/application/use_cases/segment_cells.py` (`SegmentCells`: detect `n_timepoints`, loop frames, post-process each, stack, `write_labels((T,H,W))`)
- Modify: `src/percell4/adapters/cellpose.py` (no API change expected; called per frame) and possibly `src/percell4/gui/segmentation_panel.py` (a "segment all timepoints" affordance / progress)
- Modify: `src/percell4/gui/workers.py` (QThread progress over T, to keep UI responsive)
- Test: `tests/test_segment/test_segment_cells_finalize.py` (extend)

**Approach:**
- Branch on `n_timepoints`: when `> 1`, loop frames and write a `(T,H,W)` resource; when `== 1`, run Cellpose once and write a 2D `(H,W)` resource exactly as today (no singleton T axis). The backward-compat path is an explicit special case, not an incidental `(1,H,W)` write.
- Reuse `run_cellpose` per frame; apply the existing post-process (`filter_edge_cells`, `filter_small_cells`, `relabel_sequential`) per frame. These raw per-frame label ids are intentionally inconsistent across time — tracking (U7) makes them consistent.
- Stack to `(T,H,W)` and write one resource (default name e.g. `cellpose_raw` + bin suffix). Creator: store → viewer → refresh lists → set_active.
- Run inference in the existing QThread worker with per-frame progress (T inferences can be slow).

**Patterns to follow:** `SegmentCells.finalize` Creator sequence; `bin_suffix` naming; `gui/workers.py` worker pattern.

**Test scenarios:**
- Happy path: a `(3,H,W)` intensity stack → `(3,H,W)` int32 label resource; each frame independently segmented.
- Edge case (backward compat): single-timepoint dataset → 2D label resource exactly as today.
- Edge case: a frame with zero cells yields an all-background frame without crashing the stack write.
- Integration: after finalize, the new resource is selected and visible in napari across the slider (Creator four-step holds).

**Verification:** Segmenting a time-lapse produces one `(T,H,W)` raw-label resource viewable across the slider; single-timepoint path unchanged.

---

### Phase 3 — Tracking, lineage, measurement, visualization

- U6. **laptrack tracking: domain port + adapter (label stack → track/split frames)**

**Goal:** Introduce a tracking abstraction. A narrow `Tracker` port takes an ordered list/stack of label masks and returns track and split (division) records; a laptrack-backed adapter implements it with `OverLapTrack.predict_overlap_dataframe`. Add `laptrack` as a dependency.

**Requirements:** R4, R5, R6

**Dependencies:** U5

**Files:**
- Create: `src/percell4/ports/tracker.py` (`Tracker` Protocol + result dataclass: `track_df`, `split_df`)
- Create: `src/percell4/adapters/laptrack_tracker.py` (implements `Tracker` via `OverLapTrack`)
- Create: `src/percell4/domain/tracking/__init__.py`, `src/percell4/domain/tracking/lineage.py` (pure helpers: build parent→daughter lineage records from `split_df` + `track_df`; CTC-style `(track_id, begin_t, end_t, parent_track_id, tree_id)` rows; pure, no laptrack import)
- Modify: `pyproject.toml` (`[project.dependencies]` add `laptrack` **with a pinned version range**, e.g. `laptrack>=0.16,<0.18`; and add `"laptrack"` to the `domain/` contract `forbidden_modules` under `[tool.importlinter]` line ~105 so the boundary is machine-enforced, not merely intended)
- Test: `tests/test_adapters/test_laptrack_tracker.py`, `tests/test_domain/test_lineage.py`

**Approach:**
- **Verify the laptrack API against the pinned version before writing the U6 adapter tests.** laptrack is not currently installed, and the overlap-tracking surface is version-sensitive: confirm whether the entry point is `OverLapTrack.predict_overlap_dataframe(labels)` returning `(track_df, split_df, merge_df)` with `track_df` MultiIndex `(frame,label)` / columns `tree_id,track_id` and `split_df` columns `parent_track_id,child_track_id`, vs the coordinate-based `LapTrack.predict_dataframe` path. Pin the version, install, and read the actual return arity/column names; the test scenarios below assume the documented overlap API and must be reconciled with the installed release before they are written.
- Keep the laptrack import isolated to the adapter (hexagonal boundary; domain stays library-free). The port returns plain pandas frames so the use case and lineage helpers never touch laptrack — this isolation means if the API differs only `adapters/laptrack_tracker.py` changes.
- Expose `cutoff` / `splitting_cutoff` / cost coefficients as adapter config with documented defaults; final tuning is deferred to implementation against a real sample.
- `domain/tracking/lineage.py` converts `track_df` (`(frame,label)`→`track_id`,`tree_id`) + `split_df` (`parent_track_id`,`child_track_id`) into the lineage table and the napari graph dict shape (`{child:[parent]}`), using lists (not sets) for any `np.isin`. (`frame` here is the timepoint index — see Terminology.)

**Execution note:** New code under `domain/`/`adapters/`; respect import-linter contracts (domain may not import laptrack/h5py/qt — add laptrack to the forbidden list per Files). Test-first for the pure lineage helper.

**Patterns to follow:** `src/percell4/ports/segmenter.py` + `src/percell4/adapters/cellpose.py` (port/adapter pairing); `cross_format` never-crash-report contract for unmatched/ambiguous.

**Test scenarios:**
- Covers R4. Happy path: a synthetic 3-frame label stack of one cell that overlaps frame-to-frame yields one track spanning all frames.
- Covers R5. Edge case: a cell present in frames 0–1 then absent → its track ends at frame 1 (death/exit), no error.
- Covers R5. Edge case: a cell appearing only at frame 2 → a new track beginning at frame 2.
- Covers R6. Happy path: one cell in frame 0 that becomes two overlapping cells in frame 1 → `split_df` has one parent with two children; lineage table links parent→both daughters; napari graph `{d1:[p], d2:[p]}`.
- Lineage helper (pure): `begin_t`/`end_t`/`parent_track_id`/`tree_id` computed correctly for a multi-generation tree; `parent_track_id` is 0/None for roots.
- Error path: empty stack / all-background frames → empty track/split frames, no crash.

**Verification:** Given label stacks, the adapter returns consistent track ids and division records; the pure lineage helper builds correct parent→daughter rows and the napari graph dict; laptrack stays out of the domain.

---

- U7. **TrackCells use case: relabel into tracked segmentation + lineage table (Creator)**

**Goal:** Orchestrate tracking: read the raw `(T,H,W)` labels, run the `Tracker`, relabel every frame so each pixel's value equals its `track_id` (stable across T), write a new `/labels/tracked` resource, and persist the lineage table to `/tracks/<name>`. Follow the Creator four-step and auto-select the tracked segmentation.

**Requirements:** R4, R5, R6

**Dependencies:** U6

**Files:**
- Create: `src/percell4/application/use_cases/track_cells.py` (`TrackCells`)
- Modify: `src/percell4/store.py` (add `write_tracks`/`read_tracks`/`list_tracks` for `/tracks/<name>`, following the `/measurements` serialization convention)
- Modify: `src/percell4/adapters/hdf5_store.py` + `src/percell4/ports/dataset_repository.py` (expose track read/write/list)
- Modify: `src/percell4/application/session.py` (`refresh_resource_lists` includes tracks; optional `Event.TRACK_LIST_CHANGED`)
- Test: `tests/test_application/test_track_cells.py`, `tests/test_store.py` (tracks round-trip)

**Approach:**
- Relabel per frame using the `track_df` `(frame,label)`→`track_id` map; pixels of cells with no track (rare, e.g. filtered) go to background or keep a stable high id — decide and document. Result: `/labels/tracked (T,H,W)` where label == track_id everywhere.
- Lineage table columns: `track_id, begin_t, end_t, parent_track_id, tree_id` (CTC-style). Serialize like `/measurements` (string table) per the deferred encoding decision.
- Creator four-step: write tracked labels + tracks table → push to viewer → refresh lists → set_active(tracked). New `/tracks` is a distinct payload group (one-payload-per-group preserved).

**Execution note:** T1 (`store.py`); run `learnings_applicability.py` and honor `atomic-write-contract`. Use the canonical timepoint helper for any frame indexing.

**Patterns to follow:** Creator four-step (`creator-contract-four-step-sequence`); `/measurements` write/serialize in `store.py`; `relabel_sequential` usage in segmentation post-process.

**Test scenarios:**
- Covers R4. Happy path: tracking a raw `(T,H,W)` stack produces `/labels/tracked` where the same physical cell has identical label value across all frames.
- Covers R6. Integration: a division produces daughter labels in the tracked resource and a `/tracks` row linking parent→daughters.
- Covers R5. Edge case: a cell that dies has a track with `end_t` < last frame; its label is absent in later frames of the tracked resource.
- Edge case: untracked/filtered pixels handled per the documented rule (background vs reserved id), deterministically.
- Integration: `/tracks/<name>` round-trips through store read/write; `refresh_resource_lists` surfaces it; tracked segmentation auto-selected and visible.
- Error path: tracking a single-timepoint dataset is disallowed/no-op with a clear message (tracking needs ≥2 timepoints).

**Verification:** A tracked segmentation with stable cross-time labels and a persisted lineage table exist after the use case; the resource is selected and the Creator four-step holds.

---

- U8. **Time-aware measurement: per-timepoint rows + track/lineage columns + `(timepoint, label)` identity**

**Goal:** Measure every timepoint and tag rows with `timepoint`; join `track_id`, `parent_track_id`, `tree_id` from the lineage data when measuring a tracked segmentation; make selection/filter follow a tracked cell across frames **by keying on the existing scalar label value, which for the tracked resource already equals `track_id`** — so selection plumbing stays unchanged.

**Requirements:** R7, R5

**Dependencies:** U7, U3

**Files:**
- Modify: `src/percell4/application/use_cases/measure_cells.py` (loop timepoints, read per-frame labels/channel, concat with a `timepoint` column; join track columns when the active segmentation is tracked)
- Modify: `src/percell4/domain/measure/measurer.py` (no identity change to the pure function; the use case adds `timepoint`; confirm it stays single-frame pure)
- Modify: `src/percell4/application/session.py` (`filtered_df`, `set_measurements` pruning made timepoint-aware while keeping `CellId` a scalar int — see decision below)
- Test: `tests/test_measure/test_measurer.py` (extend), `tests/test_application/test_measure_cells_timelapse.py` (new), `tests/test_session.py` (identity)

**Approach:**
- Keep `measure_cells` pure and single-frame; the use case iterates `range(n_timepoints)`, reads each frame (per-frame store read from U2), measures, and stamps `timepoint`. `bin_at_measure` stamping unchanged.
- When the active segmentation is the tracked resource, label values **are** track ids; join `parent_track_id`/`tree_id`/`begin_t`/`end_t` from `/tracks`. When measuring raw per-frame labels, only `timepoint` is added (no track columns).
- **Identity decision (resolved, not deferred): keep `CellId` a scalar int and do NOT change the selection forwarding contract.** The viewer's `selected_label` is a scalar napari label value; for the tracked resource that value equals `track_id` and is stable across timepoints, so selecting a cell naturally highlights the same track in every frame it exists — with zero churn to `set_selection`/`_on_label_selected`/peer views. The `(timepoint, label)` tuple-identity alternative was rejected: it would ripple `CellId` through every selection surface for no benefit here. Selection semantics:
  - **Tracked segmentation:** selecting a label selects that `track_id`; it stays highlighted across the slider, and the highlight simply disappears in frames where the track has no row (death/exit/pre-birth). A selection made at t=0 for a track ending at t=2 remains a valid selection — it just renders nothing past t=2.
  - **Raw per-frame segmentation:** selection is frame-specific. `label=5` at t=0 is a different physical cell from `label=5` at t=1, so selecting at one timepoint must not highlight that label at other timepoints. `filtered_df`/highlight scope to the active timepoint for the raw resource.
- `set_measurements` pruning must intersect on label **within the active timepoint** for the raw case and on `track_id` for the tracked case; keep single-timepoint behavior identical to today (no `timepoint` column at all — see below).
- **Backward compat (resolved): single-timepoint datasets get NO `timepoint` column.** Output is byte-identical to today's measurement format. The `timepoint` column is part of the time-lapse machinery and is added only when `n_timepoints > 1`, consistent with the load-bearing invariant.

**Execution note:** Touches `session.py` selection scoping — route through the canonical event path; guard against the `np.isin`-with-Python-set all-False pitfall (use a list/array) at every membership test.

**Patterns to follow:** `bin_at_measure` row-stamping; `Session.filtered_df`/`set_measurements`; multi-channel measure loop; scalar-int `CellId` selection forwarding (unchanged).

**Test scenarios:**
- Covers R7. Happy path: measuring a `(T,H,W)` tracked segmentation yields rows for every `(timepoint, cell)` with a `timepoint` column and `track_id`/`parent_track_id`/`tree_id` populated.
- Happy path: measuring raw per-frame labels adds `timepoint` only (no track columns), no crash.
- Edge case (backward compat): single-timepoint measurement has **no** `timepoint` column — output byte-identical to today; asserted by parity test.
- Covers R5. Edge case: a track absent in some frames has no rows for those timepoints — per-timepoint counts differ without error.
- Integration (tracked): selecting a label highlights that `track_id` across all frames it exists and renders nothing in frames where it is absent; the selection is not cleared when scrubbing past its lifespan.
- Integration (raw): selecting `label=5` at t=0 highlights only the t=0 cell, not `label=5` at t=1; `np.isin` uses a list/array.
- Integration: filter by `track_id` (tracked resource) survives slider moves.

**Verification:** Time-lapse measurements carry timepoint + lineage columns; tracked-cell selection follows the slider via the unchanged scalar-int contract; raw selection is frame-scoped; single-timepoint output is unchanged.

---

- U9. **napari Tracks layer + lineage graph visualization**

**Goal:** Visualize tracks and divisions: build the napari Tracks `data` array (`[track_id, t, y, x]` from centroids) and the `graph` dict (`{child:[parent]}`) and add a Tracks layer; ensure selection of a tracked cell follows it across the slider.

**Requirements:** R8

**Dependencies:** U7, U8

**Files:**
- Modify: `src/percell4/adapters/napari_viewer.py` (`add_tracks` support; build `data`/`graph` from measurements + lineage)
- Modify: `src/percell4/gui/viewer.py` (push tracks layer on tracked-segmentation select; selection→track highlight across frames)
- Modify: `src/percell4/domain/tracking/lineage.py` (pure builder: measurements/track_df → `(N,4)` tracks array; reuse the graph builder from U6)
- Test: `tests/test_domain/test_lineage.py` (tracks-array builder), `tests/test_gui_workflows/test_tracks_layer.py` (new)

**Approach:**
- Centroids for `[t,y,x]` come from measurements (`centroid_y`, `centroid_x`) keyed by `(timepoint, track_id)`; the pure builder produces the `(N,4)` array sorted by `track_id` then `t`.
- `graph` reuses the U6 `{child:[parent]}` builder. `viewer.add_tracks(data, graph=graph, name=...)`.
- Selecting a cell highlights its `track_id` via `DirectLabelColormap` on the tracked labels layer (never mutating data), so it stays highlighted as the slider moves.

**Patterns to follow:** napari Tracks layer API (`add_tracks(data, graph=)`); `DirectLabelColormap` highlight pattern; the Creator push discipline.

**Test scenarios:**
- Covers R8. Happy path (pure builder): measurements for 2 tracks over 3 frames → a correctly shaped/sorted `(N,4)` tracks array.
- Covers R6/R8. Integration: a division yields a Tracks layer whose `graph` links daughters→parent; arboretum/graph shows the split.
- Integration: selecting a tracked cell keeps it highlighted across slider positions; deselect clears it.
- Edge case: a single-frame track renders as a single point without error.

**Verification:** A napari Tracks layer renders trajectories with division links; tracked-cell selection follows the slider.

---

- U10. **Tracking GUI panel (Creator) + audit artifacts + docs**

**Goal:** Give the user a control to run tracking (a Creator panel/button operating on a selected raw segmentation), surface the tracked segmentation and lineage, and update the living audit artifacts and module docs for the new session field, Selector, and resource kind.

**Requirements:** R4 (GUI surface for), R8 (GUI surface for) — this unit exposes the Creator UI; R4–R8 are delivered by U6–U9.

**Dependencies:** U7, U9

**Files:**
- Create/Modify: a tracking affordance — extend `src/percell4/gui/segmentation_panel.py` or a new `src/percell4/gui/tracking_panel.py` (Creator: choose raw segmentation + params → run `TrackCells` in a worker → Creator four-step)
- Modify: `docs/audits/gui-element-classification.yaml` (timepoint Selector, tracking Creator), `docs/audits/session-mutation-graph.md`, `docs/audits/subscriber-rebind-matrix.md`, `docs/audits/keystroke-binding-audit.md`
- Modify: per-module `CLAUDE.md` where current-state changes (io, store, viewer, application) and `README.md` (add the time-lapse + tracking step to the workflow protocol)
- Test: `tests/test_gui/test_tracking_panel.py` (new)

**Approach:**
- The tracking control is a Creator (writes new tracked segmentation + tracks, auto-selects). Parameters (`cutoff`, `splitting_cutoff`) exposed with defaults; runs in the QThread worker with progress.
- Reflect the new `active_timepoint` Selector and tracking Creator in the GUI-element classification and mutation-graph audits (R15/R16 discipline).
- Update README workflow protocol to include: import time-lapse → scroll timepoints → segment all timepoints → track → review lineage.

**Execution note:** Per-module `CLAUDE.md` describe current state only; archive any brainstorm; capture the new timepoint-storage and tracking/lineage patterns via `/ce-compound` after landing.

**Patterns to follow:** existing Creator panels (`segmentation_panel.py`), worker usage, audit YAML structure.

**Test scenarios:**
- Integration: clicking "Track" on a raw `(T,H,W)` segmentation runs `TrackCells`, then the tracked segmentation is selected and a Tracks layer is present (Creator four-step).
- Edge case: tracking control is disabled / messaged for single-timepoint or non-segmented datasets.
- Test expectation: none for the audit-YAML/README/CLAUDE.md doc edits — documentation, no behavior.

**Verification:** A user can run tracking from the GUI and see the tracked segmentation + lineage; audits and docs reflect the new field/Selector/Creator/resource.

---

## System-Wide Impact

- **Interaction graph:** new `Event.ACTIVE_TIMEPOINT_CHANGED` → `StateChange.timepoint` → `ViewerWindow._on_state_changed`; napari `dims.events.current_step` → Selector → session, guarded by a **dedicated** `_timepoint_originator` (not the shared `_is_originator`) so it cannot collide with selection/segmentation pushes — this dims-event path is distinct from the forbidden layer-list-selection path. New `Event.TRACK_LIST_CHANGED` (optional) for the tracks resource list.
- **Error propagation:** import surfaces missing-channel-at-timepoint (never silently mis-stacks); store raises on shape/dtype mismatch; tracking never crashes on unmatched/ambiguous (reports), mirroring `cross_format`; tracking a single-timepoint dataset fails fast with a clear message.
- **State lifecycle risks:** `active_timepoint` must reset on `set_dataset` and clamp to `n_timepoints`; in-session staleness after writing the tracked segmentation must route through the canonical session-event/invalidation path (per the multi-vector-staleness learning).
- **API surface parity:** every store read path that gained a time axis (`/intensity`, `/labels`, `/masks`) must apply `view_bin` per frame consistently; the per-frame vs full-stack read contract must be uniform across intensity/labels/masks. The three `ndim` display-dispatch sites (`main_window.py`, `hdf5_store.build_view`, `hdf5_store.read_channel_images`) must use identical T-vs-C disambiguation logic — fixing one and missing the others is the likely failure mode.
- **Integration coverage:** import→store→view slider sync; segment-all→track→relabel→measure→Tracks-layer is the end-to-end path unit tests alone won't fully prove — include the GUI-workflow integration tests in U4/U7/U9/U10 and a `python main.py` smoke run on a real `_tN` sample (dialog paths aren't pytest-covered).
- **Unchanged invariants:** single-timepoint datasets keep the exact `(H,W)`/`(C,H,W)`/2D-label layout, no slider, no tracking surfaces; `native_shape` stays `(H,W)`; one-payload-type-per-group and label/mask dtype enforcement are preserved; `/decay`'s histogram T is never conflated with acquisition T.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Relaxing `write_labels`/`write_mask` 2D enforcement weakens a protective invariant | Accept only `ndim == 2` or `(T,H,W)` with trailing dims == `native_shape` **and** `shape[0] == n_timepoints`; keep dtype + one-payload-per-group checks; regression tests on single-timepoint path |
| Existing `ndim` display dispatch splits any leading axis into channels — `(T,H,W)` would render as T bogus channel layers, defeating the slider (3 dispatch sites: `main_window.py`, `hdf5_store.build_view`, `read_channel_images`) | U4 fixes all three sites together with `n_timepoints`-based T-vs-C disambiguation, covered by a dedicated adapter test; `(T,H,W)` and `(C,H,W)` are shape-indistinguishable, so the metadata consult is mandatory |
| `mode_labels` is strictly 2D and crashes on `(T,H,W)` label reads at `view_bin > 1` | U2 makes `mode_labels` loop/vectorize over leading axes; regression test at `view_bin=2` on a `(T,H,W)` stack |
| Reusing the shared `_is_originator` flag for the dims↔timepoint loop guard collides with in-flight selection/segmentation pushes | U4 uses a dedicated `_timepoint_originator` guard for the dims channel |
| laptrack overlap API/return arity differs from the assumed surface across versions | Pin a version range, install and verify the API before writing U6 tests; isolate behind the `Tracker` port so only the adapter changes |
| Timepoint token id diverges between writer and reader (deferred `KeyError` after long compute) | One canonical `timepoints.py` helper for token↔index↔name used everywhere (U1); mirror the channel-name-prefix fix |
| laptrack overlap defaults mis-track the user's cell type (over/under-splitting) | Expose `cutoff`/`splitting_cutoff`/coefficients as config with documented defaults; tune against a real sample at implementation; keep `Tracker` port narrow so btrack/trackastra can be added later |
| Memory blow-up loading full `(T,C,H,W)` stacks into napari | Per-frame chunking in `_choose_chunks`; napari lazily reads frames; measurement/segmentation use per-frame reads, not whole-stack |
| `np.isin(labels, python_set)` silently all-False on NumPy 2.x in tracking/selection | Use lists/arrays at every membership test; covered by the selection learning |
| Selection identity change `(label`→`(timepoint,label)`/track_id) breaks existing single-timepoint selection | Degenerate `timepoint == 0` path keeps current behavior; explicit session tests |
| New dependency `laptrack` adds install/build surface | Pure-Python BSD-3 wheel, no C++/GPU; isolated to the adapter behind the `Tracker` port |
| T1 modules edited without consulting canonical sources | Run `scripts/learnings_applicability.py` per T1 file; invoke `ce-learnings-researcher`; heed the PreToolUse hook |

---

## Phased Delivery

### Phase 1 — Timepoint axis (U1–U4)
Independently shippable value: import a `_tN` series, store it with a time axis, and scroll timepoints in napari. No tracking yet. Single-timepoint datasets fully preserved.

### Phase 2 — Per-timepoint segmentation (U5)
Segment every timepoint into a `(T,H,W)` raw-label resource viewable across the slider. Builds directly on Phase 1.

### Phase 3 — Tracking, lineage, measurement, visualization (U6–U10)
laptrack tracking → relabel into a track-consistent segmentation + lineage table → time-aware measurement with track columns → napari Tracks-layer/division visualization → GUI Creator + audit/doc updates. Delivers R4–R8.

---

## Documentation / Operational Notes

- Update per-module `CLAUDE.md` (io, store, viewer, application) to current state only; update `README.md` workflow protocol with the time-lapse + tracking steps (U10).
- After landing, run `/ce-compound` to capture two net-new patterns with no existing solution doc: the timepoint HDF5 leading-axis layout decision, and the laptrack tracking/lineage integration. Register canonical sources in `docs/audits/canonical-sources-matrix.yaml`.
- Smoke-test on a real `_tN` acquisition via `python main.py` — dialog-driven import/segmentation/tracking paths are not exercised by pytest.

---

## Sources & References

- Repo research: token layer (`domain/io/{scanner,discovery,models,assembler}.py`), store invariants (`store.py`), session/model (`application/session.py`, `model.py`), viewer (`adapters/napari_viewer.py`, `gui/viewer.py`), segmentation (`use_cases/segment_cells.py`, `adapters/cellpose.py`), measurement (`domain/measure/measurer.py`, `use_cases/measure_cells.py`).
- Institutional learnings: `tiff-pending-channel-name-prefix-mismatch-2026-05-21.md`, `napari-mask-layer-misclassified-as-segmentation.md`, `in-session-hdf5-staleness-multi-vector-2026-04-30.md`, `percell4-selection-filtering-multi-roi-patterns.md`, `numpy-isin-fails-with-python-sets.md`, `creator-contract-four-step-sequence-2026-05-18.md`; `docs/audits/canonical-sources-matrix.yaml`.
- External: laptrack OverLapTrack docs (https://laptrack.readthedocs.io/en/stable/examples/overlap_tracking.html), laptrack GitHub (https://github.com/yfukai/laptrack), napari Tracks layer (https://napari.org/dev/howtos/layers/tracks.html), Jaqaman et al. 2008 (LAP), CTC `man_track` format.
- Related prior plans: `docs/plans/2026-04-29-feat-tcspc-append-and-cross-format-token-matching-plan.md`, `docs/brainstorms/2026-05-18-dataset-wide-spatial-binning-requirements.md`.
