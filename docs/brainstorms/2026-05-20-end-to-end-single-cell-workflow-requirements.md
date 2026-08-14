---
date: 2026-05-20
topic: end-to-end-single-cell-workflow
---

# End-to-End Single-Cell Workflow

## Problem Frame

PerCell4's existing batch entry — **"Single-cell thresholding analysis workflow"** under `src/percell4/gui/workflows/single_cell/` — covers most of what a researcher needs for cross-dataset single-cell analysis, but it lacks two capabilities that matter for phase-separated biology and edge-of-frame biology:

1. **Edge cells are always discarded.** Cells touching the image border are filtered out as a workflow invariant. For some experiments this discards meaningful signal — a ring of partial cells can carry a quantifiable contribution that the current workflow has no way to surface.
2. **Dilute-phase mask generation is a separate single-dataset workflow.** Researchers running a batch must either skip dilute-phase analysis or run the per-dataset dilute UI by hand, dataset-by-dataset, after the batch completes. There is no way to fold it into the same run.

The "end-to-end workflow" replaces the existing SCTW **in place** by extending it to handle both, plus adds two small summary CSVs that surface group- and dataset-level structure researchers currently have to compute by hand against the parquet.

---

## Actors

- A1. **Researcher**: configures the run once in a dialog, drives interactive QC phases (segmentation, threshold rounds, dilute), reads the resulting parquet + CSVs for downstream analysis.

---

## Key Flows

The workflow keeps its existing strict-phase orchestration. New phases and decisions are flagged **[NEW]** below.

```
Phase 0: Compress (tiff sources -> .h5)                          [unattended]
Phase 1: Segment all datasets with Cellpose                      [unattended]
         (edge-cell filtering respects configured mode  [NEW])
Phase 2: Segmentation QC  DS1..N                                 [interactive queue]
Phase 3: Thresholding round 1 (all datasets)                     [unattended]
Phase 4: Threshold QC round 1  DS1..N                            [interactive queue]
  (repeat 3/4 for each configured round)
Phase 5: Dilute-phase mask generation  DS1..N  [NEW, optional]   [interactive queue]
         (adaptive round count per dataset; inner loop reuses
          the existing single-dataset dilute UI)
Phase 6: Measure all datasets                                    [unattended]
         (synthetic edge-cohort row computed here when the
          size-normalized mode is selected  [NEW])
Phase 7: Aggregate + export                                      [unattended]
         (existing parquet + CSVs, plus summary_groups.csv
          and summary_datasets.csv  [NEW])
```

- F1. **Configure and start an end-to-end run**
  - **Trigger:** Researcher clicks "Single-cell thresholding analysis workflow" in the Workflows tab.
  - **Actors:** A1
  - **Steps:** Pick datasets (h5 + pending tiff sources) → choose Cellpose settings → choose edge-cell mode (exclude / include_as_normal / include_as_size_normalized_cohort) → define ordered list of grouped-threshold rounds → optionally enable dilute-phase generation and configure it once → choose CSV columns → pick output parent → Start.
  - **Outcome:** A new `run_<ts>/` folder is created, the workflow advances through Phase 0+ with the configured behavior.
  - **Covered by:** R1, R3, R11, R14

- F2. **Per-dataset adaptive dilute round loop**
  - **Trigger:** Phase 5 dequeues the next dataset.
  - **Actors:** A1
  - **Steps:** Workflow opens the dataset in the existing single-dataset dilute UI (locked settings from config) → researcher runs round 1 (compute → ThresholdQC modal → accept/reject) → workflow dilates accepted condensed mask and NaN-subtracts in memory → researcher decides "another round" or "done" → on done, the accumulated dilute mask is persisted to `/masks/<dilute_name>` → workflow advances to next dataset.
  - **Outcome:** Each dataset has a `/masks/<dilute_name>` written by Phase 6 measurement-time. Different datasets may have run different round counts.
  - **Covered by:** R11, R12, R13

