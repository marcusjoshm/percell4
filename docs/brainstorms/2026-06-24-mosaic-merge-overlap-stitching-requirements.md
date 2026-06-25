---
date: 2026-06-24
topic: mosaic-merge-overlap-stitching
---

# Mosaic Merge — Overlap-Aware (Phase-Correlation) Tile Stitching

## Problem Frame

PerCell4 today stitches tiled acquisitions with exactly one method: `assemble_tiles()`
in `src/percell4/domain/io/assembler.py` places tiles edge-to-edge on a fixed grid
(`y0 = row·tile_h`, `x0 = col·tile_w`), with **no overlap and no registration**. This is
correct only for acquisitions captured at 0% overlap with a perfectly indexed stage.

Researchers increasingly capture mosaics **with X% tile overlap** specifically so the
overlap can be used to correct stage-positioning error and produce a seamless composite —
the workflow the Fiji *Grid/Collection Stitching* plugin serves. PerCell4 has no path for
this. The user wants overlap-captured tiles stitched by phase-correlation registration,
while **0%-overlap tiles keep the current edge-to-edge method unchanged**, everywhere
stitching exists — including the TCSPC **decay** path.

The decay path is the crux. Decay (TCSPC) tiles are not stitched in memory; they are
*streamed* tile-by-tile into the HDF5 `/decay` dataset (`write_decay_streaming`), and a
`native_shape` guard forces the decay composite to be pixel-identical to the intensity
composite so per-pixel phasor/lifetime stays aligned to the segmentation. Phase-correlation
produces a **data-dependent** canvas, which directly collides with that guard unless the
geometry is computed once and reused.

A reference implementation exists at `/Users/leelab/Downloads/grid_stitching` (a pure
numpy phase-correlation reimplementation of the Fiji plugin; **no new dependencies** for
PerCell4).

---

## Actors

- A1. Researcher (import): declares the grid, the overlap %, and the reference channel for a
  tiled mosaic at import/compress time.
- A2. Import/compress pipeline: computes the registered geometry once, persists it, and
  applies it to every intensity channel and to the decay-at-import stream.
- A3. Decay (TCSPC) append flow (`add_decay_to_dataset`): adds `.bin` decay to an
  already-stitched dataset; **consumes** persisted geometry, never recomputes it.
- A4. Downstream consumers (phasor compute, per-cell measurement, segmentation): depend on
  intensity↔decay pixel coherence on a single shared canvas.

---

## Key Flows

- F1. **Register-once at import**
  - **Trigger:** A1 imports a tiled mosaic with Register on, overlap > 0, grid > 1×1.
  - **Actors:** A1, A2
  - **Steps:** seed initial tile positions from grid + overlap → phase-correlate adjacent
    tiles on the reference intensity channel → solve global integer per-tile pixel offsets →
    normalize to a non-negative origin → persist offsets + canvas shape + reference channel +
    overlap + registered flag + quality → apply the *same* offsets (overwrite fusion) to every
    other channel and to the decay-at-import stream.
  - **Outcome:** all layers share one byte-coherent canvas; geometry is recorded as provenance.
  - **Covered by:** R1, R2, R3, R4, R5, R7, R8, R11, R12

- F2. **Decay-only append reusing persisted geometry**
  - **Trigger:** A3 appends `.bin` decay to a dataset whose intensity was registered.
  - **Actors:** A3, A4
  - **Steps:** read persisted per-tile pixel offsets + canvas shape from metadata → place each
    decay tile at the exact intensity pixel offsets via the streaming write (overwrite) → verify
    canvas equals `native_shape` (after any rotate_k) → invalidate stale phasor.
  - **Outcome:** decay is pixel-aligned to intensity; no registration is re-run.
  - **Failure/escape:** if the dataset is flagged registered but offsets are absent →
    error (no silent grid fallback). If offsets present but canvas ≠ `native_shape` →
    existing `LayerSizeMismatchError`.
  - **Covered by:** R5, R6, R9, R10

