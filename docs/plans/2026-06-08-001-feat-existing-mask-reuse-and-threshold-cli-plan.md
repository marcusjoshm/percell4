---
title: "feat: Existing-mask reuse in single-cell workflow + headless threshold/measure/inspect CLIs"
type: feat
status: active
date: 2026-06-08
deepened: 2026-06-08
---

# feat: Existing-mask reuse in single-cell workflow + headless threshold/measure/inspect CLIs

## Overview

Four related deliverables that make the single-cell thresholding analysis workflow usable on pre-existing masks and from the command line, plus a dataset inspector:

1. **GUI: existing-mask reuse** — discover and select existing `/masks/<name>` layers in `WorkflowConfigDialog` (mirroring how existing `/labels/<seg>` are discovered to skip Cellpose), and when masks are selected, **skip the Threshold Rounds step entirely** and run measure → particle analysis → export on the chosen masks.
2. **CLI: `percell4-batch-threshold`** — headless batch grouped thresholding. Exposes every grouped-thresholding option as a flag, computes rounds, and writes `/masks/<round>` + `/groups/<round>` back into each `.h5`. Requires existing `/labels`. No measurement/export.
3. **CLI: `percell4-batch-measure`** — headless measure + particle analysis + CSV export over existing masks. Generalizes the working prototype (`measure_one` → `measure_particles_one` → `export_run`). Particle `min_area` and CSV-column selection are flags.
4. **CLI: `percell4-inspect`** — print all metadata and layers (intensity, labels, masks, groups, tracks) for one or more `.h5` datasets in human-readable form, without decoding any array.

This work was prompted by a real task: running particle analysis (min area 9 px) on two pre-existing-mask datasets. That run was done by hand-driving the workflow phases; this plan makes that a first-class GUI option and a CLI pair, and adds the inspector that would have made the dataset triage trivial.

---

## Problem Frame

The single-cell thresholding workflow (`SingleCellThresholdingRunner` driven by `WorkflowConfigDialog`) always *computes* threshold masks from scratch via the per-round compute/apply phases, even when the dataset already carries finished masks. There is also **no headless entry point** for grouped thresholding — it exists only behind the Qt runner — so batch/scriptable use is impossible without hand-writing driver code (as was just done). Finally, triaging a `.h5` (what layers exist, their names, shapes, resolution) requires ad-hoc h5py scripting, and the naive shape read decodes multi-GB stacks (a known, recently-fixed performance bug).

The workflow already has the exact pattern to copy for mask reuse: existing **segmentation** layers are discovered (`DatasetStore.list_labels()`), selected per dataset (`segmentation_overrides`), and used to **skip the Cellpose phase** in the runner. Mask reuse is the same shape applied to `/masks` and the threshold phases.

---

## Requirements Trace

- R1. In `WorkflowConfigDialog`, discover each dataset's existing `/masks/<name>` layers and let the user select one or more per dataset, mirroring the existing segmentation-override UI.
- R2. When existing masks are selected, the run skips the Threshold Rounds step entirely (either/or per run) and proceeds to measure → particle analysis → export on the selected masks, using the dataset's existing/selected segmentation for per-cell context.
- R3. Provide a headless CLI (`percell4-batch-threshold`) that runs grouped thresholding across datasets and writes `/masks/<round>` + `/groups/<round>`, exposing every grouped-thresholding option (algorithm, gmm criterion/max-components, kmeans k, gaussian sigma, channel, metric, round name, edge mode/margin, and `--segmentation` = the existing `/labels` layer to measure against) as flags. Requires existing `/labels`. Refuses to silently overwrite an existing same-name mask (see U5).
- R4. Provide a headless CLI (`percell4-batch-measure`) that runs per-cell measurement + particle analysis + CSV export over selected existing masks, with particle `min_area`/unit and CSV-column selection as flags, writing a run folder of CSVs/parquet (measurements live only in the run folder, never back into the `.h5`).
- R5. Provide a headless CLI (`percell4-inspect`) that prints, per dataset, file size + all metadata + every layer group (intensity, labels, masks, groups, tracks) with name/shape/dtype, classified by payload type, in human-readable form — reading shapes/dtypes without decoding arrays.
- R6. All new CLIs are Qt-free and Cellpose-free (require pre-existing segmentation), follow the established `percell4-batch-*` argparse conventions, and are registered as console scripts.
- R7. Preserve invariants: masks are binary `uint8` in `/masks`; measurements live only in the run folder; "thresholding" (mask) vs "segmentation" (labels) naming stays strict; all whole-file outputs are written atomically.

---

## Scope Boundaries

- **Not** building new puncta-detection methods or the validation harness — that is the separate `docs/brainstorms/2026-06-03-headless-grouped-thresholding-puncta-requirements.md` effort. This plan exposes the *existing* grouped (kmeans/gmm + per-group Otsu) thresholding, not new detectors.
- **Not** adding Cellpose to the CLIs. Both mutating CLIs require existing `/labels`; segmentation stays a separate concern (`percell4-batch-cellpose-laptrack` already exists).
- **Not** supporting per-dataset mixing of "reuse existing masks" and "compute new rounds" in one run (feature 1 is strictly either/or per run, per user decision). Consequence the user has accepted: a mixed-readiness cohort (some datasets have finished masks, others still need thresholding) must be processed as two runs producing two run folders.
- **Not** changing the per-cell measurement math, particle algorithm, or CSV schema — only the entry points that reach them.
- **No** edge-cell removal is performed on reused masks/segmentation; existing labels are measured as-is (the `is_edge` column remains available in the parquet for downstream filtering).

### Deferred to Follow-Up Work

- Multi-round-per-invocation for `percell4-batch-threshold` (v1 exposes a single round per invocation via flags; multiple rounds via repeated runs). A `--rounds-json` config file is a natural later addition.
- A combined "threshold + measure + export in one CLI command" convenience wrapper (the two CLIs compose; a wrapper can come later if demand appears).

---

## Context & Research

### Relevant Code and Patterns

