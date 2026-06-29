---
title: "feat: Whole-field decapping-sensor — multi-timepoint, neutral role labels, v6 preset, preset-aware dialog, per-cell expression measurement"
type: feat
status: active
date: 2026-06-29
---

# feat: Whole-field decapping-sensor — multi-timepoint, v6 preset, preset-aware dialog, per-cell expression measurement

## Overview

Five enhancements to PerCell4's **Whole-field decapping-sensor intensity** analysis
(`whole_field_intensity`, a registered `Analysis` with a custom `WholeFieldIntensityDialog`):

1. **Multi-timepoint support** — verify + harden + test (the analysis framework already
   auto-loops per timepoint and tags rows with a `timepoint` column).
2. **Global neutral role labels** — rename the two mask roles `pbody_mask → condensate_mask`
   and `dcp2_mask → mng_mask` for *all* presets (the regions are generic "condensate" /
   "mNG-filter" roles whether the assay is P-bodies or stress granules). The pure core and
   the output column names are **unchanged**.
3. **`decapping-sensor-v6` preset** — a stress-granule two-region variant ported from
   `mask-intensity-analysis-repo/whole_field_analysis.py` (`mNG_filter='NaN'`,
   `FLIM_filter='zero'`, `percent`, **no** intermediate assemblies).
4. **Preset-aware dialog** — a new generic capability: a preset can declare *required* and
   *hidden* optional roles. v6 makes `mng_mask` required (blocks Start until supplied) and
   hides the intermediate masks (`dcp2_mask_2`, `interaction_mask_2`, `sir_mask`).
5. **Per-cell whole-cell expression measurement** — in single-cell mode, a selectable
   whole-cell mean of the mNG and/or Halo channel per cell (`mNG_cell_mean` /
   `Halo_cell_mean`), for grouping cells by expression level downstream. Mirrors the
   per-particle `cell_mean_channels` feature.

The unifying constraint: **pure-domain math stays 2D and unchanged; the framework loops
timepoints; the schema and dialog gain the new capabilities; single-timepoint and existing
v2–v5 behavior stay byte-identical** (the parity fixtures must not change except where a new
column is explicitly added).

---

## Problem Frame

The whole-field decapping-sensor analysis was built for single-timepoint P-body assays
(presets v2–v5). The user now needs it for:

- **Time-lapse datasets** — the same compartment quantification per acquisition frame.
- **Stress-granule assays** — the source repo's `decapping-sensor-v6` preset, which measures
  the same two-region (condensate + dilute) quantities but over a stress-granule mask
  (`SG_mask`) with a generic mNG-filter mask (`mNG_mask`) instead of P-body / Dcp2. The
  dialog's "P-body mask" / "Dcp2 mask" labels are misleading for this assay, and v6 needs
  `mNG_mask` to be required while the v4/v5 intermediate masks are irrelevant.
- **Expression-level cell grouping** — for polyclonal/transient data, the user wants a
  whole-cell intensity per cell (mNG or Halo) so cells can be binned by expression
  downstream. The core already computes `mNG_cell_mean` in single-cell mode; the user wants
  this exposed as a selector and extended to Halo.

The script is `whole_field_intensity` (`application/analysis/modules/whole_field_intensity.py`
+ `domain/analysis/_impl/whole_field_intensity.py` + `gui/whole_field_intensity_dialog.py`),
run through `application/use_cases/run_analysis.py`. The v6 source of truth is
`mask-intensity-analysis-repo/whole_field_analysis.py` (preset at its lines 755–799).

---

## Requirements Trace

- R1. **Multi-timepoint works** — on a `(T,…)` dataset the analysis emits per-frame rows
  (two-region, three-region, and single-cell modes) with a `timepoint` column;
  single-timepoint output is byte-identical (no `timepoint` column).
- R2. **The two mask roles are globally relabeled** to neutral terms `condensate_mask`
  (was `pbody_mask`) and `mng_mask` (was `dcp2_mask`) across the schema and dialog. The pure
  core's kwargs and the **output column names** (`pbody_*`, `dcp2`-derived) are unchanged —
  role↔column decoupling.
- R3. **`decapping-sensor-v6` is added** as a new immutable preset matching the source's
  parameters (two-region, `mNG_filter='NaN'`, `FLIM_filter='zero'`, `percent=True`,
  `intermediate_assemblies=False`), snapshot-locked.
- R4. **Preset-aware required + hidden roles, enforced in the dialog AND the headless run.**
  A preset may declare required + hidden optional roles via new `Analysis`-base fields. v6
  requires **`mng_mask` and `interaction_mask`** (its `mNG_filter`/`FLIM_filter` silently
  no-op without them) and hides `dcp2_mask_2` / `interaction_mask_2` / `sir_mask`. The dialog
  blocks Start with a reason until the required roles are supplied; **`run_analysis` raises a
  hard `ValueError`** when a preset's required role is absent from `layer_map` (so headless
  runs fail loud, not silently wrong). Dialog-local (Action — no session writes), scroll-safe.
- R5. **A per-cell whole-cell expression measurement** — in single-cell mode, `mNG_cell_mean`
  stays always-on (unchanged) and an opt-in `Halo_cell_mean` is added, surfaced as a
  `TableOutput` column, for grouping cells by expression downstream.
- R6. **Existing behavior is preserved** — v2–v5 presets, single-timepoint runs, and the
  parity fixtures are unchanged except for explicitly-added new columns; the analysis dialog
  never mutates session selection fields.

---

## Scope Boundaries

- **Non-goal: changing the pure compartment math.** `run_one_image_set` and `_measure_region`
  keep their formulas and output column names; v6 reuses the existing two-region path.
- **Non-goal: renaming output columns or the `dcp2_mask_2` / `interaction_mask_2` roles.**
  Only `pbody_mask` and `dcp2_mask` roles are relabeled (per the user); the intermediate
  roles and all CSV column names stay (`pbody_*`, `dilute_*`, `mNG_*`).
- **Non-goal: a generic dialog *builder*.** Whole-field has a hand-built custom dialog
  (`WholeFieldIntensityDialog`); the preset-aware capability is wired into it (consuming the
  `analysis_widgets.py` factories), not a new auto-generator. Other analyses adopt the new
  schema fields when they need them.
