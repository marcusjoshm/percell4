---
title: "feat: Overlap-aware (phase-correlation) mosaic stitching"
type: feat
status: active
date: 2026-06-24
deepened: 2026-06-24
origin: docs/brainstorms/2026-06-24-mosaic-merge-overlap-stitching-requirements.md
---

# feat: Overlap-aware (phase-correlation) mosaic stitching

## Overview

Add a second tile-stitching method to PerCell4: tiles captured **with X% overlap** are
aligned by phase-correlation registration, while **0%-overlap tiles keep the current
edge-to-edge `assemble_tiles` method byte-for-byte**. Registration is computed **once** on a
reference intensity channel at compress/import time; the resulting integer per-tile pixel
offsets are persisted as a first-class payload and **reused verbatim** for every other
channel and for the decay (TCSPC) stream — including a later decay-only append. Overlap
fusion is **overwrite** everywhere. The registration engine is the user's numpy-only
`grid_stitching` package, vendored into the domain layer.

This is the **first data-dependent stitch path** in `domain/io`. Its central risk is the
`/decay` ↔ `/intensity` alignment invariant (a prior critical, scientifically-wrong bug),
which the design neutralizes by computing geometry once and applying it identically to all
layers.

---

## Problem Frame

`assemble_tiles()` (`src/percell4/domain/io/assembler.py:12`) places tiles edge-to-edge on a
fixed grid (`y0 = row·tile_h`, `x0 = col·tile_w`) — no overlap, no registration. Researchers
capture mosaics with overlap specifically to correct stage-positioning error, and PerCell4
has no path for that. The decay path is the crux: decay tiles are *streamed* tile-by-tile
into `/decay` via `write_decay_streaming`, and a `native_shape` lock forces the decay
composite to be pixel-identical to the intensity composite so per-pixel phasor stays aligned
to the segmentation. Phase correlation produces a data-dependent canvas, which collides with
that lock unless geometry is computed once and reused. See origin:
`docs/brainstorms/2026-06-24-mosaic-merge-overlap-stitching-requirements.md`.

---

## Requirements Trace

- R1. New registration path is opt-in, entered only when `register ∧ overlap>0 ∧ grid_rows·grid_cols>1`; all other inputs use the existing grid path byte-identically (single-tile fast path untouched).
- R2. Registration computed exactly once, at compress/import time, on one user-selected reference intensity channel.
- R3. Seed initial positions from grid+overlap → phase-correlate on reference channel → solve integer per-tile pixel offsets → normalize to non-negative origin; canvas = bounding box of placed tiles.
- R4. Persist per-tile offsets + canvas + reference channel + overlap + registered flag + quality as provenance, through the single store write boundary (no direct h5py in domain/application).
- R5. Every other channel, z-plane, timepoint, and the decay stream reuse persisted offsets verbatim; decay never registered independently; all layers share one solve.
- R6. Decay-only append reads persisted offsets and reuses them; errors if registered-but-absent; runs the existing grid path unchanged when not registered (back-compat).
- R7. Overwrite fusion everywhere under one pinned deterministic tile priority (ascending tile index) applied identically across layers.
- R8. Decay overwrite uses the existing streamed write (no read-back, no in-memory fuse, no photon double-counting); the registration library only *computes* offsets, never fuses decay.
- R9. Registered canvas becomes `native_shape`; decay-append guard derives expected canvas from persisted geometry (not grid·tile), preserving the `rotate_k` transpose; phase order REGISTER→stitch→rotate→flip; stale `/phasor/<ch>` invalidated.
- R10. The two **live** placement functions (`assemble_tiles` intensity, `write_decay_streaming` decay) gain offset-aware variants driven by one shared `(offsets, canvas)` contract; the two **dead** placement blocks (`importer.py:622-666`, `add_decay_to_dataset.py:460-524` `_read_and_stitch_decay`) are deleted, not patched. Because domain (in-memory intensity) cannot import h5py and decay placement is a streamed h5py write, the binding enforcement is one shared contract + the cross-layer phasor-equality test (U9), not a single shared function.
- R11. Registration quality (per-pair correlation, disconnected/fallback tiles, accepted-pair fraction, coverage) is captured and surfaced. A **registration-success gate** prevents writing `stitch_registered=True` for a degenerate solve: if too few pairs clear `regression_threshold` (so most/all tiles fall back to their initial grid position) the import fails loudly rather than silently storing a grid-equivalent canvas as "registered." The engine parameters (`regression_threshold`, `n_peaks`) are persisted in provenance.
- R12. Registration is reproducible: library vendored + identity + engine parameters recorded; downstream reproduces placement by reusing persisted offsets.
- R13. Overlap%/Register/Reference-channel controls on Import + Compress surfaces only; AddLayer-batch + TCSPC get a read-only "reuse persisted geometry" affordance; TCSPC user-edited suppression preserved.
- R14. v1 requires uniform tile size (reject otherwise). v1 operates on **already-z-projected 2D** mosaics (z-stack-mosaic overlap registration is deferred — see Scope Boundaries). Time-lapse registers once and reuses across all timepoints under a documented **zero-inter-frame-drift assumption**, with a last-timepoint correlation re-check that warns if drift exceeds the overlap budget.

**Origin actors:** A1 (researcher/import), A2 (import/compress pipeline), A3 (decay TCSPC append flow), A4 (phasor/measurement consumers)
**Origin flows:** F1 (register-once at import), F2 (decay-only append reusing persisted geometry), F3 (0%-overlap / single-tile passthrough — unchanged)
**Origin acceptance examples:** AE1 (overlap pixel resolves to same tile in intensity & decay), AE2 (decay append reuses persisted geometry; canvas == native_shape; no re-registration), AE3 (0%-overlap & 1×1 byte-identical to pre-feature), AE4 (registered-but-offsets-absent → error, not silent grid fallback)

---

## Scope Boundaries

- Sub-pixel registration — library is integer-pixel by design; cross-layer consistency comes from reusing the same integer offsets.
- Any fusion other than overwrite (feather/linear-blending, average, max, sum) — out.
- Per-timepoint drift correction — register once and reuse across all timepoints.
- Ragged / non-uniform mosaics and cropped edge tiles — rejected with a clear error, not handled.
- Out-of-core / chunked registration for mosaics exceeding RAM — accept a documented RAM ceiling (only the already-streamed decay write is memory-safe).
- 3D `(z,y,x)` registration — v1 registers only on 2D planes; see z-stack deferral below.
- Auto-retry / threshold-relaxation on registration failure — surface and warn only (the success gate fails loudly; it does not auto-relax).
- Per-timepoint drift correction — register once and reuse across all timepoints (zero-drift assumption, warned per R14).
- Registration controls on TCSPC / AddLayer-batch surfaces — those reuse persisted geometry.
- Full migration of all stitch surfaces onto `StitchingFlimForm` beyond what R13 needs — separate refactor.
- Backfilling registered geometry onto pre-feature datasets — old datasets keep the grid path.

### Deferred to Follow-Up Work

- A `percell4-batch-compress` headless CLI entry point: none exists today (compress is invoked via `workflows.phases.compress_one`). The registration logic is placed as a pure shared function so a future CLI inherits it for free, but adding the CLI itself is out of this plan.
- **Z-stack-mosaic overlap registration.** v1 registers on already-z-projected 2D mosaics only. Registering a z-stack mosaic (where `_assemble_plane` z-projects per tile-group before assembly) requires capturing the reference channel's per-tile arrays at the post-projection plane — folded into the U6 import-flow restructure (see U6) but its dedicated z-stack path + test is deferred. Decided as the default for the A4 review finding; revisit in Open Questions.

---

## Context & Research

### Relevant Code and Patterns

