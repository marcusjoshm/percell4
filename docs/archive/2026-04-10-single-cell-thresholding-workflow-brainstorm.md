# Single-Cell Thresholding Analysis Workflow Brainstorm

**Date:** 2026-04-10
**Status:** Draft

## What We're Building

A first-class, multi-dataset batch workflow that takes a set of experiments from raw input through per-cell measurements and exports. The workflow lives under the currently-empty **Workflows tab** in the launcher as an entry titled **"Single-cell thresholding analysis workflow"**. Clicking it opens a configuration dialog where the user assembles every decision needed to run the batch: inputs, segmentation, thresholding rounds, CSV columns, and output folder. A second entry, **"Resume run..."**, reopens an in-progress run from disk.

Once started, the workflow executes as a **strict sequence of phases** across all selected datasets. Automatic phases run unattended with a progress dialog; interactive QC phases walk the user through each dataset in a queue. When all datasets are done, measurements are aggregated into a cross-dataset parquet file and exported as CSVs.

### Phases (strict, one after the next)

```
Phase 0: Compress  (tiff sources -> .h5)        [unattended]
Phase 1: Segment all datasets with Cellpose     [unattended]
Phase 2: Segmentation QC  DS1..N                [interactive queue]
Phase 3: Thresholding round 1 (all datasets)    [unattended]
Phase 4: Threshold QC round 1  DS1..N           [interactive queue]
Phase 5: Thresholding round 2 (all datasets)    [unattended]
Phase 6: Threshold QC round 2  DS1..N           [interactive queue]
  (repeat 5/6 for each configured round)
Phase 7: Measure all datasets                   [unattended]
Phase 8: Aggregate + export                     [unattended]
```

### Output layout

```
<user-picked parent>/
  run_2026-04-10_143022/
    run_config.json        full config + dataset list + timestamps
    run_state.json         resumable checkpoint (written on pause)
    measurements.parquet   cross-dataset DataFrame, all cells, all metrics, 'dataset' column
    combined.csv           human-readable flat export (user-selected columns)
    per_dataset/
      DS1.csv
      DS2.csv
      DS3.csv
```

## Why This Approach

**Strict phases over dataset-at-a-time.** The user explicitly rejected dataset-at-a-time because the mental mode for this workflow is "batch". Doing one phase across all datasets before starting the next also means the user can focus: segmentation QC is a different skill than threshold QC, and switching back and forth per dataset is more fatiguing than batching by task type.

**Strict (not pipelined) phases.** Simpler state, a single progress bar per unattended phase, matches the existing `_run_batch_compress` pattern at `launcher.py:801-886`, and the wall-clock savings of pipelining don't justify the added complexity.

**Parquet for the measurements store, separate from h5.** The user wants .h5 files to hold only image data and metadata. Per-cell measurements are tabular, cross-dataset, and want to be queried from pandas later. Parquet is the standard choice: typed, compressed, columnar, one-line read with `pd.read_parquet`, interoperable with R / DuckDB. The parquet always contains *every* metric on *every* channel — no data is lost at workflow time.

**No filtering inside the workflow.** All filtering (area cutoffs, intensity thresholds, group membership) happens post-hoc by the user against the parquet or CSVs. This guarantees that a different cutoff never requires re-running cellpose or thresholding — a huge time saver and provenance win.

**Labels and masks still go in the h5.** Per-pixel label arrays and binary masks *are* image data, they're already written there by the existing single-dataset flow (`store.write_labels`, `store.write_mask`), and standalone viewers of the h5 will see the QC-accepted segmentation. Only the measurement DataFrame needs to break out.

**Reuse the existing grouped-thresholding QC controller; build a slim dedicated dialog for segmentation QC.** The grouped-thresholding QC at `gui/threshold_qc.py:71-782` is already rich (group preview + per-group ROI-driven threshold QC). Reusing it verbatim means one code path and one set of bugs. The segmentation-QC side wants *only* the essentials (delete, draw, edge-margin preview, accept) — a focused dialog is clearer than bolting a workflow bar onto the full `SegmentationPanel`.

## Key Decisions

### 1. Inputs and Phase 0 (compression)

- Config dialog's **dataset picker** supports four add actions:
  - Add individual .h5 files (multi-select)
  - Add a folder of .h5 files (auto-discover, optional recursion)
  - Add a single .tiff source (opens the existing `CompressDialog` in single-dataset mode)
  - Add a .tiff folder (opens `CompressDialog` in batch discovery mode)
- Each added entry is either **existing .h5** or a **pending compress spec** (`DatasetSpec` from `io/models.py:95-181`).
- **Phase 0** compresses all pending tiff sources by calling the existing `import_dataset()` orchestrator, producing .h5 files that join the dataset list for later phases. Newly compressed files land next to the source tiffs (existing convention).

