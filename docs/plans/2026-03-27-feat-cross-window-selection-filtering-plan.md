---
title: "feat: Cross-Window Selection & Filtering"
type: feat
date: 2026-03-27
---

# Cross-Window Selection & Filtering

## Enhancement Summary

**Deepened on:** 2026-03-27
**Sections enhanced:** All phases + architecture
**Research agents used:** Python reviewer, Performance oracle, Code simplicity reviewer, Architecture strategist, Pattern recognition specialist, Race condition reviewer, pyqtgraph best practices researcher, napari API researcher

### Key Improvements from Deepening
1. **Use `DirectLabelColormap` instead of replacing `labels_layer.data`** — eliminates the `_original_labels` cache, 128MB memory churn, paint tool data loss, and feedback loops from napari data events
2. **Compose selection + filter into a single `_update_label_display()` method** in ViewerWindow — prevents conflicts between selection and filter state
3. **Use custom `SelectionViewBox` subclass** for rectangle selection — reuses pyqtgraph's built-in rubber-band rectangle, cleaner than mouse event overrides
4. **Use array-based `setData()` instead of dict-based `addPoints()`** for scatter plot — 10-20x faster for 10k+ points
5. **Defer Phase 4 (napari Select Cells mode)** — YAGNI; multi-select from data plot and cell table covers the primary use case
6. **Defer log-scale and axis-lock to a follow-up PR** — keeps this PR focused on selection/filtering
7. **Implement only "filtered only" phasor mode** — skip dual-histogram overlay for now
8. **Use `QSortFilterProxyModel.filterAcceptsRow()`** for table filtering — preserves sort state
9. **All `_updating_selection` guards must use `try/finally`** — prevents permanently stuck flags on exceptions
10. **`set_filter()` should also clear selection** — prevents degenerate "everything selected" state after filtering
11. **Add label-to-row dict index** in `PandasTableModel` — O(1) lookup instead of O(n) per label

### New Considerations Discovered
- napari's `DirectLabelColormap` supports a `None` default key for unmapped labels — GPU-side filtering, no data copy needed
- pyqtgraph `ScatterPlotItem` is NOT a `PlotDataItem` — `setLogMode()` does NOT auto-transform scatter data; must manually `np.log10()` before passing to `setData()`
- `ViewBox.sigRangeChangedManually` fires only on user zoom, never programmatic — correct signal for range lock detection
- napari `events.selected_label.blocker()` context manager prevents re-entrancy — cleaner than boolean flags for napari-specific operations
- `QSortFilterProxyModel` may destroy its selection model during `beginResetModel()`/`endResetModel()` — must guard with `_updating_selection` before `set_dataframe` calls
- The existing Python list comprehension in `data_plot.py:178` for highlight mask is O(n) in interpreter — replace with `np.isin()` for 50-100x speedup

---

## Overview

Expand the interactive exploratory workflow by improving how cells are selected, highlighted, and filtered across all windows (Data Plot, Cell Table, Phasor Plot, napari Viewer). Four phases of interconnected enhancements built on the existing `CellDataModel` signal hub.

## Problem Statement / Motivation

Currently, selection flows one-way: clicking a point in the Data Plot or a row in the Cell Table emits `selection_changed`, but the napari viewer doesn't listen back — it never highlights the selected cell. The user must manually find the label number and configure napari. This breaks the exploratory loop. Additionally, there is no way to filter all windows to a subset of cells for targeted analysis.

## Brainstorm Reference

`docs/brainstorms/2026-03-27-cross-window-selection-filtering-brainstorm.md`

## Research Findings

### Existing Architecture
- **CellDataModel** (`model.py`): 86 lines. Signals: `data_updated`, `selection_changed(list)`, `active_segmentation_changed(str)`, `active_mask_changed(str)`. State: `_df`, `_selected_ids`, `_active_segmentation`, `_active_mask`. No filtering concept exists.
- **Selection sources**: DataPlotWindow (single click only), CellTableWindow (shift/ctrl multi-select), ViewerWindow (napari label click). All call `model.set_selection(label_ids)`.
- **Selection consumers**: DataPlotWindow (highlight scatter), CellTableWindow (select+scroll rows). ViewerWindow does NOT listen — no reverse sync.
- **PhasorPlotWindow**: Does not participate in selection at all. Operates on pixel-level G/S maps with no label array.

