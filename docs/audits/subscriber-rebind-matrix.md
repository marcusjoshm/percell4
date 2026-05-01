---
audit_unit: U4
date: 2026-05-01
deliverable_of_plan: docs/plans/2026-05-01-refactor-gui-state-handling-audit-plan.md
---

# Subscriber Rebind Matrix

Every component that reads session-derived data, with the events it must
respond to and the gaps that need closing.

The matrix anchors Bug A's stale-snapshot symptoms (#4 Selected ROI panel
and #5 status bar). Both are subscriber-side rebind failures inside
`PhasorPlotWindow`, not contract violations of `_on_remove_roi`. They
appear here as the two "fix needed" rows; both are scheduled for U8.

## Summary

- Total subscribers: **9**
  - 5 connect via Qt signal `CellDataModel.state_changed`
  - 3 connect via `Session.subscribe` (Qt-free pub/sub)
  - 1 (`CellDataModel` itself) is a Session→Qt bridge subscriber and is
    not a UI element; it is included for completeness because it is the
    forwarding hub.
- Channel breakdown:
  - `state_changed` (Qt signal):
    `LauncherWindow`, `ViewerWindow`, `SegmentationPanel`, `DataPanel`,
    `AnalysisPanel`.
  - `Session.subscribe` (Qt-free): `PhasorPlotWindow`, `DataPlotWindow`,
    `CellTableWindow`.
  - Bridge: `CellDataModel`.
- Subscribers with stale-cache risk: **3**
  (`PhasorPlotWindow`, `DataPlotWindow`, `CellTableWindow`).
- Subscribers with rebind gaps: **1** subscriber, **2** discrete gaps —
  `PhasorPlotWindow` Selected ROI panel and status bar do not rebind on
  ROI removal.

## Matrix

Path columns are repo-relative. The `Caches` column lists *session-derived*
caches the subscriber holds across mutations (per Pattern 4/5 of
`docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`
and Vector 4 of `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`).
"None" means the subscriber reads fresh from session on every event.