### 2. Segmentation (Phase 1 + Phase 2)

- **Global Cellpose settings** for the whole batch (one model, diameter, gpu, flow_threshold, cellprob_threshold, min_size). No per-dataset overrides.
- **Edge-cell removal is always on** as a workflow invariant — this workflow is strictly for whole-cell single-cell analysis and partial cells must never survive.
- Phase 1 runs `run_cellpose` + `filter_edge_cells` + `filter_small_cells` + `relabel_sequential` per dataset in a worker thread driven by a progress dialog. Results are written to each dataset's `/labels/cellpose_qc` via `store.write_labels`.
- **Phase 2 — Segmentation QC dialog** (slim dedicated window, NOT the full `SegmentationPanel`):
  - Loads each dataset's image + labels into the existing viewer
  - Controls: **Delete selected label**, **Draw new label** (napari polygon mode), **Edge-margin preview** (red overlay of cells that would be filtered), **Apply cleanup**
  - Workflow nav bar: **[← Back]  [‖ Pause]  [Accept & Next →]  (DS i of N)**
  - No "Skip dataset" — every dataset must be QC'd to participate in downstream phases
  - On Accept: labels are persisted back to `/labels/cellpose_qc` (overwriting the pre-QC version)
  - On Back: reloads the previous dataset and rolls its accepted label state back to editable

### 3. Thresholding (Phases 3..6, once per round)

- **Ordered list of named rounds** in the config dialog. Each round has:
  - **Name** (user-supplied, used in mask names and DataFrame columns)
  - **Channel** (dropdown from available channels across datasets — see Open Questions)
  - **Metric** (from `BUILTIN_METRICS`)
  - **Algorithm**: GMM (BIC or silhouette, max components) **or** K-means (n_clusters)
  - **Gaussian σ** for threshold smoothing
- Add / Remove / Up / Down buttons to reorder rounds
- Each round runs as: automatic phase (compute grouping + per-group thresholds for all datasets) followed by interactive QC phase (per-dataset queue).
- **QC step reuses `ThresholdQCController`** from `gui/threshold_qc.py` as-is — the existing group-preview + per-group ROI-driven threshold QC flow is exactly what the user wants. The workflow only adds the same **[← Back] [‖ Pause] [Accept & Next →]** navigation bar around it.
- Each accepted mask is written to its dataset's h5 under `/masks/<round_name>` via `store.write_mask`.
- Group assignments for the round are persisted at `/groups/<round_name>` in each dataset's h5 so they can be merged into the measurements DataFrame in Phase 7.

### 4. Measurement (Phase 7)

- Unattended. For each dataset:
  1. Load image + labels + all `/masks/*` + `/groups/*` for the rounds in this run
  2. Call `measure_multichannel(images, labels, metrics=ALL_BUILTIN_METRICS, mask=...)` — **every metric on every channel**, no selection at measurement time
  3. Merge `group_<round>` columns from the stored group DataFrames (reuses `_merge_group_columns` logic at `launcher.py:1538-1567`)
  4. Add a `dataset` column identifying the source file
- Append each dataset's DataFrame to an in-memory cross-dataset DataFrame.
- **Do not** write to `/measurements` in any h5. This workflow's measurements live only in the run folder parquet — the .h5 files stay pure image + metadata.

### 5. Aggregation + export (Phase 8)

- Write the cross-dataset DataFrame to `run_<ts>/measurements.parquet` (full fidelity, every column).
- Write `run_<ts>/combined.csv` containing **only the user-selected columns** from the config (always includes identity columns: `dataset`, `cell_id`, `label`, `centroid_y`, `centroid_x`, `area`, and `group_*` columns).
- Write `run_<ts>/per_dataset/<dataset>.csv` with the same selected columns, one file per dataset.
- Because the parquet has everything, the user can re-export a different column selection later without re-running the workflow.

### 6. Run configuration (`run_config.json`)

Single source of truth for how the run was set up. Contains:

- `run_id`, `started_at`, `finished_at`
- `output_parent`, `run_folder`
- `datasets`: list of `{name, h5_path, source (existing|compressed_from_tiff), compress_config?}`
- `cellpose`: full settings dict
- `thresholding_rounds`: ordered list of round specs
- `selected_csv_columns`: list of column names for CSV export
- `phases_completed`: list (filled as the workflow progresses — same file doubles as provenance)

QSettings remembers the last-used `output_parent` for convenience.

### 7. Resumability — `run_state.json`

When the user presses **Pause** during any interactive QC queue, the workflow writes `run_state.json` to the run folder containing:

- Current phase index and name
- Current dataset index within the phase
- List of completed dataset names in this phase
- Flag indicating the current dataset's in-flight QC edits were discarded (Pause does not auto-commit)

