---
audit_unit: U3
date: 2026-05-01
inputs: docs/audits/gui-element-classification.yaml
deliverable_of_plan: docs/plans/2026-05-01-refactor-gui-state-handling-audit-plan.md
---

# Session Mutation Graph

Every code path that writes any of the five session selection fields, with
classification under the Selector / Creator / Action / Lifecycle taxonomy
and verdict against Invariant I1.

Scope: writers of `session.active_channel`, `session.active_segmentation`,
`session.active_mask`, `session.filter_ids`, and `session.selection`.

## Classification rules (recap)

- **Selector** — UI element whose explicit purpose is to let the user pick
  this field. Permitted under I1.
- **Creator** — UI flow that creates a new resource and auto-selects it.
  Permitted under OQ-2 with per-slot emissions; the Creator-cleanup variant
  (rename / delete that updates the active slot to the new shape, e.g.
  `set_active_channel(None)` after channel delete) is the same shape and
  permitted.
- **Action** — UI element that does something else and writes session as a
  side effect. **Forbidden under I1.**
- **Lifecycle handler** — non-UI, code-path-internal write that happens as
  part of the session lifecycle (e.g., `Session.set_dataset`'s reset writes;
  `Session.set_measurements`'s pruning of stale `filter_ids` and
  `selection`). Permitted because the write is not the result of a
  Selector/Creator/Action invocation.

The CLI is out of audit scope; CLI writers are listed in the per-field
tables for completeness but excluded from the I1 verdict.

## Summary

| Field | Total writers | Selector | Creator | Action | Lifecycle | I1 violations |
|---|---|---|---|---|---|---|
| `active_channel`        | 5  | 1 | 2 | 0 | 2 | 0 |
| `active_segmentation`   | 8  | 1 | 4 | 1 | 2 | 1 (shared) |
| `active_mask`           | 8  | 1 | 3 | 2 | 2 | 2 |
| `filter_ids`            | 5  | 2 | 0 | 0 | 3 | 0 |
| `selection`             | 13 | 9 | 0 | 0 | 4 | 0 |

Two distinct I1 violations exist (one writer corrupts `active_mask` only;
the other writes both `active_mask` and `active_segmentation` — counted
once per affected field in the table above, hence "2" in the `active_mask`
column and "1 (shared)" in the `active_segmentation` column). Both have
fix units already in the plan (U8 for Bug A; U9 for the napari → session
coupling). No new todos are filed in this unit.

## Writers by field

### `session.active_channel`

| File:line | Caller | YAML id | Class | Verdict | Notes |
|---|---|---|---|---|---|
| `src/percell4/interfaces/gui/task_panels/data_panel.py:186` | `_on_active_channel_combo_changed` (combo change) | `data_panel.active_channel_combo` | Selector | Compliant | Canonical Selector for `active_channel` (Data tab combo). |
| `src/percell4/interfaces/gui/task_panels/data_panel.py:409` | `_on_rename_channel` (rename button) | `data_panel.rename_channel_button` | Creator (rename-cleanup) | Compliant | See "Borderline: rename_channel_button" below. |
| `src/percell4/interfaces/gui/task_panels/data_panel.py:546` | `_on_delete_channel` (delete button) | `data_panel.delete_channel_button` | Creator (delete-cleanup) | Compliant | See "Borderline: delete_channel_button" below. |
| `src/percell4/application/session.py:153` | `Session.set_dataset` (auto-select first channel on load) | (n/a — application layer) | Lifecycle handler | Compliant | Auto-selection on dataset load; emits `ACTIVE_CHANNEL_CHANGED` implicitly via subsequent `DATASET_CHANGED` re-population. |
| `src/percell4/application/session.py:215` | `Session.clear` (close dataset) | (n/a — application layer) | Lifecycle handler | Compliant | Resets to `None` on close. |
| `src/percell4/interfaces/cli/run_pipeline.py` (no writes) | — | — | — | — | CLI does not write `active_channel`. |

