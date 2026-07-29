> **SUPERSEDED — 2026-04-10.** This refactor shipped in commit `700a9c0`
> (`refactor(model): add StateChange dataclass and state_changed signal`).
> `CellDataModel` now has a single `state_changed` signal carrying a
> `StateChange` descriptor. See `src/percell4/model.py` and
> `docs/plans/2026-03-28-refactor-atomic-state-signals-plan.md` for the
> canonical plan. Kept here for historical context only.

# Refactor: Atomic State Signals for CellDataModel

## Problem Statement

PerCell4's interactive features (cell selection, filtering, phasor preview) suffer from persistent bugs where state changes in one window don't propagate correctly to others, guard flags get stuck, and the phasor preview shows stale data. These bugs resist individual fixes because the root cause is architectural: `CellDataModel` emits multiple sequential Qt signals during a single state transition, and each signal triggers synchronous handler execution across all windows. Windows observe the model in inconsistent intermediate states, and the `_updating_selection` boolean guard — meant to prevent feedback loops — also blocks legitimate inbound updates during signal cascades.

## Root Cause

`CellDataModel.set_filter()` emits `filter_changed` then `selection_changed` as two separate synchronous signals. `set_measurements()` emits three (`filter_changed`, `data_updated`, `selection_changed`). `clear()` emits five. Because Qt direct connections execute handlers inline during `.emit()`, every handler for signal N runs before signal N+1 is emitted. Windows processing signal 1 see partially-updated model state. The `_updating_selection` flag set during signal 1 processing blocks signal 2 processing in the same window.

## Solution Overview

Replace multi-signal emission with a single `state_changed` signal that carries a `StateChange` descriptor. Each window connects one handler that processes all relevant state changes in a defined order within a single call. This eliminates signal cascades, removes the need for inbound `_updating_selection` guards, and guarantees all windows see fully consistent state.

## Constraints

- **Do not modify display/rendering logic inside handlers.** The `DirectLabelColormap` logic in `viewer.py`, the two-layer scatter approach in `data_plot.py`, the `FilterableProxyModel` in `cell_table.py`, and the histogram/ROI logic in `phasor_plot.py` all work correctly when called with consistent state. Only the signal wiring and handler entry points change.
- **Do not restructure `launcher.py`.** It's a 2,137-line god object but that's a separate concern. Only touch launcher code that directly wires signals.
- **Keep old signals emitting during migration for backward compatibility.** Remove them only after all windows are migrated and tested.
- **Preserve all existing user-facing behavior.** Click-to-select, Ctrl+click toggle, Shift+drag rectangle select, filter-to-selection, clear-filter, escape-to-clear, phasor ROI preview — all must work identically.
- **Each step must be independently testable.** If step 3 breaks something, steps 1-2 should still work.

## Codebase Context

Key files and their roles:
- `src/percell4/model.py` (141 lines) — `CellDataModel` with 5 signals: `data_updated`, `selection_changed`, `filter_changed`, `active_segmentation_changed`, `active_mask_changed`
- `src/percell4/gui/viewer.py` (363 lines) — napari wrapper, selection/filter display via `DirectLabelColormap`
- `src/percell4/gui/data_plot.py` (343 lines) — pyqtgraph scatter plot with two-layer rendering
- `src/percell4/gui/cell_table.py` (375 lines) — QTableView with `PandasTableModel` + `FilterableProxyModel`
- `src/percell4/gui/phasor_plot.py` (755 lines) — phasor histogram with ROI ellipses, directly accesses viewer for preview
- `src/percell4/gui/launcher.py` (2137 lines) — main hub, window lifecycle, operations

The repo is at: https://github.com/marcusjoshm/percell4

## Implementation Plan

### Step 1: Add `StateChange` and `state_changed` signal to `model.py`

**File:** `src/percell4/model.py`

Add a `StateChange` dataclass at module level:

```python
from dataclasses import dataclass, field

@dataclass
class StateChange:
    """Descriptor for what changed in a CellDataModel state transition."""
    data: bool = False          # DataFrame was replaced
    selection: bool = False     # selected_ids changed
    filter: bool = False        # filtered_ids changed
    segmentation: bool = False  # active segmentation layer changed
    mask: bool = False          # active mask layer changed
```

Add a new signal to `CellDataModel`:

```python
state_changed = Signal(object)  # emits StateChange
```

Modify each mutating method to emit `state_changed` **after** all state is consistent, while **keeping old signals emitting** for backward compatibility during migration:

- `set_selection()`: emit `state_changed(StateChange(selection=True))`, then emit old `selection_changed`
- `set_filter()`: emit `state_changed(StateChange(filter=True, selection=True))`, then emit old `filter_changed` + `selection_changed`
- `set_measurements()`: emit `state_changed(StateChange(data=True, filter=True, selection=True))`, then emit old `filter_changed` + `data_updated` + `selection_changed`
- `set_active_segmentation()`: emit `state_changed(StateChange(segmentation=True))`, then emit old `active_segmentation_changed`
- `set_active_mask()`: emit `state_changed(StateChange(mask=True))`, then emit old `active_mask_changed`
- `clear()`: emit `state_changed(StateChange(data=True, filter=True, selection=True, segmentation=True, mask=True))`, then emit all old signals

**Critical ordering:** `state_changed` must emit BEFORE old signals. This way, migrated windows process the atomic signal first, and unmigrated windows still get their old signals. If old signals emit first, migrated windows would process `state_changed` after already being updated by old signals, causing double-processing.

**Acceptance criteria:**
- All existing tests pass with no changes
- App launches and behaves identically (old signals still driving everything)
- `state_changed` signal fires on every state mutation

### Step 2: Migrate `data_plot.py`

**File:** `src/percell4/gui/data_plot.py`

In `_connect_signals()`, disconnect old signals and connect to `state_changed`:

```python
def _connect_signals(self) -> None:
    self.data_model.state_changed.connect(self._on_state_changed)
    self._x_combo.currentTextChanged.connect(self._refresh_plot)
    self._y_combo.currentTextChanged.connect(self._refresh_plot)
    self._base_scatter.sigClicked.connect(self._on_point_clicked)
    self._vb.sigSelectionComplete.connect(self._on_rect_selected)
```

Add the unified handler:

```python
def _on_state_changed(self, change) -> None:
    """Handle all model state changes in one atomic pass."""
    if change.data:
        self._rebuild_dropdowns()
    if change.data or change.filter:
        self._refresh_plot()
    elif change.selection:
        # Only update highlights if we didn't already refresh the full plot
        self._update_selection_highlights()
```

Extract `_rebuild_dropdowns()` from the current `_on_data_updated()` — the part that populates `_x_combo` and `_y_combo`. The current method already does dropdown rebuild + plot refresh + selection highlight; split these into the three concerns listed above.

Extract `_update_selection_highlights()` from the current `_on_selection_changed()`. Keep the `_updating_selection` guard **only** on outbound paths (`_on_point_clicked`, `_on_rect_selected`) — remove it from the inbound `_update_selection_highlights()` since there is no cascade to guard against.

Remove `_on_data_updated()`, `_on_selection_changed()`, and the connection to `filter_changed` in `_connect_signals()`.

**Acceptance criteria:**
- Click a cell in the viewer → data_plot highlights the point (red dot)
- Click a point in data_plot → viewer highlights the cell, table selects row
- Shift+drag rectangle in data_plot → multiple cells selected in viewer and table
- Ctrl+click toggles selection in/out
- Escape clears selection across all windows
- "Filter to Selection" → plot shows only filtered points
- "Clear Filter" → plot shows all points
- "Measure Cells" → dropdowns rebuild with new columns, scatter redraws
- No double-processing (add a `logger.debug` in handler to verify it fires once per state change)

