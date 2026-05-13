---
title: "feat: Prompt for layer name on Run Cellpose and grouped thresholding"
type: feat
status: completed
date: 2026-05-12
---

# feat: Prompt for layer name on Run Cellpose and grouped thresholding

## Overview

Mirror the **Apply Current Phasor as Mask** naming flow on two more
write paths: **Run Cellpose** in the Segmentation tab, and the grouped
thresholding `_on_run` flow. Each surface gets a `QInputDialog.getText`
prompt with a sensible default, empty-name re-prompt, and refuse-and-
re-prompt on collisions — same UX the phasor flow already provides.

Extract the prompt+validate block as a shared helper
(`src/percell4/gui/_resource_name_prompt.py`) so a future fourth call
site doesn't drift, and so the phasor flow consumes the same helper.
This is the same drift class that bit PR #9 and the recent
`compress_dialog` fixes; the shared helper closes it preemptively.

---

## Problem Frame

Cellpose currently writes its segmentation under a name auto-derived
from the cell count (`f"cellpose_{n_cells}"` at
`src/percell4/application/use_cases/segment_cells.py:96`). Grouped
thresholding currently writes under a name auto-derived from channel +
metric (`f"grouped_{channel}_{metric}"` at
`src/percell4/gui/grouped_seg_panel.py:241`). Neither lets the user
choose the name at write time.

The user wants symmetric naming UX across all three "write a new
resource" surfaces. The Apply Current Phasor as Mask flow already does
this correctly (`src/percell4/interfaces/gui/peer_views/phasor_plot.py:1855-1911`)
— prompt with default, validate non-empty, refuse to overwrite, re-
prompt on conflict. The two other surfaces should behave identically.

---

## Requirements Trace

- **R1.** After clicking **Run Cellpose**, a modal text-input window
  appears so the user can name the segmentation layer. Default is
  `"cellpose"`. Cancel aborts the run.
- **R2.** After clicking **Run** in the grouped thresholding panel, the
  same modal appears. Default is `"grouped"` (or `f"grouped_{channel}_{metric}"`
  — see Key Technical Decisions). Cancel aborts the run.
- **R3.** Empty name → re-prompt with the original computed default
  (not the blank string the user just submitted).
- **R4.** Name collides with an existing resource of the same kind →
  warn and re-prompt with the conflicting name as the new default. The
  user cannot overwrite by accident; they can only enter a new name.
- **R5.** Behavior, copy, and validation match
  `phasor_plot.py:_on_apply_current_phasor_as_mask` (R1–R4 collectively).
- **R6.** The existing phasor flow is refactored to consume the shared
  helper so all three call sites use one code path.

---

## Scope Boundaries

- Do not change `Apply Current Phasor as Mask`'s user-facing behavior —
  only the implementation site (consume the shared helper).
- Do not add a "force overwrite" toggle — the phasor pattern explicitly
  refuses overwrites, and the user asked for the same approach. If a
  user wants to overwrite, they delete first and re-run. (`/labels/`
  and `/masks/` already have delete affordances in the Data tab.)
- Do not modify the existing `Yes/No/Cancel` overwrite dialog in
  `grouped_seg_panel.py:243-257` to *coexist* with the new prompt —
  remove it. The shared helper's refuse-and-re-prompt is the single
  collision UX.
- Do not refactor `data_panel.py:394` and `:455` (channel rename /
  delete prompts) to consume the helper. Those have different semantics
  (rename, not create) and are out of scope.
- Do not add validation rules beyond "non-empty + no collision". Names
  with spaces, dashes, mixed case, etc. are accepted — same as the
  phasor flow today.

---

## Context & Research

### Relevant Code and Patterns

- **Reference pattern** — `src/percell4/interfaces/gui/peer_views/phasor_plot.py:1855-1911`
  (`_on_apply_current_phasor_as_mask`). The `while True` loop at lines
  1876-1898 is the exact validation shape to extract.