- Current edge-to-edge stitcher (unpacked kwargs, *not* a `TileConfig` object): `src/percell4/domain/io/assembler.py:12` (`assemble_tiles`), `:57` (`_tile_positions`).
- Decay single-source-of-truth writer: `write_decay_streaming` (`src/percell4/adapters/importer.py:801`); placement math at `:881-887`; invalidates stale `/phasor/<ch>` in the same write. Canonical per `docs/solutions/architecture-patterns/decay-write-path.md`. **Dead placement blocks to delete (U5), not patch:** `importer.py:622-666` (duplicate placement math at `:654-662`, unreachable) and `add_decay_to_dataset.py:460-524` `_read_and_stitch_decay` (zero callers). Note `importer.py:462-504` is **live** (`.bin` dict-builder + `creation_bin` floor-division) — do not touch.
- Decay-append use case + `native_shape` guard + rotate/flip phases: `src/percell4/application/use_cases/add_decay_to_dataset.py` (guard `:254-271`, rotate `:410-444`, flip `:378-407`, provenance `:335-375`).
- Frozen import-config dataclasses: `TileConfig` (`src/percell4/domain/io/models.py:49-71`), `DatasetSpec` (`:224-235`), `CompressConfig` (`:247-262`), `DatasetGuiState.tile_config_override` (`:237-245`), `ProvenanceRecord` (`:173-199`, str/bool fields for attr round-trip).
- TileConfig 4-hop threading: build in `gui/compress_dialog.py:426-433` + `gui/_stitching_flim_form.py:195` → flatten in `gui/workflows/single_cell/config_dialog.py:244-252` → rebuild in `workflows/phases.py:131-164` → consume in `adapters/importer.py:92-106`. Guarded by `tests/test_workflows/test_phases_compress_tile_config.py`.
- Store write boundary: `DatasetStore.create`/`set_metadata` (metadata as group **attrs**, `store.py:1104-1143`), `write_array` (array datasets, byte-identical round-trip at `view_bin=1`, `store.py:283-345`), `append_decay_layers` + inline provenance (`store.py:1244-1249`). `native_shape` lock + guard exceptions `store.py:~131-164`.
- Canonical stitch widget: `src/percell4/gui/_stitching_flim_form.py` (`StitchingFlimForm`, `.tile_config()` at `:195`).
- Metadata persistence of stitch scalars today: `adapters/importer.py:552-556`.

### Institutional Learnings

