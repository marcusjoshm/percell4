> **SUPERSEDED — 2026-04-10.** This reference describes the pre-refactor
> signal topology with 5 separate signals (`data_updated`, `selection_changed`,
> `filter_changed`, `active_segmentation_changed`, `active_mask_changed`) and
> `_updating_selection` re-entrancy guards. That architecture was replaced by
> a single `state_changed` signal carrying a `StateChange` descriptor (commit
> `700a9c0`). See `src/percell4/model.py` for the current contract. Kept here
> for historical context only.

# PerCell4 Window Interactions Reference

Troubleshooting guide for how user actions in one window propagate to all others.

---

## Architecture Overview

All windows communicate through a single `CellDataModel` instance. Windows never talk to each other directly. The model holds:

- **DataFrame** (`df`) — one row per cell, all measurements
- **Selection** (`selected_ids`) — list of currently highlighted cell label IDs
- **Filter** (`filtered_ids`) — set of visible cell IDs, or `None` (show all)
- **Active layers** — names of the active segmentation and mask layers

```
┌──────────┐   ┌────────────┐   ┌───────────┐   ┌─────────────┐
│  Napari   │   │ Data Plot  │   │ Cell Table│   │ Phasor Plot │
│  Viewer   │   │  (scatter) │   │           │   │             │
└─────┬─────┘   └─────┬──────┘   └─────┬─────┘   └──────┬──────┘
      │               │               │                │
      └───────────────┴───────┬───────┴────────────────┘
                              │
                     ┌────────┴────────┐
                     │  CellDataModel  │
                     │  (Qt signals)   │
                     └────────┬────────┘
                              │
                     ┌────────┴────────┐
                     │    Launcher     │
                     │  (operations)   │
                     └─────────────────┘
```

**Source:** `src/percell4/model.py`

```python
class CellDataModel(QObject):
    data_updated = Signal()
    selection_changed = Signal(list)       # list of selected label IDs
    filter_changed = Signal()
    active_segmentation_changed = Signal(str)
    active_mask_changed = Signal(str)
```

---

## Signal Flow Quick Reference

| Signal | Emitted by | Triggers |
|--------|-----------|----------|
| `selection_changed` | Any window selecting cells | Viewer highlights, plot highlights, table row select |
| `filter_changed` | Launcher filter buttons | Viewer dims non-filtered, plot redraws subset, table hides rows, phasor restricts histogram |
| `data_updated` | Launcher after measurement | Plot rebuilds columns, table reloads |
| `active_segmentation_changed` | Launcher Data tab | Viewer uses new layer for highlighting |
| `active_mask_changed` | Phasor "Apply Mask" | Launcher uses mask for measurement |

---

## 1. Cell Selection

### User clicks a cell label in Napari Viewer

**Flow:** Napari `selected_label` event → ViewerWindow → `CellDataModel.set_selection()` → all windows

**Source:** `src/percell4/gui/viewer.py:212-229`

```python
def _on_label_selected(self, event) -> None:
    """Forward label selection to CellDataModel."""
    if self._updating_selection:
        return
    try:
        source = event.source
        label_id = source.selected_label
    except AttributeError:
        return

    self._updating_selection = True
    try:
        if label_id == 0:
            self.data_model.set_selection([])
        else:
            self.data_model.set_selection([label_id])
    finally:
        self._updating_selection = False
```

**Effect on each window:**

