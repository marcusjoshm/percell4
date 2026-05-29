---
title: "Adding a registered analysis: pure core + module + CLI + dialog"
date: 2026-05-28
last_updated: 2026-05-28
category: architecture-patterns
module: percell4.application.analysis
problem_type: architecture_pattern
component: tooling
severity: medium
canonical_source: src/percell4/application/analysis/modules/per_particle_donut.py
applies_to:
  - "src/percell4/application/analysis/**/*.py"
  - "src/percell4/domain/analysis/**/*.py"
  - "src/percell4/application/use_cases/run_analysis*.py"
  - "src/percell4/gui/analysis_widgets.py"
status: canonical_clean
tags:
  - analysis
  - registry
  - registered-analysis
  - pure-core
  - single-source-of-truth
  - preset
  - layer-map
  - dialog
applies_when:
  - adding a new registered analysis (a mask-intensity script or similar) to the framework
  - editing the Analysis base, registry, loader, run_analysis, or the batch runner
  - building or modifying an analysis dialog under src/percell4/gui/*_dialog.py
  - declaring analysis inputs/params/presets/outputs or wiring a layer_map
---

# Adding a registered analysis

PerCell4 turns standalone per-image-set scripts into **registered analyses**
that the Scripts tab discovers and batch-runs across `.h5` datasets to CSV.
The full step-by-step is in **`docs/writing_an_analysis.md`**; this entry is
the durable "what must hold" summary that the learnings hook surfaces when you
touch the analysis package.

Worked examples: `per_particle_donut` (the reference), `per_particle_multichannel`,
`whole_field_intensity`.

## The load-bearing rules

1. **Single source of truth.** The math lives in a pure function
   `run_one_image_set(*, <arrays>, <params>) -> dict` under
   `src/percell4/domain/analysis/_impl/<name>.py` — no file I/O, no Qt, one
   dataset per call. Two consumers share it: the repo-root CLI wrapper
   (`<name>.py`, does TIFF reads + dir walk + CSV writes) and the declarative
   `Analysis` subclass (`application/analysis/modules/<name>.py`, wraps it for
   the framework). They cannot diverge numerically because they call the same
   function. Mutating inputs (e.g. background subtraction) must copy first.
   Reuse `_impl/_shared.py` helpers (labeling, donut geometry,
   `assign_particles_to_cells`, `weighted_mean`, `nan_safe_ratio`) **only when
   behavior is identical** — `whole_field_intensity` keeps its own
   `np.unique`+`argmax`+skip-0 particle assignment verbatim because its
   tie-break/background-majority handling differs from the shared bincount
   version; forcing the shared helper would silently change its numbers.

2. **Numeric parity is pinned by a characterization fixture, built BEFORE the
   refactor.** Generate expected CSVs from the *unmodified* original CLI,
   commit them under `tests/fixtures/<name>/`, then extract the core and assert
   identical output. Compare **per-dataset** (`per_dataset/<stem>_<table>.csv`
   vs a single-prefix CLI run): drop `group`/`dataset` id columns, reindex both
   to `sorted(columns)`, sort rows by the id key, integer exact-equal, float
   `np.allclose(rtol=1e-10, equal_nan=True)`.

3. **Input groups are all-or-nothing.** `run_analysis` computes
   `group_satisfied = all(role in layer_map for role in group_roles)`, so a
   single `input_group` with `group_requirement="at_least_one"` is satisfied
   only when **every** role in it is supplied. Never model "any one of N
   optional inputs" as one multi-role group — put the first in
   `required_inputs` and the rest in `optional_inputs`, or use one single-role
   group per option. (multichannel's 8 channel slots = `channel_1` required +
   `channel_2..8` optional, no group.) Gate "this option needs these masks"
   with `BoolParam(requires=(...roles...))`, which fires only when the bool is
   True.

4. **The loader is role-keyed; it drops the chosen layer name.** If an analysis
   must name outputs after the user's chosen layer (e.g. multichannel
   `condensed_<layername>_mean`), declare a `layer_map` kwarg on `run()` —
   `run_analysis` forwards it (and `log`/`set_label`) only when the override
   names it, via `_accepted_progress_kwargs` signature introspection. Pure
   cores stay role-agnostic; the declarative `run()` does the naming.

5. **Presets are immutable, pinned by a snapshot test.** Declare `presets` as
   an in-code dict; mirror it to `tests/fixtures/preset_snapshots/<name>.json`;
   `tests/test_application/test_presets_immutable.py` fails on drift. Never edit
   a preset value in place — add a new version. No import-time hashing.

6. **Dual-typed / optional params.** The framework has IntParam / FloatParam /
   ChoiceParam / BoolParam only. A keyword-or-integer field (e.g. a background
   mode) → `ChoiceParam(choices + "manual")` + an `IntParam` value, mapped in
   `run()`. A 3-way `none/zero/NaN` filter → `ChoiceParam` with `"none"` mapped
   to Python `None` in `run()`. Constraints the schema cannot express
   (mutually-exclusive options) are `ValueError` guards in `run()` plus dialog
   gating.

7. **Outputs persist by their dict key.** A `TableOutput` named `foo` →
   `combined_foo.csv` + `per_dataset/<stem>_foo.csv`; an `ImageOutput` named
   `foo` → `store.write_mask`/`write_labels` at `/masks/foo` or `/labels/foo`.
   Namespace generic names (`multichannel_donut_mask`, not `donut_mask`) and
   never reuse a reserved name like `whole_field` (the all-ones baseline
   segmentation). `produced_when(group_state, params)` is a real callable;
   `run()` must return exactly the produced key set.

8. **Dialogs are Actions.** A `<Name>Dialog(QDialog)` reuses the
   `analysis_widgets.py` factories (never rebuild combos from a params
   dataclass), wraps content in `wrap_in_scroll` + `cap_to_screen` (enforced by
   `tests/test_gui/test_dialog_helper_compliance.py`), drives a single
   `_refresh_state()` cascade, and **never writes the five session selection
   fields**. Late-bind `cls.dialog_class = <Name>Dialog` at module bottom and
   import the dialog module in `main_window`'s two scripts-panel sites. Wire
   user-edit signals (`activated`/`toggled`/`valueChanged`), not programmatic
   setters, to the cascade.

## Files to create/modify per analysis

Create: `domain/analysis/_impl/<name>.py`, `application/analysis/modules/<name>.py`,
`gui/<name>_dialog.py`, repo-root `<name>.py` (CLI), and tests
(`tests/test_domain/test_<name>_pure.py`, `tests/test_scripts/test_<name>_regression.py`,
`tests/test_application/test_<name>_module.py`, `tests/test_gui/test_<name>_dialog.py`,
`tests/fixtures/<name>/`). Modify: `application/analysis/__init__.py` (import to
fire `@register_analysis`) and `interfaces/gui/main_window.py` (import the dialog
in both scripts-panel sites). The registry, loader, `run_analysis`, batch runner,
run-folder, and Scripts tab are generic — they need no per-analysis change.

## See also

- `docs/writing_an_analysis.md` — the full step-by-step.
- `[[sibling-dialog-extract-shared-widget-2026-05-12]]` — consume the shared
  widget factories; don't rebuild from a dataclass.
- `[[dialog-scroll-when-tall]]` — `wrap_in_scroll` + `cap_to_screen` (CI-enforced).
- `[[gui-action-contract-exhaustiveness]]` — Run/preset buttons stay Actions.
