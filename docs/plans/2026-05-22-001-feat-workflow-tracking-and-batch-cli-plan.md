---
title: "feat: Tracking in the single-cell workflow + headless batch compress/segment/track CLI"
type: feat
status: completed
date: 2026-05-22
deepened: 2026-05-22
---

# feat: Tracking in the single-cell workflow + headless batch compress/segment/track CLI

## Overview

Two related features that extend the just-landed time-lapse tracking (branch `feat/time-lapse-tracking-lineage`) into the batch analysis pipeline:

- **Feature 1 — Tracking in the single-cell thresholding workflow.** After Cellpose segmentation and segmentation-QC, the workflow automatically tracks cells for any dataset with `n_timepoints > 1`, then runs every downstream step (grouped thresholding, dilute-phase mask, particle analysis, measurement, reporting) against the **tracked** segmentation. The run also emits a `complete_tracks.csv` containing only the cells followed cleanly through every timepoint.
- **Feature 2 — Headless batch CLI + resume entry point.** A no-GUI CLI compresses, segments (all timepoints), and tracks multi-timepoint experiments — built to run overnight on large datasets. A new workflow entry point resumes from datasets that already have segmentation (and tracking) done, skipping compress/segment/track, with a per-dataset picker for which segmentation layer to use (defaulting to the tracked layer).

The unifying technical move is making the workflow **time-lapse-aware**: the workflow's phase helpers (`segment_one`, `threshold_compute_one`, `apply_threshold_headless`, `measure_one`) are single-frame today and must gain per-timepoint behavior, and the single run-wide segmentation name must become a **per-dataset effective segmentation name** so it can be swapped to the tracked layer (Feature 1) or chosen per dataset (Feature 2).

---

## Problem Frame

PerCell4 can now segment every timepoint of a time-lapse acquisition and track cells across frames (laptrack → a `<seg>_tracked` segmentation whose label value is the track id, plus a `/tracks/<name>` lineage table), but only interactively in the GUI. The batch single-cell workflow — the tool researchers actually use to process many dishes end-to-end — is entirely single-frame: it segments one plane per dataset, thresholds and measures one plane, and threads a single segmentation name through every phase. Two gaps follow:

1. A researcher running the interactive workflow on a time-lapse dataset gets no tracking, and downstream steps would silently operate on whichever single segmentation exists — not the track-consistent one.
2. Large time-lapse datasets are slow to segment (N Cellpose inferences per dataset). There is no way to do the heavy compress+segment+track work unattended overnight and then resume the interactive analysis the next day.

This plan closes both gaps while preserving the workflow's existing single-timepoint behavior unchanged.

---

## Requirements Trace

