---
title: "Single-source-of-truth for /decay/<channel> writes — write_decay_streaming + append_decay_layers"
date: 2026-04-30
category: architecture-patterns
module: percell4.adapters.importer, percell4.store, percell4.application.use_cases.add_decay_to_dataset
problem_type: architecture_pattern
component: tooling
canonical_source: src/percell4/adapters/importer.py
applies_to:
  - "src/percell4/adapters/importer.py"
  - "src/percell4/application/use_cases/add_decay_to_dataset.py"
  - "src/percell4/store.py"
duplicates_at:
  - {path: "src/percell4/adapters/importer.py", note: "lines 462-504 contain dead-code legacy duplicate of write_decay_streaming inside the for-loop after Thread 1 refactor — already-shipped streaming path is at 594-665"}
status: canonical_clean
tags:
  - hdf5
  - tcspc
  - decay
  - streaming
  - canonical-source
  - thread-1
related_components: [io, flim]
---

# Decay-write path canonical source

> **Status: canonical_clean.** Thread 1 (`97ae037`) consolidated decay writes onto a single helper. Legacy duplicate inside `importer.py` lines 462-504 is dead code (preceded by `continue` at 458) — flagged for cleanup.

## Canonical implementations

`.bin` decay writes (streaming, tile-by-tile):
- **`adapters/importer.py:write_decay_streaming(h5_path, channel_name, tile_bins, bin_dims, ...)`** — single source of truth for `.bin → /decay/<ch>` streaming writes. Used by both the initial-import path (`importer.import_dataset` line 445) and the append path (`add_decay_to_dataset.add_decay_to_dataset` line 224). Invalidates stale `/phasor/<ch>` in the same write (lines 634-636).

In-memory decay arrays:
- **`store.DatasetStore.write_array(decay_path, ..., is_decay=True)`** — uses LZF compression and `(64, 64, T)` chunking (`store._choose_chunks`).

Append-only API (raises `LayerAlreadyExists` on conflict):
- **`store.DatasetStore.append_decay_layers(layers, provenance, cross_format_rule, force=False)`** — bulk append with per-channel `ProvenanceRecord`, persisted `cross_format_rule` in `/metadata`, conflict detection, per-channel flush+fsync.

## Thread 1 alignment commits

- `e25831a`, `37dd603`, `392f66d`, `f31a970`, `c25950e` — five commits aligning add-layer's decay-write path with compress's existing path. The canonical implementation existed before Thread 1; reuse failed at the design step. Audit lens introduced in this initiative is the structural fix.

## Drift / dead code

- `adapters/importer.py:462-504` — second `if isinstance(decay_info, dict) and decay_info.get("_streaming"):` block immediately following `continue` at line 458. This is unreachable. The block contains an inline duplicate of `write_decay_streaming`'s body. Remove in a future cleanup.
- `add_decay_to_dataset.py:_read_and_stitch_decay` (lines 407-471) — defines a separate stitching helper that is NEVER called (Phase 1 in `add_decay_to_dataset` uses `write_decay_streaming` directly at line 224). Dead code.

## Reuse rule

> Any new code that needs to write a TCSPC decay layer to HDF5 MUST go through one of:
> - `write_decay_streaming(...)` for `.bin`-tile streams,
> - `DatasetStore.write_array(..., is_decay=True)` for in-memory arrays,
> - `DatasetStore.append_decay_layers(...)` for the bulk-append API with conflict detection.
>
> Direct `h5py.File(...).create_dataset("decay/...", ...)` outside these primitives is a drift violation.

## Related

- Five-vector staleness compound (`docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`) Vector 5: derived `/phasor/<ch>` invalidation is bound to the decay-write boundary by `write_decay_streaming` (lines 634-636).
- `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md` — sibling rule about per-pixel alignment between `/intensity[ch_idx]` and `/decay/<ch>`.