**Segmentation-override pattern to mirror for masks** (`src/percell4/gui/workflows/single_cell/config_dialog.py`):
- `_build_segmentation_group()` builds the "Segmentation Selection" group (a `_run_seg_qc` checkbox + per-dataset `QComboBox`es in `self._seg_form`).
- `_dataset_segmentations(pd)` → `DatasetStore(pd.h5_path).list_labels()`.
- `_refresh_segmentation_picker()` rebuilds the per-dataset combos; called from `_refresh_dataset_tree()`.
- `segmentation_overrides` property → `dict[display_name → chosen_seg]`, consumed by the runner.
- `_try_build_config()` hard-guards empty rounds ("Add at least one thresholding round"); `WorkflowConfig` is built here with `run_seg_qc_on_existing`.

**Runner skip-segmentation branch to mirror for thresholding** (`src/percell4/gui/workflows/single_cell/runner.py`):
- `SingleCellThresholdingRunner(config, metadata, interactive_qc, segmentation_overrides)`; override dict seeds `self._effective_seg`; `_seg_name_for(entry)` resolves per dataset.
- `_phase_generator()`: the segment phase computes `existing = self._effective_seg.get(entry.name)`; when an existing segmentation is present it sets `_effective_seg`, optionally yields seg-QC, and `continue`s — **skipping Cellpose**. This is the structural template for "skip threshold compute/apply when reusing masks".
- The per-round compute/apply loop iterates `cfg.thresholding_rounds`; it is a natural no-op when that list is empty.
- `_make_measure_handler` calls `measure_one(store, round_specs=list(cfg.thresholding_rounds), seg_name=self._seg_name_for(entry), particle_settings=..., ...)` then `measure_particles_one(...)`, then export.

**Pure phase helpers (Qt-free core)** (`src/percell4/workflows/phases.py`): `threshold_compute_one`, `apply_threshold_headless`, `measure_one` (keys masks by `round.name`, tolerates missing `/masks` and `/groups` per dataset), `measure_particles_one`, `write_staging_parquet`, `write_staging_particles_parquet`, `export_run`. Config/run-folder I/O: `src/percell4/workflows/artifacts.py` (`create_run_folder`, `write_run_config`, `config_to_dict`/`config_from_dict`), `RunLog` in `src/percell4/workflows/run_log.py`.

**Models** (`src/percell4/workflows/models.py`): `WorkflowConfig` (frozen; `__post_init__` rejects empty `thresholding_rounds`), `ThresholdingRound` (`_ROUND_NAME_RE` allows letters/digits/`_`/`-`), `ParticleSettings` (`min_area`, `min_area_unit ∈ {px, um2}`), `ThresholdAlgorithm`, `GmmCriterion`, `EdgeMode`, `CELLPOSE_MODELS`.

**CLI conventions to follow** (`src/percell4/interfaces/cli/`): `batch_process.py` and `batch_validate_puncta.py` — `main(argv=None) -> int`, `RawDescriptionHelpFormatter`, deferred heavy imports inside `main()`, dataclass-sourced defaults (`defaults = CellposeSettings()`), `add_argument_group(...)`, `choices=[e.value for e in Enum]`, `_configure_logging(verbose)`, stderr + `return 1` on bad input, exit `0` iff ≥1 dataset succeeded. `_batch_report.py::resolve_paths(args)` expands positional file/dir args into `.h5` lists. Console scripts registered in `pyproject.toml [project.scripts]`. Existing `run_pipeline.py` does **single global** thresholding only — not the grouped path — so it is not a base for feature 2.

**Store introspection** (`src/percell4/store.py`): `list_labels()`, `list_masks()`, `list_groups(prefix)`, `array_shape(path)` (no decode), `labels_shape`/`masks_shape`, `array_exists`, `metadata` property (guaranteed `native_shape`/`creation_bin`/`n_timepoints`; optional `channel_names`/`pixel_size_um`/`source`/...), `open_read()`, `.path`. No public dtype accessor today.

### Institutional Learnings

- `docs/solutions/logic-errors/large-file-load-metadata-read-full-decode-2026-06-07.md` — **never** call `read_array("intensity")` to get shape/dtype; it decodes the whole gzipped stack (~250s observed). The inspector must read `.shape`/`.dtype` from h5py dataset objects (or `store.array_shape`, which is documented no-decode). **Highest-risk doc for feature 5 (U7).**
- `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md` + `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md` — a name can exist under **both** `/labels` and `/masks`. Classify segmentations as `list_labels() − list_masks()`. Masks are binary `0/1 uint8` by contract. The inspector must report correct payload types, not assume `/labels` membership.
- `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md` — **provenance invariant**: each `.h5` holds only image data + metadata + labels + masks; the measurements DataFrame lives only in the run folder. `percell4-batch-measure` must write CSVs/parquet to the run folder, never a `/measurements` group.
- `docs/solutions/logic-errors/batch-compress-development-lessons.md` — "discovery scopes, processing consumes": iterate explicit per-dataset specs, never re-derive scope from a shared parent dir mid-loop. Mask binarization at the write boundary: `(array > 0).astype(np.uint8)`.
- `docs/solutions/architecture-patterns/atomic-write-contract.md` — all whole-file outputs use tmp + `os.replace`, no `os.name == "nt"` branching. (`export_run`/`artifacts.py` already comply; new CLI outputs must too.)
- `docs/solutions/logic-errors/grouped-thresholding-development-lessons.md` — strict "thresholding" (mask) vs "segmentation" (labels) naming in flags/fields/output names; ratio metrics use means not sums; `mode_intensity` via `scipy.stats.mode`. Interactive QC concerns do not apply to the headless path.

### External References

- None required — this is internal-pattern work with strong local precedents (≥3 direct examples for every surface: segmentation-override UI, batch CLI argparse, phase helpers).

---

## Key Technical Decisions

