# Cross-Window Selection & Filtering Brainstorm

**Date:** 2026-03-27
**Status:** Draft
**Author:** Lee Lab + Claude

---

## What We're Building

Expand the interactive exploratory workflow by improving how cells are selected, highlighted, and filtered across all windows (Data Plot, Cell Table, Phasor Plot, napari Viewer). Three interconnected enhancements:

1. **Data Plot controls** — reset view, lasso/rectangle multi-select, log-scale toggle, axis range lock
2. **Auto-show selected label in viewer** — when a cell is selected anywhere, napari automatically displays only that cell's label (via "show selected")
3. **Multi-cell selection & filtering** — build up a selection from any window, then filter all windows to show only those cells
4. **Fix Cell Table column sorting** — header click sort (ascending/descending toggle) is implemented but broken. Fix so sorting works correctly, making it easier to identify and select cells for filtering.

---

## Why This Approach

Currently, selection flows one-way: clicking a point in the Data Plot or a row in the Cell Table emits `selection_changed`, but the napari viewer doesn't listen back — it never highlights the selected cell. The user must manually find the label number and configure napari. This breaks the exploratory loop.

The filtering feature extends the same selection mechanism. Instead of just highlighting, the user can lock the view to a subset of cells across all windows. This enables targeted analysis: pick interesting cells from a scatter plot, filter everything to just those cells, examine their phasor distribution, then clear the filter and move on.

---

## Key Decisions

### 1. Viewer responds to selection (auto-show selected label)

When `selection_changed` fires from any source, the ViewerWindow:
- Sets `labels_layer.selected_label` to the selected cell's label ID
- Enables `labels_layer.show_selected_label` (napari's built-in feature)
- Does NOT zoom or pan — just toggles visibility

For multi-cell selection, all selected label IDs are shown. When selection is cleared, `show_selected_label` is turned off and all labels become visible again.

**This is the default behavior** — no toggle button needed. Selecting a cell always shows it in the viewer.

### 2. Multi-cell selection sources

Three ways to build a multi-cell selection:

| Source | Method |
|--------|--------|
| **Cell Table** | Shift-click (range) or Ctrl-click (individual) rows — already supported |
| **Data Plot** | Ctrl-click individual points + new lasso/rectangle drag tool for area selection |
| **Napari Viewer** | A "Select Cells" mode button: click individual labels to add them, or draw rectangle/freehand regions to capture all labels within. Not Ctrl-click — an explicit mode toggle. |

All three feed into `CellDataModel.set_selection(label_ids)` which already accepts a list.

### 3. Selection vs. Filtering are separate concepts

- **Selection** = highlighted cells (visual feedback in all windows). Ephemeral — click background to deselect.
- **Filtering** = only show selected cells. Persistent until explicitly cleared.

Workflow:
1. Build up a selection (multi-select in any window)
2. Click **"Filter to Selection"** button → all windows show only those cells
3. Click **"Clear Filter"** button → back to all cells

The filter state lives on `CellDataModel` (e.g., `_filtered_ids: list[int] | None`). When a filter is active, all windows use the filtered DataFrame subset. Selection continues to work within the filtered set.

### 4. Where Filter/Clear buttons live

"Filter to Selection" and "Clear Filter" buttons appear in the **launcher's Analysis tab** — they're global actions, not tied to a specific window. A status indicator shows when a filter is active (e.g., "Showing 12 of 842 cells").

### 5. Data Plot enhancements

| Feature | Behavior |
|---------|----------|
| **Reset view** | Button in toolbar that calls `plotWidget.autoRange()` — restores default zoom/pan |
| **Lasso/rectangle select** | Drag tool that selects all points within the drawn region. Adds to selection with Ctrl held. |
| **Log-scale toggle** | Checkbox per axis. Switches between linear and log scale. |
| **Axis range lock** | Checkbox that prevents auto-range from changing when data updates. Useful when comparing filtered vs. unfiltered. |

### 6. Fix Cell Table column sorting

Column sorting via `QSortFilterProxyModel` is wired up (`setSortingEnabled(True)`, `setSortRole(Qt.UserRole)`) but not functioning correctly. Debug and fix so that:
- Clicking a column header sorts ascending; clicking again sorts descending
- Numeric columns sort numerically (not lexicographically)
- NaN values sort to the end regardless of direction
- Selection sync still works correctly through the proxy model after sorting

This is a bug fix, not a new feature — the infrastructure exists but doesn't work.

### 7. Phasor Plot filtering behavior

When a cell filter is active, the Phasor Plot supports two modes via a checkbox:

- **Filtered only** — recompute the 2D histogram from only the pixels belonging to filtered cells' label regions
- **Full + highlight** — show the full histogram, overlay filtered cells' pixels in a different colormap

User toggles between these. Default is "filtered only" for a clean view.

---

## Open Questions

None — all questions resolved during brainstorming.
