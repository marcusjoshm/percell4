---
date: 2026-04-29
topic: io-principles
focus: high-level rules governing all downstream I/O development — flexibility on formats, naming, payload types, timing, symmetry, batch↔incremental consistency
mode: repo-grounded
---

# Ideation: PerCell4 I/O Principles

## Grounding Context

### Codebase context (today)
PerCell4 is a single-cell microscopy analysis Qt+napari app on Python 3.12. I/O is HDF5-store-centric (one `.h5` per experiment) with payload types: intensity channels, segmentation labels, particle masks, phasor maps, TCSPC decay stacks, per-cell measurements, metadata.

The architecture is hexagonal: `domain/io/` (pure logic) + `adapters/importer.py` + `application/use_cases/` + `gui/*Dialog.py`. There are three import entry points (`ImportDialog` for single dataset, `CompressDialog` for batch discovery → multi-dataset write, `AddLayerDialog` for append-single-layer-to-existing) and one export entry point (`ExportImagesDialog`, TIFF only) — an asymmetry already noted as pain. Token-based filename parsing (`_ch##`, `_t##`, `_z##`, `_s##`) drives discovery via `FileScanner` → `discover_by_subdirectory` → `assembler.py`. Atomic writes via tmp + `os.replace`; per-operation HDF5 open/close.

Active churn in the working tree (uncommitted modifications to `importer.py`, `discovery.py`, `models.py`, `scanner.py`, `compress_dialog.py`, `store.py`) confirms the I/O seam is hot.

### Past learnings (load-bearing)
- "Discovery scopes, processing consumes" rule has been violated **twice** (compress, add-layer): batch path re-derived files from `source_dir` instead of consuming `DatasetSpec.files`. Documented in `docs/solutions/logic-errors/batch-compress-development-lessons.md` and `add-layer-flat-discovery-duplicate-import.md`.
- `_write_layer(name, layer_type, array)` is the centralized write boundary that owns dtype + HDF5 group placement (Channel→`/intensity`, Segmentation→`/labels/<name>` int32, Mask→`/masks/<name>` uint8). No mirror on the export side yet.
- One payload type per HDF5 group; new types get new groups. Same `name` shared across `/labels/` and `/masks/` caused mask-misclassified-as-segmentation bug (`napari-mask-layer-misclassified-as-segmentation.md`).
- Atomic-write contract: tmp + `os.replace`; no platform branching.
- Streaming writes for large payloads (decay stacks ≈5 GB) — first-class, not optimization.
- Token-based discovery derives names by *stripping* known patterns; index normalization (1-based file → 0-based array) at I/O boundary.
- Provenance invariant: each payload type has ONE canonical home. Per-dataset `.h5` for image/label/mask; run-folder Parquet for cross-dataset measurements.

### External prior art
GDAL driver capability matrix and Identify-then-Open. ffmpeg's structural demuxer/muxer asymmetry. napari npe2 reader/writer manifest. Pandas PDEP-9 prefix convention. OME-NGFF / Zarr hierarchical groups. BioIO entrypoint registry. Apache Arrow per-fragment schema reconciliation. MIME multipart "dumb envelope, typed parts." Postel's law tension resolved by layered split (import absorbs chaos, store stays strict).

## Ranked Ideas

