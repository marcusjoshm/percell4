---
title: "feat: Full cellpose settings in the Segment tab via a shared settings widget"
type: feat
status: active
date: 2026-06-03
---

# feat: Full cellpose settings in the Segment tab via a shared settings widget

## Overview

The Segment tab's "Cellpose" group (in `SegmentationPanel`) currently exposes only 4 controls
(Model, Diameter, Use GPU, "Remove edge cells"). The single-cell thresholding workflow setup
window (`WorkflowConfigDialog._build_cellpose_group`) exposes the full set: Model, Diameter,
Flow threshold, Cellprob threshold, Min cell size, Saturation, plus Use GPU. This plan brings
the Segment tab to parity for the cellpose-inference parameters by **extracting a shared
`CellposeSettingsForm` widget** that both surfaces consume, then **threading the newly-exposed
parameters through the Segment panel's run path** (saturation LUT and Gaussian-blur pre-processing,
flow/cellprob/min-size into `run_cellpose`, and an edge-margin into the existing edge filter).

This plan also adds **one new pre-Cellpose preprocessing knob to both surfaces**: a Gaussian
blur configured by `sigma`. It is a sibling of the existing saturation LUT — an in-memory
transformation applied to the segmentation channel before inference (never to on-disk
`/intensity`), engaged when `sigma > 0` and a no-op at `sigma == 0`. Because the shared widget
*is* a `CellposeSettings` editor, adding the blur means adding a `blur_sigma` field to the
dataclass, a `Sigma` row to the shared form, and a blur step in both run paths
(`phases.segment_one` for the workflow, `_on_run_cellpose` for the panel).

The eight shared controls map exactly to the (now eight-field) `CellposeSettings` frozen
dataclass, which makes that dataclass the natural contract for the shared widget. The remaining
controls shown in the reference image are **intentionally not replicated verbatim** in the panel,
per the decisions
below: the "Segmentation channel" picker is omitted (the panel reads `session.active_channel`
from the canonical `SessionWindow` Selector), the "Segmentation layer name" stays the panel's
existing run-time name prompt, and the three-way "Edge cells" dropdown stays the panel's existing
binary "Remove edge cells" checkbox plus a new "Edge margin (px)" spinbox.

---

## Problem Frame

A user segmenting in the Segment tab cannot tune flow threshold, cellprob threshold, min cell
size, or saturation — they are stuck with `run_cellpose`'s hardcoded defaults and no saturation
pre-processing at all. The same user running the full single-cell thresholding workflow *can*
tune all of these. The Segment tab is the interactive, one-shot segmentation surface; parity of
the inference knobs is the goal so users can dial in segmentation interactively before (or
instead of) committing to a batch workflow run.

Separately, **neither** surface offers a Gaussian blur. Noisy or speckled segmentation channels
can fragment Cellpose masks; a light blur (sigma ≈ 1–2 px) smooths shot noise so cell bodies
segment as single objects. This plan adds that knob to both surfaces at once — it is a new
capability, not a parity gap, and it slots cleanly alongside the existing saturation LUT as a
second in-memory pre-Cellpose transform.

The widget construction for these settings already exists, fully formed, in
`WorkflowConfigDialog._build_cellpose_group` (`src/percell4/gui/workflows/single_cell/config_dialog.py`,
lines ~425–561). Documented institutional learning
(`docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`)
directs that a second surface needing the same widget set should **extract a shared widget**
rather than rebuild it from the dataclass field values — because item lists, labels, default
selections, widget types, and tooltips carry intent the dataclass does not encode.

---

## Requirements Trace

- R1. The Segment tab's Cellpose group exposes Flow threshold, Cellprob threshold, Min cell size,
  Saturation, and **Gaussian blur sigma** controls in addition to the existing Model, Diameter,
  and Use GPU controls.
- R2. The newly-exposed values actually affect the Segment tab's cellpose run — they are not
  inert decoration. Flow/cellprob/min-size reach `run_cellpose`; saturation applies a LUT and the
  blur sigma applies a Gaussian filter to the input image before inference (mirroring
  `phases.segment_one`), in that order (saturation then blur).
- R8. A new `blur_sigma` field is added to `CellposeSettings` and a `Sigma` control to the shared
  form, so **both** the Segment panel and the batch workflow gain the Gaussian blur. `sigma == 0`
  is a no-op (current behavior preserved); the blur is applied to the in-memory Cellpose input
  only, never to on-disk `/intensity`.
- R3. The shared widget set is extracted once and consumed by **both** the Segment panel and the
  workflow config dialog, so the two surfaces cannot drift in items/defaults/widget-types.
- R4. The Segment panel does **not** introduce a second Selector for `active_channel` — channel
  selection remains owned by `SessionWindow`. No "Segmentation channel" combo that writes session.
- R5. The Segment panel gains an "Edge margin (px)" control that feeds the existing edge-cell
  filter; the existing "Remove edge cells" checkbox is preserved.
- R6. The workflow config dialog's behavior is unchanged by the **extraction** for the existing
  seven controls (same item lists, defaults, scroll behavior). The dialog additionally gains the
  new `Sigma` control by virtue of consuming the shared form — the *only* intended behavior change
  on that surface, and it flows through `build_config` into `WorkflowConfig.cellpose.blur_sigma`
  and is applied by `phases.segment_one` (R8).
