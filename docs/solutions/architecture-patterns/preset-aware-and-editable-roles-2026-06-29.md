---
title: "Preset-aware roles + preset-editable mode params: declare on Analysis, enforce in dialog AND use-case"
date: 2026-06-29
last_updated: 2026-06-29
category: architecture-patterns
module: percell4.domain.analysis
problem_type: architecture_pattern
component: tooling
severity: medium
canonical_source: src/percell4/domain/analysis/base.py
applies_to:
  - "src/percell4/domain/analysis/base.py"
  - "src/percell4/application/analysis/registry.py"
  - "src/percell4/application/use_cases/run_analysis*.py"
  - "src/percell4/gui/*_dialog.py"
status: canonical_clean
tags:
  - analysis
  - preset
  - dialog
  - registry
  - headless-safety
  - layer-map
  - mode-params
  - provenance
applies_when:
  - adding or editing a preset that requires specific masks or hides irrelevant ones
  - making a mode/output toggle stay editable under a preset
  - touching the Analysis base, registry validation, run_analysis preset handling, or an analysis dialog
---

# Preset-aware roles + preset-editable mode params

A registered `Analysis` preset originally fixed only *science parameters* and
fully locked everything else. Two needs broke that: (a) a preset that
scientifically *depends* on certain optional masks (e.g. v6's `mNG_filter` /
`FLIM_filter` consume their masks), and (b) "mode" toggles like `single_cell`
that are orthogonal to the science a preset sets and should stay user-editable.
This pattern adds three declarative `ClassVar` fields and enforces them in
**both** the dialog and the headless use-case.

## Guidance

Three fields on `Analysis` (`base.py`), all defaulting empty so existing
analyses are unaffected:

```python
preset_required_inputs: ClassVar[dict[str, tuple[str, ...]]] = {}  # roles a preset REQUIRES
preset_hidden_inputs:   ClassVar[dict[str, tuple[str, ...]]] = {}  # roles a preset HIDES
preset_editable_params: ClassVar[tuple[str, ...]] = ()             # mode params editable under ANY preset
```

**Validate at registration (fail-loud).** `registry.validate_schema` checks
each role is a declared input (required / optional / any group), each preset
name exists in `presets`, each editable param is a declared parameter, and
rejects a role declared **both** required and hidden for the same preset (that
would soft-lock the dialog — hide the row yet block Start on its absence — and
always hard-fail headless).

**Enforce in BOTH surfaces — a dialog-only block is insufficient.**

- *Dialog* (`gui/*_dialog.py`): Start is blocked with a reason naming the
  missing preset-required role (`_start_disabled_reason`). Hidden-role rows are
  hidden (`_refresh_hidden_roles` sets label + combo invisible) and excluded in
  `_resolve_layer_map` **without clearing the combo value** — switching to a
  non-hiding preset restores the prior selection ("hide-not-clear"). A hidden
  role that a `BoolParam` requires is disabled by the existing
  `_refresh_requires_gating`.
- *Use-case* (`run_analysis`, the headless safety net): when a preset is
  active, any declared required role absent from `layer_map` raises a hard
  `ValueError` (step "3b"). Without this, a notebook / batch run with a missing
  mask would silently no-op a filter and emit scientifically-wrong numbers —
  `run_analysis` otherwise validates only `required_inputs` / groups /
  `BoolParam.requires`.

**`resolve_params` overlay rule.** A preset + a non-editable param still raises
(no mixing). A preset + a *declared editable* mode param is allowed and
overlaid onto the preset — the preset's science values stay authoritative, and
the preset name stays in provenance (`batch_run_analysis` records
`preset_name` / `preset_values`). The dialog mirrors this: under a preset it
passes only the editable subset as `params`.

## Why This Matters

The dialog gating is UX; the use-case check is correctness. The danger is a
preset whose science depends on an input the caller forgot to map: the dialog
catches a human, but a headless batch would run clean and produce numbers that
look right and are wrong. Enforcing the same contract in `run_analysis` closes
that silent-no-op path. Separating "science params (locked)" from "mode toggles
(overlayable)" keeps a preset authoritative over its science while letting users
flip `single_cell` / `channel_cell_mean` / `export_particles` without forking a
new preset.

## When to Apply

Adding or editing a preset that needs specific masks or hides irrelevant ones;
making a mode/output toggle stay editable under a preset; or touching `base.py`,
`registry.validate_schema`, `run_analysis` preset handling, or an analysis
dialog's `_refresh_state` cascade.

## Examples

`WholeFieldIntensity` declares `single_cell` / `channel_cell_mean` /
`export_particles` editable; its v6 preset requires the mNG/FLIM masks and hides
roles irrelevant to that mode.
The dialog locks science params (`_refresh_preset_lock`) but leaves editable
ones clickable, then refines them via the existing requires/manual-bg gating.
The preset-change handler fires on the combo's `activated` signal into
`_refresh_state()`, which hides hidden rows and re-gates Start in one pass.

## Related

- `[[registered-analysis-framework]]` — the parent pattern; this extends its
  rule 5 (presets-immutable: presets may now declare required/hidden/editable),
  rule 3 (input-groups all-or-nothing), and rule 8 (dialogs are Actions / Start
  gating). It also extends the `BoolParam(requires=...)` conditional-role
  primitive rather than inventing a new gating mechanism. Framework rule 5/8
  point here.
- `[[gui-action-contract-exhaustiveness]]` — analysis dialogs are Actions;
  preset require/hide/overlay is dialog-local state, never a session write.
- `[[sibling-dialog-extract-shared-widget-2026-05-12]]` — preset gating rides
  on the shared `analysis_widgets.py` factories, not a rebuilt params dataclass.
- `[[qt-wire-user-edit-signals-2026-05-12]]` — the preset combo's `activated`
  (not a programmatic setter) drives the `_refresh_state` cascade.