- F3. **Edge-cohort synthetic row at measurement**
  - **Trigger:** Phase 6 begins measuring a dataset and the run is configured for `include_as_size_normalized_cohort`.
  - **Actors:** A1 (passive — runs unattended)
  - **Steps:** Standard per-cell measurement runs over every label (edge and whole) → identify edge cells (cells touching image border in the QC'd labels) → compute `A_mean = mean(area of non-edge cells)` → compute `N_theoretical = sum(area of edge cells) / A_mean` → for each metric column M, compute `synthetic_M = sum(M across all edge cells) / N_theoretical` → append one synthetic row with `cell_id=-1`, `is_edge_synthetic=True`, `group_<round>=NaN` to the dataset's measurements DataFrame → individual edge-cell rows are retained with `is_edge=True`.
  - **Outcome:** The dataset's contribution to the cross-dataset parquet contains its per-cell rows (edge + whole, both flagged) plus exactly one synthetic edge row.
  - **Covered by:** R5, R6, R7, R8, R9, R10

---

## Requirements

**Workflow shape and replacement scope**
- R1. The end-to-end workflow replaces the existing SCTW entry **in place**. The Workflows tab continues to show one entry ("Single-cell thresholding analysis workflow") and one "Resume run..." entry. No parallel "v2" entry is added.
- R2. Existing pipeline phases (compress, Cellpose segment, segmentation QC, grouped thresholding rounds with QC, measure, aggregate/export) are preserved as the spine of the workflow. The new dilute phase (R12) is inserted between grouped thresholding and measurement; the new edge-cohort synthetic row (R6) is computed during measurement.
- R3. The existing config dialog at `src/percell4/gui/workflows/single_cell/config_dialog.py` gains two new configuration sections: edge-cell mode (R3a) and optional dilute-phase generation (R11). All other sections (dataset picker, Cellpose settings, thresholding rounds table, CSV column picker, output parent) are preserved.
- R3a. Edge-cell mode is exposed as a single-select control with three options. Default is `exclude` to match current workflow behavior.

**Edge-cell handling (three modes)**
- R4. **`exclude` mode** (default): the workflow filters edge-touching cells out of labels during Phase 1 exactly as it does today (`filter_edge_cells` continues to run). Edge cells do not appear in `/labels/cellpose_qc`, do not participate in any downstream phase, and do not appear in the parquet.
- R5. **`include_as_normal` mode**: edge-touching cells are kept in `/labels/cellpose_qc`. They participate in segmentation QC, grouped thresholding clustering and QC, dilute (if enabled), and per-cell measurement with **no special treatment**. They appear as ordinary rows in the parquet with `is_edge=True`.
- R6. **`include_as_size_normalized_cohort` mode**: edge-touching cells are kept in `/labels/cellpose_qc` and participate in clustering, thresholding, and per-cell measurement exactly like `include_as_normal` (per the researcher's experience, partial cells do not meaningfully bias clustering). They appear as ordinary rows in the parquet with `is_edge=True`. **Additionally**, the workflow computes one synthetic edge-cohort row per dataset at measurement time per R7.
- R7. The synthetic edge-cohort row is computed per dataset as follows. Let `edge_cells` = cells whose label region touches the image border in the post-QC labels, and `whole_cells` = all other cells. Define `A_mean = mean(area of whole_cells)` and `N_theoretical = sum(area of edge_cells) / A_mean`. For each metric column M in the dataset's measurements, the synthetic value is `sum(M across edge_cells) / N_theoretical`. `N_theoretical` is left as a float; no rounding is applied.
- R8. The synthetic row has `cell_id = -1`, `is_edge_synthetic = True`, `is_edge = False`, and `group_<round>` = NaN for every configured round. The synthetic row is per dataset (not per group, not per round). Identity columns (`dataset`, `cell_id`) and a new `is_edge_synthetic` boolean column are added; all other columns are populated by the formula in R7.
- R9. Every per-cell row in the parquet carries an `is_edge` boolean. Edge cells have `is_edge=True`, whole cells have `is_edge=False`. The column is present in every run (including `exclude` mode, where it is uniformly `False`). An `is_edge_synthetic` boolean is also present in every run (uniformly `False` outside `include_as_size_normalized_cohort` mode).
- R10. Edge-mode edge cases. (a) If a dataset has zero edge cells in `include_as_size_normalized_cohort` mode, no synthetic row is emitted for that dataset. (b) If a dataset has zero whole cells (cannot compute `A_mean`), no synthetic row is emitted and a `DatasetFailure` is recorded in run metadata noting the cause. (c) `exclude` and `include_as_normal` modes are unaffected by these edge cases — they emit no synthetic row regardless.

**Dilute-phase mask in batch**
- R11. The config dialog includes an optional **"Generate dilute-phase mask"** checkbox. When checked, the dilute sub-panel exposes the same controls as `src/percell4/gui/grouped_seg_panel.py` plus the dilute-specific controls: dilute mask name (string), dilation radius (px, single value used every round). All dilute settings are **locked** when the user clicks Start; the dilute sub-panel becomes read-only for the duration of the run.
- R12. Phase 5 — a new interactive phase — runs only when dilute generation is enabled. It queues each dataset in turn and reuses the existing single-dataset dilute UI (from plan `docs/plans/2026-05-18-004-feat-dilute-phase-mask-generation-plan.md`) as the per-dataset inner loop. The inner loop is multi-round and user-terminated: the researcher iterates rounds (compute → QC modal → accept → dilate → NaN-subtract) until satisfied, then signals "done" to advance to the next dataset. **Round count per dataset is adaptive** — different datasets in the same run may complete different numbers of rounds.
- R13. The accepted dilute mask for each dataset is persisted to `/masks/<dilute_mask_name>` in that dataset's h5 via `DatasetStore.write_mask`. Measurement in Phase 6 picks up `/masks/<dilute_mask_name>` exactly like any other mask and includes its per-cell measurements in the parquet.
- R14. The dilute mask name must be unique against every threshold-round name in the same run. The config dialog validates this at "Start" time. Conflicts surface as an inline error in the dilute sub-panel.
- R15. The dilute phase always runs **after all grouped thresholding rounds** and **before measurement**. It is not interleaved with thresholding rounds.

**Grouped thresholding (no change)**
- R16. Grouped thresholding rounds remain configured as an ordered list at workflow start (add / remove / reorder in the config dialog). All rounds run in order during the run; no rounds can be added or removed once the workflow starts. This preserves existing `run_state.json` / Resume semantics.

**Outputs and report**
- R17. The existing exports — `measurements.parquet`, `combined.csv`, `per_dataset/<DS>.csv` — remain unchanged in format and location. The parquet automatically gains the new `is_edge` and `is_edge_synthetic` columns when produced by this workflow (always present, even when uniformly `False`).
- R18. The workflow writes a new `summary_groups.csv` to the run folder. One row per (`dataset`, `round_name`, `group_label`). Columns: `dataset`, `round_name`, `group_label`, `n_cells`, `fraction_of_dataset_cells`, and for every metric column M, `M_mean`, `M_median`, `M_std`. Edge cells contribute to these rows when present (their group assignment is real); the synthetic edge-cohort row does **not** contribute (it has NaN groups).
- R19. The workflow writes a new `summary_datasets.csv` to the run folder. One row per dataset. Columns: `dataset`, `source` (`h5_existing` | `compressed_from_tiff`), `n_cells_total`, `n_cells_whole`, `n_cells_edge`, `n_rounds_thresholding`, `n_rounds_dilute` (NaN when dilute disabled), `dilute_enabled` (bool), `edge_mode` (one of the three modes), `failure_reason` (NaN when none).

---

## Acceptance Examples

- AE1. **Covers R7, R8.** Given a dataset with 100 cells total, 20 of which are edge cells. Non-edge mean area = 500 px². Edge-cell total area = 4000 px². When the run is configured for `include_as_size_normalized_cohort`, the workflow computes `N_theoretical = 4000 / 500 = 8.0`. If the 20 edge cells have summed `mean_intensity_ch0` of 1600, then `synthetic_mean_intensity_ch0 = 1600 / 8.0 = 200.0`. The dataset's parquet contribution is 100 per-cell rows (20 with `is_edge=True`, 80 with `is_edge=True=False`, all with `is_edge_synthetic=False`) plus one synthetic row with `cell_id=-1`, `is_edge_synthetic=True`, `is_edge=False`, `mean_intensity_ch0=200.0`, and `group_<round>=NaN` for every round.

- AE2. **Covers R10.** Given a dataset where every detected cell touches the image border (zero whole cells) and edge mode is `include_as_size_normalized_cohort`. The workflow records a `DatasetFailure(dataset="DSx", reason="no whole cells to compute A_mean for edge-cohort normalization")`, omits a synthetic row for that dataset, but still writes per-cell rows for every edge cell (with `is_edge=True`). The run continues normally for other datasets.

- AE3. **Covers R12.** A run with 5 datasets and dilute enabled reaches Phase 5. The researcher runs 2 dilute rounds on dataset 1, accepts and moves on; runs 4 dilute rounds on dataset 2; runs 1 dilute round on dataset 3; and so on. Each dataset's final accumulated condensed-mask union is dilated and the complement-within-cells is written to `/masks/<dilute_name>` on that dataset's h5. `summary_datasets.csv` records each dataset's per-dataset `n_rounds_dilute` independently (2, 4, 1, ...).

- AE4. **Covers R14.** The researcher configures grouped thresholding rounds named `puncta_bright` and `puncta_dim`, enables dilute generation, and types `puncta_bright` as the dilute mask name. The dialog refuses to Start and shows an inline error in the dilute sub-panel: "Dilute mask name conflicts with thresholding round 'puncta_bright'."

---

## Success Criteria

- **Researcher outcome.** A researcher can configure a single batch run that produces, for every dataset, a Cellpose+QC'd segmentation, N grouped-threshold masks, optionally a dilute-phase mask, and a cross-dataset measurements parquet — without needing to re-open each dataset by hand for the dilute step.
- **Edge-cohort outcome.** A researcher running in `include_as_size_normalized_cohort` mode can find their edge contribution as a clearly-flagged row in the parquet (`is_edge_synthetic=True`) and a per-dataset count in `summary_datasets.csv`, with no further post-processing required.
- **Backward compatibility.** Existing downstream scripts that read `measurements.parquet`, `combined.csv`, or `per_dataset/<DS>.csv` keep working — the new columns (`is_edge`, `is_edge_synthetic`) and new files (`summary_groups.csv`, `summary_datasets.csv`) are additive. Existing column names and types are unchanged.
- **Handoff quality.** `ce-plan` can produce a focused implementation plan from this document without re-inventing edge-cohort math, the dilute-in-batch interaction model, or the new CSV column lists.

---

## Scope Boundaries

- **No new HTML or PDF report.** Outputs stay in parquet + CSV. Plotting and rich visualization remain the researcher's responsibility against the parquet.
- **No mid-run grouped thresholding additions.** Round list is fixed at config time. To add rounds, the researcher starts a new run.
- **No parallel "v2" workflow.** The existing SCTW config dialog and runner are modified in place. There is no alternate Workflows entry for an older or newer variant.
- **No standalone batch dilute workflow.** Dilute generation in batch is *inside* this workflow, not a separate Workflows entry. The existing single-dataset interactive dilute workflow (`docs/plans/2026-05-18-004`) remains available on its own.
- **No edge-cell highlighting in the segmentation QC UI.** The seg-QC dialog continues to show labels as it does today. Surfacing "this label is an edge cell" visually inside QC is a UX nicety left for a future round.
- **No automatic dilute convergence detection.** Per-dataset dilute round count remains user-driven. No auto-stop heuristic.
- **No per-group edge-cohort synthetic rows.** Exactly one synthetic row per dataset (when applicable), aggregated across all edge cells regardless of their group assignments. Per-group edge synthetics are rejected — not needed for the current biological framing.
- **No new summary CSVs beyond the two listed (R18, R19).** Edge-cohort and dilute-phase summaries are derivable directly from the parquet (`is_edge_synthetic=True` rows; `/masks/<dilute_name>` measurements) and don't need a dedicated file.

---

## Key Decisions

- **Modify SCTW in place** (vs. v2 alongside or hard replace): Lowest risk, preserves resume/run-folder semantics, keeps one Workflows entry. Researchers have a single mental model for batch runs.
- **Edge cells participate normally in clustering/thresholding even in size-normalized cohort mode**: Per the researcher's empirical experience, partial-cell metric values do not meaningfully bias group boundaries. Special-casing them in clustering would add complexity for no biological gain.
- **Synthetic edge row carries NaN group assignments, not a sentinel string like `'edge_cohort'`**: Avoids inventing group names that would propagate through every consumer. Filtering for the synthetic row uses `is_edge_synthetic=True` instead.
- **Parquet keeps individual edge cell rows alongside the synthetic row** (lossless): The synthetic row is a derived summary; preserving the individual rows leaves the door open for ad-hoc per-edge-cell analyses without re-running the workflow.
- **Per-dataset adaptive dilute round count** (vs. fixed N at config time, or unattended convergence): Matches the existing single-dataset dilute UX where round count is user-judgment-driven. Researchers gain batching for the dataset-switching ceremony while keeping the per-dataset interactive judgment loop intact.
- **Dilute phase runs after grouped thresholding and before measurement**: Matches the researcher's stated ordering. Grouped-thresh masks and dilute masks are orthogonal in this workflow — neither uses the other as input.
- **Two new summary CSVs only** (vs. richer report, or none): Group-level and dataset-level rollups are the most-requested derived views and the hardest to reconstruct by hand from the parquet. Edge-cohort and dilute summaries are trivially derivable and don't justify their own files.

---

## Dependencies / Assumptions

- The single-dataset dilute-phase workflow (plan `docs/plans/2026-05-18-004-feat-dilute-phase-mask-generation-plan.md`) is implemented or implemented before Phase 5 of this work begins. R12 depends on reusing that workflow's per-dataset interactive UI as the inner loop.
- `src/percell4/gui/workflows/base_runner.py` (`BaseWorkflowRunner`) continues to support `INTERACTIVE` `PhaseRequest` objects with completion callbacks, and the existing `seg_qc.py` / `threshold_qc_queue.py` queue patterns continue to compose under it.
- `src/percell4/measure/` per-cell measurement returns an `area` column for every cell (assumed — to be verified during planning, since `N_theoretical` depends on it).
- The existing `filter_edge_cells` postprocessing step in `src/percell4/segment/` can be made conditional at the workflow level without breaking other callers (assumed — to be verified during planning).
- HDF5 layout conventions for `/labels/cellpose_qc` and `/masks/<name>` are unchanged.

---

## Outstanding Questions

### Resolve Before Planning

*(None — all product questions resolved during this brainstorm.)*

### Deferred to Planning

- [Affects R6, R7][Technical] How are edge cells identified at measurement time — recomputed from label-bbox-vs-image-extent on each measurement run, or marked once at Phase 1 and persisted alongside labels? Probably recomputation, since it's cheap and avoids a new HDF5 contract.
- [Affects R3a][Technical] Where exactly does the edge-mode selector live in the config dialog UI — inside the Cellpose settings group (since it's a segmentation-postprocessing choice), or as its own section? UX question for planning.
- [Affects R12][Technical] How does the existing single-dataset dilute UI compose as an inner loop inside a `BaseWorkflowRunner` `INTERACTIVE` phase? Specifically: does the workflow host the dilute UI in the launcher's existing `ViewerWindow` (matching the seg-QC and threshold-QC pattern), or does the dilute UI spawn its own modal? Needs verification against the single-dataset plan.
- [Affects R12][Technical] Resume semantics inside the dilute phase. If the researcher pauses mid-dilute (partway through dataset 3 of 5, on round 2 of an indeterminate count), what does `run_state.json` capture and what does Resume restore? Probably "discard in-flight dilute state, resume by restarting dataset 3 from round 1", but this needs a decision during planning.
- [Affects R10][Technical] How is the "no whole cells" `DatasetFailure` surfaced to the researcher at run end — same path as Cellpose-failed-on-dataset failures, or a distinct category? Probably the existing `FailureRecord` machinery is sufficient.
- [Affects R9, R17][Needs research] What's the impact of adding two new always-present boolean columns (`is_edge`, `is_edge_synthetic`) on downstream consumers' parquet/pandas code? Likely harmless but worth a search through `src/percell4` for parquet readers.

---

## Next Steps

→ `/ce-plan` for structured implementation planning.