### `session.active_segmentation`

| File:line | Caller | YAML id | Class | Verdict | Notes |
|---|---|---|---|---|---|
| `src/percell4/interfaces/gui/task_panels/data_panel.py:178` | `_on_active_seg_combo_changed` | `data_panel.active_seg_combo` | Selector | Compliant | Canonical Selector for `active_segmentation` (Data tab combo). |
| `src/percell4/application/use_cases/segment_cells.py:95` | `SegmentCells` use case (auto-select after Cellpose) | `segmentation_panel.run_cellpose_button` | Creator | Compliant | Writes new `/labels/<name>` resource and auto-selects per OQ-2. |
| `src/percell4/gui/segmentation_panel.py:335` | `_on_create_empty_labels` | `segmentation_panel.create_empty_labels_button` | Creator | Compliant | Auto-selects new in-memory `manual` labels layer; HDF5 persistence deferred to Save Labels. |
| `src/percell4/gui/add_layer_dialog.py:637` | ROI tab `_on_import_roi` | `add_layer.roi_tab.import_button` | Creator | Compliant | Imports ImageJ ROI → labels resource; auto-selects. |
| `src/percell4/gui/add_layer_dialog.py:700` | Cellpose-import tab `_on_import_cellpose` | `add_layer.cellpose_tab.import_button` | Creator | Compliant | Imports external Cellpose labels; auto-selects. |
| `src/percell4/interfaces/gui/main_window.py:951, 965` | `_sync_active_layers_from_viewer` (napari → session) | `main_window.events_active_closure` | Action | **I1 VIOLATION** | Bug class: napari layer-list event coupling. Fixed in U9. |
| `src/percell4/application/session.py:143, 213` | `Session.set_dataset` / `Session.clear` (reset writes) | (n/a — application layer) | Lifecycle handler | Compliant | Resets to `None`; emits `ACTIVE_SEGMENTATION_CHANGED` per per-slot rule (`session.py:159`). |
| `src/percell4/interfaces/cli/run_pipeline.py:119, 129` | CLI batch driver | (n/a — CLI) | (out of GUI scope) | n/a | CLI sets active segmentation programmatically during scripted pipelines. |

### `session.active_mask`

| File:line | Caller | YAML id | Class | Verdict | Notes |
|---|---|---|---|---|---|
| `src/percell4/interfaces/gui/task_panels/data_panel.py:182` | `_on_active_mask_combo_changed` | `data_panel.active_mask_combo` | Selector | Compliant | Canonical Selector for `active_mask` (Data tab combo). |
| `src/percell4/application/use_cases/accept_threshold.py:70` | `AcceptThreshold` use case (single-cell threshold accept) | `analysis_panel.accept_threshold_button` | Creator | Compliant | Writes new `/masks/<name>` resource and auto-selects per OQ-2. |
| `src/percell4/gui/threshold_qc.py:760` | `_finalize` (grouped threshold completion) | `grouped_seg_panel.run_button` | Creator | Compliant | Combined per-group masks → single `/masks/<name>` resource; auto-selects after the multi-group flow. Cross-linked to `grouped_seg_panel.run_button`. |
| `src/percell4/interfaces/gui/main_window.py:1083` | `_on_phasor_mask_applied` (Apply ROIs as Masks handler) | `phasor_plot.apply_rois_as_masks_button` | Creator | Compliant | Writes one mask per ROI; auto-selects the last one. Per OQ-2 per-slot emission. Button renamed 2026-05-06 from "Apply Visible as Mask"; handler/signal names unchanged. |
| `src/percell4/interfaces/gui/peer_views/phasor_plot.py:360` | `_on_remove_roi` (Remove button) | `phasor_plot.remove_roi_button` | Action | **I1 VIOLATION (Bug A)** | Off-label `set_active_mask(None)` write inside ROI Remove. Fixed in U8. |
| `src/percell4/interfaces/gui/main_window.py:947, 961` | `_sync_active_layers_from_viewer` (napari → session) | `main_window.events_active_closure` | Action | **I1 VIOLATION** | Same closure as the segmentation violation above; counted once in the summary table. Fixed in U9. |
| `src/percell4/application/session.py:144, 214` | `Session.set_dataset` / `Session.clear` | (n/a — application layer) | Lifecycle handler | Compliant | Reset writes; per-slot emission at `session.py:161`. |

