---
title: "feat: End-to-end single-cell workflow (edge cohort + dilute in batch)"
type: feat
status: active
date: 2026-05-20
origin: docs/brainstorms/2026-05-20-end-to-end-single-cell-workflow-requirements.md
---

# feat: End-to-end single-cell workflow (edge cohort + dilute in batch)

## Overview

Extend the existing single-cell thresholding workflow (`src/percell4/gui/workflows/single_cell/`) in place to (1) replace the always-on edge-cell removal invariant with a config-level three-mode choice, (2) add a measure-time synthetic edge-cohort row when the size-normalized mode is selected, (3) insert a new INTERACTIVE Phase 5 between grouped thresholding and measurement that runs the existing single-dataset dilute UI as a per-dataset adaptive-round queue, and (4) produce two new summary CSVs in the run folder alongside the existing parquet and CSV outputs. The existing pipeline spine and `run_config.json` Resume contract are preserved; new fields are added with safe defaults so old run folders still load.

---

## Problem Frame

PerCell4's "Single-cell thresholding analysis workflow" is the canonical batch path for cross-dataset single-cell analysis, but it hardcodes two decisions the user now wants to control: edge cells are always discarded, and dilute-phase mask generation is only available as a separate single-dataset workflow. The "end-to-end" framing in the origin doc is product polish for the same workflow — modify in place, not v2. See origin: `docs/brainstorms/2026-05-20-end-to-end-single-cell-workflow-requirements.md`.

---

## Requirements Trace

- R1. Replace the existing SCTW entry in place — no v2, no parallel workflow.
- R2. Preserve existing pipeline phases; new dilute phase between grouped thresholding and measurement; new edge-cohort synthetic row computed during measurement.
- R3, R3a. Config dialog gains edge-mode (default `exclude`) and optional dilute sub-panel; all other sections preserved.
- R4. `exclude` mode keeps today's `filter_edge_cells` behavior in Phase 1.
- R5. `include_as_normal` mode skips the filter; edge cells appear as ordinary rows with `is_edge=True`.
- R6. `include_as_size_normalized_cohort` mode skips the filter, edge cells participate normally in clustering/thresholding/measurement, and one synthetic row per dataset is appended at measurement time.
- R7. Synthetic row formula: `N_theoretical = sum(edge_areas) / mean(whole_areas)`; for each metric M, `synthetic_M = sum(M across edge cells) / N_theoretical`; `N_theoretical` is a float (no rounding).
- R8. Synthetic row carries `cell_id=-1`, `is_edge_synthetic=True`, `is_edge=False`, `group_<round>=NaN` for every round.
- R9. Every per-cell row carries `is_edge` boolean (always present); `is_edge_synthetic` is also always present.
- R10. Edge cases: zero edge cells → no synthetic row; zero whole cells → no synthetic row + `DatasetFailure` recorded; `exclude` / `include_as_normal` modes emit no synthetic row.
- R11. Optional dilute generation toggle in config dialog with locked settings post-Start.
- R12. New Phase 5 — per-dataset interactive queue, adaptive round count, reuses existing single-dataset dilute UI as inner loop.
- R13. Accepted dilute mask written to `/masks/<dilute_name>` per dataset; picked up by Phase 7 measurement like any other mask.
- R14. Dilute name uniqueness validated against threshold-round names at Start time.
- R15. Dilute phase runs after all grouped thresholding rounds, before measurement.
- R16. No change to grouped thresholding round structure (config-time, fixed, ordered).
- R17. Existing exports preserved; `is_edge` / `is_edge_synthetic` columns always present.
- R18. New `summary_groups.csv` written to run folder (per dataset×round×group).
- R19. New `summary_datasets.csv` written to run folder (per dataset).

**Origin actors:** A1 (Researcher)
**Origin flows:** F1 (Configure and start an end-to-end run), F2 (Per-dataset adaptive dilute round loop), F3 (Edge-cohort synthetic row at measurement)
**Origin acceptance examples:** AE1 (synthetic row numeric example, covers R7+R8), AE2 (zero whole cells, covers R10), AE3 (adaptive per-dataset round counts, covers R12), AE4 (dilute name conflict, covers R14)

---

## Scope Boundaries

- No new HTML or PDF report — outputs stay parquet + CSV.
- No mid-run grouped thresholding additions — round list is fixed at config time.
- No parallel "v2" workflow — modify SCTW in place.
- No standalone batch dilute workflow — dilute is *inside* this workflow.
- No edge-cell highlighting in the segmentation QC UI — deferred to a future round.
- No automatic dilute convergence detection — round count is user-driven.
- No per-group edge-cohort synthetic rows — exactly one synthetic row per dataset.
- No new summary CSVs beyond `summary_groups.csv` and `summary_datasets.csv`.

### Deferred to Follow-Up Work

- Migrating `ThresholdQCController.write_measurements_to_store` from boolean-shim to callback-based DataFrame return (per `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`). This area is adjacent but not blocking; revisit in a separate PR.
- Adding an entry to `docs/audits/canonical-sources-matrix.yaml` pinning `workflows/models.py::WorkflowConfig` as the canonical recipe-evolution surface once this plan lands.

---

## Context & Research

### Relevant Code and Patterns

**Workflow config + runner**
- `src/percell4/workflows/models.py` — frozen `WorkflowConfig`, nested frozen dataclasses (`CellposeSettings`, `ThresholdingRound`), StrEnums (`ThresholdAlgorithm`, `GmmCriterion`, `DatasetSource`). New `EdgeMode(StrEnum)` and `DiluteSettings` frozen dataclass follow the same shape. Cross-field validation in `WorkflowConfig.__post_init__`.
- `src/percell4/workflows/artifacts.py` — hand-rolled `_X_to_dict` / `_X_from_dict` helpers (avoids `dataclasses.asdict` flattening); `config_to_dict` / `config_from_dict` at lines 201-224. `write_run_config` wrapped through `write_atomic` (tmp + fsync + `os.replace`).
- `src/percell4/gui/workflows/base_runner.py` — generator-driven state machine. `PhaseRequest(kind=UNATTENDED|INTERACTIVE, ..., handler)`. INTERACTIVE handler registers `on_complete(PhaseResult)` against a controller signal and returns; control yields back to Qt's event loop. `_finish` is the single idempotent exit point. Cancellation is cooperative, checked at dataset boundaries.
- `src/percell4/gui/workflows/single_cell/runner.py` — `_phase_generator` template; `_make_<phase>_handler` closure pattern; `_active_qc_controller` strong-ref slot to defeat Qt GC; `_wrapped_complete` adapter translates controller domain result → `PhaseResult` and sniffs `"cancel"` in message to propagate runner-level cancel; `interactive_qc=False` headless fallback (lines 80-96, 575-621).
- `src/percell4/gui/workflows/single_cell/seg_qc.py`, `src/percell4/gui/workflows/single_cell/threshold_qc_queue.py` — concrete templates for queue-style INTERACTIVE phases. `ThresholdQCQueueEntry` is the closest analogue to what the new dilute queue wrapper will be.

**Edge-cell filtering**
- `src/percell4/domain/segmentation/postprocess.py` — `filter_edge_cells(labels, edge_margin=0)` at lines 14-55. Pure numpy: collects labels in border rows/columns, zeros them out, returns `(filtered_labels, count_removed)`. The same border-collection algorithm is what U2 reuses for the measure-time `is_edge` helper.
- `src/percell4/workflows/phases.py:197` — the single workflow-Phase-1 call site (`# Postprocess: edge removal is always on per workflow invariant`). Other callers (`seg_qc.py`, `gui/segmentation_panel.py`, `application/use_cases/segment_cells.py`) are not workflow-invariant and stay unchanged.

**Measurement pipeline**
- `src/percell4/domain/measure/measurer.py` — `measure_multichannel_with_masks` (line 457) used by Phase 7 via `phases.measure_one`. `CORE_COLUMNS` includes `area` (lines 23-26), which is sourced from `regionprops`. `area` is always present in per-cell output regardless of which metric names are passed via `metrics=`.
- `src/percell4/workflows/phases.py:412-524` — `measure_one`. Build `df = measure_multichannel_with_masks(...)` at line 496, then merge `group_<round_name>` columns at lines 514-522. The U4 post-process inserts between these two steps.
- `src/percell4/domain/measure/metrics.py:113-123` — `BUILTIN_METRICS` registry. `area` is one of them; the synthetic row computation is metric-agnostic (applies `sum/N_theoretical` to every metric column uniformly).

**Run-folder export**
- `src/percell4/workflows/phases.py:549-687` — `export_run`. Reads `staging/*.parquet` via `pyarrow.dataset`, writes `measurements.parquet`, `combined.csv`, `per_dataset/<DS>.csv`. New summary CSVs (U6) hook in after the parquet write and before staging cleanup, using the same `df.to_csv(...)` kwargs and `write_atomic`.

**Single-dataset dilute UI**
- `src/percell4/gui/workflows/dilute_phase/controller.py` — `DilutePhaseMaskController(QObject)`. Owns the in-memory working buffer, cumulative condensed union, round counter, locked config. Public API: `start_round()`, `finish()`, `cancel()`. Signals: `round_complete(int)`, `workflow_done`, `workflow_cancelled`, `error(str)`. **This is the inner loop that U5 reuses.**
- `src/percell4/gui/workflows/dilute_phase/panel.py` — `DilutePhaseMaskPanel(QWidget)`. The outer-loop chrome for the standalone single-dataset workflow. U5 does **not** reuse this panel — the runner queue is the outer loop in batch mode.
- `src/percell4/gui/_grouped_threshold_settings.py` — `GroupedThresholdSettingsWidget`. Reusable embedded widget for the dilute sub-panel in U3.
- `src/percell4/application/use_cases/accept_dilute_mask.py` — persists the final mask. **Watch for the Creator four-step contract** (see Institutional Learnings).