- R7. The Selector/Creator/Action contract is preserved: the new controls are Action operands
  (read at run time, never write session); the "Run Cellpose" button remains a four-step Creator.

---

## Scope Boundaries

- **Not** replicating the "Segmentation channel" combo in the panel (decision: omit — see Key
  Technical Decisions). The panel keeps reading `session.active_channel`.
- **Not** replacing the panel's run-time `prompt_for_resource_name` naming with a persistent
  "Segmentation layer name" `QLineEdit`. Two naming surfaces would conflict.
- **Not** replacing the panel's binary "Remove edge cells" checkbox with the three-way `EdgeMode`
  dropdown. The cohort/size-normalized `EdgeMode` modes only have meaning at *measurement* time in
  the batch workflow; for a one-shot segment they are inert.
- **Not** adding persistence (QSettings) for cellpose parameters on either surface. Neither
  surface persists these today; values reset to defaults on open. Out of scope.
- **Not** changing the batch workflow's segmentation behavior **except** for the additive Gaussian
  blur step: `phases.segment_one` gains a `sigma > 0` blur applied after the saturation LUT (R8).
  With the default `blur_sigma=0.0` the batch path is byte-identical to today.

### Deferred to Follow-Up Work

- Whether the panel's "Min cell size" should *also* feed `SegmentCells.finalize(min_area=...)`
  (post-process small-cell filter) in addition to `run_cellpose(min_size=...)` — see Open
  Questions. Default in this plan: feed `run_cellpose` only, matching the workflow's mapping.

---

## Context & Research

### Relevant Code and Patterns

- **Source widget construction:** `src/percell4/gui/workflows/single_cell/config_dialog.py`
  — `_build_cellpose_group()` (lines ~425–561) builds all controls in a `QFormLayout`;
  `build_config()` (lines ~1755–1828) reads them into `CellposeSettings` + `WorkflowConfig`.
  Module constant `_CELLPOSE_MODELS = ("cpsam", "cyto3", "cyto2", "cyto", "nuclei")` (line ~85).
- **Target panel:** `src/percell4/gui/segmentation_panel.py` — `SegmentationPanel._build_ui`
  (`QGroupBox("Cellpose")` ~lines 104–141); `_on_run_cellpose` (line 389) reads model/diameter/
  gpu only and passes them to `run_cellpose` / `run_cellpose_stack`; `_on_cellpose_done`
  (line 486) delegates to `SegmentCells.finalize(remove_edge_cells=...)`.
- **Contract value object:** `src/percell4/workflows/models.py` — `CellposeSettings` frozen
  dataclass (line ~77): `model`, `diameter`, `gpu`, `flow_threshold`, `cellprob_threshold`,
  `min_size`, `saturation_pct`. This plan **adds an eighth field** `blur_sigma: float = 0.0`
  (validated `>= 0` in `__post_init__`, mirroring the `saturation_pct` invariant). Maps 1:1 to
  the eight shared controls.
- **Inference adapter (already supports the new params):** `src/percell4/adapters/cellpose.py`
  — `run_cellpose(..., flow_threshold=0.4, cellprob_threshold=0.0, min_size=15, model=None)`
  (line 53); `run_cellpose_stack` (line 126) forwards `**kwargs`.
- **Saturation pre-processing:** `src/percell4/domain/segmentation/preprocess.py`
  — `apply_saturation_lut(plane, saturation_pct)`. Applied by `phases.segment_one`
  (`src/percell4/workflows/phases.py`, the `_preprocess(plane)` closure ~lines 313–318) to the
  in-memory plane before inference; never to on-disk `/intensity`.
- **Gaussian blur pre-processing (NEW):** add `apply_gaussian_blur(plane, sigma)` to
  `src/percell4/domain/segmentation/preprocess.py` as a sibling of `apply_saturation_lut`.
  `sigma == 0` returns the plane unchanged (no-op, parallel to `saturation_pct == 0`); `sigma > 0`
  applies `scipy.ndimage.gaussian_filter` (already a project dep) preserving input dtype and
  shape. The workflow's `_preprocess` closure applies it **after** the saturation LUT; the panel
  run path mirrors the same order.
- **Edge filter:** `src/percell4/domain/segmentation/postprocess.py` —
  `filter_edge_cells(labels, edge_margin=0)` (line 44). Called by
  `SegmentCells._postprocess_frame` today as `filter_edge_cells(raw)` (margin 0).
- **Creator post-processing:** `src/percell4/application/use_cases/segment_cells.py` —
  `SegmentCells.finalize(raw_masks, min_area=15, remove_edge_cells=True, name=None, view_bin=1)`;
  owns store write + `refresh_resource_lists` + `set_active_segmentation` (Creator steps 1, 3, 4).
- **Precedent for an extracted shared form:** `src/percell4/gui/_stitching_flim_form.py`
  (`StitchingFlimForm`) — private-utility naming convention `_<name>_form.py`.

### Institutional Learnings

