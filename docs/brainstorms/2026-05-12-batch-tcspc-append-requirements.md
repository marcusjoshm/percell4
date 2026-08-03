---
title: Batch TCSPC (.bin) append to existing datasets
status: open
created: 2026-05-12
type: feature-requirements
related:
  - docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md
  - docs/plans/2026-04-29-feat-tcspc-append-and-cross-format-token-matching-plan.md
---

# Batch TCSPC (.bin) append to existing datasets

## Problem

Adding FLIM `.bin` files to an existing PerCell4 dataset today goes through `AddLayerDialog` → `add_decay_to_dataset` one dataset at a time. A typical FLIM experiment produces 3–12 dishes per session, each exported as its own subfolder of `.bin` files. Walking through the single-dataset dialog 12 times — picking each `.h5`, picking the matching folder, re-entering the same tile/orientation settings, and pasting per-dataset calibration values — is slow, repetitive, and error-prone (especially the per-channel `(phase, modulation)` calibration values, which are easy to transpose).

Within a session the structure is uniform: same channel layout, same tile grid and orientation across every dish. The only thing that genuinely varies dish-to-dish is calibration. So the batch feature should hold everything uniform constant once and vary calibration via a single per-batch CSV.

## User outcome

The user opens a new **Batch TCSPC Append** dialog from the launcher. They:

1. Pick the existing `.h5` datasets to append to (multi-select).
2. Point at one parent root folder; the dialog lists the immediate `.bin` group subfolders inside it (one per dish).
3. Manually pair each selected dataset with one group folder. A name-similarity default fills in the obvious matches; the user adjusts any wrong ones.
4. Upload a calibration CSV in long format: one row per `(dataset, channel)` with `frequency_mhz`, `phase`, `modulation`.
5. Enter the tile/orientation settings *once* for the whole batch (grid rows × cols, scan order, rotation, flip, token regex, conflict policy).
6. Click **Validate** to see a pre-flight report: every dataset, its paired folder, its channel calibrations, and any conflicts.
7. Click **Run** and watch a per-dataset progress list. At the end, see a summary: which datasets landed cleanly, which had errors, which had conflicts that were skipped or overwritten.

After the run, each touched `.h5` has new `/decay/<ch>` layers, calibration written to `/metadata`, and is ready for the existing phasor compute flow with no further input.

## Requirements

### R1. Dataset selection: explicit multi-select

The dialog shows a table of available `.h5` datasets and lets the user check exactly which ones to include in this batch.

- Default source: the currently-loaded PerCell4 project (`ProjectIndex` / `project.csv`). Every `.h5` in the project appears as one row.
- Fallback when no project is loaded: an **Add datasets…** button opens a file picker that accepts multiple `.h5` paths. Selected paths join the table.
- Each row shows: dataset filename, channel names (from `metadata.channel_names`), and whether any `/decay/<ch>` already exists.
- Datasets that already have decay for *every* channel are visually flagged (e.g., greyed and pre-unchecked) but the user can still tick them to overwrite.

No batch operation runs unless at least one dataset is checked.

### R2. Group discovery: parent root → immediate subfolders

The dialog has a **Source root** picker. When set, the dialog lists the immediate subfolders inside that root as candidate **groups**. Each group:

- Has a name equal to the subfolder name.
- Contains some set of `.bin` files, discovered recursively (`rglob("*.bin")`). Recursive discovery handles both flat layouts (the common LAS X export, e.g. `Dish 1 - WT 60min/{name}_s{N}_ch{N}.bin`) and any nested per-tile layouts a future export format might produce — no separate "flatten" toggle is needed.
- Reports a tile/channel count (e.g., "16 tiles × 3 channels = 48 .bin files") in the discovery list.

The pairing UI never lists individual `.bin` files; groups are the only unit the user touches.

### R3. Manual dataset ↔ group pairing with auto-fill default

The dialog shows a two-column pairing grid:

```
Dataset                              Group (.bin folder)
─────────────────────────────────────────────────────────────
Dish 1 - WT 60min As + Noco.h5    →  [Dish 1 - WT 60min As + Noco ▼]
Dish 2 - TAOK2 KO 60min As + Noco  →  [Dish 2 - TAOK2 KO 60min As + Noco ▼]
Dish 3 - RTN4 KO 60min As + Noco   →  [— select —                       ▼]
```

