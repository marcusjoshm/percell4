---
title: "feat: Multi-timepoint (time-lapse) TCSPC FLIM decay support"
type: feat
status: active
date: 2026-06-26
deepened: 2026-06-26
---

# feat: Multi-timepoint (time-lapse) TCSPC FLIM decay support

## Overview

Today `/decay/<ch>` is a single `(H, W, T_bins)` photon-decay volume with **no acquisition-time axis**, so `add_decay_to_dataset` hard-refuses any dataset with `n_timepoints > 1` (the U20 guard). Users hit this on real Batch TCSPC Append runs against time-lapse experiments (the 3-timepoint Control/VCPi Washout datasets).

This plan extends the FLIM stack to a per-acquisition-time decay: `/decay/<ch>` gains a **leading `T_acq` axis** → `(T_acq, H, W, T_bins)`, one decay frame per intensity timepoint, with the append binding each per-timepoint `.bin` tile set to its acquisition time and reusing the persisted overlap-stitch geometry verbatim for every frame. Phasor `(g, s)` and its derived layers become per-timepoint, and the downstream FLIM consumers (compute, plot, masks, lifetime) select the active timepoint.

This is the follow-up that the time-lapse-tracking plan (`docs/plans/2026-05-21-003-feat-time-lapse-tracking-lineage-plan.md`) and the batch-TCSPC-append work explicitly deferred ("Representing multi-timepoint FLIM requires a new decay schema — out of scope").

---

## Problem Frame

PerCell4's single-cell value proposition is tracking cells across timepoints and conditions. Time-lapse FLIM (per-frame phasor/lifetime) is a first-class scientific need, but the `/decay` schema predates the `(T, …)` layout that `/intensity`, `/labels`, and `/masks` already use. The `T` in `/decay`'s `dims` is the **TCSPC histogram** axis, not acquisition time — so the storage model has no place to put per-frame decay, and every FLIM consumer assumes a single 2-D spatial phasor.

The user's data (confirmed): **one full `.bin` tile set per acquisition timepoint**, carrying a per-timepoint token like the intensity `_t<N>`. The append must bind each set to its timepoint and require the decay timepoint count to equal `/intensity`'s `n_timepoints`.

The companion overlap-registered single-timepoint rotation fix just landed (commit `49a4cf26`) and is the placement model to mirror per timepoint: register once on the reference intensity channel, persist integer offsets, reuse them verbatim for every channel **and now every timepoint**.

---

## Requirements Trace

