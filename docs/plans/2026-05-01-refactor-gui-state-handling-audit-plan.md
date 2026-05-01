---
title: "refactor: Audit and repair GUI state handling — Selector/Creator/Action compliance and anchor-bug retirement"
type: refactor
status: active
date: 2026-05-01
origin: docs/brainstorms/2026-05-01-gui-state-handling-audit-requirements.md
---

# refactor: Audit and repair GUI state handling

## Overview

Operationalize the Selector / Creator / Action taxonomy and five invariants from
the requirements doc as four living artifacts under `docs/audits/`, retire two
anchor bugs (Phasor Remove silently corrupting `active_mask`; `M` keystroke
shadowed by napari), and decouple napari's layer-list click from PerCell4's
session state so napari's event loop can no longer write domain state.

The audit is the *means*; the goal is a UI where state ownership is explicit,
silent failures are forbidden, and napari's event timing cannot rewrite session
selection.

---

## Problem Frame

Two recent reproducible bugs (preserved in the origin doc as Anchor Bug A and
Anchor Bug B) share the same structural shape: non-Selector UI elements mutate
session state outside their advertised contract, and failure modes are silent.
The codebase has accumulated four classes of related drift:

1. **I1 violations** — Action buttons writing `session.active_*`. Bug A's
   Phasor Remove (`src/percell4/interfaces/gui/peer_views/phasor_plot.py:360`)
   is the smoking gun; channel-delete (`src/percell4/interfaces/gui/task_panels/data_panel.py:545-546`)
   and the napari → session sync (`src/percell4/interfaces/gui/main_window.py:602-604, 926-969`)
   are additional candidates the audit will surface.
2. **Subscriber-side staleness** — Selected ROI panel and status bar in the
   phasor plot retain post-removal data because their rebind paths are
   incomplete.
3. **Keystroke shadowing** — PerCell4 has zero `viewer.bind_key` registrations.
   `M` is bound as a window-scoped `QAction` on the launcher; napari's native
   Labels-layer `M` wins when the napari viewer has focus.
4. **napari event-loop coupling** — `_sync_active_layers_from_viewer` writes
   `session.active_mask` / `active_segmentation` from napari's
   `viewer.layers.selection.events.active`. This is the structural cause of
   "napari side-effects rewrite domain state" bug class.

The plan addresses these in order: build the audit artifacts (which surface
exactly what the codebase does today), write regression tests for the anchor
bugs (red), then apply the fixes (green).

---

## Requirements Trace

- R1. Establish a single explicit rule for who may mutate
  `session.active_channel | active_segmentation | active_mask | filter_ids |
  selection`. *(origin: requirements doc, Goals)*
- R2. Classify every interactive UI element so the rule can be mechanically
  audited. *(origin: Goals; Audit Scope deliverable §1)*
- R3. Eliminate silent failure modes — every refused or no-op action must be
  legible to the user. *(origin: I3, I4)*
- R4. Eliminate causally opaque side effects — buttons do what their labels
  say. *(origin: I2, I5)*
- R5. Produce living artifacts in `docs/audits/` that future GUI work
  continues to satisfy. *(origin: Audit Scope deliverable §1–4)*
- R6. Anchor Bug A (Phasor Remove corrupts active_mask) is no longer
  reproducible and has a regression test. *(origin: Success Criteria)*
- R7. Anchor Bug B (`M` shadowed by napari) is no longer reproducible and has
  a regression test. *(origin: Success Criteria)*
- R8. napari's event loop cannot write `session.active_*` (resolves OQ-1
  per user's stated goal: "avoid issues related to napari that could
  potentially conflict with PerCell4 domain states").

**Origin actors:** end users running PerCell4 interactively; future contributors
auditing or extending the GUI; CI for the regression tests.

**Origin acceptance examples:**
- AE1: After Load Dataset → FLIM → Compute Phasor → Apply Wavelet → Filter by
  active mask → Load ROI → Remove, the `Filter by active mask` checkbox
  remains checked, the histogram remains mask-filtered, and the Selected ROI
  panel + status bar are cleared. *(origin Bug A)*
- AE2: After Load Dataset, pressing `M` on the napari viewer immediately
  opens the Multi-select dialog. The native napari "next-label" behavior
  does not fire. *(origin Bug B)*

---

## Scope Boundaries

- Implementing per-module Selector dropdowns for segmentation/mask is **not** in
  this plan. Audit documents the Data-tab combo pattern (`task_panels/data_panel.py:176-186`,
  `task_panels/data_panel.py:198-204`) as the template for future Selectors;
  no new ones are added here. *(origin OQ-3)*
- Refactoring `CellDataModel` / `Session` shape, consolidating the dual event
  systems, or adding new fields beyond what `StateChange` already tracks is
  **not** in scope. The bridge stays. *(origin: Non-goals)*
- Domain-logic correctness (FLIM math, segmentation quality) is **not** in
  scope. *(origin)*
- Visual design / theming is **not** in scope. *(origin)*
- CLI surfaces (`src/percell4/interfaces/cli/`) are **not** in scope. The audit
  is GUI-only.
- Plugin-author-facing rules are **not** in scope (`src/percell4/plugins/`
  is empty).

### Deferred to Follow-Up Work

- Address remaining low-priority drift surfaced during inventory but not
  blocking the anchor bugs (channel-delete classification, filter-button
  reclassification): file as `todos/` per existing repo convention.
- Promotion of new conventions to `docs/solutions/` canonical-source entries:
  filed as U13 below; runs after the audit is otherwise complete.

---

## Context & Research

### Relevant Code and Patterns

- **Session as the single mutator.** `src/percell4/application/session.py:126-220`
  — `set_dataset`, `set_filter`, `set_active_segmentation`, `set_active_mask`,
  `set_active_channel`, `clear`, `set_measurements`. CellDataModel
  (`src/percell4/model.py:135-152`) is a Qt facade that delegates to Session and
  re-emits as `StateChange`. The dual system is documented as transitional in
  `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`.
- **Per-slot emission rule (2026-04-30 companion rule).** Every `Session.set_*`
  method that clears multiple slots must emit per-slot events, not a coarse
  parent event. Documented at `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md:82`.
  This is the canonical Creator emission contract (resolves OQ-2).
- **Data-tab Selector pattern.** `src/percell4/interfaces/gui/task_panels/data_panel.py:72-98, 176-186`.
  Combo change → `session.set_active_*`. Subscriber rebind via `state_changed`
  → handler at `:198-204` uses `blockSignals(True/False)` around `setCurrentText`
  to prevent feedback loops. This is the canonical Selector pattern.
- **Identity-based ROI widget lookup.** `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`
  Pattern 3. Capture by widget identity, not list index, to avoid stale lambdas
  after Remove. Bug A's Selected ROI panel staleness is the same shape.