- F3. **0%-overlap / single-tile passthrough (unchanged)**
  - **Trigger:** Register off, or overlap = 0, or grid is 1×1.
  - **Actors:** A1, A2, A3
  - **Steps:** existing `assemble_tiles` / `write_decay_streaming` grid path runs verbatim.
  - **Outcome:** stored bytes are identical to the pre-feature behavior.
  - **Covered by:** R1

---

## Architecture at a glance

```
                  ┌─────────────────────────────────────────────┐
   reference  ──▶ │ phase-correlation registration (ONCE)        │
   intensity      │  seed from grid+overlap → refine → solve     │
   channel        │  → integer per-tile (y0,x0), canvas (H,W)    │
                  └───────────────┬─────────────────────────────┘
                                  │ persist as provenance in .h5
                                  ▼
            ┌──────────────  /metadata  ──────────────┐
            │ stitch_registered, tile offsets (N,2),  │
            │ canvas_shape, reference_channel, overlap│
            └───────────────┬───────────────┬─────────┘
       reuse verbatim       │               │   reuse verbatim
       (overwrite fuse)     ▼               ▼   (overwrite, streamed)
              other intensity channels   decay-at-import  +  decay-only append
                         │                       │                  │
                         └────────── ONE shared canvas ─────────────┘
                              intensity ↔ decay pixel-coherent
```

---

## Requirements

**Activation & scope gating**
- R1. The overlap-aware registration path is opt-in and entered **only** when
  `register = True` **and** `overlap > 0` **and** `grid_rows · grid_cols > 1`. In every other
  case (Register off, 0% overlap, or single tile) the existing `assemble_tiles` /
  `write_decay_streaming` grid path runs verbatim and produces byte-identical output to today.
  The `use_tiling=False` single-tile fast path is untouched.
- R2. Registration is computed **exactly once**, at compress/import time, on a single
  user-selected **reference intensity channel** — never per channel, per z-plane, per
  timepoint, or on decay.

**Geometry computation & reuse**
- R3. Registration seeds initial tile positions from the declared grid + overlap %, refines
  them by phase correlation on the reference channel, solves global integer per-tile pixel
  offsets, and normalizes them to a non-negative origin `(0,0)`. The resulting canvas shape is
  the bounding box of placed tiles (data-dependent; not a multiple of tile size).
- R4. The computed per-tile pixel offsets, canvas shape, reference-channel name, overlap
  fraction, and a `registered` flag are persisted into the dataset as provenance, written
  through the single store write boundary (no direct `h5py`). Coordinates are stored in the
  same pre/post-`creation_bin` convention as `native_shape`.
- R5. Every other intensity channel, z-plane, timepoint, and the decay stream **reuse the
  persisted offsets verbatim** (apply with re-registration disabled). Decay is never
  registered independently; all layers share one geometry solve.
- R6. The decay-only **append** flow reads persisted offsets and reuses them. If the dataset
  is flagged `registered` but offsets are absent, it errors rather than falling back to grid
  placement. If `registered` is absent/false, it runs the existing grid path unchanged
  (backward compatibility for pre-feature `.h5`).

**Fusion**
- R7. Overlap fusion is **overwrite** (last-writer-wins) for intensity, all channels, and
  decay, under one **pinned deterministic tile priority** (ascending tile index) applied
  identically across every layer, so intensity and decay resolve each overlap pixel to the
  **same** tile. No feather/blend/average/sum/max anywhere.