- **Either/or per run via a run-wide flag.** Add `use_existing_masks: bool` (+ per-dataset `existing_mask_selections: dict[str, list[str]]`) to `WorkflowConfig`. When set, the runner skips the threshold compute/apply phases and measures the selected masks. Rationale: matches the user's "skip entirely" decision and the existing run-wide `run_seg_qc_on_existing` style; avoids per-round/per-dataset branching complexity.
- **Reuse `measure_one`'s name-keyed mask reads instead of new measurement code.** `measure_one`/`measure_particles_one` already read `/masks/<round.name>` (+ optional `/groups/<round.name>`) and skip missing masks per dataset. In existing-mask mode the runner/CLI builds **measure-only round specs** whose `name` equals each selected mask name. The prototype proved this produces correct columns (`<mask>_particle_count`, etc.) and a correct `summary_groups.csv`. Decision: synthesize a `ThresholdingRound` per selected mask (placeholder channel/metric/algorithm, which `measure_one` ignores) **and** carry the per-dataset selection so a dataset only measures masks the user picked — not every mask whose name collides with another dataset's selection.
- **Relax the empty-rounds invariant conditionally.** `WorkflowConfig.__post_init__` allows empty `thresholding_rounds` **iff** `use_existing_masks` is true and at least one dataset has a non-empty selection. The dialog guard at `_try_build_config()` is relaxed the same way. This keeps the loud-validation convention (a stale config with neither rounds nor masks still fails).
- **CLIs drive `phases.py` helpers directly, not the QObject runner.** Keeps them Qt-free and unit-testable without a `QApplication` (mirrors the `test_cli_pipeline.py` import-seam test). `percell4-batch-threshold` = `threshold_compute_one` + `apply_threshold_headless` per dataset; `percell4-batch-measure` = `measure_one` + `measure_particles_one` + staging + `export_run`.
- **Inspector reads shapes/dtypes without decoding.** Use `store.metadata` + `store.path.stat().st_size` + one read-only `h5py` walk (or `list_groups` + `array_shape` per prefix). Add a small no-decode dtype accessor to `DatasetStore` (`array_dtype`) so the inspector stays within the store's single-read-boundary convention rather than opening h5py itself.
- **CLI naming** follows `percell4-batch-*` for the two mutating/processing tools and a short `percell4-inspect` for the read-only inspector. Single round per `percell4-batch-threshold` invocation (multi-round deferred).
- **The dialog mask picker is a pre-run config control, not a live-session Selector.** It writes only dialog-local state (exactly like the segmentation-override combos) and must never touch the five session selection fields. Standard Qt multi-select (`QListWidget` with `ExtendedSelection`, or a column of `QCheckBox`es per dataset) — not the napari `MultiLabelSelectController`.

---

## Open Questions

### Resolved During Planning

- Does feature 2 produce CSVs or just masks? → Split into two composable CLIs: `percell4-batch-threshold` (masks only) and `percell4-batch-measure` (measure + particles + CSV). (User decision.)
- Does the CLI run segmentation? → No; require existing `/labels`. (User decision.)
- Does mask reuse mix with new rounds? → No; either/or per run. (User decision.)
- Is feature 2 the same as the 2026-06-03 puncta requirements? → No; that is new detector methods + a validation harness. This exposes the existing grouped thresholding. (Research.)
- How do synthesized measure-only rounds reach `export_run` summaries when `thresholding_rounds` is empty? → `export_run` gains an explicit round-names parameter; the runner/CLI pass the measured mask names (U2). (Doc review.)
- Does `config_to_dict`/`from_dict` round-trip the new fields automatically? → No; both are hand-enumerated whitelists, so the new fields are a required explicit edit with a missing-key default on load (U1). (Doc review.)

### Deferred to Implementation

- Exact multi-select widget choice in the dialog (`QListWidget` vs checkbox column) for the per-dataset mask list — pick during implementation based on layout fit with the existing `QFormLayout`. (Toggle behavior and visual states are resolved in U3.)
- CSV-column flag surface for `percell4-batch-measure` (full per-channel/metric selection vs a `--csv-preset default|all` plus overrides) — settle when wiring the flags.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
GUI (deliverable 1)                     CLI pipeline (deliverables 2, 3, 4)
───────────────                         ──────────────────────────────
WorkflowConfigDialog                    percell4-inspect  <dsets>
  ├─ segmentation picker (exists)          └─ store.metadata + no-decode walk → human-readable report
  └─ NEW mask picker  ──┐
                        ▼               percell4-batch-threshold <dsets> --channel ... --algorithm kmeans ...
   use_existing_masks=True                 └─ per dataset: threshold_compute_one → apply_threshold_headless
   existing_mask_selections={ds:[m..]}        → writes /masks/<round> + /groups/<round>   (requires /labels)
                        │
                        ▼               percell4-batch-measure <dsets> --mask <name>.. --min-particle-area 9 --output ~/out
   SingleCellThresholdingRunner             └─ per dataset: measure_one → measure_particles_one → staging
     skip compress (keep) ─ keep seg            then export_run → run folder (combined.csv, particles.csv, ...)
     SKIP threshold compute/apply  ◄── new branch (mirror of skip-Cellpose)
     measure_one(round_specs = synthesized-from-selected-masks)
     measure_particles_one → export_run → run folder CSVs