- **Viewer:** Highlights the cell yellow via `DirectLabelColormap`, dims all others to gray (`viewer.py:239-313`)
- **Data Plot:** Red highlight circle on the selected point (`data_plot.py:203-234`)
- **Cell Table:** Row selected and scrolled into view (`cell_table.py:234-276`)
- **Phasor Plot:** No direct reaction (phasor doesn't subscribe to `selection_changed`)

### User clicks a point in the Data Plot

**Source:** `src/percell4/gui/data_plot.py:282-300`

```python
def _on_point_clicked(self, scatter_item, points, ev) -> None:
    """Handle click on scatter point -> select cell. Ctrl-click toggles."""
    if not points or self._labels_array is None:
        return
    idx = points[0].index()
    label_id = int(self._labels_array[idx])

    if ev.modifiers() & Qt.ControlModifier:
        # Ctrl-click: toggle this label in/out of selection
        current = set(self.data_model.selected_ids)
        if label_id in current:
            current.discard(label_id)
        else:
            current.add(label_id)
        self.data_model.set_selection(list(current))
    else:
        self.data_model.set_selection([label_id])
```

**Interactions:**
- **Single click:** Selects one cell
- **Ctrl+click:** Toggles a cell in/out of a multi-selection
- **Shift+drag:** Rectangle selection of all enclosed points (`data_plot.py:302-314`)
- **Escape:** Clears selection (`data_plot.py:320-325`)

### User clicks a row in the Cell Table

**Source:** `src/percell4/gui/cell_table.py:296-313`

```python
def _on_table_selection_changed(self, selected, deselected) -> None:
    """Forward table row selection to CellDataModel."""
    if self._updating_selection:
        return  # Avoid feedback loop

    rows = set()
    for index in self._table.selectionModel().selectedRows():
        source_index = self._proxy.mapToSource(index)
        rows.add(source_index.row())

    label_ids = []
    for row in sorted(rows):
        lid = self._model.get_label_for_row(row)
        if lid is not None:
            label_ids.append(lid)

    if label_ids:
        self.data_model.set_selection(label_ids)
```

**Note:** The table supports `ExtendedSelection` mode — click, Shift+click for range, Ctrl+click for toggle.

### Feedback Loop Prevention

Every window guards against re-entrant updates with `_updating_selection`:

```python
# In each window's signal handler:
if self._updating_selection:
    return
self._updating_selection = True
try:
    # ... update display ...
finally:
    self._updating_selection = False
```

The Viewer additionally coalesces rapid-fire signals (e.g., `set_filter` emits `filter_changed` then `selection_changed`) into a single repaint:

**Source:** `src/percell4/gui/viewer.py:231-237`

```python
def _schedule_label_update(self, *args) -> None:
    """Coalesce rapid-fire signals into a single display update."""
    if not self._display_update_pending:
        self._display_update_pending = True
        QTimer.singleShot(0, self._update_label_display)
```

---

## 2. Cell Filter

### User clicks "Filter to Selection" in Launcher

**Source:** `src/percell4/gui/launcher.py:1107-1113`

```python
def _on_filter_to_selection(self) -> None:
    """Filter all windows to show only the currently selected cells."""
    selected = self.data_model.selected_ids
    if not selected:
        self.statusBar().showMessage("No cells selected to filter", 3000)
        return
    self.data_model.set_filter(list(selected))
```

**What `set_filter` does internally** (`model.py:69-78`):

```python
def set_filter(self, label_ids: list[int] | None) -> None:
    self._filtered_ids = set(label_ids) if label_ids is not None else None
    self._filtered_df_cache = None
    self._selected_ids = []          # auto-clears selection
    self.filter_changed.emit()       # signal 1
    self.selection_changed.emit([])  # signal 2
```

**Effect on each window:**

| Window | Subscribes to | Behavior |
|--------|--------------|----------|
| **Viewer** | `filter_changed` | Non-filtered cells become fully transparent; filtered cells shown as cyan; selected cells (if any) shown as yellow (`viewer.py:277-311`) |
| **Data Plot** | `filter_changed` | Redraws scatter using `model.filtered_df` — only filtered cells appear (`data_plot.py:238-278`) |
| **Cell Table** | `filter_changed` | Proxy model hides rows not in filter set (`cell_table.py:278-292`) |
| **Phasor Plot** | `filter_changed` | Debounced histogram refresh — only pixels belonging to filtered cells are binned (`phasor_plot.py:566-568, 582-585`) |
| **Launcher** | `filter_changed` | Updates status label: "Showing N of M cells" (`launcher.py:1119-1132`) |

### User clicks "Clear Filter" in Launcher

```python
def _on_clear_filter(self) -> None:
    self.data_model.set_filter(None)
```

All windows revert to showing all cells.

### Viewer Display Logic for Filter + Selection

**Source:** `src/percell4/gui/viewer.py:239-313`

```python
# No selection, no filter → restore original colormap
# Single cell, no filter → napari's built-in show_selected_label
# Multi-cell and/or filter → DirectLabelColormap:

color_dict = {0: "transparent"}

if visible_ids and highlight_ids:
    # Both filter + selection active
    color_dict[None] = [0.0, 0.0, 0.0, 0.0]      # hide non-filtered
    for lid in visible_ids:
        if lid in highlight_ids:
            color_dict[lid] = [1.0, 1.0, 0.0, 0.8]  # selected = yellow
        else:
            color_dict[lid] = [0.5, 0.5, 0.5, 0.15]  # filtered = dim gray
elif visible_ids:
    # Filter only (no selection)
    color_dict[None] = [0.0, 0.0, 0.0, 0.0]      # hide non-filtered
    for lid in visible_ids:
        color_dict[lid] = [0.3, 0.8, 0.8, 0.5]    # filtered = cyan
elif highlight_ids:
    # Selection only (no filter)
    color_dict[None] = [0.5, 0.5, 0.5, 0.15]      # dim everything
    for lid in highlight_ids:
        color_dict[lid] = [1.0, 1.0, 0.0, 0.8]    # selected = yellow
```

---

## 3. Measurements (data_updated)

### User clicks "Measure Cells" in Launcher

**Source:** `src/percell4/gui/launcher.py:1358-1428`

```python
def _on_measure_cells(self) -> None:
    # 1. Collect all image layers from viewer
    image_layers = {}
    for layer in viewer_win.viewer.layers:
        if layer.__class__.__name__ == "Image":
            image_layers[layer.name] = layer.data.astype(np.float32)

    # 2. Get active segmentation labels
    seg_name = self.data_model.active_segmentation
    # ... find layer by name ...

    # 3. Get active mask (optional, for multi-ROI measurement)
    mask_name = self.data_model.active_mask
    # ... find layer by name ...

    # 4. Compute measurements
    if is_multi_roi:
        df = measure_multichannel_multi_roi(image_layers, labels, mask, roi_names)
    else:
        df = measure_multichannel(image_layers, labels, mask=mask)

    # 5. Push to model → triggers all windows
    self.data_model.set_measurements(df)
```

**What `set_measurements` does** (`model.py:80-91`):

```python
def set_measurements(self, df: pd.DataFrame) -> None:
    self._df = df
    self._filtered_ids = None          # clears stale filter
    self._filtered_df_cache = None
    self._selected_ids = []            # clears stale selection
    self.filter_changed.emit()         # signal 1
    self.data_updated.emit()           # signal 2
    self.selection_changed.emit([])    # signal 3
```

**Effect on each window:**

| Window | Reaction |
|--------|----------|
| **Data Plot** | `_on_data_updated()` rebuilds X/Y column dropdowns from new numeric columns, redraws scatter (`data_plot.py:154-201`) |
| **Cell Table** | `_on_data_updated()` replaces table model with new DataFrame, resizes columns (`cell_table.py:221-232`) |
| **Viewer** | Indirectly via `filter_changed` + `selection_changed` — clears highlighting |
| **Phasor Plot** | `filter_changed` triggers histogram refresh (filter was cleared) |

---

## 4. Phasor Plot Interactions

### User drags/resizes a phasor ROI ellipse

**Source:** `src/percell4/gui/phasor_plot.py:396-409`

```python
def _on_roi_moved_widget(self, widget: _ROIWidget) -> None:
    """Recompute only the changed ROI's cached mask."""
    pos = widget.roi.pos()
    size = widget.roi.size()
    widget.phasor_roi.center = (
        pos.x() + abs(size.x()) / 2,
        pos.y() + abs(size.y()) / 2,
    )
    widget.phasor_roi.radii = (abs(size.x()) / 2, abs(size.y()) / 2)
    self._update_ellipse_curve_for(widget)
    widget.cached_mask = None       # invalidate cache
    self._preview_timer.start()     # debounced preview update (100ms)
```

After 100ms debounce, `_update_preview()` computes the combined mask from all visible ROIs and pushes a `_phasor_roi_preview` layer to the napari viewer.

**Note:** The phasor plot accesses the viewer directly through `self._launcher._windows.get("viewer")` — this is the one exception to the "no direct window communication" rule.

### User clicks "Apply Visible as Mask"

**Source:** `src/percell4/gui/phasor_plot.py:644-680`

```python
def _on_apply_mask(self) -> None:
    mask = self._compute_combined_mask()

    # Remove preview layer from viewer
    viewer_win._viewer.layers.remove("_phasor_roi_preview")

    # Add final mask layer with ROI colors
    viewer_win.add_mask(mask, name="phasor_roi", color_dict=color_dict)

    # Save to HDF5
    store.write_mask("phasor_roi", mask)

    # Notify model → launcher knows which mask to use for measurements
    self.data_model.set_active_mask("phasor_roi")
```

**Downstream:** When the user next clicks "Measure Cells", the launcher reads `data_model.active_mask` to find the mask layer and computes per-ROI measurements.

### Phasor respects cell filter

When a cell filter is active, the phasor histogram only includes pixels from filtered cells:

**Source:** `src/percell4/gui/phasor_plot.py:580-585`

```python
# Inside _refresh_histogram():
filtered_ids = self.data_model.filtered_ids
if filtered_ids is not None and self._labels_flat is not None:
    cell_mask = np.isin(self._labels_flat, list(filtered_ids))
    valid = valid & cell_mask
```

The combined mask also restricts to filtered cells (`phasor_plot.py:467-472`):

```python
# Inside _compute_combined_mask():
filtered_ids = self.data_model.filtered_ids
if filtered_ids is not None and self._labels is not None:
    cell_mask = np.isin(self._labels, list(filtered_ids))
    mask[~cell_mask] = 0
```

---

## 5. Active Layer Changes

### User selects segmentation/mask in Launcher Data tab

The launcher has dropdown combos for active segmentation and active mask. Changing these calls:

```python
self.data_model.set_active_segmentation(name)  # emits active_segmentation_changed
self.data_model.set_active_mask(name)           # emits active_mask_changed
```

**Effect:**
- **Viewer:** `_get_active_labels_layer()` uses `active_segmentation` to decide which labels layer gets selection/filter highlighting
- **Launcher:** Uses `active_segmentation` and `active_mask` when running "Measure Cells"

---

## 6. Dataset Lifecycle

### User loads a dataset

1. Launcher opens `.h5` file via `DatasetStore`
2. Populates viewer with image/label/mask layers
3. Sets `active_segmentation` and `active_mask` on model
4. If measurements exist in HDF5, calls `model.set_measurements(df)`

### User closes a dataset

**Source:** `src/percell4/model.py:125-140`

```python
def clear(self) -> None:
    """Reset all state. Emits all signals AFTER state is fully consistent."""
    self._df = pd.DataFrame()
    self._selected_ids = []
    self._filtered_ids = None
    self._filtered_df_cache = None
    self._active_segmentation = ""
    self._active_mask = ""
    self.filter_changed.emit()
    self.data_updated.emit()
    self.selection_changed.emit([])
    self.active_segmentation_changed.emit("")
    self.active_mask_changed.emit("")
```

All windows reset to empty state.

---

## 7. Window Creation and Lifecycle

Windows are created lazily on first access and reused:

**Source:** `src/percell4/gui/launcher.py:706-722`

```python
def _get_or_create_window(self, key: str) -> QWidget:
    if key not in self._windows:
        factories = {
            "viewer": lambda: ViewerWindow(self.data_model),
            "data_plot": lambda: DataPlotWindow(self.data_model),
            "phasor_plot": lambda: PhasorPlotWindow(self.data_model, launcher=self),
            "cell_table": lambda: CellTableWindow(self.data_model),
        }
        if key in factories:
            self._windows[key] = factories[key]()
    return self._windows.get(key)
```

Closing a window hides it rather than destroying it (`closeEvent` calls `hide()` + `event.ignore()`). The napari viewer is special — if the user force-closes it (Qt deletes the window), `ViewerWindow._ensure_viewer()` recreates it on next access.

---

## Troubleshooting Checklist

| Symptom | Check |
|---------|-------|
| Selection in viewer doesn't highlight in plot/table | Is `data_model.df` populated? Selection sync requires measurements. |
| Filter shows wrong cells | Check `model.filtered_ids` — set_filter auto-clears selection. |
| Viewer shows all cells dimmed gray | A filter or selection is active. Check `model.is_filtered` and `model.selected_ids`. |
| Phasor histogram is empty | Check `model.filtered_ids` — if filter is active, only filtered cells' pixels are shown. |
| Measurement fails "no active segmentation" | Check Data tab dropdown — `model.active_segmentation` must name an existing labels layer. |
| Phasor ROI preview not showing | Viewer must be open. Phasor accesses viewer through `launcher._windows["viewer"]`. |
| Clicking in table doesn't select in viewer | Check `_updating_selection` flag — may be stuck `True` from an exception. |
| Plot shows fewer points than table | Plot uses `model.filtered_df`; if filter active, only filtered cells plot. Table uses proxy filter separately — they should match. |