### Key Gotchas (from docs/solutions/)
- **Feedback loop prevention**: Use `_updating_selection` guard flags with `try/finally`. CellTableWindow has one; DataPlotWindow and ViewerWindow do not — must add them.
- **napari event API**: Use `event.source.selected_label`, NOT `event.value`. Use `events.selected_label.blocker()` context manager for re-entrancy prevention.
- **napari `show_selected_label`**: Only supports a SINGLE label ID. Multi-cell highlighting uses `DirectLabelColormap` with `None` default key — GPU-side filtering, no data copy.
- **pyqtgraph SI prefix**: Re-enables on data refresh. Must disable in `_build_ui()` AND after every `_refresh_histogram()`.
- **Qt thread safety**: Never pass GUI callbacks to Worker threads. Route through Qt signals.
- **`_is_alive()` check**: Always check before accessing napari viewer — it can be destroyed/recreated.
- **Paint tool interaction**: Never replace `labels_layer.data` for visual filtering — this destroys user edits from napari's paint/fill/erase tools. Use colormap manipulation instead.

### Cell Table Sorting Bug
`PandasTableModel.data()` returns raw numpy scalars (`np.float64`, `np.int64`) for `Qt.UserRole`. `QSortFilterProxyModel` uses `operator<` for comparison, which may not handle numpy types correctly. Fix: cast to native Python `float()`/`int()` in the `UserRole` branch. Check `np.floating` before `float` since `df.iat` returns numpy scalars.

### Performance Findings
- **Scatter plot `addPoints` with Python dicts** (`data_plot.py:223-228`): Creates 10k+ dict objects per refresh. Replace with array-based `setData(x=, y=)` for 10-20x speedup.
- **Highlight mask Python loop** (`data_plot.py:178`): `[lid in id_set for lid in array]` is O(n) in Python. Replace with `np.isin()` for 50-100x speedup.
- **`find_row_for_label` is O(n) per call** (`cell_table.py:95-103`): Called in a loop for each selected label. Build a `{label: row}` dict index on `set_dataframe()` for O(1) lookups.

---

## Proposed Solution

### Architecture Changes

**New signal on CellDataModel:**
```python
filter_changed = Signal()  # emitted when filter state changes
```

**New state on CellDataModel:**
```python
_filtered_ids: set[int] | None = None  # None = no filter active; set for O(1) lookups
```

**New methods on CellDataModel:**
```python
def set_filter(self, label_ids: list[int] | None) -> None:
    """Set filter. None clears. Also clears selection. Emits filter_changed then selection_changed."""
    self._filtered_ids = set(label_ids) if label_ids is not None else None
    self._selected_ids = []  # clear selection to avoid "everything selected" state
    self.filter_changed.emit()
    self.selection_changed.emit([])

@property
def filtered_ids(self) -> set[int] | None:
    """Currently active filter IDs, or None if no filter."""
    return self._filtered_ids

@property
def filtered_df(self) -> pd.DataFrame:
    """Return filtered DataFrame, or full DataFrame if no filter."""
    if self._filtered_ids is None:
        return self._df
    return self._df[self._df["label"].isin(self._filtered_ids)]

@property
def is_filtered(self) -> bool:
    return self._filtered_ids is not None
```

**Filter auto-clears on `data_updated`**: When `set_measurements()` or `clear()` is called, `_filtered_ids` resets to `None`, `_selected_ids` resets to `[]`, and both `filter_changed` and `selection_changed` emit. Prevents stale IDs after re-segmentation.

**Signal ordering convention**: `filter_changed` emits before `selection_changed`. Windows that need selection info inside `_on_filter_changed` can read `model.selected_ids` directly.

**Convention**: `model.df` for schema discovery (column lists, types). `model.filtered_df` for data display (rows to show).

