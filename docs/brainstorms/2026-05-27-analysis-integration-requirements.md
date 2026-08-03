---
date: 2026-05-27
topic: analysis-integration
related: docs/brainstorms/ANALYSIS_INTEGRATION_PLAN.md
---

# Analysis Integration: registered analyses with declared layer-map I/O

## Problem Frame

PerCell4 generates image data (channels, masks, segmentation labels) and stores each experiment in one `.h5` file. Downstream measurement scripts — currently standalone Python tools like `per_particle_analysis.py` — operate on TIFFs, identify their inputs by filename keywords, and walk directories to find grouped image sets. Researchers have to export TIFFs from each dataset, rename them to match the script's keyword vocabulary, and run the script outside PerCell4. The pipeline disconnects right at the point measurement should be the most fluent.

This brainstorm captures the decisions for closing that loop: registered analyses, declared layer-map I/O, batch iteration over `.h5` datasets handled by PerCell4 instead of the script. The first migration is the per-particle donut background subtraction; the framework is designed for additional analyses to follow.

A detailed technical sketch already exists at `docs/brainstorms/ANALYSIS_INTEGRATION_PLAN.md` — that document is the eventual `/ce-plan` input. This requirements doc captures the product decisions that supersede the seven `[DECIDE]` markers in it.

---

## Actors

- **A1. Researcher** — selects an analysis from the Scripts tab, maps dataset layers to the analysis's declared input roles, picks parameters or a preset, runs the analysis across one or more `.h5` files, reads the resulting CSVs and any new mask layers.
- **A2. Analysis author** — declares an analysis as a class with declared inputs/outputs/parameters/presets. Reuses the existing pure-function logic; doesn't reimplement.

---

## Key Flows

```
Researcher                                      PerCell4
─────────────                                   ─────────────
1. Open Scripts tab          ─────────────▶     Lists registered analyses from registry
2. Pick "Per-particle donut" ─────────────▶     Opens dialog with declared roles, params, presets
3. Add .h5 datasets          ─────────────▶     Channel/mask/label dropdowns populated from
                                                 each dataset's /decay/* /masks/* /labels/*
4. Map roles → layers        ─────────────▶     Validates dtype/shape per role declaration
5. Pick preset OR params     ─────────────▶     Locks params when preset selected (strict)
6. Click Start               ─────────────▶     For each dataset:
                                                   - Load arrays per layer_map
                                                   - Call analysis.run(inputs, params)
                                                   - Write image outputs back to dataset's .h5
                                                   - Accumulate table rows with dataset column
                                                 After all datasets:
                                                   - Write combined CSVs to run folder
                                                   - Write per-dataset CSVs
                                                   - Show summary dialog
```

- **F1. Configure and run an analysis (GUI).** Researcher opens Scripts tab → picks `Per-particle donut analysis` → adds 2 `.h5` files → maps Cap to channel `mNG`, P-body_mask to `/masks/pbody`, etc. → picks preset `m7g-cap-v1` → clicks Start → walks away. ~30 seconds per dataset, fully unattended after Start.

- **F2. Headless re-run (Python API / future CLI).** Researcher imports `run_analysis()`, supplies the same arguments programmatically, gets the same outputs. The original `per_particle_analysis.py` CLI continues to work unchanged as a regression-test surface (TIFF in / CSV out, same flags, same defaults).

---

## Requirements

### Framework

- **R1.** Analyses are declared as a class subclassing a framework base. Each declares (a) `required_inputs`, (b) optional `input_groups` (all-or-nothing role bundles) with a `group_requirement` (`at_least_one` / `exactly_one` / `all`), (c) `optional_inputs`, (d) `parameters` with types + constraints + `requires=` gating, (e) `presets` (immutable named parameter bundles), and (f) `outputs` (tables and image layers, each with an optional `produced_when` predicate).