```

Shared Qt-free core for all three CLIs and the runner's measure path: the `phases.py` helpers. The runner's new branch and `percell4-batch-measure` build the *same* measure-only round specs from selected mask names.

---

## Implementation Units

### Phase 1 — GUI existing-mask reuse

- U1. **WorkflowConfig: existing-mask fields + conditional empty-rounds invariant**

**Goal:** Let a config represent "reuse existing masks, no rounds" without weakening validation.

**Requirements:** R2, R7

**Dependencies:** None

**Files:**
- Modify: `src/percell4/workflows/models.py`
- Modify: `src/percell4/workflows/artifacts.py` (verify/extend `config_to_dict`/`config_from_dict` round-trip for the new fields)
- Test: `tests/test_workflows/test_models.py` (or the existing models test module — match current location)
- Test: `tests/test_workflows/test_artifacts.py` (round-trip)

**Approach:**
- Add `use_existing_masks: bool = False` and `existing_mask_selections: dict[str, list[str]] = field(default_factory=dict)` to `WorkflowConfig`.
- In `__post_init__`, allow empty `thresholding_rounds` **iff** `use_existing_masks and any(existing_mask_selections.values())`; otherwise keep the existing "at least one round" error. Add a guard that `existing_mask_selections` keys are a subset of dataset names and values are non-empty when `use_existing_masks`.
- **`config_to_dict`/`config_from_dict` are hand-enumerated field-by-field (the module docstring explicitly rejects `dataclasses.asdict`), so both new fields are a *required* edit, not a "verify": add `use_existing_masks` and `existing_mask_selections` to both functions explicitly.** `config_from_dict` must default a missing key (a `run_config.json` written before these fields existed) to `use_existing_masks=False` / empty selections — otherwise the relaxed invariant would reject a legacy masks-less, rounds-present config on Resume. Keep the dataset-name subset check lenient on load (warn, don't raise) so a renamed/removed dataset doesn't block Resume.

**Patterns to follow:** Existing frozen-dataclass + loud `__post_init__` validation in `models.py`; the `run_seg_qc_on_existing` run-wide bool field.

**Test scenarios:**
- Happy path: `use_existing_masks=True`, non-empty `existing_mask_selections`, empty rounds → constructs successfully.
- Edge case: `use_existing_masks=True` but all selections empty → raises (no masks and no rounds).
- Edge case: `use_existing_masks=False`, empty rounds → still raises (unchanged behavior).
- Edge case: `existing_mask_selections` references an unknown dataset name → raises.
- Happy path: round-trip `config_to_dict` → `config_from_dict` preserves both new fields exactly — assert the serialized dict **contains** both keys (not merely that load doesn't crash) and that the nested `dict[str, list[str]]` survives byte-for-byte.
- Edge case: `config_from_dict` on a payload missing the new keys (legacy run folder) → `use_existing_masks=False`, empty selections, and a legacy rounds-present config still loads.

**Verification:** Model tests pass; a config with masks-only validates and round-trips; legacy configs (no new fields) still load.

---

- U2. **Runner: skip threshold compute/apply and measure selected existing masks**

**Goal:** When `use_existing_masks`, branch the phase generator to bypass the per-round compute/apply phases and drive measurement over the selected masks.

**Requirements:** R2

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/runner.py` (per-dataset specs into both measure call sites; pass round names to export)
- Modify: `src/percell4/workflows/phases.py` (`export_run` gains an explicit round-names parameter defaulting to `config.thresholding_rounds`)
- Test: `tests/test_gui/test_single_cell_runner.py` (or the existing runner test module; runner is QObject — use the existing `interactive_qc=False` headless test harness)
- Test: `tests/test_workflows/test_phases.py` (export round-name parameter populates summaries)

**Approach:**
- With `config.use_existing_masks` and `thresholding_rounds=[]`, the per-round compute/apply loop is **already a no-op** (it iterates an empty list) — no `continue` guard is needed and the "mirror of skip-Cellpose" framing is inaccurate. The real change surface is the **measure path**, not a loop guard.
- Add a per-dataset helper `_measure_round_specs_for(entry)` that, in existing-mask mode, builds **measure-only round specs** from `config.existing_mask_selections[entry.name]` — one synthesized `ThresholdingRound` per selected mask name (placeholder `channel` = first intersected channel, `metric` = a valid builtin like `mean_intensity`, `algorithm` = KMEANS; `measure_one` reads only `.name`, but `ThresholdingRound.__post_init__` still validates these placeholders, so they must be valid). Wrap synthesis in `try/except ValueError` and record a per-dataset failure if a mask name fails `_ROUND_NAME_RE` rather than aborting the run. Outside existing-mask mode the helper returns `list(config.thresholding_rounds)` unchanged.
- **Rewire both measure call sites** in the runner to use `_measure_round_specs_for(entry)` instead of the hardcoded `round_specs=list(self._config.thresholding_rounds)`: `_make_measure_handler`'s `measure_one(...)` **and** the particle branch's `measure_particles_one(...)`. Both must receive the identical per-dataset specs or particle-detail rows silently disappear. This is the substantive work of the unit — sourcing per-dataset specs into the otherwise dataset-agnostic handler. Do **not** stuff the union of all selections into a shared `thresholding_rounds`; that would let dataset A also measure dataset B's mask whenever the name physically exists on A (the cross-contamination the design forbids).
- Keep segmentation resolution (`_seg_name_for`) unchanged so existing/selected labels provide per-cell context.
- **Export round-name seam (required):** `export_run` derives `round_names` from `config.thresholding_rounds`, which is empty here, so `summary_groups.csv` and `summary_datasets.n_rounds_thresholding` would be blank despite masks being measured. `WorkflowConfig` is frozen, so the synthesized specs can't be injected after construction. Resolve by giving `export_run` an explicit round-names parameter (defaulting to `config.thresholding_rounds` for the legacy path) and passing the union of measured mask names from the runner's export handler. Add tests asserting the summaries populate — not just that `combined.csv` has particle columns.

**Execution note:** Start from the existing headless (`interactive_qc=False`) runner test to assert the masks-only path produces staged measurements without entering any threshold phase.

**Patterns to follow:** The skip-Cellpose branch in `_phase_generator`; `_make_measure_handler`'s existing `measure_one`/`measure_particles_one`/staging/export sequence.

**Test scenarios:**
- Happy path: a dataset with an existing `/masks/<m>` + `/labels/<seg>`, `use_existing_masks=True`, selection `{ds:[m]}` → no threshold-compute/apply phase requests are yielded; measurement stages rows; particle staging present; `export_run` emits CSVs including `<m>_particle_count`; **`summary_groups.csv` and `summary_datasets.n_rounds_thresholding` are populated** (the export round-name seam). Covers R2.
- Edge case: a dataset whose selected mask name is absent on disk → that mask is skipped (logged), run still completes for other datasets (matches `measure_one` tolerance).
- Edge case: a selected mask name that fails `_ROUND_NAME_RE` (e.g. starts with a digit) → synthesis records a per-dataset failure, the run continues for valid datasets, no crash.
- Edge case: time-lapse dataset (n_timepoints > 1) whose mask's `/groups` table **lacks a `timepoint` column** (e.g. a hand-authored or older mask) → measurement does not silently apply frame-0 group assignments to every frame (verify the per-timepoint group slice degrades safely — either no group column or correct per-frame handling).
- Integration: two datasets with *different* mask names each selecting their own, where dataset A *also physically contains* a mask whose name matches B's selection → dataset A's rows have **no** column for B's mask (per-dataset specs, not the union); combined export has both families with cross-dataset NaNs as expected.