---

## Implementation Phases

### Phase 1: Fix Cell Table Sorting + Add Guards + Performance Fixes

**Goal:** Fix the broken sort, add missing feedback loop guards, and fix existing performance bottlenecks before adding new features.

**Files to modify:**

#### `src/percell4/gui/cell_table.py`

**Fix sorting** — in `PandasTableModel.data()`, cast numpy scalars to native Python types for `Qt.UserRole`. Check `np.floating` before `float` since `df.iat` returns numpy scalars:

```python
if role == Qt.UserRole:
    value = self._df.iat[row, col]
    if isinstance(value, np.floating):
        return float("inf") if math.isnan(value) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, float) and math.isnan(value):
        return float("inf")
    return value
```

**Add label-to-row index** for O(1) lookups during selection sync:

```python
def set_dataframe(self, df: pd.DataFrame) -> None:
    self.beginResetModel()
    self._df = df
    self._columns = list(df.columns)
    self._label_to_row = (
        {int(v): i for i, v in enumerate(df["label"])}
        if "label" in df.columns else {}
    )
    self.endResetModel()

def find_row_for_label(self, label_id: int) -> int | None:
    return self._label_to_row.get(label_id)
```

**Verify:** Click column headers — ascending/descending toggle works. NaN values sort to end.

#### `src/percell4/gui/data_plot.py`