- **Cellpose entry** — `src/percell4/gui/segmentation_panel.py:385-425`
  (`_on_run_cellpose`). Worker dispatched at line 420; completion
  handler at `_on_cellpose_done` (line 433) calls
  `SegmentCells.finalize(masks, remove_edge_cells=...)`.
- **Cellpose name source** —
  `src/percell4/application/use_cases/segment_cells.py:96`. Hardcoded
  `seg_name = f"cellpose_{n_cells}"`. The use case must grow an
  optional `name` parameter; when supplied, it replaces the hardcoded
  default.
- **Grouped thresholding entry** —
  `src/percell4/gui/grouped_seg_panel.py:196-278` (`_on_run`).
  Auto-derived name at line 241 plus the inline 3-way Yes/No/Cancel
  collision dialog at lines 242-257 — both to be replaced by the
  shared helper.
- **`ThresholdQCController(mask_name=...)`** —
  `src/percell4/gui/threshold_qc.py:87, 101`. Already accepts `mask_name`
  via constructor; just thread the prompted name in.
- **QInputDialog already imported** in
  `interfaces/gui/main_window.py:26`, `task_panels/data_panel.py:17`,
  and `peer_views/phasor_plot.py:25` — established convention.

### Institutional Learnings

- **`docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`** —
  this work doesn't introduce a new user-edit widget needing a signal
  wire (we use the modal `QInputDialog.getText` which is fully blocking),
  but the convention's *spirit* (every interactive widget needs its
  consumer wired) shapes the helper signature: it returns the chosen
  name synchronously, so there's nothing to wire.
- **`docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`** —
  the exact drift class this plan preempts. Three call sites doing the
  same naming flow → extract once, consume three times. Don't paste the
  Phasor `while True` block into Cellpose and grouped thresholding.

---

## Key Technical Decisions

- **Prompt timing — *up front, not after the Worker completes*.** The
  user said "after clicking Run Cellpose, have a window pop up". Prompt
  in `_on_run_cellpose` after the channel/viewer checks but before the
  Worker starts. Cancel aborts cheaply; no wasted Cellpose run. The
  name is stored on `self._cellpose_pending_name` and threaded through
  `_on_cellpose_done` → `SegmentCells.finalize(..., name=...)`.
  Same pattern for grouped: prompt at the top of `_on_run`, store name,
  thread through to the existing `ThresholdQCController(mask_name=...)`.
- **Default name for grouped thresholding is `"grouped"`, not
  `f"grouped_{channel}_{metric}"`.** The user said "Make 'cellpose' the
  default" — short and obvious. Symmetry with Cellpose argues for
  `"grouped"`. The auto-derived long form was useful as a *unique*
  default to avoid collisions; the helper's refuse-and-re-prompt now
  handles collisions explicitly, so the short default is fine.
- **`SegmentCells.finalize` grows an optional `name: str | None = None`
  parameter.** When `None`, falls back to the existing `f"cellpose_{n_cells}"`
  heuristic so any non-GUI caller (workflows runner, tests) keeps
  working. The GUI always passes a non-None value.
- **Helper lives in `src/percell4/gui/_resource_name_prompt.py`** —
  leading underscore matches `_dialog_utils.py` and
  `_stitching_flim_form.py` (private to `gui/`). Pure function returning
  `str | None` (None on cancel).
- **Helper does NOT know about the resource kind.** It takes
  `(parent, title, default, existing_names)` and returns the chosen
  name or `None`. Callers supply the kind-specific defaults and the
  collision-set lookup. Keeps the helper Qt-only with no domain coupling.
- **Refactor phasor first (U1), add to Cellpose (U2), add to grouped
  (U3).** Refactoring phasor first is a behavior-preserving change that
  proves the helper against the canonical reference. Cellpose and
  grouped then consume the proven helper.

---

## Open Questions

### Resolved During Planning