### 1. Capability Matrix Over Folklore
**Description:** The HDF5 store is a dumb envelope; payloads are typed parts. Every importer / exporter / layer-writer declares — in one machine-readable registry — which payload types it handles, which dtypes, whether it streams, whether it round-trips losslessly. Adding a new format = one row; adding a new payload type = one column. UI dialogs query the matrix instead of grepping code.
**Warrant:** `external:` GDAL driver capability matrix; napari npe2 manifest; pandas PDEP-9 prefix convention `reader_<format>` / `writer_<format>` rejects overloaded `engine=` dispatch. `direct:` payload types are implicit today (channel/labels/masks/phasor/decay/measurements scattered across `compress_dialog`, `add_layer_dialog`, `_write_layer`, assemblers).
**Rationale:** Compounds across formats × payload types × directions. The round-trip principle (#2) is enforceable only against this artifact. Without it, "what can we export?" is folklore.
**Downsides:** Can become a god object. Out-of-tree format plugins need an entrypoint mechanism. New contributors must remember to update it.
**Confidence:** 95%
**Complexity:** Medium
**Status:** Unexplored

### 2. Round-Trip Is Contractual
**Description:** For each payload type the importer accepts, an exporter MUST exist that produces a result the importer can re-ingest into an equivalent store. New payload types ship both directions in the same PR. The capability matrix declares supported round-trips; CI runs `import → store → export → re-import → diff` smoke tests parameterized over the matrix.
**Warrant:** `direct:` codebase asymmetry — 3 import paths, 1 export path; phasor and decay payloads cannot leave the store. `external:` GDAL declares read/write/create/create-copy independently per driver; ffmpeg muxer/demuxer matrix; napari npe2 makes reader/writer asymmetry explicit and queryable.
**Rationale:** Asymmetric I/O traps user data. The hexagonal architecture invites symmetry; the codebase doesn't deliver it. Making round-trip contractual forces honesty in the matrix and prevents data jail.
**Downsides:** Some payloads (computed maps with complex provenance) are genuinely hard to round-trip losslessly. Mitigation: declared "lossy" round-trip is allowed but must be documented in the matrix.
**Confidence:** 90%
**Complexity:** Medium
**Status:** Unexplored

### 3. Identify Before Open; Metadata Before Filename
**Description:** Every reader implements a cheap `identify(path)` that returns confidence + payload type, probing embedded metadata first (OME-XML, SDT headers, ImageJ tags), then sidecars, then directory structure, with filename tokens as last-resort fallback. `open()` is a separate commitment that may only run after identify succeeded. Batch operations identify ALL inputs before any open commits.
**Warrant:** `external:` GDAL Identify-then-Open; ffmpeg `av_probe_input_format`; libmagic; BioIO/AICSImageIO entrypoint registry. `direct:` token-based discovery already strips patterns; the existing scanner pipeline is already two-phase-shaped. `reasoned:` filenames are fragile — get renamed by Windows, mangled by Dropbox, copy-pasted across experiments — embedded metadata is harder to corrupt.
**Rationale:** Operationalizes the "naming-convention flexibility" goal at the right layer — naming is one signal among several, not the source of truth. Also kills the "fails mid-batch at 3am" failure mode by front-loading metadata cost.
**Downsides:** Identify must stay cheap (header reads, not full parses). Metadata-vs-filename conflicts need a documented precedence. Some vendors embed metadata inconsistently.
**Confidence:** 90%
**Complexity:** Medium-High
**Status:** Unexplored

### 4. Two-Layer Postel + Single Write Boundary
**Description:** The importer is liberal — tolerates token variants, mixed delimiters, off-by-one indices, missing channels, vendor quirks. The HDF5 store is strict — canonical group placement, fixed dtypes per payload type, normalized 0-based indexing, one payload type per group, typed metadata key (`PERCELL_TYPE_KEY`). All canonicalization happens at the I/O boundary; no normalization code lives downstream. A single function (`_write_layer(name, layer_type, array, provenance)`) is the only door into the store; mirror it with a single `_read_layer` on the way out. Direct `h5py.File(...).create_dataset` outside this dispatch is a CI lint failure.
**Warrant:** `external:` Postel's law (be liberal in what you accept, strict in what you produce); the IETF "Postel was wrong" tension is resolved by the *layered* split — chaos absorbed at boundary, never leaks inward. `direct:` `_write_layer` is already the centralized write boundary; "one payload type per HDF5 group" is already an invariant; index normalization at boundary is a stated rule. The mask-vs-segmentation bug was a violation.
**Rationale:** Names where "messy" stops. Every downstream consumer (viewer, phasor, measurements) currently has to be defensive about shape/dtype — if canonicalization is enforced at the boundary, downstream code shrinks. Lint-able and provable.
**Downsides:** Single boundary is a chokepoint that must stay performant under streaming / multi-GB writes. Liberal accepting risks codifying bugs as de-facto standard if not paired with strict storage.
**Confidence:** 95%
**Complexity:** Low (the boundary already exists; codifying + linting is cheap)
**Status:** Unexplored

### 5. One Pipeline: Plans In, Execution Consumes, Batch ≡ Incremental
**Description:** Discovery is side-effect-free and returns immutable plan objects (what would be done, where it would land, what conflicts exist). Execution is a separate function that consumes plans. There is one ingestion path: `execute(plan)` where `len(plan.items) == 1` is incremental and `> 1` is batch. Import, compress, and add-layer collapse into thin entry UIs over one model. Discovery functions never open HDF5, mutate state, or show dialogs. Plans never re-derive files from `source_dir`.
**Warrant:** `direct:` past learning — "discovery scopes, processing consumes" rule has been violated TWICE (compress, add-layer); two solution docs document the recurrence. `reasoned:` plans-as-data is the same pattern that makes Terraform plan, SQL EXPLAIN, and dry-run flags valuable — preview, validate, log, retry, test all become free.
**Rationale:** Operationalizes the user's "batch and incremental must be consistent" goal as a code-level invariant. Every new dialog gets preview/dry-run/conflict-detection for free. Tests assert against plan objects (deterministic) instead of filesystem state. Prevents the recurring bug class as a structural matter, not a code-review checklist.
**Downsides:** Re-architecting the existing 3 dialogs is non-trivial (active churn in working tree confirms). Some pre-existing optimizations may not survive the refactor. Plans must be serializable for replay/debug.
**Confidence:** 90%
**Complexity:** High
**Status:** Unexplored

### 6. Provenance Is a Payload
**Description:** Every artifact written to the store carries structured provenance — source path(s), importer name+version, parameters, timestamp, content hash — through the same `_write_layer` boundary as the data itself. There is no sidecar log file. Provenance lives in the .h5 in a `/provenance/<group>/<name>` mirror or as group attrs. Reading the store always returns enough to retrace the bytes back to their origin.
**Warrant:** `direct:` past learning — "each payload has ONE canonical home"; provenance is the missing half. `external:` OME-NGFF `omero` block; BIDS sidecar metadata; Apache Arrow per-fragment schema reconciliation; GxP / 21 CFR Part 11 audit-trail requirements. `reasoned:` separating provenance from data invariably leads to drift; co-location is the only design that survives reorganization.
**Rationale:** Single-cell tracking across timepoints/conditions is the project's core value proposition. Provenance is the substrate that makes "the same cell" claim verifiable. Without it, debugging "why does this dataset look weird" is archaeology. Compounds with reproducibility — every figure can be answered for.
**Downsides:** Adds write-time overhead (small). Schema for provenance must be versioned. Will balloon if not pruned (e.g., parameter dicts).
**Confidence:** 90%
**Complexity:** Medium
**Status:** Unexplored

### 7. Headless API Is Primary; Dialogs Are Sugar
**Description:** Every I/O action exposed in the GUI MUST be reachable via a serializable invocation object (YAML / TOML / Python config). Dialogs are generators and editors of those invocations — they MUST NOT contain logic the headless API cannot reproduce. CLI, batch scripts, regression tests, and reproducibility reports are all renderers of the same invocation. Corollary: when a dialog action runs, the resulting invocation can be printed/saved.
**Warrant:** `direct:` project goals state CLI is "future" — codifying this principle now prevents the GUI/CLI fork. `external:` Snakemake, Nextflow, every modern bioinformatics pipeline tool. CLI-first with GUI-on-top is the durable architecture; GUI-first becomes a maintenance trap. `reasoned:` reproducibility in microscopy is a publication requirement — a serializable invocation IS the reproducibility artifact.
**Rationale:** Compounds across testability (no Qt in the inner loop), reproducibility (invocations are records), batch automation (CLI is free), and multi-user collaboration (recipes are shareable). Defers no work — paid up front, dividends every PR.
**Downsides:** Some dialog UX patterns (live previews, partial states) don't map cleanly to a single invocation object. Need a "draft invocation" type for dialog-in-progress state. Initial setup effort to retrofit existing dialogs.
**Confidence:** 85%
**Complexity:** Medium-High
**Status:** Unexplored

## Cross-cutting

- **Surface triad (1+2+4)** — capability matrix declares the surface; round-trip enforces symmetry; single write boundary owns canonicalization. Adopting all three is more than the sum: the matrix becomes the test oracle for round-trip; the boundary becomes the lint target the matrix asserts against.
- **Reproducibility stack (5+6+7)** — plans-as-data + provenance-as-payload + headless-first invocations together let any analysis replay from a config + .h5 alone.

## Rejection Summary

| # | Idea | Reason rejected |
|---|------|-----------------|
| I | Streaming Is Default for Bulk Payloads | Tactical, not principle-level — better expressed as a per-payload-type capability declared in matrix (#1). |
| J | Index Normalization at the Boundary | Subsumed by Two-Layer Postel (#4). |
| K | Layer Type Inferred, Never Asked | Conflicts with explicit user override; better as brainstorm. |
| L (partial) | Filename Is a Hint, Not a Contract | Folded into #3. |
| M | Schema Evolution Is Read-Side, Not Migration | Premature — re-evaluate after first external user. |
| N | Journaled Multi-Step Operations | Heavy machinery; partly addressed by #5 + #6. |
| P | Store Is Truth; GUI Is View | Out of I/O scope — closer to state-management. |
| Q (partial) | HDF5 Is the IR / Dumb Envelope, Typed Parts | Folded into #1 and #4. |
| R | No "Import" Verb — Only Register | Too radical for a foundational rule; brainstorm seed. |
| S | Re-Import Only What Is Stale (DAG) | Feature-level — follows from #5 once shipped. |
| T | Worker → GUI Via Qt Signals Only | Coding convention; belongs in `gui/CLAUDE.md`. |
| U | Imports Are Total; Every Input Gets a Status | Strong; cut for capacity. Achievable as consequence of #1 + #5. |
| V | Storage Granularity Is a Declared Choice | Too vague — doesn't tell developers what to DO. |
| W | Project Index Is Authoritative, Stores Cacheable | Premature — needed when multi-user / federated scenarios appear. |
| X | No User-Facing Performance Tuning Knobs | Strong; cut for capacity. Honorable mention for slot 8. |
| Y | Reference vs. Consolidate Modes | Feature, not principle. |
| Z | Content-Addressable Payloads | Architectural pivot; deserves its own deep brainstorm. |
