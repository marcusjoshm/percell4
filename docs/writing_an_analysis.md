# Writing a registered analysis

This guide shows how to turn a standalone per-image-set script (like the
mask-intensity scripts) into a **registered analysis** that appears in the
Scripts tab and batch-runs across `.h5` datasets to CSV.

Three analyses are worked examples you can copy from:

| Analysis | Pure core | Module | Dialog | CLI |
|---|---|---|---|---|
| `per_particle_donut` (reference) | `domain/analysis/_impl/per_particle_donut.py` | `application/analysis/modules/per_particle_donut.py` | `gui/per_particle_donut_dialog.py` | `per_particle_analysis.py` |
| `per_particle_multichannel` (dynamic channel list) | `.../_impl/per_particle_multichannel.py` | `.../modules/per_particle_multichannel.py` | `gui/per_particle_multichannel_dialog.py` | `per_particle_multichannel.py` |
| `whole_field_intensity` (presets, 3-region, single-cell) | `.../_impl/whole_field_intensity.py` | `.../modules/whole_field_intensity.py` | `gui/whole_field_intensity_dialog.py` | `whole_field_analysis.py` |

The framework itself — registry, loader, `run_analysis`, batch runner,
run-folder, Scripts tab — is generic. Adding an analysis is **additive**: you
write four files, two tests-worth of coverage, and add two import lines.

---

## Architecture in one picture

```
repo-root CLI <name>.py            application/analysis/modules/<name>.py        gui/<name>_dialog.py
  reads TIFFs, dir-walk, CSV  ──┐    @register_analysis("<name>")                  dataset picker,
                               │    class <Name>(Analysis): schema + run()  ◄────── role→layer combos,
                               │      └ run() renames cols via layer_map            preset lock, Start
                               ▼          ▼                                          (Action) → batch_run_analysis
       domain/analysis/_impl/<name>.py   run_one_image_set(*, arrays, params, set_label, log)
                               │          │   PURE: no I/O, one dataset per call
                               └────┬─────┘
                                    ▼
       domain/analysis/_impl/_shared.py   donut geometry · assign_particles_to_cells · _weighted_mean
                                          · nan_safe_ratio · label_and_filter · nanmean/nansum_or_nan
```

The **single source of truth** is the pure core. The CLI and the framework
module both call it, so they cannot diverge numerically.

---

## Step 1 — Extract the pure core

Create `src/percell4/domain/analysis/_impl/<name>.py` with a kwargs-only
`run_one_image_set(*, <arrays>, <params>, set_label="", log=None) -> dict`:

- **No file I/O, no Qt, one dataset per call.** Inputs are numpy arrays
  (the caller reads TIFFs/`.h5`).
- **Copy before mutating** (`arr.astype(np.float64, copy=True)`) so the
  caller's arrays are untouched.
- Reuse `_impl/_shared.py` helpers (`label_and_filter`,
  `region_and_donut_masks`, `assign_particles_to_cells`, `weighted_mean`,
  `nan_safe_ratio`, `nanmean_or_nan`, `nansum_or_nan`) instead of duplicating.
  *Caveat:* if your assignment/tie-break semantics differ (whole-field's
  `np.unique`+`argmax`+skip-0 vs the shared bincount), keep your own verbatim
  rather than bending the shared helper.
- Thread an optional `log` sink (`if log is not None: log(...)`) so both the
  CLI and the GUI batch stream progress to the terminal.
- Return a plain dict of row-lists / arrays (e.g. `{"rows": [...]}` or
  `{"particle_rows": ..., "cell_rows": ..., "donut_mask": ...}`).

## Step 2 — Build the characterization fixture FIRST

Numeric parity is the contract. Before touching the original script:

1. Write `tests/fixtures/<name>/_generate_fixtures.py` to emit deterministic
   synthetic TIFFs (fixed values, no randomness).
2. Run the **unmodified original CLI** against them and commit the resulting
   CSVs under `tests/fixtures/<name>/expected/`.
3. Add `tests/test_scripts/test_<name>_regression.py` that runs the repo-root
   CLI via `subprocess` and compares to the committed expected CSVs with the
   parity rule: drop `group`/`dataset`, reindex to `sorted(columns)`, sort rows
   by the id key, integer exact-equal, float
   `np.allclose(rtol=1e-10, equal_nan=True)`.