### `session.filter_ids`

| File:line | Caller | YAML id | Class | Verdict | Notes |
|---|---|---|---|---|---|
| `src/percell4/interfaces/gui/task_panels/analysis_panel.py:294` | `_on_filter_to_selection` (Filter to Selection button) | `analysis_panel.filter_to_selection_button` | Selector | Compliant | Reclassified as Selector for `filter_ids` per Key Technical Decision "I1 scope extension". See "Borderline: filter / clear-filter buttons" below. |
| `src/percell4/interfaces/gui/task_panels/analysis_panel.py:297` | `_on_clear_filter` (Clear Filter button) | `analysis_panel.clear_filter_button` | Selector | Compliant | Same reclassification rationale. |
| `src/percell4/application/session.py:146, 180, 217` | `Session.set_dataset` (reset) / `Session.set_measurements` (prune stale filter) / `Session.clear` | (n/a — application layer) | Lifecycle handler | Compliant | Three non-UI-driven writes triggered by lifecycle events (dataset switch, measurement update, dataset close). Permitted because they're internal to Session, not the result of a UI invocation. |
| (no other GUI writers) | — | — | — | — | The Filter/Clear-Filter pair are the only GUI-side writers. |

### `session.selection`

| File:line | Caller | YAML id | Class | Verdict | Notes |
|---|---|---|---|---|---|
| `src/percell4/interfaces/gui/task_panels/analysis_panel.py:287` | `_on_clear_selection` | `analysis_panel.clear_selection_button` | Selector | Compliant | Explicit Clear-Selection button. |
| `src/percell4/interfaces/gui/peer_views/data_plot.py:314, 316` | `_on_scatter_clicked` (left-click and Ctrl-click toggle) | `data_plot.scatter_point_click` | Selector | Compliant | Scatter plot click → set selection. |
| `src/percell4/interfaces/gui/peer_views/data_plot.py:330` | `_on_rect_selected` (Shift+drag rect select) | `data_plot.shift_drag_rect_select` | Selector | Compliant | Rect-select → set selection. |
| `src/percell4/interfaces/gui/peer_views/data_plot.py:339` | `eventFilter` Esc handler | `data_plot.escape_clear_selection` | Selector | Compliant | Esc clears selection. |
| `src/percell4/interfaces/gui/peer_views/cell_table.py:332` | `_on_table_selection_changed` | `cell_table.row_selection` | Selector | Compliant | Cell-table row click → set selection. |
| `src/percell4/gui/viewer.py:268, 270` | `_on_label_selected` (canvas click) | `viewer.canvas_click_label_selection` | Selector | Compliant | Sole napari-side Selector for `selection` per OQ-1: canvas mouse-callback handler, not a layer-list event. |
| `src/percell4/gui/multi_select.py:203` | `MultiLabelSelectController.accept` | `multi_select.accept_button` (also reachable via `multi_select.accept_shortcut_ctrl_return` and `multi_select.accept_shortcut_ctrl_enter`) | Selector | Compliant | Tool's commit point. Module docstring: "Domain state moves here — the one and only place." |
| `src/percell4/gui/threshold_qc.py:334` | `_on_proceed` | `threshold_qc.proceed_button` | Selector | Compliant | See "Borderline: threshold_qc.proceed_button" below. |
| `src/percell4/gui/threshold_qc.py:355` | `_on_group_select` | `threshold_qc.group_select_buttons` | Selector | Compliant | Per-group buttons → select all cells in that group. |
| `src/percell4/application/session.py:145, 182, 188, 216` | `Session.set_dataset` (reset) / `Session.set_measurements` (prune) / `Session.set_filter` (clears `selection` as a co-emit when a filter is applied) / `Session.clear` | (n/a — application layer) | Lifecycle handler | Compliant | Four non-UI-driven writes triggered by lifecycle events. The set_filter co-emit (line 188) is the side effect of applying a filter (the prior selection is no longer meaningful in the new filtered view); per-slot emission at `session.py:190` ensures `selection` subscribers see the reset. The `_selection = ids` assignment at line 170 inside `set_selection` is the Selector-originated write and is captured under the relevant Selector rows above, not here. |

