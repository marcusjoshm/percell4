---
title: Analysis Integration — registered analyses + first migration
type: feat
status: completed
date: 2026-05-27
completed: 2026-05-28
origin: docs/brainstorms/2026-05-27-analysis-integration-requirements.md
---

# Analysis Integration — registered analyses + first migration

## Overview

Build a registered-analyses framework in `percell4` and migrate the first standalone script (`per_particle_analysis.py`) into it. Researchers map dataset layers to declared input roles in a dialog, pick parameters or a preset, and run the analysis across one or more `.h5` files. The Scripts tab — currently a stub — becomes the home for analyses. CSVs land in a per-run folder; image outputs go back into each dataset's `.h5`. The existing CLI keeps working with **numeric parity** (regression-test fixture) — same scientific output, allowing for column-name and float-precision differences between the CLI and the framework path.

The framework is the architectural commitment; the per-particle donut migration is the first instance that exercises it. See origin: `docs/brainstorms/2026-05-27-analysis-integration-requirements.md` for the product decisions; supplementary technical sketches live in `docs/brainstorms/ANALYSIS_INTEGRATION_PLAN.md` (its `[DECIDE]` markers are superseded by this plan).

---

## Problem Frame

PerCell4 generates image data and stores each experiment as one `.h5`. Downstream measurement scripts — including `per_particle_analysis.py` for donut background subtraction of P-body / SG fluorescence — operate on exported TIFFs with filename keywords (`Cap`, `P-body_mask`, `pnorm`, `SG_mask`, `sgnorm`, `cp_mask`). Researchers have to export, rename, and run outside the tool. The pipeline disconnects at the most important point — measurement.

This plan closes the loop: each analysis declares its input roles, output kinds, parameters, and presets as class-level data. A registry collects them. A single runner validates layer maps, loads `numpy` arrays from `.h5`, calls the analysis's pure `run()`, and routes outputs (CSVs to a run folder, image layers back into each dataset). A schema-driven dialog renders the inputs/parameters as widgets so researchers never edit YAML or filenames.

---

## Requirements Trace

- R1. Analyses are declared as `Analysis` subclasses with class-level schema (required inputs, input groups, optional inputs, parameters, presets, outputs with `produced_when` predicates).
- R2. A registry validates each schema at registration; bad schemas raise with role/group/param names in the message.
- R3. Presets are content-hash-enforced — changing a preset's value without renaming raises at import; new keys are allowed.
- R4. The framework loads layers from `.h5` into `numpy` arrays with dtype coercion (binary → bool, labels → int, float → float64) before calling `run()`.
- R5. `run(inputs, params) → outputs` is pure: one dataset per call, no I/O, no iteration.
- R6. `run_analysis(analysis_name, h5_path, layer_map, params=None, preset=None) → outputs` is the single entry point; returns outputs without writing them.
- R7. `per_particle_analysis.py` is refactored in two passes — remove inner-function I/O (Phase A), extract a pure `run_one_image_set` (Phase B). CLI numeric parity (drop ID columns, sort by stable key, integer exact-equal + float `np.allclose(rtol=1e-10)`) on a regression fixture before and after.
- R8. A `PerParticleDonut(Analysis)` class wires the refactored logic into the framework. Roles, groups, params, and the `m7g-cap-v1` preset migrate verbatim.
- R9. Outputs of the first analysis: two `TableOutput` (pbody, sg) and two optional `ImageOutput` (donut masks) gated on `export_donuts` parameter.
- R10. Analyses live under the Scripts tab; Workflows tab unchanged. Dialog is populated dynamically from the registry.
- R11. Dialog renders one widget per declared role and parameter. Strict preset mode: parameters disable when a preset is selected.
- R12. `BoolParam.requires` declarations gate parameter availability (e.g. `single_cell` requires `cp_mask`).
- R13. Batch runner iterates over selected `.h5` datasets; the runner — not the analysis — attaches a `dataset` column to table rows.
- R14. Output destinations: CSVs to `run_<timestamp>/` folder under user-chosen output parent (combined + per-dataset). Image outputs back into each `.h5` (`/masks/<name>` or `/labels/<name>`). `run_config.json` captures provenance.
- R15. Per-dataset failures isolate. End-of-run summary dialog reports counts and per-failure messages.
- R16. Existing CLI continues to work unchanged from the user's perspective. Regression-test fixture verifies numeric parity (per R7) before and after each refactor step.
- R17. PerCell4's dataset format is not changed. Image outputs use canonical store-write APIs.

**Origin actors:** A1 (Researcher), A2 (Analysis author).
**Origin flows:** F1 (Configure and run an analysis, GUI), F2 (Headless re-run via Python API / preserved CLI).

---

## Scope Boundaries

- No yaml workflow loader (the original ANALYSIS_INTEGRATION_PLAN.md §6 proposal is superseded). Analyses are a separate surface from `BaseWorkflowRunner` workflows.
- No napari coupling in analysis logic. Analyses run headless.
- No hot-loading plugin marketplace. Discovery is via in-repo `@register_analysis` decorator at import time. Future package-entry-point discovery is allowable but not in v1.
- No 3D inputs in the first migration. `ImageRole.ndim` is in the framework so future analyses can declare 3D, but `PerParticleDonut` declares `ndim=(2,)`.
- No reimplementation of analysis math. Donut algorithm, background subtraction, single-cell aggregation are preserved verbatim from the original script.
- The empty `src/percell4/plugins/` package is NOT repurposed here. It remains scaffolding for an eventual external plugin-discovery surface that's out of scope for this plan.

### Deferred to Follow-Up Work

- **Per-cell analysis results integrated into the dataset's measurements DataFrame.** When `single_cell` is on, results stay CSV-only for v1. Future iteration could append rows into the single_cell workflow's parquet store. Out of scope here.
- **Second migration target.** No specific second script named today. Framework edges are partly speculative; a second migration will stress-test them.
- **`percell4-batch-analysis` CLI.** A future PerCell4-style headless CLI mirroring the GUI. The existing `per_particle_analysis.py` CLI is the v1 headless surface.
- **Workflow-step composition.** Chaining multiple analyses in a pipeline. Single-analysis runs cover immediate needs.

---

## Context & Research

### Relevant Code and Patterns

- **`src/percell4/store.py`** — `DatasetStore.list_masks()`, `list_labels()`, `list_groups(prefix)`, `metadata.channel_names`. The dialog's layer-dropdown population reads from these.
- **HDF5 layer conventions** — channels at `/intensity` (single group) or `/decay/<name>` (FLIM, per-channel); masks at `/masks/<name>`; labels at `/labels/<name>`; phasor at `/phasor/<ch>/{g,s,...}`; metadata at `/metadata`. The layer-map dialog picks from these well-known groups.
- **`src/percell4/workflows/artifacts.py`** — existing `RunMetadata` + `run_config.json` round-trip used by `single_cell`. The analysis batch runner mirrors this shape (folder name, JSON layout) where it can.
- **`src/percell4/gui/flim_fret_dialog.py`** — primary dialog pattern. Modal `QDialog` with inline `QProgressDialog`-driven per-item loop on the main thread, `QApplication.processEvents()` between items, `QMessageBox` summary at end. NO `BaseWorkflowRunner`. Same shape the analysis dialog adopts.
- **`src/percell4/gui/phasor_masks_dialog.py`** — secondary reference. Multi-row dataset list with per-row widgets, refresh-on-change pattern for derived state (channel intersection, layer-map validation), persistent fell-back indicators with Start guard. The analysis dialog will reuse these patterns.
- **`src/percell4/interfaces/gui/main_window.py::_create_scripts_panel`** (line ~319) — current Scripts tab. Has a `Run Script...` button and a "Macro System — coming soon" placeholder. U8 replaces this with a registry-driven entry list.
- **Existing batch-CLI patterns** — `percell4-batch-phasor`, `percell4-batch-phasor-masks`, `percell4-batch-rename`, `percell4-batch-delete`, `percell4-batch-whole-field`. The `BatchPhasorItemResult` / `BatchPhasorReport` dataclass shape (with the 4-state `succeeded` / `partial` / `skipped_no_changes` / `failed` taxonomy) is the canonical *per-channel* batch result. The analysis runner introduces its own `BatchAnalysisItemResult` / `BatchAnalysisReport` with a 3-state per-dataset taxonomy (`succeeded` / `failed` / `skipped`) because analyses are atomic at the dataset level — see U6 and Key Technical Decisions.
- **Test patterns** — synthetic-array tests in `tests/test_domain/` (e.g. `test_phasor_masks.py`), real-h5 integration tests in `tests/test_application/`, GUI dialog tests with `pytest-qt` in `tests/test_gui/`. The analysis tests follow these conventions.

### Institutional Learnings