- `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
  — **directly governs this feature.** Extract-by-default; read `_build_cellpose_group`
  end-to-end (every `addItem`, label, `itemData`, default `setValue`/`setCurrentIndex`,
  tooltip, widget type) and preserve it in the shared widget; do not reconstruct from dataclass
  fields. If duplicating instead, snapshot-test both sites.
- `docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md` (**superseded**)
  — the per-panel channel-override combo was retired; the inline panel reads
  `session.active_channel` directly. **Do not** add a channel combo that writes session
  (forbidden second Selector). Backs decision R4.
- `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md` (pre_canonical)
  — the new config widgets are **Action operands**. Add them as Action entries to
  `docs/audits/gui-element-classification.yaml` (existing cellpose widgets are classified there
  at lines ~944–976).
- `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md`
  (canonical; `applies_to` includes `src/percell4/gui/**/*.py` and `segment_cells.py`) —
  the "Run Cellpose" Creator must keep all four steps. `SegmentCells.finalize` owns steps 1/3/4;
  the panel owns step 2 (`viewer.add_labels`). Don't break this when threading new params.
- `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` (canonical) — if the refactor changes the
  workflow dialog's height, keep `wrap_in_scroll`/`cap_to_screen` compliance
  (test: `tests/test_gui/test_dialog_helper_compliance.py`).
- `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md` — wire user-edit signals
  only if something reads derived state. Both surfaces read these widgets at run/accept time
  (pull-style), so no live `changed` consumer exists; a `changed` signal is **not** required.

### External References

- None required. The cellpose adapter, saturation LUT, and edge filter already exist and are
  well-patterned locally; no external research warranted.

---

## Key Technical Decisions

- **Shared widget covers exactly the eight `CellposeSettings` fields.** The genuinely-shared,
  drift-prone surface is Model/Diameter/GPU/Flow/Cellprob/Min-size/Saturation/**Sigma** — which
  map 1:1 to `CellposeSettings`. The divergent controls (seg-channel, seg-name, edge-mode/margin)
  stay per-surface by design, so they are not part of the shared widget. This keeps the shared
  widget cohesive (it *is* a `CellposeSettings` editor) and the workflow-dialog refactor minimal
  (swap a contiguous block of form rows; keep the bespoke rows around it).
- **Gaussian blur is a `CellposeSettings` field, not a per-surface control.** Because the blur
  must reach both the panel run path and the batch `phases.segment_one`, it belongs in the shared
  contract alongside `saturation_pct`. New field `blur_sigma: float = 0.0` (px). Default `0.0`
  keeps every existing run byte-identical. The `Sigma` form row is a `QDoubleSpinBox`
  (range 0–20, step 0.5, 1 decimal, suffix " px", tooltip explaining it smooths shot noise and is
  applied to the Cellpose input only). Applied **after** the saturation LUT in both run paths — a
  contrast stretch first clips hot-pixel outliers, then the blur smooths; both are in-memory and
  per-frame for `(T, H, W)` stacks.
- **Standardize on `QDoubleSpinBox` for Diameter.** The workflow already uses `QDoubleSpinBox`
  (range 0–1000, `0 = auto-detect`); the panel's current `QSpinBox` with "Auto" special-value
  text is replaced. `0` still means auto in the run path (`diameter = value if value > 0 else None`).
- **Each surface seeds its own defaults via the shared form's constructor.** The form takes an
  `initial: CellposeSettings` and applies it. The workflow seeds `diameter=300.0` (preserving its
  current shown default); the panel also seeds `diameter=300.0` for parity with the reference
  window (a behavior change from the panel's current `30` — accepted for parity).
- **Omit the "Segmentation channel" combo in the panel** (R4). The panel reads
  `session.active_channel`; a writing combo would be a forbidden second Selector.
- **Keep the panel's binary edge handling**, add an "Edge margin (px)" spinbox feeding
  `filter_edge_cells(edge_margin=...)`. The three-way `EdgeMode` dropdown's cohort modes are
  inert outside the batch measure phase.
- **No `changed` signal on the shared form.** Both consumers read on action/accept; YAGNI.

---

## Open Questions

### Resolved During Planning

- *Should the panel get a "Segmentation channel" picker?* No — omit (R4, superseded-pattern
  learning). User-confirmed.
- *Shared widget vs. duplicate?* Extract a shared widget. User-confirmed.
- *Full `EdgeMode` dropdown vs. margin-only?* Margin-only + keep checkbox. User-confirmed.

### Deferred to Implementation

- **Panel "Edge margin (px)" default value.** Plan default: `0` (preserves the panel's current
  strict-border `filter_edge_cells` behavior). The reference image shows `100`; if parity is
  preferred over behavior preservation, seed `100`. Confirm at implementation; trivially changed.
- **Does "Min cell size" also feed `finalize(min_area=...)`?** Plan default: it feeds
  `run_cellpose(min_size=...)` only, matching the workflow's mapping. The panel's
  `finalize` keeps its `min_area=15` default. Revisit if double-filter semantics are desired.
- **`Sigma` spinbox range / step.** Plan default: range `0–20` px, step `0.5`, 1 decimal,
  default `0.0` (no blur). These mirror the saturation spinbox's shape; trivially adjusted if a
  wider range is wanted. The `CellposeSettings.__post_init__` validates `blur_sigma >= 0`.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not
> implementation specification. The implementing agent should treat it as context, not code
> to reproduce.*

```
                       src/percell4/gui/_cellpose_settings_form.py   (NEW)
                       ┌──────────────────────────────────────────┐
                       │ CellposeSettingsForm(QWidget)            │
                       │  rows: Model, Diameter, GPU, Flow,       │
                       │        Cellprob, Min size, Saturation,   │
                       │        Sigma (NEW)                       │
                       │  __init__(initial: CellposeSettings)     │
                       │  settings() -> CellposeSettings          │
                       └──────────────┬───────────────┬───────────┘
                                      │ consumed by   │ consumed by
                ┌─────────────────────┘               └───────────────────────┐
                ▼                                                              ▼
  config_dialog.py  _build_cellpose_group()                  segmentation_panel.py  QGroupBox("Cellpose")
  ┌───────────────────────────────────┐                      ┌──────────────────────────────────────┐
  │ seg-channel combo (kept)          │                      │ [CellposeSettingsForm]                │
  │ [CellposeSettingsForm]            │                      │ Use GPU is inside the form            │
  │ seg-name QLineEdit (kept)         │                      │ "Remove edge cells" checkbox (kept)   │
  │ EdgeMode dropdown + margin (kept) │                      │ "Edge margin (px)" spinbox  (NEW)     │
  │ build_config(): form.settings()   │                      │ "Run Cellpose" (Creator, unchanged    │
  │   → CellposeSettings (unchanged)  │                      │   four-step contract)                 │
  └───────────────────────────────────┘                      └──────────────────────────────────────┘

  Panel run path (_on_run_cellpose / _on_cellpose_done):
    s = form.settings()
    image = apply_saturation_lut(active_layer.data, s.saturation_pct)   # if saturation_pct > 0
    image = apply_gaussian_blur(image, s.blur_sigma)                    # if blur_sigma > 0 (NEW)
    run_cellpose[_stack](image, model_type=s.model, diameter=s.diameter or None, gpu=s.gpu,
                         flow_threshold=s.flow_threshold,
                         cellprob_threshold=s.cellprob_threshold, min_size=s.min_size)
    ...
    finalize(masks, remove_edge_cells=checkbox, edge_margin=margin_spin.value(), ...)

  Batch path (phases.segment_one._preprocess, per-frame):
    plane = apply_saturation_lut(plane, cfg.saturation_pct)  # if saturation_pct > 0
    plane = apply_gaussian_blur(plane, cfg.blur_sigma)       # if blur_sigma > 0 (NEW)