- **Per-ROI mask cache invalidation.** Same doc, Pattern 5. Invalidate on G/S
  change for all caches; on ROI move for the moved one. Bug A's "histogram
  still appears mask-filtered" is a missing invalidation.
- **Modal-tool overlay pattern.** `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md`.
  Documents `LauncherWindow.set_workflow_locked` / `is_workflow_locked` as the
  single tool-mode coordination primitive. Relevant for Bug B's precondition
  feedback under I4.
- **`_phasor_roi_preview` is canonical.** `src/percell4/gui/viewer.py:444-540`
  exposes `add/update/remove_staged_overlay`. Confirmed at
  `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md:60`.
  Bug A's contract part (b) calls this API directly rather than mutating the
  layer list.
- **Test patterns.**
  - Phasor regression test template: `tests/test_gui_workflows/test_phasor_mask_filter.py`
    (real `PhasorPlotWindow` + fake `Session` + MagicMock repo).
  - Multi-select regression test template: `tests/test_gui_workflows/test_multi_select_e2e.py`
    (real `Session` + `CellDataModel` + `ViewerWindow` with `napari.Viewer(show=False)`).

### Institutional Learnings

- **5-vector HDF5 staleness compound** (`docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`)
  — "*'had to run it multiple times' is the canonical signature of a stacked-
  cache bug, not a flaky test.*" Bug A's symptoms are stacked subscriber
  staleness; the regression test must verify each subscriber rebinds, not just
  one.
- **FLIM phasor cross-layer alignment** (`docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`)
  — distinguishes legitimate Creator cross-resource writes (decay → phasor
  invalidation) from forbidden Action cross-resource writes (Remove → mask).
  The audit's I2 codifies this distinction.
- **DirectLabelColormap re-entrancy** (`docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`)
  — "*Use `_is_originator` flag for re-entrancy protection, not event blockers.*"
  The session → napari one-way push (U9) uses this pattern.
- **`napari-modal-tool-overlay-pattern-2026-04-29.md` guidance #10** — the
  doc currently states `M` is "correct as window-scoped." OQ-1's resolution
  contradicts this guidance (the user *does* expect `M` to work while the
  napari viewer has focus). The plan updates this doc as part of U13.
- **`channel-deletion-permanence.md`** — defines the 7-step transactional
  pattern for permanent resource removal. Useful precedent for distinguishing
  Creator cleanup (legitimate session writes during deletion) from Action
  side-effects (forbidden under I1/I2).

### External References

- napari keymap chain order verified against
  `napari/utils/key_bindings.py:367-378` (`keymap_chain` ChainMap order:
  `[user_keymap, active_layer.keymap, active_layer.class_keymap,
  viewer.keymap, viewer.class_keymap]`).
- napari's `Labels` class binds `M` to `napari:new_label` via
  `napari/utils/shortcuts.py:43`, registered on `Labels.class_keymap`.
- Scene-canvas event injection recipe for tests verified against
  `napari/_tests/test_key_bindings.py:45`.
- Otherwise codebase-internal — napari's keymap behavior was observed
  (Labels-layer `M` = "next unused label" toast) and the chain order
  cross-checked with the source.

---

## Key Technical Decisions

- **OQ-1 — napari layer-list selection events are decoupled from session;
  napari canvas mouse callbacks remain the Selector for `session.selection`.**
  Forbidden: subscriptions to `viewer.layers.selection.events.*` that write
  `session.active_*`. Allowed: napari mouse-callback handlers (already
  registered on the canvas, not on the layer-list widget) that write
  `session.selection` when the user clicks a cell — that is the user genuinely
  picking, not the napari event loop reflecting layer-list state. Allowed:
  PerCell4-controlled writes *to* napari (`viewer.layers.selection.active =
  layer`), guarded by `_is_originator`-style re-entrancy flags. *Rationale:*
  matches user goal of minimizing napari-event-loop coupling to domain state
  while preserving user-initiated picking; converts the existing two-way
  subscription into one PerCell4-controlled emit.
- **OQ-2 — Creator emission contract.** Creators emit per-slot events for
  every slot they clear or set. Already documented in
  `session-bridge-event-forwarding.md:82`; the audit codifies it without
  changing it.
- **OQ-3 — per-module Selectors deferred.** No new dropdowns in this plan.
  Audit documents the Data-tab combo pattern as the template.