| # | Subscriber | File | Class | Handler | Channel | Reads | Responds to flags/events | Caches | Currently correct? | Fix needed |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | CellDataModel (Session→Qt bridge) | `src/percell4/model.py` | `CellDataModel` | `_on_dataset_changed`, `_on_measurements_updated`, `_on_selection_changed`, `_on_filter_changed`, `_on_segmentation_changed`, `_on_mask_changed`, `_on_channel_changed` (lines 71-100) | `Session.subscribe` (forwards to Qt `state_changed`) | none — pure forwarder | `DATASET_CHANGED`, `MEASUREMENTS_UPDATED`, `SELECTION_CHANGED`, `FILTER_CHANGED`, `ACTIVE_SEGMENTATION_CHANGED`, `ACTIVE_MASK_CHANGED`, `ACTIVE_CHANNEL_CHANGED` (`model.py:54-62`) | None | Yes — every Session event has a handler that re-emits a `StateChange`. `_wiring_session` guard prevents re-entrant emits. | No |
| 2 | LauncherWindow | `src/percell4/interfaces/gui/main_window.py` | `LauncherWindow` | `_on_state_changed` (line 1028-1032) | `state_changed` | none — handler is a documented no-op | All flags (signature accepts every `StateChange`) | None | Yes — handler delegates to panel-level subscribers; the empty body is intentional. Per the plan U9 keeps the launcher decoupled from active-layer changes. | No |
| 3 | ViewerWindow | `src/percell4/gui/viewer.py` | `ViewerWindow` | `_on_state_changed` (line 274-279); `_update_label_display` (line 281-351) | `state_changed` | `data_model.selected_ids`, `data_model.filtered_ids` — read fresh from model on every event (no per-event snapshot) | `change.filter`, `change.selection` (line 278) | `_original_colormaps` dict (per layer name, lines 353-360) — caches the *napari layer's* original colormap so it can be restored when filter/selection clears. Not session-derived; restored from napari layer state. `_is_originator` re-entrancy flag. | Yes — reads selection/filter live. The `_original_colormaps` cache is keyed by layer name and restored when both filter and selection are empty (line 303-308); it is not invalidated by session events because it tracks napari state, not session state. | No (but U10 will extend this handler with `change.mask` and `change.segmentation` branches; the existing rebind contract is intact.) |
| 4 | SegmentationPanel | `src/percell4/gui/segmentation_panel.py` | `SegmentationPanel` | `_on_state_changed` (line 52-54) | `state_changed` | `session.active_channel` (read inside `update_channel_label`) | `change.data`, `change.channel` (line 53) | None — `update_channel_label` reads fresh from session every time | Yes | No |
| 5 | DataPanel | `src/percell4/interfaces/gui/task_panels/data_panel.py` | `DataPanel` | `_on_state_changed` (line 164-172); `_on_model_active_seg_changed`, `_on_model_active_mask_changed` (lines 188-206) | `state_changed` | `data_model.active_segmentation`, `data_model.active_mask`, channel list from store/viewer | `change.segmentation`, `change.mask`, `change.data` (lines 165, 168, 171) | None — combos rebuilt from store on each refresh; uses `blockSignals` to break feedback loops (canonical Selector pattern, see `data_panel.py:198-204`) | Yes — three flags handled, fresh reads from session, blockSignals on combo updates. **Gap shape:** does *not* respond to `change.channel` for the `_active_channel_combo`; combo is populated via `change.data`. Acceptable today because `set_active_channel` is only emitted alongside dataset/data changes; flagged for follow-up if that ever becomes false. | No |
| 6 | AnalysisPanel | `src/percell4/interfaces/gui/task_panels/analysis_panel.py` | `AnalysisPanel` | `_on_state_changed` (line 278-282); `_on_filter_state_changed` (line 299-313) | `state_changed` | `data_model.is_filtered`, `data_model.filtered_df`, `data_model.df`, `data_model.session.active_channel` | `change.filter`, `change.data`, `change.channel` (lines 279, 281) | None — counts/labels read fresh from session on each event | Yes | No |
| 7 | DataPlotWindow | `src/percell4/interfaces/gui/peer_views/data_plot.py` | `DataPlotWindow` | `_on_data_changed` (line 161-164), `_on_filter_changed` (line 166-168), `_on_selection_changed` (line 170-172) | `Session.subscribe` (lines 149-152) | `session.df`, `session.filtered_df`, `session.selected_ids` | `MEASUREMENTS_UPDATED`, `DATASET_CHANGED`, `FILTER_CHANGED`, `SELECTION_CHANGED` | `_x_data`, `_y_data`, `_labels_array` numpy caches (line 71-73). Refilled in `_refresh_plot` (line 254-294). | Yes — `_refresh_plot` rebuilds the three caches from `session.filtered_df` on every `MEASUREMENTS_UPDATED`/`DATASET_CHANGED`/`FILTER_CHANGED` event and on column-combo changes. `_update_selection_highlights` reads `session.selected_ids` live. | No |
| 8 | CellTableWindow | `src/percell4/interfaces/gui/peer_views/cell_table.py` | `CellTableWindow` | `_on_data_changed` (line 214-221), `_on_filter_changed` (line 223-226), `_on_selection_changed` (line 228-231) | `Session.subscribe` (lines 203-206) | `session.df`, `session.filter_ids`, `session.selected_ids`, `session.is_filtered` | `MEASUREMENTS_UPDATED`, `DATASET_CHANGED`, `FILTER_CHANGED`, `SELECTION_CHANGED` | `PandasTableModel._df` and `_label_to_row` map (lines 38-49). `FilterableProxyModel._visible_labels` (line 114). Both rebound via `set_dataframe` and `set_filter_labels`. | Yes — `_reload_table_data` replaces the model's DataFrame on data events; `_apply_filter` flushes the proxy filter on filter events. `_is_originator` flag prevents Qt selection-feedback loops. | No |
| 9 | PhasorPlotWindow | `src/percell4/interfaces/gui/peer_views/phasor_plot.py` | `PhasorPlotWindow` | `_on_filter_changed` (line 627-629), `_on_active_mask_changed` (line 631-651) | `Session.subscribe` (lines 163-166) | `session.filter_ids`, `session.active_mask`, `session.dataset`, `session.active_channel` | `FILTER_CHANGED`, `ACTIVE_MASK_CHANGED` | `_active_mask_array`, `_active_mask_flat` (lines 141-142); per-`_ROIWidget.cached_mask` (line 98); `_g_map`, `_s_map`, `_g_map_unfiltered`, `_s_map_unfiltered` (lines 129-133); `_labels`, `_labels_flat` (lines 134-135); `_total_valid_pixels` (line 136); `_preview_colormap` (line 147); `_colormap_dirty` (line 146) | **Partial.** ACTIVE_MASK and FILTER caches rebind correctly. Selected ROI panel widgets (`_name_edit`, `_angle_spin`, `_vis_check`) and the `_status` bar do **not** rebind when an ROI is removed via `_on_remove_roi` (lines 349-362) — they retain the removed ROI's text and stats. | **Yes** — both gaps fixed in U8 (see Verdict table below). |

