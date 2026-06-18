---
title: "feat: Incorporate whole-field and multichannel mask-intensity scripts as registered analyses"
type: feat
status: active
date: 2026-05-28
deepened: 2026-05-28
---

# feat: Incorporate whole-field and multichannel mask-intensity scripts as registered analyses

## Overview

Add two more standalone scripts from `~/mask-intensity-analysis-repo`
to PerCell4 as **registered analyses**, following the `per_particle_donut`
pattern shipped in `docs/plans/2026-05-27-004-feat-analysis-integration-plan.md`:

1. **`per_particle_multichannel`** — per-particle dilute-vs-condensed phase
   analysis across an arbitrary set of fluorescence channels (raw intensities,
   no background subtraction), with an optional single-cell aggregation mode
   that also emits whole-cell intensity statistics.
2. **`whole_field_intensity`** — whole-field (aggregate) decapping-sensor
   quantification of a Halo channel vs an mNG normalization channel across
   masked compartments (P-body / dilute, plus an optional intermediate
   compartment), with per-field background subtraction and a single-cell mode.

Each analysis reuses the existing framework (registry, loader,
`run_analysis`, batch runner, run-folder, Scripts tab) and ships the same
artifact set the donut migration did: a **pure domain core** shared by a
**repo-root CLI wrapper** and a **declarative `Analysis` module**, a **GUI
dialog**, and a **numeric-parity regression fixture**. Two preparatory units
come first: a shared-helper extraction (so the two new cores and donut don't
triplicate the donut geometry / particle→cell assignment) and a small,
opt-in framework extension that threads the user-chosen layer **names** into
`run()` so multichannel output columns keep their friendly names
(`condensed_<channel>_mean`).

This is an I/O-boundary refactor + schema declaration, **not** a math
rewrite — analysis math is preserved verbatim and pinned by regression
fixtures, exactly as the donut migration was.

---

## Problem Frame