**Verification:** Headless runner over a fixture dataset with existing mask + labels yields a run folder with `combined.csv` + `particles.csv` and never enters a threshold phase.

---

- U3. **Config dialog: mask discovery, per-dataset selection, and rounds-skip wiring**

**Goal:** Mirror the segmentation-override UI for masks; add a run-wide "use existing masks" toggle; relax the empty-rounds guard; forward selections to the runner.

**Requirements:** R1, R2

**Dependencies:** U1, U2

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py`
- Modify: `src/percell4/interfaces/gui/main_window.py` (capture selections, pass to runner / into `WorkflowConfig`)
- Modify: `docs/audits/gui-element-classification.yaml` (classify the new mask picker as a pre-run config control, like the segmentation combos)
- Test: `tests/test_gui/test_workflow_config_dialog.py` (or existing dialog test module; mark `gui` if it instantiates Qt)

**Approach:**
- Add `_build_mask_selection_group()` with a run-wide `_use_existing_masks` checkbox and a per-dataset multi-select (a `QListWidget` set to `ExtendedSelection`, or a checkbox column) populated from `_dataset_masks(pd)` → `DatasetStore(pd.h5_path).list_masks()`.
- Add `_refresh_mask_picker()`, called from `_refresh_dataset_tree()` right after `_refresh_segmentation_picker()`, so it stays in sync as datasets change.
- Add an `existing_mask_selections` property → `dict[display_name → list[selected_mask]]` (empty/disabled rows omitted). When the toggle is on, disable/grey the rounds table; when off, restore today's behavior.
- In `_try_build_config()`: when the toggle is on, skip the "add at least one round" warning (require ≥1 mask selected on ≥1 dataset instead), and build `WorkflowConfig(use_existing_masks=True, existing_mask_selections=..., thresholding_rounds=[])`. The masks-mode guard message names the dataset(s) that need a selection (mirroring the specificity of the existing "Add at least one thresholding round" warning).
- In `main_window.py`, capture the dialog's selections and construct the runner so `config.use_existing_masks`/`existing_mask_selections` flow through (alongside the existing `segmentation_overrides`).

**Interaction states (resolved — implement as specified, do not re-decide):**
- The mode control is a single **checkbox** "Use existing masks (skip thresholding rounds)". When **on**, the rounds-table group is **hidden** (removed from layout to cut cognitive load) and the mask-selection group is shown; when **off**, the reverse. The two are mutually exclusive by construction.
- Mask selections are **preserved** in widget state when the user toggles off and back on (do not clear them) — toggling is non-destructive so the user can compare modes.
- A dataset with **no `/masks`** shows a disabled row labeled "No masks found"; it contributes nothing to `existing_mask_selections`.
- Each dataset's masks are presented under the **dataset display name** (row label / section header in the `QFormLayout`), so two datasets with disjoint mask sets read unambiguously.

**Patterns to follow:** `_build_segmentation_group` / `_dataset_segmentations` / `_refresh_segmentation_picker` / `segmentation_overrides` triad; the main-window wiring that already forwards `segmentation_overrides` to the runner.

**Test scenarios:**
- Happy path: dialog over a dataset with `/masks/{a,b}` → both offered; selecting `a` with the toggle on yields `existing_mask_selections == {ds:[a]}`, `use_existing_masks=True`, empty rounds, and a valid config. Covers R1, R2.
- Edge case: toggle on but no mask selected on any dataset → `_try_build_config` warns and returns None.
- Edge case: dataset with no `/masks` → its row is disabled with a "None" sentinel; not included in selections.
- Edge case: toggle off → rounds table re-enabled, original empty-rounds guard fires, mask selections ignored.
- Integration: two datasets with different mask name sets → each row shows only its own masks.

**Verification:** With the toggle on and a mask selected, Start produces a `use_existing_masks` config; with it off, behavior is byte-identical to today.

---

### Phase 2 — Headless CLIs (independent of Phase 1)

> **Sequencing note:** The originating task (measure + particle + export on *existing* masks) is served directly by `percell4-batch-measure` (U6) + `percell4-inspect` (U7) + the GUI reuse path (Phase 1). `percell4-batch-threshold` (U5) fills a separate real gap (no headless grouped-threshold entry point) but does **not** serve the originating task, whose datasets already had masks. If value-first sequencing matters, U6/U7 can land before U5.

- U4. **DatasetStore: no-decode dtype accessor**

**Goal:** Expose array dtype without decoding, so the inspector stays within the store's read boundary.

**Requirements:** R5, R7

**Dependencies:** None

**Files:**
- Modify: `src/percell4/store.py`
- Test: `tests/test_store.py` (or existing store test module)

**Approach:**
- Add `array_dtype(hdf5_path) -> np.dtype` reading `f[path].dtype` inside `open_read()`, no array materialization — sibling to the documented no-decode `array_shape`.

**Patterns to follow:** `array_shape` (explicitly no-decode); the `open_read` session-read boundary.

**Test scenarios:**
- Happy path: dtype of `/intensity`, `/labels/<n>`, `/masks/<n>` returned correctly without reading data.
- Edge case: missing path → same error shape as `array_shape` on a missing path.
- Verification (no-decode): reading dtype does not trigger a full-array read (assert via timing-independent means, e.g., a spy/patch on the read path, mirroring the large-file-load learning).

**Verification:** Store tests pass; dtype available for every layer type with no decode.

---

- U5. **CLI `percell4-batch-threshold`: headless grouped thresholding (masks only)**

**Goal:** Compute grouped threshold rounds across datasets and write `/masks/<round>` + `/groups/<round>`, with every option as a flag. Requires existing `/labels`.

**Requirements:** R3, R6, R7

**Dependencies:** None (uses existing `phases.py` helpers)

**Files:**
- Create: `src/percell4/interfaces/cli/batch_threshold.py`
- Modify: `pyproject.toml` (`[project.scripts]` → `percell4-batch-threshold = "percell4.interfaces.cli.batch_threshold:main"`)
- Test: `tests/test_cli_batch_threshold.py`

**Approach:**
- `main(argv=None) -> int` following `batch_process.py`: positional dataset args resolved via `_batch_report.resolve_paths`; deferred heavy imports inside `main`; `_configure_logging(verbose)`.
- Flags (defaults sourced from `ThresholdingRound`): `--round-name`, `--channel` (required), `--metric` (`choices` from `BUILTIN_METRICS`), `--algorithm` (`choices` from `ThresholdAlgorithm`), `--gmm-criterion` (`choices` from `GmmCriterion`), `--gmm-max-components`, `--kmeans-n-clusters`, `--gaussian-sigma`, `--segmentation` (existing `/labels` name; error clearly if absent), `--edge-mode`/`--edge-margin` if they affect compute, `--verbose`.
- Per dataset (explicit per-dataset scope — never re-derive from a shared root): open store, resolve segmentation, run `threshold_compute_one` → `apply_threshold_headless` to write `/masks/<round>` + `/groups/<round>`. Mask binarization at the write boundary (`(arr > 0).astype(uint8)`) is already handled by the apply helper; confirm.
- **Mask-overwrite guard (data-loss prevention):** `store.write_mask` → `write_array` deletes any existing `/masks/<round>` before recreating it, so re-running with the same `--round-name`, or running on a dataset that already carries a hand-made mask of that name, would silently destroy the prior mask + its `/groups`. Before writing, check `array_exists(f"masks/{round}")`; if present, error to stderr and `return 1` **unless** an explicit `--overwrite` flag is passed.
- **Pipeline hand-off:** on success, print the exact follow-up command (`percell4-batch-measure <datasets> --segmentation <seg> --mask <round> ...`) so the user knows masks were written and how to get CSVs. `--help` states where this CLI sits in the threshold → measure pipeline.
- No measurement, no run folder. Exit `0` iff ≥1 dataset succeeded; stderr + `return 1` on bad flags/missing labels. Per-dataset progress + final "N ok / M failed" summary follow the `batch_process.py` `_progress` + summary convention.

**Patterns to follow:** `batch_process.py` (argparse groups, dataclass-sourced defaults, exit codes, `_progress`), `batch_validate_puncta.py` (`ThresholdingRound` construction in a `try/except ValueError`), `_batch_report.resolve_paths`.

**Test scenarios:**
- Happy path: a fixture `.h5` with `/intensity` + `/labels/<seg>` → run writes `/masks/<round>` + `/groups/<round>`; mask is `uint8` binary. Covers R3.
- Edge case: dataset missing `/labels/<seg>` → clear stderr error, `return 1`, no partial mask written.
- Edge case: invalid `--algorithm`/`--metric` → argparse `choices` rejects before any work.
- Edge case: invalid round name (regex) → `ValueError` surfaced as a clean message.
- Edge case: `/masks/<round>` already exists, no `--overwrite` → stderr error, `return 1`, **existing mask left intact** (no partial delete). With `--overwrite` → mask replaced.
- Integration: two datasets, one valid one missing labels → valid one processed, summary reports 1 ok / 1 failed, exit `0`; success path prints the `percell4-batch-measure` follow-up command.
- Seam: importing the module triggers no Qt/napari (mirror `test_cli_pipeline.py::TestImportSeam`).

**Verification:** `percell4-batch-threshold --help` lists every option; running on a fixture writes binary masks + groups and leaves measurements untouched.

---

- U6. **CLI `percell4-batch-measure`: measure + particle analysis + CSV export over existing masks**

**Goal:** Generalize the prototype into a registered CLI: per-cell measurement + particle analysis + CSV/parquet export over selected existing masks, into a run folder.

**Requirements:** R4, R6, R7

**Dependencies:** None (uses existing `phases.py` helpers; conceptually pairs with U5's output)

**Files:**
- Create: `src/percell4/interfaces/cli/batch_measure.py`
- Create: `src/percell4/workflows/csv_columns.py` (Qt-free default-selection constants shared by dialog + CLI)
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py` (seed `__init__` CSV-default sets from the shared constants)
- Modify: `pyproject.toml` (`percell4-batch-measure = "percell4.interfaces.cli.batch_measure:main"`)
- Test: `tests/test_cli_batch_measure.py`