The Workflows tab has a second button, **"Resume run..."**, which opens a directory picker. Selecting a `run_<ts>/` folder reads both JSON files, reloads the config, jumps to the paused phase/dataset, and resumes the queue. Automatic phases that were already complete are not re-run.

### 8. Workflows tab structure

Replaces the two dead placeholders currently in `_create_workflows_panel` at `launcher.py:534-544`:

```
Workflows
  [Single-cell thresholding analysis workflow]   <- opens config dialog
  [Resume run...]                                 <- pick a run_*/ folder
```

Adding the workflow is a matter of registering a new entry in the categories list at `launcher.py:157-166` and writing a panel factory. The config dialog and the phase orchestrator are the new code to write.

## Resolved Questions

### 1. Channel availability across datasets

Datasets may have non-matching channel sets. The workflow computes the **intersection** of channel names across all selected datasets:

- **All datasets share 1+ channels.** Available channels for thresholding rounds = intersection. Extra per-dataset channels are hidden from the workflow UI but the dataset still participates. No warning.
- **Some datasets have zero overlap with the others.** Show a warning dialog listing the outlier datasets ("these datasets share no channels with the rest") and prompt the user to either **Proceed with the analyzable datasets** (drop the outliers from the run) or **Abort and fix the selection**. This covers the common case where the user picked the wrong folder.
- **No datasets share any channels.** Warning dialog with **Abort** as the only option — the run can't proceed.

Validation runs as soon as the user clicks **Start** in the config dialog, before Phase 0.

### 2. Back across phase boundaries

**Back stops at the current QC phase boundary.** Back is disabled on the first dataset of any QC phase. Completed phases are frozen — to revisit earlier work, the user would have to start a new run. This keeps state simple and avoids cascading invalidation (e.g., a seg edit invalidating downstream thresholds).

### 3. Launcher state when the workflow starts

**Close current dataset and lock main UI.** Clicking **Start** in the config dialog prompts "Close current dataset and start workflow?". On confirmation the workflow calls `CellDataModel.clear()`, disables all main-launcher action panels (Import, Segmentation, Grouped thresh, Measure, Export, etc.), and takes ownership of the viewer until the run finishes or is paused. Paused runs likewise keep the main UI locked until the run is resumed or explicitly cancelled.

### 4. Interactive QC dialog hosting

**Reuse the launcher's existing `ViewerWindow`.** Both the slim segmentation QC dialog (Phase 2) and the existing `ThresholdQCController` (Phases 4, 6, ...) load their per-dataset images, labels, and masks into the launcher's single viewer. Layers are cleared between datasets as the queue advances. No new viewer is spawned.

### 5. CSV column picker list

**Computed dynamically from config state.** As the user changes the dataset selection or adds/removes thresholding rounds, the column picker re-populates:

- Always-on identity columns: `dataset`, `cell_id`, `label`
- Always-available core columns (opt-in): `centroid_y`, `centroid_x`, `bbox_y`, `bbox_x`, `bbox_h`, `bbox_w`, `area`
- One `{channel}_{metric}` row for every intersected channel × every `BUILTIN_METRIC`
- One `group_{round_name}` row for every configured thresholding round

If the intersection or rounds change, the picker's checked state is preserved for column names that still exist; removed columns are silently dropped from the selection.

## Decisions Summary (for the planning step)

| Decision | Chosen |
|---|---|
| Execution model | Strict step-at-a-time phases across all datasets |
| Output layout | `run_<ts>/` with parquet + combined CSV + per-dataset CSVs |
| Measurements format | Parquet (cross-dataset, with `dataset` column) |
| Measurements location | Run folder only — NOT in any .h5 file |
| Labels/masks location | In each .h5 via existing `/labels` and `/masks` |
| Run config format | `run_config.json` sidecar in the run folder |
| No filters at workflow time | All filtering is post-hoc against parquet/CSVs |
| CSV column selection | User picks at config time; parquet has everything |
| Cellpose settings scope | Global (one config for the batch) |
| Edge-cell removal | Always on (workflow invariant) |
| Threshold rounds | Ordered list, add/remove/reorder, each with name + channel + metric + algo + sigma |
| Seg QC UI | Slim dedicated dialog (delete / draw / edge preview / accept) |
| Threshold QC UI | Reuse existing `ThresholdQCController` verbatim |
| QC nav controls | Accept & Next, Back, Pause (no Skip) |
| Run output location | User picks parent dir once in config; QSettings remembers it |
| Inputs | .h5 files (individual or folder) + .tiff sources (single or batch) compressed in Phase 0 |
| Resumability | Pause writes `run_state.json`; "Resume run..." entry in Workflows tab |
