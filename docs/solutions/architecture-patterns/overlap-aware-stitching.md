---
title: "Overlap-aware registered stitching — register once at compress, persist offsets, reuse verbatim for every layer"
date: 2026-06-24
category: architecture-patterns
module: percell4.domain.io.assembler, percell4.adapters.importer, percell4.application.use_cases.add_decay_to_dataset, percell4.store
problem_type: architecture_pattern
component: tooling
canonical_source: src/percell4/domain/io/assembler.py
applies_to:
  - "src/percell4/domain/io/assembler.py"
  - "src/percell4/adapters/importer.py"
  - "src/percell4/application/use_cases/add_decay_to_dataset.py"
  - "src/percell4/store.py"
canonical_functions:
  - "src/percell4/domain/io/assembler.py::estimate_tile_offsets"
  - "src/percell4/domain/io/assembler.py::canvas_from_offsets"
  - "src/percell4/domain/io/assembler.py::assemble_tiles_with_offsets"
  - "src/percell4/adapters/importer.py::import_dataset"
  - "src/percell4/adapters/importer.py::write_decay_streaming"
  - "src/percell4/store.py::DatasetStore.write_stitch_geometry"
  - "src/percell4/store.py::DatasetStore.read_stitch_geometry"
  - "src/percell4/application/use_cases/add_decay_to_dataset.py::add_decay_to_dataset"
status: canonical_clean
tags:
  - stitching
  - phase-correlation
  - registration
  - mosaic
  - overlap
  - flim
  - decay
  - phasor
  - cross-layer-alignment
  - native-shape
  - provenance
  - canonical-source
related_components: [io, flim, hdf5]
---

# Overlap-aware registered stitching

The first **data-dependent** stitch path in `domain/io`. Tiles captured with X%
overlap are aligned by phase-correlation registration; 0%-overlap and single-tile
inputs keep the byte-identical edge-to-edge grid path. The whole design exists to
neutralize one failure mode: a data-dependent canvas that drifts between
`/intensity` and `/decay` (the prior critical, scientifically-wrong FLIM bug —
see `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`).

## The architecture: register once, persist, reuse verbatim

```
COMPRESS / IMPORT (register-once)         gate: register ∧ overlap>0 ∧ grid>1×1
  reference channel PER-TILE arrays  (post-creation_bin, post-z-projection)
        │  estimate_tile_offsets()        → final rounded non-negative int (y0,x0)
        ▼                                    + success gate (degenerate → RegistrationError)
   offsets (N,2) int32 ≥0  ── canvas_from_offsets() ──▶ canvas (H,W)  [the ONLY canvas computation]
        ├─▶ every intensity channel:  assemble_tiles_with_offsets(tiles, offsets, canvas, asc-index)
        ├─▶ decay-at-import:          write_decay_streaming(..., pixel_offsets=offsets)  (streamed, overwrite)
        │   native_shape := canvas (post-rotate);  assert == assembled /intensity.shape[-2:]
        └─▶ COMMIT (flag written LAST):
                store.write_stitch_geometry(offsets, StitchProvenanceRecord, …)
                  → dataset  stitch/tile_offsets   → /provenance/stitch   → /metadata stitch_registered=True

DECAY-ONLY APPEND (consume)               read geometry FRESH via read_stitch_geometry()
   ├─ registered & offsets present → place decay at the SAME offsets (write_decay_streaming, +invalidate /phasor)
   │       expected canvas = canvas_from_offsets() ; assert == native_shape (after rotate_k) else LayerSizeMismatchError
   ├─ registered flag set but offsets absent → raise (no silent grid fallback)
   └─ not registered → existing grid path, unchanged (back-compat)
```

Geometry is computed **exactly once**, at compress, on one user-selected reference
intensity channel (`estimate_tile_offsets`). The resulting integer per-tile pixel
offsets are persisted and **reused verbatim** for every other channel, every
timepoint, and the decay stream — including a later decay-only append. Decay is
never registered independently.

## Load-bearing rules

1. **`canvas_from_offsets` is the single canvas source.** `max(y0+th)` by
   `max(x0+tw)` over the final non-negative integer offsets, computed in
   `assemble_tiles_with_offsets`, in `write_decay_streaming`'s offset branch, and
   adopted as `native_shape`. Recomputing the canvas anywhere else is forbidden —
   that is the divergence vector. Offsets always have per-axis min 0 (asserted on
   write *and* read in the store), so the derivation is unconditionally
   `max(offset + extent)`. Use `math.prod` for any element/byte sizing (registered
   mosaics get large; `np.prod` wraps int32 on Windows LLP64).

2. **`canvas_from_offsets(offsets, tile_shape)` consumes a single solve.** All
   channels reuse one shared `(N,2)` offset array per dataset — not per-channel
   solves. This is what guarantees intensity and decay land on the same canvas.