- **OQ-4 — keystroke binding mechanism uses napari's keymap chain, not
  the viewer keymap layer.** napari's keymap chain order is `[user_keymap,
  active_layer.keymap, active_layer.class_keymap, viewer.keymap,
  viewer.class_keymap]` (verified against `napari/utils/key_bindings.py`).
  `viewer.bind_key("M", overwrite=True)` is unreachable when a Labels layer
  is active because `Labels.class_keymap` is checked first. `M` therefore
  binds via `Labels.bind_key("M", handler, overwrite=True)` (rewrites the
  class keymap; affects all Labels layers in the process — fine since
  PerCell4 owns its napari viewer). Per-key, no blanket suppression. Future
  PerCell4 keystrokes that target Labels-layer behavior follow the same
  recipe.
- **I1 scope extension.** I1 covers all five session selection fields:
  `active_channel`, `active_segmentation`, `active_mask`, `filter_ids`, and
  `selection`. Filter / Clear-Filter buttons reclassify as Selectors for
  `filter_ids` (their stated purpose is to set/clear it). napari **canvas
  mouse-callback handlers** (cell click) remain the sole Selector for
  `selection`; napari **layer-list selection events** are forbidden from
  writing any session field.
- **`M` migrates from launcher QAction to a process-wide `Labels.bind_key`.**
  Bug B's keystroke routing is a code fix, not just a precondition fix.
  `ViewerWindow` emits a new `multi_select_requested` Qt signal; the
  launcher subscribes and invokes `_on_multi_select`. The launcher's menu
  `QAction("Multi-select…")` keeps its menu entry but loses `setShortcut("M")`
  — the keystroke is now process-wide on Labels layers; the QAction trigger
  also routes through the same launcher slot.
- **Phasor Remove subscribers, not contract.** The Selected ROI panel and
  status bar showing post-removal data is a subscriber-side rebind failure,
  not an unstated part of Remove's contract. Origin's I2 ("two parts.
  Nothing else.") is preserved. The fix introduces a `roi_list_changed`
  signal on the phasor plot (or extends the existing widget-identity
  bookkeeping) and wires the Selected ROI panel and status bar to rebind on
  it. Remove's only edit is to delete the off-label `set_active_mask(None)`.
- **Regression tests are red-first.** U6 and U7 land before U8–U11. Per
  origin's "Next Step" item 5.

---

## Open Questions

### Resolved During Planning

- OQ-1: Resolved as asymmetric decoupling, with explicit canvas-vs-layer-list
  separation. napari `viewer.layers.selection.events.*` subscriptions are
  forbidden from writing session; napari canvas mouse callbacks remain the
  Selector for `session.selection`. *(see Key Technical Decisions)*
- OQ-2: Resolved by existing convention (per-slot emissions). *(see Key Technical Decisions)*
- OQ-3: Deferred. No per-module Selectors added in this plan. *(see Scope Boundaries)*
- OQ-4: Resolved — `M` binds via `Labels.bind_key("M", handler, overwrite=True)`,
  not `viewer.bind_key`, because napari's keymap chain checks `Labels.class_keymap`
  before the viewer keymap. *(see Key Technical Decisions)*
- `filter_ids` / `selection` in I1 scope: Yes (extension). *(see Key Technical Decisions)*
- Filter / Clear-Filter buttons: reclassified as Selectors for `filter_ids`.
  No violation todo filed; U3 documents the verdict in the mutation graph.
- Phasor Remove contract reconciliation: Selected ROI panel and status bar
  rebinds are subscriber-side fixes, not added to Remove's contract.
  Origin's I2 ("two parts. Nothing else.") is preserved. *(see Key Technical Decisions)*

### Deferred to Implementation

- U9's grep may surface additional `viewer.layers.selection.events`
  subscriptions beyond `main_window.py:602-604`. **Scope boundary:** if found,
  they are filed as `todos/` entries and *not* fixed in U9. U9 only removes
  the `_sync_active_layers_from_viewer()` call inside the existing closure
  (preserving the closure's other side effects — seg-panel and grouped-seg-panel
  channel-label updates).
- Whether `gui/segmentation_panel.py:350, 364, 548` and `gui/viewer.py:503`
  (which write `viewer.layers.selection.active`) need additional guards under
  the new session → napari push pattern. They are tool-internal flows, not
  lifecycle handlers; expected to be untouched. Verify during U10.
- Whether channel-delete (`task_panels/data_panel.py:545-546`) should be
  reclassified as Creator-cleanup (legitimate `set_active_channel(None)` on
  resource removal, paralleling `channel-deletion-permanence.md`'s 7-step
  pattern) or kept as an Action with the side-effect surfaced. Decided during
  U3's mutation-graph pass.

---

## Implementation Units

### Phase 1 — Audit artifacts

- U1. **GUI element classification — task panels and peer views**

**Goal:** Produce `docs/audits/gui-element-classification.yaml` covering the
canonical `interfaces/gui/` tree (task panels and peer views). Every
interactive widget classified Selector / Creator / Action with `path:line`,
`reads`, `writes`, `keystroke`, `notes`.

**Requirements:** R2, R5

**Dependencies:** None.

**Files:**
- Create: `docs/audits/gui-element-classification.yaml`

**Approach:**
- Mechanical sweep of `src/percell4/interfaces/gui/task_panels/` (data, flim,
  analysis, io panels — ~1,600 lines combined) and
  `src/percell4/interfaces/gui/peer_views/` (phasor_plot, data_plot,
  cell_table — ~1,700 lines combined).
- For each `QAction`, `QPushButton`, `QCheckBox`, `QComboBox`, `QShortcut`,
  record the connected handler and what `session.*` / `data_model.*` calls
  it makes (or transitively reaches).
- Schema: `id`, `class` (Selector|Creator|Action), `path`, `lines`, `reads`
  (list of session fields), `writes` (list of session fields), `keystroke`
  (or null), `notes`. Mirror the frontmatter convention from
  `docs/solutions/architecture-patterns/channel-deletion-permanence.md`
  (`canonical_source`, `applies_to`) where applicable.
- This unit produces classification only; violations are filed in U2.

**Patterns to follow:**
- `docs/audits/canonical-sources-matrix.yaml` schema for YAML shape.

**Test scenarios:**
- Test expectation: none — pure documentation artifact, no behavior change.

**Verification:**
- Every interactive widget under the canonical paths above appears in the
  YAML with non-null `class` and `path:line` precision sufficient to
  navigate to the handler.

---

- U2. **GUI element classification — legacy `gui/` tree**

**Goal:** Extend `docs/audits/gui-element-classification.yaml` to cover the
legacy flat tree (`src/percell4/gui/`), including viewer keystrokes and
canvas mouse-callback handlers (the Selectors for `session.selection`).

**Requirements:** R2, R5

**Dependencies:** U1.

**Files:**
- Modify: `docs/audits/gui-element-classification.yaml`

**Approach:**
- Cover: `gui/segmentation_panel.py` (561), `gui/grouped_seg_panel.py` (417),
  `gui/threshold_qc.py` (818), `gui/multi_select.py` (412),
  `gui/add_layer_dialog.py` (1690 — the heaviest single file),
  `gui/import_dialog.py` (397), `gui/compress_dialog.py` (816),
  `gui/export_images_dialog.py` (180), `gui/viewer.py` (566).
- For `gui/viewer.py`: explicitly classify (a) every `bind_key` registration
  (today: zero — see U5), (b) every `mouse_drag_callbacks` /
  `mouse_double_click_callbacks` handler that writes `session.selection`,
  (c) every programmatic `viewer.layers.selection.active = …` write
  (writes *to* napari, not *from* events; classified as PerCell4-controlled
  push, not a Selector).
- `add_layer_dialog.py` is large enough that splitting its three tabs (TIFF,
  cellpose-seg, ROI) into separate YAML sections improves auditability.
- Apply the same schema and classification rules as U1.

**Patterns to follow:** Same as U1.

**Test scenarios:**
- Test expectation: none — documentation artifact.

**Verification:**
- Every interactive widget in the legacy tree is classified; the YAML's total
  count of Selector / Creator / Action entries is reported in the file
  preamble.

---

- U3. **Mutation-graph artifact**

**Goal:** Produce `docs/audits/session-mutation-graph.md` listing every code
path that writes `session.active_channel | active_segmentation | active_mask
| filter_ids | selection`, with file:line citations and I1 verdict (compliant
/ violation / borderline). Cross-reference each writer with its YAML entry
in U1/U2.

**Requirements:** R1, R5, R6, R7 (Bug A's writer is the smoking gun this
deliverable formalizes).

**Dependencies:** U1, U2.

**Files:**
- Create: `docs/audits/session-mutation-graph.md`

**Approach:**
- Seed list from research: `application/use_cases/segment_cells.py:95`,
  `application/use_cases/accept_threshold.py:70`,
  `interfaces/gui/task_panels/data_panel.py:176-186, 408-409, 545-546`,
  `interfaces/gui/task_panels/analysis_panel.py:294, 297`,
  `interfaces/gui/peer_views/phasor_plot.py:360`,
  `interfaces/gui/main_window.py:602-604, 926-969, 1083`,
  `gui/segmentation_panel.py:335`, `gui/threshold_qc.py:760`,
  `gui/add_layer_dialog.py:637, 700`.
- Verify the seed list is exhaustive via grep:
  `grep -rn "set_active_\(channel\|segmentation\|mask\)\|set_filter\|set_selection" src/percell4/`.
- For each writer, record: caller path:line, classification (Selector |
  Creator | Action | Lifecycle handler), permitted under I1?, fix needed?,
  cross-link to YAML entry.
- File each violation as a `todos/NNN-pending-pX-<slug>.md` entry following
  the existing convention.
- Decide channel-delete classification (Creator cleanup vs Action with
  side-effect) and document the rationale.
- Decide Filter / Clear-Filter classification per Key Technical Decisions
  ("filter_ids in I1 scope") and document.

**Patterns to follow:**
- `todos/021-…030-pending-p2-*.md` for the violation-todo format.

**Test scenarios:**
- Test expectation: none — documentation artifact.

**Verification:**
- Every grep hit appears in the document with classification and verdict.
- Every violation has an open `todos/` entry.
- The doc states explicitly that after U8, U9, U11 land, only Selectors and
  Creators write any of the five session selection fields.

---

- U4. **Subscriber-rebind matrix**

**Goal:** Produce `docs/audits/subscriber-rebind-matrix.md` listing every
widget or component that displays session-derived data, the `state_changed`
flags or `Session.subscribe` events it must respond to, what it currently
does, and the gap.

**Requirements:** R3, R5, R6 (the Selected ROI panel + status bar staleness
in Bug A is what this deliverable structurally retires).

**Dependencies:** U1.

**Files:**
- Create: `docs/audits/subscriber-rebind-matrix.md`

**Approach:**
- Walk every `state_changed.connect` site (data_panel, analysis_panel,
  flim_panel, viewer, launcher, segmentation_panel, grouped_seg_panel) and
  every `Session.subscribe` site (phasor_plot, data_plot, cell_table).
- Build a table: subscriber, channel (`state_changed` or `Session.subscribe`),
  fields read, flags responded to, caches held, current correctness, fix
  needed.
- Pay special attention to:
  - Phasor Selected ROI panel (`peer_views/phasor_plot.py:271-296`,
    `_on_roi_list_selection:401-416`) — must rebind when selected ROI is
    removed (Bug A symptom #4).
  - Phasor status bar (`peer_views/phasor_plot.py:315-317`) — must reset to
    standard pixel-count format on ROI removal (Bug A symptom #5).
  - Phasor `_active_mask_array` / `_active_mask_flat` cache
    (`peer_views/phasor_plot.py:_on_active_mask_changed:631-651`) — already
    correct; verify it stays correct after Bug A fix.
- Cite the 5-vector HDF5 staleness compound's prevention rules as the
  rebind contract.

**Patterns to follow:**
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  Prevention #4 and #5.

**Test scenarios:**
- Test expectation: none — documentation artifact.

**Verification:**
- Every subscriber site identified by grep is in the matrix.
- Each row has a "fix needed?" column with explicit Yes/No verdict.

---

- U5. **Keystroke-binding audit**

**Goal:** Produce `docs/audits/keystroke-binding-audit.md` enumerating every
PerCell4 keystroke (Qt-window-bound or napari-bound), its precondition
checks, its failure-mode feedback, and any napari natives that shadow it.

**Requirements:** R3, R5, R7

**Dependencies:** U1, U2.

**Files:**
- Create: `docs/audits/keystroke-binding-audit.md`

**Approach:**
- Seed list from research:
  - `M` — `interfaces/gui/main_window.py:128-135` (QAction on launcher).
  - `Ctrl+Return`, `Ctrl+Enter`, `Esc` — `gui/multi_select.py:391-396`
    (dock window).
  - `Ctrl+Return`, `Ctrl+Enter`, `Esc` — `gui/workflows/single_cell/seg_qc.py:205-210`
    (dock window).
- Confirm exhaustiveness with grep:
  `grep -rn "setShortcut\|bind_key\|QShortcut\|keyPressEvent\|keymap" src/percell4/`.
- For each: scope (window | application | viewer), precondition (what state
  must hold), failure feedback (what the user sees if precondition fails),
  napari native that shadows it (or null), verdict under I3 + I4.
- Document the OQ-4 decision: per-key, mechanism chosen by which keymap
  level the conflicting napari binding lives on. For Labels-layer
  conflicts (today: just `M` against `napari:new_label`), use
  `Labels.bind_key(K, handler, overwrite=True)`; for viewer-level
  conflicts, use `viewer.bind_key`; for absolute precedence, use
  `napari.utils.key_bindings.bind_key` on the user keymap. Specifically,
  `M` is rewired in U11 via `Labels.bind_key`; no other keys conflict
  today.
- Cross-reference and *update* `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md`
  guidance #10 in U13 (the convention that "M is correct as window-scoped"
  is being reversed here based on user repro).

**Patterns to follow:**
- napari-modal-tool-overlay-pattern doc's existing "Files this pattern lives
  in" footer convention.

**Test scenarios:**
- Test expectation: none — documentation artifact.

**Verification:**
- Every grep hit is in the doc with all columns populated.
- The doc states explicitly that after U11 lands, `M` is bound via
  `Labels.bind_key("M", handler, overwrite=True)` (rewriting
  `Labels.class_keymap`), and napari's native `new_label` `M` action is
  suppressed for the PerCell4 viewer's lifetime.

---

### Phase 2 — Anchor-bug regression tests (red-first)

- U6. **Bug A regression test — Phasor Remove must not corrupt mask state**

**Goal:** A failing pytest-qt test that asserts the Selected ROI panel,
status bar, `Filter by active mask` checkbox state, and `session.active_mask`
are all correct after clicking Remove on a phasor ROI.

**Requirements:** R6

**Dependencies:** U3 (mutation graph confirms `phasor_plot.py:360` is the
violation site).

**Files:**
- Create: `tests/test_gui_workflows/test_phasor_remove_roi.py`

**Execution note:** Test-first — this test must fail on `main` and pass after
U8 lands.

**Approach:**
- Mirror the `phasor_window` fixture from `tests/test_gui_workflows/test_phasor_mask_filter.py:33-46`.
- Setup: real `PhasorPlotWindow`, fake `Session`, MagicMock repo, a small
  in-memory mask (e.g., 64×64 binary), a single ROI added via
  `phasor_window._on_add_roi()`, `Filter by active mask` checked.
- Action: call the Remove handler.
- Assertions:
  - `session.active_mask` is unchanged (still the original mask name).
  - `_mask_filter_check.isChecked()` is True.
  - `_mask_filter_check.isEnabled()` is True.
  - `_name_edit.text()` is empty (Selected ROI panel cleared).
  - `_vis_check.isChecked()` is False (or the panel is otherwise reset).
  - `_status` text matches the standard "Phasor: N valid pixels" format,
    not "ROI_1: N (X%)".
  - `_phasor_roi_preview` is no longer in the layer list (verify via fake
    viewer or layer-list mock).

**Patterns to follow:**
- `tests/test_gui_workflows/test_phasor_mask_filter.py` fixture and
  assertion style (qtbot.wait for QTimer flushes).

**Test scenarios:**
- Happy path: Covers AE1. Setup as described, call Remove, the five
  in-fixture assertions hold (`session.active_mask` unchanged,
  `_mask_filter_check.isChecked()` True, `_mask_filter_check.isEnabled()`
  True, `_name_edit.text()` empty, `_status` text matches the standard
  "Phasor: N valid pixels" format).
- Edge case: Remove with no ROI present is a no-op (no exception, status
  unchanged).
- Edge case: Remove with two ROIs present removes only the selected one;
  the other ROI's stats remain in the status bar.

**Notes on assertion scope.** The `_phasor_roi_preview` napari layer
removal happens via the `preview_mask_ready` signal → napari subscriber
chain, which has no real viewer in this fixture. Layer-removal is
asserted indirectly via the signal being emitted with the post-removal
ROI list (separate scenario), not by inspecting `viewer.layers`. This
keeps the test fixture lightweight (no real `napari.Viewer`) while still
covering Remove's contract.

**Verification:**
- Test fails on current `main`.
- Test passes after U8.

---

- U7. **Bug B regression test — `M` opens Multi-select while napari has focus**

**Goal:** A failing pytest-qt test that asserts pressing `M` on the napari
viewer immediately opens the Multi-select dialog, regardless of the napari
active layer (mask layer, segmentation layer, image layer).

**Requirements:** R7

**Dependencies:** U5 (keystroke audit confirms `M` is the only conflict).

**Files:**
- Create: `tests/test_gui_workflows/test_multi_select_keystroke.py`

**Execution note:** Test-first — must fail on `main` and pass after U11.

**Approach:**
- Mirror the `viewer_harness` fixture from
  `tests/test_gui_workflows/test_multi_select_e2e.py:43-69` (real `Session`
  + `CellDataModel` + `ViewerWindow` with `napari.Viewer(show=False)` + a
  labels layer + `_FakeLauncher`).
- Construct a real `LauncherWindow`; subscribe to its
  `multi_select_requested` signal slot or whatever launcher slot fires
  after U11.
- **Keystroke delivery:** use napari's own scene-canvas event-injection
  recipe (the same one napari's internal tests use):

  ```python
  from napari.utils.key_bindings import KeyCode
  viewer.window._qt_viewer.canvas._scene_canvas.events.key_press(
      key=KeyCode.from_string("M")
  )
  ```

  `qtbot.keyClick(viewer.window._qt_window, Qt.Key_M)` does NOT exercise
  napari's keymap chain (napari routes through vispy scene-canvas events,
  not Qt key events on the top-level window). Use `qtbot.wait(50)` after
  injection to flush single-shot timers.
- Assertions:
  - The Multi-select controller is alive (`launcher._multi_select_controller is not None`,
    or whatever attribute holds it post-show).
  - `_OVERLAY_LAYER_NAME in viewer.layers` (the staged overlay was created).
  - The labels layer's `selected_label` has not silently incremented (the
    napari native binding did not fire).
- Three parametrized variants: active layer = mask | segmentation | image.
  All three must result in the dialog opening.

**Patterns to follow:**
- `tests/test_gui_workflows/test_multi_select_e2e.py:43-69` fixture.

**Test scenarios:**
- Happy path: Covers AE2. Mask layer active, press M, dialog opens, label
  not incremented.
- Variant: Segmentation layer active (cellpose), press M, same outcome.
- Variant: Image layer active (mNG), press M, dialog still opens (no labels
  layer in scope, but a viable one exists in the layer list — Multi-select
  picks it via `viewer_win.active_labels_layer_or_none()`).
- Edge case: No labels layer in the dataset at all → Multi-select refuses
  with explicit status-bar feedback (Invariant I4); test asserts the message
  text.
- Integration: After Multi-select Accept, `session.filter_ids` is the staged
  set and `state_changed.filter` was emitted exactly once.

**Verification:**
- All variants fail on current `main` (label increments instead of dialog
  opening).
- All variants pass after U11.

---

### Phase 3 — Code fixes

- U8. **Fix Bug A — strip Remove's off-label session write; rebind subscribers**

**Goal:** Restore Remove's two-part contract from origin's I2 by deleting
the single off-label `set_active_mask(None)` call. Make the Selected ROI
panel and status bar rebind correctly when the ROI list changes by adding a
`roi_list_changed` signal (or extending the existing widget-identity
bookkeeping) — this is a subscriber-side fix, not an extension of Remove's
contract.

**Requirements:** R4, R6

**Dependencies:** U6 (regression test must be red).

**Files:**
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
- Test: `tests/test_gui_workflows/test_phasor_remove_roi.py` (created in U6).

**Approach:**
- Read `_on_remove_roi` (`peer_views/phasor_plot.py:349-362`).
  - **Off-label (the only line to delete): line 360 —
    `self._session.set_active_mask(None)`.**
  - **Legitimate ROI bookkeeping (preserve): line 358
    (`self._selected_roi_index = None`) clears the *internal* selected-ROI
    index after the widget is gone. Line 359 (`self._colormap_dirty = True`)
    flags the local `_preview_colormap` for rebuild because surviving ROIs
    are renumbered at lines 355-356.** Both stay.
- Subscriber rebind for Selected ROI panel and status bar:
  - Add a `roi_list_changed` signal (or reuse an existing emit point) that
    fires whenever an ROI is added, removed, or renumbered.
  - Wire `_on_roi_list_selection` (`peer_views/phasor_plot.py:401-416`) so
    that when `_selected_roi_index is None`, the Selected ROI panel widgets
    (`_name_edit`, `_angle_spin`, `_vis_check`) reset to defaults.
  - Status bar: when `_on_remove_roi` finishes and no ROI widgets remain,
    call `_refresh_histogram` (which already writes the standard "Phasor:
    N valid pixels" status at `phasor_plot.py:790`) to restore the no-ROI
    status. `_update_preview` early-returns when `not self._roi_widgets`
    (`phasor_plot.py:541-542`), so it's not the hook point — `_refresh_histogram`
    is.
- Verify against `tests/test_gui_workflows/test_phasor_mask_filter.py` —
  none of those assertions should regress (they assert the active_mask
  ↔ checkbox sync when active_mask actually changes via Selectors; that
  path is unchanged).
- The `_phasor_roi_preview` layer removal already goes through
  `viewer.add/update/remove_staged_overlay` via the
  `preview_mask_ready` signal chain. No edits to that path.

**Patterns to follow:**
- The two-part Remove contract from origin doc Invariant I2.
- Identity-based lookup (Pattern 3, multi-roi patterns doc) for any
  surviving subscribers that reference the removed widget.

**Test scenarios:**
- All scenarios from U6 (now passing).
- Existing `test_phasor_mask_filter.py` continues to pass (active_mask
  ↔ checkbox sync still correct when active_mask actually changes via
  Selectors).

**Verification:**
- U6 tests pass.
- Existing phasor tests pass.
- Manual repro of Bug A's exact 7-step sequence no longer reproduces.

---

- U9. **Decouple napari → session: surgically remove the `_sync_active_layers_from_viewer` call**

**Goal:** napari's `viewer.layers.selection.events.active` no longer triggers
session writes. The existing closure at `main_window.py:594-605` also drives
seg-panel and grouped-seg-panel channel-label refreshes — those side effects
are preserved. Only the `_sync_active_layers_from_viewer()` call inside the
closure is removed; the closure stays connected to the napari event for the
remaining panel updates.

**Requirements:** R8

**Dependencies:** U3 (mutation graph confirms this is an I1 violation).

**Files:**
- Modify: `src/percell4/interfaces/gui/main_window.py`
  - In the `events.active` closure (`:594-605`), delete the
    `_sync_active_layers_from_viewer()` line (currently `:600`).
  - Delete `_sync_active_layers_from_viewer:926-969` (the function body
    becomes dead code after its sole call site is removed).
  - Confirm `_update_active_channel_label` (`:917`, currently a documented
    no-op) and the seg/grouped-seg `update_channel_label` /
    `update_channels` calls inside the closure remain wired and still fire
    on napari's active-layer change.
- Test: `tests/test_gui_workflows/test_no_napari_session_writes.py`

**Approach:**
- Grep for every `viewer.layers.selection.events` subscription in
  `src/percell4/`. **Scope boundary:** if additional subscriptions reach
  `session.set_active_*`, they are filed as `todos/` entries and *not* fixed
  in U9. U9 owns only the `_sync_active_layers_from_viewer` call.
- Verify `gui/segmentation_panel.py:350, 364, 548` and `gui/viewer.py:503`
  remain unaffected (they write `viewer.layers.selection.active` *to* napari,
  not *from* napari events).
- Removal of this single line closes the napari → `set_active_mask` /
  `set_active_segmentation` path. Combined with U10's session → napari
  one-way push, the system retains visual coherence without the napari
  event loop writing session state.

**Patterns to follow:**
- The `_is_originator` re-entrancy convention from
  `napari-direct-label-colormap-rendering-blocked-by-events.md` (used in U10).

**Test scenarios:**
- Happy path: Click a layer in the napari layer list. Assert `session.active_mask`
  and `session.active_segmentation` are unchanged (they were the auto-selected
  values from dataset load).
- Variant: Click each layer type (mask, segmentation, image) in turn. None
  cause session writes.
- Integration: The Data-tab combo for active mask/segmentation is unchanged
  by napari clicks (the combo's `currentText` does not update from a napari
  click).
- Edge case: Confirm `gui/segmentation_panel.py`'s manual-edit UX still
  works (it writes napari active layer programmatically; that should still
  function because session → napari is allowed).

**Verification:**
- New test passes.
- Existing tests pass (in particular, `test_phasor_mask_filter.py`).
- Manual: dataset load → click a different mask layer in napari → Data-tab
  active-mask combo does NOT update.

---

- U10. **Add session → napari one-way push for active mask/segmentation**

**Goal:** When session writes `active_mask` or `active_segmentation`, napari's
matching layer becomes selected in the layer list. The push is a controlled
PerCell4-side write inside `ViewerWindow._on_state_changed`, not a
subscription to a napari event. Re-entrancy guarded by `_is_originator`.

**Requirements:** R8 (visual coherence after decoupling).

**Dependencies:** U9.

**Files:**
- Modify: `src/percell4/gui/viewer.py`
  - Extend `ViewerWindow._on_state_changed` (already wired to
    `data_model.state_changed` at `:115`) to handle `change.mask` and
    `change.segmentation` by setting `viewer.layers.selection.active` to the
    matching layer.
  - Reuse the existing `_get_active_labels_layer` helper (`viewer.py:398-424`,
    which already does name + `metadata["percell_type"]` lookup) rather than
    duplicating the search logic.
  - Reuse the existing `self._is_originator` flag (`viewer.py:75`) to guard
    against re-entrancy. The matching `Labels.bind_key("M", …)` and any
    future PerCell4-driven napari writes follow the same pattern.
  - `PERCELL_TYPE_KEY`, `LAYER_TYPE_MASK`, `LAYER_TYPE_SEGMENTATION` are
    already defined at `viewer.py:18-20`. Reuse, do not redefine.
- Test: extend `tests/test_gui_workflows/test_no_napari_session_writes.py`
  with positive assertions.

**Approach:**
- Add the mask/segmentation push branches inside `_on_state_changed`'s
  existing dispatch on `StateChange` flags. The handler mirrors the existing
  `change.filter` and `change.selection` branches in shape.
- No new handler is created; this is an extension of an existing
  subscriber. The launcher's existing `state_changed` wiring is unchanged.
- Re-entrancy: the layer-list event subscription is gone (U9), so the
  reverse path that produced re-entrancy is closed. The `_is_originator`
  guard remains as defense-in-depth for any future bidirectional logic.

**Patterns to follow:**
- `_is_originator` re-entrancy guard from
  `napari-direct-label-colormap-rendering-blocked-by-events.md`.
- Selector-side `blockSignals(True/False)` from `data_panel.py:198-204`.

**Test scenarios:**
- Happy path: Data-tab Active Mask combo change → napari active layer flips
  to the matching mask layer.
- Variant: Active Segmentation combo change → napari active layer flips to
  the matching segmentation layer.
- Edge case: session writes `active_mask = None` → napari active layer is
  cleared (or remains on a non-mask layer).
- Edge case: A Creator (Cellpose, threshold accept) writes a new
  segmentation and auto-selects it → napari highlights the new layer.
- Integration: The push must not re-enter `_sync_active_layers_from_viewer`
  (verified by U9 — that handler is gone).

**Verification:**
- New tests pass.
- Manual: dataset load → use Data tab to change Active Mask → napari layer
  list highlights the new mask.

---

- U11. **Fix Bug B — bind `M` via `Labels.bind_key`; emit `multi_select_requested` signal**

**Goal:** Migrate the `M` shortcut from the launcher's window-scoped
`QAction` to `Labels.bind_key("M", handler, overwrite=True)`, which
rewrites napari's `Labels.class_keymap` and wins over the native
"new_label" binding. The handler emits a new `multi_select_requested` Qt
signal on `ViewerWindow`; the launcher subscribes and runs its existing
`_on_multi_select` slot. Add explicit per-cause precondition feedback
when Multi-select cannot open (no labels layer / workflow locked / viewer
not alive). Conform to Invariants I3 and I4.

**Requirements:** R3, R7

**Dependencies:** U7 (regression test must be red), U9 (no longer
entangled with active-layer wiring).

**Files:**
- Modify: `src/percell4/gui/viewer.py`
  - At module-import time (or once on first `_ensure_viewer` call),
    register `Labels.bind_key("M", _on_m_keystroke, overwrite=True)`. This
    rewrites `Labels.class_keymap`'s `M` entry process-wide. PerCell4 owns
    its embedded napari, so the rewrite is safe.
  - Add a new `multi_select_requested = Signal()` on `ViewerWindow`. The
    `_on_m_keystroke` handler resolves the active `ViewerWindow` instance
    (e.g., via the napari `Labels` layer's `_viewer_ref` or via a
    module-level registry of `ViewerWindow` instances) and emits
    `multi_select_requested`.
  - The handler always claims the event (returns nothing from the bind_key
    callback — which is napari's signal that the keystroke is consumed).
- Modify: `src/percell4/interfaces/gui/main_window.py`
  - Remove `setShortcut("M")` from the QAction at `:128-135`. The QAction
    itself stays (menu entry).
  - In `_setup_viewer_window` (or wherever ViewerWindow is constructed),
    connect `viewer_win.multi_select_requested` to the same launcher slot
    that `QAction("M")`'s `triggered` signal already invokes
    (`_on_multi_select` at `:637-651`).
  - Update `_on_multi_select` so its single status-bar message becomes
    per-cause: split based on which precondition failed (no labels layer
    vs workflow_locked vs viewer not alive). The
    `launch_multi_select_tool` return value or an out-parameter conveys
    the cause.
- Test: `tests/test_gui_workflows/test_multi_select_keystroke.py` (created
  in U7).

**Approach:**
- **Why `Labels.bind_key` and not `viewer.bind_key`:** napari's keymap
  chain order is `[user_keymap, active_layer.keymap, active_layer.class_keymap,
  viewer.keymap, viewer.class_keymap]`. `Labels.class_keymap` is checked
  before any viewer-level binding; a `viewer.bind_key("M", overwrite=True)`
  would never fire while a Labels layer is active. `Labels.bind_key`
  rewrites the class keymap directly. Verified against
  `napari/utils/key_bindings.py` and `napari/_qt/qt_viewer.py`.
- **Why a Qt signal and not a direct call:** `ViewerWindow` does not own a
  launcher reference today (`viewer.py` has zero `launcher` references).
  Adding one would expand the existing decoupled-viewer pattern. Emitting
  a Qt signal preserves the decoupling — launcher subscribes, viewer emits.
- The handler must claim the event regardless of preconditions
  (Invariant I3); on precondition failure it surfaces feedback (Invariant
  I4) but does not let the keystroke fall through to napari's native
  `new_label` action.
- `napari-modal-tool-overlay-pattern-2026-04-29.md` guidance #10 ("M is
  correct as window-scoped") is reversed by this unit; U13 updates that
  doc.

**Patterns to follow:**
- `napari-modal-tool-overlay-pattern-2026-04-29.md` for the overall
  Multi-select install/teardown contract.
- Per-cause precondition feedback inspired by I4.

**Test scenarios:**
- All scenarios from U7 (now passing).
- Edge case: Workflow locked (another tool active) → status bar shows
  "Multi-select unavailable: another tool is running"; native `M`
  behavior does not fire.
- Edge case: No labels layer at all → status bar shows "Multi-select
  unavailable: no labels layer"; native `M` does not fire.
- Edge case: Viewer not alive (closed) → handler is a no-op; no exception.

**Verification:**
- U7 tests pass.
- Manual repro of Bug B's exact 2-step sequence (load dataset, press M)
  immediately opens Multi-select.

---

### Phase 4 — Documentation and follow-ups

- U12. **Refresh stale CLAUDE.md files**

**Goal:** Update `src/percell4/CLAUDE.md` and `src/percell4/gui/CLAUDE.md`
to reflect the hexagonal layout and the new state-ownership rules — but
only after the code has actually been changed to enforce them.

**Requirements:** R5 (living artifacts).

**Dependencies:** U7, U8, U9, U10, U11 (the code fixes must land before
documentation describes the post-fix state, per the project rule "Per-module
CLAUDE.md files describe current state only — never plans, never history").

**Execution note:** U12 must land *after* U8–U11 are complete and verified
in CI. Updating CLAUDE.md before the code enforces the new invariants would
inject aspirational rules into Claude's context window for every future
session and violate the project's "current state only" rule.

**Files:**
- Modify: `src/percell4/CLAUDE.md`
- Modify: `src/percell4/gui/CLAUDE.md`
- Modify: `CLAUDE.md` (root) — add a "GUI state ownership" section that
  references the audit deliverables and Invariants I1–I5.

**Approach:**
- Remove path references to files that have moved
  (`gui/launcher.py` → `interfaces/gui/main_window.py`,
  `gui/data_plot.py` → `interfaces/gui/peer_views/data_plot.py`, etc.).
- Add a short "State ownership" subsection naming the three classes
  (Selector / Creator / Action) and pointing to
  `docs/audits/gui-element-classification.yaml`.
- Per CLAUDE.md project rule: "Per-module CLAUDE.md files describe current
  state only — never plans, never history." Keep the docs descriptive of
  the post-audit state, not prescriptive of the audit work itself.

**Patterns to follow:**
- Existing CLAUDE.md style (terse, declarative, no history).

**Test scenarios:**
- Test expectation: none — documentation.

**Verification:**
- Every path reference in the three CLAUDE.md files resolves to an
  existing file.
- The state-ownership invariants are stated once and the audit deliverables
  are linked, not duplicated.

---

- U13. **Promote new conventions to `docs/solutions/` canonical sources**

**Goal:** Capture the audit's net-new conventions (Action contracts are
exhaustive; per-key keystroke suppression via `bind_key(K, overwrite=True)`;
session → napari one-way push pattern) as canonical-source entries
following the existing frontmatter shape.

**Requirements:** R5.

**Dependencies:** U7–U11 (conventions only become canonical after the code
has landed).

**Files:**
- Create: `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`
- Create: `docs/solutions/architecture-patterns/keystroke-binding-on-napari-viewer.md`
- Create: `docs/solutions/architecture-patterns/session-to-napari-one-way-push.md`
- Modify: `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md`
  — update guidance #10 to reflect the OQ-1 reversal: `M` is now bound on
  the napari viewer, not as a window-scoped QAction.
- Modify: `docs/audits/canonical-sources-matrix.yaml` — register the new
  canonical sources so the R15/R16 PreToolUse hook fires when relevant
  files are edited.

**Approach:**
- Each new doc follows the frontmatter shape in
  `docs/solutions/architecture-patterns/channel-deletion-permanence.md`
  (`canonical_source`, `applies_to`, `status: pre_canonical`,
  `duplicates_at: []`, `module`, `tags`, `problem_type`).
- `applies_to` globs for the new sources:
  - Action-contract: `src/percell4/interfaces/gui/**/*.py`,
    `src/percell4/gui/**/*.py` (broad, but qualify in the doc body).
  - Keystroke-on-viewer: `src/percell4/gui/viewer.py`,
    `src/percell4/interfaces/gui/main_window.py`.
  - Session-to-napari push: `src/percell4/gui/viewer.py`,
    `src/percell4/interfaces/gui/main_window.py`.
- Cross-link from each new doc to the audit deliverables in `docs/audits/`.

**Patterns to follow:**
- `docs/solutions/architecture-patterns/channel-deletion-permanence.md`
  frontmatter and structure.

**Test scenarios:**
- Test expectation: none — documentation.

**Verification:**
- Each new file passes the frontmatter schema check used by
  `docs/audits/canonical-sources-matrix.yaml`.
- Running `python3 scripts/learnings_applicability.py
  src/percell4/gui/viewer.py` surfaces the keystroke and push canonical
  sources.

---

## System-Wide Impact

- **Interaction graph.**
  - `_sync_active_layers_from_viewer()`'s removal (single line inside
    the `events.active` closure) severs napari → session for active mask
    and active segmentation. The closure stays connected for the
    seg-panel and grouped-seg-panel channel-label updates.
  - The new session → napari push lives in `ViewerWindow._on_state_changed`
    (existing handler, extended) and creates a controlled, single-direction
    edge guarded by `_is_originator`.
  - `M`'s migration moves the keystroke from a window-scoped `QAction` on
    the launcher to a process-wide entry in `Labels.class_keymap` (rewritten
    via `Labels.bind_key("M", handler, overwrite=True)`). The launcher's
    menu QAction stays for menu invocation; only its `setShortcut("M")` is
    removed. ViewerWindow gains a `multi_select_requested` Qt signal that
    the launcher subscribes to.
- **Error propagation.** Multi-select precondition failures now produce
  per-cause status messages (I4); previously a single generic message.
  No new error classes.
- **State lifecycle risks.**
  - Confirm no panel or peer view caches `active_mask` / `active_segmentation`
    via the *old* napari sync. U4's matrix is the safety check.
  - Verify `gui/segmentation_panel.py` and `gui/multi_select.py`'s
    programmatic napari active-layer writes still function under the new
    push pattern (they write *to* napari, not *from* it; should be fine).
- **API surface parity.**
  - The launcher's QAction for Multi-select still exists in the menu but
    without a keyboard shortcut on the QAction. The keystroke is now on
    napari's `Labels.class_keymap` instead, and reaches the launcher via
    `ViewerWindow.multi_select_requested`. Menu invocation continues to
    work; both paths converge on the same launcher slot.
- **Integration coverage.**
  - U6 and U7 are pytest-qt e2e tests with real `Session`/`CellDataModel`/
    `ViewerWindow`. They prove the full subscriber chain, not just the
    handler.
- **Unchanged invariants.**
  - `CellDataModel.state_changed` shape is unchanged. No new fields, no new
    flag bits.
  - The Session → CellDataModel bridge is unchanged.
  - The `_phasor_roi_preview` overlay layer naming is unchanged.
  - The five test files under `tests/test_gui_workflows/` (phasor mask
    filter, multi-select e2e, multi-select unit, TCSPC tab state, add-layer
    TCSPC) continue to pass.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Removing the `_sync_active_layers_from_viewer()` call inside the existing `events.active` closure regresses the seg-panel / grouped-seg-panel channel-label updates that share the same closure. | U9 specifies surgical removal of one line, not the whole subscription. The closure stays connected for the other panel updates; `_update_active_channel_label` is verified to remain a documented no-op. |
| `Labels.bind_key("M", handler, overwrite=True)` rewrites class keymap process-wide and could affect Labels layers added by future plugins. | PerCell4 owns its embedded napari; no plugins currently consume the Labels class keymap. If that changes, the binding can move to `napari.utils.key_bindings.bind_key` on the user keymap (top of the chain). U13's canonical-source doc records both options. |
| The new `multi_select_requested` Qt signal couples ViewerWindow to a specific launcher slot. | The signal is decoupled by design — ViewerWindow does not import the launcher; the launcher subscribes. Mirrors the existing pattern where ViewerWindow exposes signals (e.g., layer-selection emissions) and the launcher wires them up. |
| Subscriber-side rebind (U8 panel/status reset) introduces a new lifecycle bug if `_on_roi_list_selection` fires for a removed widget. | Use the identity-based widget lookup pattern documented in `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md` Pattern 3. U6's regression test exercises Remove-with-no-ROI and Remove-with-two-ROIs cases to surface stale-reference bugs. |
| Naming a new `applies_to` glob in U13 fires the PreToolUse hook on too many files and creates noise. | Scope `applies_to` narrowly (single files where possible); `status: pre_canonical` signals the convention is new and may be revised. |
| The mutation graph (U3) surfaces additional violations not anticipated. | Each violation files a `todos/` entry; the plan does not block on fixing them. R6/R7 only require the anchor bugs retire. |
| Scene-canvas event injection in U7 differs from napari's published API and may break across napari versions (0.5–0.8). | The injection recipe is taken from napari's own internal tests (`napari/_tests/test_key_bindings.py:45`), which are version-pinned with the library. U7 imports from `napari.utils.key_bindings` to track API moves. If napari renames the path, the test fails fast with an import error rather than silently passing the wrong assertion. |

---

## Documentation / Operational Notes

- All four `docs/audits/` artifacts are living documents — they should be
  updated whenever a new GUI element is added, a new keystroke is bound, or
  a new session field is introduced. U12's CLAUDE.md updates note this
  expectation.
- After U13 lands, the canonical-sources matrix gains three new entries.
  Future contributors editing `viewer.py`, `main_window.py`, or any
  `interfaces/gui/**` file will see a hook warning pointing them at the new
  conventions.
- No rollout/migration concerns — this is a refactor with regression tests.
  No user-data implications.

---

## Sources & References

- **Origin document:** `docs/brainstorms/2026-05-01-gui-state-handling-audit-requirements.md`
- **Related code:**
  - `src/percell4/model.py`
  - `src/percell4/application/session.py`
  - `src/percell4/interfaces/gui/main_window.py`
  - `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
  - `src/percell4/interfaces/gui/task_panels/data_panel.py`
  - `src/percell4/gui/viewer.py`
  - `src/percell4/gui/multi_select.py`
- **Related learnings:**
  - `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`
  - `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md`
  - `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  - `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`
  - `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`
  - `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`
- **Related tests (templates):**
  - `tests/test_gui_workflows/test_phasor_mask_filter.py`
  - `tests/test_gui_workflows/test_multi_select_e2e.py`