- R1. After Cellpose segmentation and seg-QC, the workflow automatically runs tracking for any dataset with `n_timepoints > 1`.
- R2. After tracking, all downstream workflow steps (grouped thresholding, dilute-phase mask, particle analysis, measurement, reporting) use the **tracked** segmentation for that dataset.
- R3. The workflow segments **all** timepoints of a time-lapse dataset (a `(T,H,W)` raw-label resource), not just one frame.
- R4. For time-lapse datasets, downstream steps run **per-frame independently**, producing per-`(timepoint, cell)` results (grouped thresholding clusters within each frame; masks, particles, and measurements are computed per frame). (User-confirmed.)
- R5. For tracked datasets the run produces a `complete_tracks.csv` — **long format**, one row per `(track, timepoint)` — containing only **full-span, gap-free tracks with no parent and no daughters**: present in every timepoint, not a division daughter (`parent_track_id == NO_PARENT`), and never itself a division parent. (User-confirmed.)
- R6. A headless CLI batch-processes multi-timepoint experiments: compress → segment all timepoints → track, with no GUI, suitable for an overnight run.
- R7. A workflow entry point resumes from datasets that already have segmentation (and tracking), skipping compress/segment/track and starting at thresholding/measurement.
- R8. The resume entry point lets the user choose, **per dataset**, which `/labels/` segmentation to use, pre-selecting the tracked layer when present. (User-confirmed.)
- R9. All measurement output and the complete-tracks CSV are written to the **run folder** (never back into each dataset's `/measurements`), atomically.
- R10. When an `.h5` dataset used by the workflow **already has a segmentation layer**, the workflow **automatically skips the Cellpose (segment) step** (and seg-QC) for that dataset and uses the existing segmentation downstream — preferring a tracked layer when present. This applies in the normal workflow, not only the explicit resume entry point. (User-added.)

**Related prior work:** the single-cell workflow (`docs/plans/2026-04-10-feat-single-cell-thresholding-workflow-plan.md`, `docs/plans/2026-05-20-001-feat-end-to-end-single-cell-workflow-plan.md`) and the tracking foundation (`docs/plans/2026-05-21-003-feat-time-lapse-tracking-lineage-plan.md`, which explicitly deferred batch-workflow and CLI integration — this plan picks it up).

---

## Scope Boundaries

- Not changing single-timepoint workflow behavior: a dataset with `n_timepoints == 1` **and no pre-existing segmentation** flows exactly as today (no tracking phase, no per-timepoint loops, no complete-tracks CSV). (The R10 auto-skip can still apply to a pre-segmented single-timepoint `.h5` — that is an intended new edge behavior, not part of the main computed-segmentation path.)
- Not building lineage-tree visualization, manual track correction, or track-editing UI — tracking is computed and reported, not hand-edited (carried from the tracking plan's scope).
- Not adding cross-FOV/stage-position tracking — tracking is within one assembled field over time.
- Not adopting a second tracking engine — laptrack remains the engine.
- Not implementing a generic resume-any-phase mechanism — the resume entry point specifically resumes from "segmentation (and tracking) already done", not arbitrary mid-run checkpoints. (The README's advertised "Resume run…" button has no existing implementation; this plan does not build general run-resume.)

### Deferred to Follow-Up Work

- **Interactive QC of long time-lapse movies at scale** (U8, U9): per-frame seg-QC and threshold-QC across many timepoints is included, but if reviewing every frame of long movies proves impractical, a "QC a representative subset of frames" affordance is a separate follow-up.
- **Tracking-aware summary statistics** beyond the complete-tracks CSV (e.g. division-rate or survival summaries) — a later reporting iteration.

---

## Context & Research

### Relevant Code and Patterns

- **Workflow phase generator:** `src/percell4/gui/workflows/single_cell/runner.py` (`SingleCellThresholdingRunner._phase_generator`, lines ~128–292) — a generator yielding `PhaseRequest`s in order: compress → segment → seg_qc → (per round: threshold_compute → threshold_qc/threshold_apply) → dilute → measure → export. Handler factories `_make_*_handler` build zero-arg callables.
- **Pure phase helpers:** `src/percell4/workflows/phases.py` — `compress_one`, `segment_one` (~175, single 2D plane via `adapters.cellpose.run_cellpose`), `threshold_compute_one`, `apply_threshold_headless`, `measure_one` (~849, single-frame `int(labels.max())`), `measure_particles_one` (~1137), `write_staging_parquet` (~1115), `export_run` (~1303 — writes `measurements.parquet`, `combined.csv`, `per_dataset/*.csv`, `summary_groups.csv`, `summary_datasets.csv`, `particles.*` via `_build_summary_groups`/`_build_summary_datasets`). **`phases.py` uses `store.DatasetStore` + `adapters.cellpose` + `domain.*` directly (Qt-free) and threads `seg_name=` into every helper.**
- **Config + entry models:** `src/percell4/workflows/models.py` — `WorkflowConfig` (`cellpose_segmentation_name` default `"cellpose_qc"`, line ~288), `WorkflowDatasetEntry` (`name`, `source`, `compress_plan`, line ~231), `DatasetSource` (`H5_EXISTING`, `TIFF_PENDING`), `CellposeSettings`, `ThresholdingRound`, `ParticleSettings`, `DiluteSettings`, `RunMetadata`.
- **Runner base:** `src/percell4/gui/workflows/base_runner.py` — `BaseWorkflowRunner(QObject)`, `PhaseRequest`/`PhaseResult`/`PhaseKind`/`WorkflowEvent`, generator-driven state machine; `run_config.json` lifecycle + `RunLog`.
- **QC controllers:** `src/percell4/gui/workflows/single_cell/seg_qc.py` (`SegmentationQCController` — per-dataset label editor, persists to `/labels/<seg_name>` on accept), `threshold_qc_queue.py` (`ThresholdQCQueueEntry`), `dilute_queue.py` (`DilutePhaseQueueEntry`). All single-frame today.
- **Tracking:** `src/percell4/application/use_cases/track_cells.py` (`TrackCells.execute` — shifts track ids to 1-based, relabels, writes `<seg>_tracked` + `/tracks`), `src/percell4/adapters/laptrack_tracker.py` (`LaptrackTracker`), `src/percell4/domain/tracking/lineage.py` (`build_lineage_table`, `build_tracks_array`, `build_graph_from_lineage`, `LINEAGE_COLUMNS`, `NO_PARENT`), `relabel.py` (`relabel_stack_by_track`).
- **Time-lapse measurement reference:** `src/percell4/application/use_cases/measure_cells.py` (`MeasureCells._measure_timelapse`, `_measure_one`, `_join_lineage`) — the per-timepoint pattern `phases.measure_one` must mirror. Measurements carry `timepoint`, `track_id`, `tree_id`, `parent_track_id` for tracked segmentations.
- **Store time-lapse APIs:** `src/percell4/store.py` — `read_labels(name, view_bin, timepoint=None)`, `read_array_frame`, `write_labels`/`write_mask` accept `(T,H,W)`, `write_tracks`/`read_tracks`/`list_tracks`, `metadata["n_timepoints"]`. Segment-all-timepoints adapter: `adapters/cellpose.py::run_cellpose_stack`.
- **CLI templates:** `src/percell4/interfaces/cli/batch_export.py` (argparse, `_resolve_paths`, per-item stdout progress, exit codes — the **batch iteration + headless progress** template), `run_pipeline.py` (the **Qt-free composition root** template: `Hdf5DatasetRepository` + `NullViewerAdapter` + `Session` + use cases). `adapters/importer.py::import_dataset` is the headless compress entry.
- **Entry points:** `pyproject.toml` `[project.gui-scripts] percell4-gui` only; no `[project.scripts]`; CLIs run via `python -m percell4.interfaces.cli.<module>`.

### Institutional Learnings

- `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md` — tracked-segmentation creation is a Creator (store → viewer → refresh → set_active); the **headless** workflow/CLI paths have no viewer/combos, so factor the store-write + selection into a use-case/phase the headless path can call without the viewer steps.
- `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md` + the provenance invariant — **each `.h5` holds only image data + metadata + labels + masks; measurement DataFrames live in the run folder** (Parquet + CSVs). The complete-tracks CSV and tracked measurements go to the run folder (R9), mirroring `export_run`'s summary builders.
- `docs/solutions/architecture-patterns/atomic-write-contract.md` — new output-file sites (complete-tracks CSV) must use the tmp + `os.replace` idiom (`workflows/artifacts.py::write_atomic`) so an interrupted overnight CLI never leaves a half-written artifact.
- `docs/solutions/logic-errors/batch-compress-development-lessons.md` — discover/scan datasets at config time and pass explicit per-dataset inputs into processing; do not `rglob` at consumption time. Binarize masks 0/1 at the write boundary.
- `docs/solutions/architecture-patterns/decay-write-path.md` — if the CLI handles `.bin`/TCSPC, reuse `write_decay_streaming` via `import_dataset`; do not add a new decay write path.
- `docs/solutions/architecture-patterns/{session-to-napari-one-way-push,consolidate-canonical-state-over-per-module-overrides-2026-05-14}.md` — the resume per-dataset picker is a **Selector**: in the GUI it writes the per-dataset config choice; it must not become a parallel hidden override of `Session.active_segmentation`.
- `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md` — the per-dataset segmentation picker must list `/labels/` resources only (never masks), and tag layer type metadata.
- **T1 audit (root CLAUDE.md R15/R16):** edits to `application/use_cases/*`, `adapters/*`, `store.py`, and `gui/*Dialog.py` (the resume config dialog) are T1 — run `python3 scripts/learnings_applicability.py <path>` and consult `docs/audits/canonical-sources-matrix.yaml` before editing. `workflows/phases.py` and `gui/workflows/` are **not** T1. Capture two doc gaps with `/ce-compound` after landing: the laptrack tracking adapter (undocumented) and the workflow resume/per-dataset-segmentation model.

### External References

- None required — laptrack is already integrated and verified (tracking plan); the CLI/argparse and workflow patterns are well-established locally (3+ direct examples each).

---

## Key Technical Decisions

- **Per-dataset effective segmentation name.** Replace the single run-wide `seg_name` threading with a per-dataset value: phases read `entry.effective_seg_name or cfg.cellpose_segmentation_name`. This one mechanism serves both features — tracking sets a dataset's effective name to `<seg>_tracked` (Feature 1), and the resume picker sets it per dataset (Feature 2). Add a mutable field on the per-dataset working entry (or a runner-owned `dict[dataset_name -> seg_name]`), kept off the frozen `WorkflowConfig`.
- **Extend `phases.py` with per-timepoint loops; do not cross into the Session/use-case seam.** The workflow operates on `DatasetStore` + run-folder artifacts; `MeasureCells` operates on repo/Session. To avoid mixing the two storage APIs, `phases.measure_one`/`threshold_*`/`segment_one` gain their own per-timepoint behavior, mirroring the **logic** of `MeasureCells._measure_timelapse` (read `read_labels(seg, timepoint=t)`, loop, tag `timepoint`/`track_id`). The use-case layer remains the path for the CLI/GUI.
- **A `phases`-level per-frame channel reader is a load-bearing prerequisite (4D layout).** `store.read_channel("intensity", channel_idx)` — used today by `segment_one`, `threshold_compute_one`, `apply_threshold_headless`, and the QC controllers — only handles 2D `(H,W)` and 3D `(C,H,W)`; it **raises on the 4D `(T,C,H,W)` layout** that `import_dataset` writes for multichannel time-lapse, and it has no `timepoint=` parameter. Every per-timepoint phase must instead read one frame via `store.read_array_frame("intensity", t)` then split channels with `domain/io/layout.py::split_channels_2d` (both already used by `Hdf5DatasetRepository.read_channel_images(timepoint=t)`). Introduce a small `phases`-level helper (e.g. `read_segmentation_channel_frame(store, channel_idx, t)`) built on those, reused by U2/U4/U5 and the QC controllers (U8/U9). `split_channels_2d` lives in `domain/io/` so `phases.py` imports it cleanly. This is the single most underestimated piece — call it out explicitly in each per-timepoint unit.
- **Import boundaries are clear for this work.** `percell4.workflows` has **no** import-linter contract, and `phases.py` already imports `adapters.cellpose`/`adapters.importer`/`domain.*`; adding `adapters.laptrack_tracker` + `domain.tracking` to `phases.py` introduces no new violation. The U6 shared helper (`build_tracked_result`) added to `domain/tracking/` must take a `TrackingResult` and **never import laptrack or h5py** — that keeps the `domain` contract (the one contract currently passing) green. (Note: the `application` contract is already BROKEN on `main` for pre-existing reasons unrelated to this plan, e.g. `segment_cells -> gui._bin_suffix`; this plan neither worsens nor must fix it.)
- **Workflow tracking via a phase helper `phases.track_one(store, raw_seg_name, ...)`,** not the `TrackCells` use case (which needs a Session). To avoid drift, extract the shared orchestration — shift track ids to 1-based + `relabel_stack_by_track` + `build_lineage_table` — into a pure `domain/tracking` helper used by **both** `TrackCells` and `phases.track_one`. The two differ only in store API (`Hdf5DatasetRepository` vs `DatasetStore`).
- **Complete-tracks selection is pure domain.** A `select_complete_tracks(measurements_df, lineage_df, n_timepoints)` function in `domain/tracking/lineage.py` returns the long-format subset; the workflow's `export_run` writes `complete_tracks.csv` from it (atomic). "Complete" = `begin_t == 0` AND `end_t == n_timepoints - 1` AND a row in every timepoint (no gaps) AND `parent_track_id == NO_PARENT` AND `track_id` is never a division parent.
- **Per-frame-independent downstream (R4).** For time-lapse, each phase loops timepoints and processes each frame independently: grouped thresholding clusters within each frame, masks/particles/measurements are per frame. Stored per-frame masks are written as `(T,H,W)` mask resources (the store accepts them).
- **CLI = use-case composition root + batch loop.** New `interfaces/cli/batch_process.py` (argparse, mirroring `batch_export.py`) over a new headless use case `application/use_cases/batch_process_datasets.py` that, per discovered dataset, calls `import_dataset` → `SegmentCells.run_inference_stack`+`finalize` → `TrackCells.execute` (when `n_timepoints > 1`). Register a `percell4-batch` console_script in `pyproject.toml` for convenient overnight invocation.
- **Resume entry = a config-dialog mode** that builds a `WorkflowConfig` of `H5_EXISTING` datasets and a per-dataset `effective_seg_name`, plus a runner flag that makes `_phase_generator` skip compress/segment/seg_qc/track. The generator already skips compress for non-`TIFF_PENDING` entries.
- **Per-dataset auto-skip of Cellpose when a segmentation already exists (R10).** Independent of the explicit resume mode: the segment/seg-QC phases are emitted **per dataset only when that dataset has no usable segmentation** on disk. The runner inspects each dataset's `store.list_labels()` (minus masks) at generator time; if a segmentation is present, it skips segment + seg-QC for that dataset and seeds the per-dataset effective seg name to the detected segmentation (preferring a `*_tracked` layer). Likewise the track phase is skipped for a dataset that already has a tracked segmentation. This makes "add a pre-segmented `.h5` to a normal run" just work; the explicit resume entry (U12) is the all-datasets-pre-segmented convenience with an explicit per-dataset picker for disambiguation/override.

---

## Open Questions

### Resolved During Planning

- Complete-track definition: full-span, gap-free, non-dividing (user-confirmed).
- Complete-tracks CSV shape: long, one row per `(track, timepoint)` (user-confirmed).
- Resume segmentation selection: per-dataset picker defaulting to tracked (user-confirmed).
- Time-lapse downstream behavior: per-frame independent (user-confirmed).

### Deferred to Implementation

- Exact home of the per-dataset effective-seg-name state (mutable field on the working entry vs a runner-owned dict) — decide when touching `runner.py`/`models.py`; both are viable, pick the one that keeps `WorkflowConfig` frozen.
- Whether per-frame grouped-thresholding should reuse one clustering model across frames or re-cluster each frame — default re-cluster per frame (per-frame independent); revisit if results are noisy.
- CLI input surface: discover TIFF dataset folders under a root (reuse `domain/io` discovery) vs accept explicit per-dataset specs — settle against the existing compress discovery API during U10.
- How interactive seg-QC presents a `(T,H,W)` stack (slider review of all frames vs a chosen frame) — settle in U8 against the napari dims slider already wired.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

Workflow phase flow with tracking (time-lapse dataset, `n_timepoints > 1`):

```
per dataset, at generator time (U13):
   has existing segmentation on disk?
     yes → SKIP segment + seg-QC; effective_seg = existing (prefer *_tracked)
            (and SKIP track if a *_tracked layer already exists)
     no  → run segment + seg-QC below

compress ─▶ segment (ALL timepoints → /labels/<seg> (T,H,W)) ─▶ seg-QC (slider, frame-scoped edits)
   │
   ▼
TRACK (new phase, gated n_timepoints>1):
   track_one(store, raw_seg=<seg>) → /labels/<seg>_tracked (T,H,W) + /tracks/<seg>_tracked
   set entry.effective_seg_name = "<seg>_tracked"
   │
   ▼   (downstream now reads effective_seg_name, looping timepoints)
per round: threshold_compute (per-frame grouping) ─▶ threshold_qc/apply (per-frame masks)
   ▼
dilute mask (per-frame) ─▶ measure (per-(timepoint,cell), + track columns) ─▶ staging parquet
   ▼
export_run: measurements.parquet, combined.csv, summaries…  +  complete_tracks.csv  (tracked datasets only)
```

Effective-segmentation resolution (every phase):

```
seg = entry.effective_seg_name or cfg.cellpose_segmentation_name
# single-timepoint dataset: effective_seg_name is None, seg == cfg name (unchanged behavior)
# after track phase: effective_seg_name == "<seg>_tracked"
# resume entry: effective_seg_name set per dataset by the picker
```

Complete-tracks filter (per tracked dataset):

```
keep track_id where:
   begin_t == 0  and  end_t == n_timepoints-1          (spans the whole movie)
   rows present at every timepoint 0..n_timepoints-1    (no gaps)
   parent_track_id == NO_PARENT                         (not a division daughter)
   track_id not in {parent_track_id of any track}       (never divided)
→ long-format rows (track_id, timepoint, measurements…) for kept tracks
```

---

## Implementation Units

> **Unit ordering note:** units are listed in logical/dependency order, not strict numeric order. U13 (auto-skip) was added after the initial U1–U12 set and is placed next to U2 because it is part of the segment-phase decision; U6 (shared tracking helper) precedes U3 (tracking phase) because U3 depends on it. U-IDs are stable identifiers, not a sequence.

### Phase 1 — Time-lapse-aware workflow foundation (shared by both features)

- U1. **Per-dataset effective segmentation name**

**Goal:** Introduce a per-dataset effective segmentation name, held in a **runner-owned dict** (`self._effective_seg: dict[str, str]`, dataset name → seg name), resolved in every phase handler with fallback to `cfg.cellpose_segmentation_name`, so it can be overridden to the tracked layer (U3) or a picked/auto-detected layer (U12/U13) without touching the frozen `WorkflowConfig`.

**Requirements:** R2, R8

**Dependencies:** None

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/runner.py` (runner-owned `self._effective_seg: dict[str, str]`; resolve `self._effective_seg.get(entry.name, cfg.cellpose_segmentation_name)` in each `_make_*_handler`, including the seg-QC/threshold-QC/dilute controller constructors that take `seg_name=`)
- Modify: `src/percell4/workflows/phases.py` (helpers already accept `seg_name=`; no signature change — the runner passes the resolved name)
- Test: `tests/test_workflows/test_effective_seg_name.py` (new)

**Decision (resolved):** the override lives in the runner-owned dict, not on `WorkflowDatasetEntry`; `WorkflowConfig`/`WorkflowDatasetEntry` stay frozen. Seed the dict empty per run and reset it on each `start()` so it never leaks across runs.

**Approach:**
- Add a runner attribute (e.g. `self._effective_seg: dict[str, str]`) seeded empty; every handler factory resolves `self._effective_seg.get(entry.name, cfg.cellpose_segmentation_name)` and passes it as `seg_name=`.
- This is a no-op for current runs (map empty → config name everywhere), preserving single-timepoint behavior exactly.

**Patterns to follow:** the existing `seg_name=self._config.cellpose_segmentation_name` threading in every `_make_*_handler`.

**Test scenarios:**
- Happy path: with no overrides, every phase handler resolves to `cfg.cellpose_segmentation_name` (parity with today).
- Edge case: setting the map for one dataset changes only that dataset's resolved seg name; others stay on the config default.

**Verification:** All phase handlers read the per-dataset resolved name; existing single-timepoint workflow tests pass unchanged.

---

- U2. **Time-lapse-aware segmentation phase**

**Goal:** `phases.segment_one` segments **all** timepoints when `n_timepoints > 1`, writing a `(T,H,W)` raw-label resource; single-timepoint datasets are unchanged.

**Requirements:** R3

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/workflows/phases.py` (`segment_one`, `_read_segmentation_channel`; **add a per-frame channel reader** `read_segmentation_channel_frame(store, channel_idx, t)` built on `store.read_array_frame("intensity", t)` + `domain/io/layout.py::split_channels_2d`, and a `(T,H,W)` stack reader for the seg channel)
- Modify: `src/percell4/gui/workflows/single_cell/runner.py` (`_make_segment_handler` / `_make_segment_worker_handler` — pass the channel stack; hoisted model reused across frames)
- Test: `tests/test_workflows/test_phases_segment_timelapse.py` (new), `tests/test_workflows/test_phases_channel_frame_reader.py` (new — covers the 4D `(T,C,H,W)` read)

**Approach:**
- **First close the 4D read gap (see Key Technical Decisions):** `store.read_channel` raises on `(T,C,H,W)` and cannot select a timepoint. Build the seg-channel `(T,H,W)` stack by reading each frame via `read_array_frame("intensity", t)` + `split_channels_2d` (picking the seg channel), or by reading the full `/intensity` and slicing the channel axis — whichever is cleaner; this helper is reused by U4/U5/U8/U9.
- Read `store.metadata["n_timepoints"]`. When `> 1`, assemble the seg channel as a `(T,H,W)` stack and run `adapters.cellpose.run_cellpose_stack` (reuses the hoisted model), post-process each frame (existing `filter_edge_cells`/`filter_small_cells`/`relabel_sequential`), stack, `store.write_labels(seg, (T,H,W))`. When `== 1`, the existing single-plane path runs unchanged.
- Mirror the per-frame postprocess already implemented in `SegmentCells.finalize` (don't duplicate inference logic — reuse `run_cellpose_stack`).

**Patterns to follow:** `application/use_cases/segment_cells.py::run_inference_stack` + `_postprocess_frame`; `adapters/cellpose.py::run_cellpose_stack`.

**Test scenarios:**
- Happy path: a `(3,H,W)` single-channel time-lapse → `(3,H,W)` raw labels; each frame independently segmented (use a fake/monkeypatched segmenter as existing phase tests do).
- Covers the 4D gap. Happy path: a `(3,C,H,W)` multichannel time-lapse → the per-frame reader returns the correct `(H,W)` seg-channel plane per timepoint (does NOT raise as `read_channel` would), and segmentation produces `(3,H,W)` labels.
- Edge case (backward compat): single-timepoint dataset (`(H,W)` or `(C,H,W)`) → 2D labels exactly as today via the unchanged path.
- Edge case: a frame with no cells produces an all-background frame without aborting the stack write.

**Verification:** Time-lapse datasets (single- and multi-channel) get a `(T,H,W)` raw segmentation in the workflow; single-timepoint output byte-identical to today.

---

- U13. **Auto-skip Cellpose (and seg-QC / track) for datasets with an existing segmentation**

**Goal:** Per dataset, when the `.h5` already has a usable segmentation on disk, the workflow skips the segment and seg-QC phases for that dataset and uses the existing segmentation downstream (preferring a `*_tracked` layer); skip the track phase too when a tracked segmentation already exists. Applies to the normal workflow, not only the resume entry.

**Requirements:** R10

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/runner.py` (`_phase_generator` emits segment/seg_qc per dataset only when no segmentation is detected; seeds `self._effective_seg[name]` from the detected segmentation)
- Modify: `src/percell4/workflows/phases.py` (a small pure helper to pick the default segmentation name from a dataset's label inventory, e.g. `pick_existing_segmentation(label_names) -> str | None`, preferring `*_tracked`)
- Test: `tests/test_workflows/test_runner_autoskip_segmentation.py` (new), `tests/test_workflows/test_pick_existing_segmentation.py` (new, pure)

**Approach:**
- At generator time, for each active dataset, read `store.list_labels()` (which already excludes masks — masks live in a separate `/masks/` group, so no subtraction is needed). If it is non-empty, do **not** yield segment or seg_qc for that dataset; instead seed `self._effective_seg[name]` via `pick_existing_segmentation`.
- `pick_existing_segmentation(label_names)` selection rule (resolved): if any name ends with `_tracked`, pick that (the most-derived tracked layer); else if exactly one segmentation, pick it; else (**multiple untracked, no tracked** — e.g. `cellpose`, `cellpose_bin3`, `manual` coexisting) pick the **lexicographically first** and record a non-fatal warning to the run log. Do **not** silently fail the dataset; the explicit resume picker (U12) is where the user overrides an undesired auto-pick.
- A dataset with a `*_tracked` segmentation also skips the track phase (already tracked). A dataset with only a raw segmentation on a multi-timepoint `.h5` still goes through the track phase (U3) so downstream uses tracked data.
- Datasets with no segmentation flow through segment + seg-QC as today. `TIFF_PENDING` datasets are always segmented (they have no labels until compressed+segmented).

**Patterns to follow:** `datasets_without_failures`; the per-dataset effective-seg seeding from U1; note `store.list_labels()` already returns segmentations only (masks are a separate group — see `_build_handle_metadata`).

**Test scenarios:**
- Covers R10. Happy path: an `H5_EXISTING` dataset with a `cellpose` segmentation → generator yields no segment/seg_qc for it; effective seg name == `cellpose`; downstream phases run.
- Covers R10. Happy path: an `H5_EXISTING` dataset with both `cellpose` and `cellpose_tracked` → effective name == `cellpose_tracked`; segment, seg_qc, and track all skipped.
- Edge case: a multi-timepoint `H5_EXISTING` dataset with only a raw `(T,H,W)` segmentation → segment/seg_qc skipped but the track phase still runs, then downstream uses the tracked result.
- Edge case: a `TIFF_PENDING` dataset (no labels yet) → segmented normally.
- Edge case: a dataset with multiple untracked segmentations and no tracked one → `pick_existing_segmentation` returns the lexicographically first and a warning is logged (not a silent mis-pick, not a hard failure).

**Verification:** Pre-segmented datasets skip Cellpose automatically in a normal run; tracked datasets also skip tracking; un-segmented and TIFF-pending datasets segment as today.

---

- U6. **Shared tracking orchestration helper (domain) + TrackCells refactor**

**Goal:** Extract the tracked-result construction (shift track ids to 1-based, relabel the stack, build the lineage table) into a pure helper both `TrackCells` and the new workflow `track_one` reuse, preventing logic drift.

**Requirements:** R1, R2

**Dependencies:** None

**Files:**
- Modify: `src/percell4/domain/tracking/lineage.py` or new `src/percell4/domain/tracking/build.py` (e.g. `build_tracked_result(raw_stack, tracking_result) -> (tracked_labels, lineage_df)`)
- Modify: `src/percell4/application/use_cases/track_cells.py` (call the shared helper instead of inline id-shift/relabel)
- Test: `tests/test_domain/test_build_tracked_result.py` (new); existing `tests/test_application/test_track_cells.py` must still pass

**Approach:**
- Move the 1-based shift + `relabel_stack_by_track` + `build_lineage_table` sequence (currently inline in `TrackCells.execute`) into the pure helper, taking a raw `(T,H,W)` stack and a `TrackingResult`, returning the tracked `(T,H,W)` labels and the lineage DataFrame. `TrackCells.execute` becomes: load raw → `tracker.track` → `build_tracked_result` → write via repo + Creator steps.

**Execution note:** Characterization-first — assert the refactored `TrackCells` produces identical tracked labels + lineage to the current implementation before/after.

**Patterns to follow:** existing `TrackCells.execute` body; `domain/tracking/relabel.py`, `lineage.py`.

**Test scenarios:**
- Happy path: a raw `(T,H,W)` stack + a `TrackingResult` → tracked labels with 1-based stable ids and a lineage table; matches the current `TrackCells` output (characterization).
- Edge case: division `TrackingResult` → daughters get distinct ids, lineage links parent.

**Verification:** `TrackCells` behavior unchanged; the helper is importable by `phases.py` without pulling Qt/Session.

---

- U3. **Tracking phase in the workflow generator**

**Goal:** Add a tracking phase after seg-QC that, for datasets with `n_timepoints > 1`, runs `phases.track_one` and sets the dataset's effective segmentation name to `<seg>_tracked` so all downstream phases use it.

**Requirements:** R1, R2

**Dependencies:** U1, U2, U6

**Files:**
- Modify: `src/percell4/workflows/phases.py` (new `track_one(store, raw_seg_name, ...) -> (tracked_seg_name, failure, msg)` using `LaptrackTracker` + `build_tracked_result` + `store.write_labels`/`store.write_tracks`)
- Modify: `src/percell4/gui/workflows/single_cell/runner.py` (`_phase_generator` yields an UNATTENDED `track` request per time-lapse dataset after seg_qc; `_make_track_handler` sets `self._effective_seg[name] = tracked_name` on success)
- Test: `tests/test_workflows/test_phases_track.py` (new), `tests/test_workflows/test_runner_tracking_phase.py` (new, headless `interactive_qc=False`)

**Approach:**
- `track_one` mirrors `TrackCells` but on `DatasetStore`: read `read_labels(raw_seg)` `(T,H,W)`, `LaptrackTracker().track(...)`, `build_tracked_result`, `store.write_labels(<seg>_tracked, ...)`, `store.write_tracks(<seg>_tracked, lineage)`. Returns the tracked name.
- Generator: after the seg/seg_qc loop, iterate `datasets_without_failures`; for each with `store.metadata["n_timepoints"] > 1` **and no existing `*_tracked` segmentation** (per U13), yield a `track` `PhaseRequest`. The handler records the effective name. Single-timepoint datasets, and datasets already tracked (U13), are skipped entirely (no tracking phase).
- Failures here are recorded as `DatasetFailure` (the dataset drops out of downstream phases via `datasets_without_failures`, same as other phases).

**Patterns to follow:** existing UNATTENDED phase handler factories; `datasets_without_failures`; `TrackCells.execute`.

**Test scenarios:**
- Covers R1. Happy path: a 3-timepoint dataset → `track` phase runs, `/labels/<seg>_tracked` and `/tracks/<seg>_tracked` exist, effective seg name updated.
- Covers R2. Integration: after tracking, a subsequent phase handler resolves the effective name to `<seg>_tracked`.
- Edge case: single-timepoint dataset → no `track` phase yielded; effective name stays the config default.
- Error path: tracking failure records a `DatasetFailure` and the dataset is excluded from downstream phases (no crash).

**Verification:** Time-lapse datasets are tracked between seg-QC and thresholding; downstream uses the tracked segmentation; single-timepoint runs unaffected.

---

- U4. **Per-timepoint thresholding phases**

**Goal:** `threshold_compute_one` and `apply_threshold_headless` process each timepoint independently for time-lapse datasets (per-frame grouping + per-frame mask), writing `(T,H,W)` mask resources; single-timepoint unchanged.

**Requirements:** R4

**Dependencies:** U3

**Files:**
- Modify: `src/percell4/workflows/phases.py` (`threshold_compute_one`, `apply_threshold_headless`, and the grouping cache shape — keyed per `(dataset, round)` today; may need per-`(dataset, round, timepoint)`)
- Test: `tests/test_workflows/test_phases_threshold_timelapse.py` (new)

**Approach:**
- When `n_timepoints > 1`, loop timepoints: read `read_labels(seg, timepoint=t)` and the seg channel frame via the U2 per-frame reader (not `store.read_channel`, which is 4D-unsafe), compute grouping per frame, write the per-frame mask into a `(T,H,W)` `/masks/<round>` resource (store accepts `(T,H,W)` masks). Cache grouping per `(dataset, round, t)` (the grouping cache key gains a timepoint dimension for time-lapse).
- Reuse the existing single-frame grouping/threshold logic per frame (no new clustering algorithm).

**Patterns to follow:** existing `threshold_compute_one`/`apply_threshold_headless`; `MeasureCells._measure_timelapse` looping shape; store `(T,H,W)` mask writes.

**Test scenarios:**
- Covers R4. Happy path: a `(T,H,W)` tracked seg → per-frame grouping; `/masks/<round>` is `(T,H,W)` with each frame independently thresholded.
- Edge case (backward compat): single-timepoint dataset → 2D mask, grouping cache keyed as today.
- Edge case: a frame with no cells yields an all-zero mask frame, no crash.

**Verification:** Per-frame masks produced for time-lapse; single-timepoint thresholding unchanged.

---

- U5. **Per-timepoint measurement phase + particle analysis**

**Goal:** `phases.measure_one` (and `measure_particles_one`) produce per-`(timepoint, cell)` rows for time-lapse datasets, tagging `timepoint` and joining `track_id`/`tree_id`/`parent_track_id` from `/tracks` for the tracked segmentation; single-timepoint unchanged.

**Requirements:** R2, R4

**Dependencies:** U3, U4

**Files:**
- Modify: `src/percell4/workflows/phases.py` (`measure_one`, `measure_particles_one`, `write_staging_parquet` columns)
- Test: `tests/test_workflows/test_phases_measure_timelapse.py` (new)

**Approach:**
- Per-timepoint-ize the **whole** `measure_one` body, not just the measurement call: `phases.measure_one` uses `measure_multichannel_with_masks` plus round-mask loading, `group_<round>` merges, `_append_synthetic_row` (edge cohort), and `_add_area_um2_columns` — all of that must run per frame and concatenate. The round masks it reads are now `(T,H,W)` (from U4) and must be sliced per timepoint; the channel read uses the U2 per-frame reader. So this unit is **at least as large as U4**, not a thin wrapper — `MeasureCells._measure_timelapse` gives the loop/`_join_lineage` shape but uses a different measurement engine (`measure_multichannel`), so copy the pattern, not the code.
- For the tracked seg set `track_id = label` and join `tree_id`/`parent_track_id` from `store.read_tracks(seg)` (mirror `MeasureCells._join_lineage`). Concatenate per-frame frames into the staging DataFrame with a `timepoint` column.
- Particle analysis (`measure_particles_one`) loops timepoints likewise (per-frame particle detail with `timepoint`).

**Patterns to follow:** `application/use_cases/measure_cells.py::_measure_timelapse`, `_join_lineage`; existing `measure_one` column-merge + `_um2` siblings.

**Test scenarios:**
- Covers R2/R4. Happy path: tracked `(T,H,W)` seg → staging rows for every `(timepoint, cell)` with `timepoint`/`track_id`/`tree_id`/`parent_track_id` columns.
- Edge case (backward compat): single-timepoint dataset → no `timepoint`/track columns, output as today.
- Edge case: a track absent in some frames contributes no rows there (counts differ per timepoint).
- Integration: particle per-cell columns merge correctly per frame.

**Verification:** Time-lapse measurement staging carries timepoint + lineage columns; single-timepoint staging unchanged.

---

### Phase 2 — Complete-tracks reporting

- U7. **Complete-tracks selection (domain) + `complete_tracks.csv` artifact**

**Goal:** Add a pure `select_complete_tracks` helper and write `complete_tracks.csv` (long format) in `export_run` for tracked datasets, atomically, to the run folder.

**Requirements:** R5, R9

**Dependencies:** U5

**Files:**
- Modify: `src/percell4/domain/tracking/lineage.py` (`select_complete_tracks(measurements_df, lineage_df, n_timepoints) -> pd.DataFrame`)
- Modify: `src/percell4/workflows/phases.py` (`export_run` writes `complete_tracks.csv` via `write_atomic`, gated on presence of tracked datasets/`track_id` columns)
- Test: `tests/test_domain/test_complete_tracks.py` (new), `tests/test_workflows/test_export_complete_tracks.py` (new)

**Approach:**
- `select_complete_tracks` keeps `track_id`s where `begin_t == 0`, `end_t == n_timepoints - 1`, a row exists at every timepoint (no gaps), `parent_track_id == NO_PARENT`, and the id is never a division parent (not present in any track's `parent_track_id`). Returns the long-format measurement subset for those tracks.
- `export_run` computes per-dataset `n_timepoints` (from `/metadata`) and the lineage (`/tracks`), runs the filter on the staged measurements, and writes one combined `complete_tracks.csv` (with a `dataset` column) plus optional `per_dataset/<name>_complete_tracks.csv`. Datasets with no tracks contribute nothing; a run with no tracked datasets writes no file (or an empty file — decide and assert).

**Patterns to follow:** `_build_summary_groups`/`_build_summary_datasets` + `write_atomic` in `export_run`; `domain/tracking/lineage.py` pure helpers.

**Test scenarios:**
- Covers R5. Happy path: tracks spanning all timepoints with no division → included; a late-born daughter, a dying track, a division parent, and a gap-bridged track → all excluded.
- Edge case: a dataset with zero complete tracks contributes no rows.
- Edge case: single-timepoint / untracked run → no `complete_tracks.csv` (or empty), no crash.
- Integration (R9): the CSV lands in the run folder via `write_atomic` (tmp + replace); an interrupted write leaves no partial file.

**Verification:** `complete_tracks.csv` contains exactly the full-span, gap-free, non-dividing tracks in long format; written atomically to the run folder.

---

### Phase 3 — Interactive QC for time-lapse (Feature 1 interactive path)

- U8. **Time-lapse segmentation QC**

**Goal:** `SegmentationQCController` reviews and edits a `(T,H,W)` raw segmentation across the napari timepoint slider, with frame-scoped edits, persisting the edited `(T,H,W)` stack on accept.

**Requirements:** R3 (QC of the segmented stack)

**Dependencies:** U2

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/seg_qc.py`
- Test: `tests/test_gui_workflows/test_seg_qc_timelapse.py` (new)

**Approach:**
- Note this is a **structural conversion**, not just a display tweak: `SegmentationQCController` has its own load/edit/persist path that is single-frame today (`seg_qc.py` reads via `read_channel` — 4D-unsafe — and reads/writes a flat 2D labels array), and it has no timepoint concept. The work is: read the channel via the U2 per-frame reader, display the `(T,H,W)` labels layer with the napari dims slider, make edits **frame-scoped** (the same approach added to `gui/segmentation_panel.py`, but `SegmentationQCController` does not currently share that code — it needs the equivalent logic), and persist the full `(T,H,W)` stack to `/labels/<seg>` on accept.
- Single-timepoint datasets behave exactly as today.

**Execution note:** Highest-UX-risk unit and a non-trivial controller rework. Keep edits frame-scoped and the accept path writing the whole stack; if per-frame review of long movies is impractical, the "representative-subset" refinement is deferred (see Scope Boundaries).

**Patterns to follow:** the frame-scoped delete/relabel/cleanup in `src/percell4/gui/segmentation_panel.py`; existing `SegmentationQCController` accept/cancel/visibility-restore flow.

**Test scenarios:**
- Happy path: editing a label at one timepoint and accepting persists a `(T,H,W)` stack with only that frame changed.
- Edge case (backward compat): single-timepoint QC accept persists a 2D label as today.
- Integration: accept → `/labels/<seg>` holds the edited stack that the tracking phase then consumes.

**Verification:** Time-lapse seg-QC reviews/edits the stack per frame and persists it; single-timepoint QC unchanged.

---

- U9. **Time-lapse threshold-QC and dilute-phase mask**

**Goal:** `ThresholdQCQueueEntry`/`ThresholdQCController` and `DilutePhaseQueueEntry` review per-frame thresholds/masks across the slider for time-lapse datasets, writing `(T,H,W)` masks.

**Requirements:** R4 (interactive per-frame review)

**Dependencies:** U4

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/threshold_qc_queue.py`, `src/percell4/gui/workflows/single_cell/dilute_queue.py` (and the underlying `gui/threshold_qc.py` / dilute controller as needed)
- Test: `tests/test_gui_workflows/test_threshold_qc_timelapse.py` (new)

**Approach:**
- Like U8, this is a structural conversion of single-frame, 4D-unsafe controllers (`threshold_qc_queue.py`/`gui/threshold_qc.py` and `dilute_queue.py` read via `read_channel`): wire the dims slider, read channel frames via the U2 per-frame reader, present the per-frame grouping/threshold result across the slider, and accept-write the `(T,H,W)` mask. Reuse the per-frame grouping from U4. Dilute mask likewise per frame.
- Single-timepoint behavior unchanged.

**Execution note:** High-UX-risk and a controller rework like U8; per-frame review of long movies may warrant the deferred "representative subset" affordance.

**Patterns to follow:** existing `ThresholdQCController` (`write_measurements_to_store=False`), dilute controller; U4 per-frame grouping; `(T,H,W)` mask writes.

**Test scenarios:**
- Happy path: accepting a per-frame threshold writes a `(T,H,W)` mask whose frames match the previewed per-frame grouping.
- Edge case (backward compat): single-timepoint threshold-QC writes a 2D mask as today.
- Integration: the accepted `(T,H,W)` mask is what `measure_one` reads per frame.

**Verification:** Interactive per-frame threshold/dilute QC works for time-lapse and writes `(T,H,W)` masks; single-timepoint unchanged.

---

### Phase 4 — Headless batch CLI + resume entry (Feature 2)

- U10. **Batch compress+segment+track use case (headless)**

**Goal:** A Qt-free use case that, per dataset, compresses (TIFF → HDF5), segments all timepoints, and tracks when `n_timepoints > 1`, with a progress callback and a per-dataset result report.

**Requirements:** R6

**Dependencies:** U6

**Files:**
- Create: `src/percell4/application/use_cases/batch_process_datasets.py` (`batch_process_datasets(specs, output_dir, cellpose_settings, seg_channel, track=True, progress_callback=None) -> BatchProcessReport`)
- Test: `tests/test_application/test_batch_process_datasets.py` (new)

**Approach:**
- Composition: `Hdf5DatasetRepository` + `NullViewerAdapter` + `Session` (like `run_pipeline.py`). Per dataset: `import_dataset(...)` → `LoadDataset` → `SegmentCells(...).run_inference_stack(...).finalize(...)` (or single-frame `run_inference` for `n_timepoints == 1`) → if `n_timepoints > 1` and `track`, `TrackCells(...).execute(seg)`. Collect per-dataset success/failure/messages into a report (mirror `batch_export_images`'s report object). Never raise on a single dataset failure — record and continue (overnight robustness).
- Discover datasets at call time from explicit specs (don't `rglob` at consumption — per batch-compress learning).

**Patterns to follow:** `interfaces/cli/run_pipeline.py` composition root (which **already calls `SegmentCells.finalize` headlessly** — `gui._bin_suffix` is pure Python with a lazy import, so the headless path is proven, no factorization needed); `application/use_cases/batch_export_images.py` report/iteration shape; `adapters/importer.py::import_dataset`.

**Test scenarios:**
- Covers R6. Happy path: two synthetic multi-timepoint TIFF folders → each imported, segmented `(T,H,W)`, tracked; report lists both as succeeded with track counts.
- Edge case: a single-timepoint dataset → segmented 2D, NOT tracked; reported succeeded.
- Error path: one dataset with unreadable/empty input fails, is recorded, and the batch continues to the next (no crash, non-zero failure count).
- Edge case: `track=False` skips tracking even for multi-timepoint datasets.

**Verification:** The use case processes a batch headlessly to tracked HDF5 datasets, robust to per-dataset failures.

---

- U11. **`batch_process` CLI + console_script**

**Goal:** An argparse CLI front-end (`python -m percell4.interfaces.cli.batch_process`) over U10, with headless stdout progress and exit codes, plus a `percell4-batch` console_script for overnight invocation.

**Requirements:** R6

**Dependencies:** U10

**Files:**
- Create: `src/percell4/interfaces/cli/batch_process.py`
- Modify: `pyproject.toml` (`[project.scripts] percell4-batch = "percell4.interfaces.cli.batch_process:main"`)
- Test: `tests/test_application/test_batch_process_cli.py` (new — `main([...])` exit codes + arg parsing)

**Approach:**
- Mirror `batch_export.py`: `_resolve_inputs(args)` expands a source root / explicit dataset specs; flags for `--output-dir`, `--seg-channel`, Cellpose model/diameter/`--gpu`, `--no-track`, `--quiet`; per-item stdout status via the progress callback; totals line; exit code 0 if ≥1 dataset succeeded, 1 otherwise. `main(argv=None) -> int` for programmatic/test use.

**Patterns to follow:** `interfaces/cli/batch_export.py` (`main`, `_resolve_paths`, argparse, exit codes, `percell4._compat` import).

**Test scenarios:**
- Covers R6. Happy path: `main(["<src>", "--output-dir", "<out>", "--seg-channel", "mNG"])` processes datasets and returns 0.
- Edge case: empty/no-match input → prints a clear message, returns 1.
- Edge case: `--no-track` forwarded to the use case (no tracking performed).
- Error path: invalid args (missing `--output-dir`) → argparse error, non-zero exit.

**Verification:** The CLI runs compress+segment+track headlessly and is invocable as `percell4-batch`; exit codes reflect success/failure.

---

- U12. **Resume-from-segmented workflow entry point + per-dataset segmentation picker**

**Goal:** A workflow entry that builds a `WorkflowConfig` of `H5_EXISTING` datasets, skips compress/segment/seg_qc/track, and lets the user pick (per dataset) which `/labels/` segmentation to use — defaulting to the tracked layer — wired into the per-dataset effective seg name.

**Requirements:** R7, R8

**Dependencies:** U1 (effective seg name), U13 (auto-skip + default-segmentation picking it reuses), U4, U5 (downstream consumes it); independent of U2/U3

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py` (a "resume from segmented datasets" mode with a per-dataset segmentation picker column listing each dataset's `/labels/` resources, default = tracked when present) — **T1 (`*Dialog.py`)**
- Modify: `src/percell4/gui/workflows/single_cell/runner.py` (`_phase_generator` honors a `resume_segmented` flag: skip compress, segment, seg_qc, and track; seed `self._effective_seg` from the per-dataset picks)
- Modify: `src/percell4/interfaces/gui/main_window.py` (launcher entry to open the resume dialog)
- Test: `tests/test_workflows/test_runner_resume_segmented.py` (new), `tests/test_gui/test_resume_config_dialog.py` (new)

**Approach:**
- Picker lists `/labels/` only (never masks — per the misclassification learning), defaulting each dataset to its tracked layer (`<seg>_tracked` if present, else the lone segmentation; flag ambiguous untracked datasets). The chosen names seed `self._effective_seg[name]`.
- Runner flag `resume_segmented=True` makes `_phase_generator` start at the thresholding rounds (skipping compress/segment/seg_qc/track). All entries are `H5_EXISTING` (compress already a no-op for those). This is effectively the all-datasets case of the U13 auto-skip, with the picker providing explicit per-dataset override/disambiguation instead of the automatic default.
- The picker is a **Selector** in classification terms (writes the per-dataset config choice; creates no resource).

**Execution note:** T1 dialog edit — run `scripts/learnings_applicability.py` on `config_dialog.py` and consult the matrix before editing.

**Patterns to follow:** `WorkflowConfigDialog` group-builders; `DatasetSource.H5_EXISTING` handling; the per-dataset effective seg name from U1.

**Test scenarios:**
- Covers R7. Happy path (headless runner): `resume_segmented=True` with `H5_EXISTING` datasets → generator yields no compress/segment/seg_qc/track requests, starts at threshold_compute.
- Covers R8. Happy path: picker defaults each dataset to its tracked layer; the resolved effective seg name for each dataset is the picked name.
- Edge case: a dataset with only a raw (untracked) segmentation defaults to it; a dataset with multiple untracked segmentations and no tracked one is flagged for explicit selection.
- Edge case: a single-timepoint pre-segmented dataset resumes and measures with no timepoint columns.
- Integration: downstream phases (U4/U5) read the per-dataset picked segmentation.

**Verification:** The resume entry processes pre-segmented datasets from thresholding onward, using the per-dataset chosen segmentation (tracked by default).

---

## System-Wide Impact

- **Interaction graph:** new `track` phase in the generator between seg_qc and the threshold rounds; the runner-owned effective-seg map feeds every downstream handler; `export_run` gains a `complete_tracks.csv` writer. The resume entry adds a launcher menu item + dialog mode and a `resume_segmented` generator branch.
- **Error propagation:** tracking/segment/threshold/measure failures record `DatasetFailure` and drop the dataset from later phases via `datasets_without_failures` (existing mechanism); the CLI use case records per-dataset failures and continues.
- **State lifecycle risks:** the effective-seg map must be seeded/reset per run (not leak across runs); per-frame mask/label writes must validate `(T,H,W)` against `n_timepoints` (store already enforces this). Complete-tracks CSV and staging parquet are run-folder artifacts (provenance invariant) written atomically.
- **API surface parity:** `phases.measure_one`/`threshold_*`/`segment_one` gain per-timepoint behavior consistent with the use-case layer's `MeasureCells._measure_timelapse`; both layers must agree on the timepoint/track column names. `read_labels(seg, timepoint=t)` and `(T,H,W)` mask writes are the shared store contract.
- **Integration coverage:** the end-to-end time-lapse path (segment-all → seg-QC → track → per-frame threshold → per-frame measure → complete_tracks.csv) and the CLI→resume path (CLI segments+tracks → resume picks tracked → thresholds/measures) are the cross-layer flows unit tests alone won't fully prove — include the headless runner integration tests (`interactive_qc=False`) in U3/U5/U12 and a real `python -m percell4.interfaces.cli.batch_process` smoke run on a `_tN` sample.
- **Unchanged invariants:** single-timepoint datasets flow through the workflow exactly as today (no tracking phase, no per-timepoint loops, no complete-tracks CSV); the `.h5` provenance rule (no measurements written back into datasets) is preserved; `WorkflowConfig` stays frozen.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `store.read_channel` raises on the 4D `(T,C,H,W)` multichannel time-lapse layout and has no `timepoint=` — every per-timepoint phase/QC read breaks (U2/U4/U5/U8/U9) | Add a `phases`-level per-frame channel reader on `read_array_frame` + `split_channels_2d` in U2; reuse it everywhere; never call `read_channel` on a time-lapse dataset (Key Technical Decisions) |
| Per-timepoint adaptation of `phases.measure_one`/`threshold_*` drifts from the use-case `MeasureCells` time-lapse logic; `measure_one` uses a *different* measurement engine so it is a larger lift than a mirror | Copy the loop/`_join_lineage` *pattern* (not code); per-timepoint-ize the full `measure_one` body incl. round masks/`group_` merges/synthetic rows/`_um2`; assert identical timepoint/track column names |
| Interactive QC controllers (U8/U9) are single-frame, 4D-unsafe, and don't share the panel's frame-scoping — structural rework, not a display tweak | Treat U8/U9 as controller reworks (per-frame read + dims slider + frame-scoped edits + `(T,H,W)` persist); land last; defer "representative-subset QC" (Scope Boundaries) |
| Per-frame grouped thresholding is noisy or slow on many frames | Re-cluster per frame by default; allow revisiting (Deferred to Implementation); failures recorded per dataset, not fatal |
| Tracking-phase + effective-seg map adds a stateful seam to the runner | Seed/reset the map per run; cover with headless runner tests; keep the map runner-owned, `WorkflowConfig` frozen |
| U6 shared helper in `domain/tracking/` could pull `laptrack`/`h5py` into `domain` and break the one passing import-linter contract | `build_tracked_result` takes a `TrackingResult` + numpy stack only; no laptrack/h5py imports in `domain` |
| Large scope across two features | Phased delivery: Phase 1+2 (+ Phase 4 CLI) deliver the headless/overnight path and reporting; Phase 3 (interactive time-lapse QC) can ship after |

---

## Phased Delivery

- **Phase 1 (U1, U2, U13, U6, U3, U4, U5)** — time-lapse-aware workflow foundation: per-dataset effective seg name, segment-all-timepoints, auto-skip Cellpose for pre-segmented datasets, shared tracking helper, the tracking phase, and per-frame thresholding/measurement. Delivers R1–R4 and R10 for the headless (`interactive_qc=False`) path.
- **Phase 2 (U7)** — complete-tracks CSV (R5, R9).
- **Phase 4** — two parallel efforts converging on the overnight→resume path: the headless batch CLI (U10 → U11, depends on U6) and the resume entry point (U12, depends on U1/U13/U4/U5). (R6–R8.)
- **Phase 3 (U8, U9)** — interactive time-lapse QC. Highest UX risk; can land last since the CLI+resume path lets users avoid in-workflow time-lapse segmentation/QC entirely.

---

## Documentation / Operational Notes

- Update `README.md`: document the `percell4-batch` CLI (overnight compress+segment+track) and the resume-from-segmented workflow entry; note `complete_tracks.csv` in the run-folder outputs.
- Update per-module `CLAUDE.md` (workflows, interfaces/cli) to current state; update `docs/audits/gui-element-classification.yaml` for the resume picker (Selector) and any new buttons.
- After landing, run `/ce-compound` to capture the laptrack tracking adapter (currently undocumented in `docs/solutions/`) and the workflow resume / per-dataset-segmentation model.
- Operational: the CLI is the overnight path — ensure it logs per-dataset progress to stdout and writes outputs atomically so an interrupted run is resumable by re-running (already-imported datasets can be skipped or overwritten — decide in U10).

---

## Sources & References

- Repo research: workflow generator `gui/workflows/single_cell/runner.py`; phase helpers `workflows/phases.py`; models `workflows/models.py`; base runner `gui/workflows/base_runner.py`; QC controllers `seg_qc.py`/`threshold_qc_queue.py`/`dilute_queue.py`; tracking `application/use_cases/track_cells.py`, `adapters/laptrack_tracker.py`, `domain/tracking/{lineage,relabel}.py`; time-lapse measurement `application/use_cases/measure_cells.py`; store `store.py`; CLIs `interfaces/cli/{run_pipeline,batch_export}.py`; importer `adapters/importer.py`; entry points `pyproject.toml`.
- Institutional learnings: `creator-contract-four-step-sequence-2026-05-18.md`, `threshold-qc-measurements-write-owned-by-controller.md`, `atomic-write-contract.md`, `batch-compress-development-lessons.md`, `decay-write-path.md`, `session-to-napari-one-way-push.md`, `consolidate-canonical-state-over-per-module-overrides-2026-05-14.md`, `napari-mask-layer-misclassified-as-segmentation.md`.
- Related plans: `docs/plans/2026-05-21-003-feat-time-lapse-tracking-lineage-plan.md` (tracking foundation; deferred batch/CLI), `docs/plans/2026-05-20-001-feat-end-to-end-single-cell-workflow-plan.md`, `docs/plans/2026-04-10-feat-single-cell-thresholding-workflow-plan.md`.