- **Non-goal: doing the expression grouping itself.** This plan adds the per-cell
  measurement column; clustering cells by it is a downstream step (the existing
  grouper/k-means path).
- **Non-goal: full FLIM/decay or new mask-generation.** v6 consumes existing on-disk masks.
- **Non-goal: per-dataset preset/layer-map overrides.** The dialog passes one layer map +
  preset for the whole batch (the existing v1 closure); per-dataset is a future extension.

### Deferred to Follow-Up Work

- **Capture the analysis-framework multi-timepoint contract in `docs/solutions/`** — the
  per-timepoint auto-loop + `_aggregate_timepoints` contract is currently code-only (the
  `registered-analysis-framework.md` doc predates it). A `/ce-compound` after this lands.
- **`mng_mask`/`dcp2_mask_2` naming asymmetry** — after the relabel, the inner role is still
  `dcp2_mask_2` while the outer is `mng_mask`. Renaming the inner role to `mng_mask_2` is a
  cosmetic follow-up the user did not request.

---

## Context & Research

### The script and its layers

- **Module (schema):** `src/percell4/application/analysis/modules/whole_field_intensity.py`
  — `required_inputs` (pbody_mask/dilute_mask/halo/mng, lines 86–95), `optional_inputs`
  (cp_mask/dcp2_mask/interaction_mask/sir_mask/dcp2_mask_2/interaction_mask_2, 98–111),
  `parameters` (114–169, incl. `BoolParam(requires=…)` for `mNG_in_FLIM`,
  `intermediate_assemblies`, `single_cell`), `presets` v2–v5 (171–204), and `run()`
  (219–297) which maps inputs to the pure core (kwargs `pbody_mask=`, `dcp2_mask=`, …) and
  enforces cross-cutting `ValueError` guards.
- **Pure core:** `src/percell4/domain/analysis/_impl/whole_field_intensity.py` —
  `run_one_image_set` (340–533); single-cell branches `_two_region_single_cell` (556–589)
  and `_v4_single_cell` (592–624); the **existing per-cell `mNG_cell_mean`** is computed at
  `_two_region_single_cell:574–579` (the exact precedent + hook point for R5). Output
  columns come from `_measure_region` (119–203): `pbody_*`, `dilute_*`, `mNG_*`, `halo_*`.
- **Custom dialog:** `src/percell4/gui/whole_field_intensity_dialog.py`
  `WholeFieldIntensityDialog` (80–591; late-bound `WholeFieldIntensity.dialog_class = …` at
  591). Preset selection: `_preset_combo.activated → _on_preset_changed` (472–481) → the
  `_refresh_state()` cascade (318–325: `_refresh_combos` / `_refresh_preset_lock` /
  `_refresh_requires_gating` / `_refresh_start_button` …). Start gate:
  `_refresh_start_button` / `_start_disabled_reason` (433–448) checks required roles in
  `_resolve_layer_map` (452–458). Role combos keyed by role name in `_role_combos`.
- **Framework run path:** `src/percell4/application/use_cases/run_analysis.py` — the
  per-timepoint auto-loop (180–189: `load_layers(timepoint=t)` per frame) and
  `_aggregate_timepoints` (236–281: `TableOutput` concat + `timepoint` column; single-t
  byte-identical, no column).
- **Schema base + types:** `src/percell4/domain/analysis/base.py` (`Analysis` class fields
  46–63) and `src/percell4/domain/analysis/types.py` (`BoolParam.requires`, 91–104).
  Registry validation: `src/percell4/application/analysis/registry.py` (110–116 validates
  preset keys; add validation for the new fields).
- **Per-particle `cell_mean` precedent (the R5 template):**
  `application/analysis/modules/per_particle_multichannel.py` (BoolParams `{role}_cell_mean`
  201–210; `run()` collects `cell_mean_channels` 269–279; column order inserts
  `cell_<ch>_mean` after `cell_id`) and `domain/analysis/_impl/per_particle_multichannel.py`
  (the per-cell cache + column add 468–483, gated on `cp_mask`).
- **Shared dialog widgets:** `src/percell4/gui/analysis_widgets.py` (`build_layer_combo`,
  `build_param_widget`, `build_preset_combo`); `src/percell4/gui/_dialog_utils.py`
  (`wrap_in_scroll` / `cap_to_screen`).
- **v6 source of truth (SIBLING repo, outside this checkout):**
  `/Users/leelab/mask-intensity-analysis-repo/whole_field_analysis.py` — the
  `decapping-sensor-v6` preset (lines ~755–787) and its `PRESET_CHANNEL_ROLES` substitution
  (`pbody → SG_mask`, `mng_filter → mNG_mask`, lines ~797–799). **Not** the in-repo
  `scripts/whole_field_analysis.py` (an older file with no v6). The v6 parameter values are
  **inlined verbatim in U4** (the authoritative contract for the implementer — read U4, not the
  external path). Required files per image set: `SG_mask, dilute_mask, interaction_mask,
  mNG_mask, mNG, Halo` (+`cp_mask` for single-cell). v6 keeps the generic `pbody`/`dilute`
  output column names.
- **Relabel safety (verified against code):** there is **no** role-name→layer auto-match —
  `analysis_widgets.populate_layer_combo` matches on-disk *layer* names and defaults to the
  sentinel, so renaming a role cannot break selection. The only consumers of the whole-field
  role keys are the module, `WholeFieldIntensityDialog` (incl. the enumerated hardcoded spots),
  and the module/dialog test files; `scripts/whole_field_analysis.py` and `per_particle_donut.py`
  use their own independent `pbody_mask`/`dcp2_mask` (core kwargs / a different analysis),
  unaffected.
- **Fixtures + drift guards:** `tests/fixtures/preset_snapshots/whole_field_intensity.json`
  (preset snapshot — v6 added here), `tests/fixtures/whole_field_intensity/` (input TIFFs +
  `expected/*.csv` parity), `tests/test_application/test_presets_immutable.py`,
  `tests/test_gui/test_dialog_helper_compliance.py` (scroll-helper CI guard).

### Institutional learnings (gates)

- **`.../architecture-patterns/registered-analysis-framework.md`** — the governing contract:
  presets are **immutable** (v6 = new entry + snapshot mirror; never edit one); **role↔column
  decoupling** (the loader is role-keyed, so a role rename need not touch output columns —
  exploit this for R2); conditional roles are modeled as `BoolParam(requires=…)` (the
  primitive to **extend**, not reinvent, for R4); never reuse the reserved `whole_field` name;
  reuse `_impl/_shared.py` helpers **only when behavior is identical** (whole-field keeps its
  own per-cell assignment for a reason — mirror behavior for R5, verify numbers before
  sharing).