## Borderline classifications (settled here)

### `data_panel.delete_channel_button` — Creator-cleanup

**Verdict.** Classified as **Creator (delete-cleanup)**, *not* Action.
Cleared by I1.

**Rationale.** Channel delete is functionally a Creator that *removes* a
resource: it deletes the channel from the HDF5 store, removes the napari
layer, and prunes the in-memory `channel_names` list. Setting
`session.active_channel = None` when the just-deleted channel was the
active one (line 545-546) is a legitimate per-slot emission that prevents
the rest of the system from holding a dangling reference to a non-existent
resource. This is structurally identical to the 7-step transactional
pattern documented in
`docs/solutions/architecture-patterns/channel-deletion-permanence.md`.
The `if session.active_channel == name:` guard at line 545 means the write
only fires when needed, which is the correct shape for delete-cleanup.

The `data_panel.delete_seg_button` (`data_panel.py:343-371`) and
`data_panel.delete_mask_button` (same `_on_delete_layer` handler) do
*not* perform analogous active-slot cleanup today; the YAML notes flag
this as a minor staleness candidate. It is not an I1 violation (no
`active_*` write happens), but it is a parallel gap. Not in scope of this
unit; documented in the YAML notes for those entries.

### `data_panel.rename_channel_button` — Creator-cleanup

**Verdict.** Classified as **Creator (rename-cleanup)**, *not* Action.
Cleared by I1.

**Rationale.** Rename writes a new resource shape (the channel keys in
`metadata` and the napari layer name change). Updating
`session.active_channel` to the new name when the renamed channel was the
active one (line 408-409) is the per-slot emission that preserves the
user's expressed intent across the rename. Without this write, the active
channel would point at a key that no longer exists. The
`if session.active_channel == old_name:` guard means the write only fires
when needed.

### `analysis_panel.filter_to_selection_button` and `analysis_panel.clear_filter_button` — Selectors for `filter_ids`

**Verdict.** Classified as **Selectors for `filter_ids`**, per the plan's
Key Technical Decision "I1 scope extension". Cleared by I1.

**Rationale.** The plan explicitly extends I1 to cover all five session
selection fields. Filter / Clear-Filter buttons' stated purpose is to
set / clear `filter_ids` — that is the textbook Selector contract for
this field. The Filter button reads `selected_ids` only as the *source of
the operand* it is selecting on; it does not mutate `selection`. Clear
Filter is even more explicit (writes `None`, which is the canonical
"empty filter" value).

### `gui/threshold_qc.proceed_button` — Selector for `selection`

**Verdict.** Classified as **Selector for `selection`**. Cleared by I1.

**Rationale.** `_on_proceed` calls `data_model.set_selection([])` at line
334 to clear histogram selections before transitioning to per-group
thresholding phase. Clearing `selection` is the explicit user intent
expressed by clicking Proceed (the histogram is dismissed; per-group QC
is the next phase, which has its own per-group selection model). Writing
the empty frozenset is the Selector's contract.

### Creators of derived/auxiliary resources — `compute_phasor_button`, `apply_wavelet_button`, `compute_lifetime_button`, `measure_cells_button`, `analyze_particles_button`