```

---

## Implementation Units

- U0. **Add `blur_sigma` to `CellposeSettings`, an `apply_gaussian_blur` helper, and wire it into
  the batch `phases.segment_one` path**

**Goal:** Establish the shared contract field and the domain helper, and make the Gaussian blur
real on the batch workflow side — so the form (U1) and both run paths have something to bind to.

**Requirements:** R8, R2 (batch half), R6 (the workflow gains the knob).

**Dependencies:** None.

**Files:**
- Modify: `src/percell4/workflows/models.py` (`CellposeSettings`)
- Modify: `src/percell4/domain/segmentation/preprocess.py` (`apply_gaussian_blur`)
- Modify: `src/percell4/workflows/phases.py` (`segment_one._preprocess`)
- Test: `tests/test_domain/test_preprocess.py` *(match the repo's domain-test location)*,
  `tests/test_workflows/test_models.py` *(extend if present)*

**Approach:**
- Add `blur_sigma: float = 0.0` to `CellposeSettings` (after `saturation_pct`). Validate
  `blur_sigma >= 0` in `__post_init__`, mirroring the `saturation_pct` guard. Add a field comment
  noting it is an in-memory Cellpose-input preprocessor (never modifies `/intensity`), `0.0`
  disables it.
- Add `apply_gaussian_blur(channel: NDArray, sigma: float) -> NDArray` to `preprocess.py` next to
  `apply_saturation_lut`. Contract: `sigma == 0.0` returns `channel` unchanged (no-op);
  `sigma < 0` raises `ValueError`; `sigma > 0` returns `scipy.ndimage.gaussian_filter(channel,
  sigma)` cast back to the input dtype (`gaussian_filter` preserves dtype for integer input via
  rounding — verify and cast explicitly to be safe). Same shape as input. Mirror the module's
  existing docstring style and the "apply per-frame for stacks" caller guidance.
- In `phases.segment_one`, extend the `_preprocess(plane)` closure to apply the blur **after** the
  saturation LUT: `plane = apply_saturation_lut(...)` then `if cfg.blur_sigma > 0: plane =
  apply_gaussian_blur(plane, cfg.blur_sigma)`. The closure is already called per-frame for stacks,
  so per-frame blur is automatic.

**Patterns to follow:**
- `apply_saturation_lut` in `preprocess.py` (no-op-at-zero contract, dtype preservation, docstring
  shape).
- The existing `_preprocess` closure in `phases.segment_one`.

**Test scenarios:**
- Happy path: `apply_gaussian_blur(img, 1.5)` returns a same-shape, same-dtype, visibly-smoothed
  array (assert variance decreases / equals `scipy.ndimage.gaussian_filter` reference).
- Edge case: `sigma == 0.0` returns the input unchanged (identity; ideally same object or
  array-equal).
- Edge case: `sigma < 0` raises `ValueError`.
- Contract: `CellposeSettings(blur_sigma=2.0)` constructs; `blur_sigma=-1` raises in
  `__post_init__`; default `CellposeSettings().blur_sigma == 0.0`.
- Integration: `phases.segment_one` with `blur_sigma > 0` calls `apply_gaussian_blur` per frame
  after the saturation LUT (spy); with `blur_sigma == 0` it is not called (byte-identical to
  today).

**Verification:** A batch run with `blur_sigma > 0` feeds a blurred plane to Cellpose; with the
default `0.0` the batch path is unchanged.

---

- U1. **Extract `CellposeSettingsForm` shared widget**

**Goal:** Create a reusable `QWidget` that renders the eight `CellposeSettings` controls (the
seven existing workflow controls plus the new `Sigma` row) and exposes
`settings() -> CellposeSettings`.

**Requirements:** R3 (partial), R1 (provides the controls), R8 (the `Sigma` row).

**Dependencies:** U0 (the `blur_sigma` field must exist on `CellposeSettings`).

**Files:**
- Create: `src/percell4/gui/_cellpose_settings_form.py`
- Modify: `src/percell4/workflows/models.py` *(only if exposing `_CELLPOSE_MODELS` as a shared
  constant lives better here; otherwise define the shared constant in the new form module and
  have `config_dialog.py` import it — implementer's call)*
- Test: `tests/test_gui/test_cellpose_settings_form.py`

**Approach:**
- Mirror `_build_cellpose_group`'s seven existing rows verbatim (widget types, ranges, steps,
  decimals, tooltips, suffix " %" on saturation, Model item list = `_CELLPOSE_MODELS`). Read that
  method end-to-end before writing (per the extract-shared-widget learning).
- Add the new `Sigma` row: `QDoubleSpinBox`, range `0–20`, step `0.5`, 1 decimal, suffix " px",
  tooltip explaining it smooths shot noise (Gaussian blur) on the Cellpose input only; `0`
  disables it. Seed from `initial.blur_sigma`.
- Constructor `__init__(self, initial: CellposeSettings = CellposeSettings(), parent=None)` seeds
  every widget from `initial`. Standardize Diameter on `QDoubleSpinBox` (range 0–1000, `0 = auto`).
- `settings()` reads the widgets and returns a `CellposeSettings(...)` (lets the dataclass'
  `__post_init__` validate invariants).
- Use `QFormLayout`; import colors from `theme.py` if any styling is needed (no hardcoded hex).
- No `changed` signal (no live consumer).

**Patterns to follow:**
- `src/percell4/gui/_stitching_flim_form.py` (`StitchingFlimForm`) for the extracted-form shape
  and `_<name>_form.py` naming.
- `_build_cellpose_group` in `config_dialog.py` for exact widget construction.

**Test scenarios:**
- Happy path: construct with default `CellposeSettings()`, `settings()` returns a value equal to
  the defaults (model `cpsam`, flow `0.4`, cellprob `0.0`, min_size `15`, saturation `1.0`,
  blur_sigma `0.0`).
- Happy path: construct with `CellposeSettings(diameter=300.0, flow_threshold=0.7, min_size=40,
  blur_sigma=1.5)`; widgets reflect those; `settings()` round-trips them back.
- Edge case: Model combo items equal `_CELLPOSE_MODELS` in order; default selected text matches
  `initial.model`.
- Edge case: Diameter widget is a `QDoubleSpinBox`; saturation spinbox shows the " %" suffix and
  1-decimal precision; the Sigma spinbox shows the " px" suffix and 1-decimal precision.
- Edge case: setting saturation to `0.0` is accepted and returned (boundary; means "no LUT").
- Edge case: setting Sigma to `0.0` is accepted and returned (boundary; means "no blur").

**Verification:** A `CellposeSettingsForm` can be constructed standalone, displays the eight
controls (including Sigma), and round-trips a `CellposeSettings` through `settings()`.

---

- U2. **Refactor `WorkflowConfigDialog` to consume the shared form**

**Goal:** Replace the seven inline cellpose rows in `_build_cellpose_group` with a
`CellposeSettingsForm` instance, keeping the seg-channel / seg-name / edge-mode / edge-margin
rows around it. `build_config()` reads cellpose values from `form.settings()`. The only behavior
change is the new `Sigma` row the form contributes (R8) — it flows into
`WorkflowConfig.cellpose.blur_sigma` automatically via `form.settings()`; everything else is a
pure refactor.

**Requirements:** R3, R6, R8 (the dialog surfaces the new control).

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py`
- Test: `tests/test_gui/test_workflow_config_dialog.py` *(extend existing test module if present;
  otherwise add focused coverage here)*