- **`.../architecture-patterns/extending-per-cell-detection-to-time-lapse-2026-06-25.md`** —
  the per-timepoint contract for R1: math rank-agnostic, loop per-frame, single-t
  byte-identical. (Whole-field has **no** ImageOutput, so the exact-`T` stack guard does not
  apply; only `TableOutput` concat.)
- **`.../logic-errors/grouped-thresholding-development-lessons.md`** — #7 *never reuse a
  domain term for a different operation* (the R2 rename must not collide with a reserved /
  segmentation term — `condensate_mask`/`mng_mask` are safe); this is **the** expression-level
  cell-grouping path R5 feeds; #4 use float-safe reductions (`np.nanmean`, not integer
  `bincount`) for the cell mean.
- **`.../architecture-patterns/adding-thresholding-method-to-single-cell-workflow-2026-06-15.md`**
  — eye/real-data validate v6 (the "empty mask reported as success" trap); a clean synthetic
  test is not enough.
- **`.../conventions/qt-wire-user-edit-signals-2026-05-12.md`** — wire the preset combo's
  `activated` (already done) into the single `_refresh_state()` cascade for R4; do not gate on
  programmatic setters.
- **`.../ui-bugs/dialog-scroll-when-tall.md`** — keep `wrap_in_scroll`/`cap_to_screen` intact
  as roles show/hide and the cell-mean selector is added (CI-enforced by
  `test_dialog_helper_compliance.py`).
- **`.../architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`** — consume
  the `analysis_widgets.py` factories for R4/R5 widgets; do not rebuild from the params dict.
- **`.../architecture-patterns/gui-action-contract-exhaustiveness.md`** — the dialog is an
  **Action**; preset require/hide and the cell-mean selector are dialog-local state and must
  never write the five session selection fields.
- **`.../tech-debt/threshold-qc-measurements-write-owned-by-controller.md`** — the cell-mean is
  a measurement → a `TableOutput` column (→ CSV), never an `/measurements` h5 write.

### External references

None — every change follows an in-repo pattern (the registered-analysis framework, the
per-particle `cell_mean` feature, the `BoolParam.requires` primitive, the per-timepoint
auto-loop). The v6 parameters come from the user-named source repo, read directly.

---

## Key Technical Decisions

- **D1 — Multi-timepoint is verify + harden + test (R1).** The framework auto-loops and
  aggregates with a `timepoint` column; the pure core stays 2D. Whole-field has no
  ImageOutput, so only `TableOutput` concat is exercised. Work = a time-lapse test (all
  modes) + a single-t byte-identical assertion; production code changes only if a gap surfaces.
- **D2 — Relabel roles, keep core + columns (R2, role↔column decoupling).** Rename the schema
  role keys `pbody_mask → condensate_mask` and `dcp2_mask → mng_mask` (and the dialog
  `_role_combos`, `_start_disabled_reason`, `_resolve_layer_map`, the `BoolParam.requires`
  tuples, and the test/fixture `LAYER_MAP` keys). At the `run()` boundary, map the new role
  names onto the **unchanged** pure-core kwargs (`pbody_mask=inputs["condensate_mask"]`,
  `dcp2_mask=inputs.get("mng_mask")`). The core, its column names (`pbody_*`/`dilute_*`), and
  the parity CSVs are untouched — matching the source's choice to keep generic `pbody` columns.
- **D3 — v6 is a new immutable preset (R3).** Add `decapping-sensor-v6` to the `presets` dict
  with the source's exact params; mirror it into
  `tests/fixtures/preset_snapshots/whole_field_intensity.json`. The role substitution
  (SG→condensate, mNG→mng) is the user assigning their `SG_mask`/`mNG_mask` layers to the
  `condensate_mask`/`mng_mask` roles — no preset-side role mapping needed. v6 keeps the generic
  `pbody`/`dilute` output column names.
- **D4 — Preset-aware roles: two `Analysis`-base fields, enforced in BOTH the dialog and the
  use-case (R4).** Add `preset_required_inputs: dict[str, tuple[str,…]]` and
  `preset_hidden_inputs: dict[str, tuple[str,…]]` to the `Analysis` base (default empty), with a
  light validation (extend the existing registry preset-key loop) that declared roles exist.
  The fields live on the base — **not** the dialog — because **both** surfaces must read them:
  - **Dialog (UX):** `WholeFieldIntensityDialog._refresh_state()` blocks Start + names a missing
    required role, and **hides** hidden-role rows. Hidden roles are **excluded at
    `_resolve_layer_map`** (the row is hidden, the combo value is *not* cleared) so switching
    away from the preset restores the user's prior selection. A hidden role also disables any
    param that `requires` it via the existing `_refresh_requires_gating` (and v6's preset locks
    the SiR options off regardless, so the `SiR_filter`-needs-`sir_mask` case is consistent).
  - **Use-case (hard safety):** **`run_analysis` enforces preset-required roles as a hard
    `ValueError`** when a preset names a role absent from `layer_map`. Without this, a *headless*
    v6 run (notebook / `batch_run_analysis` / a future CLI) with `mng_mask` (or
    `interaction_mask`) absent silently skips the filter and produces scientifically-wrong
    numbers with no error (the empty-mask-as-success trap) — the dialog block alone does not
    cover the headless path (`run_analysis` validates only `required_inputs`, groups, and
    `BoolParam.requires` today).
  Dialog-local state only (Action — no session writes). The base fields earn their place via
  this dual-surface enforcement, not speculative reuse.