PerCell4's value proposition is tracking single cells across analysis steps.
The lab's analysis methods currently live as standalone CLI scripts in a
sibling repo. The donut migration proved the registered-analysis framework on
one example; the framework's own plan named the **second migration as the
intended stress test** of the framework's edges
(`docs/plans/2026-05-27-004-...` → "Second migration target … the framework
edges are partly speculative on a single example").

The user wants `whole_field_analysis.py` and `per_particle_multichannel.py`
incorporated **the same way as the donut analysis**: `.h5` dataset layers
assigned to named roles, presets and options surfaced in a dialog, batch-run
across datasets to CSV. Both scripts are genuine standalone assays (distinct
science from donut), so each becomes its own registered analysis.

Two framework edges surface immediately — and are exactly the stress-test the
framework plan anticipated:

- **Variable-arity channels** (multichannel): the CLI auto-detects an
  arbitrary number of channels named after their files. The declarative
  framework needs statically-declared roles, and the loader is **role-keyed**
  (it discards the on-disk layer name). Resolved below as a fixed pool of 8
  channel slots + a dynamic "Add channel" dialog + a tiny `run()`-contract
  extension to recover the chosen layer names for column naming.
- **Cross-dataset state** (whole-field `decapping-sensor-v1` / `SiR_mean`):
  the framework is strictly per-dataset. Resolved below by dropping v1 (user
  confirmed it is no longer used) and shipping v2–v5.

---

## Requirements Trace

- R1. `per_particle_multichannel` is registered (`@register_analysis`),
  discoverable in the Scripts tab, and batch-runnable to CSV with numeric
  parity to the original CLI.
- R2. `whole_field_intensity` is registered, discoverable, and batch-runnable
  to CSV with numeric parity to the original CLI for presets **v2–v5**.
- R3. Both analyses share one **pure domain core** with their repo-root CLI
  wrapper (single source of truth; CLI and framework cannot diverge silently).
- R4. The multichannel dialog lets the user **add an arbitrary number of
  channel rows (up to 8)** to analyze, modeled on the "Threshold Rounds"
  add/remove-row UX; output columns are named by the user-chosen layer name.
- R5. Both dialogs surface presets/options the donut way: dataset picker,
  role→layer combos (incl. branch/optional handling), preset lock, output
  folder, live Start-gating; both are **Actions** (never write the five
  session selection fields).
- R6. Presets are in-code constants pinned by a committed JSON snapshot +
  immutability test (the donut mechanism). `whole_field_intensity` ships
  presets `decapping-sensor-v2..v5`. `per_particle_multichannel` ships none.
- R7. Analysis math is preserved verbatim; no reimplementation. Numeric parity
  is enforced by regression fixtures built **before** any refactor
  (characterization-first), compared **per-dataset** (CLI single-prefix dir vs
  framework `per_dataset/<stem>_<table>.csv`): drop BOTH `group` and `dataset`
  id columns, reindex both to `sorted(columns)`, sort rows by the stable id key,
  then integer exact-equal + float `np.allclose(rtol=1e-10, equal_nan=True)`.
- R8. Image outputs persist only into existing `/masks/<name>` /
  `/labels/<name>` via the batch runner's canonical `store.write_mask` /
  `store.write_labels`; no dataset-schema migrations; no output named
  `whole_field` (reserved by the existing whole-field segmentation).
- R9. The framework extension that threads layer names into `run()` is
  **opt-in** (forwarded only to a `run()` that names the kwarg), so no existing
  analysis or test stub breaks.
- R10. Shared per-particle/donut helpers used by more than one analysis live in
  one place (`domain/analysis/_impl/_shared.py`), not duplicated per analysis.

---

## Scope Boundaries

- **Two analyses only.** `per_particle_multichannel` and
  `whole_field_intensity`. The other sibling-repo scripts are out of scope:
  `mTQ2-4A-4B-analysis.py` and `Type1_Type2_analysis.py` (standalone but not
  requested now), `add_area_um2.py` (overlaps existing
  `workflows/phases.py::_add_area_um2_columns`; a CSV post-process that does
  not fit the per-image-set `Analysis` contract), `snippets_bg_and_donut.py`
  (a teaching extract already fully covered by `per_particle_donut`).
- **`decapping-sensor-v1` is dropped** (user no longer uses it; its `SiR_mean`
  Halo background requires cross-dataset As/UT pairing the per-dataset runner
  cannot do). The `SiR_mean` choice is removed from `halo_bg_mode`.
- **`save_processed` (whole-field) is dropped** — it emits float32
  troubleshooting TIFFs; `ImageOutput` supports only `binary`/`labels`, and it
  is off in every shipped preset.
- **No math reimplementation, no 3D inputs** (`ndim=(2,)`), no napari/Qt
  coupling in analysis logic, no YAML workflow loader, no plugin marketplace,
  no touching `src/percell4/plugins/`. (Inherited verbatim from the framework
  plan's scope boundaries.)
- **No variable-arity framework primitive.** Multichannel uses a fixed 8-slot
  pool, not a new generic "repeatable role" concept (honors the framework
  plan's "do not pre-generalize on a single example").

### Deferred to Follow-Up Work

- **Cross-dataset SiR_mean (whole-field v1)**: if ever needed, a pre-pass that
  pairs As/UT datasets and injects a per-dataset Halo-bg override — a separate
  plan; the framework is per-dataset by design.
- **Per-cell results into the measurements DataFrame**: single-cell results
  stay CSV-only (same boundary the donut migration left).
- **`mTQ2-4A-4B-analysis.py` / `Type1_Type2_analysis.py`** migrations: separate
  future plans (research already mapped them; not requested now).
- **`add_area_um2` µm² conversion** for these analyses' CSVs: reuse/extend the
  existing `workflows/phases.py::_add_area_um2_columns` at CSV-write time
  rather than a new analysis — separate follow-up.

---

## Context & Research

### Relevant Code and Patterns

- **Reference analysis (the template to mirror end-to-end):**
  - Pure core: `src/percell4/domain/analysis/_impl/per_particle_donut.py`
    (`run_one_image_set`, `analyze_regions`, `assign_particles_to_cells`,
    `aggregate_by_cell`, `estimate_bg_threshold`).
  - Module: `src/percell4/application/analysis/modules/per_particle_donut.py`
    (`@register_analysis`, schema, `run()` dispatch, module-level
    `produced_when` callables).
  - CLI wrapper: `per_particle_analysis.py` (repo root; imports the pure core).
  - Dialog: `src/percell4/gui/per_particle_donut_dialog.py`; shared factories
    `src/percell4/gui/analysis_widgets.py`.
- **Framework seams (reused, mostly unchanged):**
  - Types/base: `src/percell4/domain/analysis/types.py`,
    `src/percell4/domain/analysis/base.py`.
  - Registry + schema validation: `src/percell4/application/analysis/registry.py`.
  - Loader (role-keyed, kind-dispatched): `src/percell4/application/analysis/loader.py`.
  - Single-run + batch: `src/percell4/application/use_cases/run_analysis.py`,
    `.../run_analysis_batch.py`; run-folder `src/percell4/application/analysis/run_folder.py`.
  - Scripts-tab wiring (generic over `list_analyses()`):
    `src/percell4/interfaces/gui/main_window.py` (`_create_scripts_panel`,
    `_on_open_analysis`).
- **Dynamic "Add channel" row UI** — two in-repo patterns:
  - Pattern A (`QTableWidget`, one row per item):
    `src/percell4/gui/workflows/single_cell/config_dialog.py`
    (`WorkflowConfigDialog._build_rounds_group` / `_on_add_round` /
    `_on_remove_round` / `_read_round_row` / `_rounds_from_table`). This is the
    "Threshold Rounds" UI the user referenced.
  - Pattern B (`QListWidget` + `setItemWidget` row frames with a per-row "×"
    button, backed by a Python list rebuilt via `enumerate`):
    `src/percell4/gui/phasor_masks_dialog.py`
    (`_refresh_dataset_list` / `_build_dataset_row_widget` / `_on_remove_row`).
    **Pattern B is the closer match** (per-row remove button + a combo per
    row) and is the recommended template for the channel list.
  - Both are hand-rolled; each channel row's combo should still be built with
    `build_layer_combo()` + `populate_layer_combo()` from `analysis_widgets.py`.
    Neither pattern has a row cap today — **the up-to-8 cap is new behavior**
    (gate the "Add channel" button in `_refresh_state()`, mirroring the
    existing min-count Start gating).
- **Whole-field naming/overlap caveat:** PerCell4 already has a `whole_field`
  baseline segmentation (`/labels/whole_field`, written by
  `application/use_cases/batch_create_whole_field_segmentation.py`) and an
  interactive "Whole Field Thresholding" panel
  (`interfaces/gui/task_panels/analysis_panel.py`). Neither computes the
  decapping-sensor metrics this analysis does. **Do not** name any output
  `whole_field`; register as `whole_field_intensity` with a clear
  `display_name` to avoid user confusion.

### Institutional Learnings (`docs/solutions/`)

- **Run/preset/batch buttons must be pure Actions** — never call
  `session.set_active_*` / `data_model.set_active_*` as a side effect
  (`gui-action-contract-exhaustiveness.md`). Verify with the documented grep
  before merging each dialog.
- **Extract shared widgets when building sibling dialogs**
  (`sibling-dialog-extract-shared-widget-2026-05-12.md`) — consume
  `analysis_widgets.py` factories; never rebuild combos/spinboxes from a params
  dataclass.
- **Wrap tall dialogs** in `wrap_in_scroll(...)` + `cap_to_screen(self)`
  (`dialog-scroll-when-tall.md`) — there is a CI compliance test
  (`tests/test_gui/test_dialog_helper_compliance.py`) that fails the build for
  a `gui/**/*Dialog.py` that skips the helper.
- **Atomic file writes** for any new output file
  (`atomic-write-contract.md`) — the existing run-folder writers already
  satisfy this; new CSV/image outputs go through the batch runner's existing
  persist path, so no new write site is introduced.
- **Loader maps layers by role, not by name; results never overload
  `/labels/`** (`napari-mask-layer-misclassified-as-segmentation.md`,
  `one-payload-type-per-h5-group`).
- **Numeric-parity must also be validated in-process**
  (`in-session-hdf5-staleness-multi-vector-2026-04-30.md`) — the subprocess CLI
  parity test is necessary but not sufficient; pure-core and `run_analysis`
  tests run in-process (write → read → assert) and cover the staleness vector.
- **The analysis package is absent from `docs/audits/canonical-sources-matrix.yaml`**
  and has no `docs/solutions/` entry — the framework is new and undocumented in
  institutional memory. This second migration is the trigger to write
  `docs/writing_an_analysis.md` and register the analysis globs (see U9).

### Per-script mapping summaries (from research)

**`per_particle_multichannel.py` → `per_particle_multichannel`** (complexity:
high). Required: `mask` (binary particle mask) + `channel_1` (intensity).
Optional: `cp_mask` (labels) + `channel_2..channel_8` (intensity). **No channel
group** (see Key Technical Decisions — a group would force all 8 channels
required). Params: `buffer`(int=5), `donut`(int=5),
`min_size`(int=4), `single_cell`(bool=False, requires `cp_mask`),
`export_donuts`(bool=False). **No presets.** Outputs: `particle_table`
(produced_when `not single_cell`), `cell_table` (produced_when `single_cell`),
`multichannel_donut_mask` (binary ImageOutput, produced_when `export_donuts`;
namespaced to avoid a generic `/masks/donut_mask` collision). No background
subtraction, no norm channel; raw `np.mean` over float64 pixels. `run()` returns
**exactly one** table (`particle_table` XOR `cell_table`) matching `single_cell`
— never both, or `run_analysis` raises "undeclared output". When `cp_mask` is
mapped but `single_cell=False`, particle rows still gain a `cell_id` column
(CLI behavior — `assign_particles_to_cells`, unmatched→0). Single-cell
mode adds area-weighted aggregation **plus** whole-cell stats (mean/median/
mode/min/max/integ) computed directly from cell-mask pixels, emitting a row for
every nonzero cell id (even empty ones). Shares donut geometry +
`assign_particles_to_cells` + `_weighted_mean` + `_ratio` (→ `_shared.py`).
`_whole_cell_stats` stays local to this impl. **Defaults differ from donut**
(buffer 5 not 4, min_size 4 not 10) — do not copy donut defaults.

**`whole_field_analysis.py` → `whole_field_intensity`** (complexity: high).
Required: `pbody_mask`, `dilute_mask` (binary), `halo`, `mng` (float). Optional:
`cp_mask`, `dcp2_mask`, `interaction_mask`, `sir_mask`, `dcp2_mask_2`,
`interaction_mask_2` — **all optional, no `intermediate` group** (a group would
force all four required; see Key Technical Decisions). The four masks needed for
v4/v5 three-region mode are enforced via `intermediate_assemblies =
BoolParam(requires=(those four roles))`. Params include
the dual-typed background modes (modeled as ChoiceParam + IntParam pairs — see
Key Technical Decisions), `mNG_filter`/`FLIM_filter`/`SiR_subtract` (choice
incl. `none`, mapped `"none"→None` in `run()`),
`SiR_filter`/`mNG_in_FLIM`/`percent`/`intermediate_assemblies`/
`intermediate_zero_fill`/`single_cell` (bools with `requires`), `min_size`(int).
Presets `decapping-sensor-v2..v5`. One `whole_field_table` output whose columns
vary by mode (two-region / three-region / single-cell / three-region+single-cell).
Single-cell intersects
each compartment with each cell region and recomputes the field math per cell
(not donut-style post-hoc aggregation). Core math: `compute_bg_value`,
`filter_mask_by_size`, `_measure_region`, `_measure_v4_regions`,
`parse_bg_mode`. Shares `filter_mask_by_size`, `_ratio`, nan-safe stat helpers
(→ `_shared.py`).

---

## Key Technical Decisions

- **Two analyses, donut-pattern each.** Pure core in
  `domain/analysis/_impl/<name>.py` (kwargs-only, no I/O, one dataset/call);
  repo-root CLI wrapper imports it; declarative module wraps it; dialog +
  regression fixture per analysis. (R3, R7.)
- **Shared helpers first (`_shared.py`).** Extract donut geometry, particle→cell
  assignment, `_weighted_mean`, `_ratio`, label+min_size filter, and nan-safe
  stats into `domain/analysis/_impl/_shared.py`; refactor
  `per_particle_donut/_impl` to consume them (behavior-preserving, guarded by
  the existing donut regression + pure-core tests). Both new cores consume
  `_shared`. (R10.) Rationale: avoids triplicating the donut geometry the
  moment the second analysis lands; the existing parity fixture makes the donut
  refactor safe. `assign_particles_to_cells` gains an explicit `unmatched`
  policy parameter (donut omits unmatched particles; multichannel/whole-field
  semantics differ) so one helper serves all callers without changing any
  caller's numerics.
- **Multichannel = fixed 8-slot pool + dynamic dialog + opt-in `layer_map`
  threading.** The dialog (Pattern B) dynamically adds up to 8 channel rows;
  the i-th row fills `channel_{i}`. Output columns are named by the **chosen
  layer name**, recovered via a new opt-in `layer_map` kwarg on
  `Analysis.run()` (the loader is role-keyed and drops names). (R4, R9.)
- **Channel slots are NOT a single multi-role group** (correctness, not
  cosmetics). `run_analysis` computes `group_satisfied = all(role in layer_map
  for role in group_roles)` and `at_least_one` then requires ≥1 group
  satisfied — so a single 8-role `channels` group would be satisfied only when
  **all 8** slots are filled, raising `ValueError` for the normal 2–4-channel
  case before `run()` is reached. Therefore model **`channel_1` in
  `required_inputs`** and **`channel_2..channel_8` in `optional_inputs`**, with
  **no channel group**. The dynamic dialog always fills `channel_1` first, so
  "≥1 channel" is structurally guaranteed; Start-gating requires ≥1 channel row.
- **Whole-field intermediate masks are NOT a group either** (same trap). A
  single `intermediate` group with `at_least_one` would make all four
  intermediate masks de-facto required, breaking every two-region / v2 / v3 run
  (which supply none). Model `dcp2_mask`, `interaction_mask`, `dcp2_mask_2`,
  `interaction_mask_2` as **`optional_inputs` with no group**
  (`group_requirement` trivially satisfied), and enforce "all four needed for
  v4/v5" via `intermediate_assemblies = BoolParam(requires=(those four roles))`
  — `BoolParam.requires` is checked only when the bool is `True`.
- **`run()` gains an opt-in `layer_map` kwarg.** Add
  `layer_map: dict[str, str] | None = None` (keyword-only) to
  `Analysis.run()`; extend `run_analysis._accepted_progress_kwargs` to forward
  it by the same signature-introspection mechanism used for `log`/`set_label`.
  `run_analysis_batch.py` needs no change (it passes `layer_map` positionally to
  `run_analysis`, which threads it). Forwarded **only** to a `run()` that names
  the kwarg → no existing stub breaks. The pure cores stay role-keyed; the
  declarative `run()` wrapper does the column rename. (R9.)
- **Whole-field background modes modeled as ChoiceParam + IntParam pairs.** The
  framework has no free-string param. Model `mng_bg_mode` /`halo_bg_mode` as a
  `ChoiceParam` (enum keywords incl. `"manual"`; halo adds the `mng-nan*`
  variants; **`SiR_mean` excluded** with v1) plus `mng_bg_value`/`halo_bg_value`
  `IntParam`s used when the choice is `"manual"`. The pure core's
  `parse_bg_mode` maps `(choice, value)` to the original CLI mode string,
  preserving exact `ceil`/`int` rounding per branch. v2–v5 presets set the
  choice to `"manual"` with value `0` (the CLI's `bg_mode=0` "no subtraction").
- **One whole-field table with mode-dependent columns.** Single
  `whole_field_table` TableOutput (always produced); its DataFrame columns
  differ by mode (two-region / three-region / single-cell). Document that users
  should not mix two-region and three-region datasets in one batch (the
  `combined_*.csv` would have ragged columns; `pd.concat` NaN-aligns but the
  layout is mixed). The dialog can warn. Avoids ragged dual-output bookkeeping.
- **Cross-cutting param constraints enforced in `run()` (and the dialog), not
  the schema.** `BoolParam.requires` expresses "needs this layer/group" but not
  "mutually exclusive with param X" or "requires param Y is set". Enforce
  `SiR_subtract` xor `SiR_filter`; `intermediate_assemblies` requires
  `mNG_filter`+`FLIM_filter`+the `_2` masks and forbids any SiR option — as
  explicit `ValueError` guards in the whole-field `run()`/core, mirrored as
  dialog gating.
- **Dialogs are Actions.** Neither dialog writes the five session selection
  fields. Mask/label outputs persist via the batch runner's existing
  `_persist_outputs` path (same as donut's donut masks), not a GUI Creator. (R5,
  R8.)
- **CLI wrappers included.** Each analysis ships a repo-root CLI wrapper
  (copied from the sibling repo, refactored to import the pure core) — the v1
  headless surface and the harness the subprocess regression-parity fixture
  drives.
- **Parity is compared PER-DATASET, with column normalization.** The donut
  harness sorts rows but asserts **exact column-order equality**, and the
  framework emits columns in pure-core dict-insertion order + a `dataset` id
  column, whereas the CLIs emit a hand-ordered `base_cols + per-channel/region
  blocks` + a `group` id column. So the new regression harnesses must: (1)
  compare at **per-dataset granularity** — CLI run on a single-prefix directory
  vs the framework's `per_dataset/<stem>_<table>.csv` for the matching `.h5`
  (avoids the non-unique-`cell_id`/`particle_id` cross-dataset interleave under
  a combined sort); (2) **drop BOTH `group` and `dataset`** id columns; (3)
  **normalize columns** (reindex both to `sorted(columns)`) before the
  value comparison; (4) then sort rows by the stable id key and apply the donut
  numeric rule (int exact, float `allclose(rtol=1e-10, equal_nan=True)`). Build
  each fixture **before** refactoring (characterization-first). Validate both
  via subprocess (CLI CSV vs framework per-dataset CSV) and in-process (pure
  core / `run_analysis`).
- **Multichannel combined-CSV column union is a synthetic in-process test, not
  a GUI-reachable subprocess case.** The CLI back-fills the union of channel
  names (`sorted`) across groups; the framework's `pd.concat` preserves
  first-seen order, not sorted. The v1 GUI passes the *same* `layer_map` to
  every dataset (`lambda _p: layer_map`), so heterogeneous per-dataset channel
  sets are not reachable through the dialog. Cover the union/NaN-fill behavior
  with a synthetic `run_analysis`-level test that reindexes the combined columns
  to `base_cols + [block for ch in sorted(union)]`; do not attempt it as a
  subprocess parity case.
- **Cross-cutting param-constraint guards are a deliberate behavioral upgrade.**
  The whole-field CLI *warns-and-`return`s* (aborting the whole run, emitting no
  CSV) on SiR/intermediate conflicts — it does not raise. Modeling them as
  `ValueError` in `run()` is intentionally stricter (a per-dataset failed item,
  isolated by `BatchAnalysisItemResult`, plus pre-dispatch dialog gating).
  Parity fixtures therefore cover **only valid** param combinations (the CLI
  produces no baseline CSV for invalid ones); conflict cases are error-path
  unit tests on `run()`/the core, never parity cases.
- **Choice `"none"` ↔ Python `None` and `"manual"` ↔ integer normalization.**
  `mNG_filter`/`FLIM_filter`/`SiR_subtract` are `ChoiceParam(("none","zero",
  "NaN"))`; the whole-field `run()` maps `"none" → None` before calling the
  core (the core's `is None` branch semantics are preserved). Likewise the
  bg-mode pair maps `choice=="manual"` to the CLI's integer path. The preset
  dicts store the choice strings (not Python `None`); the snapshot test pins
  them. Both mappings are pinned in the pure-core test.
- **Whole-field `particle_count` keeps its own `np.unique`+`argmax`+skip-0
  assignment.** It is NOT the donut/multichannel `np.bincount().argmax()` —
  the tie-break and background-majority handling differ. The shared
  `assign_particles_to_cells` (U1) serves donut + multichannel only; whole-field
  retains its assignment verbatim, pinned by a tie-break fixture.

---

## Open Questions

### Resolved During Planning

- **Multichannel variable channel count** → fixed pool of **8** slots + dynamic
  "Add channel" dialog (user: "never more than 8" + wants a Threshold-Rounds-
  style add UI). No framework-wide variable-arity primitive.
- **Whole-field `decapping-sensor-v1` / `SiR_mean`** → **dropped** (user no
  longer uses it). Ship v2–v5.
- **How to get chosen layer names into the core** → opt-in `layer_map` kwarg on
  `run()` (loader confirmed role-keyed; this is the only channel; change is two
  files, no stub breaks).
- **Dynamic-row UI template** → Pattern B (`QListWidget` + per-row "×",
  Python-list backing, `enumerate` rebuild) from `phasor_masks_dialog.py`, with
  rows built from `analysis_widgets.build_layer_combo`.
- **Whole-field bg-mode dual-typed param** → ChoiceParam + IntParam pair, parsed
  in the core.

### Deferred to Implementation

- **Exact 8-slot vs N**: 8 is the declared ceiling; if a real dataset exceeds it
  the cap is a one-line change. Confirm 8 is comfortable when wiring the dialog.
- Final helper signatures in `_shared.py` (names/arguments) — settle when
  extracting against the real donut code.
- Whether to additionally reproduce the CLI's exact column **order** in `run()`
  (vs. only normalizing columns in the parity harness). The plan's parity rule
  normalizes columns in the test; emitting CLI order from `run()` is a nicety
  to decide during U4/U7.

(Resolved during planning, moved out of "deferred": the combined-CSV column
union is now a synthetic in-process test with explicit `sorted(union)`
reindexing, and the whole-field `particle_count` keeps its own
`np.unique`+`argmax`+skip-0 assignment verbatim — see Key Technical Decisions.)

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for
> review, not implementation specification. The implementing agent should treat
> it as context, not code to reproduce.*

**Layered shape per analysis (mirrors donut):**

```
repo-root CLI wrapper            declarative module                 GUI dialog
<name>.py                        application/analysis/modules/      gui/<name>_dialog.py
  reads TIFFs, dir-walk,           <name>.py                          dataset picker,
  writes CSV/TIFF        ──┐       @register_analysis(...)            role→layer combos,
                          │        schema (roles/params/presets/     preset lock, Start
                          │        outputs) + run() wrapper  ◄──────── (Action) → batch_run_analysis
                          │          │  (renames cols via layer_map)
                          ▼          ▼
        domain/analysis/_impl/<name>.py   run_one_image_set(*, arrays, params, set_label, log)
                          │          │     PURE: no I/O, one dataset per call
                          └────┬─────┘
                               ▼
        domain/analysis/_impl/_shared.py   donut geometry · assign_particles_to_cells(unmatched=…)
                                           · _weighted_mean · _ratio · label+min_size · nan-safe stats
```

**Opt-in `layer_map` threading (framework extension, U2):**

```
batch_run_analysis(layer_map_resolver)         # UNCHANGED — passes layer_map positionally
   └─ run_analysis(name, h5, layer_map, …)      # has layer_map param, but today uses it
        │                                        #   ONLY for load_layers + validation,
        │                                        #   NOT forwarded to run(). U2 changes this.
        ├─ _accepted_progress_kwargs(run, log=, set_label=, layer_map=)   # U2 edit #1: +layer_map arg + forward-by-introspection
        └─ run_callable(arrays, resolved, **run_kwargs)  # U2 edit #2: run_kwargs may now include layer_map
                                                     # forwards layer_map ONLY if run() names it (or **kwargs)
multichannel.run(... layer_map): build channels dict keyed by layer_map[channel_i]
  → columns come out as "condensed_<layername>_mean" (core is role-agnostic re: names)
```

**Multichannel produced_when matrix:**

| Output         | produced_when            |
|----------------|--------------------------|
| `particle_table` | `not params["single_cell"]` |
| `cell_table`     | `params["single_cell"]`     |
| `multichannel_donut_mask` | `params["export_donuts"]`   |

`run()` must return exactly the produced key set (one table + maybe the mask);
returning a non-produced key raises "undeclared output" in `run_analysis`.

**Whole-field mode → table columns (one `whole_field_table`):**

| Mode (params)                                   | Column set |
|-------------------------------------------------|-----------|
| two-region (default)                            | pbody/dilute mean/integ/ratios (+pct if `percent`) |
| `intermediate_assemblies=True` (v4/v5)          | + intermediate_* mirror columns |
| `single_cell=True`                              | prepend cell_id/cell_area_px/particle_count, append mNG_cell_mean |
| `single_cell=True` AND `intermediate_assemblies=True` | three-region columns + the four per-cell columns |

---

## Implementation Units

Phases are commit-and-review boundaries. Order: U1 → U2 → U3 → U4 → U5 → U6 →
U7 → U8 → U9. Every feature-bearing unit is **characterization-first** where it
refactors existing behavior, and ships test scenarios in the donut convention
(Happy path / Edge case / Error path / Integration / Cancel / Persistence).

### Phase 1 — Shared foundation + framework extension

- U1. **Extract shared per-particle/donut helpers into `_shared.py`**

**Goal:** Create one home for the math both new cores reuse, and refactor donut
to consume it, before adding the second/third copy.

**Requirements:** R10, R7.

**Dependencies:** None.

**Files:**
- Create: `src/percell4/domain/analysis/_impl/_shared.py`
- Modify: `src/percell4/domain/analysis/_impl/per_particle_donut.py`
- Create: `tests/test_domain/test_analysis_shared.py`
- Test (guard, unchanged): `tests/test_domain/test_per_particle_donut_pure.py`,
  `tests/test_scripts/test_per_particle_regression.py`

**Approach:**
- Move into `_shared.py`: the donut-ring geometry (bbox crop + pad,
  `distance_transform_edt(~crop_region)`, `in_donut_range`, `crop_donut`,
  full-image map-back, `donut.sum()==0` skip, donut-export accumulation);
  `assign_particles_to_cells(mask, cp_mask, min_size, *, unmatched)` where
  `unmatched` selects drop-particle (donut) vs map-to-0 (others); `_weighted_mean`;
  nan-safe `_ratio`; a `label_and_filter(mask, min_size)` connected-component +
  strict-`>`-min_size filter; nan-safe mean/sum guard.
- Refactor donut's `_impl` to import these; **no numeric change** — the existing
  donut pure-core tests and the subprocess regression fixture are the guard.
- **Scope guard:** the shared `assign_particles_to_cells` is the
  `np.bincount().argmax()` variant used by donut + multichannel only.
  **Whole-field's `particle_count` does NOT use it** — it has a different
  `np.unique(return_counts=True)`+`argmax`+skip-majority-0 rule that must stay
  verbatim in the whole-field impl (U6). Do not unify the two assignment rules.

**Execution note:** Characterization-first — run the existing donut regression +
pure-core tests before and after; output must be identical.

**Patterns to follow:** the existing functions in
`domain/analysis/_impl/per_particle_donut.py` (verbatim extraction).

**Test scenarios:**
- Happy path: each helper returns the same values the donut inline code did
  (port a few donut assertions to call `_shared` directly).
- Edge case: `assign_particles_to_cells` with `unmatched="drop"` vs
  `unmatched="zero"` on a particle overlapping no cell; `label_and_filter` with
  all components ≤ min_size (empty); `_ratio` with b==0/NaN.
- Integration: donut regression fixture (`test_per_particle_regression.py`)
  still passes byte-for-byte against committed expected CSVs.

**Verification:** donut tests + regression unchanged; `_shared` imported by
donut; new `_shared` unit tests pass.

---

- U2. **Framework extension: thread `layer_map` into `Analysis.run()`**

**Goal:** Let an analysis recover the user-chosen on-disk layer names (opt-in),
so multichannel can name columns by layer.

**Requirements:** R9, R4.

**Dependencies:** None (independent of U1).

**Files:**
- Modify: `src/percell4/domain/analysis/base.py` (add keyword-only
  `layer_map: dict[str, str] | None = None` to `run()` + docstring)
- Modify: `src/percell4/application/use_cases/run_analysis.py`
  (`_accepted_progress_kwargs` forwards `layer_map`; pass the in-scope
  `layer_map` at the call site)
- Test: `tests/test_application/test_run_analysis.py` (add two cases)

**Approach:**
- Mirror exactly how `log`/`set_label` are handled: signature introspection,
  forward only when the param is named or `**kwargs` present.
- `run_analysis_batch.py` is unchanged (forwards `layer_map` positionally).
- Pure cores stay role-keyed; only the declarative `run()` wrapper uses
  `layer_map` (column rename happens there).

**Execution note:** Test-first — write the "stub that names `layer_map` receives
it" and "plain `(inputs, params)` stub still runs" tests before editing.

**Patterns to follow:** `run_analysis._accepted_progress_kwargs` and the
`log`/`set_label` plumbing added in the donut verbose-output work.

**Test scenarios:**
- Happy path: a stub `run(self, inputs, params, *, layer_map=None)` receives the
  resolved role→layer_name map.
- Edge case: a stub declaring `**kwargs` receives `layer_map`.
- Integration / Error path (regression guard): a plain `run(self, inputs,
  params)` stub runs without `TypeError` and is not passed `layer_map`.

**Verification:** full `tests/test_application` green (no existing stub breaks);
new cases pass.

### Phase 2 — `per_particle_multichannel`

- U3. **Bring `per_particle_multichannel.py` into the repo; extract pure core;
  commit regression fixture**

**Goal:** The per-script "pure-core extraction" unit (donut U4 template).

**Requirements:** R1, R3, R7.

**Dependencies:** U1.

**Files:**
- Create: `per_particle_multichannel.py` (repo root; thin CLI wrapper importing
  the pure core)
- Create: `src/percell4/domain/analysis/_impl/per_particle_multichannel.py`
  (`run_one_image_set`, `_whole_cell_stats`; uses `_shared`)
- Create: `tests/test_scripts/test_per_particle_multichannel_regression.py`
- Create: `tests/fixtures/per_particle_multichannel/{group_*,group_*_expected}/`
  + `_generate_fixtures.py`
- Create: `tests/test_domain/test_per_particle_multichannel_pure.py`

**Approach:**
- Copy the sibling-repo script in, commit it unmodified first; generate the
  expected CSVs from the unmodified CLI (per-particle + single-cell modes,
  ≥2 channels, including a deliberately heterogeneous channel set across two
  groups to exercise the combined-CSV column union).
- Extract the per-image-set math into `run_one_image_set(*, mask,
  channels: dict[str, ndarray], cp_mask=None, buffer, donut, min_size,
  single_cell, export_donuts, set_label="", log=None)` returning
  `{"particle_rows", "cell_rows", "donut_mask"}`. `channels` is an ordered
  `{channel_name: float64 array}` dict so columns are named by channel; the CLI
  builds it from filenames, the module builds it from `layer_map` (U4).
- No background subtraction; raw `np.mean` over float64; preserve strict
  `> min_size`, `donut.sum()==0` skip, and `_whole_cell_stats` mode rounding.

**Execution note:** Characterization-first — fixture committed before extraction;
parity test runs before/after each refactor step.

**Patterns to follow:** `per_particle_analysis.py` (CLI wrapper shape),
`domain/analysis/_impl/per_particle_donut.py` (pure-core shape), donut fixture
layout under `tests/fixtures/per_particle/`.

**Test scenarios:**
- Happy path: per-particle mode, 3 channels — row count = surviving particles;
  `condensed_<ch>_mean`/`dilute_<ch>_mean`/`<ch>_condensed_over_dilute` per
  channel match hand-computed values.
- Happy path: single-cell mode — one row per nonzero cell id incl. an
  empty cell (particle metrics NaN, whole-cell stats present); area-weighted
  means; summed integ; recomputed ratios.
- Happy path: `cp_mask` mapped, `single_cell=False` — particle rows still carry
  a `cell_id` column (CLI behavior, unmatched→0); a dedicated fixture covers it.
- Edge case: no particles after `min_size`; `donut.sum()==0` particle skipped;
  `export_donuts` returns a uint8 union mask.
- Error path: zero channels — note this is enforced at the framework layer
  (`channel_1` is a required input → `run_analysis` raises → failed batch item),
  which intentionally differs from the CLI's silent group-skip; the pure core
  need not re-check arity.
- Integration: **per-dataset** parity — CLI on a single-prefix dir vs the
  framework's `per_dataset/<stem>_<table>.csv`; drop `group` AND `dataset`,
  reindex to `sorted(columns)`, sort rows by `particle_id`/`cell_id`, int exact,
  float `allclose(rtol=1e-10, equal_nan)`.
- Integration (synthetic, in-process): combined-CSV column union — two datasets
  with different channel layer-name sets; assert the combined columns equal
  `base_cols + [block for ch in sorted(union)]` with NaN-fill (this is NOT a
  subprocess case; the v1 dialog maps the same layers to every dataset).

**Verification:** regression fixture passes; pure-core tests pass; CLI runnable
headless.

---

- U4. **Register `PerParticleMultichannel` analysis module**

**Goal:** Declarative schema + `run()` wrapper (uses `layer_map` to name
columns); discoverable in the registry.

**Requirements:** R1, R4, R9.

**Dependencies:** U2, U3.

**Files:**
- Create: `src/percell4/application/analysis/modules/per_particle_multichannel.py`
- Modify: `src/percell4/application/analysis/__init__.py` (import to fire
  `@register_analysis`)
- Test: `tests/test_application/test_run_analysis.py` and/or
  `tests/test_application/test_run_analysis_batch.py` (add cases)

**Approach:**
- `required_inputs = {"mask": ImageRole(kind="mask", dtype="binary"),
  "channel_1": ImageRole(kind="intensity", dtype="float")}`;
  `optional_inputs = {"cp_mask": ImageRole(kind="label", dtype="labels"),
  "channel_2": …, …, "channel_8": ImageRole(kind="intensity", dtype="float")}`.
  **No `input_groups`** — a single 8-role group with `at_least_one` would be
  "satisfied" only when all 8 are mapped (`group_satisfied = all(role present)`),
  forcing all 8 channels. With `channel_1` required + `channel_2..8` optional,
  "≥1 channel" is structural and 2–7 channels load fine.
- `parameters`: `buffer=IntParam(5,min=0)`, `donut=IntParam(5,min=1)`,
  `min_size=IntParam(4,min=0)`, `single_cell=BoolParam(False,
  requires=("cp_mask",))`, `export_donuts=BoolParam(False)`. `presets = {}`.
- `outputs`: `particle_table` (produced_when `not single_cell`) /
  `cell_table` (produced_when `single_cell`) — module-level callables;
  `multichannel_donut_mask` (binary ImageOutput, produced_when `export_donuts`;
  namespaced to avoid a generic `/masks/donut_mask` collision and to keep the
  artifact distinguishable from user input masks).
- `run(self, inputs, params, *, log=None, set_label="", layer_map=None)`: build
  the ordered `channels` dict by walking `channel_1..channel_8` present in
  `inputs`, keyed by `layer_map[role]` (fallback to role name if `layer_map`
  absent); dispatch to the pure core; **return exactly one** table
  (`cell_table` when `single_cell` else `particle_table` — never both) plus
  `multichannel_donut_mask` only when `export_donuts`, so the returned key set
  equals the produced set. When `cp_mask` is present and `single_cell=False`,
  the particle rows carry a `cell_id` column (unmatched→0).

**Patterns to follow:**
`application/analysis/modules/per_particle_donut.py` (schema + `run()` +
module-level `produced_when`).

**Test scenarios:**
- Happy path: `run_analysis(..., {mask, channel_1, channel_2})` returns
  `particle_table` with `condensed_<layername>_*` columns (layer-name naming
  proven).
- Happy path: **3 channels mapped (not 8)** — loads and runs (guards against the
  group-semantics trap); `single_cell=True` with `cp_mask` returns `cell_table`,
  not `particle_table`; returned key set == produced set for each
  (single_cell, export_donuts) combination.
- Edge case: `export_donuts=True` writes `/masks/multichannel_donut_mask` via the
  batch runner.
- Error path: `single_cell=True` without `cp_mask` → `BoolParam.requires`
  failure; zero channels mapped → `channel_1` required-input failure (failed
  batch item), not a silent skip.
- Integration: `produced_when` matrix (particle vs cell table) honored; combined
  CSV across two datasets with different channel sets reindexed to
  `base_cols + sorted(union)` with NaN-fill.

**Verification:** `registry.get("per_particle_multichannel")` returns the class;
appears in `list_analyses()`; batch run writes expected CSVs.

---

- U5. **`PerParticleMultichannelDialog` with dynamic "Add channel" list**

**Goal:** The dialog unit — donut dialog shape + a dynamic channel list (up to
8) modeled on Threshold Rounds / phasor row frames.

**Requirements:** R4, R5.

**Dependencies:** U4.

**Files:**
- Create: `src/percell4/gui/per_particle_multichannel_dialog.py` (+ bottom-of-
  file `PerParticleMultichannel.dialog_class = PerParticleMultichannelDialog`)
- Modify: `src/percell4/interfaces/gui/main_window.py` (import the dialog module
  in `_create_scripts_panel` and `_on_open_analysis`, alongside the donut import)
- Possibly Modify: `src/percell4/gui/analysis_widgets.py` (only if a small shared
  helper for a removable combo-row is warranted; prefer reusing
  `build_layer_combo`)
- Test: `tests/test_gui/test_per_particle_multichannel_dialog.py`

**Approach:**
- Mirror `PerParticleDonutDialog` section-by-section
  (`_build_section_datasets` / `_build_section_layer_map` /
  `_build_section_params` / `_build_section_outputs` /
  `_build_section_output_parent`, single `_refresh_state()` cascade,
  `_resolve_layer_map()`, Action semantics).
- Replace the fixed layer-map rows with a **dynamic channel list** (Pattern B):
  `QListWidget` + `setItemWidget` row frames backed by a Python list, rebuilt via
  `enumerate`; each row is `build_layer_combo(...)` + a 24×24 "×" remove button
  capturing its index; "Add channel" appends a row. Combos populate from the
  dataset-intersection inventory via `populate_layer_combo`; use
  `combo.activated` + `QSignalBlocker` (no rebuild recursion).
- **8-row cap (new behavior):** gate the "Add channel" button
  `setEnabled(len(rows) < 8)` in `_refresh_state()`; a small status label shows
  "N/8 channels". `_resolve_layer_map()` assigns the i-th row to `channel_{i+1}`.
- `mask` row + optional `cp_mask` row stay fixed; `single_cell`/`export_donuts`
  params via `build_param_widget`.
- `wrap_in_scroll(...)` + `cap_to_screen(self)` (CI compliance).

**Patterns to follow:** `per_particle_donut_dialog.py`;
`phasor_masks_dialog.py` (`_refresh_dataset_list` / `_build_dataset_row_widget`
/ `_on_remove_row`); `workflows/single_cell/config_dialog.py` (add/remove-row
idiom, default-arg index capture).

**Test scenarios:**
- Happy path: add 3 channel rows, map layers, Start dispatches to the injected
  orchestrator with `layer_map` containing `channel_1..channel_3`.
- Edge case: add up to 8 then "Add channel" disables; remove a middle row →
  remaining rows renumber to contiguous `channel_1..N` on resolve.
- Edge case: combo intersection across datasets; stale selection snaps to
  sentinel.
- Error path: Start disabled with 0 channels / no dataset / no output folder.
- Persistence: output-parent QSettings round-trip.
- Integration: `_refresh_state()` outputs panel ticks `particle_table` vs
  `cell_table` as `single_cell` toggles; programmatic `setValue`/`setCurrentIndex`
  does NOT fire the cascade (qt-wire-user-edit-signals).

**Verification:** dialog constructs under `qtbot`;
`PerParticleMultichannel.dialog_class` bound; Scripts-tab button appears;
`test_dialog_helper_compliance` passes.

### Phase 3 — `whole_field_intensity`

- U6. **Bring `whole_field_analysis.py` into the repo; extract pure core; commit
  regression fixture (v2–v5)**

**Goal:** The per-script pure-core extraction unit for whole-field.

**Requirements:** R2, R3, R7.

**Dependencies:** U1.

**Files:**
- Create: `whole_field_analysis.py` (repo root; CLI wrapper importing the core)
- Create: `src/percell4/domain/analysis/_impl/whole_field_intensity.py`
  (`run_one_image_set`, `compute_bg_value`, `filter_mask_by_size`,
  `_measure_region`, `_measure_v4_regions`, `parse_bg_mode`; uses `_shared`)
- Create: `tests/test_scripts/test_whole_field_intensity_regression.py`
- Create: `tests/fixtures/whole_field_intensity/{...}/` + `_generate_fixtures.py`
  (fixtures for two-region, three-region v4/v5, and single-cell; presets v2–v5)
- Create: `tests/test_domain/test_whole_field_intensity_pure.py`

**Approach:**
- Copy script in unmodified; generate expected CSVs from the unmodified CLI for
  presets **v2–v5** plus a two-region default-params run AND single-cell runs for
  **both** a two-region and a three-region preset (the single-cell+intermediate
  combination is a distinct 4th column set — see the mode→columns matrix).
  **Fixture generation must NOT pass `--save-processed`** so the dropped path is
  never in the baseline.
- Extract `run_one_image_set(*, pbody_mask, dilute_mask, halo, mng,
  cp_mask=None, dcp2_mask=None, interaction_mask=None, sir_mask=None,
  dcp2_mask_2=None, interaction_mask_2=None, **params, set_label="", log=None)`
  returning `{"rows": [...]}`. Omit `save_processed`/`save_dir` entirely (no dead
  kwargs). Keep the filter/bg/region math verbatim; preserve longest-keyword-
  first matching in the CLI wrapper so `*_2` masks aren't shadowed; preserve the
  **exact per-branch rounding**: `parse_bg_mode` returns the **unrounded** int
  for the manual/integer path (incl. 0 = no subtraction), `math.ceil` for
  `mean`/`median`/`top_*`, `int(scipy.stats.mode)` for `mode`/`mng-nan-mode`, and
  the CLI's exact ceil/int per `mng-nan*` branch.
- Keep whole-field's `particle_count` assignment **verbatim**
  (`np.unique(return_counts=True)`+`argmax`+skip-majority-0) — do NOT route it
  through `_shared.assign_particles_to_cells` (different tie-break/zero handling).
- **Drop `save_processed`** and the `SiR_mean` path (v1).
- Cross-cutting constraint guards are a **deliberate upgrade**: the CLI
  *warns-and-`return`s* (no CSV) on SiR/intermediate conflicts; modeling them as
  `ValueError` (per-dataset failed item) is stricter. Parity fixtures cover only
  VALID combinations; conflict cases are error-path unit tests, not parity cases.
- Single-cell path intersects each compartment with each cell region and
  recomputes the region math per cell (one row per nonzero cell id, incl. empty
  cells); compatible with both two-region and three-region modes.

**Execution note:** Characterization-first — commit fixtures for v2–v5 before
extraction; parity test runs before/after.

**Patterns to follow:** `per_particle_analysis.py`,
`domain/analysis/_impl/per_particle_donut.py`, donut fixture layout.

**Test scenarios:**
- Happy path: two-region default — `mNG_*`/`halo_*` means/integs/ratios and bg
  values match hand-computed.
- Happy path: three-region v4 and v5 — intermediate_* columns; v4 vs v5 differ
  only in `halo_intermediate_mean`/`halo_dilute_mean`/(v5)`dilute_area_px`.
- Happy path: single-cell (two-region) — one row per cell incl. empty cell (NaN
  metrics); `mNG_cell_mean`, `particle_count`. AND single-cell + three-region
  (the 4th column set: three-region columns + the four per-cell columns).
- Edge case: `percent` adds `pct_halo_in_mNG_*` in [0,100]; **each bg branch's
  exact rounding** pinned per keyword (manual int unrounded incl. 0; ceil for
  mean/median/top_*; int(mode) for mode/mng-nan-mode; per-branch for mng-nan*);
  `exclude_halo_one` supersedes `exclude_halo_zero`; `particle_count` tie-break +
  background-majority handling pinned.
- Error path (unit tests on `run()`/core only, NOT parity): `SiR_subtract` +
  `SiR_filter` both set → ValueError; `intermediate_assemblies` without
  `mNG_filter`/`FLIM_filter`/the four `_2`/Dcp2/interaction masks → ValueError.
- Integration: **per-dataset** parity — CLI single-prefix dir vs framework
  `per_dataset/<stem>_whole_field_table.csv` for presets v2–v5; drop
  `group`/`dataset`, reindex to `sorted(columns)`, sort rows by `cell_id` (or
  single row for non-single-cell), numeric rule.

**Verification:** v2–v5 regression passes; pure-core tests pass; CLI headless.

---

- U7. **Register `WholeFieldIntensity` analysis module + presets v2–v5**

**Goal:** Declarative schema, ChoiceParam+IntParam bg-mode modeling, preset
snapshot + immutability test.

**Requirements:** R2, R6, R8.

**Dependencies:** U6.

**Files:**
- Create: `src/percell4/application/analysis/modules/whole_field_intensity.py`
- Modify: `src/percell4/application/analysis/__init__.py` (import to register)
- Create: `tests/fixtures/preset_snapshots/whole_field_intensity.json`
- Modify: `tests/test_application/test_presets_immutable.py` (add a case)
- Test: `tests/test_application/test_run_analysis.py` /
  `test_run_analysis_batch.py` (add cases)

**Approach:**
- `required_inputs`: `pbody_mask`,`dilute_mask` (mask/binary), `halo`,`mng`
  (intensity/float). `optional_inputs`: `cp_mask`(label),
  `dcp2_mask`,`interaction_mask`,`sir_mask`,`dcp2_mask_2`,`interaction_mask_2`
  (mask/binary). **No `input_groups`** (so `group_requirement` is trivially
  satisfied). A single `intermediate` group with `at_least_one` would make all
  four intermediate masks de-facto required and break every two-region / v2 / v3
  run; instead the four masks are optional and enforced only for v4/v5 via
  `intermediate_assemblies = BoolParam(requires=("dcp2_mask","interaction_mask",
  "dcp2_mask_2","interaction_mask_2"))`.
- `parameters`: `min_size=IntParam(10)`;
  `mng_bg_mode=ChoiceParam(("mean","median","mode","top_quintile",
  "top_quartile","top_decile","manual"), "mean")`, `mng_bg_value=IntParam(0)`;
  `halo_bg_mode=ChoiceParam((…mng modes…, "mng-nan","mng-nan-median",
  "mng-nan-mode","mng-nan-top_quintile","mng-nan-top_quartile",
  "mng-nan-top_decile","mng-nan-max","manual"), "median")`,
  `halo_bg_value=IntParam(0)`; `exclude_halo_zero=BoolParam(True)`,
  `exclude_halo_one=BoolParam(False)`;
  `mNG_filter=ChoiceParam(("none","zero","NaN"),"none")`
  (dcp2_mask requirement enforced in run/dialog since choice gating isn't a
  BoolParam);
  `FLIM_filter=ChoiceParam(("none","zero","NaN"),"none")`;
  `SiR_subtract=ChoiceParam(("none","zero","NaN"),"none")`;
  `SiR_filter=BoolParam(False, requires=("sir_mask",))`;
  `mNG_in_FLIM=BoolParam(False, requires=("interaction_mask","dcp2_mask"))`;
  `percent=BoolParam(False)`;
  `intermediate_assemblies=BoolParam(False, requires=("dcp2_mask",
  "interaction_mask","dcp2_mask_2","interaction_mask_2"))`;
  `intermediate_zero_fill=BoolParam(False)`;
  `single_cell=BoolParam(False, requires=("cp_mask",))`.
- `presets`: `decapping-sensor-v2..v5` — preset dicts use the **choice strings**
  (e.g. `mNG_filter="NaN"`, `SiR_subtract="none"`) and `mng_bg_mode/halo_bg_mode
  ="manual"` + `*_bg_value=0`. The snapshot pins these exact serialized values.
- `outputs`: one `whole_field_table` TableOutput (always produced; columns vary
  by mode — see the 4-row mode matrix).
- `run(self, inputs, params, *, log=None, set_label="")`: (1) validate
  cross-cutting constraints, raising `ValueError` (a deliberate stricter upgrade
  over the CLI's warn-and-abort); (2) **normalize values for the core**: map each
  filter choice `"none" → None` (the core's `is None` branches are preserved) and
  map `*_bg_mode=="manual"` to the CLI's integer-`*_bg_value` path (unrounded),
  other choices to their string mode; (3) dispatch; `pd.DataFrame(rows)`.

**Execution note:** Add the preset snapshot JSON in the same commit; the
immutability test pins it.

**Patterns to follow:** `application/analysis/modules/per_particle_donut.py`;
preset snapshot mechanism in `tests/test_application/test_presets_immutable.py`
+ `tests/fixtures/preset_snapshots/per_particle_donut.json`.

**Test scenarios:**
- Happy path: `run_analysis` two-region default returns `whole_field_table`;
  v4 preset returns the three-region column set.
- Happy path: preset v2–v5 resolved params == committed snapshot
  (immutability test).
- Edge case: `intermediate_assemblies=True` requires the four intermediate
  masks via `BoolParam.requires` (NOT an input group) → run rejects when any is
  unmapped; v2/v3/two-region runs with none of them succeed; single-cell
  requires `cp_mask`.
- Edge case: filter-choice `"none"` resolves to the core's `None` branch (not a
  literal `"none"` string) — pinned so the gating logic takes the right path.
- Error path: mutually-exclusive SiR options both set → ValueError surfaced by
  `run()`; preset value drift → snapshot test fails with a diff.
- Integration: image-free Action — no `/labels/whole_field` collision; combined
  CSV written; `run_config.json` records preset name + values.

**Verification:** `registry.get("whole_field_intensity")` returns the class;
preset snapshot test green; batch run writes expected CSVs.

---

- U8. **`WholeFieldIntensityDialog`**

**Goal:** The dialog unit — donut dialog shape with a visually-grouped
intermediate-mask sub-section (gated by `intermediate_assemblies`, not a
framework input group), optional masks, bg-mode controls, preset lock.

**Requirements:** R5, R2.

**Dependencies:** U7.

**Files:**
- Create: `src/percell4/gui/whole_field_intensity_dialog.py` (+ late-bind
  `WholeFieldIntensity.dialog_class`)
- Modify: `src/percell4/interfaces/gui/main_window.py` (import the dialog in
  both wiring sites)
- Test: `tests/test_gui/test_whole_field_intensity_dialog.py`

**Approach:**
- Mirror `PerParticleDonutDialog`: required role combos (pbody/dilute/halo/mng),
  the four intermediate masks as a visually-grouped, collapsible sub-section
  (enabled only when `intermediate_assemblies` is on — a UI grouping, not a
  framework `input_group`; the donut `_BRANCHES` layout pattern is reused for
  appearance only), optional-mask combos, params form with preset combo + lock,
  outputs panel, output-folder picker, single `_refresh_state()`, Action
  semantics, `wrap_in_scroll`+`cap_to_screen`.
- Reflect the cross-cutting constraints as live dialog gating (disable
  incompatible options) so Start can't dispatch an invalid combo; the `run()`
  guard is the backstop.
- bg-mode rows: a ChoiceParam combo + an IntParam spin (enabled only when choice
  == "manual"), built from `analysis_widgets.build_param_widget`.

**Patterns to follow:** `per_particle_donut_dialog.py` (branch group skip +
preset lock + requires-gating); `analysis_widgets.py` factories.

**Test scenarios:**
- Happy path: assign required roles + a preset; Start dispatches to the injected
  orchestrator with `preset=` and `params=None`.
- Edge case: choosing a preset locks all param widgets; turning off
  `intermediate_assemblies` disables the intermediate-mask combos and excludes
  their roles from `_resolve_layer_map`; bg-mode spin enables only on "manual".
- Edge case: requires-gating disables `single_cell` until `cp_mask` mapped,
  `SiR_filter` until `sir_mask` mapped, and `intermediate_assemblies` until all
  four intermediate masks are mapped (its `BoolParam.requires`).
- Error path: Start disabled without required roles / output folder.
- Persistence: output-parent QSettings round-trip.
- Integration: outputs panel reflects the single `whole_field_table`;
  programmatic widget sets don't fire `_refresh_state()`.

**Verification:** dialog constructs under `qtbot`; `dialog_class` bound;
Scripts-tab button appears; compliance test passes.

### Phase 4 — Documentation & institutional memory

- U9. **Write `docs/writing_an_analysis.md`; register analysis globs in the
  canonical-sources matrix**

**Goal:** Capture the now-proven "add an analysis" pattern (deferred by the
framework plan until the second migration — this is it) and make the PreToolUse
learnings hook cover the analysis package.

**Requirements:** R3 (durability), R10.

**Dependencies:** U4, U7 (the worked examples).

**Files:**
- Create: `docs/writing_an_analysis.md`
- Modify: `docs/audits/canonical-sources-matrix.yaml` (add `applies_to` globs
  for `src/percell4/application/analysis/**`,
  `src/percell4/domain/analysis/**`, `run_analysis*.py`, `analysis_widgets.py`)
- Optionally Create: a `docs/solutions/` entry for the registered-analysis
  pattern (only if it adds beyond `writing_an_analysis.md`)

**Approach:**
- Document the file set, declaration shapes, the single-source-of-truth core,
  the `layer_map` threading, the dynamic-channel dialog pattern, the
  presets-immutability snapshot mechanism, and the "new analysis module"
  checklist distilled from the institutional learnings (Action vs Creator;
  consume `analysis_widgets`; `wrap_in_scroll`+`cap_to_screen`; loader is
  role-keyed; outputs never overload `/labels/`).

**Execution note:** none (documentation).

**Test scenarios:** Test expectation: none — documentation + audit-registry
config; verify `python3 scripts/learnings_applicability.py
src/percell4/application/analysis/modules/per_particle_multichannel.py` now
returns the analysis globs.

**Verification:** doc renders; `learnings_applicability.py` reports the new
globs for analysis-package paths.

---

## System-Wide Impact

- **Interaction graph:** Both analyses flow through the unchanged
  `run_analysis` → `cls().run()` → batch persist path. The only framework
  surface that changes is `Analysis.run()` (+`layer_map`) and
  `run_analysis._accepted_progress_kwargs`; both are additive and opt-in.
- **Error propagation:** Per-dataset failures isolate into
  `BatchAnalysisItemResult` (3-state succeeded/failed/skipped). Cross-cutting
  param-constraint violations raise `ValueError` in `run()` and surface as a
  **per-dataset failed item** (a deliberate change from the CLI's whole-run
  warn-and-abort); the dialog's live gating prevents most from ever dispatching.
  The behavioral upgrade is intentional and documented; parity fixtures cover
  only valid combinations.
- **State lifecycle risks:** Mask outputs (`multichannel_donut_mask`) persist via
  the runner's `store.write_mask` to `/masks/<output-key>`; one per `.h5`,
  idempotent overwrite on re-run (NOT per-group like the CLI's TIFF-per-set), so
  the donut mask is validated in-process against the pure core's array, not via
  subprocess parity. No new HDF5 groups; no schema migration.
- **API surface parity:** The Scripts tab and batch runner are generic — both
  analyses appear automatically once registered + dialog-imported. No per-
  analysis change to `main_window` beyond the two import lines each.
- **Integration coverage:** Subprocess CLI-vs-framework parity (per analysis) +
  in-process pure-core/`run_analysis` tests cover the staleness vector flagged
  in `in-session-hdf5-staleness-multi-vector`.
- **Unchanged invariants:** `per_particle_donut` numeric output (guarded by its
  regression fixture through the U1 refactor); the five session selection fields
  (dialogs are Actions); the existing `whole_field` segmentation
  (`/labels/whole_field`) is untouched and its name is not reused.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| **Group semantics make slots/masks required** (was a blocker) | Do NOT model channel slots or intermediate masks as a single `at_least_one` group (`group_satisfied = all(roles present)`). Channel_1 required + channel_2..8 optional; intermediate masks optional + gated by `BoolParam.requires`. Test the 3-channels-mapped and v2/v3-no-intermediate happy paths explicitly. |
| Parity test fails on column order, not numbers | Compare per-dataset; drop `group` AND `dataset`; reindex both to `sorted(columns)`; then sort rows + numeric rule. |
| U1 refactor changes donut numerics | Existing donut regression + pure-core tests are the guard; characterization-first; no behavior change permitted. Whole-field `particle_count` assignment kept verbatim (not shared). |
| Multichannel column names diverge from CLI (role vs layer name) | `layer_map` threading (U2) recovers chosen names; combined-CSV union is a synthetic in-process test reindexed to `base_cols + sorted(union)`. |
| 8-slot cap too low for some dataset | Declared ceiling per user ("never more than 8"); cap is a one-line change; status label shows N/8. |
| Multichannel `cell_id`/produced_when mismatch | `run()` returns exactly the produced key set; particle rows carry `cell_id` when `cp_mask` mapped even if `single_cell=False`; fixture covers it. |
| Whole-field bg-mode dual-typed param mis-modeled | ChoiceParam+IntParam pair + `parse_bg_mode`; v2–v5 presets pinned by snapshot; per-branch `ceil`/`int` rounding pinned by pure-core tests. |
| Cross-cutting param constraints unenforceable in schema | Explicit `ValueError` guards in `run()` + live dialog gating; error-path tests assert each. |
| Mixed two/three-region datasets in one batch → ragged combined CSV | Single mode-dependent table + documented "don't mix modes" caveat + optional dialog warning. |
| `save_processed` float images unsupported by ImageOutput | Dropped (off in all presets; troubleshooting-only). |
| Dialog skips scroll helper → CI failure | `wrap_in_scroll`+`cap_to_screen` in both dialogs; `test_dialog_helper_compliance` enforces. |

---

## Documentation / Operational Notes

- `docs/writing_an_analysis.md` (U9) becomes the canonical "add an analysis"
  reference, with these two analyses + donut as worked examples.
- `docs/audits/canonical-sources-matrix.yaml` updated so the PreToolUse
  learnings hook warns on future edits in the analysis package.
- No rollout/migration concerns: additive analyses, no dataset-schema change,
  idempotent batch overwrite.

---

## Sources & References

- **Framework plan (the pattern this extends):**
  `docs/plans/2026-05-27-004-feat-analysis-integration-plan.md`
- **Framework requirements:**
  `docs/brainstorms/2026-05-27-analysis-integration-requirements.md`
- Reference implementation: `src/percell4/application/analysis/modules/per_particle_donut.py`,
  `src/percell4/domain/analysis/_impl/per_particle_donut.py`,
  `per_particle_analysis.py`, `src/percell4/gui/per_particle_donut_dialog.py`,
  `src/percell4/gui/analysis_widgets.py`
- Dynamic-row UI: `src/percell4/gui/workflows/single_cell/config_dialog.py`
  (Threshold Rounds), `src/percell4/gui/phasor_masks_dialog.py`
- Source scripts: `~/mask-intensity-analysis-repo/per_particle_multichannel.py`,
  `~/mask-intensity-analysis-repo/whole_field_analysis.py`,
  `~/mask-intensity-analysis-repo/README.md`
- Institutional learnings: `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`,
  `.../sibling-dialog-extract-shared-widget-2026-05-12.md`,
  `.../atomic-write-contract.md`, `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`,
  `.../napari-mask-layer-misclassified-as-segmentation.md`,
  `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