## Stale-cache risk audit

The three peer views that hold session-derived numpy/state caches across
mutations:

### PhasorPlotWindow (`peer_views/phasor_plot.py`)

- `_active_mask_array`, `_active_mask_flat` — **rebinds correctly.**
  - On `ACTIVE_MASK_CHANGED` (`_on_active_mask_changed:631-651`):
    cleared to `None` on every event; the next `_load_active_mask_flat`
    re-reads `/masks/<name>` from the repo. Verified at line 640-641.
  - On `set_phasor_data` (line 605-606): cleared to `None` because each
    `compute_phasor` produces a new `(g, s)` frame whose alignment may
    differ from the previously cached mask (see Vector 4 of the
    5-vector staleness compound).
  - Verdict: **rebinds on every relevant event**. After U8 (which only
    deletes the off-label `set_active_mask(None)` from `_on_remove_roi`),
    this cache is unaffected — the `ACTIVE_MASK_CHANGED` event is no
    longer emitted on Remove, but the cache was being unnecessarily
    refreshed because of that emission, not relying on it. Confirmed by
    the compound doc Vector 4 fix: invalidation is owned by
    `set_phasor_data`, not by `set_active_mask`.

- `_ROIWidget.cached_mask` (per-ROI) — **rebinds correctly.**
  - On ROI move (`_on_roi_moved_widget:462`): cleared for the moved
    widget only.
  - On angle change (`_on_angle_changed:439`): cleared for the selected
    widget.
  - On `_on_filtered_toggled` (line 622-624): cleared for **all**
    widgets (filter switch invalidates G/S basis).
  - On `_on_mask_filter_toggled` (line 656-657): cleared for all
    widgets.
  - On `set_phasor_data` (line 596-597): cleared for all widgets.
  - On Remove (line 357 `w.cached_mask = None` for each survivor):
    cleared for every surviving widget because labels are renumbered.
  - Verdict: **rebinds on every relevant event** (Pattern 5 of the
    multi-ROI patterns doc).

- `_preview_colormap` / `_colormap_dirty` — **rebinds correctly.**
  - On Add ROI (line 344): `_colormap_dirty = True`.
  - On Remove ROI (line 359): `_colormap_dirty = True`.
  - On visibility toggle (line 446): `_colormap_dirty = True`.
  - Read in `_update_preview` (line 547); rebuilt only when dirty.
  - Verdict: **rebinds on every relevant event.**

- `_g_map`, `_s_map`, `_g_map_unfiltered`, `_s_map_unfiltered`,
  `_labels`, `_labels_flat`, `_total_valid_pixels` — **rebinds
  correctly** because they are explicitly assigned in
  `set_phasor_data` (lines 586-617). They are not session-subscriber
  caches; the launcher's compute-phasor flow pushes new values in.
  No subscriber rebind needed.