- **Where to put the helper?** `src/percell4/gui/_resource_name_prompt.py` — see Key Technical Decisions.
- **Should the helper handle the "empty mask warning" too?** No. That's an
  Apply-Phasor-specific check (`int(binary.sum()) == 0`) and not relevant
  to Cellpose or grouped. Keep it inline at the phasor call site.
- **Default for grouped: `"grouped"` or `f"grouped_{channel}_{metric}"`?**
  `"grouped"`. See Key Technical Decisions.
- **Should `SegmentCells.finalize` *require* a name?** No. Optional with
  fallback preserves non-GUI callers (e.g., workflow runners) that
  pass `None` and accept the auto-derived name.

### Deferred to Implementation

- **Exact `existing_names` lookup for Cellpose** — `store.list_labels()`
  returns both segmentation and mask groups; the segmentation set is
  `list_labels()` minus `list_masks()`. The implementer should mirror
  what `SegmentCells.finalize` already does at lines 102-104.
- **Whether the QInputDialog inherits the right modal parent** —
  for Cellpose / grouped, `self` (the panel) is the natural parent. The
  Phasor flow passes `self` too. No expected difference, but verify
  modality during implementation.

---

## Implementation Units

- U1. **Extract shared name-prompt helper; refactor phasor flow**

**Goal:** Create the reusable name-prompt helper and prove it by
replacing the inline `while True` block in
`_on_apply_current_phasor_as_mask`. No user-visible behavior change.

**Requirements:** R3, R4, R5, R6.

**Dependencies:** None.

**Files:**
- Create: `src/percell4/gui/_resource_name_prompt.py`
- Modify: `src/percell4/interfaces/gui/peer_views/phasor_plot.py`
- Test: `tests/test_gui/test_resource_name_prompt.py`

**Approach:**
- Helper signature: `prompt_for_resource_name(parent, *, title, label, default, existing_names) -> str | None`. Returns the chosen name (validated non-empty and not in `existing_names`), or `None` if the user cancelled.
- Implementation mirrors `phasor_plot.py:1876-1898`: `while True` loop with `QInputDialog.getText`, empty-name re-prompt with original default, collision warn (`QMessageBox.warning`) with the conflicting name as new default, cancel returns `None`.
- Refactor `_on_apply_current_phasor_as_mask` to call the helper. The empty-mask warning at lines 1900-1909 stays inline (out of scope per Open Questions).

**Patterns to follow:**
- `phasor_plot.py:_on_apply_current_phasor_as_mask` lines 1876-1898 — port verbatim into the helper, then delete from the original call site.
- Naming convention: `_resource_name_prompt.py` matches `_dialog_utils.py`, `_stitching_flim_form.py` (private to `gui/`).

**Test scenarios:**
- Happy path: helper returns the typed name when the input passes validation. Use `monkeypatch` to stub `QInputDialog.getText` to return `("my_mask", True)`.
- Edge case: empty name on first attempt, valid name on second → helper re-prompts and returns the second value.
- Edge case: collision on first attempt, fresh name on second → helper warns and re-prompts; returns the second value. Verify the second prompt's default is the *colliding* name (per R4).
- Error path: user clicks Cancel → helper returns `None`.
- Edge case: whitespace-only name (e.g., `"   "`) is treated as empty per `strip()`.
- Integration: phasor flow after refactor — Apply Current Phasor as Mask with a fresh name still emits `phasor_mask_applied`. Cancel still aborts. Existing `tests/test_gui/test_phasor_*` (if any cover this path) continue to pass.

**Verification:**
- Helper is callable from any Qt context without circular imports.
- Phasor flow behavior is byte-identical to today on happy/cancel/collision paths.

---

- U2. **Prompt for name in Run Cellpose flow**

**Goal:** Prompt up front in `_on_run_cellpose`. Default `"cellpose"`,
refuse-and-re-prompt on collision with any existing
`/labels/<name>` segmentation. Thread the chosen name through to
`SegmentCells.finalize` and out to the napari labels-layer name.