- `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md` — mask-write Creator contract. Image outputs from analyses go through `store.write_mask` (step 1 of the contract). Steps 2–4 (viewer add-mask, session refresh, set-active) are skipped in batch mode; the dialog can emit one `session.refresh_resource_lists` at end-of-run if the active dataset was touched (same as phasor-masks workflow's pattern).
- `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md` — cross-layer alignment rule. Not directly relevant since this analysis takes pre-computed layers as inputs, but worth flagging: the analysis must not silently mix layers from different sources (e.g. `intensity` from one channel + `decay` from another). The layer-map declaration is per-role-per-dataset, so each role gets its own explicit layer path.
- `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md` — `qtbot.mouseClick` / `QTest.keyClick` driving real `activated` signals, not programmatic `setCurrentIndex`. Dialog tests follow this.
- `docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md` — the lesson that `(CLI, GUI)` parity needs an explicit test asserting both surfaces pass identical kwargs to the underlying logic. The analysis runner's tests cover this for GUI-vs-direct-Python-API.

### External References

None — the work is entirely inside established local patterns plus a new framework whose shape is defined by the origin doc.

---

## Key Technical Decisions

- **Hexagonal split for the framework:**
  - **Domain layer** (`src/percell4/domain/analysis/`): types (`ImageRole`, `IntParam`, etc.), `Analysis` base class, `register_analysis` decorator, pure-function implementations of registered analyses (under `domain/analysis/_impl/`).
  - **Application layer** (`src/percell4/application/analysis/`): global registry, HDF5 loader, `run_analysis()` entry point, batch runner, run-folder management. The actual registered modules (`PerParticleDonut(Analysis)`) live here because they're glue (declared schema + thin `run()` wrapper around the pure function).
  - **Interface layer** (`src/percell4/gui/`): a small library of shared widget factories (`analysis_widgets.py`) plus a per-analysis dialog (`per_particle_donut_dialog.py` for the first migration). `interfaces/gui/main_window.py` is modified to populate the Scripts tab from the registry.
  - The empty `src/percell4/plugins/` package is NOT touched. Its purpose is future external-plugin-loader scaffolding, not in-repo registered analyses.

- **`ImageRole.kind` declares what kind of layer a role accepts.** Three values: `"intensity"` (2D intensity image; accepts both `/intensity` channels by name AND `/decay/<name>` channels with on-the-fly sum-over-bins projection), `"mask"` (2D binary mask from `/masks/<name>`), `"label"` (2D integer label image from `/labels/<name>`). The layer-map values are simple strings the user chose in the dropdown (channel name OR mask name OR label name); the LOADER (U3) uses the role's `kind` to dispatch to the right `DatasetStore` read API. This resolves the HDF5-layout problem: `/intensity` is a 3D `(C, H, W)` Dataset, not a group, so the loader uses `metadata.channel_names` to resolve the chosen name to a channel index and reads via `store.read_channel(channel_idx)`. For decay channels, the loader reads `store.read_decay(name)` and projects via `.sum(axis=-1)` to 2D before returning.

- **`produced_when` is a Python callable, not a string DSL.** Analyses declare `produced_when: Callable[[GroupState, ParamDict], bool] | None = None` on each output. A `lambda groups, params: groups["pbody"]` is equally auditable as a string DSL, type-checkable, eliminates the `predicate.py` module entirely, and avoids the suffix-grammar ambiguity (role names with hyphens like `P-body_mask`, names ending in `_supplied`, etc.). Bad callables fail at class-definition time with normal Python errors. The `GroupState` is a small dataclass exposing `.<group_name>_satisfied` and `.<role_name>_supplied` booleans the caller can read directly.

- **Per-analysis dialog with shared widget factories** (not a single generic schema-driven dialog). Each registered analysis declares a `dialog_class: type[QDialog]` as a class attribute. The Scripts tab calls `registry.get(name).dialog_class(...)`. A shared `gui/analysis_widgets.py` module exports reusable factories (`role_picker_row`, `param_widget`, `dataset_list_widget`, `output_summary_row`) that any per-analysis dialog can compose. For the first migration: `PerParticleDonutDialog(QDialog)` is the only dialog. When a second analysis arrives, its dialog reuses the same factories. The shared abstractions get earned by a second concrete example, not speculated against zero examples. This is structurally what `PhasorMasksDialog` (1217 lines) already shows works.

- **Presets are in-code constants with a snapshot test, not content-hash-enforced.** Each preset is a module-level dict declared inside the analysis class. A test in `tests/test_application/test_presets_immutable.py` asserts the current `m7g-cap-v1` dict content matches a committed snapshot file (`tests/fixtures/preset_snapshots/per_particle_donut.json`). If a developer changes a preset value, the test fails until they either (a) revert OR (b) explicitly update the snapshot (which shows up in PR review). No import-time hashing, no write-back race, no committed JSON in the production codepath. Same property (tamper detection) without the developer-workflow friction.

- **Layer dropdowns show the intersection across selected datasets** (matching the phasor-masks workflow pattern). When the user adds a dataset that lacks a chosen layer, the dropdown's existing selection is reset to a sentinel item — a non-selectable `"— select a layer —"` entry at index 0 — and a status-label message names what changed. The dataset list row gets a per-dataset warning indicator (small amber dot or `[missing: <role>]` parenthetical) for any dataset that lacks a layer the user has selected in another dataset's intersection. This surfaces missing-layer information BEFORE Start, instead of only in the post-run summary.

- **Refresh-on-change for dialog state.** Every editable widget (dataset list, layer dropdown, param spinbox, suffix line edit, preset dropdown) connects to `_refresh_state()`, which re-validates the `BoolParam.requires` gating, the group satisfaction logic, the preset-lock state of param widgets, and Start enable. Same shape as PhasorMasksDialog. Programmatic `setCurrentIndex` does NOT trigger the cascade (per qt-wire-user-edit-signals).

- **New `BatchAnalysisItemResult` dataclass, NOT a reuse of `BatchPhasorItemResult`.** The phasor result type has per-channel `partial` semantics that don't fit analyses (an analysis's `run()` is atomic — it either returns the full output dict or raises). The analysis runner uses a 3-state taxonomy: `succeeded` (analysis ran cleanly), `failed` (dataset-level error or `run()` raised), `skipped` (group-requirement not satisfied at runtime; shouldn't happen if dialog validation works, but defensive). The dataclass carries `produced_outputs: tuple[str, ...]` (output names actually returned by `run()`, after `produced_when` filtering), `error: str | None`. Lives in `application/analysis/types.py`.

- **Run folder layout mirrors `single_cell` workflow** (see `src/percell4/workflows/artifacts.py`). Folder name: `run_<YYYY-MM-DD_HH-MM-SS>/`. Inside: `combined_<output_name>.csv` (all datasets), `per_dataset/<dataset_stem>_<output_name>.csv` (one per dataset), `run_config.json` (provenance). Image outputs go into each `.h5` directly via `store.write_mask` / `store.write_labels` per the output's declared type.

- **`run_config.json` provenance scope (v1):** analysis name + version, preset name + content-hash (if preset used) OR explicit params dict (if no preset), layer map keyed by resolved dataset path, list of resolved dataset paths, ISO timestamp, percell4 version (from `pyproject.toml`). Excluded from v1: host info, full library version listing. Add later if reproducibility issues surface.

- **Image outputs write through canonical store APIs.** Donut masks → `store.write_mask(name, array)`. The Creator contract's steps 2–4 (viewer add-mask, session refresh, set-active) are skipped in batch mode; the dialog emits ONE `session.refresh_resource_lists(...)` at end-of-run when the active dataset's path is among the processed paths (same pattern as phasor-masks workflow).

- **Phased delivery: framework + migration → user-facing surface.** Two phases (originally three; consolidated because U4 has no framework dependency and naturally lands in Phase 1 alongside the framework that consumes it).
  - **Phase 1 — Framework + first migration usable from Python** (U1, U3, U4, U5; U2 deliberately absent — see Implementation Units). After phase 1, a developer can write a new `Analysis` subclass, register it, and call `run_analysis(...)` from a notebook against a real `.h5`. CLI parity test passes. No GUI surface yet.
  - **Phase 2 — User-facing surface** (U6, U7, U8). Batch runner + per-analysis dialog + Scripts-tab wiring. After phase 2, a researcher uses the Scripts tab end-to-end.

- **Cancellation semantics: no resume-from-checkpoint.** On cancel mid-batch, datasets processed before cancel have their image outputs persisted in their `.h5`; the run folder contains CSVs only for those datasets. Re-running with the same dataset list **re-processes from the beginning** — image outputs overwrite idempotently, but the user pays the recomputation cost. There's NO resume-from-where-left-off mechanism. Documented in U6 docstring + the `run_config.json` `status` field includes `"cancelled"`. Future iteration could add a `--skip-existing` flag that checks output presence and skips; out of scope for v1.

- **CLI parity test verifies numeric values, not byte-identical CSVs.** The CLI emits a `group` column with the image-set key; the runner emits a `dataset` column with the `.h5` path stem. These are different. Plus the CLI reads TIFFs (native dtype, often float32) while the runner reads from `.h5` with `float → float64` coercion, so floating-point arithmetic produces different LSBs. The honest parity test: (1) drop the `group`/`dataset` column from both outputs, (2) sort rows by `pbody_id` / `sg_id` for stable ordering, (3) compare integer columns with exact equality, (4) compare float columns with `np.allclose(rtol=1e-10)`. The "byte-identical" claim in U4/U5/R16 is replaced with "numeric parity" — same scientific guarantee (the CLI and framework compute the same values), without the false-precision overpromise.

---

## Open Questions

### Resolved During Planning

- **Exact package paths.** Resolved: hexagonal split (`domain/analysis/`, `application/analysis/`, per-analysis dialog files under `gui/`, shared widget factories in `gui/analysis_widgets.py`). `plugins/` untouched.
- **`produced_when` evaluator.** Resolved during doc-review: Python callable, not a string DSL. Eliminates `predicate.py` entirely. Bad callables fail at class-definition time with normal Python errors. Lexer ambiguity (role names with hyphens, suffix collisions) dissolves.
- **HDF5 layer-layout for non-FLIM channels.** Resolved during doc-review: `ImageRole.kind` declares what kind of layer the role accepts (`"intensity"` / `"mask"` / `"label"`). The loader uses the kind to dispatch the right `DatasetStore` read API. `/intensity` is a 3D `(C, H, W)` Dataset, not a group — the loader resolves channel names via `metadata.channel_names` to indices and reads via `store.read_channel(channel_idx)`. For decay channels assigned to `kind="intensity"` roles, the loader sums-over-bins to project to 2D.
- **Layer-dropdown content with multiple datasets.** Resolved: intersection. Stale-selection sentinel item (`"— select a layer —"`) at index 0 when a prior selection is no longer valid after a dataset add. Per-dataset warning indicator in the dataset list row when one dataset lacks a layer present in others.
- **Dialog state rebuild on changes.** Resolved: refresh-on-change pattern from PhasorMasksDialog.
- **`run_config.json` provenance scope.** Resolved: analysis name + version, preset name + the in-code preset dict (since we dropped hash enforcement, the preset values themselves are recorded), layer map per dataset, dataset list, timestamp, percell4 version, `status` field (`"completed"` / `"cancelled"`), `completed_dataset_count`.
- **Per-analysis dialog vs. generic schema-driven dialog.** Resolved during doc-review: per-analysis dialog with shared widget factories. `PerParticleDonutDialog(QDialog)` is the only dialog in v1. Generic abstraction earned by a second concrete example, not speculated.
- **Preset immutability mechanism.** Resolved during doc-review: in-code constants + snapshot test (`tests/test_application/test_presets_immutable.py` + `tests/fixtures/preset_snapshots/per_particle_donut.json`). No import-time hashing, no committed `preset_hashes.json`, no write-back race.
- **Item result dataclass.** Resolved during doc-review: new `BatchAnalysisItemResult` in `application/analysis/types.py`. 3-state taxonomy (`succeeded` / `failed` / `skipped`) plus `produced_outputs: tuple[str, ...]`. Drops the `partial` state that didn't apply to atomic `run()` calls. Does NOT reuse `BatchPhasorItemResult`.
- **CLI parity claim.** Resolved during doc-review: numeric parity test (drop dataset/group identifier columns, sort rows by stable key, integer columns exact-equal, float columns `np.allclose(rtol=1e-10)`). NOT byte-identical — that's not achievable given column-name differences and float dtype-coercion differences.
- **Script location.** Resolved during doc-review: COPY `per_particle_analysis.py` into the percell4 repo at root (`percell4/per_particle_analysis.py`). External repo gets a deprecation note pointing to the new location. U4's first step is the copy.

### Deferred to Implementation

- **End-of-run viewer refresh for ImageOutputs.** When the user has the active dataset open and the analysis writes new masks back, the viewer should auto-refresh. Mirrors phasor-masks workflow's one-shot `refresh_resource_lists` at end-of-run if the active dataset was touched. Confirm during U6 / U7. Defensive None-guards for `session.dataset` (user closed dataset mid-run) added in U7.
- **Output_parent validation timing.** Validate on Browse-button click AND on Start-click (defense in depth). Exact UX (red inline label below QLineEdit on invalid Browse selection; QMessageBox.critical on invalid Start-click) decided during U7.

---

## Output Structure

```
src/percell4/
├── domain/
│   └── analysis/                            # NEW
│       ├── __init__.py
│       ├── types.py                         # U1: ImageRole (with kind/dtype/ndim),
│       │                                    #     IntParam, BoolParam, ..., GroupState
│       ├── base.py                          # U1: Analysis class, @register_analysis
│       └── _impl/                           # NEW: pure-function impls of registered analyses
│           ├── __init__.py
│           └── per_particle_donut.py        # U4: refactored run_one_image_set
├── application/
│   ├── analysis/                            # NEW
│   │   ├── __init__.py
│   │   ├── types.py                         # U6: BatchAnalysisItemResult / BatchAnalysisReport
│   │   ├── registry.py                      # U1: global registry + schema validation
│   │   ├── loader.py                        # U3: HDF5 layer (kind-dispatched) -> ndarray
│   │   ├── run_folder.py                    # U6: reuses workflows.artifacts helpers
│   │   └── modules/
│   │       ├── __init__.py                  # U5: imports each registered module to fire decorators
│   │       └── per_particle_donut.py        # U5: PerParticleDonut(Analysis), schema-only
│   └── use_cases/
│       ├── run_analysis.py                  # U3: run_analysis() single-dataset entry
│       └── run_analysis_batch.py            # U6: batch over many .h5 + run folder
├── gui/
│   ├── analysis_widgets.py                  # U7: shared widget factories
│   │                                        #     (role_picker_row, param_widget,
│   │                                        #      dataset_list_widget, output_summary_row)
│   └── per_particle_donut_dialog.py         # U7: PerParticleDonutDialog(QDialog)
└── interfaces/
    └── gui/
        └── main_window.py                   # U8: Scripts tab wired to registry

src/percell4/  # NOT touched:
└── plugins/                                 # stays empty scaffolding

per_particle_analysis.py                     # U4: COPIED from external repo into percell4
                                             #     root. External repo deprecated with note.

tests/
├── test_domain/
│   ├── test_analysis_types.py               # U1
│   └── test_per_particle_donut_pure.py      # U4: synthetic-array tests of run_one_image_set
├── test_application/
│   ├── test_analysis_registry.py            # U1: registration + schema validation
│   ├── test_analysis_loader.py              # U3
│   ├── test_run_analysis.py                 # U3+U5: end-to-end on small .h5 fixture
│   ├── test_run_analysis_batch.py           # U6
│   └── test_presets_immutable.py            # U5: snapshot test for preset values
├── test_gui/
│   ├── test_analysis_widgets.py             # U7: shared widget factories
│   └── test_per_particle_donut_dialog.py    # U7: pytest-qt for the donut dialog
└── fixtures/
    ├── per_particle/                        # U4: regression fixture (tifs + expected CSV)
    └── preset_snapshots/
        └── per_particle_donut.json          # U5: committed preset values for snapshot test
```

**What's gone vs. v1 of this plan:**
- `predicate.py` — removed (Python callables instead of DSL).
- `preset_hashes.json` in production code — removed (snapshot test in tests/fixtures/ instead).
- `analysis_dialog.py` (generic) — replaced by per-analysis dialogs + shared widget factories.

---

## High-Level Technical Design

> *This illustrates the schema-to-dialog-to-runner data flow and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
Registered analysis (declared at module-import time):

  @register_analysis("per_particle_donut")
  class PerParticleDonut(Analysis):
      required_inputs = {"Cap": ImageRole(...)}
      input_groups = {"pbody": {...}, "sg": {...}}
      group_requirement = "at_least_one"
      optional_inputs = {"cp_mask": ImageRole(...)}
      parameters = {"buffer": IntParam(default=4, min=0), ...}
      presets = {"m7g-cap-v1": {...}}
      outputs = {"pbody_table": TableOutput(produced_when=lambda g, p: g.pbody_satisfied), ...}
      dialog_class = PerParticleDonutDialog          # the analysis owns its dialog
      def run(self, inputs, params): return run_one_image_set(**inputs, **params)


When the user opens the Scripts tab:

  ┌─────────────────────────────────────────────────────────────┐
  │  Scripts tab → reads registry.list_analyses()                │
  │   • Per-particle donut analysis     [opens cls.dialog_class] │
  └─────────────────────────────────────────────────────────────┘

When the user clicks an analysis entry:

  ┌─────────────────────────────────────────────────────────────┐
  │  PerParticleDonutDialog (analysis-specific QDialog)          │
  │   • Hand-laid layout for THIS analysis (header + skip-able   │
  │     P-body / SG groups + optional cp_mask + params + preset  │
  │     + outputs panel + output-parent picker)                  │
  │   • Reuses gui/analysis_widgets.py factories for shared bits │
  │     (dataset picker, layer combos, param widgets, preset     │
  │     combo, output-parent picker)                             │
  │   • Layer dropdowns populated by populate_layer_combo from   │
  │     DatasetStore.list_masks() / list_labels() / channel_names│
  │   • Intersection across all selected datasets, with a stale- │
  │     selection sentinel if the dataset list changes           │
  │   • Refresh-on-change: any USER widget edit → _refresh_state │
  │     which re-validates requires-gating, preset lock,         │
  │     output gating, and Start enable+tooltip                  │
  └─────────────────────────────────────────────────────────────┘

On Start click → batch runner:

  for h5_path in selected_paths:
      result = run_analysis(
          "per_particle_donut",
          h5_path,
          layer_map=dialog.layer_map_for(h5_path),
          params=dialog.params,
          preset=dialog.preset,
      )
      # Write image outputs into h5_path via store.write_mask / write_labels
      # Append table rows to accumulators with `dataset` column
      # Per-dataset summary line via progress_callback

  # After loop:
  # Write combined CSVs + per_dataset CSVs to run_<ts>/
  # Write run_config.json
  # Show summary QMessageBox

Inside run_analysis():

  cls = registry.get(analysis_name)
  validate_layer_map(cls, layer_map)            # role coverage, group satisfaction
  resolved_params = resolve_preset_or_params(cls, params, preset)
  arrays = loader.load(h5_path, layer_map, cls.role_dtypes())
  outputs = cls().run(arrays, resolved_params)
  produced = evaluate_produced_when(cls.outputs, group_state, params=resolved_params)
  validate_outputs(outputs, declared=cls.outputs, produced=produced)
  return outputs
```

---

## Implementation Units

- U1. **Framework types + `Analysis` base + registry**

**Goal:** Foundation layer. Declared types (`ImageRole` with `kind` / `dtype` / `ndim` fields, `IntParam`, `FloatParam`, `BoolParam`, `ChoiceParam`, `TableOutput`, `ImageOutput`, `GroupState` dataclass exposing `.<name>_satisfied` / `.<name>_supplied` for `produced_when` callables to read), `Analysis` base class with class-level schema attributes including `dialog_class: ClassVar[type[QDialog] | None] = None`, `@register_analysis` decorator, and the global registry with schema validation (parameter coverage in presets, role-name collision detection, valid `group_requirement`, declared output names unique, `BoolParam.requires` resolves to known roles/groups). NO `produced_when` DSL — outputs declare `produced_when` as a Python callable that the runner invokes directly.

**Requirements:** R1, R2.

**Dependencies:** None.

**Files:**
- Create: `src/percell4/domain/analysis/__init__.py`
- Create: `src/percell4/domain/analysis/types.py`
- Create: `src/percell4/domain/analysis/base.py`
- Create: `src/percell4/application/analysis/__init__.py`
- Create: `src/percell4/application/analysis/registry.py`
- Test: `tests/test_domain/test_analysis_types.py`
- Test: `tests/test_application/test_analysis_registry.py`

**Approach:**
- `types.py`: frozen dataclasses, no runtime dependencies beyond `dataclasses` + `typing` + `collections.abc.Callable`. `BoolParam.requires: tuple[str, ...]` field for parameter gating. `Output.produced_when: Callable[[GroupState, dict[str, Any]], bool] | None = None` — a Python callable evaluated at run-time against the current group state + resolved params. `None` means "always produced." `GroupState` is a dataclass with one boolean attribute per declared input group (`<group_name>_satisfied`) and one per role (`<role_name>_supplied`); the runner constructs it from the layer map. Mirror the role/param/output shapes in the brainstorm `ANALYSIS_INTEGRATION_PLAN.md` §4.1, except `produced_when` is a callable rather than a string DSL.
- `base.py`: `Analysis` base class with `ClassVar` typed schema attributes and an abstract `run()` method. Subclasses inherit empty defaults and override what they declare. `register_analysis(name)` decorator that adds the class to the registry's module-level dict.
- `registry.py`: module-level `_REGISTRY: dict[str, type[Analysis]]`. `get(name) -> type[Analysis]`. `list_analyses() -> list[AnalysisInfo]` (lightweight dataclass with name, display name, input/param summaries — used by the Scripts tab to populate entries). Schema-validation function called from `register_analysis` raises on bad inputs.

**Execution note:** Implement test-first. The class-level schema attributes are unusual enough that pinning their shape with tests is the cleanest path.

**Patterns to follow:**
- `src/percell4/domain/segmentation/phasor_masks.py` — pure-domain dataclass shape (`PhasorEllipseFit`, `PhasorEllipseMasks`).
- The brainstorm's `ANALYSIS_INTEGRATION_PLAN.md` §4.1 and §4.2 — type and class sketches.

**Test scenarios:**
- *Happy path.* Declare a minimal `Analysis` subclass with one `required_input`, one `parameter`, one `TableOutput`, no presets. `@register_analysis("test_stub")` registers it; `registry.get("test_stub")` returns it.
- *Happy path: list_analyses summary shape.* Register two stubs; `list_analyses()` returns a list of `AnalysisInfo` with name + display_name + a summary of inputs/params (string form). Pins the shape the Scripts tab will consume.
- *Edge case: empty groups dict.* Subclass declares only required_inputs (no `input_groups`). Registration succeeds. `group_requirement` is irrelevant when no groups exist.
- *Error path: duplicate registration.* Registering two analyses with the same name raises `ValueError("Analysis 'foo' already registered")`.
- *Error path: role-name collision.* `required_inputs` has a key that also appears inside an `input_groups` group, or two groups share a role name. Registration raises with the conflicting name in the message.
- *Error path: preset references unknown parameter.* Preset dict has key `"buffer"` but the class doesn't declare `buffer` as a parameter. Registration raises.
- *Error path: invalid `group_requirement`.* Value other than `"at_least_one"` / `"exactly_one"` / `"all"`. Raises at registration.
- *Edge case: BoolParam.requires references a real role.* A `BoolParam` with `requires=("cp_mask",)` registers cleanly if `cp_mask` is an `optional_inputs` or `required_inputs` key.
- *Error path: BoolParam.requires references unknown role.* Raises at registration with the unknown name.
- *Edge case: Analysis subclass without `run()` override.* Calling `.run()` raises `NotImplementedError`. (Verifies abstract behavior.)

**Verification:**
- All tests pass.
- `from percell4.domain.analysis import ImageRole, IntParam, ...` works.
- `from percell4.application.analysis.registry import register_analysis, get, list_analyses` works.

---

*(Note: U2 — originally a `produced_when` predicate parser + preset hash enforcement unit — is deliberately absent. Both were eliminated during doc-review: `produced_when` becomes a Python callable (no DSL, no parser), presets use a snapshot test (covered by U5's `tests/test_application/test_presets_immutable.py`) instead of import-time content hashing. Per the U-ID stability rule, the gap stays.)*

- U3. **HDF5 loader + `run_analysis` entry point**

**Goal:** Bridge the framework to actual `.h5` files. Given a layer map (role → h5 layer path) and a registered analysis's role declarations, the loader reads each layer with dtype coercion and shape validation. The runner is the single Python-API entry point: validate inputs, resolve preset/params, load arrays, call `run()`, validate outputs.

**Requirements:** R4, R5, R6.

**Dependencies:** U1.

**Files:**
- Create: `src/percell4/application/analysis/loader.py`
- Create: `src/percell4/application/use_cases/run_analysis.py`
- Test: `tests/test_application/test_analysis_loader.py`
- Test: `tests/test_application/test_run_analysis.py`

**Approach:**
- `loader.py`: function `load_layers(h5_path: Path, layer_map: dict[str, str], roles: dict[str, ImageRole]) -> dict[str, np.ndarray]`. Opens the `.h5` via `DatasetStore`, then for each role dispatches by `role.kind`:
  - `kind="intensity"`: resolve `layer_map[role]` against `metadata.channel_names` to get a channel index, then read via `store.read_channel(channel_idx)`. If the chosen name is NOT in `channel_names` but IS in `store.list_groups("decay")`, read via `store.read_decay(name)` and project to 2D via `.sum(axis=-1)`. Either way the result is a 2D float64 array.
  - `kind="mask"`: read `/masks/<name>` via `store.read_array(f"masks/{name}")`; coerce to bool via `(arr > 0).astype(bool)`.
  - `kind="label"`: read `/labels/<name>` via `store.read_labels(name)`; coerce to int32 via `.astype(np.int32)`.
  - Validates `ndim` if declared on the role (defaults to 2D for all v1 kinds). Missing layers raise `LayerNotFoundError(role, layer_name)`. Channel-name lookup failure (name not in `channel_names` and not in `list_groups("decay")`) raises `LayerNotFoundError`.
- `run_analysis.py`: function `run_analysis(analysis_name, h5_path, layer_map, params=None, preset=None) -> dict[str, Any]`. Order: (1) registry lookup, (2) compute `GroupState` from layer_map keys (group N satisfied iff every role in that group has a layer_map entry; each role.supplied = (role in layer_map)), (3) check `group_requirement` against `GroupState`, (4) resolve preset+params (strict: error if both given), (5) check every `BoolParam.requires` against group/role state, (6) call `load_layers`, (7) call `cls().run(arrays, resolved_params)`, (8) compute `produced` set by invoking each `Output.produced_when(group_state, resolved_params)` (Python callable; outputs with `produced_when=None` are always produced), (9) validate returned dict keys ⊆ `produced` and value types match declared `TableOutput` (`pd.DataFrame`) / `ImageOutput` (`np.ndarray`).
- Errors at any step raise with the offending role/param/output name in the message and `h5_path` for context.

**Execution note:** Implement test-first. End-to-end test on a stub analysis with a small synthetic `.h5` fixture is the most valuable single test.

**Patterns to follow:**
- `src/percell4/store.py::DatasetStore` — use `read_array(path)` / `read_decay(channel)` / `read_labels(name)` for layer reads.
- `src/percell4/application/use_cases/batch_fit_phasor_masks.py` — overall use-case shape (input validation, normalized paths, registry-style lookup).

**Test scenarios:**
- *Happy path: simple stub.* Register a stub `Analysis` with one required input (`x: ImageRole(dtype="float")`), one param, one `TableOutput`. Build a small synthetic `.h5` with `/some/x` = `np.ones((4,4), float32)`. Call `run_analysis("stub", h5_path, {"x": "some/x"}, {})`. Returns `{"table": pd.DataFrame(...)}`.
- *Happy path: dtype coercion.* Stub declares `mask: ImageRole(dtype="binary")`. `.h5` has `/m` = `np.array([[0, 5], [0, 7]], int16)`. Loader returns `mask` as `bool` array `[[F, T], [F, T]]`.
- *Happy path: dtype labels.* Stub declares `labels: ImageRole(dtype="labels")`. `.h5` has `/l` = `np.array(..., uint16)`. Loader returns int (no scaling).
- *Edge case: ndim validation.* Stub declares `ndim=(2,)`. `.h5` has a 3D array. Loader raises `LayerDtypeError`-style error naming the role and actual shape.
- *Error path: layer not found.* `.h5` lacks the requested path. Raises `LayerNotFoundError` with role + path.
- *Error path: group_requirement.* Stub has two groups, `group_requirement="exactly_one"`. Caller supplies layers for both groups → raises. Caller supplies for neither → raises.
- *Error path: BoolParam.requires unmet.* Param `single_cell=True` but no `cp_mask` in the layer_map. Raises with `single_cell` and `cp_mask` named.
- *Error path: preset + overlapping params.* Caller supplies both `preset="m7g-cap-v1"` and `params={"buffer": 9}` → raises (strict, no overrides).
- *Edge case: optional input absent.* `cp_mask` is declared optional, layer_map doesn't include it. Loader returns arrays without `cp_mask`; `run()` sees no `cp_mask` key in inputs.
- *Integration: produced_when filtering.* Stub has two outputs, one with `produced_when="x_supplied"` and one with `"y_supplied"`. Layer_map provides `x` but not `y`. Returned dict has only the `x`-gated output; the runner does NOT raise on missing `y`-gated output because it's not produced.
- *Error path: run() returns undeclared output.* Stub returns `{"surprise": 1}` not in declared outputs. Runner raises with the unexpected key named.

**Verification:**
- All tests pass.
- `run_analysis()` callable from a Python REPL against a real `.h5` (manual smoke is acceptable for this layer).

---

- U4. **Refactor `per_particle_analysis.py` — pure `run_one_image_set`, regression fixture**

**Goal:** Refactor the existing standalone script to (1) remove file I/O from inner functions, (2) extract a pure `run_one_image_set(arrays, params) -> dict` function, (3) preserve **CLI numeric parity** via a regression-test fixture: drop ID columns, sort rows by stable key, integer columns exact-equal, float columns `np.allclose(rtol=1e-10)`. The pure function lands at `src/percell4/domain/analysis/_impl/per_particle_donut.py`; the CLI script imports it.

**Requirements:** R7, R16.

**Dependencies:** None (independent of the framework; can land before U1–U3 if desired, but listed here in phased order).

**Files:**
- Create: `per_particle_analysis.py` (at percell4 repo root — **copied from** `~/mask-intensity-analysis-repo/per_particle_analysis.py`)
- Create: `src/percell4/domain/analysis/_impl/__init__.py`
- Create: `src/percell4/domain/analysis/_impl/per_particle_donut.py`
- Create: `tests/test_domain/test_per_particle_donut_pure.py`
- Create: `tests/fixtures/per_particle/` (input TIFFs + expected CSV files for regression test)

**Approach:**
- **Step 0 (acquire):** COPY `per_particle_analysis.py` from `~/mask-intensity-analysis-repo/` to `percell4/per_particle_analysis.py` (repo root, alongside `main.py`). Add a deprecation note to the README of the external repo pointing to the new location. From this point forward, the canonical home of the script is the percell4 repo.
- **Step 0.5 (regression fixture, BEFORE any refactor):** Create `tests/fixtures/per_particle/` with 2 small image sets (`group_a/{Cap.tif, P-body_mask.tif, pnorm.tif}` for P-body-only, `group_b/{Cap.tif, P-body_mask.tif, pnorm.tif, SG_mask.tif, sgnorm.tif, cp_mask.tif}` for both modes + single-cell). Generate expected CSV files by running the just-copied CLI against the fixture and committing the outputs. The regression test invokes `python per_particle_analysis.py --data-dir tests/fixtures/per_particle/group_a --output-pbody /tmp/out_p.csv --output-sg /tmp/out_s.csv` (via subprocess), compares against the committed expected files using the **numeric parity test** (drop the `group` column, sort rows by `pbody_id`/`sg_id`, integer columns exact-equal, float columns `np.allclose(rtol=1e-10)`). Run this test before and after every subsequent step.
- **Step 1 (refactor — no behavior change):** Move `mask_img = tifffile.imread(mask_path)` out of `analyze_regions` and `assign_particles_to_cells`. They now take `mask_img: np.ndarray` directly. The CLI reads the TIFF before calling them.
- **Step 2 (refactor — no behavior change):** Move the donut-TIFF write OUT of `analyze_regions`. The function returns `{"rows": [...], "donut_mask": np.ndarray | None}` instead. The CLI writes the TIFF when `--export-donuts` is set.
- **Step 3 (extract):** Pull everything currently inside the `for group_key, channels in sorted(groups.items()):` loop into a new pure function `run_one_image_set(*, cap, pbody_mask=None, pnorm=None, sg_mask=None, sgnorm=None, cp_mask=None, buffer, donut, bg_mode, bg_value, exclude_cap_zero, min_size, bgsub_k, no_bgsub, single_cell, export_donuts) -> dict`. This includes mode detection (has_pbody / has_sg), the global Cap background subtraction, the SG-before-P-body exclusion, and the optional single-cell aggregation. **No directory walks, no file I/O, no CSV writing inside.** Returns `{"pbody_rows": list[dict] | None, "sg_rows": list[dict] | None, "pbody_donut_mask": np.ndarray | None, "sg_donut_mask": np.ndarray | None}`.
- **Step 4:** Move `run_one_image_set` to `src/percell4/domain/analysis/_impl/per_particle_donut.py`. The CLI imports from there via `from percell4.domain.analysis._impl.per_particle_donut import run_one_image_set` (the script being inside the percell4 repo makes this import path valid).
- **CLI wrapper unchanged from the user's perspective:** parses args, walks the directory with `group_image_sets`, for each group reads TIFFs, calls `run_one_image_set`, attaches `group_key` to rows, writes donut TIFFs if requested, writes CSVs (combined or single-cell) at the end. Same flags, same defaults, same output column format.

**Execution note:** **Characterization-first.** Build the regression fixture BEFORE any refactor steps. Each refactor step (1, 2, 3, 4) re-runs the fixture; output must pass numeric parity. This is the safest path through a behavior-preserving refactor of a 787-line script.

**Patterns to follow:**
- `src/percell4/domain/segmentation/phasor_masks.py` — pure-domain function shape (kwargs-only, returns dataclass-or-dict, no I/O, no imports outside `numpy` + `scipy` + sibling domain primitives).

**Test scenarios:**

*Regression fixture (covers R7, R16):*
- *Happy path: CLI numeric parity.* Run `per_particle_analysis.py --data-dir tests/fixtures/per_particle/group_a --output-pbody /tmp/out_p.csv --output-sg /tmp/out_s.csv` before and after each refactor step. Output CSVs match the committed expected outputs under the numeric-parity test: drop the `group` column, sort rows by `pbody_id`/`sg_id`, integer columns exact-equal, float columns `np.allclose(rtol=1e-10)`. Repeat for `group_b` (both modes + single-cell with cp_mask). The pre-refactor commit of the expected outputs is the reference — Step 0.5 generates and commits them BEFORE Step 1 modifies any code.

*Pure-function unit tests (cover the new `run_one_image_set`):*
- *Happy path: P-body only.* Synthetic small arrays. `run_one_image_set(cap=..., pbody_mask=..., pnorm=..., **default_params)` returns `{"pbody_rows": [...], "sg_rows": None, "pbody_donut_mask": None, "sg_donut_mask": None}`. Verify row count matches connected components in `pbody_mask`.
- *Happy path: SG only.* `run_one_image_set(cap=..., sg_mask=..., sgnorm=..., **default_params)` returns SG rows only.
- *Happy path: Both modes with SG-exclusion.* Both masks + Cap. `run_one_image_set(...)` returns both `pbody_rows` and `sg_rows`. **Verify the SG-exclusion side effect:** P-body rows computed after SG-mask pixels in Cap have been NaN'd. Specifically, build a fixture where a P-body lies inside an SG region; assert its `cap_pbody_mean` is `NaN` (because all its Cap pixels were excluded).
- *Happy path: single_cell with cp_mask.* Per-cell aggregation produces one row per unique cell ID in `cp_mask`. Assert area-weighted means and summed integrals match hand-computed expectations on a small fixture.
- *Edge case: no particles after min_size filter.* `min_size=10000` → no particles survive. Returns `{"pbody_rows": [], ...}` (empty list, not None).
- *Edge case: cap with no signal.* Cap is all zeros. Background subtraction produces NaN for all pixels. Returns rows with `NaN` for all intensity columns; no crash.
- *Edge case: export_donuts.* When `export_donuts=True`, returned dict includes `pbody_donut_mask` and/or `sg_donut_mask` as `uint8` arrays. Otherwise those keys are `None`.

**Verification:**
- The regression fixture's expected CSV files are committed.
- CLI's CSV output passes the numeric-parity test against the committed expected outputs before and after each refactor step (CI guard).
- `run_one_image_set` is callable directly with `numpy` arrays.
- No `tifffile.imread` / `tifffile.imwrite` calls inside `run_one_image_set`.

---

- U5. **Register `PerParticleDonut` analysis module**

**Goal:** Wire the refactored pure function into the framework. Subclass `Analysis`, declare roles / groups / params / presets / outputs, implement `run()` as a thin wrapper around `run_one_image_set`. Commit a preset snapshot (`tests/fixtures/preset_snapshots/per_particle_donut.json`) and assert it matches the in-code preset values via `tests/test_application/test_presets_immutable.py`.

**Requirements:** R8, R9.

**Dependencies:** U1, U3, U4.

**Files:**
- Create: `src/percell4/application/analysis/modules/__init__.py`
- Create: `src/percell4/application/analysis/modules/per_particle_donut.py`
- Create: `tests/test_application/test_presets_immutable.py`
- Create: `tests/fixtures/preset_snapshots/per_particle_donut.json`
- Modify: `src/percell4/application/analysis/__init__.py` (add the import that fires the decorator at package-import time)
- Test: `tests/test_application/test_run_analysis.py` (extend with end-to-end test against a synthetic `.h5`)

**Approach:**
- `application/analysis/modules/per_particle_donut.py`: `@register_analysis("per_particle_donut")` decorates a `PerParticleDonut(Analysis)` subclass. Declares all roles/params/presets/outputs verbatim from the original script's argparse + the `m7g-cap-v1` preset. Sets `dialog_class = PerParticleDonutDialog` (forward import; the dialog is U7, but the attribute can be set after U7 lands or via a lazy property — defer the exact import shape to U7). `run(inputs, params)` builds a kwargs dict from `inputs.get(...)` for each optional role and `inputs[...]` for required, then calls `run_one_image_set(**inputs, **params)`. Wraps row lists into `pd.DataFrame` for the `TableOutput` returns. `produced_when` for each output is a `lambda groups, params: <expression>` — e.g., `pbody_table` declares `produced_when=lambda g, p: g.pbody_satisfied`; `pbody_donut_mask` declares `produced_when=lambda g, p: g.pbody_satisfied and p["export_donuts"]`.
- **Where the registration import happens:** the module must be imported for the `@register_analysis` decorator to fire. Add `from percell4.application.analysis.modules import per_particle_donut` to `application/analysis/__init__.py` so any import of `percell4.application.analysis` registers the bundled analyses. Future analyses follow the same pattern.
- **Preset snapshot test:** `tests/fixtures/preset_snapshots/per_particle_donut.json` is a committed JSON of the form `{"m7g-cap-v1": {"buffer": 5, "donut": 5, ...}}` exactly mirroring the in-code preset values. `tests/test_application/test_presets_immutable.py` reads both and asserts they match key-by-key. If a developer changes a preset value in code without updating the snapshot, the test fails with a diff. Updating the snapshot is the explicit way to register a new preset version (or a v1 → v2 rename); the test diff shows up in PR review. NO import-time hashing, NO write-back, NO `preset_hashes.json` in `src/`.
- End-to-end test: build a synthetic `.h5` fixture with `metadata.channel_names=["Cap", "pnorm"]`, `/intensity` 3D array with two channels, `masks/P-body_mask`, etc. Call `run_analysis("per_particle_donut", h5, {"Cap": "Cap", "P-body_mask": "P-body_mask", "pnorm": "pnorm"}, preset="m7g-cap-v1")`. (Note: layer-map values are NAMES, not paths — the loader's kind-dispatch resolves `kind="intensity"` roles against `channel_names` automatically.) Assert the returned `pbody_table` DataFrame has the expected columns and numeric parity with the CLI on the equivalent TIFFs.

**Execution note:** Implement test-first. The registration glue is small but the cross-system test (h5 → run_analysis → run_one_image_set → DataFrame) is the load-bearing check.

**Patterns to follow:**
- The brainstorm doc's `ANALYSIS_INTEGRATION_PLAN.md` §5.2 — full class declaration template.
- `src/percell4/application/use_cases/batch_compute_phasor.py` — application-layer module that bridges domain logic to a use case.

**Test scenarios:**
- *Happy path: end-to-end.* Synthetic `.h5` with all P-body inputs. `run_analysis("per_particle_donut", h5, {...}, preset="m7g-cap-v1")` returns `{"pbody_table": <DataFrame>}`. DataFrame has expected columns (`pbody_id`, `pbody_area_px`, `cap_pbody_mean`, ...). Row count matches.
- *Happy path: both branches.* `.h5` with all P-body AND SG inputs. Returns `{"pbody_table": ..., "sg_table": ...}`. Both have rows.
- *Happy path: with cp_mask + single_cell.* `params={"single_cell": True}`. Returned `pbody_table` has one row per cell.
- *Edge case: export_donuts.* `params={"export_donuts": True}`. Returned dict additionally includes `pbody_donut_mask` and/or `sg_donut_mask` as `uint8` arrays. Type matches `ImageOutput.dtype="binary"`.
- *Error path: BoolParam.requires unmet.* `params={"single_cell": True}` but no `cp_mask` in layer_map. Raises (from U3's runner, not U5).
- *Error path: missing required role.* layer_map doesn't include `Cap`. Raises (from U3's runner) with `Cap` named.
- *Error path: missing both group branches.* layer_map has neither pbody nor sg. `group_requirement="at_least_one"` is violated. Raises with the group names.
- *Happy path: registration fires at import.* Importing `percell4.application.analysis.modules.per_particle_donut` (which is auto-imported via `application/analysis/__init__.py`) succeeds. `_REGISTRY["per_particle_donut"]` is populated. Importing `percell4.application.analysis` (the parent package) is sufficient.
- *Preset snapshot test: in-code values match committed snapshot.* `tests/test_application/test_presets_immutable.py` loads `PerParticleDonut.presets["m7g-cap-v1"]` and `tests/fixtures/preset_snapshots/per_particle_donut.json["m7g-cap-v1"]`; asserts key-by-key equality. If a developer changes `buffer: 5 → 6` in code without also updating the snapshot file, the test fails with a clear diff. (NOT an import-time check; runs with the rest of pytest.)
- *Integration: cross-system numeric parity with CLI.* On the same fixture inputs, the CLI's `combined_pbody.csv` and `run_analysis()`'s `pbody_table` pass the numeric-parity test (drop ID columns, sort by stable key, integer exact-equal, float `np.allclose(rtol=1e-10)`). Mandatory test per the `phasor-view-bin-not-forwarded` learning.

**Verification:**
- All tests pass.
- `from percell4.application.analysis.modules.per_particle_donut import PerParticleDonut` works.
- `registry.get("per_particle_donut")` returns the class.

---

- U6. **Run folder + batch runner**

**Goal:** Batch runner that iterates over selected `.h5` datasets, calls `run_analysis()` for each, accumulates table outputs with a `dataset` column, writes image outputs back into each `.h5`, and produces a `run_<timestamp>/` folder with combined + per-dataset CSVs and `run_config.json`.

**Requirements:** R13, R14, R15, R17.

**Dependencies:** U3 (calls `run_analysis`), U5 (the first analysis to run against).

**Files:**
- Create: `src/percell4/application/analysis/types.py` (`BatchAnalysisItemResult`, `BatchAnalysisReport`)
- Create: `src/percell4/application/analysis/run_folder.py`
- Create: `src/percell4/application/use_cases/run_analysis_batch.py`
- Test: `tests/test_application/test_run_analysis_batch.py`

**Approach:**
- `types.py`: new dataclasses. `BatchAnalysisItemResult(h5_path: Path, status: Literal["succeeded", "failed", "skipped"], produced_outputs: tuple[str, ...] = (), error: str | None = None)`. `BatchAnalysisReport(items: tuple[BatchAnalysisItemResult, ...])`. 3-state taxonomy (no `partial` — analyses are atomic). Does NOT reuse `BatchPhasorItemResult`; that one's per-channel semantics don't fit.
- `run_folder.py`: reuses `src/percell4/workflows/artifacts.py::create_run_folder` (already used by `FlimFretDialog` with `prefix="flim_fret_run"`). Use `prefix="analysis_run"` and `create_subdirs=True` (to get the `per_dataset/` subfolder). Reuses `artifacts.write_atomic` for the JSON write. Does NOT reuse `write_run_config` because that one is typed to `WorkflowConfig`/`RunMetadata` and our schema is different. Define an analysis-specific `write_analysis_run_config(folder, payload: dict) -> None` thin wrapper around `write_atomic` to a path of `<folder>/run_config.json`.
- `run_analysis_batch.py`: function `batch_run_analysis(analysis_name, h5_paths, layer_map_resolver, params, preset, output_parent, *, progress_callback=None, cancel_check=None) -> BatchAnalysisReport`. `layer_map_resolver` is a callable `(h5_path) -> dict[role, layer_name]` — for v1 the dialog passes a closure that returns the SAME map for every dataset.
- Per-dataset isolation: open `DatasetStore(path)` inside a try/except. On open failure → `BatchAnalysisItemResult(status="failed", error=str(exc))`. Otherwise call `run_analysis(...)`. ValueErrors from `run_analysis` (missing layer for this specific dataset, fit-time degenerate cases, etc.) → `failed` with the error message. Successful runs: write image outputs immediately via `store.write_mask(name, array)` (for `ImageOutput` whose underlying role kind is `"mask"`) or `store.write_labels(name, array)` (for `kind="label"`). Accumulate table rows with `dataset=path.stem` prepended into in-memory dicts keyed by output name.
- At end of loop: for each accumulated table, write `<run_folder>/combined_<output_name>.csv` (all datasets) and `<run_folder>/per_dataset/<dataset_stem>_<output_name>.csv` (one per dataset). Write `run_config.json` with: `analysis_name`, `analysis_version`, `preset_name` (if used), `preset_values` (the actual dict — no hash mechanism), `params` (resolved), `layer_map` (per-dataset), `dataset_paths` (resolved absolute), `timestamp` (ISO), `percell4_version`, `status` (`"completed"` / `"cancelled"`), `completed_dataset_count` (number of datasets processed before any cancel).
- Progress callback fires once per dataset with the item. Cancel check polled between datasets. **Cancel semantics (per Key Technical Decisions): no resume-from-checkpoint.** On cancel, datasets-so-far have their image outputs persisted; combined and per-dataset CSVs are written for those datasets only; `run_config.json` records `status="cancelled"` and `completed_dataset_count=N`. Re-running re-processes from the start; the user manually deduplicates the dataset list if needed. Documented in U6 docstring.

**Execution note:** Implement test-first. End-to-end test against 2-3 synthetic `.h5` fixtures is the load-bearing scenario.

**Patterns to follow:**
- `src/percell4/application/use_cases/batch_fit_phasor_masks.py` — exactly the same shape (per-dataset isolation, progress callback, cancel check, output tracking). Reuse the canonical patterns.
- `src/percell4/workflows/artifacts.py::write_run_config` — JSON shape and folder layout convention.

**Test scenarios:**
- *Happy path: 2 datasets, both succeed.* `batch_run_analysis("per_particle_donut", [a.h5, b.h5], lm_resolver, preset="m7g-cap-v1", output_parent=tmp_path)`. Returns a report with 2 succeeded items. Run folder exists with `combined_pbody_table.csv`, `combined_sg_table.csv`, `per_dataset/a_pbody_table.csv`, etc. Combined CSV's first column is `dataset`.
- *Happy path: 1 dataset with export_donuts.* Image outputs land at `/masks/pbody_donut` and `/masks/sg_donut` inside the dataset's `.h5`. Round-trip: read back, compare to expected mask.
- *Edge case: per-dataset failure isolates.* Dataset A is missing a required layer; dataset B has everything. Report has A=failed, B=succeeded. Both have rows in their respective CSVs (B's rows for the combined; A doesn't contribute). `run_config.json` includes A in the dataset list with a failure note.
- *Edge case: all datasets fail.* Run folder is still created. Empty CSVs (header-only) or just `run_config.json`? Decide during implementation; lean toward `run_config.json` only (no CSVs if no rows).
- *Edge case: dataset has no rows produced.* Analysis succeeds but `min_size` filter eliminates all particles. Combined CSV has 0 data rows for that dataset (but header is correct). Report status `succeeded` (not partial) — the analysis ran cleanly, it just found nothing.
- *Cancel: mid-run.* `cancel_check()` returns True after dataset 1 of 3. Loop breaks. Run folder contains CSVs for dataset 1 only. `run_config.json` notes cancellation.
- *Error path: invalid output_parent.* `output_parent` doesn't exist or isn't writable. Raises before any per-dataset work begins.
- *Integration: run_config.json shape.* After a clean run, `run_config.json` contains analysis name, version, preset name + hash (when preset used), layer map per dataset, dataset paths (resolved), timestamp, percell4 version. JSON is round-trippable via the existing `artifacts.py` helpers.
- *Integration: end-of-run viewer refresh hook.* The batch runner does NOT call session refresh — that's the dialog's job. The runner returns the report and the list of processed paths; the dialog decides whether to fire `session.refresh_resource_lists`.

**Verification:**
- All tests pass.
- A real run against 2 fixture `.h5` files produces a complete `run_<ts>/` folder + image outputs in each dataset.

---

- U7. **`PerParticleDonutDialog` + shared widget factories**

**Goal:** Concrete per-analysis dialog for `PerParticleDonut`. The dialog is hand-written against `PerParticleDonut`'s known schema, but reuses widget factories from `gui/analysis_widgets.py` so the second-and-later analysis dialogs can lean on the same primitives without each re-deriving Qt layout boilerplate. On Start, invokes the batch runner and shows an end-of-run summary. Future analyses ship their own dialog class (each registered as `Analysis.dialog_class`) — the framework does NOT generate one from the schema.

Why per-analysis instead of schema-driven: every realistic analysis has affordances the schema can't capture cleanly (group-skip toggles styled for that analysis, output-section visual treatment, custom help text near a tricky param, etc.). One generic dialog that handles all of those for all analyses is the wrong abstraction at N=1. Doc-review scope-guardian flagged this; the same logic + widget factories pattern serves the second migration much better than a generic dialog plus a growing set of escape hatches.

**Requirements:** R10, R11, R12, R15.

**Dependencies:** U6.

**Files:**
- Create: `src/percell4/gui/analysis_widgets.py` (shared widget factories)
- Create: `src/percell4/gui/per_particle_donut_dialog.py`
- Modify: `src/percell4/application/analysis/modules/per_particle_donut.py` (set `dialog_class = PerParticleDonutDialog`)
- Test: `tests/test_gui/test_analysis_widgets.py`
- Test: `tests/test_gui/test_per_particle_donut_dialog.py`

**Approach:**

*`gui/analysis_widgets.py` — shared factories.* Pure-Qt helper module, no domain imports, no `Analysis` import (avoids a domain-→-gui dep cycle). Public surface:
- `build_dataset_picker(parent) -> (QListWidget, add_files_btn, add_folder_btn)` — the dataset list pattern used by `PhasorMasksDialog` and `FlimFretDialog`, factored out so dialogs declare it once.
- `build_layer_combo(role_name: str, role_desc: str, parent) -> (QHBoxLayout, QLabel, QComboBox)` — single labeled combo row.
- `populate_layer_combo(combo: QComboBox, h5_paths: list[Path], kind: Literal["intensity", "mask", "label"], *, sentinel: str = "—") -> list[str]` — populates the combo with the **intersection** of available layer names across the selected datasets, filtered by kind (intensity → `metadata.channel_names` + `list_groups("decay")`; mask → `list_masks()`; label → `list_labels()`). Inserts the `sentinel` as item 0 ("no selection / skip"), then the intersected names sorted. Preserves prior selection if still present; otherwise resets to the sentinel. Returns the list of populated names (excluding sentinel) so the caller can detect "intersection empty" and disable downstream widgets accordingly.
- `build_param_widget(param: ParamLike, parent) -> (QWidget, getter: Callable[[], Any], setter: Callable[[Any], None])` — dispatches `IntParam`/`FloatParam`/`BoolParam`/`ChoiceParam` to `QSpinBox`/`QDoubleSpinBox`/`QCheckBox`/`QComboBox` with the declaration's bounds + default. Returns the widget plus getter/setter closures so the dialog can read/lock values uniformly.
- `build_preset_combo(presets: dict[str, dict], parent) -> QComboBox` — preset picker with `"No preset"` as item 0, presets sorted by name. Tooltip on each non-default item shows the locked parameter dict.
- `build_output_parent_picker(qsettings_key: str, parent) -> (QHBoxLayout, QLineEdit, QPushButton)` — `QLineEdit` + `Browse...` button bound to a `QFileDialog.getExistingDirectory` and persisted via `QSettings(qsettings_key)`.

Each factory is plain-Qt. None of them touch `Analysis`, `registry`, `DatasetStore`, or any session. They take primitives and return primitives so dialog tests can exercise them without bootstrapping the framework.

*`gui/per_particle_donut_dialog.py` — concrete dialog.* `PerParticleDonutDialog(QDialog)` with no `analysis_cls` parameter — its sections are hand-laid because the dialog *knows* the analysis. Layout (vertical scroll area, capped to screen):
- **Header:** `QLabel` with `PerParticleDonut.display_name` in the section-title style, plus a wrapped `QLabel` with the analysis's docstring (or a class-level `description: ClassVar[str]` if declared). Surfaces the analysis-level "what does this do" gap the design-lens reviewer flagged.
- **Datasets** group: `build_dataset_picker(...)`.
- **Layer map** group, structured by the analysis's actual schema:
  - Row for required `Cap` (intensity).
  - Sub-group **"P-body branch"** (`group_a`): rows for `P-body_mask` (mask) + `pnorm` (intensity), preceded by a `QCheckBox("Skip P-body branch")`. When checked, both rows visibly disable AND their layer-map entries drop out (the resolved layer_map will not include `P-body_mask`/`pnorm`). The skip semantics: skipping a group means `group_satisfied = False` for `produced_when` callables, which suppresses the P-body table output. Skipping affects only this dialog's resolved layer map — does not modify the analysis schema.
  - Sub-group **"SG branch"** (`group_b`): rows for `SG_mask` (mask) + `sgnorm` (intensity), same skip-toggle pattern.
  - Row for optional `cp_mask` (label) with a sentinel "— (none)" entry; selecting the sentinel removes it from the resolved layer_map.
- **Parameters** group: rows built with `build_param_widget` for each param. `BoolParam.requires` gates the widget's enabled state (e.g., `single_cell` checkbox stays disabled until `cp_mask` is non-sentinel). When disabled because of `requires`, set a tooltip on the widget explaining why ("Requires `cp_mask` to be assigned").
- **Preset** dropdown built with `build_preset_combo` (placed above the parameters group). When set, the parameter widgets visibly disable (greyed out with a "Preset locked" tooltip on each); a small `QLabel("🔒 Preset locked")` next to the preset combo makes the lock affordance explicit.
- **Outputs** group (read-only): `QLabel` per output with strikethrough + greyed when `produced_when(group_state, params)` returns False, normal weight when True. Recomputed in `_refresh_state`. Surfaces the design-lens "user can't tell what will be produced" gap.
- **Output parent** picker via `build_output_parent_picker("analysis/per_particle_donut/output_parent", self)`.
- Buttons: `Start` and `Cancel`. `Start` is disabled unless ALL of: (1) at least one dataset, (2) group_requirement satisfied (at least one of group_a / group_b not skipped AND fully assigned), (3) output_parent exists. When disabled, the button's tooltip reports the specific cause ("Add at least one dataset" / "Assign Cap, then assign P-body_mask + pnorm or SG_mask + sgnorm" / "Choose an output folder"). Each cause has a distinct tooltip string — addresses the design-lens "Start-disabled with no reason" gap.

*`_refresh_state()` cascade.* Wired to user-interaction signals only (`combo.activated`, `spinbox.valueChanged`, `checkbox.toggled`, `lineEdit.textChanged`). Programmatic `setCurrentIndex` / `setValue` does NOT cascade — per the `qt-wire-user-edit-signals` learning. On each refresh: re-run `populate_layer_combo` for each role (the dataset list may have changed); validate the prior selection still appears in the new intersection — if not, snap the combo back to the sentinel and emit a non-fatal warning in the dialog's status area ("`<role>`: previous selection no longer available, please reassign"). The stale-selection sentinel addresses the design-lens "silent drift after dataset change" gap. Recompute the group-satisfied / output-produced map. Update Start's enabled state and tooltip.

*On Start.* Disable controls, create `QProgressDialog` (modal, max = #datasets), call `batch_run_analysis(...)` with a progress closure. After the loop: optional one-shot `session.refresh_resource_lists` if the active dataset is among processed paths. Build `QMessageBox.Information` (all succeeded), `Warning` (any failed), or `Information` with "cancelled" badge (if cancelled). The detail text shows per-dataset status and a link-style label ("Open run folder") that calls `subprocess.Popen` with the platform's file-manager-open command (Finder on macOS). Stash `last_run_folder` on `self` so the launcher slot reads it after `exec_()`.

**Execution note:** Implement test-first using `pytest-qt`. Drive real `activated` signals via `qtbot.mouseClick` / `QTest.keyClick`, not programmatic `setCurrentIndex` — same discipline as the recent phasor-masks dialog tests.

**Patterns to follow:**
- `src/percell4/gui/phasor_masks_dialog.py` — modal-dialog with inline `QProgressDialog` loop, refresh-on-change cascade, multi-dataset layer intersection, end-of-run `QMessageBox` summary, QSettings persistence.
- `src/percell4/gui/flim_fret_dialog.py` — simpler version of the same pattern; reuses `workflows.artifacts.create_run_folder` with a `prefix` (mirror this exactly in U6).
- The `qt-wire-user-edit-signals` learning in `docs/solutions/`.

**Test scenarios:**

*Widget factory unit tests (`tests/test_gui/test_analysis_widgets.py`):*
- *Happy path: `populate_layer_combo` intersection.* Two `.h5` fixtures both with `metadata.channel_names=["Cap", "pnorm"]` and `/masks/pbody`. `populate_layer_combo(combo, [a, b], kind="intensity")` populates `["—", "Cap", "pnorm"]`. With kind="mask", populates `["—", "pbody"]`.
- *Edge case: intersection empty.* Fixtures with disjoint channels. Combo has only `["—"]`; function returns empty list.
- *Edge case: stale selection.* Combo currently shows `"Cap"`. Repopulate against datasets that lack `Cap`. Combo snaps to `"—"`. Function's return value tells the caller (so the caller can show the stale-selection warning).
- *Happy path: `build_param_widget` dispatch.* `IntParam(default=5, min=0, max=99)` → `QSpinBox` with those bounds. `BoolParam(default=False)` → `QCheckBox` unchecked. `ChoiceParam(choices=("a","b"), default="a")` → `QComboBox` with those entries. Getter/setter closures round-trip.
- *Happy path: `build_preset_combo` shape.* `{"m7g-cap-v1": {...}}` → combo has `["No preset", "m7g-cap-v1"]`, item 1's tooltip shows the dict.

*Dialog tests (`tests/test_gui/test_per_particle_donut_dialog.py`):*
- *Happy path: layout assembles.* Open the dialog; verify children include the header label with display_name + description, dataset picker, two skip-toggleable group sections (`P-body branch`, `SG branch`), `cp_mask` optional row, parameter widgets, preset combo with `🔒` indicator, outputs panel with 4 output labels, output-parent picker, Start/Cancel buttons.
- *Happy path: layer dropdowns populate.* Add 2 fixtures sharing channels + masks. After dataset-list update, each role combo's items reflect the intersection.
- *Edge case: skip a branch.* Check "Skip P-body branch". Rows for `P-body_mask` + `pnorm` disable; outputs panel shows `pbody_table` + `pbody_donut_mask` greyed/struck-through. SG branch still active.
- *Edge case: stale selection.* Select a layer in `Cap`, then change the dataset list to one without that layer. The combo snaps to the sentinel; status area shows the "previous selection no longer available" warning.
- *Edge case: `BoolParam.requires` gating + tooltip.* `single_cell` checkbox starts disabled with tooltip "Requires `cp_mask` to be assigned". Assign `cp_mask` → checkbox enables, tooltip clears.
- *Edge case: preset lock affordance.* Pick `m7g-cap-v1`. All param widgets disable, each gets the "Preset locked" tooltip. Lock label `🔒 Preset locked` becomes visible next to the combo. Pick "No preset" → unlocks.
- *Edge case: outputs panel updates.* Initially all 4 outputs are greyed (no group satisfied). Assign full P-body branch → `pbody_table` lights up; `pbody_donut_mask` stays struck-through (because `export_donuts` is off by default). Toggle `export_donuts` checkbox → `pbody_donut_mask` lights up.
- *Edge case: Start-disabled tooltips per cause.* (1) No datasets: tooltip = "Add at least one dataset to begin". (2) Datasets but no group fully assigned: tooltip = "Assign Cap, then assign P-body_mask + pnorm or SG_mask + sgnorm". (3) No output parent: tooltip = "Choose an output folder". Tooltips switch as the dialog state transitions.
- *Edge case: signal wiring discipline.* `qtbot.mouseClick` + `QTest.keyClick` on a layer combo triggers `_refresh_state`. Programmatic `combo.setCurrentIndex(...)` does NOT. Pin both via `qtbot.waitSignal(combo.activated)` / lack thereof.
- *Happy path: Start dispatches to batch runner.* Monkeypatch `batch_run_analysis` to a stub. Click Start; verify the stub is called with: `analysis_name="per_particle_donut"`, `h5_paths=[the selected files]`, `layer_map_resolver` (a callable that, when invoked with any h5_path, returns the dialog-built map — verify the callable's behavior on a fixture), `params={"export_donuts": False, "single_cell": False, ...}` (the resolved dict), `preset="m7g-cap-v1"` (or `None`), `output_parent=Path(<picker value>)`.
- *Happy path: end-of-run summary — all succeeded.* Stub returns 2 succeeded. `QMessageBox.Information` opens with "2 datasets succeeded" and an "Open run folder" affordance.
- *Edge case: end-of-run summary — mixed.* Stub returns 1 succeeded + 1 failed. `QMessageBox.Warning` opens; detail text names the failed dataset + error message.
- *Cancel: in-progress.* Click Cancel on the progress dialog mid-run. Stub's `cancel_check` returns True. Summary `QMessageBox` notes "Run cancelled after 1 of 2 datasets".
- *Persistence:* On Start, the chosen output parent is persisted via QSettings under `"analysis/per_particle_donut/output_parent"`. Re-opening the dialog pre-fills it.

**Verification:**
- All widget-factory tests pass.
- All dialog tests pass.
- Manual launcher smoke: open the dialog from the Scripts tab, walk through the full layer-map population from a real dataset.

---

- U8. **Scripts tab wiring**

**Goal:** Modify `interfaces/gui/main_window.py` to populate the Scripts tab from the analysis registry. Replace the current "Run Script..." button and "Macro System — coming soon" placeholder with a list of registered analyses; clicking one opens the analysis's declared `dialog_class`.

**Requirements:** R10.

**Dependencies:** U7.

**Files:**
- Modify: `src/percell4/interfaces/gui/main_window.py` (`_create_scripts_panel`)
- Test: `tests/test_gui/test_scripts_panel.py`

**Approach:**
- In `_create_scripts_panel`: import `from percell4.application.analysis.registry import list_analyses, get`. Build a `QPushButton` per analysis entry — button label is the analysis's `display_name`, tooltip shows the analysis's description (first sentence of docstring or `description` class attr if declared). Click handler:
  ```
  cls = registry.get(name)
  if cls.dialog_class is None:
      self._show_status(f"{cls.display_name} has no dialog yet")
      return
  dialog = cls.dialog_class(parent=self)
  dialog.exec_()
  if dialog.last_run_folder:
      self._show_status(f"Analysis run complete — output at {dialog.last_run_folder}")
  ```
- Remove the existing `Run Script...` button and the "Macro System — coming soon" `_placeholder` — those are stubs being replaced.
- Re-entrance guard: standard `self.is_workflow_locked` check, mirroring the FLIM-FRET / phasor-masks slots — pre-flight the lock before instantiating the dialog.
- If the registry is empty (test environments where no analyses are imported), the panel shows a friendly "No analyses registered" label instead of an empty list.
- Importing `percell4.application.analysis` in `main_window.py` triggers the side-effect registration imports (chained from U5's `application/analysis/__init__.py`), guaranteeing the registry is populated before the launcher reads it.

**Execution note:** Test with pytest-qt; click the analyses-list button and verify the analysis's declared `dialog_class` is instantiated. Use a stub `Analysis` subclass with a stub `dialog_class` for the empty-registry-edge / dialog_class-None edge tests to avoid coupling the launcher tests to `PerParticleDonutDialog`.

**Patterns to follow:**
- `src/percell4/interfaces/gui/main_window.py::_on_open_phasor_masks_workflow` — ~25-line slot pattern.
- `_create_workflows_panel` — multi-button populated dynamically; same shape.

**Test scenarios:**
- *Happy path: registry populates buttons.* With `per_particle_donut` registered, the Scripts tab has one button labeled `"Per-particle donut background subtraction"` (the analysis's `display_name`). Clicking instantiates `PerParticleDonutDialog`.
- *Happy path: stub analysis with stub dialog.* Register a stub `Analysis` subclass with `dialog_class = StubDialog`. Scripts tab shows the stub's display_name; clicking opens `StubDialog`. Pins that the wiring is class-attribute-driven, not hardcoded.
- *Edge case: analysis with no `dialog_class`.* Register a stub with `dialog_class = None`. Clicking shows a status-bar message ("<name> has no dialog yet"), no dialog opens, no exception.
- *Edge case: empty registry.* Patch the registry to be empty. Scripts tab shows a "No analyses registered" label and no buttons.
- *Edge case: workflow lock.* While a workflow is running (`is_workflow_locked=True`), clicking an analysis button shows a status-bar message and doesn't open the dialog. (Mirrors phasor-masks workflow re-entrance behavior.)
- *Integration: dialog accept → status bar.* On accept, `last_run_folder` is read from the dialog; status bar shows `"Analysis run complete — output at <path>"`.

**Verification:**
- All tests pass.
- Launching `percell4-gui` shows the Per-particle donut entry under the Scripts tab. Clicking opens `PerParticleDonutDialog`.

---

## Phased Delivery

With U2 eliminated, the framework foundation no longer ships as an independently testable phase — the natural seam moves to "Python-callable for the first analysis" vs "user-facing for the first analysis."

### Phase 1 — Framework + first migration, Python-callable (U1, U3, U4, U5)

After phase 1, a developer can run `run_analysis("per_particle_donut", h5, layer_map, preset="m7g-cap-v1")` from Python against a real `.h5` and get back the expected DataFrames + image arrays. No GUI surface yet. The legacy CLI continues to function with numeric parity on the regression fixture. Phase complete when:
- The `Analysis` base + registry + decorator are in place (U1).
- `run_analysis(analysis_name, h5_path, layer_map, params, preset)` works end-to-end against a stub analysis and a synthetic `.h5` (U3).
- The per-particle donut script is refactored to a pure `run_one_image_set` + thin CLI wrapper (U4).
- The regression fixture committed in U4 Step 0.5 passes the **numeric parity** test (drop ID columns, sort by stable key, integer exact-equal + float `np.allclose(rtol=1e-10)`) before AND after every refactor step.
- `PerParticleDonut(Analysis)` is registered (U5).
- The preset snapshot test (`tests/test_application/test_presets_immutable.py`) is green.
- `run_analysis("per_particle_donut", h5, layer_map, preset="m7g-cap-v1")` produces DataFrames matching the CLI's numeric output on the same logical inputs.

### Phase 2 — User-facing surface (U6, U7, U8)

The Scripts tab works. After phase 2, a researcher can click `Per-particle donut background subtraction` in the Scripts tab, map layers, pick a preset, click Start, walk away, and find their results in a `run_<ts>/` folder. Phase complete when:
- Batch runner produces a complete run folder with combined + per-dataset CSVs + image outputs written back into each `.h5` (U6).
- `PerParticleDonutDialog` renders the analysis correctly: skip-toggle on each input group, preset-lock affordance, output panel that updates, Start-disabled tooltips per cause, stale-selection sentinel after dataset changes (U7).
- Manual launcher run on 2 fixture `.h5` files produces the run folder + image outputs end-to-end.
- End-of-run summary `QMessageBox` reflects succeeded / failed / cancelled counts.

Each phase is a natural commit-and-review boundary. Within phase 1, U4 is independent of U1 + U3 (the script refactor has no framework dependency), so U4 can run in parallel with U1+U3 if convenient. The commit order remains U1 → U3 → U4 → U5 → U6 → U7 → U8 for clean per-PR review.

---

## System-Wide Impact

- **Interaction graph:** New surface entirely. The Scripts tab currently has a stub `Run Script...` button + "Macro System" placeholder; both removed in U8. The analysis dialog uses the existing `QApplication.processEvents()` between items for cancel responsiveness (same as `FlimFretDialog` / `PhasorMasksDialog`). No new threads, no new signal types.
- **New dataclass surface:** `BatchAnalysisItemResult` + `BatchAnalysisReport` (U6) sit alongside the existing `BatchPhasorItemResult` + per-channel reports — they intentionally do not share a parent because the 3-state per-dataset taxonomy differs from the 4-state per-channel one. Future batch operations will pick whichever shape matches their atomicity.
- **Error propagation:** Per-dataset failures isolate within the batch runner. Schema validation errors raise at module import time (registry side, U1); layer-map errors raise at `run_analysis` call time (U3); per-dataset I/O errors are caught and reported as `BatchAnalysisItemResult(status="failed")` items. End-of-run `QMessageBox` summary shows counts + per-dataset detail text.
- **State lifecycle risks:**
  - **Active dataset open during batch run.** If the user has dataset A open in the viewer and the analysis writes new masks to A.h5, the napari layer holds stale references. Mitigation: one-shot `session.refresh_resource_lists(...)` at end-of-run when active dataset is among processed paths — same pattern as phasor-masks workflow.
  - **Mid-run cancel + image output writes.** If the analysis writes a donut mask to dataset 1 and then the user cancels before dataset 2, dataset 1 has the new mask while dataset 2 doesn't. Per Key Technical Decisions, cancel is **fail-fast, no resume**: re-running re-processes from the start; the user manually deduplicates the dataset list if needed. `run_config.json` records `status="cancelled"` and `completed_dataset_count=N` so the user can tell which datasets were touched. Documented in U6.
- **API surface parity:** `run_analysis()` is the Python entry point. The dialog calls `batch_run_analysis()` which calls `run_analysis()` per dataset. The legacy `per_particle_analysis.py` CLI is the v1 headless surface — it shares the pure `run_one_image_set` core (U4) with the framework, so they cannot diverge silently in numeric output. U5 includes the integration test that asserts this parity.
- **Integration coverage:** The schema-to-dialog-to-runner data flow crosses three layers (declared schema in domain, runner in application, widget factories + concrete dialog in GUI). U5's integration test (numeric parity with CLI), U6's run-folder integration test, and U7's stubbed-runner dialog tests together cover the chain.
- **Unchanged invariants:**
  - The HDF5 dataset format is not changed. Image outputs use existing `/masks/<name>` and `/labels/<name>` groups via `store.write_mask` / `store.write_labels`.
  - Existing workflows (single_cell, dilute_phase, flim_fret, phasor_masks) are untouched.
  - The existing `per_particle_analysis.py` CLI behavior is preserved with numeric parity (drop ID columns, sort by stable key, integer exact-equal + float `np.allclose(rtol=1e-10)`) after refactor.
  - The empty `src/percell4/plugins/` package is NOT used or modified.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Framework design is partly speculative on a single migration. The role/group/output declaration shape, the `produced_when` callable contract, the widget-factory split between shared and per-analysis dialog code — all are best-guessed against one example. The second migration may reveal gaps. | Phased delivery exposes the framework usable from Python (Phase 1) before the GUI surface is built (Phase 2). If the framework's edges show stress during the second migration (out of scope here, but anticipated), refactor at that time. Don't pre-generalize now. |
| CLI regression risk in U4. Refactoring a 787-line script is risky even with careful step-by-step changes. | Characterization-first: commit the regression fixture (input TIFFs + expected CSVs) BEFORE any refactor. Run it after every step. The **numeric parity** test (drop ID columns, sort by stable key, integer exact-equal + float `np.allclose(rtol=1e-10)`) catches every meaningful regression while accepting expected ID-column differences between CLI runs and framework runs. CI guard. |
| `produced_when` callable bugs silently include or exclude outputs. | The callable is plain Python — `lambda g, p: g.pbody_satisfied and p["export_donuts"]` style — so bugs surface as ordinary `AttributeError`/`KeyError` at runtime rather than as quiet boolean drift in a hand-written parser. U5's integration test verifies that the donut analysis's outputs are produced/skipped correctly across the four (pbody, sg, both, neither) input configurations + the `export_donuts` toggle. |
| Per-analysis dialog code grows linearly with analyses. Each new analysis ships its own `<Name>Dialog` class. | The shared widget factories in `gui/analysis_widgets.py` absorb the repeated layout boilerplate (dataset picker, layer combo, layer-intersection, param widget dispatch, preset combo, output-parent picker). A new dialog should be ~100-200 lines of Qt code, not a from-scratch rewrite. Reassess if the third migration's dialog exceeds ~300 LOC of non-factored code. |
| Active dataset open during batch run → stale viewer state after image outputs are written. | End-of-run conditional `session.refresh_resource_lists` from U7. Same pattern as phasor-masks workflow; pin in U7 tests. |
| Layer-dropdown intersection across selected datasets surfaces UX confusion when datasets have different layer sets. | The intersection rule is the same as phasor-masks workflow (which the user has experience with). The U7 stale-selection sentinel + status-area warning addresses the silent-drift case the design-lens reviewer flagged. Document the intersection rule in dialog tooltip text. |
| Preset drift over time. Developers may change preset values in code without updating the snapshot. | The committed snapshot at `tests/fixtures/preset_snapshots/per_particle_donut.json` + the `test_presets_immutable` test (U5) fail loudly with a key-by-key diff in PR review. Updating presets means updating the snapshot in the same PR; this surfaces in code review naturally. |
| (CLI, Python-API) drift over time. The legacy CLI imports `run_one_image_set` from the new domain module. If someone changes the domain function's signature, the CLI breaks silently. | The regression fixture catches numeric drift; the pure-function unit tests in U4 catch signature drift (they instantiate the function with the expected kwargs and will fail at call time if those kwargs disappear or rename). |

---

## Documentation / Operational Notes

- **`docs/solutions/` capture warranted IF the framework introduces a genuinely new pattern** — candidates: the `Analysis` + `dialog_class` registration shape, the role-kind-dispatched loader, the per-analysis dialog + shared widget-factory split, the preset snapshot-test mechanism. Add one entry per genuinely new pattern after Phase 2 ships. Skip patterns that are direct copies of phasor-masks workflow.
- **Contributor doc for "writing an analysis"** mentioned in the brainstorm doc (Phase 6 Task 6.1). Out of scope for this plan. After the framework + first migration ship and the second migration starts, write `docs/writing_an_analysis.md` using PerParticleDonut as a worked example.
- **No CI changes needed** beyond the new test suites. The existing pytest configuration picks up `tests/test_domain/`, `tests/test_application/`, `tests/test_gui/`.
- **No dependency changes.** All required libraries (`numpy`, `pandas`, `tifffile`, `scipy`, `scikit-image`, `h5py`, `qtpy`) are already in `pyproject.toml`.
- **No CLAUDE.md updates** unless a new architectural conventions becomes load-bearing. The hexagonal split for analyses is a natural extension of the existing `domain/` + `application/` + `interfaces/gui/` layout; doesn't need new doc.

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-27-analysis-integration-requirements.md](../brainstorms/2026-05-27-analysis-integration-requirements.md)
- **Supplementary technical sketch:** [docs/brainstorms/ANALYSIS_INTEGRATION_PLAN.md](../brainstorms/ANALYSIS_INTEGRATION_PLAN.md) (the `[DECIDE]` markers are superseded by this plan; the type/class/output declaration sketches in §4 and §5.2 are useful technical references for U1 and U5)
- **Related code:**
  - `src/percell4/store.py` — `DatasetStore` enumeration methods
  - `src/percell4/gui/flim_fret_dialog.py` — primary dialog pattern
  - `src/percell4/gui/phasor_masks_dialog.py` — secondary dialog pattern (multi-dataset, refresh-on-change)
  - `src/percell4/workflows/artifacts.py` — `RunMetadata` + `run_config.json` shape
  - `src/percell4/application/use_cases/batch_fit_phasor_masks.py` — per-dataset isolation pattern
- **Existing script being migrated:** `per_particle_analysis.py` — currently at `~/mask-intensity-analysis-repo/per_particle_analysis.py` (external repo); U4 Step 0 copies it into the percell4 repo at `per_particle_analysis.py` (repo root). The external copy becomes deprecated.