- **Selected ROI panel** (`_name_edit`, `_angle_spin`, `_vis_check` —
  built at lines 271-296; populated by `_on_roi_list_selection:401-416`):
  **does not rebind on ROI removal.**
  - `_on_remove_roi:349-362` calls `self._roi_widgets.pop(...)` and
    sets `_selected_roi_index = None`, then calls `_refresh_roi_list`.
    `_refresh_roi_list:389-399` rebuilds the QListWidget but only
    re-selects a row if `_selected_roi_index < len(self._roi_widgets)`.
    When `_selected_roi_index is None`, no row is selected and
    `_on_roi_list_selection` is *not* called with a fresh row.
    Result: `_name_edit.text()` still shows the removed ROI's name,
    `_vis_check` retains its checked state, `_angle_spin` retains its
    value — Bug A symptom #4.
  - **Fix in U8.** Either (a) add a `roi_list_changed` signal that the
    Selected ROI panel listens for and resets to defaults when no row
    is selected, or (b) extend `_on_roi_list_selection` with an
    explicit "no selection → clear panel" branch and call it from
    `_on_remove_roi`.

- **Status bar** (`_status`, `phasor_plot.py:315-317`):
  **does not reset on ROI removal.**
  - `_update_preview:539-572` writes per-ROI pixel-count text
    ("ROI_1: N (X%) | ROI_2: N (X%)") into the status bar.
  - `_update_preview` early-returns when `not self._roi_widgets`
    (line 541-542), so after Remove leaves zero ROIs the status bar
    keeps its last text — Bug A symptom #5.
  - **Fix in U8.** Either (a) call `_refresh_histogram` from
    `_on_remove_roi` (it writes the canonical "Phasor: N valid
    pixels" status at line 790), or (b) write that string explicitly
    inside `_on_remove_roi` when `_roi_widgets` is empty.

### DataPlotWindow (`peer_views/data_plot.py`)

- `_x_data`, `_y_data`, `_labels_array` numpy caches (line 71-73).
  - Refilled in `_refresh_plot:254-294` from `session.filtered_df`.
  - `_refresh_plot` is called on `MEASUREMENTS_UPDATED`,
    `DATASET_CHANGED`, `FILTER_CHANGED`, and on column-combo changes.
  - `_update_selection_highlights:223-250` reads `session.selected_ids`
    live and indexes into the cached arrays via `np.isin`.
  - Verdict: **rebinds on every relevant event.**

### CellTableWindow (`peer_views/cell_table.py`)

- `PandasTableModel._df` and `_label_to_row` (lines 38-49).
  - Replaced via `set_dataframe` in `_reload_table_data:233-243` on
    `MEASUREMENTS_UPDATED` / `DATASET_CHANGED`.
  - Verdict: **rebinds on every relevant event.**
- `FilterableProxyModel._visible_labels` (line 114).
  - Replaced via `set_filter_labels` in `_apply_filter:245-265` on
    `FILTER_CHANGED`.
  - Verdict: **rebinds on every relevant event.**

## Rebind contract

Every subscriber must satisfy these rules. They are derived from
prevention rules in
`docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
and from Patterns 3 and 5 of
`docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`.

1. **Per-state events on every state slot** (Compound doc Prevention #5).
   Peer views subscribe to specific slots (`ACTIVE_MASK_CHANGED`,
   `FILTER_CHANGED`, etc.), not to coarse parent events. `Session.set_*`
   methods that clear multiple slots must emit per-slot events, not just
   `DATASET_CHANGED`. The `state_changed` Qt signal carries the same
   information via `StateChange` flag bits.

2. **`Session.set_*` methods that change input semantics must invalidate
   dependent caches** (Compound doc Prevention #4). Example:
   `set_phasor_data` invalidates ROI mask cache *and* mask-flat cache
   because each new `(g, s)` frame can be misaligned with the previous
   one even when shapes match.

3. **Subscribers must rebind from session, not from a stored snapshot.**
   Read `session.field` (or `data_model.field`) inside the handler;
   never close over a value captured at subscribe time. The viewer's
   `_update_label_display`, the table's `_apply_filter`, and the
   data plot's `_refresh_plot` all follow this rule.

4. **Identity-based widget lookup, never index-based,** when the
   subscriber tracks individual items in a list (Pattern 3 of
   `percell4-selection-filtering-multi-roi-patterns.md`).
   `PhasorPlotWindow._create_roi_widget:382-386` already follows this:
   the `sigRegionChangeFinished` lambda captures the `_ROIWidget`
   identity, and `_on_roi_moved_widget` checks `widget not in
   self._roi_widgets` before acting. The U8 Selected-ROI rebind must
   follow the same rule.

5. **List-rebuild + selection reset must explicitly reset dependent
   panels.** `QListWidget.clear()` does not fire `currentRowChanged`
   for handlers that need the "no selection" state; the subscriber
   must reset the dependent widgets directly when it knows
   `_selected_roi_index is None`. This is the gap that U8 closes.

6. **Status bars are session-derived state.** A status bar that
   displays per-ROI counts ("ROI_1: 12,345 (12.3%)") must reset to its
   no-ROI default ("Phasor: N valid pixels") when the ROI set becomes
   empty, exactly as a peer view's plot resets to "no data" when the
   DataFrame becomes empty.

## Verdict and fix references

| Subscriber | Issue | Fixed in |
|---|---|---|
| `PhasorPlotWindow` Selected ROI panel (`peer_views/phasor_plot.py:271-296`, `_on_roi_list_selection:401-416`) | `_name_edit`, `_angle_spin`, `_vis_check` retain the removed ROI's values when `_on_remove_roi` clears `_selected_roi_index`. The `_refresh_roi_list:389-399` re-select branch is skipped when `_selected_roi_index is None`, so the dependent Selected-ROI widgets never see a "clear" event. (Bug A symptom #4.) | **U8** |
| `PhasorPlotWindow` status bar (`peer_views/phasor_plot.py:315-317`) | Status text from `_update_preview:560-572` (per-ROI counts) persists after the last ROI is removed because `_update_preview` early-returns on `not self._roi_widgets`. Need to call `_refresh_histogram` (which writes the canonical "Phasor: N valid pixels" status at line 790) when the ROI list becomes empty. (Bug A symptom #5.) | **U8** |
| `LauncherWindow._on_state_changed` (`interfaces/gui/main_window.py:1028-1032`) | Empty body. Intentional under the plan: state-change handling is delegated to panel-level subscribers (`DataPanel`, `AnalysisPanel`, `SegmentationPanel`). | **No fix** — verdict is "compliant by delegation"; documented here so future contributors don't add ad-hoc state writes inside the launcher. |
| `ViewerWindow._on_state_changed` (`gui/viewer.py:274-279`) | Currently handles `change.filter` and `change.selection` only. U10 extends with `change.mask` and `change.segmentation` branches as part of the session → napari one-way push (R8). | **U10** (not a Bug A fix — listed for traceability.) |
| All other subscribers | Rebind correctly today. | None |

## Cross-references

- 5-vector HDF5 staleness compound:
  `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  — Prevention rules #4 (per-cache invalidation on input-semantics
  change) and #5 (per-state events on every state slot) are this
  matrix's rebind contract.
- Multi-ROI selection / filtering patterns:
  `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`
  — Pattern 3 (identity-based widget lookup) is the rebind contract for
  any subscriber that tracks list items by widget; Pattern 5 (per-ROI
  cache invalidation) is satisfied by `PhasorPlotWindow`.
- Session bridge event-forwarding rule:
  `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`
  — companion source for the per-slot emission rule.
- GUI element classification (U1/U2 deliverable):
  `docs/audits/gui-element-classification.yaml` — the YAML cross-ref for
  every subscriber widget in this matrix.
- Mutation graph (U3 deliverable):
  `docs/audits/session-mutation-graph.md` — the writer side of the same
  Selector / Creator / Action taxonomy.