**Approach:**
- `main(argv=None) -> int` per the conventions. Positional datasets via `resolve_paths`.
- Flags: `--segmentation` (existing `/labels`), `--mask` (repeatable; one or more mask names to measure), `--min-particle-area` + `--particle-unit` (`choices=[px, um2]`, defaults from `ParticleSettings`), `--output` (run-folder parent, default cwd), CSV-column selection (`--csv-metrics`, `--csv-channels`, `--csv-particle-per-cell`, `--csv-particle-per-channel`, or a `--csv-preset default|all` with the dialog defaults as the `default` preset), `--edge-mode`/`--edge-margin`, `--verbose`.
- **`--mask` default:** when omitted, default to **all `/masks` present** but print a stderr line listing which masks were auto-selected, so a dataset carrying several mask layers (P-body, grouped, dilute, ROI gate) does not silently emit a wide CSV with unexpected column families and wasted compute. `--mask` may be passed repeatedly to scope explicitly (the originating task measured one mask per dataset).
- Per dataset: build measure-only round specs from `--mask` (synthesized `ThresholdingRound` per mask, sharing U2's `_measure_round_specs_for` helper / regex-failure handling), `create_run_folder(--output)`, `write_run_config`, `measure_one` → `write_staging_parquet`, `measure_particles_one` → `write_staging_particles_parquet`, then `export_run` (passing the measured mask names as the round-name argument added in U2). Measurements land only in the run folder (provenance invariant). One run folder per invocation aggregating all datasets (per_dataset/*.csv), matching `export_run`'s multi-dataset design. Note `create_run_folder` always creates a **timestamped subfolder** under `--output` — document this in `--help` so scripted callers don't expect `--output` itself to be the run folder.
- **CSV defaults shared via constants, not a method extraction.** Define the default selection sets as module-level constants in a minimal Qt-free module (e.g. `src/percell4/workflows/csv_columns.py`: `DEFAULT_CSV_METRICS`, `DEFAULT_CSV_PARTICLE_PER_CELL`, `DEFAULT_CSV_PARTICLE_PER_CHANNEL`) and have the dialog's `__init__` seed its selection sets from those constants. The CLI reads the same constants to populate its `default` preset. This removes the drift source (the default values) without extracting the dialog's instance-state-dependent `_build_selected_csv_columns` method. The column-list assembly stays where it is; a parity test (below) guards against divergence.

**Execution note:** Validate against the just-produced reference outputs (`~/Desktop/particle_run_*`) — the CLI on the same inputs with `--min-particle-area 9` should reproduce them column-for-column.

**Patterns to follow:** The prototype's phase sequence; `batch_process.py` argparse/exit conventions; `export_run`'s atomic CSV writes (already compliant).

**Test scenarios:**
- Happy path: fixture with `/intensity` + `/labels/<seg>` + `/masks/<m>` → run folder with `combined.csv`, `per_dataset/<ds>.csv`, `particles.csv` (+ parquet); particle `area` column min ≥ `--min-particle-area`. Covers R4.
- Edge case: `--min-particle-area 9` → no particle below 9 (the exact invariant verified in the prototype run).
- Edge case: `--particle-unit um2` on a dataset with no `pixel_size_um` → that dataset's particle phase fails explicitly (matches `_resolve_min_area_px`), recorded, run continues.
- Edge case: dataset missing the named `--mask` → skipped per `measure_one` tolerance; reported.
- Edge case: `--mask` omitted on a dataset with multiple masks → all are measured **and** a stderr line lists the auto-selected mask names.
- Parity: CLI `default` preset columns equal the dialog's default selection sets (assert both read the same `csv_columns.py` constants) — guards GUI/CLI drift without the method extraction.
- Integration: two datasets with different mask names → combined export has both particle-column families; per_dataset CSVs are clean.
- Seam: no Qt/napari import.

**Verification:** On the two Desktop datasets with `--min-particle-area 9`, output matches the hand-driven `particle_run_*` folders.

---

- U7. **CLI `percell4-inspect`: human-readable dataset metadata + layer inventory**

**Goal:** Print, per dataset, file size + all metadata + every layer (intensity, labels, masks, groups, tracks) with name/shape/dtype and correct payload classification, without decoding arrays.

**Requirements:** R5, R6, R7

**Dependencies:** U4

**Files:**
- Create: `src/percell4/interfaces/cli/inspect_dataset.py`
- Modify: `pyproject.toml` (`percell4-inspect = "percell4.interfaces.cli.inspect_dataset:main"`)
- Test: `tests/test_cli_inspect_dataset.py`

**Approach:**
- `main(argv=None) -> int`; positional datasets via `resolve_paths`; optional `--json` for machine output (human-readable default).
- Per dataset: `store.path.stat().st_size` (human-formatted), `store.metadata` block (`channel_names`, `native_shape`/resolution, `pixel_size_um`, `n_timepoints`, `creation_bin`, `source`, plus any FLIM keys present), then per prefix in `(intensity, labels, masks, groups, tracks)` list names and print shape (`store.array_shape`) + dtype (`store.array_dtype` from U4) — **no `read_array`**.
- Classify segmentation vs mask: a `/labels` name also in `list_masks()` is reported under masks; segmentations = `list_labels() − list_masks()`. Note masks are binary `uint8` by contract.
- **Output format (resolved — so the implementer doesn't invent a contract users script against):** one section per dataset, fields in this order — `File` (path), `Size` (human-scaled: B/KB/MB/GB), `Resolution` (`native_shape` as `H×W` px), `Pixel size` (`pixel_size_um` as `0.0638 µm/px`, or `(y, x)` if anisotropic, `—` if unknown), `Timepoints`, `Channels` (names), `Created bin`, `Source` (if present), then a `Layers` block grouped by kind (Intensity / Segmentations / Masks / Groups / Tracks) listing `name  shape  dtype` aligned in columns. Absent optional fields and empty groups print `—`. Provide `--json` for machine output (schema = the same fields; documented as the stable contract).
- **Error states:** a path that fails to open (truncated/corrupt/not-HDF5) prints a one-line error to stderr and continues to the next dataset; exit `0` iff ≥1 dataset was inspected successfully, else `1`.

**Patterns to follow:** `_batch_report.resolve_paths`; the large-file-load learning (no-decode shape/dtype); `store.metadata` guaranteed-vs-optional key handling with `.get`.

**Test scenarios:**
- Happy path: fixture with `/intensity`, `/labels/<a>`, `/masks/<b>`, `/groups/<b>` → output lists each with correct shape + dtype, file size, and metadata fields. Covers R5.
- Edge case: name present under both `/labels` and `/masks` → classified as a mask, not double-counted as a segmentation.
- Edge case: dataset missing optional metadata (`pixel_size_um`, `channel_names`) → prints a placeholder, no crash.
- No-decode: inspecting a dataset does not call `read_array` (assert via patch/spy on the decode path) — guards against the 2026-06-07 regression.
- Error state: a corrupt/non-HDF5 file among the inputs → one stderr error line, other datasets still inspected, exit reflects partial success.
- Integration: multiple datasets in one invocation each get a labeled section; exit `0`.
- Seam: no Qt/napari import.

**Verification:** `percell4-inspect ~/Desktop/Untreated_Merged.h5 ~/Desktop/Nutlin3a_Merged.h5` prints both datasets' layers/metadata quickly (no multi-second decode) and correctly distinguishes `cellpose` (segmentation) from `P-body_mask`/`grouped` (masks).

---

### Phase 3 — Documentation

- U8. **Update module docs and audit artifacts**

**Goal:** Keep current-state docs accurate after the new fields, runner branch, dialog control, and CLIs land.

**Requirements:** R1–R7 (documentation of)

**Dependencies:** U1–U7

**Files:**
- Modify: `src/percell4/workflows/CLAUDE.md` (new `WorkflowConfig` fields)
- Modify: `src/percell4/gui/workflows/CLAUDE.md` (runner masks-only branch; dialog mask picker)
- Modify: `src/percell4/interfaces/cli/` docs surface and the root `CLAUDE.md`/README CLI list (three new console scripts)

**Approach:** Describe only the new current state (no history/plans, per the documentation rules). Add the three console scripts to any CLI inventory. (The `gui-element-classification.yaml` audit entry for the mask picker is updated in U3, where the control is built — not duplicated here.)

**Test scenarios:** Test expectation: none — documentation only.

**Verification:** Docs mention the new fields, runner branch, dialog control, and three CLIs; no stale "rounds always required" language remains.

---

## System-Wide Impact

- **Interaction graph:** `WorkflowConfigDialog` → `main_window.py` → `SingleCellThresholdingRunner` gains a masks-only path; all three CLIs are new entry points into the existing `phases.py` core. No change to napari/session signal flow (the dialog picker is dialog-local).
- **Error propagation:** Missing labels/masks surface as clean per-dataset failures (CLIs: stderr + nonzero exit iff all fail; runner: `record_failure` + continue), matching existing batch behavior.
- **State lifecycle risks:** `percell4-batch-threshold` mutates each `.h5` (`/masks` + `/groups`) — must binarize at the write boundary and write per-operation (existing `apply_threshold_headless` contract). `percell4-batch-measure`/`export_run` write only to the run folder (provenance invariant). The inspector is read-only.
- **API surface parity:** The CSV-column default logic must be shared between the dialog and `percell4-batch-measure` (U6) so GUI and CLI cannot drift.
- **Integration coverage:** Runner masks-only path and `percell4-batch-measure` both rely on `measure_one`'s name-keyed, missing-tolerant mask reads across multiple timepoints/datasets — covered by integration-style fixture tests, not just unit mocks.
- **Unchanged invariants:** Per-cell measurement math, particle algorithm, CSV schema, mask binary-`uint8` contract, and the "measurements live only in the run folder" rule are all explicitly unchanged; the threshold compute/apply phases are untouched (only conditionally skipped).

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Empty `thresholding_rounds` blanks `export_run` summaries in mask-reuse mode | `export_run` gains an explicit round-names parameter (U2); the runner/CLI pass the measured mask names; tests assert `summary_groups.csv` + `n_rounds_thresholding` populate. |
| Per-dataset selections leak across datasets via a shared round list | Both measure call sites source specs from `existing_mask_selections[entry.name]` (U2), never the union; integration test asserts dataset A has no column for B's mask even when that name physically exists on A. |
| `percell4-batch-threshold` silently destroys a same-name mask | Pre-write `array_exists` check; error + `return 1` unless `--overwrite` (U5); collision test. |
| GUI and CLI CSV-default columns drift | Shared default-selection **constants** in `workflows/csv_columns.py` consumed by both dialog and CLI (U6); parity test — no method extraction. |
| New `WorkflowConfig` fields silently dropped on Resume | `config_to_dict`/`from_dict` are hand-enumerated and explicitly extended (U1); round-trip test asserts the keys are present and missing-key load defaults safely. |
| Inspector reintroduces the full-decode performance bug | U4 no-decode dtype accessor + U7 no-decode test asserting `read_array` is never called. |
| `existing_mask_selections` doesn't round-trip in `run_config.json` | U1 explicitly tests `config_to_dict`/`config_from_dict` for the nested dict. |
| Relaxed empty-rounds invariant lets a truly empty config through | Invariant is conditional (`use_existing_masks and any(selections)`); negative tests in U1. |
| `percell4-batch-threshold` writes a stale `/measurements` into `.h5` | Tool writes only `/masks` + `/groups`; no measurement path invoked; provenance test in U5. |
| T1 module edits (`config_dialog.py`, `store.py`) skip learnings retrieval | Run `python3 scripts/learnings_applicability.py <path>` / the `ce-learnings-researcher` agent before editing (already done for this plan; re-run at edit time). |

---

## Documentation / Operational Notes

- Three new console scripts (`percell4-batch-threshold`, `percell4-batch-measure`, `percell4-inspect`) require `pip install -e .` to register entry points after `pyproject.toml` changes.
- Update the CLI inventory wherever the existing `percell4-batch-*` tools are listed.
- After landing, consider `/ce-compound` to capture the runner phase-skipping and headless-CLI patterns — the learnings agent noted `runner.py`, `workflows/phases.py`, and `interfaces/cli/` are currently net-new institutional ground with no registered canonical entries.

---

## Sources & References

- Prototype driver and reference outputs: `/tmp/pc4_particle_run.py`, `~/Desktop/particle_run_Untreated_Merged_*`, `~/Desktop/particle_run_Nutlin3a_Merged_*` (hand-driven measure→particle→export at min area 9 px).
- Related (separate) effort: `docs/brainstorms/2026-06-03-headless-grouped-thresholding-puncta-requirements.md` (new puncta detectors — not this plan).
- Key code: `src/percell4/gui/workflows/single_cell/config_dialog.py`, `src/percell4/gui/workflows/single_cell/runner.py`, `src/percell4/workflows/phases.py`, `src/percell4/workflows/models.py`, `src/percell4/store.py`, `src/percell4/interfaces/cli/batch_process.py`, `src/percell4/interfaces/cli/_batch_report.py`.
- Learnings: `docs/solutions/logic-errors/large-file-load-metadata-read-full-decode-2026-06-07.md`, `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`, `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`, `docs/solutions/logic-errors/batch-compress-development-lessons.md`, `docs/solutions/architecture-patterns/atomic-write-contract.md`, `docs/solutions/logic-errors/grouped-thresholding-development-lessons.md`.
</content>
</invoke>