- R8. Decay overwrite uses the existing streamed write (place tile at its pixel offset; no
  read-back, no in-memory fusion, no photon double-counting — photons are summed only within a
  tile's spatial bin). The registration library is used **only to compute offsets**, never to
  fuse decay.

**Shape & placement invariants**
- R9. The registered canvas becomes the dataset `native_shape`. The decay-append
  `native_shape` guard is preserved, but its expected canvas is derived from the **persisted
  registered canvas**, not from `grid·tile`; the `rotate_k` odd-transpose wrap still applies.
  Phase ordering stays REGISTER(place) → stitch → rotate → flip, with rotate/flip whole-image
  and `/decay`-only, and stale `/phasor/<ch>` invalidated as today.
- R10. The three placement sites that hardcode `y0 = row·tile_h, x0 = col·tile_w`
  (`write_decay_streaming`, the compress inline duplicate, and `assemble_tiles`) accept direct
  pixel offsets — changed together (or behind one shared placement helper) so compress and
  append stay the single source of truth and remain byte-identical on identical inputs.

**Quality, reproducibility, UI**
- R11. Registration quality is captured (per-pair correlation; which tiles fell back to their
  initial grid position because they were disconnected or below the correlation threshold) and
  surfaced to the user; low-confidence registrations are **not silently accepted**. Quality is
  persisted as provenance.
- R12. Registration is reproducible: the stitching library is vendored into the repo with its
  version/identity recorded in provenance, and downstream operations reproduce placement by
  reusing persisted offsets. (Reproducibility-via-persisted-geometry is the invariant — there
  is no requirement that two independent imports be byte-stable through phase correlation.)
- R13. The Overlap %, Register checkbox, and Reference-channel selector appear on the
  **Import** and **Compress** surfaces only. The AddLayer-batch and TCSPC surfaces get a
  read-only "reuse persisted geometry" affordance (no registration controls), and the existing
  TCSPC user-edited re-seed suppression is preserved.
- R14. v1 requires **uniform tile size**; non-uniform / cropped-edge tiles are rejected with a
  clear error. Z-stacks register once on the chosen z-projection (e.g. MIP) of the reference
  channel and apply those offsets to all z-planes; time-lapse registers once and reuses the
  offsets for all timepoints (the existing decay time-lapse rejection is unaffected).

---

## Acceptance Examples

- AE1. **Covers R7, R5.** Given a 2×2 mosaic with overlap, when intensity and decay are both
  placed, then every pixel in an overlap region is sourced from the **same** tile index in the
  intensity composite and the decay composite (overlap-winner matches across layers).
- AE2. **Covers R5, R6, R9.** Given a dataset whose intensity was registered, when decay is
  appended, then decay tiles land at byte-identical pixel offsets to intensity, the decay
  canvas equals `native_shape`, and no phase correlation is run during the append.
- AE3. **Covers R1.** Given a 2×2 import with Register off (or overlap = 0), and given a 1×1
  import, when stitched, then the stored `/intensity` and `/decay` bytes are identical to the
  pre-feature golden output.
- AE4. **Covers R6.** Given a dataset flagged `registered` whose persisted offsets are missing,
  when a decay append is attempted, then it raises a clear error rather than placing tiles on a
  grid layout.

---

## Success Criteria

- A researcher can import an overlap-captured mosaic and get a seamless composite whose tiles
  are aligned by the overlap, with the per-cell phasor/lifetime correctly registered to the
  segmentation across seams.
- Decay appended later to that dataset is pixel-coherent with the already-stitched intensity,
  with no opportunity to misregister it (no decay-side registration path exists).
- Every existing 0%-overlap, single-tile, and single-timepoint workflow produces byte-identical
  output to before — verified by a golden regression test.
- A downstream agent/implementer can replay the registered geometry from the `.h5` + a
  serializable invocation alone (persisted offsets + provenance), with no hidden state.

---

## Scope Boundaries

- Sub-pixel registration — the library is integer-pixel by design; cross-layer consistency
  comes from reusing the same integer offsets, which is sufficient for v1.
- Any fusion other than overwrite (feather/linear-blending, average, max, sum) — explicitly out.
- Per-timepoint drift correction — register once and reuse across all timepoints.
- Ragged / non-uniform mosaics and cropped edge tiles — rejected, not handled.
- Out-of-core / chunked registration for mosaics that exceed RAM — accept a documented RAM
  ceiling; only the (already streamed) decay write is memory-safe.
- 3D `(z,y,x)` registration — register on a z-projection only.
- Auto-retry / threshold-relaxation heuristics on registration failure — surface and warn only.
- Registration controls on the TCSPC / AddLayer-batch surfaces — those reuse persisted geometry.
- Migrating the four duplicated stitch-control surfaces onto `StitchingFlimForm` — separate refactor.
- Backfilling registered geometry onto datasets imported before this feature — old datasets keep
  the grid path; no migration is attempted.

---

## Key Decisions

- Overlap tiles use phase-correlation **registration** (user decision): overlap is captured
  precisely to correct stage-positioning error, so the overlap should drive alignment, not just
  cropping.
- **Overwrite** fusion for both intensity and decay (user decision): keeps measurements honest
  (no synthesized pixels), keeps intensity and decay byte-coherent in overlap, preserves photon
  counting statistics, and needs no read-back on the streamed decay write.
- Registration is **compute-once-at-compress, persist, reuse-everywhere** (forced, not free):
  `native_shape` is locked at compress and decay is data-poor/noisy, so geometry must be solved
  once on intensity and reused; this also satisfies the I/O reproducibility principles (P5
  plans-as-data, P6 provenance-as-payload, P7 headless-first).
- The load-bearing invariant is **reproducibility-via-persisted-geometry**, not byte-stable
  phase correlation; the byte-identical guards that genuinely exist (0%-overlap, single-tile,
  single-timepoint, equal-code-path compress vs append) must not regress.
- **Vendor** the `grid_stitching` package (numpy-only, not on PyPI) and record its identity in
  provenance, rather than depending on an external/unpinned source.

---

## Dependencies / Assumptions

- The vendored `grid_stitching` core needs only numpy (scipy appears unused in the relevant
  modules); PerCell4 already has numpy/scipy/scikit-image/tifffile, so **no new dependency**.
- Persisted geometry (per-tile offset array + canvas shape) must round-trip through the store.
  *Assumption to verify:* whether `DatasetStore.create` serializes ndarray-valued metadata, or
  whether offsets must be written as a dedicated `/metadata` dataset through the write boundary.
- Tiles within a mosaic are uniform in size (the existing grid math already assumes this).
- For time-lapse mosaics, the stage does not move between frames (single geometry reused).
- The I/O principles in `docs/ideation/2026-04-29-io-principles-ideation.md` (P4 single write
  boundary, P5 plans-as-data, P6 provenance-as-payload, P7 headless-first) bind this feature;
  `assembler.py` is currently the *first* data-dependent stitch path in `domain/io`.

---

## Outstanding Questions

### Resolve Before Planning

- *(none — the product decisions are settled; the items below are implementation questions for planning.)*

### Deferred to Planning

- [Affects R4][Technical][Needs research] Does `DatasetStore.create` serialize an int array
  metadata value, or must persisted offsets be written as a dedicated `/metadata` dataset? Add a
  write→reopen→read-back round-trip test either way.
- [Affects R4, R9][Technical] Fix the coordinate convention: persist offsets pre- or
  post-`creation_bin`? Must match `native_shape` (post-bin) so the append guard stays valid.
- [Affects R2, R13][Technical] Reference-channel default — required selection vs default to
  first/brightest channel; identify by channel **name** (stable) not index.
- [Affects R12][Technical] Vendor the full `grid_stitching` package or a trimmed subset (drop
  the unused scipy declaration and the dead `_canvas_geometry` helper); where it lives
  (`domain/io/` sibling vs new module), and the compute-vs-place split per P5.
- [Affects R10][Technical] `add_decay_to_dataset` carries pre-existing audit drift (direct
  `h5py.File` writes vs P4; `source_dir.rglob('*.bin')` re-derivation vs P5). Decide whether to
  close these in the same change or track separately; a new `docs/solutions/` canonical-source
  entry + `canonical-sources-matrix.yaml` update is likely required (R13/R15/R16 of project
  conventions).
- [Affects R12][Needs research] Is phase-correlation peak selection (`argpartition`/`argsort`
  tie-handling, BLAS-dependent `lstsq`) stable enough that a recompute matches persisted offsets
  on another platform? If recompute-equality is ever needed, add a deterministic tie-break
  (prefer smaller \|shift\|). For v1, persist-and-reuse sidesteps this.

---

## Next Steps

-> `/ce-plan` for structured implementation planning.
