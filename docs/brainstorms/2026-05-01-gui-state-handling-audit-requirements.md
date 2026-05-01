---
date: 2026-05-01
topic: gui-state-handling-audit
status: requirements
---

# GUI State-Handling Audit — Requirements

## Problem Frame

Users encounter bugs where buttons and GUI features behave differently — and
incorrectly — depending on click order or which use case the user is in. Two
recent reproducible cases anchor this audit:

1. **Phasor-plot Remove silently clears `active_mask`.** `Filter by active mask`
   auto-unchecks and refuses to re-engage; the histogram stays visually
   mask-filtered; the Selected ROI panel and status bar continue showing the
   removed ROI's data.
2. **`M` after dataset load fails to open Multi-select.** The keystroke
   silently increments the Labels-layer label counter (napari's native binding)
   instead of opening PerCell4's dialog. Only after Clear Selection — which
   flips the napari active layer from `SG_mask` to `cellpose` as an
   undocumented side effect — does `M` work.

Both bugs share one structural root cause: **non-selector UI elements are
mutating state they shouldn't own**, and **failure modes are silent**. Users
are left to discover opaque incantations that happen to fix things. The
product damage is loss of UI predictability — users cannot form a stable
mental model.

The goal of this audit is not to fix any single bug. It is to define the
ownership rules, classifications, and invariants under which the GUI must
operate, plus the living artifacts that make compliance auditable as the
codebase evolves.

## Goals

- Establish a single, explicit rule for *who* may mutate
  `session.active_channel | active_segmentation | active_mask`.
- Classify every interactive UI element so the rule can be mechanically
  audited.
- Eliminate silent failure modes — every refused or no-op action must be
  legible to the user.
- Eliminate causally opaque side effects — a button does what its label says,
  and nothing else.
- Produce living artifacts in `docs/audits/` that future GUI work continues to
  satisfy.

## Non-goals

- Implementing fixes for any specific bug (planning will sequence those).
- Refactoring `CellDataModel.state_changed` itself.
- Performance / UI-responsiveness audit.
- Visual design or theming.
- Plugin-author-facing rules (`src/percell4/plugins/` is empty).
- Domain-logic correctness (FLIM math, segmentation quality) — separate audit.

## Anchor Bugs

These are the concrete cases the audit's invariants must retire. They are
preserved here because abstract invariants drift; anchored ones do not.

### Bug A — Remove ROI corrupts mask-filter state

**Repro.**
1. `python main.py`, Load Dataset
   `/Volumes/NX-01-A/2026-04-27_percell4_datasets/Dish_1_WT_As_60min.h5`.
2. Data tab → Active Channel = `mNG`.
3. FLIM tab → Compute Phasor → Apply Wavelet Filter → check `Filter by active
   mask` (active mask was auto-selected on dataset load; user never set it).
4. Load ROIs → select ROI .json. ROI loads as `ROI_1`.
5. Click Remove.

**Expected.** ROI graphic and `_phasor_roi_preview` napari layer are removed.
Nothing else changes.

**Actual.**
- `Filter by active mask` auto-unchecks and refuses re-engagement.
- Histogram still appears mask-filtered (~78k pixels) despite the unchecked
  box.
- Selected ROI panel still shows `Name: ROI_1`, `Visible ✓`.
- Status bar still shows `ROI_1: 78,359 (1.2%)` instead of the standard
  phasor pixel count.
- Unchecking and re-checking `Filtered` recovers a coherent state but loses
  the mask filter entirely.

**Root cause class.** Action button (Remove) writes session state outside its
contract; multiple subscribers read divergent stale snapshots.

### Bug B — `M` keystroke shadowed by napari, dialog never opens

**Repro.**
1. `python main.py`, Load Dataset (same dataset as Bug A).
2. Press `M` on the napari viewer.

**Expected.** Multi-select dialog opens on the segmentation layer; clicks
toggle staged cells.

**Actual.**
- First `M`: no dialog. Active label silently increments `1 → 2` (napari's
  native Labels-layer M-binding fires).
- Second `M`: napari INFO toast `Current selected label is not being used…`.
  Still no dialog.
- After clicking Analysis → Clear Selection, then pressing `M`: dialog opens,
  active napari layer flips from `SG_mask` to `cellpose`, `_multi_select_staged`
  layer appears.

**Root cause class.** Keystroke bound to a PerCell4 feature whose precondition
fails silently; the keystroke is then claimed by napari's native binding.
The fix (Clear Selection) works only because of an undocumented side effect.

## Domain Rules

### Three-class taxonomy

Every interactive UI element is exactly one of:

| Class        | Reads session | Writes session                          | Writes new resources to dataset |
|--------------|---------------|-----------------------------------------|---------------------------------|
| **Selector** | yes           | yes                                     | no                              |
| **Creator**  | yes           | yes (auto-selects newly written resource) | yes                           |
| **Action**   | yes           | **no**                                  | no                              |

**Selectors** include: Data-tab dropdowns for channel/segmentation/mask, and
per-module dropdowns whose explicit purpose is to let the user pick a
channel/segmentation/mask.

**Creators** include: Cellpose (creates a segmentation), Apply Visible as Mask
(creates a mask), TIFF/SDT importer (creates channels), ROI-to-mask save flows.

**Actions** include: Compute Phasor, Apply Wavelet Filter, Remove ROI, Clear
Selection, Save Phasor PNG, Add ROI, Load/Save ROIs, every Run / Apply / Open
button that does not exist to pick a session field or write a new resource.

### Invariants

**I1. Selection mutation is selector-and-creator-scoped.**
`session.active_channel`, `session.active_segmentation`, and
`session.active_mask` may be written only by Selectors and Creators. Actions
and implicit lifecycle handlers must treat them as read-only.

**I2. Action contracts are exhaustive.**
A button's effects are exactly the effects implied by its label and tooltip.
Hidden side effects on session state, viewer state, or sibling-window state
are forbidden. The phasor-plot Remove button has the explicit two-part
contract: (a) remove the ROI graphic from the histogram, (b) remove the
`_phasor_roi_preview` layer from napari. Nothing else.

**I3. Keystrokes are exclusive.**
Any keystroke bound to a PerCell4 feature must claim the event unconditionally
while the relevant viewer has focus. If a precondition fails, PerCell4's
handler still claims the event and surfaces feedback (Invariant I4). Native
napari bindings on the same key must be suppressed for that key while the
PerCell4 binding is registered. Silent shadowing is forbidden.

**I4. Preconditions are visible.**
When the user invokes a feature whose precondition is not met, they receive
explicit feedback (toast, status bar, dialog) naming the missing condition
and, where possible, how to satisfy it. Silent no-ops are forbidden.

**I5. No causally opaque side effects.**
A button cannot fix an unrelated feature as a side effect. Cross-feature
state changes either belong to the feature they enable (e.g., Multi-select
ensures its own preconditions on open) or are surfaced explicitly to the
user.

### Default-state rule

On dataset load, the first available channel, segmentation, and mask are
auto-selected. This is the only implicit selection in the lifecycle. After
load, only Selectors and Creators move the selection. This rule is the
foundation Bug A violates: the user never set the active mask, so a button
cannot revoke it.

### Subscriber-side rule (corollary)

Any widget that displays session-derived data — Selected ROI panel, status
bar, ROI list, layer list, dropdowns — must rebind on the relevant
`state_changed` flag, not cache its values across mutations. Bugs A
symptoms #4 and #5 (stale Selected ROI panel and status bar) are subscriber-
side stale-snapshot failures; this rule retires that class.

## Audit Scope and Output

### In scope

- All interactive UI elements under `src/percell4/interfaces/gui/` and
  `src/percell4/gui/`.
- All writes to session selection fields in `src/percell4/model.py` and
  `src/percell4/application/session.py`.
- All keystroke bindings registered on the napari viewer in
  `src/percell4/gui/viewer.py` and `src/percell4/interfaces/gui/peer_views/`.

### Out of scope

- Implementing fixes (planning sequences those after the audit).
- Refactoring `CellDataModel.state_changed` shape.
- Adding new session fields beyond what `StateChange` already tracks (unless
  OQ-1 forces a decision).

### Deliverables

All under `docs/audits/`:

1. **`gui-element-classification.yaml`** — every interactive UI element
   classified Selector / Creator / Action, with file path and line range.
   Fields: `id`, `class`, `path`, `lines`, `reads`, `writes`, `keystroke`,
   `notes`.
2. **`session-mutation-graph.md`** — every code path that writes
   `active_channel | active_segmentation | active_mask | filter_ids` plus
   whether the writer is permitted under I1, with file:line links.
3. **`subscriber-rebind-matrix.md`** — every widget that reads session-derived
   data and the `state_changed` flags it must respond to, with current state
   and required state.
4. **`keystroke-binding-audit.md`** — every PerCell4 keystroke binding on
   the napari viewer, the precondition checks (if any), the failure-mode
   feedback (if any), and any napari native bindings on the same key that
   need suppression under I3.

Each deliverable ends with a todo list of concrete fixes filed under `todos/`.

## Dependencies / Assumptions

- `CellDataModel.state_changed` is the sole signaling channel for session
  state. *Verified* at `src/percell4/model.py`.
- Some modules expose channel dropdowns; segmentation/mask dropdowns may not
  exist anywhere outside the Data tab. *Unverified — audit will inventory.*
- `_phasor_roi_preview` is the canonical napari layer name used by the phasor
  plot for ROI preview. *Unverified — audit will check
  `src/percell4/interfaces/gui/peer_views/phasor_plot.py` and
  `src/percell4/domain/flim/phasor.py`.*
- napari Labels-layer's native `M` binding is "set selected label to next
  unused" (inferred from observed INFO toast). *Unverified against napari
  source.*

## Open Questions

- **OQ-1.** Is napari's *active layer* session state (subject to I1) or a
  viewer-internal concern? Bug B shows it has session-coupled consequences.
  Audit cannot complete without a decision; if session, `StateChange` may need
  a new flag.
- **OQ-2.** When a Creator writes a new resource and auto-selects it, what
  `StateChange` flags fire — only the slot's flag (e.g. `mask`), or also
  `data`? Subscriber-rebind matrix depends on this.
- **OQ-3.** How do per-module channel/segmentation/mask Selector dropdowns
  synchronize with Data-tab Selectors? Two-way via `state_changed`? Canonical
  vs mirror? This affects whether new Selectors can be added safely.
- **OQ-4.** Should napari's native `M`, `1`, `2`, … bindings be globally
  suppressed across the PerCell4 viewer, or only on a per-key basis where
  PerCell4 has a competing binding? Affects I3 implementation surface.

## Success Criteria

The audit is complete when:

- Every interactive UI element is classified in
  `docs/audits/gui-element-classification.yaml` with `path:line` precision.
- Zero Actions write session selection state (verified by
  `session-mutation-graph.md`).
- Every Creator emits the correct `state_changed` flags for the resource it
  created.
- Every session-derived widget is wired to the correct `state_changed` flags
  (verified by `subscriber-rebind-matrix.md`).
- Every PerCell4 keystroke is exclusive vs napari natives in
  `keystroke-binding-audit.md`, and every precondition has user-visible
  feedback.
- Anchor Bugs A and B are no longer reproducible — both have regression tests
  under `tests/test_gui_workflows/`.

## Next Step

Hand off to `/ce-plan` to sequence the audit work. Suggested ordering:

1. Inventory pass — populate `gui-element-classification.yaml` mechanically
   from a grep over Qt widget instantiations and napari binding registrations.
2. Mutation-graph pass — trace every write to `session.active_*` and
   `session.filter_ids`, classify writer, file violations as todos.
3. Subscriber pass — audit each consumer widget, build rebind matrix,
   file gaps as todos.
4. Keystroke pass — enumerate every binding, classify exclusivity and
   feedback, file gaps as todos.
5. Anchor-bug regression tests — write `tests/test_gui_workflows/` tests for
   Bug A and Bug B *first*, watch them fail, then proceed with fixes from
   the todo backlog.

Resolve OQ-1 before step 4 starts.