**Approach:**
- Construct `self._cp_form = CellposeSettingsForm(initial=CellposeSettings(diameter=300.0))`
  to preserve the dialog's current 300.0 default, and insert it where the seven rows were.
- Keep `self._cp_seg_channel`, `self._cp_seg_name`, `self._edge_mode`, `self._edge_margin` exactly
  as-is (these are the divergent, surface-specific controls).
- In `build_config()`, replace the seven per-widget reads with
  `cellpose = self._cp_form.settings()` and fold it into `WorkflowConfig` unchanged.
- Remove the now-dead `self._cp_model/_cp_diameter/_cp_gpu/_cp_flow/_cp_cellprob/_cp_min_size/
  _cp_saturation` attributes and the local `_CELLPOSE_MODELS` duplication if it is now imported
  from the shared location.
- Preserve `dialog-scroll-when-tall` compliance — the dialog's overall height should not regress;
  if it changes, keep `wrap_in_scroll`/`cap_to_screen` correct.

**Execution note:** Characterization-first — capture the `CellposeSettings` (and `WorkflowConfig`
cellpose fields) that `build_config()` produces for default widget state *before* refactoring, then
assert it is byte-for-byte identical after. This is the guardrail against silent drift.

**Patterns to follow:**
- The existing `build_config()` assembly; the existing scroll-wrapping in the dialog.