- R1. Time-lapse datasets (`n_timepoints > 1`) accept TCSPC `.bin` decay append; the U20 guard (`src/percell4/application/use_cases/add_decay_to_dataset.py:160`) is **replaced by real per-timepoint binding**, not merely deleted.
- R2. `/decay/<ch>` stores `(T_acq, H, W, T_bins)` — one decay frame per intensity timepoint. `native_shape` stays `(H, W)`; `n_timepoints` remains the single authoritative time-axis count (inferred from `/intensity`).
- R3. The append binds each per-timepoint `.bin` tile set to its acquisition time via the `_t<N>` token (mirroring intensity import); a per-channel **completeness check** requires the decay timepoint count to equal `n_timepoints`, with a clear error otherwise (mirror the importer's TIFF per-timepoint completeness error).
- R4. The cross-layer alignment invariant holds **per timepoint**: `/intensity[t]` and `/decay[t]` resolve every overlap pixel to the same tile. The persisted registered offsets are reused **verbatim for every timepoint** (single geometry, zero stage drift between frames).
- R5. Phasor `(g, s)`, `g_filtered`/`s_filtered`, and lifetime are computed **per timepoint**, each weighted by **that timepoint's** `decay.sum(axis=-1)` — never `/intensity[t, ch]` — and stored `(T_acq, H, W)`. Derived phasor layers are invalidated whenever `/decay/<ch>` is rewritten.
- R6. Legacy single-timepoint 3-D `/decay` files keep reading unchanged (interpreted as `T_acq == 1`); no on-disk rewrite is required. The new `dims` vocabulary avoids the generic `dims[0] == "T"` time-stacked collision.
- R7. FLIM consumers (phasor plot, phasor masks, lifetime, GMM) select the active timepoint; the explicit "Full time-lapse FLIM is deferred" skips are removed.
- R8. All 4-D decay element/byte sizing uses `math.prod` (Windows int32 overflow); shape reads never full-decode the 4-D tensor (`array_shape`).

---

## Scope Boundaries

- Decay schema is `(T_acq, H, W, T_bins)`; `T_acq` equals `/intensity`'s `n_timepoints` exactly (the confirmed "one `.bin` set per timepoint" case). Partial/degenerate decay (fewer `.bin` sets than timepoints) is **not** supported — it raises a clear completeness error.
- Zero stage drift between timepoints is assumed (single persisted geometry reused for all frames), consistent with the overlap-stitching design. Per-timepoint re-registration is out of scope.
- No `.h5` schema-version attribute is introduced; the `dims`/`native_shape`/`n_timepoints` invariants remain the structural discriminators (consistent with the clean-rebuild convention).

### Deferred to Follow-Up Work

- **FLIM-FRET time-lapse**: `docs/plans/2026-05-25-001-feat-flim-fret-analysis-workflow-plan.md` rejects `(T,C,H,W)` `/intensity` via an `ndim == 4` pre-screen. Revisiting that rejection so FRET accepts time-lapse is a separate plan — this plan keeps the FRET rejection in place and documents the dependency.
- **Channel-lifetime time-lapse**: `ComputeLifetime.execute` writes lifetime as an `/intensity` channel and already raises "not yet supported for time-lapse datasets" (the same `(T,C,H,W)` Add-Channel wall as FRET). This plan delivers the **phasor-derived** `/phasor/<ch>/lifetime_filtered` per frame (U9) but keeps the channel-lifetime time-lapse rejection; lifting it belongs with the FRET/Add-Channel `(T,C,H,W)` follow-up.
- **`add_layer_dialog.py` debug-intensity preview**: the opt-in "write `<ch>_bin` intensity from `decay.sum(over T)`" troubleshooting checkbox sums over the histogram axis and would yield `(T_acq,H,W)` on a 4-D decay. Minor, opt-in; gate it to the active frame (or disable for time-lapse) in a follow-up unless trivially folded into U7.
- **Phasor `.npz` export/import** per-`T_acq` (`export_phasor_npz` / `import_phasor_npz`) — separate follow-up; the npz format carries its own `schema_version`.
- **`analysis/loader.py::_read_intensity`** FLIM-as-intensity fallback under 4-D decay — addressed minimally (return the active frame) but a full time-lapse analysis-loader pass is deferred.

---

## Context & Research

### Relevant Code and Patterns

- **Established `(T, …)` multi-timepoint convention** — `docs/plans/2026-06-05-002-feat-multi-timepoint-feature-parity-plan.md` and `docs/plans/2026-05-21-003-feat-time-lapse-tracking-lineage-plan.md`. The single authoritative time discriminator is `dims[0] == "T"`; `n_timepoints` lives in `/metadata`, inferred **solely from `/intensity`**; per-frame reads/writes slice on disk (`read_array_frame`, `read_channel(timepoint=t)`, `write_labels_frame`/`write_mask_frame`); the canonical loop is `MeasureCells._measure_timelapse` (2-D fast path when `n_timepoints <= 1`, else per-frame loop over pure 2-D domain functions).
- **Store timepoint-indexed write/read primitives** — `src/percell4/store.py`: `_native_shape_and_timepoints` (≈:890), `_validate_layer_shape` (≈:906), `_write_resource_frame` (≈:939, the absent/2-D-promote/`(T,H,W)`-splice pattern), `read_array_frame` (≈:605). These are the templates for a decay-specific `read_decay(channel, timepoint=)`, `write_decay_frame`, and `_validate_decay_shape`.
- **Canonical decay-write path** — `docs/solutions/architecture-patterns/decay-write-path.md`. Every decay write must route through `write_decay_streaming` (`.bin` streams, invalidates stale `/phasor` in the same write), `DatasetStore.write_array(..., is_decay=True)`, or `DatasetStore.append_decay_layers(...)`. Do **not** extend the dead-code duplicate at `importer.py:462-504` or the uncalled `_read_and_stitch_decay`. Note: `append_decay_layers` does **not** invalidate `/phasor` — a gap to fix if used.
- **Overlap-aware registered stitching** — `docs/solutions/architecture-patterns/overlap-aware-stitching.md` + `src/percell4/domain/io/CLAUDE.md`. Geometry computed once on the reference intensity channel, persisted as integer offsets, reused verbatim per channel/timepoint/decay; `canvas_from_offsets` is the single canvas source; ascending-tile-index overwrite priority identical across intensity and decay. Guard test: `tests/test_io/test_stitch_alignment_invariant.py`.
- **Rank-agnostic per-frame template** — `docs/solutions/architecture-patterns/extending-per-cell-detection-to-time-lapse-2026-06-25.md`. Keep 2-D math rank-agnostic; add a thin `for t in range(T_acq)` wrapper in the caller, not in the domain function; emit exactly `T` planes (all-zero for empty), never drop.
- **Cross-format token matching** — `src/percell4/domain/io/cross_format.py` (`match_bin_to_intensity`, channel-only binding) + `src/percell4/adapters/importer.py` (`_group_by_timepoint`, `ordered_timepoint_tokens`, `count_timepoints`; `TokenConfig.timepoint = r"_t(\d+)"` already exists and is exercised for TIFF intensity, never yet for `.bin`). Tile tokens (`_s<idx>`) are parsed in `add_decay_to_dataset._extract_tile_index`.
- **The just-landed rotation fix** — `src/percell4/application/use_cases/add_decay_to_dataset.py` + `write_decay_streaming` `tile_rotate_k`/`tile_flip_axis` (commit `49a4cf26`). The per-tile orientation correction on the registered path is applied once per tile and must run per timepoint.

### Institutional Learnings

- **FLIM phasor cross-layer alignment** (`docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`, critical): every FLIM consumer derives intensity as `decay.sum(axis=-1)` from the **same** decay tensor that produced `(g, s)` — never `/intensity[ch_idx]`. Across the new axis, each timepoint's `(g, s)` must be weighted by **that timepoint's** `decay.sum`. Reintroducing the sibling-stack read reopens a silent, scientifically-wrong misalignment.
- **In-session HDF5 staleness, multi-vector** (`docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`, high): writing time-indexed `/decay` + recomputing `/phasor` per timepoint multiplies the cache surface (frozen handle metadata, h5py per-process cache, phasor-plot `_g_map`/mask-flat caches, on-disk `g_filtered/s_filtered/lifetime_filtered`). Invalidate derived layers at the write boundary; treat `DatasetHandle.metadata` as a snapshot (`read_metadata(handle)`); thread the new `timepoint` kwarg through every cache-filling caller (the `view_bin`-not-forwarded precedent).
- **`np.prod` int32 overflow on Windows** (`docs/solutions/logic-errors/numpy-prod-int32-overflow-windows-2026-06-07.md`): a 36-timepoint stitched stack already overflowed; a 4-D decay tensor is far larger — use `math.prod` for every element/byte computation; assert `nbytes > 0`.
- **Large-file load = full-decode hazard** (`docs/solutions/logic-errors/large-file-load-metadata-read-full-decode-2026-06-07.md`): shape/exists reads must use `DatasetStore.array_shape(path)`, never decompress; catastrophic on a 4-D decay.
- **Single-channel `.bin` token fallback** (`docs/solutions/logic-errors/single-channel-bin-token-fallback-2026-05-25.md`): vendor exports omit redundant tokens in degenerate cases; test single-channel/single-timepoint/single-tile; watch for parallel `.bin` token parsers (`batch_tcspc_dialog._discover_bin_tokens` vs `add_layer_dialog`) drifting.
- **T1 canonical-sources audit** (`docs/audits/canonical-sources-matrix.yaml`): all four module groups this plan touches (`store.py`, `domain/io/`, `adapters/`, `application/use_cases/`) are T1. Run `python3 scripts/learnings_applicability.py <path>` before editing each. Honor `decay-write-path`, `overlap-aware-stitching`, `cross-format-token-matching`, `derived-layer-staleness-invalidation`, `fresh-metadata-read-in-use-cases`.

### Highest-risk coupling points (from research)

1. **`dims` "T" naming collision (highest).** `/decay` stamps `dims=["H","W","T"]` where `"T"` is the histogram axis. Generic helpers (`is_time_stacked`, `read_array_frame`, `read_channel`) treat a **leading** `dims[0]=="T"` as acquisition time. A 4-D decay must use a **distinct leading token** so generic helpers don't misfire and decay-specific readers key on the decay vocabulary.
2. **`compute_phasor` is rank-agnostic** (`einsum("...k,k->...")` + `sum(axis=-1)`) — handed a 4-D decay it silently returns `(T_acq,H,W)` `(g,s)` with no error. The caller must explicitly loop `T_acq`, never rely on broadcasting.
3. **3-D placement assumptions** — `write_decay_streaming`'s `dset[y0:y0+th, x0:x0+tw, :]` and the Phase-2 `_rotate_decay_in_place`/`_flip_decay_in_place` (`axes=(0,1)`) are hard 3-D; a 4-D layout that forgets the leading `[t_acq]` index writes every frame onto frame 0.
4. **`_infer_bin_metadata` decay native_shape** reads `decay.shape[0:2]` — on a 4-D decay that yields `(T_acq, H)`; needs a 4-D branch (`shape[1:3]`).
5. **`n_timepoints` is intensity-only** — the decay path needs the `_t`-token count cross-checked against `/intensity`'s `n_timepoints`.

---

## Key Technical Decisions

- **Schema: leading `T_acq` axis.** `/decay/<ch>` → `(T_acq, H, W, T_bins)`, mirroring the `(T, …)` convention. `native_shape` stays `(H, W)`; `n_timepoints` stays authoritative. *Rationale: consistency with `/intensity`/`/labels`/`/masks`; on-disk per-frame slicing is O(T) not O(T²).*
- **`dims` vocabulary: `["Tacq","H","W","T"]`.** Leading token `"Tacq"` (distinct from the generic `"T"` so `is_time_stacked`/`read_array_frame` do **not** misfire on decay); histogram axis keeps `"T"` (last) unchanged. Legacy 3-D decay keeps `["H","W","T"]` and is read as `T_acq == 1`. Decay-specific readers/validators key on `dims[0] == "Tacq"` (or rank 4). *Rationale: avoids the highest-risk collision with minimal churn — the histogram axis name is unchanged.*
- **`/phasor/<ch>/{g,s,g_filtered,s_filtered,lifetime,lifetime_filtered}` gain a parallel `(T_acq, H, W)` axis** (stamped e.g. `dims=["Tacq","H","W"]`), recomputed/stored per timepoint and invalidated when `/decay` is rewritten. *Rationale: parallels labels/masks gaining `T`; the plot slices the active frame; avoids recompute-on-every-redraw.*
- **Append binding by `(timepoint, channel) → {tile_idx: Path}`.** Reuse the importer's `_t`-token helpers (`_group_by_timepoint`, `ordered_timepoint_tokens`, `count_timepoints`). `cross_format.py` channel binding is unchanged (1-D); the `(timepoint, tile)` decomposition is orchestration-layer. Per-channel completeness: decay timepoint count must equal `n_timepoints` (mirror the TIFF importer error). *Rationale: minimal change to the canonical channel matcher; reuse proven timepoint tooling.*
- **`.bin` token convention: single-padded, 1-based** (user-confirmed). Timepoint tokens are `_t1`, `_t2`, … `_t10` (no zero-pad), matching the existing single-padded, 1-based `.bin` channel and tile tokens. The `r"_t(\d+)"` regex already matches; ordering is **positional via `ordered_timepoint_tokens`** (numeric sort, so `_t2` precedes `_t10`), and the sorted tokens map to `T_acq` frames `0..N-1` — so `_t1` → frame 0. The importer's existing min-index normalization (`if min_idx > 0: {k - min_idx: …}`) already absorbs 1-based tile indices; the same positional/numeric-sort approach absorbs 1-based timepoints. *Rationale: never parse the literal token value into a frame index — assign by sorted position so 1-based / single-padded "just works".*
- **Register once, reuse offsets verbatim for every timepoint.** The persisted integer offsets (and `disconnected` set, and per-tile rotate/flip) are applied identically to each `T_acq` frame. *Rationale: zero stage drift; preserves the cross-layer alignment invariant per frame.*
- **Keep phasor/wavelet/lifetime math rank-agnostic; loop `T_acq` in the caller.** Per timepoint, weight `(g, s)` by that timepoint's `decay.sum(axis=-1)`, never `/intensity[t, ch]`. *Rationale: the cross-layer alignment learning + the per-frame template.*
- **Migration: no rewrite.** 3-D decay = `T_acq == 1`; new 4-D only created for time-lapse appends. Detect by rank / `dims` length. *Rationale: clean-rebuild "favor correct schema", but existing files keep reading.*
- **`math.prod` for all 4-D sizing; `array_shape` for shape reads.** *Rationale: Windows int32 overflow + full-decode hazard.*

---

## Open Questions

### Resolved During Planning

- *How is time-lapse decay captured?* → **One `.bin` set per timepoint** (user-confirmed). `T_acq == n_timepoints`; completeness enforced.
- *`.bin` timepoint token format?* → **Single-padded, 1-based** (`_t1`, `_t2`, … `_t10`), matching the existing `.bin` channel/tile tokens. Mapped to frames positionally via numeric-sorted `ordered_timepoint_tokens` (`_t1` → frame 0), so the 1-based/variable-width form needs no special parsing.
- *Leading-axis `dims` token to avoid the `"T"` collision?* → `"Tacq"`; histogram axis stays `"T"`.
- *Store per-`T_acq` phasor vs recompute per displayed frame?* → Store `(T_acq, H, W)`.
- *Migration of existing 3-D decay?* → No rewrite; read as `T_acq == 1`.

### Deferred to Implementation

- Exact helper/method names (`read_decay(timepoint=)`, `write_decay_frame`, `_validate_decay_shape`) and whether `write_decay_streaming` gains a `timepoint`/`n_acq` param vs a thin per-frame wrapper around a 4-D allocation — settle when touching the streamer.
- The precise `.bin` `_t`-token regex / source layout for the user's real Washout data (per-file token vs per-timepoint subdir) — verify against `/Volumes/NX-01-A/...` data during U4; the architecture (group-by-timepoint-token) is layout-independent.
- Whether `compute_phasor_chunked` (no production callers) is updated now or left 3-D — decide at U6.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

**Storage shape (single-timepoint legacy vs new time-lapse):**

    legacy:   /decay/<ch>   shape (H, W, T_bins)            dims ["H","W","T"]        (T_acq == 1)
    new:      /decay/<ch>   shape (T_acq, H, W, T_bins)     dims ["Tacq","H","W","T"]
              /phasor/<ch>/g,s,...   (T_acq, H, W)          dims ["Tacq","H","W"]
              native_shape = (H, W)            n_timepoints = T_acq   (from /intensity)

**Append flow (time-lapse, registered overlap):**

    .bin files ──parse _t & _s tokens──▶ {(t_acq, channel): {tile_idx: path}}
                                              │  completeness: count(t_acq) == n_timepoints  (per channel)
                                              ▼
    read persisted stitch geometry (offsets, disconnected, rotate_k, flip)  ── once ──┐
                                                                                       ▼
    for t_acq in range(n_timepoints):                       reuse SAME offsets every frame
        write_decay_streaming(... frame=t_acq ...) ──▶ /decay[t_acq] = stitched, oriented mosaic
    (grid path Phase-2 whole-image rotate/flip applied per frame, axes=(1,2))

**Phasor (per-frame, alignment-preserving):**

    for t_acq in range(n_timepoints):
        decay_t = read_decay(ch, timepoint=t_acq)            # (H, W, T_bins)
        g_t, s_t = compute_phasor(decay_t)                   # 2-D math, unchanged
        intensity_t = decay_t.sum(axis=-1)                   # NEVER /intensity[t, ch]
        store /phasor[ch]/g[t_acq], s[t_acq], ...            # weighted by intensity_t

---

## Implementation Units

### Phase 1 — Storage schema + append + per-timepoint phasor (unblocks + makes viewable)

- U1. **4-D `/decay` storage schema in the store**

**Goal:** Teach `DatasetStore` the `(T_acq, H, W, T_bins)` decay layout: chunking, `dims` vocabulary, per-frame read/write, validation, and native-shape inference — while reading legacy 3-D decay as `T_acq == 1`.

**Requirements:** R2, R6, R8

**Dependencies:** None

**Files:**
- Modify: `src/percell4/store.py`
- Modify: `src/percell4/domain/io/view_bin.py` (`sum_bin_decay` rank-fix — see Approach)
- Modify: `src/percell4/ports/dataset_repository.py` (port: per-timepoint decay read)
- Modify: `src/percell4/adapters/hdf5_store.py` (adapter: per-timepoint decay read)
- Modify: `src/percell4/application/use_cases/batch_create_whole_field_segmentation.py` (`_infer_native_shape` 4-D branch — the second copy of the `shape[0:2]` bug)
- Test: `tests/test_io/test_store_decay_timelapse.py` (new)

**Approach:**
- `_choose_chunks`: 4-D decay → `(1, min(64, H), min(64, W), T_bins)` (chunk `T_acq` to 1). Keep 3-D behavior unchanged.
- Add `read_decay(channel, timepoint=None)`: 4-D → slice `obj[timepoint]` **on disk** (→ `(H, W, T_bins)`); 3-D legacy → require `timepoint in (None, 0)` and return whole. **Slice to 3-D BEFORE view-bin** (see next bullet).
- **`view_bin.py::sum_bin_decay` is rank-blind** (`arr.shape[:2]` mixes `T_acq` with rows on a 4-D read). Fix: either slice the frame to 3-D before `sum_bin_decay` (preferred — all decay reads go through `read_decay(timepoint=)`), or make `sum_bin_decay` operate on the trailing spatial dims regardless of leading axes. The store's `decay/`-prefix view-bin dispatch (`read_array`) must NOT hand a raw 4-D array to `sum_bin_decay`.
- **Port + adapter parity (load-bearing):** the FLIM use cases read decay via `repo.read_array(handle, "decay/<ch>", view_bin=)` on the `DatasetRepository` port, not `DatasetStore` directly. Add a per-timepoint decay read to the port (`ports/dataset_repository.py`) and adapter (`adapters/hdf5_store.py`) so U6/U7/U9 can slice one frame on disk — otherwise they `read_array` the whole 4-D tensor (the full-decode hazard) and `decay.sum(axis=-1)` yields `(T_acq,H,W)` mismatching a `(H,W)` phasor frame.
- Add `write_decay_frame(channel, frame, timepoint, n_timepoints, dims/provenance)` mirroring `_write_resource_frame`: absent → allocate `(T_acq, H, W, T_bins)` zeros (stamp `dims=["Tacq","H","W","T"]`) + splice; present 4-D → in-place `ds[timepoint] = frame`. Invalidate `/phasor/<ch>` on (re)write.
- Add `_validate_decay_shape(array, n_timepoints)`: 3-D `(H,W,T_bins)` always valid (T_acq==1); 4-D valid only when `n_timepoints > 1`, leading == `n_timepoints`, spatial `[1:3]` == `native_shape`.
- Fix BOTH native-shape inferers — `store.py::_infer_bin_metadata`/`_metadata_from_intensity_or_decay` AND `batch_create_whole_field_segmentation.py::_infer_native_shape` — to read `native_shape = shape[1:3]` when the decay is 4-D (not `shape[0:2]`).
- All element/byte sizing via `math.prod`; assert `nbytes > 0`; shape reads via `array_shape` (never full-decode).

**Patterns to follow:** `_write_resource_frame`, `_validate_layer_shape`, `read_array_frame`, `_choose_chunks`/`_compression_kwargs(is_decay=True)` in `src/percell4/store.py`; the `DatasetRepository` port/adapter `read_array` signature.

**Test scenarios:**
- Happy path: allocate a `(3, H, W, T_bins)` decay via `write_decay_frame` across t=0,1,2; `read_decay(ch, timepoint=t)` returns each frame; chunks are `(1, …)`.
- Happy path: legacy 3-D decay reads via `read_decay(ch)` and `read_decay(ch, timepoint=0)` identically; `read_decay(ch, timepoint=1)` raises a typed error.
- Edge: `sum_bin_decay`/`read_decay(timepoint=, view_bin=k)` on a 4-D frame bins the SPATIAL dims only — never folds `T_acq` into a spatial block (a 4-D read with `view_bin>1` returns `(H//k, W//k, T_bins)` for the sliced frame, not a `(T_acq//k, …)` corruption).
- Edge: `_choose_chunks` on 4-D decay caps spatial at 64 and `T_acq` at 1; on 3-D unchanged (byte-identical chunk tuple to today).
- Edge: BOTH native-shape inferers on a decay-only (no `/intensity`) 4-D file derive `native_shape == (H, W)` (not `(T_acq, H)`).
- Error: `_validate_decay_shape` rejects a 4-D decay whose leading axis != `n_timepoints` or whose `[1:3]` != `native_shape` (`LayerSizeMismatchError`).
- Integration: the repo port's per-timepoint decay read slices frame t on disk (verify it does not decode the whole tensor — `array_shape`-gated).
- Integration: writing a decay frame deletes a pre-existing `/phasor/<ch>` group (staleness invalidation).

**Verification:** Decay can be stored and read frame-by-frame at `(T_acq,H,W,T_bins)` through both `DatasetStore` and the repository port; view-bin never corrupts a 4-D read; legacy 3-D decay is unaffected; both native-shape inferers are correct from a 4-D decay.

---

- U2. **Timepoint-aware `write_decay_streaming`**

**Goal:** Let the canonical decay streamer place a per-timepoint frame into a pre-allocated `(T_acq, H, W, T_bins)` dataset (leading `[t_acq]` index), reusing the persisted offsets/rotation verbatim — without changing 3-D single-timepoint output byte-for-byte.

**Requirements:** R2, R4, R8

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/adapters/importer.py` (`write_decay_streaming`)
- Test: `tests/test_io/test_stitch_alignment_invariant.py`, `tests/test_add_decay_to_dataset.py`

**Approach:**
- Add `n_acq: int = 1` and `timepoint: int = 0` (names TBD at implementation). When `n_acq > 1`: create/extend the dataset as `(n_acq, out_h, out_w, n_bins)` (stamp `dims=["Tacq","H","W","T"]`), and place each tile at `dset[timepoint, y0:y0+th, x0:x0+tw, :]`. When `n_acq == 1`: byte-identical to today (3-D, `dims=["H","W","T"]`).
- The dataset is created once (first timepoint) and written in-place per subsequent timepoint (no delete+recreate between frames). Per-tile rotate/flip (the `49a4cf26` registered path) is unchanged and runs per frame.
- `/phasor/<ch>` invalidation fires once on (first) create.
- `math.prod` for canvas sizing.

**Patterns to follow:** the existing registered/grid placement + `tile_rotate_k`/`tile_flip_axis` in `write_decay_streaming`; `_write_resource_frame`'s allocate-once-then-splice discipline.

**Test scenarios:**
- Happy path: stream a 2-tile registered mosaic into `(3, H, W, T_bins)` across 3 timepoints; each frame equals the single-frame result; `dims == ["Tacq","H","W","T"]`.
- Golden back-compat: `n_acq == 1` (default) produces a 3-D dataset byte-identical to the current output (extend the existing golden passthrough test).
- Covers AE/R4: per frame, an overlap pixel resolves to the same tile as `/intensity[t]` (ascending-index winner), reusing the same offsets.
- Edge: per-tile `rotate_k=1` applied to every frame on a non-square canvas keeps each frame at `native_shape` (mirror the `49a4cf26` regression, per timepoint).

**Verification:** A 4-D decay can be streamed frame-by-frame; single-timepoint output is unchanged; alignment holds per frame.

---

- U3. **`add_decay_to_dataset`: per-timepoint binding, completeness, per-frame write**

**Goal:** Replace the U20 guard with real per-timepoint append: group `.bin` bindings by `(timepoint, channel)`, enforce the completeness check, loop timepoints writing each frame through `write_decay_streaming`, and apply the grid-path Phase-2 rotate/flip per frame.

**Requirements:** R1, R3, R4, R5

**Dependencies:** U1, U2, U4

**Files:**
- Modify: `src/percell4/application/use_cases/add_decay_to_dataset.py`
- Test: `tests/test_add_decay_to_dataset.py`, `tests/test_application/test_batch_add_decay.py`

**Approach:**
- Remove the `n_timepoints > 1` early-return guard (`:160`). Keep the single-timepoint path byte-identical when `n_timepoints == 1`.
- Group bindings: `{(timepoint_token, channel): {tile_idx: Path}}` using the importer's `_t`-token helpers (U4) and the existing `_extract_tile_index`.
- Per-channel completeness: the set of decay timepoint tokens must equal the intensity timepoint set (count == `n_timepoints`); otherwise a per-channel `errors[ch]` entry (mirror `SourceShapeMismatchError` phrasing) — no partial write.
- Read the persisted stitch geometry once; for each timepoint (enumerate the numeric-sorted timepoint tokens → `t_acq` frame index) call `write_decay_streaming(..., n_acq=n_timepoints, timepoint=t_acq, pixel_offsets=..., tile_rotate_k=..., tile_flip_axis=...)` with the SAME geometry every frame. `write_decay_streaming`'s top-of-call `del /decay/<ch>` + `/phasor` invalidation must be gated to `timepoint == 0` (and `n_acq == 1`) so frame 1 does not delete frame 0 (U2).
- Phase-2 (grid path only) `_rotate_decay_in_place`/`_flip_decay_in_place`: rotate/flip the whole 4-D array on `axes=(1,2)` (leading `T_acq` preserved). Registered path still does per-tile orientation (no whole-image rotate).
- Provenance per channel as today (one record; note `n_timepoints`).

**Execution note:** Test-first — start from the converted guard test (`test_add_decay_rejects_timelapse` → a success test) so the new per-timepoint path is driven by a failing assertion.

**Patterns to follow:** the existing per-channel Phase 1/2/3 structure; the importer's per-timepoint completeness check; `MeasureCells._measure_timelapse` 2-D-fast-path-vs-loop shape.

**Test scenarios:**
- Happy path: a 3-timepoint dataset with one `.bin` set per timepoint per channel appends successfully; `/decay/<ch>` is `(3, H, W, T_bins)`; `report.written` lists all channels.
- Error: a dataset with 3 intensity timepoints but only 2 decay timepoint sets is rejected per-channel with a completeness error; no `/decay` written.
- Back-compat: a single-timepoint dataset produces 3-D `/decay` byte-identical to today.
- Covers R4: per frame, `/intensity[t]` and `/decay[t]` resolve overlap pixels to the same tile (registered offsets reused).
- Edge: registered per-tile `rotate_k` applied to every frame; grid-path whole-image rotate applied per frame (4-D `axes=(1,2)`).
- Integration: appending decay invalidates any pre-existing `/phasor/<ch>` across all frames.

**Verification:** The user's 3-timepoint Washout datasets append without the "Time-lapse FLIM is not yet supported" error; single-timepoint behavior is unchanged.

---

- U4. **`.bin` timepoint-token parsing (append + compress import)**

**Goal:** Parse the per-timepoint `_t<N>` token for `.bin` files in the orchestration layer (append and the compress import path), reusing the intensity timepoint helpers, without touching the channel-only `cross_format` matcher.

**Requirements:** R3

**Dependencies:** None (consumed by U3)

**Files:**
- Modify: `src/percell4/adapters/importer.py` (decay grouping in `import_dataset`; expose/reuse `_group_by_timepoint` for `.bin`)
- Modify: `src/percell4/application/use_cases/add_decay_to_dataset.py` (timepoint grouping helper) — or a shared helper in `src/percell4/domain/io/`
- Test: `tests/test_io/test_timepoints.py`, `tests/test_add_decay_to_dataset.py`

**Approach:**
- Reuse `TokenConfig.timepoint` (`r"_t(\d+)"`), `ordered_timepoint_tokens` (numeric ordering: `_t2` before `_t10`), and `count_timepoints`. The channel binding (`match_bin_to_intensity`) is unchanged; the `(timepoint, tile)` decomposition is orchestration-only.
- **Token convention is single-padded, 1-based** (`_t1`, `_t2`, … `_t10`; likewise `_s<n>` tiles and `_ch<n>` channels). Never parse the literal token number into a frame index — sort tokens numerically via `ordered_timepoint_tokens` and assign by position (`_t1` → frame 0), mirroring the importer's existing min-index tile normalization. This makes 1-based/variable-width tokens "just work".
- Compress path (`import_dataset`): when `.bin` files carry `_t` tokens and intensity is time-lapse, build `{(timepoint, channel): {tile_idx: path}}` and write a 4-D decay at import (compress and append share `write_decay_streaming`, byte-identical per frame).
- Degenerate cases (single channel/timepoint/tile, missing redundant tokens) follow the single-channel-fallback learning; guard against the two parallel `.bin` token parsers drifting (`batch_tcspc_dialog._discover_bin_tokens` vs `add_layer_dialog`).

**Patterns to follow:** `_group_by_timepoint`, `ordered_timepoint_tokens`, `count_timepoints`, and the min-index tile normalization in `src/percell4/adapters/importer.py`; `cross_format.py` channel binding (unchanged).

**Test scenarios:**
- Happy path: `.bin` filenames `..._t1_s1_ch1.bin … _t3_s4_ch2.bin` (single-padded, 1-based) group into `{(t, ch): {tile: path}}`; `_t1` maps to frame 0, `_t2` to frame 1, `_t3` to frame 2.
- Edge: numeric ordering — a 10+-timepoint set orders `_t2` before `_t10` (not lexical).
- Edge: single-timepoint `.bin` (no `_t` token) groups as `T_acq == 1` (back-compat); single-padded 1-based tile/channel tokens normalize to 0-based positions.
- Error: a `.bin` set missing a timepoint that intensity has surfaces as a completeness error upstream (U3).

**Verification:** `.bin` files are grouped by `(timepoint, channel, tile)`; single-padded 1-based tokens map positionally (`_t1` → frame 0); ordering and degenerate cases match the intensity convention.

---

- U5. **Cross-layer alignment invariant (decay) + guard-test rewrite (per timepoint)**

**Goal:** Lock the crown-jewel regression across the new axis at the `/decay` level — compress-flow and append-flow produce **byte-identical** `(T_acq,H,W,T_bins)` `/decay` per frame — and convert the now-obsolete "deferred" guard tests into success/contract tests. (Per-frame *phasor* equality moves to U6, which produces phasor.)

**Requirements:** R1, R4, R6

**Dependencies:** U1, U2, U3, U4

**Files:**
- Modify: `tests/test_io/test_stitch_alignment_invariant.py`
- Modify: `tests/test_application/test_flim_timelapse_guards.py`
- Test: (the two files above are the tests)

**Approach:**
- Extend the alignment invariant to a multi-timepoint mosaic: Path A (decay-at-compress, time-lapse) vs Path B (intensity-only registered import → per-timepoint append) produce byte-identical `/decay` **per frame**, and per frame an overlap pixel resolves to the same tile in `/intensity[t]` and `/decay[t]` (ascending-index winner, reused offsets).
- Rewrite `test_add_decay_rejects_timelapse` into `test_add_decay_accepts_timelapse_per_timepoint` (success + shape + per-frame overlap winner). Keep/adjust the other guard tests (`test_run_phasor_gmm_timelapse_seg_no_crash`, `test_phasor_masks_dialog_imports`) to the new behavior.
- Validate in-session (same process) — never via subprocess (staleness learning).

**Patterns to follow:** the existing `test_compress_vs_append_decay_byte_identical_and_phasor_matches`.

**Test scenarios:**
- Covers R4: byte-identical `/decay` per timepoint across compress vs append; per-frame overlap pixel resolves to the same tile in intensity and decay.
- Edge: a 1-timepoint dataset still passes the original (3-D) invariant unchanged.

**Verification:** The cross-layer `/decay` alignment invariant holds per frame; the obsolete deferral tests are replaced by success/contract tests.

---

- U6. **Per-timepoint phasor computation + storage**

**Goal:** Compute and store `/phasor/<ch>/{g,s,g_filtered,s_filtered}` (and lifetime) as `(T_acq, H, W)`, per timepoint, each weighted by that timepoint's `decay.sum(axis=-1)`; invalidate derived layers on recompute.

**Requirements:** R5, R7

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/application/use_cases/compute_phasor.py`
- Modify (if needed, keep rank-agnostic): `src/percell4/domain/flim/phasor.py`
- Test: `tests/test_flim/test_phasor.py`, `tests/test_application/test_compute_phasor_timelapse.py` (new)

**Approach:**
- `ComputePhasor.execute`: when `/decay/<ch>` is 4-D, loop `for t_acq:` → read frame via the repo port's per-timepoint decay read (U1) → `compute_phasor` (2-D math unchanged, per-channel time-invariant calibration applied per frame) → write `(g,s)` into `/phasor/<ch>/{g,s}[t_acq]` (stamp `dims=["Tacq","H","W"]`); low-signal mask from `decay_t.sum(axis=-1)`. 3-D decay → unchanged 2-D path.
- Keep `compute_phasor`/`median_filter_gs` rank-agnostic 2-D; the loop lives in the use case (per-frame template). `view_bin` upsample / `created_at_bin` stamp operate on each sliced 2-D frame.
- Invalidate `g_filtered/s_filtered/lifetime/lifetime_filtered` per the derived-layer-staleness canonical (recomputed in U9).
- `compute_phasor_chunked` (no production callers) — leave 3-D or guard; decide at implementation.

**Patterns to follow:** `extending-per-cell-detection-to-time-lapse-2026-06-25.md`; `derived-layer-staleness-invalidation`; `flim-phasor-cross-layer-alignment` (intensity from same-frame decay); per-channel `flim_cal_phase_<ch>`/`flim_cal_mod_<ch>` read in `compute_phasor.py`.

**Test scenarios:**
- Happy path: 4-D decay → `/phasor/<ch>/g`,`s` are `(T_acq, H, W)`, `dims=["Tacq","H","W"]`; frame t's `(g,s)` equals `compute_phasor(read_decay(ch, t))`.
- Covers R5 (cross-flow, moved from U5): compress-flow vs append-flow produce identical per-frame `(g, s)` and size-3-median `(g_filtered, s_filtered)` — each derived via `decay[t].sum(axis=-1)`, never `/intensity[t]`; phasor non-trivial per frame. (Validate in-session, never subprocess.)
- Covers R5: per-frame intensity weight is `decay[t].sum(axis=-1)`, never `/intensity[t]` (assert a constructed case where they'd differ).
- Edge: per-channel calibration is applied identically to every frame (`omega` from `T_bins`, unchanged).
- Back-compat: 3-D decay → 2-D `(H,W)` phasor byte-identical to today.
- Edge: an all-zero decay frame → `g=s=0` there (low-signal guard), no NaN leak.

**Verification:** Per-timepoint phasor is stored `(T_acq,H,W)` and matches per-frame compute; compress and append agree per frame; single-timepoint output unchanged.

---

### Phase 2 — FLIM consumers + GUI (plot, masks, wavelet, lifetime)

- U7. **Phasor read/consume + interactive panel + plot timepoint selection**

**Goal:** Thread the active timepoint through `LoadCachedPhasor`, `RunPhasorGMM`, the FLIM task panel (`flim_panel.py`), and the phasor plot peer view so they read/display the active `T_acq` frame; mirror the napari dims-slider → `active_timepoint` Selector.

**Requirements:** R5, R7

**Dependencies:** U6

**Files:**
- Modify: `src/percell4/application/use_cases/load_cached_phasor.py`
- Modify: `src/percell4/application/use_cases/run_phasor_gmm.py`
- Modify: `src/percell4/interfaces/gui/task_panels/flim_panel.py` (the interactive Compute/Wavelet driver that feeds the plot)
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
- Test: `tests/test_application/test_load_cached_phasor.py`, `tests/test_gui/` phasor-plot + flim-panel tests

**Approach:**
- `LoadCachedPhasor`/`RunPhasorGMM`: read `phasor/<ch>/{g,s,…}` at the active timepoint (slice `[t_acq]` when 4-D; whole when 2-D legacy). `RunPhasorGMM` already reads labels/mask at `active_timepoint` (the U20 cheap fix) — now align the phasor read to the same frame.
- `flim_panel.py`: its `repo.read_array("decay/<ch>")` → `decay.sum(axis=-1)` and `phasor/<ch>/g`+`s` reads must use the per-timepoint decay read (U1 port method) + active-frame phasor slice, so the intensity-weighted histogram and maps are the active frame, not a wrong-rank whole tensor.
- Phasor plot: hold the active-frame `(g, s, intensity)`; on `active_timepoint` change, reload the matching phasor frame (cache invalidation per the staleness learning — `_g_map`/mask-flat caches keyed by timepoint).
- Treat `DatasetHandle.metadata` as a snapshot (`read_metadata`).

**Patterns to follow:** the `active_timepoint` Selector wiring in `src/percell4/gui/viewer.py`; `fresh-metadata-read-in-use-cases`; staleness multi-vector learning.

**Test scenarios:**
- Happy path: with a 4-D phasor, `LoadCachedPhasor` at `active_timepoint=t` returns frame t's `(g,s,g_filtered,s_filtered)`.
- Integration: changing `active_timepoint` reloads the plot's `(g,s)` to the new frame (cache cleared then refilled with the correct frame, not the stale default).
- Back-compat: 2-D legacy phasor loads unchanged regardless of `active_timepoint` (0).

**Verification:** The phasor plot shows the active timepoint's phasor; GMM/load read the matching frame.

---

- U8. **Phasor masks per timepoint; remove deferral skips**

**Goal:** Make `phasor_masks_dialog` (interactive, active frame) and `batch_fit_phasor_masks` (per-frame) time-lapse-aware, removing the explicit "Full time-lapse FLIM is deferred" skip. (Channel-lifetime time-lapse is deferred — see Scope Boundaries.)

**Requirements:** R7

**Dependencies:** U6, U7

**Files:**
- Modify: `src/percell4/gui/phasor_masks_dialog.py`
- Modify: `src/percell4/application/use_cases/batch_fit_phasor_masks.py`
- Test: `tests/test_gui/test_phasor_masks_dialog.py`, `tests/test_application/test_batch_fit_phasor_masks.py`

**Approach:**
- `phasor_masks_dialog` (**interactive**): replace the `n_timepoints > 1` skip (≈:465) with **active-frame** handling — fit the GMM ellipse on the currently displayed `active_timepoint` frame and write that frame's mask via `write_mask_frame` (a `(T_acq,H,W)` mask resource, other frames empty until visited). Reads phasor `(g,s)` + decay-derived intensity at the active frame (U6/U7).
- `batch_fit_phasor_masks` (**batch**): per-frame loop over `T_acq` — read each frame's phasor + same-frame decay intensity, fit, write `(T_acq,H,W)` masks via `write_mask_frame`.
- Honor the cross-layer alignment learning (intensity from same-frame decay) and derived-layer invalidation.

**Patterns to follow:** the per-frame template; `write_mask_frame`; the removed deferral comment as the contract being replaced.

**Test scenarios:**
- Happy path: phasor masks fit on a time-lapse dataset (active frame) without the deferral QMessageBox; mask written for that frame; other frames remain empty.
- Happy path: `batch_fit_phasor_masks` writes a `(T_acq,H,W)` mask, each frame fit from its own phasor + same-frame decay intensity.
- Back-compat: single-timepoint masks unchanged.

**Verification:** The deferral skip is gone; interactive masks fit the active frame and batch masks fit every frame; no cross-frame intensity leakage.

---

- U9. **Per-timepoint wavelet filtering + batch phasor orchestration**

**Goal:** Make the canonical `g_filtered`/`s_filtered`/`lifetime_filtered` producer (`ApplyWavelet`) and the batch phasor pipeline (`batch_compute_phasor`, the `batch_phasor` CLI) per-timepoint, so R5's filtered/lifetime layers exist per frame and the batch path doesn't break on a 4-D decay.

**Requirements:** R5, R7

**Dependencies:** U1, U6

**Files:**
- Modify: `src/percell4/application/use_cases/apply_wavelet.py`
- Modify: `src/percell4/application/use_cases/batch_compute_phasor.py`
- Test: `tests/test_application/test_apply_wavelet.py` (or existing), `tests/test_application/test_batch_compute_phasor.py`

**Approach:**
- `ApplyWavelet.execute`: when `/phasor/<ch>/{g,s}` is 4-D (`(T_acq,H,W)`), loop per frame — read frame's `g`/`s` + same-frame decay intensity (`read_decay(ch, timepoint=t).sum(axis=-1)`, never `/intensity[t]`) — and write `(T_acq,H,W)` `g_filtered`/`s_filtered`/`lifetime_filtered` (`dims=["Tacq","H","W"]`). 3-D legacy → unchanged 2-D path. Keep the DTCWT wavelet math rank-agnostic; loop in the use case.
- `batch_compute_phasor`: its per-channel `phasor_uc.execute()` (U6) then `wavelet_uc.execute()` sequence now both handle 4-D; verify the CLI report still prints per dataset.

**Patterns to follow:** the per-frame template; `flim-phasor-cross-layer-alignment` (intensity from same-frame decay); `derived-layer-staleness-invalidation`.

**Test scenarios:**
- Happy path: 4-D phasor → `g_filtered`/`s_filtered`/`lifetime_filtered` are `(T_acq,H,W)`; frame t equals the wavelet of frame t.
- Covers R5: each frame's filtered phasor uses same-frame decay intensity, never `/intensity[t]`.
- Integration: `batch_compute_phasor` over a time-lapse `/decay/*` completes phasor + wavelet for every channel; per-dataset report intact.
- Back-compat: single-timepoint wavelet output unchanged.

**Verification:** Filtered phasor + lifetime layers exist `(T_acq,H,W)` per frame; the `batch_phasor` pipeline succeeds on time-lapse datasets.

---

## System-Wide Impact

- **Interaction graph:** `add_decay_to_dataset`/`import_dataset` → `write_decay_streaming` → `/decay` (now 4-D) → `ComputePhasor`/`ApplyWavelet` (also via `batch_compute_phasor`) → `/phasor` (now 4-D) → `LoadCachedPhasor`/`RunPhasorGMM`/`flim_panel`/phasor plot/masks. Every decay read goes through the `DatasetRepository` port — so the port's new per-timepoint read (U1) is the chokepoint for all consumers. The `active_timepoint` Selector (napari dims slider) becomes a new alignment axis for every phasor consumer and its caches.
- **Error propagation:** per-channel completeness errors surface in `AppendReport.errors` (no partial writes); shape mismatches raise `LayerSizeMismatchError`/`SourceShapeMismatchError`.
- **State lifecycle risks:** `/decay` write must invalidate `/phasor/<ch>` (all derived frames); `append_decay_layers` currently does **not** invalidate `/phasor` — fix if that path is used. `write_decay_streaming`'s top-of-call `del /decay/<ch>` + `/phasor` invalidation must be **gated to `timepoint == 0`** so frame 1 doesn't delete frame 0 (allocate-once-then-splice per frame). Treat `DatasetHandle.metadata` as a snapshot.
- **API surface parity:** both append entry points (`gui/batch_tcspc_dialog.py`, `gui/add_layer_dialog.py`) and the two `.bin` token parsers (`batch_tcspc_dialog._discover_bin_tokens`, `add_layer_dialog`) must stay consistent; the compress import and append must share `write_decay_streaming` (byte-identical per frame). The rank-blind `view_bin.py::sum_bin_decay` and the two `shape[0:2]` native-shape inferers (`store.py`, `batch_create_whole_field_segmentation.py`) are parallel surfaces that must all be fixed together (U1).
- **Integration coverage:** the per-frame cross-layer alignment invariant (U5) is the load-bearing test mocks can't prove.
- **Unchanged invariants:** `native_shape` stays `(H, W)`; `n_timepoints` stays intensity-derived and authoritative; single-timepoint decay output stays byte-identical; the registered-stitch "compute geometry once, reuse verbatim" rule is preserved (now across timepoints).

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `dims` `"T"` collision causes generic helpers to mis-slice a 4-D decay | Distinct leading token `"Tacq"`; decay-specific readers/validators; audit every `dims[0]=="T"` site (U1). |
| `compute_phasor` rank-agnostic → silent `(T_acq,H,W)` g/s | Explicit per-frame loop in the use case; never pass 4-D decay to the 2-D math (U6); per-frame phasor assertion (U6 tests). |
| 3-D placement (`axes=(0,1)`, `dset[…, :]`) writes every frame onto frame 0 | Leading `[t_acq]` index in `write_decay_streaming`; Phase-2 rotate/flip → `axes=(1,2)`; golden single-timepoint passthrough test (U2). |
| `_infer_bin_metadata` reads `shape[0:2]` → corrupt `native_shape` from 4-D decay | 4-D branch reading `shape[1:3]` (U1). |
| Windows int32 overflow on 4-D sizing | `math.prod` everywhere; `assert nbytes > 0`; `array_shape` for shape reads (U1, U2). |
| Cross-layer misalignment per frame (the prior critical FLIM bug) | Reuse persisted offsets verbatim per frame; intensity from same-frame `decay.sum`; per-frame invariant test (U5). |
| FLIM consumers read decay via the repo port (`read_array`), not `DatasetStore` — no per-timepoint read would force a full 4-D decode + wrong-rank intensity | Add the per-timepoint decay read to the port + adapter (U1); every consumer slices a frame; `array_shape` for shape reads. |
| `view_bin.py::sum_bin_decay` is rank-blind (`shape[:2]`) — folds `T_acq` into the spatial block on a 4-D read | Slice to 3-D before view-bin; route all decay reads through `read_decay(timepoint=)` (U1); explicit 4-D view-bin test. |
| Un-enumerated consumers (`apply_wavelet`, `batch_compute_phasor`, `flim_panel`, the second `_infer_native_shape`) break post-migration on 4-D files | Surfaced by review; now covered by U1/U7/U9; `git grep` decay/phasor reads before closing each phase. |
| FLIM-FRET `ndim==4` rejection contradicts new time-lapse FLIM | Explicitly deferred; documented dependency to revisit in a separate plan. |
| Real `.bin` source layout differs from `_t`-token assumption | Architecture is layout-independent (group by timepoint token); verify against real Washout data in U4. |

---

## Documentation / Operational Notes

- After landing, capture the schema-migration decision (3-D legacy decay coexisting with 4-D; `n_timepoints` gating read shape; the `"Tacq"` dims vocabulary) as a `docs/solutions/` entry — there is no general `.h5` schema-versioning doc today.
- Update `src/percell4/domain/io/CLAUDE.md` and any decay/phasor module CLAUDE.md to state the `(T_acq,H,W,T_bins)` layout and the per-frame reuse-offsets rule.
- Per CLAUDE.md R15/R16: run `python3 scripts/learnings_applicability.py <path>` before editing each T1 file (`store.py`, `domain/io/`, `adapters/importer.py`, `application/use_cases/*`).

---

## Phased Delivery

### Phase 1 (U1, U2, U4, U3, U6, U5) — unblocks the user and makes it viewable
Storage schema (incl. the repo-port per-timepoint read + view-bin rank-fix + both native-shape inferers) → timepoint-aware streamer → token parsing → per-timepoint append → per-timepoint phasor compute → the per-frame `/decay` + cross-flow phasor invariants. After Phase 1, the 3-timepoint Washout datasets append successfully, `/decay` is `(T_acq,H,W,T_bins)`, and per-frame `(g,s)` phasor is computed and verified. (Dependency order within the phase: U1 → U2/U4 → U3 → U6; U5 tests after U3, U6.)

### Phase 2 (U7, U8, U9) — per-timepoint FLIM analysis + GUI
Phasor read/consume + interactive FLIM panel + plot timepoint selection (U7); phasor masks per timepoint (U8); per-timepoint wavelet filtering + batch phasor orchestration (U9). Removes the remaining deferral skips and makes the `batch_phasor` CLI + interactive plot time-lapse-aware.

### Deferred (separate plans / follow-up units)
FLIM-FRET time-lapse (`ndim==4` rejection), channel-lifetime time-lapse (the `(T,C,H,W)` Add-Channel wall), the `add_layer` debug-intensity preview, phasor `.npz` per-`T_acq`, and the full analysis-loader FLIM-as-intensity time-lapse pass.

---

## Sources & References

- Companion fix (single-timepoint registered per-tile rotation): commit `49a4cf26`.
- Prior art: `docs/plans/2026-06-05-002-feat-multi-timepoint-feature-parity-plan.md`, `docs/plans/2026-05-21-003-feat-time-lapse-tracking-lineage-plan.md`, `docs/plans/2026-05-12-001-feat-batch-tcspc-append-plan.md`, `docs/brainstorms/2026-05-12-batch-tcspc-append-requirements.md`, `docs/brainstorms/2026-06-24-mosaic-merge-overlap-stitching-requirements.md`.
- Learnings: `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`, `docs/solutions/architecture-patterns/overlap-aware-stitching.md`, `docs/solutions/architecture-patterns/decay-write-path.md`, `docs/solutions/architecture-patterns/extending-per-cell-detection-to-time-lapse-2026-06-25.md`, `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`, `docs/solutions/logic-errors/numpy-prod-int32-overflow-windows-2026-06-07.md`, `docs/solutions/logic-errors/large-file-load-metadata-read-full-decode-2026-06-07.md`, `docs/solutions/logic-errors/single-channel-bin-token-fallback-2026-05-25.md`.
- Audits: `docs/audits/canonical-sources-matrix.yaml`, `docs/audits/io-principles-matrix.yaml`.
- Key source: `src/percell4/store.py`, `src/percell4/adapters/importer.py`, `src/percell4/application/use_cases/add_decay_to_dataset.py`, `src/percell4/domain/flim/phasor.py`, `src/percell4/application/use_cases/compute_phasor.py`, `src/percell4/domain/io/cross_format.py`, `src/percell4/gui/phasor_masks_dialog.py`, `src/percell4/interfaces/gui/peer_views/phasor_plot.py`.