**Tests**
- `tests/test_gui_workflows/test_single_cell_runner.py` — `interactive_qc=False` headless tests; `_make_dataset()` h5 fixture; patches both `phases.segment_one` and `runner_mod.segment_one` (the runner imports by name).
- `tests/test_gui_workflows/test_interactive_runner.py` — `_FakeSegQCController` and `_FakeThresholdQCQueueEntry` patterns (auto-complete via `on_complete(PhaseResult(success=True))`, record kwargs in class-level `instances` list, monkeypatched into the runner module). `qtbot.waitUntil(...)` pump pattern.
- `tests/test_workflows/test_phases.py`, `tests/test_workflows/test_measurer_with_masks.py` — synthetic label-array fixtures for Qt-free pipeline tests.
- `tests/test_workflows/test_models.py`, `tests/test_workflows/test_artifacts.py` — frozen-dataclass validation and round-trip serialization patterns.

### Institutional Learnings

- **Creator contract four-step sequence** (`docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md`) — every mask Creator must execute `store.write_mask` → `viewer_win.add_mask` → `session.refresh_resource_lists` → `session.set_active_mask`. Two recent silent prod bugs. **Batch-mode carve-out for Phase 5 dilute writes:** steps 1 + 2 fire per-dataset; steps 3 + 4 are intentionally skipped via `DilutePhaseMaskController.finish(session_free=True)` because the launcher session is either unbound (silent no-op) or bound to a different dataset (writes wrong dataset's mask list into launcher metadata). The single-dataset workflow (`DilutePhaseMaskPanel`) continues to use `session_free=False` (default) so its Creator contract stays whole. U5 includes a session-isolation regression test pinning `session.refresh_resource_lists.call_count == 0` and `session.set_active_mask.call_count == 0` across a batch run.
- **Atomic write contract** (`docs/solutions/architecture-patterns/atomic-write-contract.md`) — all whole-file writes go through `percell4.workflows.artifacts.write_atomic`. U6 (two new CSVs) and U1 (evolved `run_config.json`) both use it. No `open(path, "w")` direct calls; no platform branching.
- **Sibling-dialog drift** (`docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`) — when two surfaces present the same controls, extract the shared widget. U3 reuses `GroupedThresholdSettingsWidget` (already extracted). U5's queue wrapper deliberately does NOT replicate `DilutePhaseMaskPanel`'s chrome; if shared UI bits are needed, extract a sub-widget rather than copy.
- **Qt user-edit signal wiring** (`docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`) — every new interactive widget in `config_dialog.py` (U3) must wire its user-edit signal at construction time. Programmatic-set tests pass even when no signal is wired; only qtbot-driven user-path tests catch the gap.
- **Dialog scroll wrapping** (`docs/solutions/ui-bugs/dialog-scroll-when-tall.md`) — `tests/test_gui/test_dialog_helper_compliance.py` AST-walks every `gui/**/*Dialog.py` and fails CI if a tall dialog isn't either wrapped in `wrap_in_scroll(...)` or explicitly exempt. U3 verifies the wrapper still applies after adding sections.
- **Consolidate canonical state, no per-module overrides** (`docs/solutions/architecture-patterns/consolidate-canonical-state-over-per-module-overrides-2026-05-14.md`) — `edge_mode` is workflow-only; do NOT add a panel-local `edge_mode_combo` in `grouped_seg_panel.py`. The override-pattern is explicitly retracted.
- **Decouple via callback injection** (`docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md`) — U5's dilute queue wrapper accesses viewer/store/session via injected accessors from the host, not by reaching into the launcher.
- **`ThresholdQCController.write_measurements_to_store` shim** (`docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`) — existing tech debt; not blocking; deferred to follow-up work (see Scope Boundaries). The plan's measure-time post-process correctly lives in the runner / `phases.py`, not inside a QC controller.
- **`sg_ratio` size dependency** (`docs/solutions/logic-errors/grouped-thresholding-development-lessons.md` Item 3) — the synthetic-row formula uses `sum/N_theoretical` intentionally (not `mean`); leave a comment at the formula so a future reader doesn't "fix" it back to a mean.

---

## Key Technical Decisions

- **`EdgeMode` defaults to `exclude`** — matches today's "always filter edges" invariant. Old `run_config.json` files load with this default via `config_from_dict` tolerance, preserving Resume on pre-existing run folders.
- **Edge detection at measure time uses border-pixel diff, not persistence in `/labels/cellpose_qc`** — `get_edge_labels(labels)` reuses the algorithm from `filter_edge_cells` (collect labels appearing in boundary rows/columns). Cheap, no new HDF5 contract.
- **Synthetic row `cell_id=-1` carried in the `cell_id` column, real cells get `cell_id=label`** — the `cell_id` column is referenced by `_ALWAYS_ON_COLUMNS` (`config_dialog.py:84`) and `identity_cols` (`phases.py:626`) but currently never populated; U4 populates it as part of the same change.
- **`is_edge` and `is_edge_synthetic` columns always present** — even in `exclude` mode (uniformly `False`) and in runs where the size-normalized cohort isn't used. Keeps the parquet schema stable across runs.
- **Synthetic row `group_<round>` columns are NaN, not a string sentinel** — natural left-merge behavior (the synthetic row's `label=-1` is absent from any `/groups/<round_name>` table). Filtering uses `is_edge_synthetic=True` instead of inventing group names.
- **Dilute Phase 5 queue wrapper instantiates `DilutePhaseMaskController` directly, skipping `DilutePhaseMaskPanel`** — the runner is the outer loop; the panel's outer-loop chrome (Setup block, Iteration block) would conflict. The wrapper provides minimal queue-aware UI (status + Run another round / Done / Cancel) and forwards controller signals to `on_complete`. The wrapper is a `QObject` because the dilute controller emits Qt signals (`ThresholdQCQueueEntry` is a plain class because its controller uses callable-style on_complete; not directly applicable here).
- **Creator four-step contract is carved out for batch mode via `session_free=True`** — `DilutePhaseMaskController.finish(session_free=True)` writes through a new session-free `write_dilute_mask(repo, handle, mask_name, mask)` helper extracted from `AcceptDiluteMask`. Steps 1 + 2 of the contract (store.write_mask + viewer_win.add_mask) still fire per-dataset; steps 3 + 4 (session.refresh_resource_lists + session.set_active_mask) are intentionally skipped because the launcher's session is either unbound (silent no-op) or bound to a different dataset (writes wrong dataset's mask list). Pinned by U5's session-isolation regression test.
- **Explicit `cancelled: bool` on `PhaseResult`, not message substring sniff** — `_wrapped_complete`'s existing `"cancel" in message` heuristic is fragile (false positives like "operation was cancelled by OS"). New handlers emit `PhaseResult(success=False, cancelled=True)` explicitly; the runner checks the flag. The substring sniff stays as backward-compat fallback for handlers not yet migrated.
- **`interactive_qc=False` headless mode skips Phase 5 entirely** — the dilute phase is inherently interactive (user terminates per-dataset). The headless test fallback is "no dilute phase ran", not "auto-iterate".
- **Resume mid-dilute discards in-flight round state and restarts that dataset's dilute loop from round 1** — bounded loss (typically <5 rounds of work); avoids serializing `DilutePhaseMaskController`'s in-memory working buffer / cumulative condensed union. The runner records per-dataset completed round counts into `RunMetadata.per_dataset_dilute_round_counts` only at the per-dataset boundary (after `workflow_done`). U6 reads this dict for `summary_datasets.csv`.
- **Summary CSVs are derived from the same in-memory `df` `export_run` already builds** — no new staging pipeline; hand-rolled `df.groupby(...).agg(...)` for both files; wrapped in try/except routed to `DatasetFailure.MEASUREMENT_ERROR` with the `"<export>"` sentinel dataset_name (existing pattern at `runner.py:758`).

---

## Open Questions

### Resolved During Planning

- **How to identify edge cells at measure time** — new helper `get_edge_labels(labels) -> set[int]` in `src/percell4/domain/segmentation/postprocess.py`, sibling to `filter_edge_cells`. Reuses the same border-row/column scan.
- **Where the edge-mode selector lives in the config dialog** — new `"Edge-cell handling"` group adjacent to the Cellpose settings group (it's logically a segmentation post-processing decision but workflow-wide rather than per-Cellpose-run).
- **How the dilute UI composes inside an INTERACTIVE phase** — new `DilutePhaseQueueEntry(QObject)` wrapper sibling to `ThresholdQCQueueEntry` (must be a `QObject` because the dilute controller emits Qt signals). Wraps `DilutePhaseMaskController` directly; provides minimal queue-aware UI; forwards `workflow_done` → `on_complete(success=True)`, `workflow_cancelled` → `on_complete(success=False, cancelled=True)`, `error` → `on_complete(success=False)` + `record_failure`.
- **Resume semantics inside dilute** — discard in-flight state, restart that dataset's dilute from round 1. `RunMetadata.per_dataset_dilute_round_counts` records per-dataset completed round counts only at the per-dataset boundary.
- **`DatasetFailure` surface for "no whole cells"** — `measure_one` returns `(df, failure_or_none)`; the runner's `_make_measure_handler` routes the failure into the existing `phases.record_failure(metadata, dataset_name, phase_name, DatasetFailure.MEASUREMENT_ERROR, message)`. No new failure category.
- **`cell_id` column population** — populate as part of U4 (`cell_id = label` for real cells, `cell_id = -1` for synthetic). Cleans up a pre-existing latent column. *(Scope-creep concern: see Open Questions / From 2026-05-20 ce-doc-review.)*
- **`run_config.json` backward compat** — `_from_dict` defaults: `edge_mode = EdgeMode.EXCLUDE`, `dilute_settings = None`, `per_dataset_dilute_round_counts = {}`. Round-trip test exercises both old (without the fields) and new (with the fields) fixtures.
- **`AcceptDiluteMask` batch-mode behavior** — extract a session-free `write_dilute_mask(repo, handle, mask_name, mask)` helper; `AcceptDiluteMask.execute` wraps it for single-dataset use; `DilutePhaseMaskController.finish(session_free=True)` calls the helper directly in batch mode. Steps 3 + 4 of the Creator contract are intentionally skipped in batch (see Key Technical Decisions). Pinned by a session-isolation regression test in U5.
- **`measure_one` signature change** — accepts optional `edge_mode: EdgeMode = EdgeMode.EXCLUDE` and returns `tuple[pd.DataFrame, FailureRecord | None]`. The runner-side wrapper routes the failure into `record_failure`. The pure-function discipline holds (no Qt, no metadata access inside `measure_one`).
- **`DiluteSettings.channel` source** — added as a required field on `DiluteSettings` (U1); selected via a channel combo in U3 backed by the intersected_channels list; non-empty validation at `__post_init__` and intersected-channel-membership check at workflow-start time.
- **Phase numbering — plan vs origin** — plan uses runner's physical phase slots (Phase 7 = measure, Phase 8 = export); origin uses sequential narrative numbering. Plan's HLD includes an explanatory note.

### Deferred to Implementation

- **Final list of columns in `summary_datasets.csv` for the `source` field encoding** — origin requires `h5_existing` | `compressed_from_tiff`; check what string value `DatasetSource` enum members serialize to and whether to coerce or expose directly.
- **Whether to add an explicit fixture loading an "old `run_config.json`" file at a specific path** under `tests/fixtures/` or generate it inline via `dataclasses.replace` minus the new fields then `_to_dict`. Pick whichever produces the more readable test.
- **Exact dock widget class** — `QMainWindow` vs `QDockWidget` vs a sub-widget extracted from `DilutePhaseMaskPanel.iteration_block`. Pick at implementation time; the sibling-dialog-drift learning suggests extracting a sub-widget if shared chrome surfaces.

### From 2026-05-20 ce-doc-review

The 2026-05-20 document-review pass surfaced 28 actionable items. Tier 1 (plan-breakers) was applied directly into the plan body above; Tier 2 and Tier 3 are captured here for the implementer to revisit during execution. Each entry includes its review tier, source persona(s), and recommended action.

#### Tier 2 — Real plan gaps to address during execution

- **[design-lens, P1] Edge-mode combo labels and tooltip copy are unspecified** — `currentIndexChanged` value labels and tooltip text per mode (`exclude` / `include_as_normal` / `include_as_size_normalized_cohort`) are not specified in U3. Specify verbatim labels (suggested: "Exclude (default)" / "Include — count as whole cells" / "Include — synthesize edge cohort") and per-mode tooltips at U3 implementation time. Decide before any test asserts the label text.
- **[design-lens, P1] Cancel scope is ambiguous — whole-run vs this-dataset** — U5's Phase 5 dock has only "Cancel" which propagates to runner-level cancel (kills the entire run). A researcher wanting to skip dilute for one difficult dataset has no affordance. Either add a "Skip this dataset" button distinct from run-Cancel, or two-step confirmation. Decide before U5 UI implementation begins.
- **[design-lens, P1] Between-round dock state is unspecified** — what does the dock show after `round_complete(N)` fires and before `start_round()` is called next? Specify a 3-state diagram (running-round / between-rounds / done-cancelled) with button enabled/disabled per state and napari layer visibility, before U5 UI implementation.
- **[adversarial, P1] Cancel-propagation substring sniff is fragile** — partially addressed by the new explicit `PhaseResult.cancelled` flag (Key Technical Decisions), but the legacy substring sniff in `_wrapped_complete` remains as fallback. If other handlers (seg-QC, threshold-QC) are migrated to the explicit flag in the same PR, the sniff can be removed entirely.
- **[product-lens, P1] Synthetic-row formula produces meaningless values for non-additive metrics** — `sum(max_intensity)/N_theoretical`, `sum(std)/N_theoretical`, etc. are mathematically defined but biologically opaque. Origin R7 prescribes uniform application; the formula stays as-is. Mitigation options to consider during U3 implementation: (a) restrict synthetic-row population to additive metric columns (NaN the rest), (b) tooltip on the edge-mode selector explaining the limitation, (c) add a column-glossary note to the run folder. Confirm with the researcher (origin author) which mitigation is preferred.
- **[product-lens + adversarial, P1] Resume mid-dilute silently discards completed rounds** — Plan accepts bounded loss but provides no user warning at Resume time. During U5 implementation, add a Resume-time dialog when `RunMetadata` shows an in-flight dilute dataset, naming the dataset and round count that will be discarded. (Optional: persist cumulative condensed-mask union to h5 per round for true partial-Resume — out of scope for this plan, surface as follow-up.)
- **[adversarial, P1] `cell_id` is post-`relabel_sequential` per-dataset, not globally unique** — Plan adds `cell_id = label` but `label` is per-dataset sequential after `relabel_sequential`. The (`dataset`, `cell_id`) composite is the actual key. During U4 implementation, either rename to `dataset_local_label` OR add a regression test asserting `cell_id` reuse across datasets is intentional. Document the composite-key contract.
- **[design-lens, P1] Settings-lock visual feedback is unspecified** — R11 says dilute settings are locked at Start; the visual cue is undefined. During U3 implementation, add a note under the dilute sub-panel ("These settings apply to all datasets — cannot be changed after Start") and a read-only summary in the Phase 5 dock showing the locked values per dataset.
- **[adversarial, P1] Synthetic-row formula is degenerate when `n_whole = 1`** — Plan handles `n_whole = 0` (R10b) but `n_whole = 1` makes `mean(whole_areas)` a single-sample point with no variance. During U4 implementation, decide: (a) add a minimum-whole-cell threshold (e.g., `n_whole < 3`) below which the synthetic row is suppressed with a `DatasetFailure`, or (b) carry `n_whole` and `N_theoretical` on the synthetic row for downstream filterability.
- **[adversarial, P1] Synthetic-row sum behavior on NaN-containing metrics is unspecified** — `measure_multichannel_with_masks` can emit NaN entries. Plan doesn't say whether to use `sum` (NaN-propagating) or `nansum`. During U4 implementation, specify: recommend `nansum` plus tracking per-metric `n_edge_contributing` on the synthetic row for transparency.

#### Tier 3 — Architectural / scope concerns

- **[scope-guardian + adversarial contradiction, P1, recommended_action=Skip] `DilutePhaseQueueEntry` wrapper — closure vs extracted sub-widget** — scope-guardian argues: implement Phase 5 as a `_make_dilute_handler` closure in `runner.py` (no new module). Adversarial argues: if the wrapper exists with "Run another round / Done / Cancel" buttons, those ARE the chrome of `DilutePhaseMaskPanel`'s iteration block — extract a shared sub-widget. Plan currently picks the wrapper-class path. During U5 implementation, decide between closure-style (simpler, matches seg-QC/threshold-QC pattern) vs sub-widget-extracted (defends against sibling-dialog drift). The sibling-dialog learning suggests extraction; the existing factory pattern suggests closure. Pick before writing the wrapper.
- **[scope-guardian, P2] `cell_id = label` for ALL real cells is scope creep beyond R8** — R8 only requires `cell_id = -1` on the synthetic row. The plan adds it for all real rows as a "latent column cleanup." If this introduces behavior change for any downstream `combined.csv` / per-dataset CSV reader, scope it as a separate named fix item with verification that downstream CSVs produce the same row counts as today. If verification is clean, fold into U4.
- **[scope-guardian, P2] `edge_cohort.py` new module is unjustified** — partially addressed in U4 (the helper is now `_append_synthetic_row` inside `phases.py`, no new module). Verify during U4 implementation that the helper stays private and the test scenarios cover it from `test_phases.py`.
- **[scope-guardian, P2] `summaries.py` new module is unjustified** — Plan still creates `summaries.py`. During U6 implementation, consider inlining `build_summary_groups` and `build_summary_datasets` as private functions in `phases.py` (`_build_summary_groups`, `_build_summary_datasets`) and merging the tests into `test_phases.py`. If `phases.py` grows beyond ~900 lines after the full 6-unit implementation, defer the split to a separate refactor.
- **[feasibility, P2] `DiluteSettings.grouped_threshold` algorithm encoding mismatch** — partially addressed in U1 (DiluteSettings now uses canonical `ThresholdAlgorithm` / `GmmCriterion` StrEnums rather than embedding `GroupedThresholdConfig`). U3 must convert from the GUI widget's snapshot to the canonical encoding in `try_build_config`.
- **[feasibility, P2] `DilutePhaseQueueEntry` must be a QObject** — addressed in U5 (now explicit in the Approach). No further action.
- **[feasibility, P2] Synthetic-row formula scope for per-round `_in_/_out_` mask columns is undefined** — `measure_multichannel_with_masks` emits per-round mask-overlap columns. Plan describes uniform `sum/N_theoretical` to "every metric column" but doesn't explicitly address these. During U4 implementation, decide: apply formula to ALL numeric columns (including overlap), restrict to whole-cell columns only (NaN the rest), or apply only to additive metrics. Add a test fixture with per-round masks present to lock the chosen behavior.
- **[product-lens, P2] No mid-batch escape for misconfigured dilute settings** — Settings lock at Start; researcher who realizes on dataset 1 that the dilation radius is wrong has only "Cancel entire run." Document as explicit scope boundary OR add a Resume-Phase-5-only entry point that re-reads dilute settings. Decide during U5 design.
- **[design-lens, P2] Per-dataset dilute error UX is unspecified** — `controller.error(msg)` triggers `record_failure` and advances. During U5 UI implementation, decide whether the dock surfaces the error inline with "Skip this dataset and continue" vs "Cancel run" choices, or silently auto-advances. Recommend inline message with explicit choice.
- **[design-lens + feasibility-residual, P2] Napari viewer layer state between Phase 5 datasets** — partially addressed in U5 (now includes `viewer.layers.clear()` at queue-entry top). Verify during U5 implementation that no controller-cached layer references leak across datasets.
- **[adversarial, P2] `ViewerWindow` rebinding contract per dataset** — addressed by U5's `viewer.layers.clear()` + fresh-controller-per-dataset approach. Add a test asserting two datasets back-to-back operate on the correct dataset's image array.
- **[adversarial, P2] Resume of partial-Phase-5 success/failure is untested** — dataset 1 dilute succeeded → dataset 2 dilute failed → Resume — does Phase 5 re-run for dataset 1 (overwrite `/masks/`)? Skip because mask exists? Specify Phase 5 idempotency during U5 implementation. Recommend: skip a dataset whose `/masks/<dilute_name>` already exists on Resume. Add a U5 test.
- **[coherence, P2] U1's Requirements list incorrectly included R16** — already addressed in the U1 edits above (R16 moved to a preservation-constraint note rather than a requirement U1 implements).

#### FYI observations (anchor 50 — no decision required, but worth noting)

- **[coherence] Phase numbering** — already addressed by HLD clarifying note above.
- **[coherence] U6 dependency on U4 implicit, not in prose** — U6's `build_summary_groups` filter uses `is_edge_synthetic` column from U4. Dependencies field on U6 already cites U4; consider adding a one-line approach note that U6 assumes U4's columns exist.
- **[feasibility] `config_dialog.py` already calls `wrap_in_scroll`** — U3 verification step is informational, not a fix.
- **[feasibility] seg-QC edge-cleanup tool unchanged when edge_mode=INCLUDE** — the seg-QC tool will still let the user manually delete edge cells, creating an inconsistency between user-stated intent (keep edges) and user-actual-action (delete some). Worth a docstring note on the seg-QC tool about edge_mode interaction.
- **[product-lens] Premature canonicalization of "Phase 5 INTERACTIVE composition pattern"** — Plan suggests documenting the pattern in `docs/solutions/architecture-patterns/`. Recommend deferring until a second batch-wraps-interactive workflow exists, then extract the pattern from two real instances.
- **[product-lens] Always-on columns lack discoverability** — `is_edge` / `is_edge_synthetic` / `cell_id` always present even in `exclude` mode. Consider adding a `column_glossary.txt` or readme to the run folder describing the parquet schema.
- **[design-lens] Per-dataset round-count visibility for calibration** — surfacing prior-dataset round counts in the Phase 5 dock status would help researchers calibrate ("am I doing too few rounds on this one?"). Cheap to add during U5 UI implementation; reads from `RunMetadata.per_dataset_dilute_round_counts`.
- **[scope-guardian] `is_edge_synthetic` always-present rationale not explicit** — Plan keeps the column even in modes where it's uniformly False, for schema stability. Add a one-sentence rationale in Key Technical Decisions or downgrade to "present only when edge_mode == INCLUDE_AS_SIZE_NORMALIZED_COHORT." Recommended: keep always-present for downstream filter-script stability.
- **[scope-guardian] HLD has matrix + phase ordering + mermaid** — partially redundant artifacts. The mermaid diagram restates the phase ordering block. Consider trimming during the next plan refresh; not blocking.
- **[adversarial] Resume transient layers cleanup** — partially addressed by U5's `viewer.layers.clear()`. If process exits non-cleanly (crash) mid-Phase-5, the napari session state may persist layers; verify launcher startup clears any `_dilute_workflow_*` layers idempotently.
- **[adversarial] U2 has no error-path test (legacy string `edge_mode` payload)** — add a U2 test where `cfg.edge_mode` arrives as a non-`EdgeMode` value (e.g., via `dataclasses.replace` bypass) and `segment_one` is called. Either it raises a clear error or it tolerates the StrEnum-comparable string. Pin whichever the actual behavior is.
- **[adversarial] `is_edge` in `exclude` mode advertises a feature that didn't run** — column uniformly False but suggests edge cells existed somewhere. Either record `edge_mode` in parquet metadata or document in `summary_datasets.csv` header that `is_edge` is only meaningful when `edge_mode` is one of the INCLUDE_* values.
- **[adversarial] Edge cells touching each other via non-exclusive masks** — `filter_edge_cells` and per-pixel `regionprops` measurement assume exclusive labels (cellpose property). Metrics measured under non-exclusive masks (e.g., a dilute mask spanning two edge cells) could over-count. Worth a docstring note on `_append_synthetic_row`; add a test combining `INCLUDE_AS_SIZE_NORMALIZED_COHORT` with a dilute mask that spans multiple edge cells.
- **[adversarial] Synthetic row + `record_failure` ordering** — for the zero-whole-cells case, the failed dataset's df still proceeds through group-merge and staging. Downstream `datasets_without_failures` filters it out during export. Internally consistent but worth a comment in `measure_one` clarifying the staging/export divergence for failed-but-measured datasets.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

### Edge-mode behavior matrix

```
                                Phase 1 filter   Phase 7: is_edge column   Phase 7: synthetic row
exclude (default)               YES (filter)     all rows False            never emitted
include_as_normal               NO  (skip)       True for edge cells       never emitted
include_as_size_normalized      NO  (skip)       True for edge cells       1 per dataset, group_<round>=NaN
  ↳ zero edge cells in DS                                                  not emitted (no edge cells)
  ↳ zero whole cells in DS                                                 not emitted + DatasetFailure
```

### Phase ordering (with U-IDs mapping to phase changes)

```
Phase 0: Compress                                       (unchanged)
Phase 1: Segment with Cellpose                          U2: edge filter conditional on cfg.edge_mode
Phase 2: Segmentation QC queue                          (unchanged)
Phase 3-N: Grouped thresholding rounds (compute + QC)   (unchanged, R16)
Phase 5 [NEW, optional]: Dilute mask queue              U5: per-dataset interactive, adaptive rounds
Phase 7: Measure                                        U4: cell_id, is_edge, is_edge_synthetic, synthetic row
Phase 8: Aggregate + export                             U6: + summary_groups.csv, summary_datasets.csv
```

> *Phase numbering note: the plan uses the physical phase slots that already exist in the runner's module docstring at `src/percell4/gui/workflows/single_cell/runner.py` (Phase 7 = measure, Phase 8 = export). Slots 5 and 6 were historically reserved for interactive QC phases that the v1 implementation never shipped as separate slots — Phase 5 is now repurposed for the dilute queue per this plan. The origin requirements doc uses sequential narrative numbering (Phase 5 = dilute, Phase 6 = measure, Phase 7 = export) because it's a product spec, not a code spec. When code lands, the runner's slot numbering is canonical; the origin doc's narrative numbering is illustrative.*

### Phase 5 (dilute queue) composition

```mermaid
flowchart LR
    Runner["SingleCellThresholdingRunner<br>_phase_generator yields<br>INTERACTIVE PhaseRequest per dataset"]
    Wrap["DilutePhaseQueueEntry<br>(new, sibling of ThresholdQCQueueEntry)"]
    Ctrl["DilutePhaseMaskController<br>(existing single-dataset controller)"]
    Tqc["ThresholdQCController<br>(inner-inner-loop modal per round)"]
    Store["DatasetStore.write_mask(/masks/&lt;dilute_name&gt;)"]

    Runner -->|handler(on_complete)| Wrap
    Wrap -->|start_round, finish, cancel| Ctrl
    Ctrl -->|spawns per round| Tqc
    Ctrl -->|finish() writes via use case| Store
    Ctrl -->|signal workflow_done| Wrap
    Wrap -->|on_complete(PhaseResult)| Runner
```

The runner owns dataset advancement; the controller owns round iteration; the QC controller owns per-round threshold UX. Three nested control loops, each at its own ownership level.

---

## Implementation Units

- U1. **WorkflowConfig + RunMetadata: EdgeMode, DiluteSettings, dilute round-count tracking, Resume back-compat**

**Goal:** Add `EdgeMode(StrEnum)`, `DiluteSettings` frozen dataclass, and `edge_mode` / `dilute_settings` fields to `WorkflowConfig`. Add `per_dataset_dilute_round_counts` mutable field to `RunMetadata` so U6 has a source for `n_rounds_dilute`. Validate dilute-name uniqueness against thresholding round names. Update `run_config.json` serialization with defaults so old run folders still load.

**Requirements:** R3, R3a, R11, R14 *(R16 is a preservation constraint U1 must not violate, not a requirement U1 implements — see U1's approach note.)*

**Dependencies:** None

**Files:**
- Modify: `src/percell4/workflows/models.py` (add EdgeMode, DiluteSettings; extend WorkflowConfig and RunMetadata)
- Modify: `src/percell4/workflows/artifacts.py` (config + metadata serialization with back-compat defaults)
- Test: `tests/test_workflows/test_models.py`
- Test: `tests/test_workflows/test_artifacts.py`

**Approach:**
- Add `EdgeMode(StrEnum)` next to the existing StrEnums in `models.py` with three values matching origin R3a.
- Add `DiluteSettings` as a frozen dataclass with fields: `mask_name: str`, `dilation_radius_px: int`, **`channel: str`** (required — the dilute controller needs a channel name to load `/intensity` per-dataset; the standalone single-dataset UI gets this from `session.active_channel` but batch mode has no session-bound channel), `algorithm: ThresholdAlgorithm`, `gmm_criterion: GmmCriterion | None`, `gmm_max_components: int | None`, `kmeans_n_clusters: int | None`, `sigma: float`. Use the canonical `ThresholdAlgorithm` / `GmmCriterion` StrEnums (matching `ThresholdingRound`'s encoding) rather than embedding the GUI-snapshot `GroupedThresholdConfig` directly — keeps `run_config.json` algorithm encoding consistent across rounds and dilute settings.
- Add `WorkflowConfig.edge_mode: EdgeMode = EdgeMode.EXCLUDE` and `WorkflowConfig.dilute_settings: DiluteSettings | None = None`.
- Add cross-field validation in `WorkflowConfig.__post_init__`: if `dilute_settings is not None`, its `mask_name` must not collide with any `thresholding_rounds[i].name` (covers R14 / AE4). `DiluteSettings.__post_init__` validates non-empty `mask_name` and `channel`, positive `dilation_radius_px`, and algorithm-specific param consistency. Channel validity against intersected_channels is checked at workflow-start time, not in `__post_init__` (intersection is a runtime concept).
- Add `RunMetadata.per_dataset_dilute_round_counts: dict[str, int] = field(default_factory=dict)`. Populated by the runner at each per-dataset dilute completion (U5). Read by U6's `summary_datasets.csv` builder.
- Update `config_to_dict` / `config_from_dict` and `metadata_to_dict` / `metadata_from_dict` in `artifacts.py` to serialize all new fields; `_from_dict` paths must tolerate their absence (default to `EdgeMode.EXCLUDE` / `None` / empty dict).
- **R16 preservation constraint:** U1 adds two new top-level fields to `WorkflowConfig` but does NOT touch `thresholding_rounds` shape, ordering, or `__post_init__` validation for thresholding rounds. The existing structure stays exactly as-is.

**Execution note:** Characterization-first — add a round-trip test loading an "old" `run_config.json` (without the new fields) before writing the new serializer code. This catches Resume break before the runner ever sees it.

**Patterns to follow:**
- `CellposeSettings` (frozen dataclass with `__post_init__` validation) — same shape as `DiluteSettings`.
- `ThresholdAlgorithm`, `GmmCriterion`, `DatasetSource` (StrEnums) — same shape as `EdgeMode`.
- Existing uniqueness validation in `WorkflowConfig.__post_init__` for thresholding round names.

**Test scenarios:**
- Happy path: `WorkflowConfig(..., edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT, dilute_settings=DiluteSettings(mask_name="dilute", dilation_radius_px=3, channel="ch0", algorithm=ThresholdAlgorithm.GMM, gmm_criterion=GmmCriterion.BIC, gmm_max_components=4, sigma=1.0))` constructs successfully.
- Happy path: `WorkflowConfig` constructed without specifying `edge_mode` or `dilute_settings` defaults to `EdgeMode.EXCLUDE` / `None`.
- Happy path: `RunMetadata(..., per_dataset_dilute_round_counts={"DS1": 2, "DS2": 4})` constructs successfully; round-trips through `metadata_to_dict` / `metadata_from_dict` unchanged.
- Edge case: each `EdgeMode` value round-trips through `config_to_dict` → `config_from_dict` unchanged.
- Edge case: `DiluteSettings(mask_name="", ..., channel="ch0", ...)` raises `ValueError` (empty name).
- Edge case: `DiluteSettings(mask_name="dilute", ..., channel="", ...)` raises `ValueError` (empty channel).
- Edge case: `DiluteSettings(mask_name="bad name", ...)` raises `ValueError` if a name regex is enforced (mirror `_ROUND_NAME_RE` policy if applicable).
- Edge case: `DiluteSettings(dilation_radius_px=0, ...)` and `DiluteSettings(dilation_radius_px=-1, ...)` raise `ValueError`.
- Edge case: `DiluteSettings(algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=None, ...)` raises `ValueError` (algorithm-specific param consistency).
- Error path: **Covers AE4.** `WorkflowConfig(..., thresholding_rounds=[ThresholdingRound(name="puncta_bright", ...)], dilute_settings=DiluteSettings(mask_name="puncta_bright", ...))` raises `ValueError` mentioning the name conflict.
- Error path: malformed `edge_mode` string in `config_from_dict` payload raises `ValueError` (StrEnum strictness).
- Integration: load a pre-change `run_config.json` fixture (no `edge_mode` / `dilute_settings` / `per_dataset_dilute_round_counts` keys) through `read_run_config(...)` → resulting `WorkflowConfig.edge_mode == EdgeMode.EXCLUDE`, `dilute_settings is None`, `RunMetadata.per_dataset_dilute_round_counts == {}`. No exception raised. Re-write via `write_run_config(...)` → resulting JSON now contains all three keys.

**Verification:**
- `pytest tests/test_workflows/test_models.py tests/test_workflows/test_artifacts.py -q` passes.
- An old `run_config.json` fixture loads through `read_run_config` and a Resume scenario starts at the correct phase with `edge_mode = EdgeMode.EXCLUDE`.

---

- U2. **Conditional edge filtering + measure-time edge detection helper**

**Goal:** Make the workflow's Phase 1 `filter_edge_cells` call conditional on `cfg.edge_mode == EdgeMode.EXCLUDE`. Add a pure-domain helper `get_edge_labels(labels) -> set[int]` for use at measure time.

**Requirements:** R4, R5, R6 (Phase 1 side)

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/workflows/phases.py` (segment_one — the call at line 197)
- Modify: `src/percell4/domain/segmentation/postprocess.py` (add `get_edge_labels`)
- Test: `tests/test_workflows/test_phases.py`
- Test: `tests/test_segmentation/test_postprocess.py` (or wherever `filter_edge_cells` is tested)

**Approach:**
- In `segment_one`: read `cfg.edge_mode`; skip `filter_edge_cells` when mode is `INCLUDE_AS_NORMAL` or `INCLUDE_AS_SIZE_NORMALIZED_COHORT`. Keep `filter_small_cells` and `relabel_sequential` running unconditionally.
- Replace the existing `# Postprocess: edge removal is always on per workflow invariant.` comment with a one-line note pointing at the new conditional.
- In `postprocess.py`: add `get_edge_labels(labels: np.ndarray, edge_margin: int = 0) -> set[int]`. Algorithm: identical to the border-collection step in `filter_edge_cells`, but returns the set instead of zeroing. The two functions share an internal helper to avoid drift.
- Do **not** add `edge_mode` checks inside `filter_edge_cells` itself — keep the helper pure; gate at the caller.
- Leave the seg-QC interactive cleanup tool (`seg_qc.py:427,478`) untouched — it's a user-driven cleanup, not the workflow invariant. Confirm in the test pass that it continues to work.

**Execution note:** Characterization-first — add a regression test that pins the **current** `exclude`-mode behavior on a known fixture before refactoring the call site, so the default path is provably unchanged when the conditional lands.

**Patterns to follow:**
- `filter_edge_cells` shape — pure numpy, no scikit-image dependency in this code path.
- `_iter_cell_crops` for the convention of iterating over `regionprops` to find cells in labels.

**Test scenarios:**
- Happy path: `segment_one` with `cfg.edge_mode = EdgeMode.EXCLUDE` removes edge-touching labels from output (existing behavior pinned).
- Happy path: `segment_one` with `cfg.edge_mode = EdgeMode.INCLUDE_AS_NORMAL` keeps edge-touching labels in output.
- Happy path: `segment_one` with `cfg.edge_mode = EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT` keeps edge-touching labels (same as INCLUDE_AS_NORMAL at this stage).
- Happy path: `get_edge_labels(labels)` on a 100×100 array with cells at corners, edges, and interior returns the set of border-touching label IDs only.
- Edge case: `get_edge_labels` on a labels array with zero cells returns an empty set.
- Edge case: `get_edge_labels` on a labels array where every cell touches the border returns every non-zero label.
- Edge case: `get_edge_labels` with `edge_margin=2` on a cell that touches row 1 (one pixel from border) returns that label.
- Integration: `segment_one(..., edge_mode=INCLUDE_AS_NORMAL)` followed by `get_edge_labels(returned_labels)` returns the same set of labels that `filter_edge_cells` would have removed in EXCLUDE mode.

**Verification:**
- Existing `tests/test_workflows/test_phases.py` tests for `segment_one` still pass with `EdgeMode.EXCLUDE` (default).
- New tests for the two `INCLUDE_*` modes pass.
- New `get_edge_labels` tests pass.

---

- U3. **Config dialog: edge-mode selector + dilute sub-panel**

**Goal:** Add an "Edge-cell handling" group with a 3-option selector (default `exclude`) and an optional "Generate dilute-phase mask" checkable group exposing the dilute settings widgets. Wire all user-edit signals. Validate dilute-name uniqueness against round names at "Start" time. Verify the dialog stays compliant with the scroll-wrapping rule.

**Requirements:** R3, R3a, R11, R14

**Dependencies:** U1 (needs `EdgeMode` enum and `DiluteSettings` dataclass)

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py`
- Test: `tests/test_gui_workflows/test_config_dialog.py`

**Approach:**
- Add an "Edge-cell handling" `QGroupBox` adjacent to the Cellpose settings group. Inside: a `QComboBox` with three entries mapped to `EdgeMode` values; tooltip text explaining each mode at a researcher level (not implementation level).
- Add a "Generate dilute-phase mask" `QGroupBox(checkable=True)`. Inside: a `QLineEdit` for `mask_name`, a `QSpinBox` for `dilation_radius_px`, and an embedded `GroupedThresholdSettingsWidget` (reused — see `src/percell4/gui/_grouped_threshold_settings.py`).
- Wire `currentIndexChanged` (combo), `textChanged` (line edit), `valueChanged` (spinbox), `toggled` (groupbox checkable), and `changed` (grouped threshold widget) to whichever validators / Start-button enablers already exist for the other dialog fields.
- In `try_build_config` (or equivalent): construct `WorkflowConfig` with `edge_mode` from the combo and `dilute_settings` from the dilute group when checked, `None` when unchecked. Let `WorkflowConfig.__post_init__` validate the name conflict (defined in U1); surface the resulting `ValueError` as an inline error message in the dilute group (or wherever existing validation errors render).
- Verify the dialog continues to pass `tests/test_gui/test_dialog_helper_compliance.py` (existing AST-walker that checks dialogs are wrapped in `wrap_in_scroll`).

**Patterns to follow:**
- Cellpose settings group construction in `config_dialog.py` — same `QGroupBox` + `QFormLayout` shape.
- Existing dialog validation surface (whatever pattern renders "Channel intersection: no channels available" etc.) for the dilute name conflict error.
- `GroupedThresholdSettingsWidget` embedding style from wherever it's used elsewhere (the dilute_phase panel itself).

**Test scenarios:**
- Happy path: dialog opens with edge-mode combo defaulting to `exclude` and dilute group unchecked. `try_build_config` returns a `WorkflowConfig` with `edge_mode=EdgeMode.EXCLUDE` and `dilute_settings=None`.
- Happy path: user selects `include_as_size_normalized_cohort` in combo, checks the dilute group, fills in a name and radius and threshold settings → `try_build_config` returns a `WorkflowConfig` with matching fields.
- Edge case: user checks the dilute group but leaves mask_name empty → validation error surfaced inline; Start disabled.
- Edge case: user enters mask_name = a string identical to one of the configured thresholding rounds → validation error mentions the conflicting round name. **Covers AE4.**
- Edge case: user unchecks the dilute group after configuring it → `try_build_config` returns `dilute_settings=None`; the prior values are not persisted into `WorkflowConfig`.
- Integration: simulate user editing combo via `qtbot.mouseClick` / `qtbot.keyClicks` (not programmatic `setCurrentIndex`) and confirm Start-enabled state updates — exercises the user-edit signal path (Qt learning #8).
- Integration: dialog AST compliance: the file still passes `tests/test_gui/test_dialog_helper_compliance.py` (verify after adding the new sections).

**Verification:**
- `pytest tests/test_gui_workflows/test_config_dialog.py tests/test_gui/test_dialog_helper_compliance.py -q` passes.
- Manually opening the dialog (or via a smoke test) confirms the new groups render and the dialog is still scrollable on a small screen.

---

- U4. **Measure-time post-process: cell_id, is_edge, is_edge_synthetic, synthetic row**

**Goal:** Populate `cell_id` (currently unpopulated) for every per-cell row. Add `is_edge` and `is_edge_synthetic` boolean columns to every per-cell row. When `cfg.edge_mode == INCLUDE_AS_SIZE_NORMALIZED_COHORT`, compute the synthetic edge-cohort row per origin R7 and append it to the dataset's measurements DataFrame before the staging parquet write. Handle the zero-edge and zero-whole edge cases per R10.

**Requirements:** R5, R6, R7, R8, R9, R10, R17

**Dependencies:** U1, U2

**Files:**
- Modify: `src/percell4/workflows/phases.py` (extend `measure_one` return type to `(df, failure_or_none)`; add `_append_synthetic_row` private helper)
- Modify: `src/percell4/gui/workflows/single_cell/runner.py` (`_make_measure_handler` routes the returned `failure_or_none` into `record_failure`)
- Test: `tests/test_workflows/test_phases.py`

**Approach:**

The post-process needs `cfg.edge_mode` (config-time) and the option to surface a `DatasetFailure` for the zero-whole-cells case. `measure_one(store, round_specs, metric_names=None)` is signature-stable today and called from `_make_measure_handler` which already has `cfg`, `metadata`, and `dataset_name` in scope. Two compatible adjustments:

1. **`measure_one` change** — accept an optional `edge_mode: EdgeMode = EdgeMode.EXCLUDE` parameter and return `tuple[pd.DataFrame, FailureRecord | None]` instead of bare `pd.DataFrame`. Existing callers pass through unchanged (the new param is optional with the today's-behavior default); the runner's `_make_measure_handler` passes `cfg.edge_mode` and routes the returned failure into `record_failure`. The pure-function discipline holds — `measure_one` still has no `metadata` or Qt knowledge; it returns the failure description and lets the caller decide where to record it.

2. **`_append_synthetic_row(df, edge_label_set, edge_mode) -> tuple[pd.DataFrame, FailureRecord | None]`** — private module-level helper in `phases.py` (NOT a separate `edge_cohort.py` module — keeps the helper next to its sole caller and follows the existing private-helper convention in `phases.py`).

Inside `measure_one`, after `df = measure_multichannel_with_masks(...)` and before the group-merge loop:
  1. Populate `df["cell_id"] = df["label"]`.
  2. Compute `edge_label_set = get_edge_labels(labels)` (from U2).
  3. Populate `df["is_edge"] = df["label"].isin(edge_label_set)`.
  4. Populate `df["is_edge_synthetic"] = False`.
  5. `df, failure = _append_synthetic_row(df, edge_label_set, edge_mode)`. The helper:
     - Returns `(df, None)` unchanged if `edge_mode != INCLUDE_AS_SIZE_NORMALIZED_COHORT`.
     - Returns `(df, None)` unchanged if zero edge cells (R10a).
     - Returns `(df, FailureRecord("no whole cells to compute A_mean for edge-cohort normalization"))` and skips appending if zero whole cells (R10b).
     - Otherwise computes the synthetic row per R7 and appends it with `cell_id=-1`, `is_edge_synthetic=True`, `is_edge=False`.

The runner's `_make_measure_handler` unwraps `(df, failure)` — if `failure is not None`, it calls `record_failure(metadata, dataset_name, "measure", failure.kind, failure.message)`.

The synthetic row is appended **before** the existing `df.merge(g_df, on="label", how="left")` group merge. The synthetic row's `label=-1` is absent from `/groups/<round_name>` tables, so the left-merge naturally leaves its `group_<round_name>` columns as NaN — satisfies R8 without special-casing in the merge code.

Add a comment near the formula: "`sum(M) / N_theoretical` is intentional — density-based extrapolation, not a sample mean. See origin R7."

**Patterns to follow:**
- `_core_row` and `_iter_cell_crops` in `measurer.py` for the convention of how `area` is sourced.
- `record_failure(metadata, dataset_name, phase_name, DatasetFailure.MEASUREMENT_ERROR, message)` — existing failure-recording call in `phases.py`.

**Test scenarios:**
- Happy path: `measure_one` with `edge_mode=EXCLUDE` and no edge cells in fixture → df contains only whole cells, all with `is_edge=False`, `is_edge_synthetic=False`, `cell_id=label`. No synthetic row.
- Happy path: `measure_one` with `edge_mode=INCLUDE_AS_NORMAL` and some edge cells in fixture → df contains all cells; edge cells have `is_edge=True`, others `False`; no synthetic row.
- Happy path: **Covers AE1.** `measure_one` on a fixture with 100 cells (20 edge, 80 whole; whole-cell mean area = 500; edge total area = 4000; edge cells have summed `mean_intensity_ch0` = 1600), with `edge_mode=INCLUDE_AS_SIZE_NORMALIZED_COHORT` → df has 101 rows: 100 per-cell rows (20 `is_edge=True`, 80 `is_edge=False`, all `is_edge_synthetic=False`) and 1 synthetic row with `cell_id=-1`, `is_edge=False`, `is_edge_synthetic=True`, `mean_intensity_ch0 = 200.0`, `area` and other metric columns populated by the same formula.
- Edge case: zero edge cells in `INCLUDE_AS_SIZE_NORMALIZED_COHORT` mode → no synthetic row appended; no failure recorded; df has only per-cell rows (all with `is_edge=False`).
- Error path: **Covers AE2.** All detected cells touch the image border (zero whole cells) in `INCLUDE_AS_SIZE_NORMALIZED_COHORT` mode → no synthetic row appended; `record_failure(..., DatasetFailure.MEASUREMENT_ERROR, message="no whole cells to compute A_mean for edge-cohort normalization")` called; df contains per-cell rows (every one with `is_edge=True`) and proceeds to staging.
- Integration: synthetic row's `group_<round>` columns are NaN after the existing `df.merge(g_df, on="label", how="left")` runs — verifies the natural left-merge composition (no special-casing in merge code).
- Integration: synthetic row's `area` value equals `sum(edge_areas) / N_theoretical = mean(whole_cell_areas)` (a mathematical sanity check from R7's formula applied to the `area` column).
- Integration: parquet written by `write_staging_parquet` contains `cell_id`, `is_edge`, `is_edge_synthetic` columns; reading it back via pandas produces the same boolean dtypes.

**Verification:**
- `pytest tests/test_workflows/test_phases.py tests/test_workflows/test_edge_cohort.py -q` passes.
- Running an end-to-end test of the workflow with the size-normalized cohort mode produces a parquet whose `is_edge_synthetic=True` rows match the expected formula on a synthetic fixture.

---

- U5. **Dilute INTERACTIVE phase: queue wrapper + runner Phase 5 + headless skip**

**Goal:** Build `DilutePhaseQueueEntry` wrapping `DilutePhaseMaskController` as a per-dataset interactive queue entry. Insert Phase 5 between grouped thresholding and measurement in `SingleCellThresholdingRunner._phase_generator`. Skip Phase 5 entirely when `interactive_qc=False` (headless test mode). Satisfy the Creator four-step contract for the per-dataset dilute mask write.

**Requirements:** R11, R12, R13, R15

**Dependencies:** U1, U3

**Files:**
- Create: `src/percell4/gui/workflows/single_cell/dilute_queue.py` (new — sibling of `threshold_qc_queue.py`)
- Modify: `src/percell4/gui/workflows/single_cell/runner.py` (add Phase 5 to `_phase_generator`; record per-dataset round counts into `RunMetadata.per_dataset_dilute_round_counts`)
- Modify: `src/percell4/application/use_cases/accept_dilute_mask.py` (extract a session-free `write_dilute_mask(repo, handle, mask_name, mask)` helper; `AcceptDiluteMask.execute` wraps it for single-dataset UI, the new batch queue calls the helper directly to skip launcher-session mutation)
- Modify: `src/percell4/gui/workflows/dilute_phase/controller.py` (`finish()` accepts an optional `session_free: bool = False` flag — when set, calls `write_dilute_mask` directly instead of `AcceptDiluteMask.execute`)
- Modify: `src/percell4/gui/workflows/CLAUDE.md` (document the new queue module)
- Test: `tests/test_gui_workflows/test_interactive_runner.py` (add fake for the dilute queue, adaptive-round-count test, session-isolation regression test)
- Test: `tests/test_gui_workflows/test_single_cell_runner.py` (extend headless tests to verify Phase 5 is skipped)

**Approach:**
- New `DilutePhaseQueueEntry(QObject)` — must be a `QObject` (sibling of `DilutePhaseMaskController` which emits Qt signals; the cited `ThresholdQCQueueEntry` template is a plain class because its controller uses callable-style `on_complete`, but the dilute controller emits Qt signals so the wrapper has to register slots). Constructor takes `entry: WorkflowDatasetEntry`, `dilute_settings: DiluteSettings`, `host` (for viewer/store/session accessors per the callback-injection learning), `queue_index`, `queue_total`. Internal API: `start(on_complete: Callable[[PhaseResult], None]) -> None`, `cancel() -> None`.
- `start(on_complete)`:
  1. Call `viewer.layers.clear()` at the top (mirroring `ThresholdQCQueueEntry`'s per-dataset clear at `threshold_qc_queue.py:84`) to drop prior dataset's layers.
  2. Open the dataset's `DatasetStore`, read `/intensity` at `dilute_settings.channel` (from U1) and `/labels/cellpose_qc` (the post-seg-QC labels).
  3. Instantiate `DilutePhaseMaskController` directly with the locked dilute config + the loaded image/labels + host-injected viewer + `session_free=True` so its `finish()` writes the mask via `write_dilute_mask(...)` and does NOT call `session.refresh_resource_lists` / `session.set_active_mask` (which would mutate the launcher session per-dataset — see the Creator-contract carve-out below).
  4. Build a minimal queue-aware UI dock (`QMainWindow` or `QDockWidget`) showing dataset N of M + status + "Run another round / Done — save / Cancel" buttons. Wire buttons to `controller.start_round()`, `controller.finish()`, `controller.cancel()`.
  5. Connect `controller.round_complete(n)` → track current round count for `RunMetadata.per_dataset_dilute_round_counts[entry.name]`; `controller.workflow_done` → `on_complete(PhaseResult(success=True))`; `controller.workflow_cancelled` → `on_complete(PhaseResult(success=False, cancelled=True))`; `controller.error(msg)` → `on_complete(PhaseResult(success=False, message=msg))` plus `record_failure(...)`.
  6. On all exits (done / cancelled / error): call `controller._teardown()` to clear transient layers (`_dilute_workflow_view`, `_dilute_workflow_condensed`), close the dock, release the strong reference.
- In `runner.py._phase_generator`: after the per-round threshold compute+QC loop and before the measure phase, check `if self._config.dilute_settings is not None and self._interactive_qc:`. If true, iterate `datasets_without_failures(self._working_entries, self._metadata)` and yield one INTERACTIVE `PhaseRequest` per dataset whose handler instantiates `DilutePhaseQueueEntry` and calls `start(_wrapped_complete)`.
- `request_cancel()` extension: propagate to `_active_qc_controller` (existing pattern at runner.py:109-124 covers this once the strong-ref slot is reused).
- **Creator four-step contract carve-out for batch mode:** Steps 1 (`store.write_mask`) and 2 (`viewer_win.add_mask`) still fire per the contract. Steps 3 (`session.refresh_resource_lists`) and 4 (`session.set_active_mask`) are intentionally skipped in batch mode because the launcher's session may have no dataset loaded (silent no-op) or a different dataset loaded (writes the WRONG dataset's mask list into the launcher's metadata). The `session_free=True` parameter is the test seam: a regression test asserts that across an N-dataset batch dilute run, `session.refresh_resource_lists.call_count == 0` and `session.set_active_mask.call_count == 0`. The single-dataset workflow's `DilutePhaseMaskPanel` continues to use `session_free=False` (default) so its Creator contract stays whole.
- **Cancel signaling:** add a `cancelled: bool = False` field to `PhaseResult` so `_wrapped_complete` checks the explicit flag rather than the substring-sniff `"cancel" in message`. The substring sniff is fragile if an error message ever contains "cancel" legitimately (e.g., "operation was cancelled by OS"). Keep the existing sniff as a fallback for backward compat with other handlers, but new code emits `PhaseResult(cancelled=True)` explicitly.
- Headless mode (`interactive_qc=False`): skip Phase 5 entirely. Document this in the runner module docstring.

**Patterns to follow:**
- `ThresholdQCQueueEntry` in `src/percell4/gui/workflows/single_cell/threshold_qc_queue.py` — same factory shape, same `on_complete` wrapping, same strong-ref slot. Note: the dilute wrapper is a `QObject` (the cited template is not) because the dilute controller emits Qt signals.
- `SegmentationQCController` cleanup behavior at `seg_qc.py:596-642` for the `_teardown` pattern that clears transient layers between datasets.
- Creator four-step contract under `session_free=True`: steps 1 (`store.write_mask`) and 2 (`viewer_win.add_mask`) fire; steps 3 and 4 are intentionally skipped per the carve-out documented in this unit's Approach. Pin with a session-isolation regression test (below).
- `_make_seg_qc_handler` factory and `_make_threshold_qc_handler` factory in `runner.py` — closure pattern.

**Test scenarios:**
- Happy path: 3-dataset run with dilute enabled; fake `DilutePhaseQueueEntry` auto-completes with `success=True` per dataset; `RUN_FINISHED` event fires; each dataset's h5 has `/masks/<mask_name>` written. **Covers F2.**
- Happy path: **Covers AE3.** 3-dataset run with adaptive round counts. Fake queue entry emits `round_complete(1)`, `round_complete(2)` for dataset 1, then `workflow_done`; emits 4 rounds for dataset 2; emits 1 round for dataset 3. Final state: each dataset has its mask written; the per-dataset round counts recorded in `RunMetadata.per_dataset_dilute_round_counts` match {DS1: 2, DS2: 4, DS3: 1}.
- Edge case: headless mode (`interactive_qc=False`) with `dilute_settings != None` → Phase 5 is skipped entirely; downstream measurement runs without `/masks/<mask_name>` (treats it as absent like any other missing mask). No exception raised.
- Edge case: `dilute_settings is None` with `interactive_qc=True` → Phase 5 is skipped entirely; the workflow advances straight from grouped thresholding to measurement.
- Edge case: between-dataset viewer rebind — dataset 1's `_dilute_workflow_view` and `_dilute_workflow_condensed` layers are absent from the viewer when dataset 2's `start_round` runs (verifies `viewer.layers.clear()` at queue-entry top).
- Error path: cancel during dilute (user clicks Cancel on dataset 2 of 3) → `DilutePhaseQueueEntry` fires `on_complete(PhaseResult(success=False, cancelled=True))`; `_wrapped_complete` checks the explicit `cancelled` flag and propagates `request_cancel()`; the runner's cooperative cancel kicks in at the next dataset boundary; downstream phases (measure, export) are skipped; `RUN_FINISHED` event fires with the cancel flag.
- Error path: cancel message false-positive — a `controller.error(msg)` with `msg="operation was cancelled by OS"` does NOT trigger runner-level cancel (the explicit `cancelled=False` flag wins over the substring); `record_failure(...)` is called; that dataset is excluded; the run continues for the others.
- Error path: simulated error during dilute round (controller emits `error("disk full")`) → `record_failure(..., DatasetFailure.MEASUREMENT_ERROR, "disk full")` is called; that dataset is excluded from subsequent phases via `datasets_without_failures`; the run continues for the others.
- Integration: **Creator-contract session-isolation regression.** After a 3-dataset batch dilute run on a real `Session`, assert `session.refresh_resource_lists.call_count == 0` and `session.set_active_mask.call_count == 0` (proves session-free path is taken). Per-dataset, assert `store.write_mask.call_count == 3` and `viewer_win.add_mask.call_count == 3` (proves steps 1 + 2 still fire).
- Integration: standalone single-dataset dilute path (NOT this workflow) still fires all four Creator steps — pin with a regression that opens `DilutePhaseMaskPanel` directly and asserts session.set_active_mask was called once.
- Integration: the dilute mask written to `/masks/<mask_name>` is read by Phase 7 measurement and produces a `<mask_name>_<metric>` set of columns in the parquet (validates R13).

**Verification:**
- `pytest tests/test_gui_workflows/test_interactive_runner.py tests/test_gui_workflows/test_single_cell_runner.py -q` passes.
- Manually running the workflow with dilute enabled, three datasets, and different round counts per dataset produces three `/masks/<mask_name>` entries and the workflow advances to measurement without intervention.

---

- U6. **Summary CSV export: summary_groups.csv + summary_datasets.csv**

**Goal:** After `measurements.parquet` is written in `export_run`, derive `summary_groups.csv` (per dataset × round × group) and `summary_datasets.csv` (per dataset) and write them atomically to the run folder. Failures in summary writing record a `DatasetFailure.MEASUREMENT_ERROR` against the `"<export>"` sentinel and do not prevent the parquet/CSV outputs.

**Requirements:** R18, R19

**Dependencies:** U1, U4

**Files:**
- Modify: `src/percell4/workflows/phases.py` (export_run, between the parquet write and staging cleanup)
- Add helpers: `src/percell4/workflows/summaries.py` (new — keeps `phases.py` focused; pure-Python, Qt-free)
- Test: `tests/test_workflows/test_phases.py`
- Test: `tests/test_workflows/test_summaries.py` (new)

**Approach:**
- New `build_summary_groups(df, thresholding_round_names) -> pd.DataFrame`:
  - Filter to per-cell rows only (`df[~df["is_edge_synthetic"]]`) — synthetic rows have NaN groups and must not contaminate group statistics.
  - For each `round_name` in `thresholding_round_names`, group by `(dataset, "group_<round_name>")`, compute `n_cells`, `fraction_of_dataset_cells` (n_cells / total per dataset), and for every metric column M, `M_mean`, `M_median`, `M_std`.
  - Concatenate per-round results into a long-format frame: `dataset`, `round_name`, `group_label`, `n_cells`, `fraction_of_dataset_cells`, `<metric>_mean`, `<metric>_median`, `<metric>_std`.
- New `build_summary_datasets(df, metadata, config) -> pd.DataFrame`:
  - One row per dataset: `dataset`, `source` (from `WorkflowConfig.datasets[i].source.value`), `n_cells_total` (per-cell rows only), `n_cells_whole` (per-cell rows where `is_edge=False`), `n_cells_edge` (per-cell rows where `is_edge=True`), `n_rounds_thresholding` (len of `config.thresholding_rounds`), `n_rounds_dilute` (NaN if disabled, else per-dataset round count from `metadata`), `dilute_enabled` (bool), `edge_mode` (string), `failure_reason` (lookup from `metadata.failures` for that dataset; NaN if none).
- In `export_run`: after `measurements.parquet` write succeeds, call both builders against the in-memory `df`, then write each via `write_atomic(path, lambda f: result_df.to_csv(f, columns=..., index=False, float_format="%.6g", na_rep="", encoding="utf-8", lineterminator="\n"))`.
- Wrap each builder + write in its own `try/except` → `record_failure(metadata, "<export>", "summary_groups" or "summary_datasets", DatasetFailure.MEASUREMENT_ERROR, str(exc))`. Failures here do not abort the run; the parquet has already landed.

**Patterns to follow:**
- Existing `export_run` CSV write kwargs (`float_format="%.6g"`, `na_rep=""`, `lineterminator="\n"`, `encoding="utf-8"`).
- `write_atomic` for whole-file replacement (per the atomic-write learning).
- `record_failure` and the `"<export>"` sentinel pattern at `runner.py:758`.

**Test scenarios:**
- Happy path: 3-dataset run, 2 thresholding rounds, dilute disabled → `summary_groups.csv` has rows per (dataset × round × group); each `n_cells` matches a hand-counted ground truth on a fixture; `fraction_of_dataset_cells` sums to 1.0 within each (dataset, round_name).
- Happy path: same fixture → `summary_datasets.csv` has 3 rows; `n_cells_total = n_cells_whole + n_cells_edge`; `edge_mode` field matches `config.edge_mode.value`; `dilute_enabled=False`; `n_rounds_dilute` is NaN; `failure_reason` is NaN for all 3.
- Happy path: run with `edge_mode=INCLUDE_AS_SIZE_NORMALIZED_COHORT` and edge cells present → `summary_groups.csv` does NOT contain the synthetic row's contribution in any group's `n_cells` (the synthetic row's groups are NaN and must be filtered out). `summary_datasets.csv` row for that dataset has `n_cells_total` = real cells (excluding synthetic).
- Edge case: a dataset with zero per-cell rows after measurement (all cells failed upstream) → `summary_groups.csv` has no rows for that dataset; `summary_datasets.csv` has a row with `n_cells_total=0`, `failure_reason` populated from metadata.
- Edge case: dilute enabled, dataset 1 used 2 rounds, dataset 2 used 4 rounds → `summary_datasets.csv` `n_rounds_dilute` column is 2 for dataset 1 and 4 for dataset 2.
- Error path: `build_summary_groups` raises (e.g., synthetic test fixture missing expected column) → failure recorded via `record_failure`; `measurements.parquet` and the other CSVs still exist on disk; the run continues to staging cleanup.
- Integration: both CSVs are written via `write_atomic` — confirm by patching `open` in a way that fails atomic semantics, then verify the files are not left half-written.

**Verification:**
- `pytest tests/test_workflows/test_phases.py tests/test_workflows/test_summaries.py -q` passes.
- A real end-to-end run produces both CSVs in the run folder, openable by pandas without quoting/lineterminator surprises.

---

## System-Wide Impact

- **Interaction graph:** New surfaces — `WorkflowConfig` consumers (dialog UI, runner generator, serialization) all see two new fields; the runner's `_phase_generator` gets a new INTERACTIVE phase between rounds and measure; `phases.measure_one` gets a new post-process step before the group-merge; `phases.export_run` gets two new derivation+write steps before staging cleanup. The launcher's `is_workflow_locked` continues to gate re-entry through the new dilute phase like it does today through seg/threshold QC.
- **Error propagation:** All new failure modes route through existing `phases.record_failure(...)` / `DatasetFailure` / `FailureRecord`. The runner's single-exit `_finish` pattern is preserved. Dilute cancellations propagate via the existing `"cancel" in message` sniff in `_wrapped_complete`.
- **State lifecycle risks:** Resume mid-Phase-5 explicitly discards in-flight round state and restarts the dataset's dilute loop from round 1 (key decision). `RunMetadata.per_dataset_dilute_round_counts: dict[str, int]` (added in U1) records per-dataset completed round counts at workflow_done boundaries; U6 reads this for `summary_datasets.csv`. No mid-run state serialization.
- **API surface parity:** `WorkflowConfig` is consumed by both the GUI dialog and any future CLI runner. Adding `edge_mode` and `dilute_settings` extends both. The serialization round-trip in `artifacts.py` is the single source of truth for the on-disk schema. New parquet columns (`is_edge`, `is_edge_synthetic`, `cell_id`) are stable across runs (always present, even if uniformly `False` or equal to `label`).
- **Integration coverage:** Mocks alone won't prove (a) the synthetic row's NaN groups compose correctly with the left-merge of `/groups/<round_name>` (integration test in U4), (b) the Creator four-step contract on the per-dataset dilute mask write (integration test in U5), or (c) Resume of an old `run_config.json` without the new fields (integration test in U1).
- **Unchanged invariants:** Existing phases (compress, segment, seg-QC, grouped-thresholding rounds, measure phases other than the synthetic-row hook, aggregate/export of parquet+CSVs) keep their current behavior under the default `edge_mode=EXCLUDE` and `dilute_settings=None`. The Workflows tab still has one entry plus "Resume run…" (R1). `run_state.json` is not introduced (the v1 implementation merged it into `run_config.json`; we don't reverse that). Existing downstream consumers reading specific columns by name from `measurements.parquet` are unaffected — new columns are additive.

---

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| Old `run_config.json` files break Resume after schema evolution. | U1 adds defaults in `config_from_dict` and a fixture test loading a pre-change config. |
| Sibling-dialog drift between batch dilute UI and single-dataset `DilutePhaseMaskPanel`. | U5 reuses `DilutePhaseMaskController` directly; the new queue wrapper provides minimal chrome only. Pin with a smoke test that the controller's signal contract is unchanged. |
| Creator four-step contract violation on the per-dataset dilute mask write (two recent silent prod bugs in this area). | Batch-mode carve-out via `session_free=True` documented in Key Technical Decisions: steps 1 + 2 fire, steps 3 + 4 intentionally skip with explicit rationale. U5 includes a session-isolation regression test pinning the skip behavior. Single-dataset path retains full four-step contract. |
| Synthetic-row formula is "wrong" for non-additive metrics (mean/max/std) — biologically opaque but mathematically defined. | Origin doc explicitly chose uniform `sum/N_theoretical` (Key Decisions section of origin); leave a comment near the formula warning future readers not to "fix" it back to a mean (sg_ratio lesson). |
| Phase 5 dilute UI hosts the launcher's `ViewerWindow` and `session`; per-dataset session mutation via `set_active_mask` during batch could thrash subscribers. | Resolved during planning via session-free `write_dilute_mask` helper extracted from `AcceptDiluteMask` and `session_free=True` flag on `DilutePhaseMaskController.finish()`. Pinned by U5 session-isolation regression. |
| Adding `is_edge` / `is_edge_synthetic` / `cell_id` columns breaks downstream parquet readers that assume a fixed schema. | Additive-only change; column types are stable; document in U6's verification step. Grep for parquet readers in `src/percell4/` before finalizing column names. |
| New `summary_*.csv` writes use direct `open()` instead of `write_atomic`. | U6 explicitly references `write_atomic`; covered by code review of U6's diff. |
| Edge-mode default changes silently — researcher who relied on always-on filtering sees different results. | Default is `exclude` (= today's behavior). New modes are opt-in via the config dialog. Documented in Key Technical Decisions. |
| `EdgeMode` enum mismatch between Python StrEnum and the JSON string in old run-config files. | StrEnum strictness is per-key; defaults in `_from_dict` cover the absence case. Unit test in U1 covers an `edge_mode = "exclude"` payload round-tripping. |

---

## Documentation / Operational Notes

- Update `src/percell4/gui/workflows/CLAUDE.md` and `src/percell4/workflows/CLAUDE.md` after the work lands — neither needs to mention the implementation, but both should reference the new modules (`single_cell/dilute_queue.py`, `workflows/edge_cohort.py`, `workflows/summaries.py`) in the "Modules" section.
- No README update required (PerCell4 does not have a user-facing README that describes workflow behavior).
- Operational rollout: this is desktop software; no migration concerns beyond Resume back-compat (handled in U1). Researchers running mid-batch when the new build lands keep working on their existing `run_config.json`; the next Start they configure can use the new modes.
- Consider adding a `docs/solutions/architecture-patterns/` entry once the work is in production capturing the "Phase 5 INTERACTIVE composition pattern" (runner → queue wrapper → existing controller as inner loop) as canonical for future batch workflows that wrap single-dataset interactive controllers.

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-20-end-to-end-single-cell-workflow-requirements.md](../brainstorms/2026-05-20-end-to-end-single-cell-workflow-requirements.md)
- Existing single-dataset dilute plan: [docs/plans/2026-05-18-004-feat-dilute-phase-mask-generation-plan.md](2026-05-18-004-feat-dilute-phase-mask-generation-plan.md)
- Original SCTW brainstorm (archived): `docs/archive/2026-04-10-single-cell-thresholding-workflow-brainstorm.md`
- Original SCTW plan: `docs/plans/2026-04-10-feat-single-cell-thresholding-workflow-plan.md`
- Related code (entry points):
  - `src/percell4/workflows/models.py::WorkflowConfig`
  - `src/percell4/workflows/artifacts.py::read_run_config` / `write_run_config`
  - `src/percell4/workflows/phases.py::segment_one`, `measure_one`, `export_run`
  - `src/percell4/domain/segmentation/postprocess.py::filter_edge_cells`
  - `src/percell4/gui/workflows/base_runner.py::BaseWorkflowRunner`
  - `src/percell4/gui/workflows/single_cell/runner.py::SingleCellThresholdingRunner._phase_generator`
  - `src/percell4/gui/workflows/single_cell/config_dialog.py::WorkflowConfigDialog`
  - `src/percell4/gui/workflows/single_cell/threshold_qc_queue.py::ThresholdQCQueueEntry`
  - `src/percell4/gui/workflows/dilute_phase/controller.py::DilutePhaseMaskController`
- Institutional learnings referenced:
  - `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md`
  - `docs/solutions/architecture-patterns/atomic-write-contract.md`
  - `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
  - `docs/solutions/architecture-patterns/consolidate-canonical-state-over-per-module-overrides-2026-05-14.md`
  - `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`
  - `docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md`
  - `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`
  - `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`
  - `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`
  - `docs/solutions/logic-errors/grouped-thresholding-development-lessons.md` (Items 3, 6)