3. **Offsets are a dataset; scalars + provenance are metadata/provenance.** The
   `(N,2) int32` offset array goes through `store.write_array("stitch/tile_offsets")`
   (pass-through view-bin, lossless — `/metadata` attrs are 64 KB-capped and not
   array-typed on read-back). A `StitchProvenanceRecord` (per-pair correlations,
   disconnected tiles, `coverage_fraction`, engine `regression_threshold`/`n_peaks`)
   is written to `/provenance/stitch` — registered geometry is a provenance payload
   (P6). Scalars `stitch_reference_channel`, `stitch_overlap`, and the commit marker
   `stitch_registered` are `/metadata` attrs. No new `h5py.File('a')` in
   domain/application — everything routes through `DatasetStore` (P4).

4. **Overwrite priority is pinned: ascending tile index, disconnected demoted.**
   `assemble_tiles_with_offsets` (intensity) and `write_decay_streaming`'s offset
   branch (decay) both iterate the same order — disconnected tiles first (lowest
   priority), then ascending index so the highest registered index wins each
   overlap pixel. Identical ordering across layers is what makes intensity and decay
   resolve every overlap pixel to the **same** tile. The winner choice is a
   *consistency* decision (assumes near-identical overlap content), not a quality
   one; a mis-placed disconnected tile can never overwrite a correctly-registered
   neighbour.

5. **`native_shape` stays the authoritative lock.** The registered canvas
   (post-`rotate_k`) *becomes* `native_shape` at compress; `import_dataset` asserts
   `canvas_from_offsets == assembled /intensity.shape[-2:]` (after the same
   `rotate_k` transpose the append guard uses) before committing. The decay-only
   append derives its expected canvas from the persisted offsets via
   `canvas_from_offsets` and reuses the existing `LayerSizeMismatchError` — no
   parallel guard, no silent resize. Offsets are persisted in **post-`creation_bin`**
   units so `bbox(offsets) == native_shape` (pre-bin offsets would trip
   `MetadataConsistencyError`).

6. **`stitch_registered=True` is the commit point.** It is written **strictly
   last**, gated on the offsets dataset, `/provenance/stitch`, and the assembled
   intensity canvas all being durably present. A crash before the flag leaves an
   un-registered (recoverable) file — "flag absent but offsets present (or vice
   versa)" reads back as `registered=False` (not committed → safe to re-import),
   never a registered-but-offsets-absent brick.

7. **The byte-identical gate.** The registered path is entered ONLY when
   `register ∧ overlap>0 ∧ grid_rows·grid_cols>1`, validated at the importer gate
   (kept out of `TileConfig`, which stays a dumb carrier). Every other input —
   0%-overlap, single-tile, `register=False` — runs the existing `assemble_tiles` /
   grid `write_decay_streaming` path with byte-identical output. The engine is not
   even imported when the gate is closed. `register` without `overlap>0` or a
   `reference_channel` raises at the gate.

## Cross-layer alignment invariant (the regression to never break)

The decay-only append reuses the persisted offsets, so the SAME overlapping source
produces a **byte-identical** `/decay` whether the decay is written at compress
time or appended afterwards. Because every FLIM consumer derives intensity from
`decay.sum(axis=-1)` (never from a sibling `/intensity` stack), byte-identical
decay ⇒ identical per-pixel phasor `(g, s)` by construction. The guard test is
`tests/test_io/test_stitch_alignment_invariant.py::test_compress_vs_append_decay_byte_identical_and_phasor_matches`
— it runs both flows in-process, asserts `np.array_equal` on `/decay`, asserts an
overlap pixel equals exactly one source tile's photons in both, and asserts the
derived phasor (and the size=3-median "Filtered" view) match.

Coverage gaps are guarded: a non-rectangular registered canvas can leave uncovered
pixels (HDF5 fill 0.0). The decay there is all-zero and the phasor `intensity<=0`
guard yields `g=s=0` — no fill leaking into a measured cell. `coverage_fraction`
is recorded in `/provenance/stitch` and a warning is surfaced when coverage < 1.0.

## Reuse rule

> Any code that needs the registered mosaic geometry MUST read it via
> `DatasetStore.read_stitch_geometry()` (fresh on each call — never a
> `handle.metadata` snapshot) and place tiles at the persisted offsets using
> `canvas_from_offsets` + the ascending-index overwrite priority. Never re-run
> `estimate_tile_offsets` on a consume path; never compute the canvas anywhere but
> `canvas_from_offsets`; never stitch decay in memory (feed `pixel_offsets` into
> `write_decay_streaming`).

## Related

- `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md` —
  the critical bug this path neutralizes; consumers derive intensity from `/decay`.
- `docs/solutions/architecture-patterns/decay-write-path.md` — all `/decay` writes
  route through `write_decay_streaming`; the offset path feeds it, not a new writer.
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  — read geometry fresh; validate in-process, not via subprocess.
- `docs/solutions/logic-errors/numpy-prod-int32-overflow-windows-2026-06-07.md` —
  `math.prod` for canvas element/byte counts.
- `docs/audits/canonical-sources-matrix.yaml` (slug `overlap-aware-stitching`),
  `docs/audits/io-principles-matrix.yaml` (P4/P6 cells).