**Verdict.** Classified as **Creators of derived/auxiliary resources**.
Cleared by I1 *because they do not write any of the five session
selection fields.*

**Rationale.** These buttons all write *new* HDF5 resources:

- `compute_phasor_button` writes `/phasor/<channel>/{g,s}`
  (`flim/phasor.py` via the ComputePhasor use case).
- `apply_wavelet_button` writes `/phasor/<channel>/{g_filtered,s_filtered}`
  (ApplyWavelet use case).
- `compute_lifetime_button` writes `/phasor/<channel>/lifetime`
  (ComputeLifetime use case) and adds an Image layer to napari.
- `measure_cells_button` writes `/measurements` (MeasureCells use case).
- `analyze_particles_button` merges particle counts into `/measurements`
  (AnalyzeParticles use case).

None of `g`, `s`, `g_filtered`, `s_filtered`, `lifetime`, `measurements`,
or particle counts are session-selectable kinds — there is no
`session.active_phasor` or `session.active_lifetime` field. The
session-selection-tracked kinds are `channel`, `segmentation`, and `mask`
(plus the operational `selection` and `filter_ids`). These Creators
therefore write new resources but legitimately have no auto-select call
to make. They are classified Creator under the "writes new resource"
half of the rule, with the auto-select half satisfied vacuously.

This distinction is important because it explains why the YAML's
`writes:` column is empty for these entries while their `class:` is
`Creator` — the audit's `writes:` column tracks session-selection-field
writes only, not HDF5 resource writes.

## I1 violations and fix references

| Violation | YAML id | File:line | Fixed in | Todo filed |
|---|---|---|---|---|
| Phasor ROI Remove writes `active_mask = None` off-label (Bug A) | `phasor_plot.remove_roi_button` | `src/percell4/interfaces/gui/peer_views/phasor_plot.py:360` | U8 | None — already covered by U8 in the plan. |
| napari layer-list `events.active` writes `active_mask` / `active_segmentation` from napari's event loop (Bug class behind napari → session coupling, OQ-1) | `main_window.events_active_closure` | `src/percell4/interfaces/gui/main_window.py:947, 951, 961, 965` (inside `_sync_active_layers_from_viewer`, called from the closure at `:600`) | U9 | None — already covered by U9 in the plan. |