**Test scenarios:**
- Covers R6. Regression: with default widget state, `build_config()` returns a `WorkflowConfig`
  whose `.cellpose` equals `CellposeSettings(model="cpsam", diameter=300.0, gpu=True,
  flow_threshold=0.4, cellprob_threshold=0.0, min_size=15, saturation_pct=1.0, blur_sigma=0.0)` —
  i.e. the pre-refactor defaults plus the new `blur_sigma=0.0` (which keeps batch runs identical).
- Happy path: changing the form's Flow/Min-size/**Sigma** before `build_config()` is reflected in
  the resulting `WorkflowConfig.cellpose`.
- Integration: seg-channel / edge-mode / seg-name / edge-margin still flow into `WorkflowConfig`
  unchanged (they were not touched).
- Edge case: dialog still wraps in a scroll area / caps to screen (assert via the existing
  `test_dialog_helper_compliance` mechanism if applicable).

**Verification:** The workflow dialog renders identically and produces an identical
`WorkflowConfig` for any given widget state; existing workflow tests pass.

---

- U3. **Add the shared form + Edge margin to the Segment panel's Cellpose group**

**Goal:** Replace the panel's four inline cellpose controls with a `CellposeSettingsForm`, keep
the "Remove edge cells" checkbox, add an "Edge margin (px)" spinbox, and omit any channel picker.

