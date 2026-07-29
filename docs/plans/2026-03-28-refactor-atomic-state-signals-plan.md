---
title: "Refactor: Atomic State Signals for CellDataModel"
type: refactor
date: 2026-03-28
---

# Refactor: Atomic State Signals for CellDataModel

## Overview

Replace CellDataModel's multi-signal emission pattern with a single `state_changed(StateChange)` signal. Migrate all 5 consumer windows to a unified handler. Decouple phasor_plot's direct viewer access into signals routed through the launcher.

**Implementation spec:** `docs/refactor-atomic-state-signals.md` — contains code snippets, extraction patterns, and per-step acceptance criteria. This plan orchestrates sequencing and defines done-conditions. The refactor doc is the source of truth for implementation details.

## Problem Statement

`set_filter()` emits 2 signals, `set_measurements()` emits 3, `clear()` emits 5 — all synchronous. Handlers see inconsistent intermediate state. The `_updating_selection` guard blocks legitimate inbound updates during cascades. Windows do redundant work (DataPlotWindow redraws 3+ times per `set_measurements`). These cause persistent bugs where state changes don't propagate correctly across windows.

## Proposed Solution

Single `state_changed(StateChange)` signal with boolean flags for what changed. Each window gets one `_on_state_changed` handler that processes all relevant changes in a defined order within one call. Old signals dual-emit during migration (Steps 1-5), removed in Step 6.

## Technical Considerations

### Corrections to the refactor doc

The following gaps were identified by SpecFlow analysis and confirmed during planning:

1. **`_apply_filter` guard is NOT removable.** The refactor doc says to remove `_updating_selection` from `_apply_filter()` in cell_table. This is wrong. `invalidateFilter()` triggers Qt's internal `QItemSelectionModel::selectionChanged`, which fires `_on_table_selection_changed`, which calls `model.set_selection()`. The guard protects against Qt's own signal, not the model's cascade. **Fix:** Rename `_updating_selection` to `_is_originator` across all windows. Keep it in `_apply_filter`, `_on_table_selection_changed` (cell_table), `_update_label_display`, `_on_label_selected` (viewer), and outbound handlers in data_plot.

2. **Constructor bootstrap not addressed.** `DataPlotWindow.__init__` and `CellTableWindow.__init__` call `_on_data_updated()` for late-joining windows that missed the initial `data_updated` signal. After migration, `_on_data_updated()` is deleted. **Fix:** Constructor calls `self._on_state_changed(StateChange(data=True, filter=True, selection=True))` to bootstrap through the unified path.

3. **`_on_apply_mask` signal undefined.** The refactor doc says "do the same" but provides no signal definition, launcher handler, or HDF5 access pattern. **Fix:** Defined below in Step 5.

4. **Launcher has 4 old-signal connections, not 1.** Step 6 only mentions `filter_changed` → `_on_filter_state_changed`. Also need to migrate: `active_segmentation_changed` → `_on_model_active_seg_changed` (line 599), `active_mask_changed` → `_on_model_active_mask_changed` (line 602).

5. **`blockSignals` in `_rebuild_dropdowns`.** When extracting `_rebuild_dropdowns()` from `_on_data_updated()` in data_plot, the `blockSignals(True/False)` calls on `_x_combo` and `_y_combo` must be preserved. Without them, `currentTextChanged` fires during population, triggering redundant `_refresh_plot()` calls.

6. **`_refresh_plot` must still call `_update_selection_highlights` internally.** The unified handler uses `elif change.selection` to avoid double work, so selection highlights are NOT applied when `change.data` or `change.filter` triggers `_refresh_plot`. The method itself must re-apply highlights after rebuilding the scatter (line 276 in current code).

7. **Existing tests break at Step 6.** `test_clear_resets_state` connects to `data_updated` and `selection_changed`. Must update to use `state_changed` or remove old signal assertions.

### Invariant to document in model.py

All `CellDataModel` mutations must occur on the main (GUI) thread. Worker threads emit results via Qt signals which are marshaled to the main thread by `AutoConnection`. This is currently true but undocumented.

### `_is_originator` rename scope

Rename `_updating_selection` → `_is_originator` in all windows for clarity:

| Window | Where set to `True` | Where checked |
|--------|---------------------|---------------|
| **viewer.py** | `_on_label_selected` (outbound click), `_update_label_display` (modifies napari `selected_label`) | `_on_state_changed` (top-level), `_on_label_selected`, `_update_label_display` |
| **data_plot.py** | `_on_point_clicked`, `_on_rect_selected` (optional — see note) | `_on_state_changed` (top-level, optional) |