- **D5 — Expression measurement: keep `mNG_cell_mean` always-on, add one opt-in `Halo_cell_mean`
  (R5, R6).** The core already computes `mNG_cell_mean` per cell in single-cell mode
  (`_two_region_single_cell:574-579` + the `_v4_single_cell` equivalent) — **leave it
  untouched** (zero parity risk, no re-gating). Add **one** `BoolParam` `halo_cell_mean`
  (default **False**) that, in single-cell mode, adds a `Halo_cell_mean` column =
  `np.nanmean(halo_sub[cell_region])` (the bg-subtracted Halo over each cell's `cp_mask`
  region). This satisfies "mNG and/or Halo" (mNG always present, Halo opt-in) with no schema
  risk. **Critical:** `halo_cell_mean` must **not** carry `BoolParam.requires=("cp_mask",)` —
  the per-particle precedent (`per_particle_multichannel.py:196-200`) warns that a True param
  whose required role is absent makes `run_analysis._check_bool_requires` raise; gate it
  **inside** the single-cell core branch (reached only with `cp_mask`) + dialog greying. The
  new core kwarg `halo_cell_mean` defaults **False** so the second core caller
  (`scripts/whole_field_analysis.py:339`) is unaffected. Place `Halo_cell_mean` immediately
  after `mNG_cell_mean` in the per-cell row (existing layout: `cell_id, cell_area_px,
  particle_count, mNG_cell_mean`) to keep `v4_sc.csv` byte-identical.
- **D6 — Eye-validate v6 on real stress-granule data.** Per the empty-mask-as-success learning,
  v6's correctness is confirmed on a real dataset, not only synthetic unit tests (the user's
  eye is ground truth).

---

## Open Questions

### Resolved during planning

- Dialog labels → **global neutral relabel** to `condensate_mask` / `mng_mask` for all presets
  (User).
- v6 input requirements → **preset-aware dialog**: required + hidden roles per preset (User).
- Expression measurement channel → **selector among existing channels** (mNG and/or Halo);
  resolved as **`mNG_cell_mean` always-on + opt-in `Halo_cell_mean`** (D5 — preserves parity,
  no re-gating). (User + review.)
- Preset-required-role enforcement → **both the dialog (UX block) and `run_analysis` (hard
  `ValueError`)**, so headless runs can't silently produce wrong numbers (D4, review).
- v6 required roles → **`mng_mask` and `interaction_mask`** (its `mNG_filter`/`FLIM_filter`
  no-op without them) (D3/D4, review).
- Cross-timepoint expression grouping → **use a tracked segmentation or `(timepoint, cell_id)`**
  (per-frame `cp_mask` `cell_id`s are not the same physical cell across frames) (U5 caveat).

### Deferred to implementation