- Each dataset row carries a dropdown of available groups (plus a "— select —" sentinel and a "— skip —" entry).
- An **Auto-pair** button fills dropdowns by name-similarity (case-insensitive substring / fuzzy ratio); ties stay unset.
- The user can override any pair.
- A group may only be assigned to one dataset; assigning a group to dataset B silently clears it from dataset A (and pre-flight surfaces the now-empty row).
- Pre-flight rejects the batch if any selected dataset still has "— select —".

Pairing is the only manual mapping step. Tiles inside a group are not individually surfaced — `add_decay_to_dataset` already orders them by token.

### R4. Calibration CSV in long format

A **Calibration CSV** button loads a CSV with this exact schema:

```
dataset,channel,frequency_mhz,phase,modulation
Dish 1 - WT 60min As + Noco,ch1,80.0,0.12,0.98
Dish 1 - WT 60min As + Noco,ch2,80.0,0.10,0.99
Dish 1 - WT 60min As + Noco,ch3,80.0,0.11,0.97
Dish 2 - TAOK2 KO 60min As + Noco,ch1,80.0,0.13,0.97
…
```

- Required columns: `dataset`, `channel`, `frequency_mhz`, `phase`, `modulation`. Extra columns are ignored.
- `dataset` matches by the `.h5` filename stem (case-sensitive, the same string the dialog shows in the pairing grid).
- `channel` matches a value in the target `.h5`'s `metadata.channel_names` (case-sensitive).
- Numeric columns parse as `float`; non-numeric rows fail validation with the row number and column called out.
- The CSV must cover every `(selected_dataset, channel)` pair that is staged for append. Missing rows fail pre-flight with an explicit list.
- Extra rows (datasets in the CSV that aren't selected for this run) are silently allowed — useful when one master CSV covers multiple batches.
- `frequency_mhz` is allowed to vary across datasets (different microscope sessions). It is required to be consistent across all channels of the same dataset; mismatches fail pre-flight.

After successful CSV parsing, the pairing grid grows a small calibration column showing `freq=80.0, ch1=(0.12, 0.98), …` per row so the user can sanity-check before running.

### R5. One global tile / orientation configuration per batch

A collapsible **Stitching & orientation** section exposes one set of controls for the whole batch (no per-dataset variants):

- Grid rows × cols (and `grid_type` / `order`) — the same `TileConfig` the existing single-dataset flow uses.
- Rotation `k` (0/90/180/270 CCW) and flip axis (none/H/V) — passed straight through to `add_decay_to_dataset`.
- Token regex / `TokenConfig` — defaults match the LAS X export naming pattern (`_s{tile}_ch{channel}`) used by the existing flow; advanced users can edit.
- `FlimConfig` raw-array settings (`bin_x`, `bin_y`, `bin_t`, `dtype`, `dim_order`, `header_bytes`) — same defaults as today, in an "Advanced" twisty.

This section is filled out *once*. Per-batch uniformity is a hard assumption of the feature; users with heterogeneous tile geometries are expected to run separate batches.

### R6. Conflict policy: skip existing or overwrite all

A single radio control governs what happens when a target dataset already has `/decay/<ch>` for a channel that this batch would write:

- **Skip existing layers (default)** — `force=False`. Per-channel `LayerAlreadyExists` situations report into the summary as "skipped (already present)"; other channels in the same dataset still write.
- **Overwrite all** — `force=True`. Matches the existing single-dataset Replace toggle.

There is no per-dataset or per-channel granularity in the UI; the radio applies uniformly across the batch. Anyone needing fine-grained control should use the single-dataset dialog.

### R7. Pre-flight validation before Run

A **Validate** button runs a non-destructive check before any I/O. It reports, in one panel:

- **Pairing** — every selected dataset has a group; every group is unique; warns on un-paired groups (informational only — extra groups are allowed).
- **Channels** — every paired group's `.bin` files map cleanly to the dataset's `channel_names` via the configured `cross_format_rule` (this is a dry-run of `match_bin_to_intensity`; surface `unmatched` and `ambiguous` lists per dataset).
- **CSV coverage** — every `(selected_dataset, channel)` has a row.
- **Frequency consistency** — `frequency_mhz` is constant within each dataset across channels.
- **Pre-existing decay** — count of `/decay/<ch>` collisions per dataset under the chosen conflict policy.
- **Disk space** — rough estimate of bytes per dataset (tiles × bin file size × ~1.2 overhead for float32 conversion) and total. Warn if total exceeds free space on the destination volume.

**Run** is disabled until Validate has succeeded at least once with the current settings; any edit (pairing change, new CSV, toggle change) re-disables Run until Validate is re-run.

### R8. Per-dataset progress + summary report

When the user clicks **Run**, the dialog switches to a progress view. A scrollable list shows one row per dataset:

```
Dish 1 - WT 60min As + Noco  ⏳ Stitching ch2 (12/16 tiles)
Dish 2 - TAOK2 KO 60min As + Noco  ✓ Done (3 layers, 0 skipped)
Dish 3 - RTN4 KO 60min As + Noco  ⚠ Errors (see report)
```

- Each dataset's append runs in a worker thread; the UI stays responsive.
- Datasets run sequentially (not in parallel) — keeps the disk happy and matches the existing single-dataset flow's I/O profile.
- The user can **Cancel** mid-batch: the in-flight dataset completes (no torn writes), pending datasets are skipped, and the summary marks them as "cancelled".

At the end, a final report panel shows:

- Per dataset: written channels, skipped channels (with reason), errors.
- A **Copy report to clipboard** button that emits a plain-text version.
- A **Close** button returns to the launcher. There is no "save report to disk" — the clipboard copy is enough; per-dataset provenance is already recorded by the underlying use case.

### R9. Calibration write happens per dataset, alongside the append

For each dataset the runner does, in order:

1. Pre-write the calibration to `/metadata`: `flim_frequency_mhz` (scalar) and a per-channel `channel_calibrations` tuple aligned to `channel_names`. This uses `DatasetStore.set_metadata({...})`. Calibration is written *before* the append so a later mid-run failure still leaves the dataset with a consistent calibration record.
2. Run `add_decay_to_dataset(...)` with the per-batch tile/orientation settings and `force` flag.
3. Surface the returned `AppendReport` into the progress row.

Computing phasors is **not** part of this batch flow — the existing `compute_phasor` flow reads calibration from `/metadata` whenever it next runs. Users who want phasors right away can run the existing batch phasor flow on the same set of datasets afterward.

### R10. Error policy: continue past per-dataset failures

A per-dataset failure (missing files, mismatched dimensions, write error) records the error into the summary and proceeds to the next dataset. The whole batch only aborts if:

- The user clicks **Cancel**, or
- Pre-flight failed (Run is disabled in that case anyway, so this is a no-op in practice).

`add_decay_to_dataset` already returns errors via `AppendReport.errors` rather than raising; the batch runner just collects those plus any outer exceptions (e.g., failure to open the `.h5`) into the same summary structure.

## UI

```
┌─ Batch TCSPC Append ─────────────────────────────────────────────────────┐
│                                                                          │
│  1. Datasets                                                             │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ ☑ Dish 1 - WT 60min As + Noco.h5      ch1,ch2,ch3   (no decay)     │  │
│  │ ☑ Dish 2 - TAOK2 KO 60min As + Noco   ch1,ch2,ch3   (no decay)     │  │
│  │ ☑ Dish 3 - RTN4 KO 60min As + Noco    ch1,ch2,ch3   (no decay)     │  │
│  │ ☐ Dish 4 - other.h5                   ch1,ch2,ch3   (decay present)│  │
│  └────────────────────────────────────────────────────────────────────┘  │
│  [Add datasets…]                                                         │
│                                                                          │
│  2. .bin source root  [/Volumes/<lab-server>/<export>/<dataset>…  ▾] │
│     Discovered groups: 3   [Auto-pair by name]                           │
│                                                                          │
│  3. Pairing                       Calibration                            │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ Dish 1 - WT 60min …   → [Dish 1 - WT 60min …          ▾]  freq=80, │  │
│  │                                                            ch1=(0.12, 0.98)…│
│  │ Dish 2 - TAOK2 KO …   → [Dish 2 - TAOK2 KO …          ▾]  freq=80, │  │
│  │                                                            ch1=(0.13, 0.97)…│
│  │ Dish 3 - RTN4 KO …    → [— select —                   ▾]  (no CSV) │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  4. Calibration CSV  [Choose CSV…]   loaded: 9 rows / 3 datasets         │
│                                                                          │
│  5. Stitching & orientation  ▸                                           │
│     Grid 4×4   Snake horizontal   Rotate 90° CCW   Flip: none            │
│     [Advanced: tokens, raw .bin geometry]                                │
│                                                                          │
│  6. If decay already exists:  (•) Skip existing  ( ) Overwrite all       │
│                                                                          │
│  [Validate]     [Run]   (Run disabled until Validate passes)             │
└──────────────────────────────────────────────────────────────────────────┘
```

The Run/progress view replaces the form once execution starts; the Close button returns to the launcher.

## Acceptance criteria

A reviewer can verify the feature end-to-end by:

1. Open a PerCell4 project containing 3 `.h5` datasets (each with `/intensity/ch1`–`/ch3`, no `/decay`).
2. Open **Batch TCSPC Append** from the launcher. All 3 datasets appear; all are checked by default.
3. Set source root to a parent folder with 3 subfolders matching dish names. The discovered-groups count reads 3.
4. Click **Auto-pair**. All 3 rows pair correctly with no user input.
5. Load a CSV covering all 3 datasets × 3 channels (9 rows). Calibration column populates for all 3 rows.
6. Leave stitching at default 4×4 snake-horizontal + 90° CCW. Click **Validate** → pre-flight succeeds, **Run** enables.
7. Click **Run**. Verify progress rows update sequentially, UI stays responsive, and final summary shows all 3 datasets ✓ with the expected channel count.
8. Open each `.h5` in the data viewer and confirm `/decay/ch1`–`/ch3` exist and `/metadata` has `flim_frequency_mhz` and per-channel calibrations matching the CSV.
9. Trigger a partial-failure scenario: corrupt one group's `.bin` files. Re-run with all 3 selected. Verify the corrupted dataset's row shows ⚠ Errors with a useful message; the other two complete ✓.
10. Re-run on the same datasets without changing conflict policy (Skip existing). Verify every channel reports "skipped (already present)" and no data is rewritten.
11. Re-run with Overwrite all. Verify channels are rewritten and calibration in `/metadata` is updated.
12. Remove one row from the CSV. Click **Validate**. Verify pre-flight fails with a specific "missing calibration for (Dish 2, ch3)" message.
13. Break pairing on one row (set to "— select —"). Verify pre-flight fails with "Dish 3 has no group assigned".
14. Cancel mid-batch (after dataset 1 finishes, while dataset 2 is stitching). Verify dataset 1 ✓, dataset 2 stops cleanly at the next safe boundary, dataset 3 marked "cancelled".
15. Click **Copy report to clipboard**. Paste into a text editor; verify a readable plain-text summary of the run.

## Scope boundaries

### Deferred for later

- **Batch phasor compute after append** — out of scope. The existing phasor flow can be re-run on the same datasets right after. Coupling the two would conflate two independent operations.
- **Per-dataset tile geometry / rotation** — explicitly out of scope. Heterogeneous tile layouts require separate batch runs. If real workflows demand it, R5 grows a CSV-driven override path later.
- **Parallel per-dataset execution** — sequential is fine for current dish counts (3–12). Parallelizing would compete for disk and Cellpose's GPU in adjacent workflows; revisit only if real runs are I/O-starved.
- **Persisting batch configurations** — no "save batch as preset" feature. The CSV and a screenshot of the dialog are enough for reproducibility right now.
- **Per-cell undo or rollback** — the append is forward-only. If the user wants to undo, the workflow is "delete `/decay/<ch>` from the affected `.h5` files" — out of scope for this feature.
- **Auto-generating the CSV from microscope metadata** — out of scope. The user produces the CSV manually from their LAS X / fitting output. If a clear automatable source ever exists, a separate generator can feed this dialog.

### Outside this product's identity

- **Editing `/intensity` channels during the append** — never. The append flow only touches `/decay/<ch>` and `/metadata`; `/intensity` is read-only here. Rotation and flip apply to `/decay` only (already enforced by `add_decay_to_dataset`).
- **Computing or modifying phasors in this dialog** — the dialog is an I/O layer, not an analysis layer. Calibration is *recorded*, not *applied*; the existing phasor flow consumes it later.
- **Discovering datasets outside the user's explicit selection** — the dialog never scans the project and silently adds datasets the user did not pick. R1's explicit multi-select is load-bearing.
- **Touching session selection fields** — `active_channel`, `active_segmentation`, `active_mask`, `filter_ids`, `selection` per the GUI state ownership rules. This dialog is an Action (per CLAUDE.md classification), not a Selector or Creator.

## Dependencies and assumptions

- `add_decay_to_dataset` already handles per-channel matching, tile stitching, rotation/flip, provenance, and `force` semantics (`src/percell4/application/use_cases/add_decay_to_dataset.py`). The batch runner is a thin loop over this use case; no changes to its public signature are required for the happy path.
- `match_bin_to_intensity` accepts the same `TokenConfig` and `CrossFormatRule` for every dataset in the batch (R5's uniformity assumption maps directly to its inputs).
- `DatasetStore.set_metadata({...})` accepts `flim_frequency_mhz` (scalar) and `channel_calibrations` (tuple of `(phase, modulation)` aligned to `channel_names`). Verified by reading `src/percell4/domain/io/models.py:204–207` (`FlimConfig`).
- `ProjectIndex` exposes the list of `.h5` files in the current project; the dialog can read it directly. (Verified: `src/percell4/project.py`.)
- The CSV reader can be a small pure-Python helper using `csv.DictReader` — no new dependency.
- LAS X export layout for the typical experiment is **parent / one-subfolder-per-dish / flat `.bin` files inside** (verified against `/Volumes/<lab-server>/<export>/<dataset>/`). Deeper nesting is handled transparently by `rglob`.
- All datasets in a single batch share the same channel count and channel name set (R4's CSV cross-check enforces this at validate time).

## Files likely touched (planning input, not implementation design)

- `src/percell4/gui/batch_tcspc_dialog.py` *(new)* — the dialog itself: dataset table, source picker, pairing grid, CSV upload, stitching/orientation pane, validation panel, progress + summary view.
- `src/percell4/application/use_cases/batch_add_decay.py` *(new)* — pure-Python batch orchestrator. Inputs: list of `(h5_path, source_dir, calibration)` triples, `TokenConfig`, `TileConfig`, `FlimConfig`, `CrossFormatRule`, conflict policy, rotation/flip. Outputs: a `BatchAppendReport` aggregating per-dataset `AppendReport`s plus calibration-write outcomes.
- `src/percell4/domain/io/calibration_csv.py` *(new)* — parser + validator for the long-format CSV. Returns a typed `BatchCalibration` mapping `dataset_stem → channel_name → (frequency_mhz, phase, modulation)`.
- `src/percell4/gui/workers.py` — extend with a `BatchAppendWorker(QThread)` that drives the orchestrator and emits progress signals per dataset / per stage.
- `src/percell4/interfaces/gui/main_window.py` — add a launcher entry / IO-tab button that opens `BatchTCSPCDialog`.
- `docs/audits/gui-element-classification.yaml` — classify the new dialog and its controls (it is an Action with embedded Selectors only at the field level — does not write `session.active_*`).
- Tests under `tests/`: CSV parser (happy + every validation failure mode); orchestrator (per-dataset success, per-dataset failure isolation, conflict-policy honored, calibration written before append, cancel mid-batch).

Detailed file changes and sequencing belong in `/ce-plan`.

## Outstanding questions

None blocking. Worth a one-line check at planning time:

- Whether the dialog should also offer a **Dry-run** mode that executes everything except the `add_decay_to_dataset` write call — useful for verifying calibrations land in `/metadata` correctly without committing decay data. Lightweight to add if `add_decay_to_dataset` grows a `dry_run` flag; can be deferred.
- Whether `frequency_mhz` should accept variation **across** datasets (R4 currently allows it). If field workflows always use one rep rate per microscope-session, locking the batch to a single global frequency would simplify the UI by hoisting `frequency_mhz` out of the CSV and into the dialog. The looser per-dataset rule is the safer default; tighten only if real CSVs reveal the column is always uniform.
- Auto-pair similarity threshold and tie-break behavior — fuzz ratio cutoff, what counts as ambiguous. Pure tuning; doesn't affect the spec.
- Whether the dialog should write a small `batch_run.log` file next to the source root for reproducibility, in addition to the clipboard report. Probably no — provenance is already on each `.h5`. Worth re-checking with users once they've run a few real batches.