**Requirements:** R1, R3, R4, R5, R7 (Action-operand classification).

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/gui/segmentation_panel.py`
- Test: `tests/test_gui/test_segmentation_panel.py` *(extend if present; otherwise add)*

**Approach:**
- In `_build_ui`, replace `self._cp_model` / `self._cp_diameter` / `self._cp_gpu` with
  `self._cp_form = CellposeSettingsForm(initial=CellposeSettings(diameter=300.0))`.
- Keep `self._cp_remove_edges` ("Remove edge cells") checkbox.
- Add `self._cp_edge_margin` (`QSpinBox`, range 0–500, default per Open Questions — `0`).
- Do **not** add a "Segmentation channel" combo; `_on_run_cellpose` continues to read
  `session.active_channel` (lines 393–398). Do not add a persistent seg-name field; keep
  `prompt_for_resource_name`.
- Keep the "Run Cellpose" button and its four-step Creator contract intact (this unit is
  layout/widget only; run-path wiring is U4/U5).

**Patterns to follow:**
- Existing `QGroupBox("Cellpose")` construction in `_build_ui`; `theme.py` for any styling.

**Test scenarios:**
- Happy path: the Cellpose group contains a `CellposeSettingsForm`; `panel._cp_form.settings()`
  returns a `CellposeSettings`.
- Edge case (R4): the panel exposes no widget that writes `session.active_channel` — assert no
  channel combo is present in the Cellpose group.
- Edge case (R5): "Remove edge cells" checkbox still present and checked by default; "Edge margin
  (px)" spinbox present with range 0–500.

**Verification:** The Segment tab's Cellpose group visually matches the inference-parameter set of
the workflow window (minus the intentionally-omitted controls) and constructs without error.

---

- U4. **Thread inference params + saturation LUT + Gaussian blur through the panel run path**

**Goal:** Make the newly-exposed values actually affect segmentation: pass flow/cellprob/min-size
to `run_cellpose`/`run_cellpose_stack`, and apply the saturation LUT then the Gaussian blur to the
input image before inference.

**Requirements:** R2, R8 (panel half).

**Dependencies:** U3, U0 (the `apply_gaussian_blur` helper).

**Files:**
- Modify: `src/percell4/gui/segmentation_panel.py` (`_on_run_cellpose`)
- Test: `tests/test_gui/test_segmentation_panel.py`

**Approach:**
- In `_on_run_cellpose`, replace the three inline reads with `s = self._cp_form.settings()`.
- Compute `diameter = s.diameter if s.diameter > 0 else None` (preserve auto semantics).
- Apply saturation then blur before inference, mirroring `phases.segment_one`:
  `image = apply_saturation_lut(np.asarray(active_layer.data), s.saturation_pct)` when
  `s.saturation_pct > 0`; then `image = apply_gaussian_blur(image, s.blur_sigma)` when
  `s.blur_sigma > 0`; otherwise pass the data through unchanged. **Apply to the in-memory image
  only** — never write back to `/intensity`. For the `(T, H, W)` stack path, apply both per-frame
  (match `phases.segment_one`'s exact per-plane call shape; verify before use).
- Pass `flow_threshold=s.flow_threshold, cellprob_threshold=s.cellprob_threshold,
  min_size=s.min_size` to both `run_cellpose` and `run_cellpose_stack` (the stack variant forwards
  `**kwargs`).

**Execution note:** Worth a failing test first asserting the kwargs reach `run_cellpose` — the
whole point of the feature is that these stop being inert.

**Patterns to follow:**
- `src/percell4/workflows/phases.py` `segment_one` for the saturation-then-infer ordering and the
  exact `apply_saturation_lut` call.
- The existing `Worker(run_cellpose, ...)` / `Worker(run_cellpose_stack, ...)` dispatch.

**Test scenarios:**
- Happy path: with the form set to non-default flow/cellprob/min-size, `run_cellpose` is invoked
  with exactly those kwargs (spy/mock the adapter; assert call args).
- Happy path: with `saturation_pct > 0`, `apply_saturation_lut` is called on the input image
  before inference, with the form's saturation value.
- Edge case: with `saturation_pct == 0`, `apply_saturation_lut` is **not** called and the raw
  layer data is passed through.
- Happy path: with `blur_sigma > 0`, `apply_gaussian_blur` is called with the form's sigma,
  **after** `apply_saturation_lut`, on the image fed to inference.
- Edge case: with `blur_sigma == 0`, `apply_gaussian_blur` is **not** called.
- Integration: a `(T, H, W)` layer routes to `run_cellpose_stack` and the same flow/cellprob/
  min-size kwargs (and per-frame saturation **and** per-frame blur) are forwarded.
- Edge case: `diameter == 0` is passed to the adapter as `None` (auto), not `0`.

**Verification:** Changing flow/cellprob/min-size/saturation/sigma in the panel and running
Cellpose produces a different segmentation result than the defaults would; the adapter receives
the values.

---

- U5. **Thread edge margin through `SegmentCells.finalize` → `filter_edge_cells`**

**Goal:** The panel's new "Edge margin (px)" value reaches the edge filter when "Remove edge
cells" is checked.

**Requirements:** R5, R7 (Creator contract preserved).

**Dependencies:** U3.

**Files:**
- Modify: `src/percell4/application/use_cases/segment_cells.py`
  (`finalize` + `_postprocess_frame`)
- Modify: `src/percell4/gui/segmentation_panel.py` (`_on_cellpose_done`)
- Test: `tests/test_use_cases/test_segment_cells.py` *(match the repo's use-case test location)*

**Approach:**
- Add an `edge_margin: int = 0` parameter to `SegmentCells.finalize` and thread it into
  `_postprocess_frame(raw, min_area, remove_edge_cells, edge_margin)`, which calls
  `filter_edge_cells(raw, edge_margin=edge_margin)` when `remove_edge_cells` is true.
- Default `0` preserves the current strict-border behavior for all existing callers (workflow
  runners, tests) that do not pass it.
- In `_on_cellpose_done`, pass `edge_margin=self._cp_edge_margin.value()`.
- Do not alter the four-step Creator sequence: `finalize` still does write_labels →
  refresh_resource_lists → set_active_segmentation; the panel still does `viewer.add_labels`.

**Patterns to follow:**
- `filter_edge_cells(labels, edge_margin=0)` in `domain/segmentation/postprocess.py`.
- The existing `finalize` signature/defaulting style (additive, backward-compatible kwargs).

**Test scenarios:**
- Happy path: `finalize(raw, remove_edge_cells=True, edge_margin=20)` calls `filter_edge_cells`
  with `edge_margin=20` (spy) and removes cells within 20 px of the border.
- Edge case: `edge_margin=0` (default) reproduces current behavior exactly (existing tests pass
  unchanged).
- Edge case: `remove_edge_cells=False` ignores `edge_margin` (no edge filtering regardless).
- Integration: a `(T, H, W)` raw-mask stack applies the margin per-frame in `_postprocess_frame`.
- Integration: after `finalize`, the segmentation is written and auto-selected (four-step Creator
  contract intact) — assert `set_active_segmentation` was called with the written name.

**Verification:** Checking "Remove edge cells" with a non-zero margin removes near-border cells in
the resulting segmentation; existing callers are unaffected.

---

- U6. **Update GUI-classification audit and per-module docs**

**Goal:** Keep the living audits and module docs consistent with the new widgets.

**Requirements:** R7 (classification accuracy).

**Dependencies:** U0, U3, U4, U5.

**Files:**
- Modify: `docs/audits/gui-element-classification.yaml`
- Modify: `src/percell4/gui/CLAUDE.md` (the `segmentation_panel.py` bullet)
- Modify: `src/percell4/workflows/CLAUDE.md` (the `models.py` bullet — `CellposeSettings` now
  carries `blur_sigma`) and `src/percell4/domain/segmentation/`'s module doc if present (the new
  `apply_gaussian_blur` helper)

**Approach:**
- Add Action entries for the new panel controls (Flow, Cellprob, Min size, Saturation, Sigma, Edge
  margin) alongside the existing cellpose Action entries (~lines 944–976), each noted as an
  "Operand for Run Cellpose". The `CellposeSettingsForm`'s controls are Action operands; the
  "Remove edge cells" checkbox classification is unchanged.
- Update `gui/CLAUDE.md`'s `segmentation_panel.py` description to mention the shared
  `CellposeSettingsForm` and the edge-margin control (current-state only, no history).

**Test scenarios:**
- Test expectation: none — documentation and audit-metadata changes only, no behavioral change.

**Verification:** `python3 scripts/learnings_applicability.py
src/percell4/gui/segmentation_panel.py` still resolves cleanly; the classification YAML lists the
new operands; module docs describe current state.

---

## System-Wide Impact

- **Interaction graph:** Two consumers of the new `CellposeSettingsForm` (the workflow dialog and
  the Segment panel). The workflow dialog's `build_config` and the panel's `_on_run_cellpose` are
  the read sites. No new cross-window signal wiring; the form is a pull-style operand.
- **Error propagation:** `CellposeSettings.__post_init__` validates invariants (diameter ≥ 0,
  min_size ≥ 0, saturation ∈ [0, 50], blur_sigma ≥ 0); `settings()` will raise on out-of-range
  input. Widget ranges already constrain inputs, so this is a backstop, not a user-facing error
  path.
- **State lifecycle risks:** None new. The panel's worker-time bin/name capture and the four-step
  Creator write are unchanged; U5 is an additive, default-preserving kwarg.
- **API surface parity:** The shared form is the parity mechanism (R3). The intentionally-divergent
  controls (seg-channel, seg-name, edge-mode) are documented non-goals, not accidental gaps.
- **Integration coverage:** U4's saturation/stack path and U5's per-frame margin are the
  cross-layer behaviors that unit mocks alone won't fully prove — covered by the integration
  scenarios in those units.
- **Unchanged invariants:** `run_cellpose`/`run_cellpose_stack` signatures and the
  `SessionWindow`-as-sole-channel-Selector rule are unchanged. `CellposeSettings` gains one
  back-compatible field (`blur_sigma=0.0`) and `phases.segment_one` gains one additive,
  default-off blur step — both byte-identical to today at the default. `finalize`'s existing
  callers keep working via the `edge_margin=0` default.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Extraction silently changes the workflow dialog's defaults/items (drift). | U2 characterization test asserts `build_config()` produces an identical `CellposeSettings` for default state before/after; read `_build_cellpose_group` verbatim before extracting. |
| Saturation LUT / Gaussian blur accidentally applied to on-disk `/intensity`. | Both are applied only to the in-memory `active_layer.data` copy passed to the worker; mirror `phases.segment_one` exactly; U4 tests assert the raw path when `saturation_pct == 0` / `blur_sigma == 0`. |
| `apply_gaussian_blur` corrupts dtype/range (e.g. float blur of a uint16 plane fed to Cellpose). | Helper casts back to the input dtype; U0 test asserts same-shape/same-dtype output and `sigma == 0` identity; default `0.0` keeps every current run untouched. |
| Saturation-then-blur ordering chosen without evidence it beats blur-then-saturation. | Documented decision (clip hot-pixel outliers before smoothing); identical helpers make the order trivially swappable; default-off means no current run is affected. |
| Panel diameter default change (30 → 300) surprises users mid-workflow. | Documented decision for parity; trivially revertible by changing the panel's `initial` seed. |
| `apply_saturation_lut` 2D-vs-3D call shape differs from how the panel feeds stacks. | Verify the exact `phases.segment_one` call shape (per-plane) before wiring; U4 stack integration test covers per-frame application. |
| Edge-margin default (0 vs 100) ambiguity. | Defaulted to `0` (behavior-preserving) with the parity alternative documented in Open Questions; confirm at implementation. |

---

## Documentation / Operational Notes

- Per-module CLAUDE.md updates are part of U6 (current-state only, per the project's
  documentation rules — no plans, no history).
- No rollout, migration, or monitoring concerns; this is interactive GUI behavior with no
  persisted-data or schema impact.

---

## Sources & References

- Reference UI: the "Cellpose Settings (applied to every dataset)" group from the single-cell
  thresholding workflow setup window (`WorkflowConfigDialog`).
- Related code: `src/percell4/gui/workflows/single_cell/config_dialog.py`
  (`_build_cellpose_group`, `build_config`); `src/percell4/gui/segmentation_panel.py`;
  `src/percell4/workflows/models.py` (`CellposeSettings`); `src/percell4/adapters/cellpose.py`;
  `src/percell4/domain/segmentation/preprocess.py` (`apply_saturation_lut`, new
  `apply_gaussian_blur`); `src/percell4/workflows/phases.py` (`segment_one._preprocess`);
  `src/percell4/domain/segmentation/postprocess.py` (`filter_edge_cells`);
  `src/percell4/application/use_cases/segment_cells.py` (`SegmentCells.finalize`).
- Learnings: `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`,
  `docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md` (superseded),
  `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md`,
  `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`,
  `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`,
  `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`.
- Audit: `docs/audits/gui-element-classification.yaml`, `docs/audits/canonical-sources-matrix.yaml`.