- **R2.** A registry holds all known analyses. Registration validates the schema (no unknown parameters in presets, no undefined groups/roles/params in predicates, no role-name collisions across required/groups/optional). Schema violations raise at registration with a clear message.

- **R3.** Presets are immutable. Their content is content-hashed at registration and persisted (committed JSON file). Changing a preset's value with the same key raises at import; adding new preset keys is allowed. Researchers who need different parameters create new preset keys (e.g. `m7g-cap-v1` → `m7g-cap-v2`), preserving reproducibility of published results.

- **R4.** The framework loads layers from `.h5` into `numpy` arrays based on role-declared dtype (binary → bool, labels → int, float → float64) before calling the analysis's `run()`. Missing layers, dtype mismatches, and shape mismatches raise with role-specific messages.

- **R5.** Each analysis's `run(inputs, params) → outputs` is pure: arrays in, dict of outputs out. No file I/O, no directory walks, no globbing, no batch iteration inside `run()`. **One dataset per call** — iteration belongs to the runner.

- **R6.** A single entry point `run_analysis(analysis_name, h5_path, layer_map, params=None, preset=None) → outputs` validates inputs, loads arrays, invokes the analysis's `run()`, and returns the outputs without writing them. Caller decides destinations.

### First migration: per-particle donut

- **R7.** The existing `per_particle_analysis.py` script is refactored in two passes (per `ANALYSIS_INTEGRATION_PLAN.md` §5.1):
  - Phase 2a: remove I/O from inner functions (`analyze_regions`, `assign_particles_to_cells`); return arrays instead of writing TIFFs. CLI behavior byte-identical on a test fixture before and after.
  - Phase 2b: extract a pure `run_one_image_set(arrays, params) → dict` containing mode detection (P-body / SG / both), the global Cap background subtraction, the SG-before-P-body exclusion, and the optional single-cell aggregation. CLI becomes a thin wrapper around this function.

- **R8.** A registered `PerParticleDonut(Analysis)` class wires the refactored pure logic into the framework. Roles: `Cap` (required); group `pbody` = (`P-body_mask`, `pnorm`); group `sg` = (`SG_mask`, `sgnorm`); optional `cp_mask`. `group_requirement = "at_least_one"`. The `m7g-cap-v1` preset is migrated verbatim and locked into the hash file.

- **R9.** Outputs of the first analysis: two `TableOutput` (per-particle or per-cell rows for P-body and SG branches) and two optional `ImageOutput` (donut masks, gated on the `export_donuts` parameter AND the corresponding group being satisfied).

### Surface and ergonomics

- **R10.** Analyses live under the Scripts tab — distinct from the Workflows tab. The Workflows tab is unchanged. Scripts tab is currently a stub ("Run Script..." button + "Macro System — coming soon" placeholder); the registered-analyses dialog replaces the stub. Per-analysis dialogs are populated dynamically from the registry by listing `list_analyses()`.

- **R11.** The dialog renders one widget per declared input role and parameter: dropdowns for layer assignment (populated from each selected dataset's `/decay/*`, `/masks/*`, `/labels/*` listings), spinboxes/checkboxes/dropdowns for parameters according to their declared type, a preset dropdown above the parameter panel. Parameters are disabled when a preset is selected (strict — no overrides). Optional groups can be skipped via a "skip this group" toggle that hides both group members.

- **R12.** Per-`BoolParam` `requires` declarations gate parameter availability: `single_cell` is disabled until `cp_mask` is assigned. The framework wires this from the declared schema; analysis authors don't write GUI code.

### Batch iteration and outputs

- **R13.** The analyses runner iterates across the user's selected `.h5` datasets. Iteration is the runner's job, not the analysis's (R5). The same `run_one_image_set` from R7 is called once per dataset; the runner attaches a `dataset` column (the dataset's `.h5` path stem) to each table row before accumulating.