Both violations have fix units in the plan. Per the unit's ground rule
("file a todo entry for every Action-class I1 violation that requires a
code fix … that doesn't already have a fix unit"), no new todos are filed
in this unit.

## Cross-link to YAML

Every writer above maps to a YAML entry by `id` (or to an out-of-tree
application/CLI seam, which is noted as "(n/a)" because those writers are
not interactive UI elements and therefore not covered by U1/U2). The two
violations cross-link to:

- `phasor_plot.remove_roi_button` (`docs/audits/gui-element-classification.yaml:619-628`)
- `main_window.events_active_closure` (`docs/audits/gui-element-classification.yaml:1988-1997`)

## Verdict

After U8, U9, U11 land, only Selectors and Creators write any of the five
session selection fields. The mutation graph is then I1-clean.

- U8 deletes the off-label `set_active_mask(None)` from `_on_remove_roi`,
  reducing `phasor_plot.remove_roi_button` from Action (with side effect
  on `active_mask`) to Action (no session write). The button no longer
  appears in the `active_mask` writers table.
- U9 deletes the `_sync_active_layers_from_viewer()` call inside the
  napari `events.active` closure, removing the only napari → session
  edge for `active_mask` / `active_segmentation`. The closure stays
  connected for the seg-panel and grouped-seg-panel channel-label
  refreshes (no session writes from those side effects).
- U11 migrates the `M` shortcut to `Labels.bind_key` and adds a
  `multi_select_requested` Qt signal on `ViewerWindow`. This does not
  add any new writers to the five fields; the eventual
  `data_model.set_selection(...)` call at `multi_select.py:203`
  (`multi_select.accept_button`) is unchanged.

After all three land, every entry in the per-field tables above has class
in {Selector, Creator, Lifecycle handler}. The "I1 violations" column in
the summary table is zero across the board.

## Resource-list inventory updates (added 2026-05-01)

Beyond the five selection fields tracked above, the dataset-lifecycle
plan introduces a sixth axis: the *inventory* of available channels /
segmentations / masks. Three new Session events
(`CHANNEL_LIST_CHANGED`, `SEGMENTATION_LIST_CHANGED`, `MASK_LIST_CHANGED`)
fire whenever the inventory changes within the current dataset.

The central API is `Session.refresh_resource_lists(*, channel_names,
segmentation_names, mask_names)`. Each kwarg that is non-None replaces
the corresponding entry in `DatasetHandle.metadata` and emits the
matching list event. Creators call this between writing the resource
and calling `set_active_*`, so subscribers re-list before they look up
the just-written name.

Inventory writers (each fires the matching list event):

| Caller | File:line | Kind | Trigger |
|---|---|---|---|
| `Session.set_dataset` | `application/session.py:set_dataset` | all three | dataset load (always fires all three with new inventory) |
| `Session.clear` | `application/session.py:clear` | all three | dataset close (fires all three with empty inventory) |
| `SegmentCellsUseCase.finalize` | `application/use_cases/segment_cells.py:94-99` | segmentation | Cellpose finalize |
| `AcceptThresholdUseCase` | `application/use_cases/accept_threshold.py:66-72` | mask | threshold accept |
| `add_layer_dialog._write_layer` | `gui/add_layer_dialog.py:_write_layer` | per-kind | TIFF tab Channel/Segmentation/Mask write |
| `add_layer_dialog` ROI tab | `gui/add_layer_dialog.py:633-640` | segmentation | ROI .json import |
| `add_layer_dialog` cellpose tab | `gui/add_layer_dialog.py:697-704` | segmentation | `_seg.npy` import |
| `ThresholdQCController` accept | `gui/threshold_qc.py:756-765` | mask | grouped-threshold final accept |
| `LauncherWindow._on_phasor_mask_applied` | `interfaces/gui/main_window.py:1063-1069` | mask | Apply Phasor Mask |
| `DataPanel._on_rename_channel` | `interfaces/gui/task_panels/data_panel.py` | channel | channel rename (Creator-cleanup) |
| `DataPanel._on_delete_channel` | `interfaces/gui/task_panels/data_panel.py` | channel | channel delete (Creator-cleanup) |

After this inventory, every Creator that mutates the available-resource
list fires the appropriate list event. The Data tab combos and any
peer-view that subscribes re-list immediately without an app restart
(closes Anchor Bug C3 from
`docs/brainstorms/2026-05-01-dataset-lifecycle-and-resource-list-events-requirements.md`).

## 2026-05-12 — Batch TCSPC Append dialog

`BatchTCSPCDialog` (added under `feat/batch-tcspc-append`, plan
`docs/plans/2026-05-12-001-feat-batch-tcspc-append-plan.md`) **writes no
session selection field**. The dialog operates on user-picked `.h5`
files that are not loaded into the active session, so
`session.active_channel`, `active_segmentation`, `active_mask`,
`filter_ids`, and `selection` are all untouched. It also does not call
`Session.refresh_resource_lists` — by design, since the dialog never
mutates the active dataset's resource inventory.

The dialog **does** defensively update `session.dataset.metadata` in
place after each successful per-item append, but only when
`session.dataset.path == item.h5_path`. This is the documented defense
against the h5py library-level metadata cache (see
`docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`),
not a session-selection mutation: the frozen `DatasetHandle` is unchanged;
only its `metadata` dict — which is a mutable field on the frozen
dataclass — is updated in place.

No new edges in this mutation graph.