**Add `_updating_selection` guard** on the receiver side (matching CellTableWindow's established pattern), with `try/finally`:

```python
def __init__(self, ...):
    ...
    self._updating_selection = False

def _on_selection_changed(self, selected_ids: list[int]) -> None:
    if self._updating_selection:
        return
    self._updating_selection = True
    try:
        # ... highlight logic ...
    finally:
        self._updating_selection = False
```

**Fix highlight mask performance** — replace Python loop with `np.isin()`:

```python
# Before (O(n) in Python interpreter):
mask = np.array([lid in id_set for lid in self._labels_array])

# After (vectorized, 50-100x faster):
mask = np.isin(self._labels_array, list(id_set))
```

**Replace dict-based `addPoints` with array-based `setData`:**

```python
# Before (slow — creates 10k+ dicts):
spots = [{"pos": (xi, yi), "data": int(lid)} for xi, yi, lid in zip(x, y, labels)]
self._base_scatter.clear()
self._base_scatter.addPoints(spots)

# After (10-20x faster):
self._base_scatter.setData(x=x, y=y)
self._labels_array = labels  # index-aligned with scatter points
```

For `_on_point_clicked`, retrieve the label by finding the nearest point index rather than using `.data()`:

```python
def _on_point_clicked(self, scatter_item, points, ev) -> None:
    if not points:
        return
    # Find the index of the clicked point in the scatter data
    pt = points[0]
    idx = pt.index()
    if idx is not None and idx < len(self._labels_array):
        label_id = int(self._labels_array[idx])
        self.data_model.set_selection([label_id])
```

#### `src/percell4/gui/viewer.py`

**Add `_updating_selection` guard** with `try/finally` — needed for Phase 2.

**Verify:** Selection works without feedback loops across all windows.

---

### Phase 2: Viewer Responds to Selection (DirectLabelColormap Approach)

**Goal:** When a cell is selected anywhere, napari highlights it via colormap manipulation — no data replacement.

### Research Insight: DirectLabelColormap vs Data Replacement

**napari's `DirectLabelColormap`** supports a `None` default key that colors all unmapped labels. By setting selected labels to bright colors and the `None` default to a dim/transparent color, we achieve visual selection filtering entirely on the GPU — no array copies, no cache, no paint tool data loss.

For **single-cell selection**, napari's built-in `show_selected_label` works perfectly (GPU-side, sets `colormap.use_selection = True`).

**Files to modify:**

#### `src/percell4/gui/viewer.py`

**Unified display update method** — composes filter and selection into one colormap update:

```python
from napari.utils.colormaps import DirectLabelColormap

def __init__(self, data_model):
    ...
    self.data_model.selection_changed.connect(self._update_label_display)
    # filter_changed will also connect to _update_label_display in Phase 4

def _update_label_display(self, *args) -> None:
    """Update labels layer display based on current selection and filter state.

    Composes: original labels → filter (which cells exist) → selection (which are highlighted).
    Uses DirectLabelColormap for GPU-side rendering — never modifies layer.data.
    """
    if self._updating_selection:
        return
    if not self._is_alive():
        return
    labels_layer = self._get_active_labels_layer()
    if labels_layer is None:
        return

    self._updating_selection = True
    try:
        selected_ids = self.data_model.selected_ids
        filtered_ids = self.data_model.filtered_ids  # set[int] | None

        if not selected_ids and not filtered_ids:
            # No selection, no filter: show all labels normally
            labels_layer.show_selected_label = False
            # Reset to default colormap (napari's auto colormap)
            labels_layer.colormap = labels_layer._original_colormap  # or reset
            return

        if len(selected_ids) == 1 and not filtered_ids:
            # Single cell, no filter: use napari's built-in show_selected_label
            with labels_layer.events.selected_label.blocker():
                labels_layer.selected_label = selected_ids[0]
            labels_layer.show_selected_label = True
            return

        # Multi-cell selection and/or filter active: use DirectLabelColormap
        labels_layer.show_selected_label = False

        # Determine which labels are visible (filter) and highlighted (selection)
        visible_ids = filtered_ids if filtered_ids else None  # None = all visible
        highlight_ids = set(selected_ids) if selected_ids else None

        color_dict = {0: "transparent"}

        if visible_ids and highlight_ids:
            # Both filter and selection: dim filtered non-selected, highlight selected
            color_dict[None] = [0.0, 0.0, 0.0, 0.0]  # hide non-filtered
            for lid in visible_ids:
                if lid in highlight_ids:
                    color_dict[lid] = [1.0, 1.0, 0.0, 0.8]  # yellow highlight
                else:
                    color_dict[lid] = [0.5, 0.5, 0.5, 0.15]  # dim gray
        elif visible_ids:
            # Filter only: show filtered, hide rest
            color_dict[None] = [0.0, 0.0, 0.0, 0.0]
            for lid in visible_ids:
                # Use a color cycle or default label colors
                color_dict[lid] = [0.3, 0.8, 0.8, 0.5]  # visible teal
        elif highlight_ids:
            # Selection only: highlight selected, dim rest
            color_dict[None] = [0.5, 0.5, 0.5, 0.15]  # dim default
            for lid in highlight_ids:
                color_dict[lid] = [1.0, 1.0, 0.0, 0.8]  # yellow highlight

        with labels_layer.events.colormap.blocker():
            labels_layer.colormap = DirectLabelColormap(color_dict=color_dict)
        labels_layer.refresh(extent=False)
    finally:
        self._updating_selection = False

def _get_active_labels_layer(self) -> "napari.layers.Labels | None":
    """Find the labels layer matching the active segmentation."""
    seg_name = self.data_model.active_segmentation
    for layer in self._viewer.layers:
        if isinstance(layer, napari.layers.Labels) and layer.name == seg_name:
            return layer
    return None
```

**Benefits of DirectLabelColormap over data replacement:**
- **No `_original_labels` cache needed** — layer data is never modified
- **No memory churn** — no 64MB array copies per selection change
- **No paint tool data loss** — user edits to labels are preserved
- **No feedback loops from napari `events.data`** — colormap changes don't fire data events
- **GPU-side rendering** — colormap mapping happens on the GPU, faster than CPU array manipulation
- **Simpler cleanup** — `clear()` just resets the colormap, no cache to invalidate

**Handle `_on_label_selected` with blocker:**

```python
def _on_label_selected(self, event) -> None:
    """napari label click → CellDataModel."""
    if self._updating_selection:
        return
    label_id = event.source.selected_label
    self._updating_selection = True
    try:
        if label_id == 0:
            self.data_model.set_selection([])
        else:
            self.data_model.set_selection([label_id])
    finally:
        self._updating_selection = False
```

**Verify:** Click a point in the Data Plot → napari dims all labels except that cell (yellow). Click a row in the Cell Table → same. Ctrl-click multiple rows in table → multiple cells highlighted, rest dimmed. Click background in napari → all labels return to normal colormap.

---

### Phase 3: Data Plot Multi-Select + Reset View

**Goal:** Add rectangle multi-select (Shift+drag), Ctrl-click additive selection, Reset View button, and Escape to deselect. Defer log-scale and axis-lock to a follow-up PR.

### Research Insight: Custom SelectionViewBox

pyqtgraph's `ViewBox` has a built-in rubber-band rectangle (`rbScaleBox`) used in `RectMode`. By subclassing `ViewBox` and overriding `mouseDragEvent`, we reuse this visual for selection instead of zoom. Use `Shift+drag` for selection, normal drag for pan/zoom — no mode toggle needed.

**Files to modify:**

#### `src/percell4/gui/data_plot.py`

**Custom ViewBox for selection:**

```python
class SelectionViewBox(pg.ViewBox):
    """ViewBox that supports Shift+drag for rectangle selection."""

    sigSelectionComplete = Signal(object)  # QRectF in data coordinates

    def mouseDragEvent(self, ev, axis=None):
        if (ev.button() == Qt.LeftButton and
                ev.modifiers() & Qt.ShiftModifier):
            ev.accept()
            if ev.isStart():
                self.updateScaleBox(ev.buttonDownPos(), ev.pos())
            elif ev.isFinish():
                self.rbScaleBox.hide()
                p1 = ev.buttonDownPos()
                p2 = ev.pos()
                rect = QRectF(p1, p2).normalized()
                data_rect = self.childGroup.mapRectFromParent(rect)
                self.sigSelectionComplete.emit(data_rect)
            else:
                self.updateScaleBox(ev.buttonDownPos(), ev.pos())
        else:
            super().mouseDragEvent(ev, axis)
```

**Wire into PlotWidget:**

```python
vb = SelectionViewBox()
self._plot = pg.PlotWidget(viewBox=vb)
vb.sigSelectionComplete.connect(self._on_rect_selected)
```

**Rectangle selection handler:**

```python
def _on_rect_selected(self, data_rect: QRectF) -> None:
    """Select all points within the rectangle bounds."""
    if self._x_data is None:
        return
    mask = (
        (self._x_data >= data_rect.left()) &
        (self._x_data <= data_rect.right()) &
        (self._y_data >= data_rect.top()) &
        (self._y_data <= data_rect.bottom())
    )
    selected_labels = self._labels_array[mask].tolist()
    if selected_labels:
        self.data_model.set_selection([int(lid) for lid in selected_labels])
```

**Ctrl-click for additive selection:**

```python
def _on_point_clicked(self, scatter_item, points, ev) -> None:
    if not points:
        return
    idx = points[0].index()
    if idx is None or idx >= len(self._labels_array):
        return
    label_id = int(self._labels_array[idx])

    if ev.modifiers() & Qt.ControlModifier:
        # Toggle: add if not selected, remove if selected
        current = set(self.data_model.selected_ids)
        if label_id in current:
            current.discard(label_id)
        else:
            current.add(label_id)
        self.data_model.set_selection(list(current))
    else:
        self.data_model.set_selection([label_id])
```

**Reset View button:**

```python
reset_btn = QPushButton("Reset")
reset_btn.clicked.connect(lambda: self._plot.autoRange())
```

**Escape to clear selection** — install event filter on the plot widget since `keyPressEvent` on the QMainWindow may not receive events consumed by child widgets:

```python
def __init__(self, ...):
    ...
    self._plot.installEventFilter(self)

def eventFilter(self, obj, event) -> bool:
    if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
        self.data_model.set_selection([])
        return True
    return super().eventFilter(obj, event)
```

**Verify:** Shift+drag rectangle → multiple points selected. Normal drag → pan/zoom unchanged. Ctrl-click adds/removes individual points. Escape clears selection. Reset restores zoom.

---

### Phase 4: Filter to Selection

**Goal:** Explicit "Filter to Selection" / "Clear Filter" workflow across all windows.

**Files to modify:**

#### `src/percell4/model.py`

Add `filter_changed` signal, `_filtered_ids` state, `set_filter()` method (clears selection), `filtered_ids` property, `filtered_df` property, `is_filtered` property. Clear filter on `set_measurements()` and `clear()`. (See Architecture Changes section above.)

In `clear()`, emit signals only after all state is consistent:

```python
def clear(self) -> None:
    self._df = pd.DataFrame()
    self._selected_ids = []
    self._filtered_ids = None
    self._active_segmentation = ""
    self._active_mask = ""
    # Emit all signals AFTER state is fully consistent
    self.filter_changed.emit()
    self.data_updated.emit()
    self.selection_changed.emit([])
    self.active_segmentation_changed.emit("")
    self.active_mask_changed.emit("")
```

#### `src/percell4/gui/launcher.py`

**Add filter controls to the Analysis tab** — a prominent group at the top:

```
┌─ Cell Filter ────────────────────────────┐
│ [Filter to Selection] [Clear Filter]     │
│ Status: Showing 12 of 842 cells          │
└──────────────────────────────────────────┘
```

```python
def _on_filter_to_selection(self) -> None:
    selected = self.data_model.selected_ids
    if not selected:
        self.statusBar().showMessage("No cells selected to filter", 3000)
        return
    self.data_model.set_filter(list(selected))

def _on_clear_filter(self) -> None:
    self.data_model.set_filter(None)

def _on_filter_changed(self) -> None:
    if self.data_model.is_filtered:
        n_filtered = len(self.data_model.filtered_df)
        n_total = len(self.data_model.df)
        self._filter_status.setText(f"Showing {n_filtered} of {n_total} cells")
        self._clear_filter_btn.setEnabled(True)
    else:
        self._filter_status.setText("No filter active")
        self._clear_filter_btn.setEnabled(False)
```

#### `src/percell4/gui/data_plot.py`

**React to `filter_changed`:** Use `model.filtered_df` for data display, `model.df` for column discovery.

```python
self.data_model.filter_changed.connect(self._refresh_plot)

def _refresh_plot(self) -> None:
    df = self.data_model.filtered_df  # filtered rows for display
    if df.empty:
        self._base_scatter.setData(x=[], y=[])
        return
    # Column combos still populated from model.df (all columns always available)
    # ... rest of refresh logic using df for row data ...
```

#### `src/percell4/gui/cell_table.py`

**React to `filter_changed` using `QSortFilterProxyModel.filterAcceptsRow()`** — preserves sort state and selection model:

```python
class FilterableProxyModel(QSortFilterProxyModel):
    """Proxy that filters rows by label ID set."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._visible_labels: set[int] | None = None  # None = show all

    def set_filter_labels(self, label_ids: set[int] | None) -> None:
        self._visible_labels = label_ids
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        if self._visible_labels is None:
            return True
        source_model = self.sourceModel()
        label = source_model.get_label_for_row(source_row)
        return label in self._visible_labels if label is not None else False
```

Wire up in CellTableWindow:

```python
self.data_model.filter_changed.connect(self._on_filter_changed)

def _on_filter_changed(self) -> None:
    self._updating_selection = True
    try:
        self._proxy.set_filter_labels(self.data_model.filtered_ids)
    finally:
        self._updating_selection = False
```

The `_updating_selection = True` guard prevents the `selectionChanged` signal from clearing the global selection when `invalidateFilter()` updates visible rows.

#### `src/percell4/gui/viewer.py`

**React to `filter_changed`:** Connect to the same unified `_update_label_display()` method from Phase 2:

```python
self.data_model.filter_changed.connect(self._update_label_display)
```

The method already handles both filter and selection state via `DirectLabelColormap`. No additional code needed — composing filter + selection in a single colormap update is the core benefit of this approach.

#### `src/percell4/gui/phasor_plot.py`

**React to `filter_changed`:** Implement "filtered only" mode (defer "full + highlight" dual-histogram to follow-up).

Add a `labels` parameter to `set_phasor_data()`:

```python
def set_phasor_data(self, g_map, s_map, intensity=None,
                    g_unfiltered=None, s_unfiltered=None,
                    labels=None) -> None:  # NEW parameter
    ...
    self._labels = labels  # (H, W) int32, same shape as g_map
    self._labels_flat = labels.ravel() if labels is not None else None
```

Filter checkbox and debounced histogram refresh:

```python
def __init__(self, ...):
    ...
    self._filter_timer = QTimer()
    self._filter_timer.setSingleShot(True)
    self._filter_timer.setInterval(150)
    self._filter_timer.timeout.connect(self._refresh_histogram)
    self.data_model.filter_changed.connect(self._on_filter_changed)

def _on_filter_changed(self) -> None:
    self._filter_timer.start()  # debounce — restarts if already pending

def _refresh_histogram(self) -> None:
    g, s = self._get_active_gs_maps()
    g_flat = g.ravel()
    s_flat = s.ravel()

    # Apply cell filter if active
    filtered_ids = self.data_model.filtered_ids
    if filtered_ids is not None and self._labels_flat is not None:
        cell_mask = np.isin(self._labels_flat, list(filtered_ids))
        valid = np.isfinite(g_flat) & np.isfinite(s_flat) & cell_mask
    else:
        valid = np.isfinite(g_flat) & np.isfinite(s_flat)

    g_valid = g_flat[valid]
    s_valid = s_flat[valid]
    # ... histogram2d and display ...
```

**Verify:** Select 5 cells in table → click "Filter to Selection" → Data Plot shows 5 points, Cell Table shows 5 rows, napari dims non-filtered labels, Phasor Plot shows only those cells' phasor pixels. Click "Clear Filter" → everything restored.

---

## Deferred to Follow-Up PRs

These features were in the original brainstorm but are deferred to keep this PR focused:

| Feature | Reason | PR |
|---------|--------|----|
| **Napari "Select Cells" mode** (Phase 4 from brainstorm) | YAGNI — multi-select from data plot and cell table covers the primary use case. Single-click in napari already works. | Follow-up if users request |
| **Log-scale toggle** | Tangential to selection/filtering. Requires manual `np.log10()` for scatter (ScatterPlotItem is not auto-transformed by `setLogMode`). | Separate PR |
| **Axis range lock** | Tangential to selection/filtering. Use `sigRangeChangedManually` + `setLimits()` pattern when implemented. | Separate PR |
| **Phasor "full + highlight" dual-histogram** | "Filtered only" mode is sufficient. Dual-histogram requires two ImageItems, z-ordering, and a second colormap. | Follow-up if users request |
| **Navigate/Select mode toggle** | Shift+drag is sufficient for rectangle selection. No permanent mode state needed. | Not needed |

---

## Edge Cases and Mitigations

| Edge Case | Mitigation |
|-----------|-----------|
| Filter active, then re-segment/re-measure | `set_measurements()` clears filter and selection automatically |
| Filter with stale label IDs | `filtered_df` uses `isin()` — non-existent IDs silently produce empty matches |
| Empty selection when "Filter to Selection" clicked | Show status bar message, do nothing |
| Viewer destroyed while filter active | `_is_alive()` check. On viewer recreate, `_update_label_display()` reapplied |
| Feedback loops during multi-select | `_updating_selection` guards with `try/finally` on all windows |
| Shift+drag emitting intermediate selections | Only emit `set_selection()` on `ev.isFinish()`, not during drag |
| Escape key consumed by child widget | Use `installEventFilter` on the plot widget, not `keyPressEvent` on window |
| User paints labels in napari during selection | DirectLabelColormap approach never modifies `layer.data` — edits preserved |
| `set_dataframe` in table clears selection model | Guard with `_updating_selection = True` before `invalidateFilter()` |
| Signal cascade: filter_changed triggers selection_changed from table | Guard in table's `_on_filter_changed` prevents outbound selection signals |
| Phasor histogram recomputation >100ms | Debounced at 150ms via `QTimer.singleShot` |
| `selected_ids` contains IDs outside filtered set | Harmless — `np.isin` and DataFrame.isin() silently ignore non-matching IDs |

## Acceptance Criteria

### Functional Requirements

- [ ] **Cell Table sorting works**: click column header toggles ascending/descending correctly for all numeric types
- [ ] **Single-cell selection syncs to viewer**: click point in scatter or row in table → napari shows only that cell via `show_selected_label`
- [ ] **Multi-cell selection syncs to viewer**: shift/ctrl-click in table → napari dims non-selected labels via `DirectLabelColormap`
- [ ] **Data Plot reset view**: button restores default zoom/pan
- [ ] **Data Plot Shift+drag rectangle select**: selects all points within bounds
- [ ] **Data Plot ctrl-click**: additive single-point selection with toggle
- [ ] **Filter to Selection**: button in launcher Analysis tab, filters all windows
- [ ] **Clear Filter**: button restores all windows to full data
- [ ] **Filter status indicator**: shows "Showing N of M cells" when active
- [ ] **Phasor filtered-only mode**: histogram recomputed from filtered cells' pixels only
- [ ] **Escape key clears selection** from data plot, cell table, and viewer
- [ ] **Filter auto-clears on re-measurement/re-segmentation**
- [ ] **Paint tool edits preserved** during selection/filter (no `layer.data` replacement)

### Non-Functional Requirements

- [ ] No feedback loops during selection propagation (guards with `try/finally` on all windows)
- [ ] Selection of 1000+ cells completes in <200ms (array-based scatter, `np.isin`)
- [ ] Filter change refreshes all windows in <500ms
- [ ] No memory leaks (no `_original_labels` cache — DirectLabelColormap approach)
- [ ] Scatter plot renders 10k+ points in <100ms (array-based `setData`, not dict `addPoints`)

## Dependencies & Risks

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| napari `DirectLabelColormap` API changes | Low | Core napari colormap API; used internally by napari itself |
| Large number of unique labels in DirectLabelColormap | Low | napari uses compact uint8 textures for <256 unique colors regardless of label dtype |
| pyqtgraph `SelectionViewBox` conflicts with existing interactions | Low | Only activates on Shift+drag; normal drag unchanged |
| Phasor histogram recomputation slow for "filtered only" mode | Medium | Debounced at 150ms; vectorized numpy masking |
| Signal cascade on "Filter to Selection" (filter_changed + selection_changed) | Medium | `set_filter()` emits both signals atomically; guards prevent double-refresh |

## References

- `src/percell4/model.py` — CellDataModel, needs filter_changed signal + filtered_ids property
- `src/percell4/gui/data_plot.py:178` — highlight mask Python loop (replace with np.isin)
- `src/percell4/gui/data_plot.py:223` — dict-based addPoints (replace with array setData)
- `src/percell4/gui/cell_table.py:78` — sorting bug (Qt.UserRole returns numpy scalar)
- `src/percell4/gui/viewer.py` — no reverse selection sync exists, needs DirectLabelColormap approach
- `src/percell4/gui/phasor_plot.py` — no label array plumbing, needs labels parameter + debounced filter
- `docs/solutions/ui-bugs/percell4-phases-0-6-napari-qt-learnings.md` — feedback loop prevention patterns
- `docs/solutions/ui-bugs/percell4-phasor-plot-axis-desync.md` — SI prefix gotcha for phasor filter mode
- napari `DirectLabelColormap`: `napari/utils/colormaps/colormap.py:408` — supports `None` default key
- napari `events.selected_label.blocker()`: `napari/utils/events/event.py` — context manager for re-entrancy
- pyqtgraph `ViewBox.mouseDragEvent`: `pyqtgraph/graphicsItems/ViewBox/ViewBox.py:1335` — rubber-band rect reuse
- pyqtgraph `sigRangeChangedManually`: `ViewBox.py:92` — fires only on user zoom, not programmatic