- **R14.** Output destinations follow the existing `single_cell` workflow pattern:
  - **Tables (CSV) → a per-run folder under a user-chosen output parent.** Folder name `run_<YYYY-MM-DD_HH-MM-SS>/`. Inside: `combined_<output_name>.csv` (all datasets concatenated with `dataset` column) plus `per_dataset/<dataset_stem>_<output_name>.csv` (one file per dataset). A `run_config.json` records analysis name, layer map, params/preset, dataset list, timestamp.
  - **Image outputs → back into each dataset's `.h5`** at the conventional location for their type (donut masks → `/masks/<name>`, label outputs → `/labels/<name>`). The user picks the name in the dialog; sensible defaults derive from the output declaration.

- **R15.** Per-dataset failures isolate. If one `.h5` fails to open or one channel is missing from one dataset, that dataset is recorded as failed/partial in the summary; the batch continues. End-of-run summary dialog reports counts (succeeded / partial / failed) and per-failure messages, mirroring existing batch CLIs and workflows.

### Backwards compatibility

- **R16.** The existing `per_particle_analysis.py` CLI continues to work unchanged from the user's perspective. After Phase 2 refactor it imports `run_one_image_set` from the new module path, but exposes the same flags, defaults, and CSV output format. A regression test fixture (small directory of TIFFs + expected CSV) verifies byte-identical output before and after each refactor step.

- **R17.** PerCell4's dataset format is not changed. No new HDF5 groups, no schema migrations. Image outputs go into existing `/masks/<name>` and `/labels/<name>` groups via the canonical store-write APIs.

---

## Scope Boundaries

### Explicit non-goals

- **No yaml workflow loader.** The original plan's §6.1 proposed a yaml `analysis` step type for workflows. PerCell4 has no yaml infrastructure today; analyses live on their own surface (Scripts tab) with their own batch runner. Workflows tab stays Python-class-based.
- **No napari coupling in analysis logic.** Analyses run headless. The dialog and viewer can be callers, but the registry, runner, and pure functions have no Qt or napari imports.
- **No hot-loading plugin marketplace.** Discovery is via in-repo registration (decorator at import time) or, in the future, package entry points. No runtime drop-in `.py` files in v1.
- **No 3D inputs in the first migration.** The per-particle donut script is 2D-only (`mask_img.shape` is unpacked as `h, w`). The framework's `ImageRole.ndim` field stays so future analyses can declare 3D support, but the first migration declares `ndim=(2,)`.
- **No reimplementation of analysis math.** The donut algorithm, background subtraction, single-cell aggregation, and all numeric computations are preserved verbatim. This is an I/O-boundary refactor plus a framework, not an analysis rewrite.
- **No "image set" concept in PerCell4.** The script's "image set" maps 1:1 to PerCell4's "dataset" (one `.h5` per experiment). Vocabulary in code and UI is "dataset" throughout.

### Deferred for later