Compare **per-dataset** (`per_dataset/<stem>_<table>.csv` vs a single-prefix
CLI run) — `cell_id`/`particle_id` are not unique across datasets, so a
combined-CSV sort would interleave rows non-deterministically.

## Step 3 — Wrap the CLI around the core

Copy the original script to the repo root as `<name>.py`, add the `src/` path
shim, and replace its per-image-set math with a call to `run_one_image_set`.
Keep its filename→channel detection, directory walk, and CSV column ordering so
the regression test (which compares to the original's output) stays green.

## Step 4 — Declare the `Analysis` module

Create `application/analysis/modules/<name>.py` with an
`@register_analysis("<name>")`-decorated `Analysis` subclass. Declare:

- **`required_inputs` / `optional_inputs`** as `{role: ImageRole(kind=..., dtype=..., desc=...)}`
  (`kind` ∈ `intensity` / `mask` / `label`). **Do not** model "any one of N
  optional inputs" as a single multi-role `input_group` — a group with
  `group_requirement="at_least_one"` is satisfied only when *every* role in it
  is present. Put the first required, the rest optional (see
  `per_particle_multichannel`'s `channel_1` + `channel_2..8`).
- **`parameters`** as IntParam / FloatParam / ChoiceParam / BoolParam. For a
  keyword-or-integer field, use `ChoiceParam(... + "manual")` + an `IntParam`
  value. For a 3-way `none/zero/NaN`, use a `ChoiceParam` and map `"none" →
  None` in `run()`. Gate "this option needs that input" with
  `BoolParam(requires=(...roles...))`.
- **`presets`** as an in-code dict + a committed
  `tests/fixtures/preset_snapshots/<name>.json` + a case in
  `tests/test_application/test_presets_immutable.py`. Never edit a preset in
  place; add a version.
- **`outputs`** as `{name: TableOutput(produced_when=...)}` /
  `ImageOutput(dtype=..., produced_when=...)`. The dict key becomes the CSV /
  HDF5 resource name — namespace generic names, never reuse `whole_field`.
- **`run(self, inputs, params, *, log=None, set_label="", layer_map=None)`** —
  map framework params to the core's resolved kwargs, dispatch, and return
  exactly the produced key set. Add `layer_map` only if you need the chosen
  layer names for column headers. Raise `ValueError` for constraints the schema
  can't express.

Then add the module to the import block in
`src/percell4/application/analysis/__init__.py` so `@register_analysis` fires.

## Step 5 — Build the dialog

Create `gui/<name>_dialog.py`, a `QDialog` that composes the
`gui/analysis_widgets.py` factories (`build_dataset_picker`,
`build_layer_combo` + `populate_layer_combo`, `build_param_widget`,
`build_preset_combo`, `build_output_parent_picker`). Mirror
`per_particle_donut_dialog.py`. It must:

- `wrap_in_scroll(content)` + `cap_to_screen(self)` (CI-enforced by
  `test_dialog_helper_compliance.py`).
- Drive one `_refresh_state()` cascade from **user-edit** signals
  (`activated`/`toggled`/`valueChanged`), never programmatic setters.
- Be an **Action**: never write the five session selection fields.
- Late-bind `cls.dialog_class = <Name>Dialog` at the bottom of the module.

For a dynamic input list (multichannel's "Add channel"), keep a Python list of
row widgets and rebuild/relabel on add/remove (see
`per_particle_multichannel_dialog.py`). Finally, import the dialog module in
`interfaces/gui/main_window.py`'s two scripts-panel sites so the late-binding
fires; the Scripts tab then shows the button automatically.

## Step 6 — Tests

- `tests/test_domain/test_<name>_pure.py` — pure-core happy/edge/error paths
  (in-process; pin tricky numerics like per-branch rounding).
- `tests/test_scripts/test_<name>_regression.py` — CLI-vs-committed parity.
- `tests/test_application/test_<name>_module.py` — registration + framework
  end-to-end + framework↔CLI parity + constraint-guard error paths (use an
  autouse fixture that reloads the module if the registry was cleared).
- `tests/test_gui/test_<name>_dialog.py` — construction, `dialog_class`
  binding, Start gating, preset lock, requires-gating, Start dispatch.

See the `registered-analysis-framework` entry in `docs/solutions/` for the
condensed "what must hold" rules the learnings hook surfaces when you edit the
analysis package.