**Requirements:** R1, R3, R4, R5.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/application/use_cases/segment_cells.py` (add optional `name` to `finalize`)
- Modify: `src/percell4/gui/segmentation_panel.py` (prompt up front; thread name through)
- Test: `tests/test_gui/test_segmentation_panel_cellpose_name_prompt.py`

**Approach:**
- In `_on_run_cellpose`, after the channel/viewer checks (line 408 is fine; before the `image = active_layer.data` line on 410), look up the existing segmentation names via the same `list_labels() - list_masks()` pattern `SegmentCells.finalize` uses internally. Call the helper with `default="cellpose"`. On cancel, return early (no Cellpose run starts).
- Stash the chosen name on `self._cellpose_pending_name`.
- In `_on_cellpose_done`, pass the stashed name to `uc.finalize(masks, name=..., remove_edge_cells=...)`.
- In `SegmentCells.finalize`, add `name: str | None = None`. When `None`, fall back to `f"cellpose_{n_cells}"`. Otherwise use the supplied name.
- The napari `add_labels(result.labels, name=result.seg_name)` call at `segmentation_panel.py:456` is unchanged — `seg_name` already reflects whatever `finalize` chose.

**Patterns to follow:**
- `phasor_plot.py:_on_apply_current_phasor_as_mask` for the prompt-then-emit flow.
- `SegmentCells.finalize`'s existing `set_active_segmentation` + `refresh_resource_lists` sequence — preserved.

**Test scenarios:**
- Happy path: stub `prompt_for_resource_name` to return `"my_cells"`; Cellpose worker completes with synthetic masks; assert `/labels/my_cells` was written and `session.active_segmentation == "my_cells"`.
- Happy path with default: stub helper to return `"cellpose"`; assert the segmentation lands at `"cellpose"` (verifying the default works).
- Edge case: helper returns `None` (user cancelled); assert Worker is never instantiated, no `/labels/<x>` write happens, status bar shows nothing alarming.
- Error path: `_on_cellpose_done` runs after a real Worker; `finalize` receives the threaded name and writes it.
- Integration: covers R1 + R4 — pre-existing segmentation named `"cellpose"`, helper called with that as default → simulated collision returns `"cellpose_2"`; verify only `/labels/cellpose_2` is written and the original `"cellpose"` is untouched.
- Unit (use case): `SegmentCells.finalize(masks, name=None)` falls back to `f"cellpose_{n_cells}"`. `finalize(masks, name="explicit")` uses the explicit name. Use existing fixtures in `tests/test_use_cases.py` if present, else add coverage.

**Verification:**
- `_on_run_cellpose` aborts cleanly on cancel — no Worker started, no UI thread blocked.
- After a real Cellpose run, the labels layer in napari and the `/labels/<name>` group in HDF5 both reflect the user-typed name.

---

- U3. **Prompt for name in grouped thresholding `_on_run`**

**Goal:** Replace the auto-derived `mask_name` (and its inline 3-way
Yes/No/Cancel collision dialog) with the shared helper. Default
`"grouped"`. Thread the chosen name through to the existing
`ThresholdQCController(mask_name=...)` flow.

**Requirements:** R2, R3, R4, R5.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/gui/grouped_seg_panel.py`
- Test: `tests/test_gui/test_grouped_seg_panel_name_prompt.py`

**Approach:**
- In `_on_run`, after the channel/segmentation/image checks (line 238 is fine; before line 241 where the auto-derived name lives), call the helper with `default="grouped"` and `existing_names=store.list_masks()`.
- On cancel, return early.
- Delete the old auto-derive block (line 241) and the inline 3-way collision dialog (lines 242-257).
- Continue with the existing flow: `_auto_measure_then_group` / `_run_grouping` / `_on_grouping_done` → `ThresholdQCController(..., mask_name=mask_name, ...)`. No changes downstream.

**Patterns to follow:**
- Same call shape as U2 — the helper does all the validation, the panel just stashes the result.