### Step 3: Migrate `cell_table.py`

**File:** `src/percell4/gui/cell_table.py`

Same pattern. In `_connect_signals()`:

```python
def _connect_signals(self) -> None:
    self.data_model.state_changed.connect(self._on_state_changed)
    self._table.selectionModel().selectionChanged.connect(
        self._on_table_selection_changed
    )
```

Add unified handler:

```python
def _on_state_changed(self, change) -> None:
    """Handle all model state changes in one atomic pass."""
    if change.data:
        self._reload_table_data()
    if change.filter:
        self._apply_filter()
    if change.selection:
        self._highlight_selected_rows()
```

Extract from existing handlers:
- `_reload_table_data()` from `_on_data_updated()` — sets dataframe on model, updates status, resizes columns
- `_apply_filter()` from `_on_filter_changed()` — calls `self._proxy.set_filter_labels()`, updates status. **Remove the `_updating_selection = True` wrapper** — it's no longer needed because there's no cascade
- `_highlight_selected_rows()` from `_on_selection_changed()` — clears selection, selects rows, scrolls to first. **Remove the `_updating_selection` guard on the inbound path.** Keep it only on `_on_table_selection_changed` (the outbound path where user clicks a row).

**Important edge case:** When `_on_table_selection_changed` fires (user clicks a row), it calls `self.data_model.set_selection()`, which emits `state_changed`. The cell_table's `_on_state_changed` handler fires with `change.selection=True`. This is where `_highlight_selected_rows` must guard against the re-entrant call. Use the existing `_updating_selection` flag:

```python
def _on_state_changed(self, change) -> None:
    if self._updating_selection:
        return
    # ... process changes
```

But rename it to `_is_originator` or similar for clarity — it now means "I initiated this state change" rather than the overloaded "I'm in the middle of something."

**Acceptance criteria:**
- Click a row → viewer and data_plot highlight the cell
- Click cell in viewer → table selects and scrolls to the row
- Shift+click range in table → multi-selection reflected in viewer and plot
- Filter → table shows only filtered rows
- Clear filter → table shows all rows
- New measurements → table reloads with new columns
- Column header click to sort still works
- Export CSV still works

### Step 4: Migrate `viewer.py`

**File:** `src/percell4/gui/viewer.py`

In `_ensure_viewer()`, replace the two signal connections:

```python
# Old:
# self.data_model.selection_changed.connect(self._schedule_label_update)
# self.data_model.filter_changed.connect(self._schedule_label_update)

# New:
self.data_model.state_changed.connect(self._on_state_changed)
```

Add unified handler:

```python
def _on_state_changed(self, change) -> None:
    """Handle model state changes."""
    if change.filter or change.selection:
        self._update_label_display()
```

**Remove `_schedule_label_update` and the `_display_update_pending` flag entirely.** The coalescing timer was only needed because `set_filter()` fired two signals that both triggered the same handler. With a single signal, there's nothing to coalesce.

Keep the `_updating_selection` guard in `_on_label_selected` (outbound: user clicked a label in napari) and in `_update_label_display` (to prevent re-entrance when the handler modifies `selected_label` on the napari layer, which could trigger `_on_label_selected` again).

**Acceptance criteria:**
- Click cell in viewer → yellow highlight appears, other cells dim
- Click cell in data_plot → viewer highlights that cell
- Filter → non-filtered cells transparent, filtered cells cyan
- Filter + selection → filtered non-selected dim gray, selected yellow
- Clear selection → original colormap restored
- Clear filter → original colormap restored
- Viewer force-close + reopen → selection/filter state correctly applied to new viewer

### Step 5: Migrate `phasor_plot.py`

**File:** `src/percell4/gui/phasor_plot.py`

Replace `filter_changed` connection with `state_changed`:

```python
# Old:
# self.data_model.filter_changed.connect(self._on_filter_changed)

# New:
self.data_model.state_changed.connect(self._on_state_changed)
```

Add unified handler:

```python
def _on_state_changed(self, change) -> None:
    if change.filter:
        self._filter_timer.start()  # existing debounce behavior preserved
```

**Fix the direct viewer access for phasor preview.** The `_update_preview()` method currently reaches into the viewer via `self._launcher._windows.get("viewer")`. Refactor this to use a signal:

Add a new signal to `PhasorPlotWindow`:
```python
preview_mask_ready = Signal(np.ndarray, object)  # (mask_array, colormap)
```

In `_update_preview()`, instead of directly manipulating the viewer's layers, emit:
```python
self.preview_mask_ready.emit(mask, self._preview_colormap)
```

In `launcher.py`, when creating the phasor window, connect this signal to a method that forwards the preview to the viewer:

```python
phasor_win.preview_mask_ready.connect(self._on_phasor_preview)
```

```python
def _on_phasor_preview(self, mask, colormap):
    viewer_win = self._windows.get("viewer")
    if viewer_win is None or not viewer_win._is_alive():
        return
    preview_name = "_phasor_roi_preview"
    try:
        layer = viewer_win._viewer.layers[preview_name]
        layer.data = mask
        layer.colormap = colormap
    except KeyError:
        viewer_win._viewer.add_labels(
            mask, name=preview_name,
            colormap=colormap, opacity=0.4,
            blending="translucent",
        )
```

Do the same for `_on_apply_mask()` — the phasor plot should not directly remove/add layers on the viewer. Emit a signal, let the launcher mediate.

**Acceptance criteria:**
- Phasor ROI drag → preview updates in viewer
- Filter active → phasor histogram shows only filtered cells' pixels
- Filter active → phasor ROI mask restricted to filtered cells
- "Apply Visible as Mask" → mask layer appears in viewer, preview removed
- Viewer closed and reopened → phasor preview still works (no stale reference)

### Step 6: Remove old signals from `model.py`

**File:** `src/percell4/model.py`

After all windows are migrated:

1. Remove the old signal emissions from `set_filter()`, `set_measurements()`, `set_selection()`, `set_active_segmentation()`, `set_active_mask()`, and `clear()`
2. Keep the old signal *declarations* on the class but add a deprecation comment
3. Search the codebase for any remaining connections to old signals (grep for `.data_updated.connect`, `.selection_changed.connect`, `.filter_changed.connect`, `.active_segmentation_changed.connect`, `.active_mask_changed.connect`). The launcher likely has connections for status label updates — migrate those too
4. Remove old signals entirely once all connections are gone

**Also in this step**: update `src/percell4/gui/launcher.py` connections. The launcher connects to `filter_changed` for the status label (`_on_filter_state_changed`). Migrate this to `state_changed`:

```python
def _on_state_changed(self, change):
    if change.filter:
        self._on_filter_state_changed()
```

**Acceptance criteria:**
- `grep -r "data_updated\|selection_changed\|filter_changed\|active_segmentation_changed\|active_mask_changed" src/percell4/ --include="*.py"` returns only the signal declarations in `model.py` and no `.connect()` calls anywhere
- Full end-to-end workflow works: load dataset → measure → select cells → filter → phasor ROI → apply mask → measure with ROI → export CSV

## Risk Mitigation

- **Each step preserves backward compatibility** because old signals continue emitting (Steps 1-5) until explicitly removed (Step 6)
- **Order `state_changed` before old signals** so migrated windows get the atomic signal first; unmigrated windows then process old signals as before — no double-processing because migrated windows no longer connect to old signals
- **No display logic changes** — if rendering was correct before when given consistent state, it will be correct after
- **The phasor preview refactor (Step 5)** is the highest-risk change because it restructures a cross-window communication path. Keep the old direct-access code commented out until the signal-based approach is confirmed working