- `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md` (**critical**) — a data-dependent canvas must be computed once and applied identically to intensity and decay; the bug was silent and scientifically wrong. Verify the **consumer** side too: FLIM consumers derive intensity via `decay.sum(axis=-1)`, not `/intensity[ch_idx]`. Never "rotate /intensity to match decay." Add the compress-vs-append phasor-equality regression test.
- `docs/solutions/architecture-patterns/decay-write-path.md` — all `/decay` writes route through `write_decay_streaming` / `DatasetStore.write_array(is_decay=True)` / `append_decay_layers`; feed persisted offsets *into* the streamer, don't stitch decay in memory.
- `docs/solutions/logic-errors/batch-compress-development-lessons.md` — `assemble_tiles()` takes unpacked kwargs (predates `TileConfig`); every prior path to the stitcher must still resolve (single-tile/0%-overlap passthrough test is the "matcher-refactor scoping collapse" defense, now 3× documented).
- `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md` — add controls to `StitchingFlimForm`, not each dialog (PR #9 hit four drift bugs rebuilding widgets off dataclass fields).
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md` — use cases reading persisted geometry after a write must go through `DatasetRepository.read_metadata(handle)`, never `handle.metadata.get(...)` (lint keeps that grep at zero); validate in-process, not via subprocess.
- `docs/solutions/logic-errors/numpy-prod-int32-overflow-windows-2026-06-07.md` — use `math.prod` for canvas element/byte counts (registered mosaics are larger; `np.prod` wraps on Windows LLP64).
- I/O principles matrix (`docs/audits/io-principles-matrix.yaml`): P6 gold standard is `add_decay_to_dataset.py.6` (`/provenance/decay/<ch>`); P4 violations already flagged for direct `h5py.File('a')` in importer/add_decay/add_layer — do not add a new one; P5 `append-flow-plan-consumption` thread is the named home for "compute once, consume verbatim."

### External References

- None used. Internal codebase work with strong local patterns; the registration algorithm is supplied as a vendorable package. (Algorithm: Preibisch, Saalfeld & Tomančák 2009 phase-correlation grid stitching — the Fiji Grid/Collection plugin method.)

---

## Key Technical Decisions

- **Vendor only the computational core of `grid_stitching`** into `src/percell4/domain/_vendor/grid_stitching/`, not the whole package. Vendor `phase_correlation.py`, `optimize.py`, the `Tile` dataclass + `grid_positions` from `tiles.py`, and `fuse.py` (for reference) — but **do not vendor or re-export `load_tile_images` / `parse_tile_configuration` / `write_tile_configuration`**: those do filesystem I/O (`tifffile`/`skimage.io`/`open`) and would leak I/O into the pure domain layer. PerCell4 already has tiles in memory; it never needs the engine's file loaders. Rationale: the user's package implements the multi-peak Preibisch disambiguation matching their Fiji workflow; it is numpy-only (confirmed — the `setup.py` scipy claim is spurious); `domain/_vendor/` respects the import-linter and pins the source for reproducibility (R12).
- **Placement is a hand-written integer overwrite loop, NOT vendored `fuse`.** The engine is consulted only for `register_pair` / `optimize_positions` (offset *computation*). `assemble_tiles_with_offsets` must not call `fuse`: `fuse` upcasts to `float64` then re-clips (breaks the golden byte-identity invariant), iterates in tile-list order (not the pinned ascending-index priority), and recomputes the canvas internally (the canvas must be pinned once and passed in). Decay placement likewise stays the existing integer slice-assignment in `write_decay_streaming`.
- **Single-source the canvas.** One pure `canvas_from_offsets(offsets, tile_shape)` helper (= `max(y0+h)`, `max(x0+w)` over the final rounded non-negative integer offsets) is the *only* canvas computation; `native_shape`, the intensity allocator, and the decay allocator all consume that one value — recomputation in multiple places is forbidden. This extends R10's "placement sites change together" to canvas sizing.
- **`stitch_registered` is the commit point (write atomicity).** `import_dataset` is not atomic across its multiple file opens. The **primary** guarantee is ordering: write `stitch_registered=True` **strictly last**, gated on the offsets dataset, `/provenance/stitch`, and the assembled intensity canvas all being durably present, so a crash before the flag leaves an un-registered (recoverable) file rather than the AE4 brick state — and recovery treats "flag absent but offsets present (or vice versa)" as "not committed → safe to re-import." Full `create_atomic` (temp-then-rename) routing is the **stretch** goal: it is a real restructure, not wiring, because today's write primitives (`store.write_array`, `write_decay_streaming`) open the file by path rather than accepting an open handle, so atomic routing means threading one handle through them or reopening the temp path inside `build_fn`. Before writing the flag, assert `bbox(offsets) == assembled /intensity.shape[-2:]`.
- **Persist per-tile offsets as a dataset, scalars as metadata attrs.** `/metadata` is attrs-only (64 KB cap, no generic array typing on read-back), so the final rounded non-negative `(N,2) int32` offset array goes through `store.write_array("stitch/tile_offsets", …)` (pass-through view-bin, lossless, no `is_decay`/`dims` requirement); `stitch_registered`, `stitch_reference_channel`, `stitch_overlap` join the existing `stitch_grid_*` metadata attrs (typed normalization added at `store.py:1096-1099`). Persisted offsets always have per-axis min 0 (asserted on write and read) so the canvas derivation is unconditionally `max(offset+extent)`.
- **Registered geometry is a provenance payload (P6).** Add a `StitchProvenanceRecord` (frozen, str/bool/JSON fields) written to `/provenance/stitch` via a real store method — modeled on `ProvenanceRecord` → `/provenance/decay/<ch>`. Its `quality_json` carries per-pair correlations, disconnected-tile indices, **and the canvas coverage fraction**. No new `h5py.File('a')` carve-out in `application/`.
- **Register on the post-`creation_bin`, post-z-projection reference tiles — which the import flow must be restructured to retain.** Persisted offsets must be in the same post-bin pixel units as `native_shape` (`bbox(offsets) == native_shape`); pre-bin offsets would make the canvas a factor of `creation_bin` too large and trip `MetadataConsistencyError`. **Critical structural note:** the reference channel's per-tile arrays do **not** exist as a dict at `importer.py:497` — the TIFF path stitches and discards them inside `_load_and_stitch` (`:761`) during the per-channel loop, and the `.bin` path freezes decay positions into `tcspc_data` at `:460-471` *before* `:497`. So U6 cannot "insert between `:497` and `:500`"; it must restructure the import so the reference channel's per-tile arrays (post-bin, post-z-projection) are **captured before stitching consumes them**, registration runs on those, and the resulting offsets redirect both the intensity assembly and the (not-yet-frozen) decay placement. This restructure is the load-bearing part of U6.
- **Registration math is a pure domain function.** `estimate_tile_offsets(...)` (computation) and `assemble_tiles_with_offsets(...)` (placement) live in `domain/io/assembler.py` alongside `assemble_tiles`, callable from both the Qt workflow and a future CLI (P7). The P5 computation/placement seam is the boundary *between* these two functions.
- **One pinned overwrite priority: ascending tile index, with disconnected tiles demoted.** Matches the existing decay `sorted(tile_bins.items())` last-writer-wins; the in-memory intensity placement uses the same explicit ascending-index loop so intensity and decay resolve every overlap pixel to the same tile (R7). The winner choice is a **consistency** decision, not a quality one — it assumes overlap-region content is near-identical between adjacent tiles (true when registration succeeded). **Disconnected tiles** (registration untrusted, left at their initial grid position) are demoted to **lowest** priority so a mis-placed disconnected tile can never overwrite a correctly-registered neighbor's overlap region; this is applied identically to intensity and decay. (Default resolution of the A6 finding; the bleaching/SNR-based winner question is recorded in Open Questions.)
- **Cross-field validation (`register ⇒ overlap>0 ∧ reference set`) lives at the importer gate, not in `TileConfig.__post_init__`.** Keep `TileConfig` a dumb carrier so every existing `TileConfig(grid_rows=1, grid_cols=1)` keeps constructing; the gate `register ∧ overlap>0 ∧ grid>1×1` is the natural enforcement point.
- **`native_shape` stays the authoritative lock.** The registered canvas (post-`rotate_k`) *becomes* `native_shape` at compress; the append derives its expected canvas from persisted offsets and reuses the existing `LayerSizeMismatchError` — no parallel guard, no silent resize.

---

## Open Questions

### Resolved During Planning

- Where do per-tile offsets live? → A `stitch/tile_offsets` **dataset** via `store.write_array` (not a metadata attr — attrs are capped at 64 KB and not array-typed on read-back). Read-back raises `KeyError` when absent (catch it for the back-compat "not registered" branch).
- One offset array or per-channel? → **One** shared array per dataset (all channels reuse the single reference-channel solve).
- Vendor vs skimage? → **Vendor** the numpy-only *computational core* of `grid_stitching` (not the file-I/O modules) into `domain/_vendor/`.
- Where does registration math live? → Pure functions in `domain/io/assembler.py`; persistence at the store boundary; the computation/placement (P5) seam is the boundary between `estimate_tile_offsets` and `assemble_tiles_with_offsets`.
- How is decay placed at registered offsets? → Feed pixel `(y0,x0)` offsets into the existing `write_decay_streaming` (reinterpret its positions as absolute pixels); never stitch decay in memory; never route through vendored `fuse`.
- **Offset coordinate convention vs `creation_bin`?** → Register on the **post-`creation_bin`** plane (between `importer.py:497` and `:500`); persist post-bin offsets so `bbox(offsets) == native_shape`. Pre-bin offsets would trip `MetadataConsistencyError`. (Resolved by repo-grounded research.)
- **Recompute-equality / tie-break stability?** → Closed by construction: persisted offsets are the **final rounded non-negative integers**, and the canvas is `max(y0+h, x0+w)` over those integers, so the append recomputes the identical canvas from persisted ints with no float involved. No deterministic-tie-break work needed for v1.
- **Validation home for `register ⇒ overlap>0`?** → **Call-site (importer gate)**, not `TileConfig.__post_init__`; keep the dataclass a dumb carrier so 1×1 construction stays valid.
- **Disconnected-tile disposition (default; overridable).** → **Warn loudly + place at lowest overwrite priority** so an untrusted tile never overwrites a registered neighbor; **hard-fail** the import when the disconnected fraction exceeds the R11 success-gate threshold (a degenerate solve is not silently stored as registered). Pinned in U3/U6 with tests.
- **Z-stack-mosaic scope (default; overridable).** → **Defer** z-stack-mosaic overlap to follow-up; v1 operates on already-z-projected 2D mosaics (see Scope Boundaries). Resolves review finding A4.
- **Time-lapse inter-frame drift (default; overridable).** → **Register once, reuse across timepoints**, document the zero-drift assumption, and **re-check correlation on the last timepoint** to warn when drift exceeds the overlap budget (no per-timepoint re-registration in v1). Resolves review finding A5.
- **Overlap winner rule (default; overridable).** → **Ascending tile index** (consistency between intensity and decay), assuming near-identical overlap content; disconnected tiles demoted. An SNR/recency-based winner is out of v1. Resolves review finding A6.

### Deferred to Implementation

- **Exact `grid_stitching` API plumbing.** Call `compute_pairwise_shifts` + `optimize_positions` directly on `Tile`s built from `grid_positions` with images attached (the `Stitcher` orchestration class is not vendored). Either way the `(x,y)` positions convert to non-negative integer `(y0,x0)` — see the axis-swap test target in U3.
- **`grid_stitching` licensing/attribution.** No LICENSE file in the source package; add an origin/attribution header to the vendored copy (user-owned reimplementation of the published Preibisch algorithm). Confirm with the user at vendor time.
- **Reference-channel default.** Required selection vs default-to-first; identify by channel **name** (stable), not index. Settle in U8 UI wiring. Note: a poorly-textured reference can produce a degenerate solve — the R11 success gate catches it, but the UI should steer the user toward the highest-contrast channel.
- **Intensity canvas fill value & coverage policy.** With non-rectangular stage drift, the registered canvas may have uncovered pixels (HDF5 fill 0.0). Decide the intensity fill value and whether uncovered pixels are distinguishable from genuine zero signal (see Risks); record the coverage fraction in provenance. Resolve in U3/U6 with a phasor-guard test in U9.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
COMPRESS / IMPORT (register-once, F1)                 gate: register ∧ overlap>0 ∧ grid>1×1
──────────────────────────────────────────           (validated at the importer gate)
 reference channel PER-TILE arrays  ── retained before stitching consumes them; post-creation_bin, post-z-proj ──
        │  estimate_tile_offsets()   (vendored compute_pairwise_shifts / optimize_positions, compute only)
        │      → final rounded non-negative int (y0,x0), per-axis min 0  + success gate (degenerate → raise)
        ▼
   offsets (N,2) int32 ≥0   ──canvas_from_offsets()──▶  canvas (H,W)   [the ONLY canvas computation]
        │                                                 │
        │  quality{correlations, disconnected[], coverage_fraction}
        │
        ├─▶ every intensity channel:  assemble_tiles_with_offsets(tiles, offsets, canvas, priority=asc idx)
        │       (hand-written integer overwrite loop — NOT vendored fuse)
        ├─▶ decay-at-import:          write_decay_streaming(..., pixel_offsets=offsets)   ── overwrite, streamed
        │
        │   ── assert bbox(offsets) == assembled /intensity.shape[-2:]  (post-rotate) ──
        │   native_shape := canvas (post-rotate)
        │
        └─▶ COMMIT (flag-written-LAST ordering primary; create_atomic = stretch):
                store.write_stitch_geometry(offsets, StitchProvenanceRecord)   ── P4/P6
                  → dataset  stitch/tile_offsets        → /provenance/stitch (incl coverage, engine params)
                  → /metadata attrs: reference_channel, overlap, then stitch_registered=True  ◀── commit point

DECAY-ONLY APPEND (consume, F2)        ── read geometry FRESH via repository, never a metadata snapshot ──
──────────────────────────────
 read_stitch_geometry()
   ├─ registered & offsets present → place decay at SAME offsets (write_decay_streaming, +invalidate /phasor)
   │     → expected canvas = canvas_from_offsets() ; assert == native_shape (after rotate_k) else LayerSizeMismatchError
   ├─ registered flag set but offsets absent → ERROR (no silent grid fallback)        (AE4)
   └─ not registered → existing _tile_positions grid path, unchanged                  (back-compat)

0%-OVERLAP / SINGLE-TILE (F3)  →  assemble_tiles / write_decay_streaming grid path, byte-identical (gate false)
```

---

## Implementation Units

Phased delivery: **Phase 1 (Foundation, U1–U4)** is pure/independent and lands first;
**Phase 2 (Import & append wiring, U5–U7)** depends on it; **Phase 3 (GUI & verification,
U8–U9)** closes the loop.

### Phase 1 — Foundation

- U1. **Vendor the `grid_stitching` engine into the domain layer**

**Goal:** Bring the numpy-only phase-correlation engine into the repo, pinned and import-linter-clean.

**Requirements:** R12

**Dependencies:** None

**Files:**
- Create: `src/percell4/domain/_vendor/__init__.py`
- Create: `src/percell4/domain/_vendor/grid_stitching/__init__.py` (re-export **only the computational core**: `register_pair`, `phase_correlation_matrix`, `PeakResult`, `compute_pairwise_shifts`, `optimize_positions`, `PairwiseShift`, `Tile`, `grid_positions`. Do **not** re-export `load_tile_images` / `parse_tile_configuration` / `write_tile_configuration` / `Stitcher` — file-I/O helpers/orchestration that would leak `tifffile`/`skimage.io`/`open` into the domain layer.)
- Create: `src/percell4/domain/_vendor/grid_stitching/{tiles,phase_correlation,optimize}.py` (copied from `/Users/leelab/Downloads/grid_stitching/grid_stitching/`). From `tiles.py`, **strip** `load_tile_images`/`parse_tile_configuration`/`write_tile_configuration` (and their `tifffile`/`skimage`/`open` imports); keep only `Tile` + `grid_positions`.
- Create: `src/percell4/domain/_vendor/grid_stitching/pairwise.py` — **relocate** `compute_pairwise_shifts` and its private helpers (`_overlap_region`, `_img_shape_xy`, `_slice_xy`, `_imgshift_to_xy`) out of the source `stitcher.py` (where they are defined) into this module. This is required because U3 and the `__init__` re-export both need `compute_pairwise_shifts`, but it lives in `stitcher.py` alongside the `Stitcher` class whose module-level imports pull the stripped file-I/O names — copying `stitcher.py` whole would fail to import. Do **not** vendor `stitcher.py`, `fuse.py`, or `setup.py`.
- Test: `tests/test_domain/test_vendor_grid_stitching.py`

**Approach:**
- Vendor the **numpy-only computational subset** (`tiles` minus file I/O, `phase_correlation`, `optimize`, `pairwise`), not the whole package. `fuse.py` is **not** vendored — placement is hand-written (U3), so vendoring `fuse` would ship pure dead code (resolves review finding S2).
- Add a short origin/attribution header comment to each file (source: user's `grid_stitching`, algorithm: Preibisch et al. 2009). Confirm license posture with the user (see Deferred to Implementation).
- **Enforcement note:** the import-linter contract (`pyproject.toml`) forbids only `qtpy`/`PyQt5`/`napari`/`h5py`/`laptrack` from `domain` — it does **not** forbid `tifffile`/`skimage`/`scipy`. So the linter will **not** catch a leaked file-I/O import; the U1 test must assert directly that importing `percell4.domain._vendor.grid_stitching` pulls in no `tifffile`/`skimage`/`h5py`/`qtpy` (resolves review finding F6). Also verify no `import scipy` leaks in.

**Patterns to follow:** existing `domain/` subpackage layout (`flim/`, `measure/`); `domain/io/assembler.py` pure-numpy module contract.

**Test scenarios:**
- Happy path: `register_pair` recovers a known integer shift between two synthetically-shifted overlapping arrays (port `test_stitching.py::test_pairwise` into pytest).
- Happy path: `grid_positions` + `compute_pairwise_shifts` + `optimize_positions` on a 3×3 grid with small jitter recovers positions to within ±tolerance (port the registration half of `test_full_pipeline`, using the core functions directly — not the `Stitcher` orchestration class, which is not vendored).
- Edge case: importing `percell4.domain._vendor.grid_stitching` pulls in no forbidden import — assert `tifffile`/`skimage`/`h5py`/`qtpy` are not imported by it (or rely on the existing import-linter test).

**Verification:** the vendored core imports cleanly with numpy only; ported tests pass; import-linter and `tests/test_qt_free_imports.py` stay green.

---

- U2. **Extend `TileConfig` and add `StitchProvenanceRecord`**

**Goal:** Carry overlap/register/reference-channel through the config, and define the provenance payload shape.

**Requirements:** R1, R2, R4, R13

**Dependencies:** None

**Files:**
- Modify: `src/percell4/domain/io/models.py`
- Test: `tests/test_domain/test_models_tile_config.py` (create), `tests/test_io/test_models_provenance.py` (extend or create)

**Approach:**
- Add to `TileConfig` (frozen): `overlap: float = 0.0`, `register: bool = False`, `reference_channel: str | None = None`. Extend `__post_init__` only with the **single-field** rule `0.0 <= overlap < 1.0`. Keep the dataclass a **dumb carrier**: the cross-field rule `register ⇒ overlap>0 ∧ reference_channel set` is enforced at the **importer gate** (U6), not in `__post_init__`, so every existing `TileConfig(grid_rows=1, grid_cols=1)` still constructs (back-compat is load-bearing — see U8's four-hop threading test).
- Add `StitchProvenanceRecord` (frozen, **str/bool/JSON-string fields only** for attr round-trip): `reference_channel: str`, `overlap: str`, `library: str` (name+identity), `quality_json: str` (per-pair correlations, disconnected-tile indices, `accepted_pair_fraction`, `coverage_fraction`, and the engine parameters `regression_threshold` + `n_peaks` so the solve is reproducible from the record — R12), `n_tiles: str`, `importer_version: str`, `timestamp_utc: str`. Provide `to_attrs()`.

**Patterns to follow:** `ProvenanceRecord` (`models.py:173-199`) — frozen, str/bool fields, `to_attrs()`; existing `TileConfig.__post_init__` validation style.

**Test scenarios:**
- Happy path: `TileConfig(grid_rows=1, grid_cols=1)` constructs with `register=False, overlap=0.0` (back-compat); two default instances are `==` and hash-equal.
- Edge case: `overlap` out of `[0,1)` raises in `__post_init__`.
- Edge case: `TileConfig(register=True, overlap=0.0)` **constructs without error** (cross-field validation is deferred to the importer gate, not the dataclass) — a guard against re-coupling validation into the frozen carrier.
- Happy path: `StitchProvenanceRecord.to_attrs()` returns an all-str/bool dict including `quality_json` with a `coverage_fraction` key (round-trips as HDF5 attrs).

**Verification:** new fields default to today's behavior; single-field validation rejects bad overlap; cross-field rule is not in the dataclass; provenance record is attr-safe and carries coverage.

---

- U3. **Pure registration + offset-aware placement in `assembler.py`**

**Goal:** Compute integer per-tile offsets from overlapping reference tiles, and place tiles at arbitrary offsets with overwrite fusion — without touching the existing `assemble_tiles`.

**Requirements:** R3, R5, R7, R14

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/domain/io/assembler.py`
- Test: `tests/test_io/test_stitch_register.py` (create)

**Approach:**
- `canvas_from_offsets(offsets, tile_shape) -> (H, W)`: the **single** canvas computation — `max(y0+h)`, `max(x0+w)` over the final integer offsets (use **`math.prod`** for any element/byte sizing). `native_shape`, the intensity allocator, and the decay allocator all consume this; no recomputation elsewhere.
- `estimate_tile_offsets(reference_tiles, grid_rows, grid_cols, grid_type, order, overlap) -> (offsets_int32_Nx2_yx, canvas_hw, quality)`:
  - **Seed positions:** map PerCell4's two-field `(grid_type, order)` (e.g. `row_by_row` + `right_down`) onto the vendored `grid_positions` single hyphenated `order` vocabulary (`row-by-row`/`column-by-column`/`snake-by-rows`/`snake-by-columns`) — provide an explicit mapping table so the scan pattern is not mis-seeded (resolves review finding F4). `grid_positions` returns image-less `Tile`s, so **attach each reference tile array onto its `Tile.image`** before registering (resolves F5).
  - **Register:** call `compute_pairwise_shifts` + `optimize_positions` directly (the `Stitcher` class is not vendored). Convert `(x,y)` positions → image-axis `(y0,x0)`, round each to int, then subtract per-axis min so origin is `(0,0)` and **per-axis min is exactly 0** (assert this — the canvas derivation depends on it).
  - **Quality + success gate:** `quality` carries per-pair correlations, the list of tiles left at their initial (disconnected) position, `accepted_pair_fraction`, `coverage_fraction`, and the engine `regression_threshold`/`n_peaks`. Raise a clear error when the solve is **degenerate** — too few pairs clear `regression_threshold` (most/all tiles disconnected), which would otherwise collapse to a grid-equivalent canvas yet be stored as "registered" (R11; resolves review finding A3).
- `assemble_tiles_with_offsets(tiles, offsets, canvas_hw, fill_value, disconnected) -> ndarray`: a **hand-written integer overwrite loop** (NOT vendored `fuse`, which upcasts to float64 and iterates in list order). Allocate the canvas at `fill_value` (decide the fill so uncovered pixels are distinguishable — see Open Questions), place each tile at its `(y0,x0)` with plain overwrite, iterating in **ascending tile index** so the highest index wins overlaps (identical to the decay streamer's `sorted(tile_bins.items())`) — except **disconnected tiles are placed first (lowest priority)** so they never overwrite a registered neighbor (resolves review finding A6).
- Reject non-uniform tile shapes with a clear `ValueError` (R14). Keep `assemble_tiles` and `_tile_positions` unchanged.

**Execution note:** Implement test-first — offset recovery, overlap-winner, and the axis-order conversion have exact expected values on synthetic inputs.

**Test scenarios:**
- Happy path: tiles shifted by a known offset → `estimate_tile_offsets` recovers offsets within ±tolerance; `canvas_from_offsets` == expected bbox.
- **Axis-swap guard (critical):** a tile shifted by a deliberately **asymmetric** offset (dy=10, dx=3) recovers `(y0,x0)=(10,3)`, **not** `(3,10)` — symmetric offsets would not catch an `(x,y)↔(y,x)` transposition (the axis swap is where the prior critical FLIM bug lived).
- Covers AE1. Edge case: two overlapping tiles with distinct constant values → `assemble_tiles_with_offsets` returns the **higher-index** tile's value at every overlap pixel (pinned priority).
- Edge case (disconnected demotion): a disconnected tile overlapping a registered neighbor → the **registered** tile wins the overlap (disconnected placed at lowest priority), and the disconnected tile is reported in `quality.disconnected` with a warning.
- Error path (degenerate solve): inputs where most/all pairs fall below `regression_threshold` → `estimate_tile_offsets` raises rather than returning a grid-equivalent canvas (R11 success gate).
- Edge case (seed mapping): each PerCell4 `(grid_type, order)` pair seeds the same grid layout as the corresponding `grid_positions` order — assert the mapping table.
- Edge case: a disconnected tile that **expands** the bounding box → `canvas_from_offsets` includes it and equals the assembled array shape.
- Edge case: offsets always have per-axis min 0 (assert on the returned array).
- Error path: non-uniform tile shapes → `ValueError`.
- Edge case: large canvas dims whose product exceeds int32 → `math.prod` sizing does not overflow.

**Verification:** offsets are non-negative integers with per-axis min 0; one `canvas_from_offsets` is the sole canvas source; placement is a deterministic integer loop whose overlap-winner matches the pinned priority and the decay streamer; `assemble_tiles` output is unchanged for existing tests.

---

- U4. **Store persistence + read-back for stitch geometry (P4/P6)**

**Goal:** Persist and reload the offset array + provenance + scalar flags through the single store write boundary.

**Requirements:** R4, R6, R12

**Dependencies:** U2

**Files:**
- Modify: `src/percell4/store.py`
- Test: `tests/test_store.py` (extend), `tests/test_io/test_store_append.py` (extend)

**Approach:**
- `write_stitch_geometry(offsets_int32, provenance: StitchProvenanceRecord, *, reference_channel, overlap)`: writes `stitch/tile_offsets` via `write_array` (confirmed: arbitrary path, no `is_decay`/`dims` requirement, pass-through view-bin so offsets are never downsampled, lossless int32); writes `/provenance/stitch` group attrs from `provenance.to_attrs()`; sets `/metadata` attrs `stitch_reference_channel`, `stitch_overlap`, and — **strictly last** — `stitch_registered=True` (the commit marker, see U6 ordering). All inside the store (no domain/application h5py).
- `read_stitch_geometry() -> StitchGeometry | None`: returns offsets ndarray + scalar flags. Catch the `KeyError` `read_array` raises when `stitch/tile_offsets` is absent and report `registered=False`/`None` (back-compat for older `.h5`). Read flags fresh from `/metadata` attrs on each call (no in-memory cache on the store — staleness vector).
- Add typed normalization for the new scalar keys in the `metadata` property normalizer at `store.py:1096-1099` (`stitch_overlap`→float, `stitch_registered`→bool), matching how `creation_bin`/`n_timepoints` are cast.

**Patterns to follow:** `write_array`/`read_array` byte-identical round-trip (`store.py:283-345`); `append_decay_layers` inline provenance write (`store.py:1244-1249`); `_write_provenance_attrs` (`add_decay_to_dataset.py:366-375`); the `metadata`-property normalizer (`store.py:1096-1099`).

**Test scenarios:**
- Happy path: write offsets `(N,2) int32`, reopen store, `read_stitch_geometry()` returns `np.array_equal` offsets and the scalar flags typed correctly (`stitch_overlap` float, `stitch_registered` bool).
- Happy path: `/provenance/stitch` attrs round-trip (reference channel, library identity, `quality_json` incl. `coverage_fraction`).
- Edge case: a dataset with no `stitch/tile_offsets` → `read_stitch_geometry()` catches `KeyError` and reports `registered=False`/`None`.
- Edge case: persisted offsets always read back with per-axis min 0 (invariant asserted on read).
- Integration: writing geometry does not perturb `native_shape`/`channel_names` metadata normalization (confirmed: `_infer_bin_metadata` only inspects `/intensity` + `/decay/<ch>`).

**Verification:** geometry round-trips byte-identically; absence is tolerated via `KeyError`; scalars normalize typed; no direct h5py added outside the store.

---

### Phase 2 — Import & append wiring

- U5. **Offset-aware decay placement in `write_decay_streaming`**

**Goal:** Let the canonical decay streamer place tiles at absolute pixel offsets (overwrite), preserving the byte-identical grid path when no offsets are given.

**Requirements:** R8, R10, R7

**Dependencies:** U3

**Files:**
- Modify: `src/percell4/adapters/importer.py` (extend `write_decay_streaming` `:801-888`; delete dead block `:622-666`)
- Modify: `src/percell4/application/use_cases/add_decay_to_dataset.py` (delete dead `_read_and_stitch_decay` `:460-524`)
- Test: `tests/test_io/test_importer_load_and_stitch.py` (extend), `tests/test_add_decay_to_dataset.py` (extend)

**Approach:**
- `write_decay_streaming` is confirmed the **single source of truth** for both compress-time decay (`importer.py:606`) and decay-only append (`add_decay_to_dataset.py:277`). Add an optional `pixel_offsets: dict[int, tuple[int,int]] | None` (absolute post-bin `(y0,x0)` per tile index) to it. When provided, place `dset[y0:y0+h, x0:x0+w, :] = tile` at those offsets, iterating ascending tile index (overwrite priority — same order as the grid path's `sorted(tile_bins.items())`). When `None`, the existing `y0=row·tile_h` grid math (`:881-887`) runs **unchanged**.
- Pre-allocate the canvas from `canvas_from_offsets` (U3), not a local recomputation; keep the `use_tiling=False` single-tile fast path untouched. `tile_h`/`tile_w` here are already post-bin (spatial_bin applied per-tile at `:878-879`), so offsets must be post-bin too (U6).
- **`/phasor/<ch>` invalidation is a hard checklist item:** the new `pixel_offsets` branch must hit the same `del f[phasor_path]` as the grid path — it is trivially easy to add the branch and skip invalidation, leaving a cached phasor computed against old placement.
- **Delete dead code, do not patch it:** the duplicate placement at `importer.py:622-666` (math at `:654-662`) is unreachable (the live `_streaming` branch `continue`s at `:620` before the repeated guard at `:624`); `add_decay_to_dataset.py:460-524` `_read_and_stitch_decay` has zero callers. Removing both prevents a fifth placement convention drifting back in. (Note: `importer.py:462-504` is **live** `.bin` dict-builder + `creation_bin` floor-division — do not touch it.)

**Test scenarios:**
- Covers AE1. Happy path: two overlapping decay tiles (distinct patterned values) placed at offsets → overlap pixels hold the higher-index tile's photons.
- Covers AE3. Happy path: `pixel_offsets=None` produces output byte-identical to the current grid path (golden `np.array_equal`).
- Edge case: single-tile (`use_tiling=False`) path unchanged.
- Integration: a `/phasor/<ch>` present before the offset-path write is invalidated after (explicit assertion).

**Verification:** decay lands at the given post-bin offsets with no read-back; grid path is byte-identical; phasor invalidation fires on the offset branch; both dead placement blocks are gone.

---

- U6. **Register-once orchestration in `import_dataset` (intensity + decay-at-import)**

**Goal:** When gated on, compute geometry once on the reference channel, apply to all intensity channels and decay-at-import, persist it, and lock `native_shape` to the registered canvas.

**Requirements:** R1, R2, R3, R4, R5, R7, R9, R11, R12

**Dependencies:** U3, U4, U5

**Files:**
- Modify: `src/percell4/adapters/importer.py`
- Test: `tests/test_io/test_importer.py` (extend), `tests/test_io/test_importer_load_and_stitch.py` (extend)

**Approach:**
- **Gate (call-site validation):** enter the registered path only when `tile_config.register ∧ tile_config.overlap>0 ∧ grid_rows·grid_cols>1`; if `register` is set without `overlap>0` or a `reference_channel`, raise here (this is the cross-field validation deliberately kept out of `TileConfig`). Otherwise the existing grid path runs verbatim (F3).
- **Re-import guard:** before registering, if the output `.h5` already exists with `stitch_registered=True` and appended decay layers, **refuse** the re-import (re-solving would rewrite offsets that the existing decay was placed against — silent misalignment). Surface a clear error directing the user to a fresh output path. (Resolves review finding S1; geometry is unversioned in v1.)
- **Capture the reference channel's per-tile arrays (the load-bearing restructure):** registration needs the reference channel's *per-tile* arrays at the post-`creation_bin`, post-z-projection plane — but today the TIFF path stitches and discards them inside `_load_and_stitch` (`:761`) and the `.bin` path freezes decay positions into `tcspc_data` (`:460-471`) before the bin step (`:497`). So this unit restructures the import: for the reference channel, **retain the per-tile arrays before stitching consumes them** (and do **not** freeze decay positions until offsets exist). Run `estimate_tile_offsets` on those retained tiles. Offsets are post-bin, so `bbox(offsets)` is in the same units as `native_shape`. For a z-stack mosaic, register on the post-projection reference plane; **v1 supports already-2D mosaics only** and z-stack-mosaic overlap is deferred (Scope Boundaries) — reject a z-stack mosaic + register combination with a clear error.
- **Apply offsets** on this path, replacing the grid-derived `out_h/out_w` + `_tile_positions_from_config` at `importer.py:402-411`: every intensity channel via `assemble_tiles_with_offsets` (intensity synth at `:442-454`; TIFF path via the restructured `_assemble_plane`/`_load_and_stitch`); decay via `write_decay_streaming(pixel_offsets=…)` at the `:606-619` call site. Pass `disconnected` through so both placements demote the same tiles.
- **Time-lapse:** register once on the first timepoint and reuse offsets for all timepoints; re-check correlation on the **last** timepoint and warn if inter-frame drift exceeds the overlap budget (no per-timepoint re-registration — R14).
- **Consistency assertion + native_shape:** because `_infer_bin_metadata` derives `native_shape` from `/intensity.shape[-2:]` (`store.py:86-90`) and `set_metadata` cross-checks it (`MetadataConsistencyError`, `store.py:1116-1125`), the assembled `/intensity` must equal `canvas_from_offsets`. Assert `canvas_from_offsets == assembled /intensity.shape[-2:]` (after applying the same `rotate_k` transpose convention the append guard uses) **before** committing; write that post-rotate canvas as the *only* `native_shape` (`:500-503`, `:516-517`) — never the grid-derived value.
- **Commit ordering (write atomicity):** the **primary** guarantee is ordering — persist geometry via `store.write_stitch_geometry(...)` with `stitch_registered=True` written **strictly last**, gated on the offsets dataset, `/provenance/stitch`, and the assembled intensity canvas all durably present; recovery treats a flag/offsets mismatch as "not committed → safe to re-import," never a hard brick. Full `create_atomic` (temp-then-rename) routing is a **stretch** (the write primitives open by path, not via a shared handle — a real restructure, not wiring).
- **Coverage / fill + success gate:** specify the intensity canvas `fill_value` (U3) so uncovered pixels are distinguishable from genuine zero signal; record `coverage_fraction` in provenance; the degenerate-solve gate (U3) prevents writing `stitch_registered=True` for an all-fallback solve; surface a warning when coverage < 1.0 or any tile is disconnected (R11). Do not silently accept low-confidence results.

**Technical design:** *(directional)* see the COMPRESS/IMPORT block in High-Level Technical Design.

**Test scenarios:**
- Happy path: a 2×2 overlapping import registers, persists offsets, and stores `/intensity` + `/decay` on one canvas; `read_stitch_geometry().registered` is True; `native_shape == canvas_from_offsets`.
- Covers AE1. Integration: in the stored dataset, an overlap pixel resolves to the **same** tile in `/intensity` and in `decay.sum(axis=-1)`.
- Covers AE3. Happy path: the same source with `register=False` (or overlap=0, or 1×1) yields `/intensity` + `/decay` bytes identical to a pre-feature golden.
- Edge case: a binned (`creation_bin>1`) registered import → `bbox(offsets) == native_shape` (post-bin units); no `MetadataConsistencyError`.
- Edge case: odd `rotate_k` registered import → stored `native_shape` is the transposed canvas and the append guard mis-compare does not fire.
- Error path (R14): non-uniform tiles → clear error before any write.
- Error path (degenerate solve): a low-texture reference where most pairs fall below threshold → import raises; `stitch_registered=True` is never written.
- Error path (re-import guard): re-import over an existing `stitch_registered=True` dataset that has appended decay → refused with a clear error.
- Error path (z-stack mosaic): a z-stack mosaic + `register=True` → clear "deferred to follow-up" error (v1 = 2D mosaics).
- Edge case: a disconnected tile / coverage < 1.0 produces a surfaced warning and recorded `quality` entries; the registered neighbor wins the overlap.
- Edge case (time-lapse drift): a synthetic time-lapse with drift on the last frame → warning surfaced; offsets still come from the first timepoint.
- Integration (atomicity): simulate a failure after offsets are written but before the flag → reopening yields a non-registered (recoverable) dataset, never the AE4 brick state.

**Verification:** registered datasets carry persisted geometry and one shared canvas; `native_shape` equals `canvas_from_offsets`; the commit flag is last and crash-safe; degenerate/re-import/z-stack cases fail loudly; non-registered imports are byte-identical to today.

---

- U7. **Decay-only append consumes persisted geometry**

**Goal:** Make `add_decay_to_dataset` reuse the registered offsets verbatim, never recompute, and keep the `native_shape` guard correct on a registered canvas.

**Requirements:** R5, R6, R9, R10

**Dependencies:** U4, U5

**Files:**
- Modify: `src/percell4/application/use_cases/add_decay_to_dataset.py`
- Test: `tests/test_add_decay_to_dataset.py` (extend)

**Approach:**
- Read geometry via the store **fresh** (`read_stitch_geometry()` on a fresh open, not a `handle.metadata` snapshot — per the staleness learning; the append already reads `native_shape`/`channel_names` this way at `add_decay_to_dataset.py:122`). Branch the `out_h/out_w` computation at `add_decay_to_dataset.py:233-244`:
  - `registered` & offsets present → set `out_h/out_w = canvas_from_offsets(offsets, (tile_h, tile_w))` — `canvas_from_offsets` needs the tile shape (offsets are top-left corners), and the append already reads `(tile_h, tile_w)` from the first decay tile; pass it through (resolves review finding C1). This replaces the grid `rows·tile_h`/`cols·tile_w`. Place decay at the persisted `(y0,x0)` via `write_decay_streaming(pixel_offsets=…)`; reuse the **existing** rotate-aware guard verbatim at `:254-271` (which already transposes for odd `rotate_k` at `:256-259`) — only the upstream `out_h/out_w` derivation changes, not the guard logic. Mismatch → existing `LayerSizeMismatchError`.
  - `registered` flag set but offsets absent → **raise** (no silent grid fallback) — AE4.
  - not registered → existing `_tile_positions` grid path, unchanged (back-compat).
- Keep phase order REGISTER(place)→stitch→rotate→flip; rotate/flip stay whole-image, `/decay`-only, and continue invalidating `/phasor/<ch>` (the offset-path decay write also invalidates, U5). Do not recompute registration here.

**Test scenarios:**
- Covers AE2. Happy path: append decay to a registered dataset → decay tiles land at byte-identical pixels to intensity; final `/decay` (H,W) == `native_shape`; no registration runs (assert `estimate_tile_offsets` is not called).
- Covers AE4. Error path: dataset flagged `registered` with `stitch/tile_offsets` missing → raises (not a grid placement).
- Edge case: append to a **non-registered** legacy dataset (no `stitch_registered`) → unchanged grid behavior, succeeds as today.
- Integration: rotate_k odd on a registered dataset → expected-canvas transpose still matches `native_shape`; `/phasor/<ch>` invalidated.
- Edge case: geometry read after a prior in-session decay write reflects the on-disk offsets (fresh read, no stale snapshot).

**Verification:** decay-only append is pixel-coherent with intensity by construction; the guard derives from persisted geometry via `canvas_from_offsets`; back-compat path untouched; registration never re-runs on append.

---

### Phase 3 — GUI & verification

- U8. **Compress-time controls + config threading; reuse affordance on append surfaces**

**Goal:** Expose Overlap%/Register/Reference-channel on Import + Compress (via the canonical widget), thread the new fields through all four hops, and give the append surfaces a read-only "reuse persisted geometry" affordance.

**Requirements:** R13, R1, R2

**Dependencies:** U2

**Files:**
- Modify: `src/percell4/gui/_stitching_flim_form.py` (add controls + extend `.tile_config()`)
- Modify: `src/percell4/gui/import_dialog.py`, `src/percell4/gui/compress_dialog.py` (surface controls; build `TileConfig` with new fields)
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py` (flatten new fields into the plan dict)
- Modify: `src/percell4/workflows/phases.py` (rebuild `TileConfig` with new fields in `compress_one`)
- Modify: `src/percell4/gui/add_layer_dialog.py` (TCSPC + batch tabs: read-only "reuse persisted geometry" affordance; preserve `_tcspc_stitching_user_edited` suppression)
- Test: `tests/test_workflows/test_phases_compress_tile_config.py` (extend), `tests/test_gui/test_compress_dialog_*.py` (extend), `tests/test_gui/test_single_cell_config_dialog_compress_plan.py` (extend)

**Approach:**
- Add controls **once** in `StitchingFlimForm`: an Overlap% spinbox, a Register checkbox, a Reference-channel combo (populated from discovered channels, identified by name). Reuse `itemData` carriers; read existing widget construction end-to-end first (PR #9 drift precedent).
- Thread the three new fields through every hop; a missing hop silently drops the field (the exact bug class `test_phases_compress_tile_config.py` guards) — extend that test for the new fields.
- Append surfaces (AddLayer batch + TCSPC): no registration controls; show a read-only indicator that persisted geometry will be reused (seed from `read_stitch_geometry`), and keep the user-edited re-seed suppression intact.

**Test scenarios:**
- Happy path: setting Register + overlap + reference in `CompressDialog` produces a `TileConfig` with those fields; after plan→`compress_one`, `import_dataset` is called with a `tile_config` carrying them (mock `import_dataset`, assert kwargs).
- Edge case: Register unchecked → `TileConfig.register is False`, overlap `0.0` (gate stays closed).
- Integration: the single-cell config-dialog plan dict serializes and rebuilds the new fields without loss.
- Test expectation (append surfaces): the TCSPC tab shows the reuse affordance when the dataset is registered and suppresses re-seed after a user edit.

**Verification:** new controls live only in the canonical widget; fields survive all four hops; append surfaces reuse geometry read-only.

---

- U9. **Cross-layer alignment regression, golden passthrough tests, audit + docs**

**Goal:** Lock in the scientific invariant and back-compat, and update the audit/learnings record (per the project's audit-driven retrieval conventions in `CLAUDE.md`).

**Requirements:** R9, R10, R12, R1

**Dependencies:** U6, U7

**Files:**
- Create: `tests/test_io/test_stitch_alignment_invariant.py`
- Modify: `docs/audits/io-principles-matrix.yaml`, `docs/audits/canonical-sources-matrix.yaml`
- Create: `docs/solutions/architecture-patterns/overlap-aware-stitching.md`
- Modify: `src/percell4/domain/io/CLAUDE.md` (and/or `src/percell4/CLAUDE.md`) to document the new registered path

**Approach:**
- **Alignment-invariant test** (from the critical FLIM learning): run the *same* overlapping source through (a) compress-with-registration and (b) decay-only append on a registered dataset; assert the resulting per-pixel `(g, s, g_filtered, s_filtered)` match (modulo any documented permutation). Run **in-process**, not via subprocess. Derive intensity via `decay.sum(axis=-1)`.
- **Golden passthrough test**: single-tile and 0%-overlap imports byte-identical (`np.array_equal`) to pre-feature output (the "matcher-refactor scoping collapse" defense).
- Update the audit matrices: assembler now has a data-dependent path (re-evaluate its cells), and importer/add_decay gain stitch-geometry persistence; record the new canonical source. Author the `docs/solutions/` entry with `applies_to` globs + `canonical_source`.

**Execution note:** Characterization-first — write the golden passthrough assertion before touching shared placement code if any U5/U6 refactor risks the legacy path.

**Test scenarios:**
- Covers AE2/AE1. Integration: compress-registered vs append-registered phasor maps `(g, s, g_filtered, s_filtered)` match per-pixel (in-process; intensity derived via `decay.sum(axis=-1)`).
- Covers AE3. Happy path: single-tile and 2×2 0%-overlap golden bytes unchanged.
- Edge case: registered dataset, then a fresh in-process read of geometry reproduces identical placement (reproducibility, R12; offsets are persisted ints so no recompute).
- Edge case (coverage gap): a non-rectangular registered mosaic with an uncovered canvas region → that region's decay is all-zero and the phasor guard yields `g=s=0` there (no fill-zeros leaking into a measured cell); the intensity fill is distinguishable per the chosen `fill_value`.
- Edge case (atomicity): a registered import interrupted after offsets but before the `stitch_registered` flag reopens as non-registered/recoverable, never the AE4 brick state.

**Verification:** the alignment invariant holds across compress and append; uncovered pixels do not pollute measurements; legacy outputs are byte-identical; the commit flag is crash-safe; audit matrices and a `docs/solutions/` entry reflect the new path.

---

## System-Wide Impact

- **Interaction graph:** `import_dataset` (intensity + decay-at-import), `add_decay_to_dataset` (append), `write_decay_streaming` (decay placement), `DatasetStore` (geometry persistence, `create_atomic`), Import + Compress dialogs + `StitchingFlimForm`, the compress plan dict + `compress_one`. FLIM phasor compute consumes the aligned canvas downstream.
- **Canvas single-sourcing:** `native_shape`, the intensity allocator, and the decay allocator all derive from one `canvas_from_offsets`; recomputation in multiple places is the divergence vector and is forbidden. `_infer_bin_metadata` derives `native_shape` from `/intensity.shape[-2:]`, so the assembled intensity must equal `canvas_from_offsets` or `set_metadata` raises `MetadataConsistencyError`.
- **Write atomicity / commit point:** `import_dataset` is non-atomic across multiple file opens; the registered path routes through `create_atomic` and writes `stitch_registered=True` strictly last so a crash never yields the AE4 registered-but-absent brick state.
- **Error propagation:** non-uniform tiles → `ValueError` before any write; `register` without `overlap>0`/reference → `ValueError` at the gate; registered-but-offsets-absent on append → raise; canvas ≠ `native_shape` → existing `LayerSizeMismatchError`; stored-vs-inferred `native_shape` drift → `MetadataConsistencyError`. Surface (not swallow) low-confidence/low-coverage registration.
- **State lifecycle risks:** in-session staleness — read geometry fresh on each call, never a `handle.metadata` snapshot and no in-memory cache on the store; the offset-path decay write must invalidate `/phasor/<ch>` exactly like the grid path (a hard checklist item, easy to omit on the new branch).
- **Data integrity (coverage gaps):** a non-rectangular registered canvas can leave uncovered pixels (HDF5 fill 0.0) — new vs the gap-free grid path. Decay uncovered pixels are guarded by the phasor `intensity≤0` rule; the intensity canvas `fill_value` must keep uncovered pixels distinguishable from real zero signal so segmentation/measurement don't ingest them. Coverage fraction is recorded in provenance.
- **API surface parity:** decay and intensity placement both honor the same `(offsets, canvas)` contract and the same ascending-index overwrite priority; compress and append decay both remain the single `write_decay_streaming` source of truth.
- **Integration coverage:** the compress-vs-append phasor-equality test is the cross-layer scenario unit tests alone cannot prove.
- **Unchanged invariants:** `assemble_tiles` / grid `write_decay_streaming` output for 0%-overlap and single-tile inputs is byte-identical; `native_shape` remains the authoritative lock with its existing guard exceptions; FLIM consumers keep deriving intensity from `decay.sum(axis=-1)`; the dead placement blocks are deleted, not left patchable.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Registered path silently entered for existing 0%-overlap imports, breaking byte-identity | Hard gate `register ∧ overlap>0 ∧ grid>1×1`; golden passthrough tests (U9); don't even import the engine when gated off |
| Decay misregisters vs intensity (silent, scientifically wrong — prior critical bug) | One solve, reused verbatim; same offsets + same ascending-index overwrite priority for both; axis-swap test (U3); alignment-invariant regression test (U9) |
| Crash mid-import leaves a half-registered file → AE4 brick state permanently | Route registered import through `create_atomic`; write `stitch_registered=True` strictly last (commit point); recovery treats flag/offsets mismatch as "not committed" (U6); atomicity test (U9) |
| Destructive / repeat re-import rewrites offsets that prior appended decay was placed against → silent misalignment | U6 re-import guard refuses re-import over a `stitch_registered=True` dataset that already has appended decay; geometry is unversioned in v1 (documented) |
| Low-texture reference channel → all pairs below threshold → grid-equivalent canvas silently stored as "registered" | R11 success gate raises on a degenerate solve (U3); engine params recorded in provenance; UI steers toward the highest-contrast reference (U8) |
| Reference channel's per-tile arrays are consumed/discarded before the registration point | U6 restructures the import to retain the reference channel's per-tile arrays (post-bin, post-z-projection) before stitching consumes them, and defers freezing decay positions until offsets exist |
| `compute_pairwise_shifts` lives in the unvendored `stitcher.py` → vendored core won't import | U1 relocates `compute_pairwise_shifts` + its private helpers into a `pairwise.py` module; `Stitcher`/`fuse`/file-I/O not vendored |
| Time-lapse inter-frame drift reuses stale first-frame offsets → seam/FLIM misalignment on late frames | Register-once + last-timepoint correlation warning (U6); per-timepoint drift correction explicitly deferred; assumption documented (R14) |
| `MetadataConsistencyError` on the registered `native_shape` | Never write a grid-derived `native_shape` on the registered path; write `canvas_from_offsets` (post-rotate) as the only value; assert `== assembled /intensity.shape` before commit (U6) |
| Uncovered pixels (HDF5 fill 0.0) pollute intensity measurements / phasor | Choose an intensity `fill_value` keeping gaps distinguishable; record `coverage_fraction` in provenance; phasor-guard + coverage test (U3/U6/U9) |
| Canvas computed differently in 3 places → drift | Single `canvas_from_offsets` helper consumed by native_shape + both allocators; no recomputation (U3) |
| Persisted offsets lose the min-0 origin → canvas/placement diverge | Normalize to per-axis min 0; assert on write **and** read (U3/U4) |
| Per-tile offsets don't round-trip (attrs cap / typing) | Persist offsets as a `write_array` **dataset** (pass-through view-bin), scalars as typed metadata attrs; catch `KeyError` on absent; round-trip test (U4) |
| New `TileConfig` field dropped at a config hop | Thread through all four hops; extend `test_phases_compress_tile_config.py` (U8) |
| Larger registered canvas overflows size math on Windows | Use `math.prod` for all element/byte counts (U3, U5) |
| New direct-h5py write violates P4 | Persist via a real `DatasetStore` method (U4); no `h5py.File('a')` in domain/application |
| Vendored I/O helpers leak `tifffile`/`skimage` into pure domain | Vendor only the numpy computational core; drop file loaders + `Stitcher` file constructors (U1) |
| Vendored package licensing unclear (no LICENSE) | Add attribution header; confirm posture with user at vendor time (U1, Deferred) |
| Offset units vs `creation_bin` mismatch | Register on the post-`creation_bin` plane so `bbox(offsets)==native_shape`; binned-import test (U6) |

---

## Documentation / Operational Notes

- Update `src/percell4/domain/io/CLAUDE.md` to document the registered stitch path and the offsets-as-dataset + `/provenance/stitch` convention (U9).
- Author `docs/solutions/architecture-patterns/overlap-aware-stitching.md` with `applies_to` globs and `canonical_source` (U9), and update both audit matrices in the same PR (per the project's audit-driven retrieval conventions in `CLAUDE.md`).
- No migration/backfill for pre-feature datasets — they keep the grid path automatically (absent `stitch_registered`).

---

## Phased Delivery

### Phase 1 (U1–U4)
Pure, independently testable foundation: vendor the engine, extend config + provenance models, add registration/placement math, add store persistence. No behavior change to existing paths yet.

### Phase 2 (U5–U7)
Wire registration into compress (intensity + decay-at-import) and decay-only append, with the byte-identical grid path preserved behind the gate.

### Phase 3 (U8–U9)
GUI controls + config threading + append-surface affordance, then the cross-layer alignment regression, golden passthrough tests, and audit/docs updates.

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-06-24-mosaic-merge-overlap-stitching-requirements.md](docs/brainstorms/2026-06-24-mosaic-merge-overlap-stitching-requirements.md)
- Engine to vendor: `/Users/leelab/Downloads/grid_stitching/` (numpy-only; Preibisch et al. 2009 phase-correlation grid stitching)
- Canonical decay path: `docs/solutions/architecture-patterns/decay-write-path.md`
- Critical alignment learning: `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`
- Shared-widget pattern: `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
- Staleness learning: `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
- Windows overflow learning: `docs/solutions/logic-errors/numpy-prod-int32-overflow-windows-2026-06-07.md`
- `native_shape` lock origin: `docs/plans/2026-05-18-001-feat-dataset-wide-spatial-binning-plan.md`
- Audit matrices: `docs/audits/io-principles-matrix.yaml`, `docs/audits/canonical-sources-matrix.yaml`