- **Exact dialog hide-vs-grey for hidden roles** — fully hiding the row (the plan's default) vs
  greying a disabled combo; resolve against the real dialog layout so `wrap_in_scroll` stays
  correct. Either way, hidden roles are excluded at `_resolve_layer_map` (value preserved).
- **v6 eye-validation gating** — synthetic unit tests gate CI / PR merge; the real-data
  eye-validation (D6) is a post-merge correctness check by the user, not a CI blocker.
- Exact line anchors will have drifted; resolve against the real files at execution.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not
> implementation specification. The implementing agent should treat it as context.*

**The preset-aware schema → dialog hook (R4):**

```
# domain/analysis/base.py — Analysis base (new generic fields, default empty)
preset_required_inputs: ClassVar[dict[str, tuple[str, ...]]] = {}
preset_hidden_inputs:   ClassVar[dict[str, tuple[str, ...]]] = {}

# whole_field_intensity module (U4) declares them for v6:
preset_required_inputs = {"decapping-sensor-v6": ("mng_mask", "interaction_mask")}
preset_hidden_inputs   = {"decapping-sensor-v6": ("dcp2_mask_2", "interaction_mask_2", "sir_mask")}

# BOTH surfaces enforce required roles:
# (a) run_analysis (use-case) — the headless safety net:
for role in getattr(cls, "preset_required_inputs", {}).get(active_preset, ()):
    if role not in layer_map: raise ValueError(f"Preset {active_preset!r} requires '{role}'.")
# (b) WholeFieldIntensityDialog._refresh_state() cascade (existing) gains:
def _start_disabled_reason():                      # extend existing — Start UX block
    for role in preset_required_inputs.get(active_preset, ()):
        if role not in layer_map: return f"Preset {active_preset!r} requires '{role}'."
def _refresh_hidden_roles():                        # NEW cascade step
    hide = preset_hidden_inputs.get(active_preset, ())
    for role, combo_row in role_rows: combo_row.setVisible(role not in hide)  # scroll-safe
    # hidden roles are EXCLUDED in _resolve_layer_map (combo value preserved, not cleared)
```

**The per-cell expression hook (R5), in the pure core single-cell branches:**

```
# _two_region_single_cell / _v4_single_cell, per cell:
metrics["mNG_cell_mean"]  = nanmean(mng_sub[cell_region])               # UNCHANGED (always-on)
if halo_cell_mean: metrics["Halo_cell_mean"] = nanmean(halo_sub[cell_region])  # NEW, opt-in, right after
```

**Unit dependency graph** (arrows = "must land first"):

```mermaid
graph LR
  U1[U1 · multi-timepoint verify+harden+test]
  U2[U2 · neutral relabel condensate_mask/mng_mask]
  U3[U3 · preset-aware roles capability (schema+dialog)]
  U4[U4 · add decapping-sensor-v6 preset + role decls]
  U5[U5 · per-cell expression measurement (mNG/Halo)]
  U2 --> U4
  U3 --> U4
  %% U1 and U5 are independent of the others. U2/U3/U5 all touch the dialog → land serially.
```

---

## Implementation Units

- U1. **Multi-timepoint: verify + harden + test (whole-field)**

**Goal:** Prove the whole-field analysis produces correct per-frame output on a `(T,…)`
dataset across all modes (two-region, three-region, single-cell), with a `timepoint` column
and single-t byte-identical, via the framework auto-loop.

**Requirements:** R1, R6.

**Dependencies:** None.

**Files:**
- Test: `tests/test_application/test_whole_field_intensity_module.py` (add a time-lapse suite)
- Modify (only if a gap is found): `src/percell4/application/analysis/modules/whole_field_intensity.py`
  or `src/percell4/domain/analysis/_impl/whole_field_intensity.py`

**Approach:**
- The framework (`run_analysis.py:180-189`) loads each frame via `load_layers(timepoint=t)`,
  runs the **unchanged 2D** core, and `_aggregate_timepoints` concats the `whole_field_table`
  with a `timepoint` column. Whole-field has only a `TableOutput`, so no exact-`T` ImageOutput
  concern. This unit is expected to be **test-only**.
- Build a small synthetic `(T,…)` `.h5` fixture (mirror the existing module-test fixture but
  with `n_timepoints>1`) and assert: per-frame rows for each `t`, the `timepoint` column
  present, and single-cell mode producing per-cell rows per frame. Confirm single-t output is
  unchanged (no `timepoint` column) — the existing v2/v4/v4_sc parity tests already pin this.

**Patterns to follow:** the per-particle multichannel multi-timepoint test added previously;
`run_analysis._aggregate_timepoints`.

**Test scenarios:**
- Happy path (two-region): a `(T,…)` dataset (e.g. T=3) → `whole_field_table` has rows for
  `timepoint ∈ {0,1,2}` with the `timepoint` column; v2-style params.
- Happy path (single-cell): single-cell + cp_mask on a `(T,…)` dataset → per-cell rows per
  frame, each tagged with `timepoint`; a cell present in one frame but not another is simply
  absent that frame (no error, duplicated `cell_id` across timepoints is expected). **Caveat
  (see U5):** because `cp_mask` is re-segmented per frame, the same `cell_id` across timepoints
  is not the same physical cell — cross-timepoint expression grouping needs a *tracked*
  segmentation (label == track id) or a `(timepoint, cell_id)` key.
- Happy path (three-region): v4 params on a `(T,…)` dataset → per-frame three-region rows.
- Edge case (backward compat / single-t): a single-t dataset → **no** `timepoint` column,
  byte-identical to the existing parity fixtures.

**Verification:** Running the analysis on a multi-timepoint dataset yields a CSV spanning all
timepoints (with the `timepoint` column) across modes; single-timepoint output is unchanged.

---

- U2. **Global neutral relabel: `condensate_mask` / `mng_mask`**

**Goal:** Rename the two mask roles to neutral terms across the schema, dialog, and tests,
while leaving the pure core and all output column names unchanged.

**Requirements:** R2, R6.

**Dependencies:** None.

**Files:**
- Modify: `src/percell4/application/analysis/modules/whole_field_intensity.py`
  (`required_inputs`/`optional_inputs` keys; `BoolParam.requires` tuples for `mNG_in_FLIM` and
  `intermediate_assemblies`; the `run()` `inputs[...]` keys mapped onto the unchanged core
  kwargs)
- Modify: `src/percell4/gui/whole_field_intensity_dialog.py` — beyond `_role_combos` /
  `_resolve_layer_map` / `_start_disabled_reason`, a grep-driven rename must catch the
  enumerated hardcoded spots: the `_OPTIONAL_ROLES` module constant (~`:72`) and the two
  hardcoded required-role tuples `("pbody_mask","dilute_mask","halo","mng")` (~`:201`, ~`:443`),
  plus the role display labels/desc strings.
- Modify/Test: `tests/test_application/test_whole_field_intensity_module.py`,
  `tests/test_gui/test_whole_field_intensity_dialog.py` (the `LAYER_MAP` dicts + role-name
  references). **Note:** `tests/test_domain/test_whole_field_intensity_pure.py` calls the pure
  core directly with the **core kwargs** (`pbody_mask=`, `dcp2_mask=`), which are intentionally
  **not** renamed — so that file likely needs **no** edit; verify rather than assume.
  The layer **files** in `tests/fixtures/whole_field_intensity/` do **not** change — only the
  module/dialog/test role keys that map them.

**Approach:**
- Rename roles `pbody_mask → condensate_mask`, `dcp2_mask → mng_mask`. Keep `dilute_mask`,
  `halo`, `mng`, `interaction_mask`, `sir_mask`, `dcp2_mask_2`, `interaction_mask_2`, `cp_mask`.
- At the `run()` boundary, map the new role names onto the **unchanged** pure-core kwargs:
  `pbody_mask=inputs["condensate_mask"]`, `dcp2_mask=inputs.get("mng_mask")`. The core, its
  formulas, and the output columns (`pbody_*`, `dilute_*`, `mNG_*`, `halo_*`) stay exactly as
  they are (role↔column decoupling) — so the parity CSVs are unchanged.
- Update the `BoolParam.requires` tuples that referenced the old names (`mNG_in_FLIM` requires
  `interaction_mask` + `mng_mask`; `intermediate_assemblies` requires `mng_mask` +
  `interaction_mask` + `dcp2_mask_2` + `interaction_mask_2`). Update the role descs to the
  neutral terms.

**Execution note:** Characterization-first — run the existing whole-field parity tests green
before the rename, then update the `LAYER_MAP` role keys, and confirm the parity CSVs still
match (the rename must not change a single output value).

**Patterns to follow:** the role↔column decoupling rule (loader is role-keyed); the
grouped-thresholding "never collide a domain term" guardrail.

**Test scenarios:**
- Happy path: the existing v2 / v4 / v4_single_cell parity tests pass unchanged after the
  rename (same output values), with the new role keys in `LAYER_MAP`.
- Edge case (dialog): the dialog's role combos and labels read `condensate_mask` / `mng_mask`;
  Start still gates on the (renamed) required roles.
- Error path (requires gating): `intermediate_assemblies` / `mNG_in_FLIM` still gate on the
  renamed roles (`mng_mask`, …) — a test asserting the requires-tuple wiring uses the new names.

**Verification:** The dialog shows the neutral labels; every parity fixture is byte-identical;
no output column was renamed.

---

- U3. **Preset-aware required/hidden roles capability**

**Goal:** A schema-driven capability — a preset can declare optional roles it *requires* and
optional roles it *hides* — enforced in **both** the whole-field dialog (UX block) and the
**use-case** (hard `ValueError`, so headless runs can't silently produce wrong numbers).

**Requirements:** R4, R6.

**Dependencies:** None (consumed by U4).

**Files:**
- Modify: `src/percell4/domain/analysis/base.py` (add `preset_required_inputs` /
  `preset_hidden_inputs` `ClassVar` dict fields, default empty)
- Modify: `src/percell4/application/analysis/registry.py` (light validation: the new fields'
  keys are declared roles + the preset names exist — extend the existing preset-key loop)
- Modify: **`src/percell4/application/use_cases/run_analysis.py`** (enforce preset-required
  roles: when a preset is active and a declared required role is absent from the resolved
  `layer_map`, raise a clear `ValueError` — the headless safety net)
- Modify: `src/percell4/gui/whole_field_intensity_dialog.py` (extend `_start_disabled_reason`
  to block + name a missing preset-required role; add a hidden-role pass to the
  `_refresh_state()` cascade that hides those role rows; **exclude** hidden roles inside
  `_resolve_layer_map` — do NOT clear the combo — so switch-away restores; preserve
  `wrap_in_scroll`)
- Test: `tests/test_gui/test_whole_field_intensity_dialog.py`,
  `tests/test_application/test_run_analysis.py` (headless enforcement),
  the registry test module

**Approach:**
- Add the two `Analysis`-base fields (default `{}`). Light registry validation: a
  preset-required/hidden role that isn't a declared input, or a preset name not in `presets`,
  fails loud at registration (extend the existing preset-key loop — no new machinery).
- **Headless enforcement (the critical half):** `run_analysis` reads
  `getattr(cls, "preset_required_inputs", {})` for the active preset and raises a clear
  `ValueError` when a required role is absent from `layer_map` — so a notebook/`batch_run_analysis`
  v6 run with `mng_mask`/`interaction_mask` unset fails loud instead of silently no-opping the
  filter (`run_analysis` otherwise validates only `required_inputs`/groups/`BoolParam.requires`).
- **Dialog (UX):** read `getattr(type(analysis), "preset_required_inputs/._hidden_inputs", {})`
  keyed by the active preset. `_start_disabled_reason` adds a clear reason per missing required
  role (`"Preset 'decapping-sensor-v6' requires 'mng_mask'."`). A new cascade step **hides** the
  preset-hidden role rows; hidden roles are **excluded inside `_resolve_layer_map`** (the combo
  value is preserved, not cleared) so switching to a non-hiding preset restores the prior
  selection. Keep scroll-safe (`wrap_in_scroll` still wraps the variable-height role section —
  assert via the helper-compliance test). A hidden role that some param `requires` is already
  disabled by the existing `_refresh_requires_gating` once it's out of `layer_map`.
- Wire it off the existing `_on_preset_changed → _refresh_state()` cascade (the preset combo's
  `activated` already drives it). Dialog-local only — no session writes (Action).

**Patterns to follow:** the existing `BoolParam.requires` gating; `qt-wire-user-edit-signals`
(`activated` → `_refresh_state`); `dialog-scroll-when-tall`; the `analysis_widgets.py` factories.

**Test scenarios:**
- Happy path (dialog required): with a preset declaring a required role and that role
  unassigned, Start is disabled with a reason naming the role; assigning it enables Start.
- Error path (**headless required**): `run_analysis`/`batch_run_analysis` for a preset whose
  required role is absent from `layer_map` raises a clear `ValueError` (it does **not** run and
  silently no-op the filter). *This is the headless safety net — the most important scenario.*
- Happy path (hidden + restore): with a preset declaring hidden roles, those rows are hidden
  and the roles are excluded from the resolved `layer_map`; a role the user assigned *before*
  selecting the hiding preset has its combo value **preserved**, and switching to a non-hiding
  preset shows the row again with the prior selection intact (no destructive clear).
- Edge case (no preset): with "No preset" selected, no roles are required/hidden beyond the
  base schema — byte-identical to today.
- Error path (schema validation): registering an analysis whose `preset_required_inputs` /
  `preset_hidden_inputs` names an undeclared role, or an unknown preset, raises at registration.
- Edge case (scroll): showing/hiding roles keeps the dialog scroll-wrapped (helper-compliance
  test passes).

**Verification:** A preset can require + hide roles; the dialog blocks Start with a clear
reason and hides irrelevant roles; a headless run with a missing preset-required role fails
loud; switch-away restores hidden-role selections.

---

- U4. **Add the `decapping-sensor-v6` preset + its role declarations**

**Goal:** Ship the stress-granule v6 preset (matching the source) plus its preset-required
(`mng_mask` + `interaction_mask`) and preset-hidden (intermediate masks) role declarations.

**Requirements:** R3 (uses R4's capability).

**Dependencies:** U2 (neutral role names), U3 (preset-aware fields + dialog).

**Files:**
- Modify: `src/percell4/application/analysis/modules/whole_field_intensity.py` (add the
  `decapping-sensor-v6` preset entry; add `preset_required_inputs = {"decapping-sensor-v6":
  ("mng_mask", "interaction_mask")}` and `preset_hidden_inputs = {"decapping-sensor-v6":
  ("dcp2_mask_2", "interaction_mask_2", "sir_mask")}`)
- Modify: `tests/fixtures/preset_snapshots/whole_field_intensity.json` (mirror v6 — required by
  `test_presets_immutable.py`)
- Test: `tests/test_application/test_whole_field_intensity_module.py`,
  `tests/test_application/test_presets_immutable.py`

**Approach:**
- Add `decapping-sensor-v6` with the source's parameters: `min_size=2`, `mng_bg_mode="manual"`/
  `mng_bg_value=0`, `halo_bg_mode="manual"`/`halo_bg_value=0`, `mNG_filter="NaN"`,
  `percent=True`, `exclude_halo_zero=True`, `exclude_halo_one=False`, `SiR_subtract="none"`,
  `SiR_filter=False`, `FLIM_filter="zero"`, `mNG_in_FLIM=False`, `intermediate_assemblies=False`,
  `intermediate_zero_fill=False`. (Two-region; the SG/mNG substitution is the user assigning
  their SG/mNG layers to the `condensate_mask`/`mng_mask` roles — no preset-side role mapping.)
- Declare v6's required roles — **`mng_mask`** (its `mNG_filter="NaN"` silently no-ops without
  it) **and `interaction_mask`** (its `FLIM_filter="zero"` only applies when `interaction_mask`
  is supplied — `_impl/whole_field_intensity.py:399` — else the Halo filtering silently never
  runs and the means are wrong) — and hidden roles (the intermediate
  `dcp2_mask_2`/`interaction_mask_2` and `sir_mask`,
  all irrelevant to v6's two-region path). Mirror v6 into the preset snapshot JSON.
- Do **not** modify v2–v5 (immutability). v6 keeps the generic `pbody`/`dilute` output column
  names.

**Execution note:** Eye-validate v6 on a real stress-granule dataset after it lands (the
empty-mask-reported-as-success trap) — the unit tests prove the wiring, the eye proves the
science.

**Patterns to follow:** the existing v4/v5 preset entries; `test_presets_immutable.py`'s
snapshot mirror; the source preset (`whole_field_analysis.py:773-787`).

**Test scenarios:**
- Happy path: running v6 on a two-region dataset (condensate + dilute + mng-filter + Halo + mNG)
  produces the expected `pbody_*`/`dilute_*` columns with `mNG_filter='NaN'` + `FLIM_filter='zero'`
  semantics (parity against a v6 expected CSV, if a fixture is generated, else a value check).
- Edge case (preset-required roles): selecting v6 in the dialog without `mng_mask` **or**
  `interaction_mask` blocks Start naming the missing role (via U3); with both, runs.
- Error path (headless v6): a `run_analysis` v6 run missing `mng_mask` or `interaction_mask`
  raises a clear `ValueError` (via U3's use-case enforcement) — not a silent unfiltered result.
- Edge case (preset-hidden roles): selecting v6 hides `dcp2_mask_2`/`interaction_mask_2`/`sir_mask`.
- Error path (immutability): `test_presets_immutable.py` passes with the v6 snapshot added and
  v2–v5 unchanged; mutating any v2–v5 value fails the test.

**Verification:** v6 appears in the preset combo, requires `mng_mask` **and** `interaction_mask`
(blocked in both the dialog and headless runs when absent), hides the intermediate masks, and
produces correct two-region stress-granule measurements; v2–v5 are untouched.

---

- U5. **Per-cell whole-cell expression measurement (opt-in `Halo_cell_mean`)**

**Goal:** In single-cell mode, keep the existing always-on `mNG_cell_mean` and add an opt-in
whole-cell `Halo_cell_mean` per cell, for grouping cells by expression — mirroring the
per-particle `cell_mean` feature without re-gating the existing column.

**Requirements:** R5, R6.

**Dependencies:** None (independent of the relabel; touches the dialog + module + core).

**Files:**
- Modify: `src/percell4/domain/analysis/_impl/whole_field_intensity.py`
  (`_two_region_single_cell` ~574-579 and `_v4_single_cell` ~613: leave `mNG_cell_mean` as-is;
  add `Halo_cell_mean` immediately after it, gated on a new `halo_cell_mean` kwarg default False)
- Modify: `src/percell4/application/analysis/modules/whole_field_intensity.py` (add ONE
  `BoolParam` `halo_cell_mean` default **False** — **no** `requires=("cp_mask",)`; pass it to
  the core in `run()`)
- Modify: `src/percell4/gui/whole_field_intensity_dialog.py` (one checkbox via
  `build_param_widget`, greyed unless `single_cell` is on like the other single-cell controls;
  keep `wrap_in_scroll`)
- Test: `tests/test_domain/test_whole_field_intensity_pure.py`,
  `tests/test_application/test_whole_field_intensity_module.py`,
  `tests/test_gui/test_whole_field_intensity_dialog.py`

**Approach:**
- **Do NOT re-gate `mNG_cell_mean`** — the core already always computes it in single-cell mode
  (parity-safe, zero schema change). Add `Halo_cell_mean = np.nanmean(halo_sub[cell_region])`
  (the bg-subtracted Halo over each cell's `cp_mask` region) when `halo_cell_mean` is True,
  placed **immediately after `mNG_cell_mean`** in the per-cell row (existing layout:
  `cell_id, cell_area_px, particle_count, mNG_cell_mean`) so `v4_sc.csv` stays byte-identical.
  Use float-safe `np.nanmean` (not integer reductions).
- The module declares `halo_cell_mean` (default **False**, **no** `BoolParam.requires` — a
  default-False param that fires only when the user opts in, like the per-particle precedent
  at `per_particle_multichannel.py:196-200`; a `requires=("cp_mask",)` on it would be safe only
  because it defaults False, but the core branch already gates on cp_mask, so omit it). The new
  core kwarg `halo_cell_mean` defaults **False** so the in-repo second caller
  `scripts/whole_field_analysis.py:339` is unaffected. Outside single-cell mode it's a no-op.
- The measurement is a `TableOutput` column (→ CSV), never an h5 write (provenance).
- **Caveat for the user's downstream grouping (cross-timepoint):** the per-cell value keys on
  the `cp_mask` `cell_id`, which is *re-segmented per frame* on a time-lapse — `cell_id=5` at
  `t=0` and `t=2` may be different physical cells. To group cells by expression *across*
  timepoints, use a **tracked** segmentation as `cp_mask` (label == track id) or key on
  `(timepoint, cell_id)`; grouping within a single frame is unaffected. Documented so the
  downstream grouper isn't fed conflated ids (see U1).

**Execution note:** Characterization-first — confirm `v4_sc.csv` (which contains `mNG_cell_mean`)
matches **before and after** adding `Halo_cell_mean` (it must not move or change a value).

**Patterns to follow:** per-particle `cell_mean_channels` (module BoolParam default-False + no
`requires` + `run()` pass-through + core per-cell add); the existing `mNG_cell_mean` computation
as the template; the `analysis_widgets.py` checkbox factory.

**Test scenarios:**
- Happy path (default): single-cell run with defaults → `mNG_cell_mean` present and equal to the
  prior value (parity preserved, position unchanged); `Halo_cell_mean` absent.
- Happy path (Halo opt-in): `halo_cell_mean=True` → a `Halo_cell_mean` column right after
  `mNG_cell_mean`, whose per-cell value equals `np.nanmean` of the bg-subtracted Halo over each
  cell's `cp_mask` region.
- Edge case (no single-cell / no cp_mask): `halo_cell_mean` is a no-op (no column; no raise);
  aggregate output unchanged — and a `halo_cell_mean=True` run *without* cp_mask does **not**
  raise (no `requires=cp_mask`).
- Edge case (empty/NaN cell): a cell whose Halo pixels are all NaN → `Halo_cell_mean` is NaN, no
  error.
- Edge case (pure-core default): `run_one_image_set(...)` called directly with `single_cell=True`
  and the default `halo_cell_mean=False` still emits `mNG_cell_mean` and no `Halo_cell_mean`
  (the pure tests are unaffected; the second core caller is unaffected).
- Integration (dialog): the checkbox appears, greyed unless single-cell; opting in flows through
  to the `Halo_cell_mean` column in the run.

**Verification:** Single-cell output keeps `mNG_cell_mean` unchanged and gains an opt-in
`Halo_cell_mean`; the parity fixture is byte-identical by default; cross-timepoint grouping
guidance is documented.

---

## System-Wide Impact

- **Interaction graph:** U1 touches only tests (+ the framework loop it relies on). U2 renames
  roles across module + dialog + tests (no core/column change). U3 adds generic schema fields +
  registry validation + dialog cascade steps. U4 adds a preset + role declarations + snapshot.
  U3 also adds enforcement in `run_analysis` (the use-case). U5 adds one core column
  (`Halo_cell_mean`) + one module `BoolParam` + one dialog checkbox. U2/U3/U5 all touch
  `whole_field_intensity_dialog.py` → land serially. No cross-window signal changes.
- **Error propagation:** schema-validation failures (U3) are fail-loud at registration; a
  preset-required role missing from `layer_map` raises a `ValueError` in **both** the dialog
  (Start blocked) and `run_analysis` (headless); other cross-cutting constraint violations remain
  `ValueError` (recorded as a failed batch item).
- **State lifecycle risks:** none new to storage — analyses write a `TableOutput` (CSV), never
  the `.h5` (provenance). The cell-mean is a column, not a resource.
- **API surface parity:** the new `preset_required_inputs`/`preset_hidden_inputs` fields are
  optional `Analysis`-base additions other analyses can adopt; existing analyses (empty defaults)
  are unaffected.
- **Integration coverage:** U1 mandates a real time-lapse run; U4 mandates an eye-validation on
  real stress-granule data (synthetic tests can't prove the science).
- **Unchanged invariants:** the pure core math + output column names, v2–v5 presets, the parity
  CSVs (except U5's explicit new column), single-timepoint behavior, and the Action contract (no
  session writes) are all explicitly preserved.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| The role rename (U2) silently changes an output value or breaks a fixture. | D2 role↔column decoupling: core + columns untouched; characterization-first parity check before/after; only the module/dialog/test role keys change (verified: no role-name→layer auto-match; the pure test uses unchanged core kwargs). |
| Editing an existing preset / drifting the snapshot. | D3 immutability: v6 is a new entry; `test_presets_immutable.py` + the snapshot JSON guard v2–v5. |
| **Headless v6 silently produces wrong numbers** — a notebook/batch run with `mng_mask` or `interaction_mask` absent makes `mNG_filter`/`FLIM_filter` a silent no-op. | D4: `run_analysis` enforces `preset_required_inputs` as a hard `ValueError`; v6 requires both `mng_mask` **and** `interaction_mask`; eye-validation (D6) as a backstop. |
| Adding the cell-mean BoolParam with `requires=cp_mask` breaks every cp_mask-less run. | D5: `halo_cell_mean` defaults **False** and carries **no** `requires=cp_mask`; gated inside the single-cell core branch (per the per-particle precedent). |
| Hiding roles breaks the scroll-wrapped dialog (CI guard). | Keep `wrap_in_scroll`/`cap_to_screen`; assert via `test_dialog_helper_compliance.py`; hide rows, don't restructure the scroll area. |
| v6 passes synthetic tests but collapses to empty masks on real data. | D6 eye-validation on a real stress-granule dataset (the empty-mask-as-success trap). |
| A hidden role leaks into `layer_map`, or a switch-away loses the user's assignment. | U3: hide the row but **exclude** hidden roles at `_resolve_layer_map` (preserve the combo value) — no destructive clear, so switch-away restores. |
| The new role names collide with a reserved/segmentation term. | `condensate_mask`/`mng_mask` are checked against reserved names (esp. the reserved `whole_field`); grouped-thresholding #7 guardrail. |
| Cross-timepoint expression grouping conflates re-segmented `cell_id`s. | D5/U5 caveat: use a tracked segmentation (label == track id) as `cp_mask` or key on `(timepoint, cell_id)`; documented for the downstream grouper. |

---

## Documentation / Operational Notes

- Update the relevant per-module `CLAUDE.md` (current-state only) for the whole-field module +
  the new `Analysis`-base preset-aware fields.
- After landing, capture two `/ce-compound` entries: (a) the analysis-framework **multi-timepoint
  contract** (currently code-only), and (b) the **preset-aware required/hidden-roles** capability.
- No migration: purely additive + a role-label rename (no on-disk change). Existing run folders
  and `.h5` files are unaffected.

---

## Sources & References

- **The script:** `src/percell4/application/analysis/modules/whole_field_intensity.py`,
  `src/percell4/domain/analysis/_impl/whole_field_intensity.py`,
  `src/percell4/gui/whole_field_intensity_dialog.py`.
- **Framework:** `src/percell4/application/use_cases/run_analysis.py` (per-timepoint loop +
  `_aggregate_timepoints`), `src/percell4/domain/analysis/base.py`,
  `src/percell4/application/analysis/registry.py`, `src/percell4/gui/analysis_widgets.py`,
  `src/percell4/gui/_dialog_utils.py`.
- **Precedent (cell_mean):** `src/percell4/application/analysis/modules/per_particle_multichannel.py`,
  `src/percell4/domain/analysis/_impl/per_particle_multichannel.py`.
- **v6 source of truth (sibling repo, outside percell4):**
  `/Users/leelab/mask-intensity-analysis-repo/whole_field_analysis.py` (preset
  decapping-sensor-v6 ~lines 755–787 + `PRESET_CHANNEL_ROLES`),
  `/Users/leelab/mask-intensity-analysis-repo/README.md` (Stress-granule preset v6 section).
  The v6 values are inlined in U4 (authoritative); the in-repo `scripts/whole_field_analysis.py`
  is a different, older file without v6.
- **Fixtures/guards:** `tests/fixtures/preset_snapshots/whole_field_intensity.json`,
  `tests/fixtures/whole_field_intensity/`, `tests/test_application/test_presets_immutable.py`,
  `tests/test_gui/test_dialog_helper_compliance.py`.
- **Learnings:** `docs/solutions/architecture-patterns/registered-analysis-framework.md`,
  `docs/solutions/architecture-patterns/extending-per-cell-detection-to-time-lapse-2026-06-25.md`,
  `docs/solutions/logic-errors/grouped-thresholding-development-lessons.md`,
  `docs/solutions/architecture-patterns/adding-thresholding-method-to-single-cell-workflow-2026-06-15.md`,
  `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`,
  `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`,
  `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`.