**data_plot `_is_originator` note:** Outbound handlers (`_on_point_clicked`, `_on_rect_selected`) call `set_selection()`, which emits `state_changed`, which calls `_on_state_changed` → `_update_selection_highlights()`. This is a redundant highlight redraw — the scatter already reflects the click. It won't cause a bug (no feedback loop), but it's wasted work. Implementer's choice: add `_is_originator` to skip the round-trip, or accept the cheap redundant redraw for simpler code.
| **cell_table.py** | `_on_table_selection_changed` (outbound row click), `_apply_filter` (before `invalidateFilter`) | `_on_state_changed` (top-level), `_on_table_selection_changed` |

### Phasor decoupling signals (Step 5)

Two new signals on `PhasorPlotWindow`:

```
preview_mask_ready = Signal(object, object)   # (mask_ndarray, DirectLabelColormap)
mask_applied = Signal(object, object, str)    # (mask_ndarray, color_dict, mask_name)
```

Launcher connects both when creating phasor window. `mask_applied` handler: removes preview layer, calls `viewer_win.add_mask()`, calls `store.write_mask()`, calls `data_model.set_active_mask()`.

**`self._launcher` removal check:** `self._launcher` is used at 3 runtime sites in phasor_plot.py: `_update_preview` (line 478), `_on_apply_mask` (lines 657-675). All three access `self._launcher._windows.get("viewer")` or `self._launcher._current_store`. After decoupling preview and apply-mask into signals, all runtime uses are eliminated. The `launcher=self` constructor arg and `self._launcher` attribute can be removed. (The launcher pushes data into phasor via `set_phasor_data()` — that direction doesn't require `self._launcher`.)

## Implementation Steps

### Step 1: `StateChange` + `state_changed` signal + model tests

**Scope:** `model.py`, `tests/test_model.py`

- Add `StateChange` dataclass and `state_changed = Signal(object)` to CellDataModel
- Each mutating method emits `state_changed` BEFORE old signals (critical ordering)
- Preserve `if name != old` guards in `set_active_segmentation`/`set_active_mask`
- Add model-only tests verifying each mutation emits exactly one `state_changed` with correct flags: `set_selection`, `set_filter`, `set_measurements`, `set_active_segmentation`, `set_active_mask`, `clear`
- Add test for `set_filter` clearing selection
- Add test for `filtered_df` correctness and cache invalidation

**Done when:** All tests pass. App launches and works identically (old signals still driving windows).

### Step 2: Migrate `data_plot.py`

**Scope:** `data_plot.py`

- Replace 3 old signal connections with single `state_changed.connect`
- Add `_on_state_changed` handler (see refactor doc for logic)
- Extract `_rebuild_dropdowns()` — preserve `blockSignals` on combos
- Extract `_update_selection_highlights()` from `_on_selection_changed`
- `_refresh_plot()` keeps its internal call to `_update_selection_highlights()`
- Constructor bootstrap: replace `_on_data_updated()` call with `_on_state_changed(StateChange(data=True, filter=True, selection=True))`
- Remove `_on_data_updated`, `_on_selection_changed`, `_on_filter_changed` → `_refresh_plot` connection
- **`_is_originator` decision:** Outbound click → `set_selection()` → `state_changed` → `_update_selection_highlights()` causes a redundant (but harmless) highlight redraw. Either add `_is_originator` to skip it, or accept the cheap redraw. Make a conscious choice and comment it

**Done when:** Acceptance criteria from refactor doc Step 2 pass. Add `logger.debug` to verify handler fires once per state change.

### Step 3: Migrate `cell_table.py`

**Scope:** `cell_table.py`

- Replace 3 old signal connections with single `state_changed.connect`
- Add `_on_state_changed` with top-level `if self._is_originator: return` guard
- Rename `_updating_selection` → `_is_originator`
- Extract `_reload_table_data()`, `_apply_filter()`, `_highlight_selected_rows()`
- **Critical:** `_apply_filter()` sets `_is_originator = True` in try/finally around `invalidateFilter()`
- `_on_table_selection_changed` sets `_is_originator = True` in try/finally (outbound guard)
- Constructor bootstrap: replace `_on_data_updated()` with `_on_state_changed(StateChange(data=True, filter=True, selection=True))`
- Remove old handlers

**Done when:** Acceptance criteria from refactor doc Step 3 pass.

### Step 4: Migrate `viewer.py`

**Scope:** `viewer.py`

- In `_ensure_viewer()`, replace 2 old connections with `state_changed.connect`
- Add `_on_state_changed` with top-level `if self._is_originator: return` guard (consistent with cell_table). Calls `_update_label_display()` when `change.filter or change.selection`
- Remove `_schedule_label_update` and `_display_update_pending` flag entirely
- Rename `_updating_selection` → `_is_originator`
- Keep `_is_originator` in `_on_label_selected` (outbound) and `_update_label_display` (napari `selected_label` modification triggers `_on_label_selected`). The top-level check in `_on_state_changed` short-circuits before calling `_update_label_display`, avoiding the unnecessary function call depth

**Done when:** Acceptance criteria from refactor doc Step 4 pass. Verify viewer close + reopen reconnects `state_changed`.

### Step 5: Migrate `phasor_plot.py` + decouple viewer access

**Scope:** `phasor_plot.py`, `launcher.py`

Part A — State signal migration:
- Replace `filter_changed` connection with `state_changed.connect`
- `_on_state_changed`: starts `_filter_timer` when `change.filter`

Part B — Preview decoupling:
- Add `preview_mask_ready = Signal(object, object)` to PhasorPlotWindow
- `_update_preview()` computes mask + colormap, then emits `preview_mask_ready` instead of directly manipulating viewer layers
- Launcher connects `phasor_win.preview_mask_ready` → `_on_phasor_preview` handler (create/update `_phasor_roi_preview` layer)

Part C — Apply mask decoupling:
- Add `mask_applied = Signal(object, object, str)` to PhasorPlotWindow
- `_on_apply_mask()` computes mask + color_dict, emits `mask_applied(mask, color_dict, "phasor_roi")` instead of direct viewer/store access
- Launcher connects `phasor_win.mask_applied` → `_on_phasor_mask_applied` handler:
  - Remove `_phasor_roi_preview` layer (try/except ValueError)
  - Call `viewer_win.add_mask(mask, name, color_dict=color_dict)`
  - Call `store.write_mask(name, mask)`
  - Call `data_model.set_active_mask(name)`
- Remove `self._launcher` attribute from PhasorPlotWindow (no longer needed)

**Done when:** Acceptance criteria from refactor doc Step 5 pass. Viewer closed and reopened → phasor preview still works (no stale reference because launcher mediates).

### Step 6: Remove old signals + migrate launcher

**Scope:** `model.py`, `launcher.py`, `tests/test_model.py`

- Migrate launcher's 3 old-signal connections to `state_changed`:
  - `filter_changed` → `_on_filter_state_changed`
  - `active_segmentation_changed` → `_on_model_active_seg_changed`
  - `active_mask_changed` → `_on_model_active_mask_changed`
- Remove old signal emissions from all mutating methods in model.py
- Remove old signal declarations from CellDataModel
- Update `test_clear_resets_state` and any tests referencing old signal names
- Grep verification: `grep -r "data_updated\|selection_changed\|filter_changed\|active_segmentation_changed\|active_mask_changed" src/percell4/ --include="*.py"` returns zero `.connect()` calls

**Done when:** Full end-to-end workflow works: load dataset → measure → select → filter → phasor ROI → apply mask → measure with ROI → export CSV. Grep returns clean.

## Acceptance Criteria

Per-step criteria are in the refactor doc. Global criteria:

- [ ] All existing user interactions work identically (click, Ctrl+click, Shift+drag, Escape, filter, clear) — requires manual testing
- [ ] No double-processing of state changes (verify with logger.debug) — requires manual testing
- [ ] Phasor ROI preview works with viewer open, closed, or reopened — requires manual testing
- [x] Model-only tests pass for all 6 mutations with correct StateChange flags
- [x] No `_updating_selection` references remain — all renamed to `_is_originator`
- [x] No old signal `.connect()` calls remain after Step 6

## Dependencies & Risks

| Risk | Mitigation |
|------|-----------|
| Phasor viewer decoupling (Step 5) is highest-risk | Keep old direct-access code commented out until signal approach confirmed |
| `invalidateFilter` feedback loop if guard removed | Plan explicitly preserves guard as `_is_originator` |
| Late-joining windows miss state | Constructor bootstrap via `_on_state_changed(StateChange(...))` |
| Steps depend on each other | Each step preserves backward compat via dual-emit; rollback = revert one file |
| No GUI tests | Manual validation per acceptance criteria; model tests guard the core invariant |

## References

- **Implementation spec:** `docs/refactor-atomic-state-signals.md`
- **Related brainstorm:** `docs/brainstorms/2026-03-27-cross-window-selection-filtering-brainstorm.md`
- **Institutional learnings:** `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md` (Patterns 2, 4, 8)
- **np.isin gotcha:** `docs/solutions/logic-errors/numpy-isin-fails-with-python-sets.md` — always `list()` wrap sets for `np.isin`
- **Window interactions doc:** `docs/window-interactions.md`