**Test scenarios:**
- Happy path: stub helper to return `"my_grouped"`; grouped pipeline completes; assert `ThresholdQCController` was instantiated with `mask_name="my_grouped"`.
- Edge case: helper returns `None` (user cancelled); assert measurement worker and grouping worker are NOT started.
- Integration: covers R2 + R4 — pre-existing mask `"grouped"` in `store.list_masks()`; helper simulated to choose `"grouped_v2"`; verify the panel proceeds with `"grouped_v2"`, never starts a write under `"grouped"`.
- Edge case: covers R3 — empty name → helper handles re-prompt internally (covered by U1 tests); panel test only needs to verify the panel calls the helper and uses its result.

**Verification:**
- The inline `QMessageBox.question` Yes/No/Cancel overwrite dialog is gone from `grouped_seg_panel.py`.
- The user-typed name appears in the ThresholdQC window title (`"Group Preview — <name>"`) and the final status message ("Mask saved as '<name>'.").

---

## System-Wide Impact

- **Interaction graph:** New helper consumed by three call sites. No new signals, no new threads, no new modal stacking concerns (each prompt is a single blocking `QInputDialog`).
- **API surface parity:** `SegmentCells.finalize` grows an optional kwarg with a back-compat default. Existing callers (workflow runner, tests) keep working unchanged.
- **Error propagation:** Cancel returns `None` from the helper; each caller treats it as "user aborted, return early". No exceptions raised.
- **State lifecycle risks:** None new — write-then-auto-select sequence is unchanged in both surfaces.
- **Integration coverage:** Three call sites use the same helper; the U1 tests cover the helper's behavior end-to-end (empty, collision, cancel, happy), and the U2/U3 tests cover the panel-side wiring (helper called → name threaded through).
- **Unchanged invariants:** Apply Current Phasor as Mask user-facing behavior is preserved; the `phasor_mask_applied` signal shape is unchanged; non-GUI Cellpose callers via `SegmentCells.finalize` still get the auto-derived name when they don't supply one.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Refactoring the phasor flow accidentally changes its behavior. | U1 ports the `while True` block verbatim into the helper and verifies via the existing phasor test surface plus the helper unit tests. Any drift fails both. |
| `_on_run_cellpose` runs a long Worker and the user accidentally double-clicks the run button while the prompt is showing. | `QInputDialog.getText` is application-modal — Qt blocks further button presses while the dialog is open. The existing pattern in phasor_plot.py already relies on this. |
| `SegmentCells.finalize` is called from non-GUI code paths with a name that happens to collide with an existing segmentation. | Optional kwarg has a back-compat default that uses the safe auto-derived name. The collision-refuse behavior lives in the GUI helper, not the use case — the use case still trusts its caller. |
| Helper grows accidental coupling to specific resource kinds. | Helper takes `existing_names` as a plain `Iterable[str]` from the caller. No knowledge of `/labels/`, `/masks/`, etc. Reviewed in U1. |

---

## Documentation / Operational Notes

- Update `docs/audits/gui-element-classification.yaml` to classify the
  Run Cellpose and grouped thresholding "Run" buttons explicitly as
  Creators (they write new `/labels/<name>` and `/masks/<name>`
  resources). The current entries should be reviewed during U2/U3 —
  per the existing GUI Action Contract pattern, Creators are widgets
  that write a new resource and auto-select via session.
- No user-facing docs in this codebase; the new prompts are
  self-explanatory.
- No migration, rollout, or feature flag — additive UX behind existing
  buttons.

---

## Sources & References

- Reference pattern: `src/percell4/interfaces/gui/peer_views/phasor_plot.py:1855-1911`
- Cellpose entry: `src/percell4/gui/segmentation_panel.py:385-456`
- Cellpose use case: `src/percell4/application/use_cases/segment_cells.py:73-113`
- Grouped entry: `src/percell4/gui/grouped_seg_panel.py:196-278`
- Grouped name consumer: `src/percell4/gui/threshold_qc.py:87-773`
- Related convention: `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`
- Related pattern: `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