- **Per-cell analysis results integrated into the dataset's measurements DataFrame.** When `single_cell` is on, the analysis produces per-cell aggregated rows. For v1 these go to CSV only. A future iteration could append them into the dataset's measurements (the existing `single_cell` workflow's parquet store), making them queryable alongside the workflow's per-cell measurements. Out of scope until a researcher needs that integration.
- **Second migration target.** The framework's edges are partly speculative on a single example. When a second analysis script is ready to migrate, the framework will be stress-tested against it and may need refinements. No specific second target is named today.
- **CLI wrapper for the registered analysis.** The existing `per_particle_analysis.py` CLI continues to work as a regression test surface and as the headless entry point for v1. A future PerCell4-batch-style CLI (`percell4-batch-analysis per_particle_donut ...`) could be added to mirror the GUI; not in v1.
- **Workflow-step composition.** Once enough analyses exist that researchers want to chain them (e.g. "run analysis A, then analysis B on A's output"), revisit the question of yaml or programmatic workflow integration. Single-analysis runs cover the immediate use case.

---

## Dependencies and Assumptions

- **PerCell4's hexagonal layout is the natural home.** Pure analysis logic → `src/percell4/domain/` (e.g. `domain/analysis/per_particle_donut.py` for the refactored pure functions). Registry, runner, types → `src/percell4/application/` (e.g. `application/analysis/registry.py`, `application/analysis/types.py`, `application/use_cases/run_analysis.py`). GUI dialog → `src/percell4/gui/` or `src/percell4/interfaces/gui/` per existing dialog conventions. The empty `src/percell4/plugins/` package is renamed or repurposed if it's not the home for analyses. Exact paths are a planning-time decision.
- **Existing HDF5 conventions hold.** Channels at `/decay/<name>` (FLIM) or top-level intensity layers, masks at `/masks/<name>`, segmentation labels at `/labels/<name>`, phasor data at `/phasor/<channel>/{g,s,...}`, metadata at `/metadata/`. The dialog's layer dropdowns populate from these well-known groups via existing `DatasetStore` enumeration methods (`list_masks()`, `list_labels()`, `metadata.channel_names`).
- **Existing `BaseWorkflowRunner` summary-dialog plumbing is reusable.** The Scripts-tab runner can mirror `FlimFretDialog`'s self-driving `QProgressDialog` pattern (modal dialog with per-dataset progress + end-of-run `QMessageBox` summary). No new GUI infrastructure required.
- **`tifffile` and `scipy.ndimage` dependencies are already in the percell4 environment** (per `pyproject.toml`). No new heavy deps for the first migration.

---

## Success Criteria

- A researcher selects 5 `.h5` datasets, picks `Per-particle donut analysis`, maps Cap/P-body_mask/pnorm/SG_mask/sgnorm to their dataset's layers, picks the `m7g-cap-v1` preset, clicks Start, and walks away. ~3 minutes later a `run_<timestamp>/` folder exists with `combined_pbody.csv`, `combined_sg.csv`, per-dataset CSV files, and (if `export_donuts` was enabled) `/masks/pbody_donut` and `/masks/sg_donut` written into each `.h5`.
- The existing CLI (`python per_particle_analysis.py --data-dir tiffs/ --output-pbody results_p.csv --output-sg results_s.csv`) produces byte-identical CSV output before and after every refactor step. A regression-test fixture in `tests/fixtures/per_particle/` enforces this.
- A second analysis can be added in under ~50 lines of glue (subclass `Analysis`, declare roles/params/outputs, call the pure function in `run()`) — no GUI code, no batch-loop code, no preset-enforcement code per analysis. (Stress-test deferred until a real second analysis arrives.)
- Trying to mutate the `m7g-cap-v1` preset's contents without renaming raises at import. Adding a new preset key (`m7g-cap-v2`) does not.

---

## Open Questions for Planning

- **Exact package paths inside the hexagonal layout.** Where exactly do `Analysis`, `register_analysis`, `run_analysis`, `loader.py`, `runner.py` live? `application/analysis/`? Re-using the empty `plugins/`? Decide during `/ce-plan`.
- **`produced_when` predicate evaluator implementation.** The plan doc proposes a tiny `eval`-with-empty-builtins approach. A recursive-descent mini-parser is cleaner and avoids any `eval` exposure. Worth a one-paragraph decision in the plan.
- **Layer-dropdown content when multiple datasets are selected.** If dataset A has channel `mNG` and dataset B has `mng_corrected`, what does the Cap dropdown show? Show only the intersection (per the existing phasor-masks workflow pattern), or show a per-dataset table where roles are mapped per-row? Resolved at planning time once the dialog is sketched.
- **What happens when the user changes the layer map after configuring a preset?** Layer map and parameter set are orthogonal in the declaration — both can change independently. Confirm the dialog rebuilds correctly when either side changes.
- **Run-config provenance scope.** What goes into `run_config.json`? At minimum: analysis name + version, preset name + hash (if used) or explicit params dict (if not), layer map, dataset list, timestamp. Should it also include software version, library versions, host info? Decide during planning.
