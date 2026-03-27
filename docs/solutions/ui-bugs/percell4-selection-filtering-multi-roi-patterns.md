---
title: "Cross-Window Selection, Filtering, and Multi-ROI Patterns"
category: ui-bugs
tags: [napari, pyqtgraph, Qt, signals, colormap, ROI, lambda, filtering, measurement, performance]
module: [gui/viewer, gui/phasor_plot, gui/data_plot, gui/cell_table, model, measure/measurer]
date: 2026-03-27
symptom: "Multiple interconnected issues when building cross-window selection sync, cell filtering, and multi-ROI phasor measurement: data mutation in napari labels, double-repaint on filter, stale lambda closures after ROI removal, sort state loss during filtering, duplicated measurement iteration"
root_cause: "Each issue had a distinct root cause but they share a theme: naive approaches to multi-window state synchronization create subtle bugs. napari labels are not meant to be mutated for visual filtering. Qt signals fire synchronously causing cascading repaints. Lambda closures capture references not values. QSortFilterProxyModel loses state on source model reset."
---

# Cross-Window Selection, Filtering, and Multi-ROI Patterns

## Overview

This document captures 9 patterns discovered while building cross-window cell selection, filtering, and multi-ROI phasor measurement for PerCell4. Each pattern addresses a specific failure mode in the Qt/napari/pyqtgraph desktop architecture.

---

## Pattern 1: DirectLabelColormap for Multi-Cell Highlighting

**Problem:** Highlighting selected/filtered cells in napari by mutating `labels_layer.data` (zeroing unselected labels) causes data loss, 128MB memory churn per selection change, and destroys user paint edits.

**Wrong approach:**
```python
# WRONG: mutates data, requires caching original, loses paint edits
original = layer.data.copy()  # 64MB
filtered = original.copy()     # another 64MB
filtered[~np.isin(original, selected_ids)] = 0
layer.data = filtered  # triggers napari events, feedback loops
```

**Correct approach:** Use `DirectLabelColormap` with a `None` default key for GPU-side rendering:
```python
from napari.utils.colormaps import DirectLabelColormap

color_dict = {0: "transparent", None: [0.5, 0.5, 0.5, 0.15]}  # dim default
for lid in selected_ids:
    color_dict[lid] = [1.0, 1.0, 0.0, 0.8]  # bright yellow

with labels_layer.events.colormap.blocker():
    labels_layer.colormap = DirectLabelColormap(color_dict=color_dict)
labels_layer.refresh(extent=False)
```

**Why:** The colormap is a GPU-side lookup table. `layer.data` is never touched. Fully reversible by restoring the saved original colormap. The `None` key is the fallback for any label ID not explicitly listed.

**Corollary — save/restore the original colormap:**
```python
def _save_colormap(self, layer):
    if layer.name not in self._original_colormaps:
        self._original_colormaps[layer.name] = layer.colormap

def _restore_colormap(self, layer):
    if layer.name in self._original_colormaps:
        with layer.events.colormap.blocker():
            layer.colormap = self._original_colormaps.pop(layer.name)
        layer.refresh(extent=False)
```

---

## Pattern 2: Signal Coalescing with QTimer.singleShot(0)

**Problem:** `set_filter()` emits `filter_changed` then `selection_changed` synchronously. Both connect to the same expensive display update, causing a double repaint with the first one using stale selection state.

**Correct approach:**
```python
def _schedule_label_update(self, *args):
    if not self._display_update_pending:
        self._display_update_pending = True
        QTimer.singleShot(0, self._update_label_display)

def _update_label_display(self):
    self._display_update_pending = False
    # ... expensive work runs exactly once ...
```

**Why:** `QTimer.singleShot(0)` posts to the event queue. Both signals fire within the same call stack, but the flag prevents a second timer. The update runs once after both signals have been processed and state is consistent.

---

## Pattern 3: Identity-Based Widget Lookup for pyqtgraph ROI Lambdas

**Problem:** Connecting `sigRegionChangeFinished` with `lambda _idx=idx: handler(_idx)` captures the index at creation time. After removing an ROI and renumbering, surviving ROIs' lambdas have stale indices — they silently update the wrong ROI or get dropped by bounds checks.

**Wrong approach:**
```python
roi.sigRegionChangeFinished.connect(lambda _roi, _idx=idx: self._on_roi_moved(_idx))
```

**Correct approach:** Capture the widget object by identity:
```python
roi.sigRegionChangeFinished.connect(
    lambda _roi, _w=widget: self._on_roi_moved_widget(_w)
)

def _on_roi_moved_widget(self, widget):
    if widget not in self._roi_widgets:
        return  # removed, ignore stale signal
    # ... update widget.phasor_roi from widget.roi position ...
```

**Why:** Object identity is stable across list reordering. The `not in` check safely ignores signals from deleted ROIs.

**Note:** `sigRegionChangeFinished` passes the ROI object as the first positional argument. The lambda must accept it (`_roi`) or it overrides the default `_w` parameter.

---

## Pattern 4: FilterableProxyModel Preserves Sort State

**Problem:** Filtering a `QTableView` by replacing the source model's DataFrame (`beginResetModel`/`endResetModel`) destroys sort column, direction, and scroll position.

**Correct approach:** Subclass `QSortFilterProxyModel` with a label filter set:
```python
class FilterableProxyModel(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self._visible_labels: set[int] | None = None

    def set_filter_labels(self, label_ids: set[int] | None):
        self._visible_labels = label_ids
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent) -> bool:
        if self._visible_labels is None:
            return True
        label = self.sourceModel().get_label_for_row(source_row)
        return label in self._visible_labels if label is not None else False
```

**Why:** `invalidateFilter()` re-evaluates row visibility without touching the source model. Sort state lives in the proxy and survives filter changes.

**Critical:** Guard `_on_filter_changed` with `_updating_selection = True` in `try/finally`. `invalidateFilter()` may trigger `selectionChanged` on the proxy's selection model, which would otherwise fire back to the data model and clear the selection.

---

## Pattern 5: Per-ROI Mask Caching for Live Preview

**Problem:** Computing N elliptical masks on a 4096x4096 image for every ROI drag event is too slow for interactive preview.

**Correct approach:** Cache each ROI's boolean mask on the widget dataclass. Invalidate only the moved ROI:
```python
@dataclass
class _ROIWidget:
    cached_mask: np.ndarray | None = None

def _on_roi_moved_widget(self, widget):
    widget.cached_mask = None  # invalidate ONLY this one
    self._preview_timer.start()  # debounced at 100ms

def _compute_combined_mask(self):
    mask = np.zeros(g.shape, dtype=np.uint8)
    for widget in self._roi_widgets:
        if widget.cached_mask is None:
            widget.cached_mask = phasor_roi_to_mask(g, s, ...)  # expensive
        mask[widget.cached_mask] = widget.phasor_roi.label
    return mask
```

**Invalidate all caches** when G/S maps change (harmonic switch, filtered/unfiltered toggle).

---

## Pattern 6: Shared `_iter_cell_crops` Generator

**Problem:** Both `measure_cells` and `measure_cells_multi_roi` need `find_objects()` + `regionprops()` + crop extraction. Duplicating this is a maintenance trap — ~50 lines of identical boilerplate.

**Correct approach:** Extract a generator yielding `_CellCrop` dataclasses:
```python
def _iter_cell_crops(image, labels) -> Iterator[_CellCrop]:
    slices = find_objects(labels)  # once
    props = regionprops(labels)    # once
    for prop in props:
        sl = slices[prop.label - 1]
        cell_mask = labels[sl] == prop.label
        yield _CellCrop(label=prop.label, image_crop=image[sl],
                         cell_mask=cell_mask, sl=sl, ...)
```

Both functions consume the same generator. The `_CellCrop.sl` field lets downstream code index into masks or other arrays using the same bounding box slice.

---

## Pattern 7: Array-Based setData for pyqtgraph Scatter

**Problem:** `ScatterPlotItem.addPoints([{"pos": (x, y), "data": id}, ...])` creates N Python dicts. For 10k points this is 10-20x slower than the array API.

**Correct approach:**
```python
self._base_scatter.setData(x=x_array, y=y_array)
self._labels_array = labels  # index-aligned, for click lookup
```

For click handling, use `points[0].index()` to look up the label:
```python
idx = points[0].index()
label_id = int(self._labels_array[idx])
```

---

## Pattern 8: `filtered_df` Caching

**Problem:** 4+ windows call `model.filtered_df` on every `filter_changed`. Each call runs `DataFrame.isin()` redundantly.

**Correct approach:** Cache with invalidation:
```python
@property
def filtered_df(self):
    if self._filtered_ids is None:
        return self._df
    if self._filtered_df_cache is None:
        self._filtered_df_cache = self._df[self._df["label"].isin(self._filtered_ids)]
    return self._filtered_df_cache

def set_filter(self, label_ids):
    self._filtered_df_cache = None  # invalidate
    ...
```

---

## Pattern 9: `_apply_cell_filter` Helper

**Problem:** The "zero out non-filtered labels" pattern was duplicated in `_on_measure_cells` and `_on_analyze_particles`.

**Correct approach:** Extract to a shared helper:
```python
def _apply_cell_filter(self, labels):
    filtered_ids = self.data_model.filtered_ids
    if filtered_ids is not None:
        labels = labels.copy()
        labels[~np.isin(labels, filtered_ids)] = 0
        if labels.max() == 0:
            self.statusBar().showMessage("No filtered cells to process")
            return None
    return labels
```

---

## Related Documentation

- [napari + Qt learnings (phases 0-6)](percell4-phases-0-6-napari-qt-learnings.md) — feedback loop prevention, `event.source.*` pattern, `_is_alive()` checks
- [Phasor plot axis desync](percell4-phasor-plot-axis-desync.md) — SI prefix and auto-range gotchas for ROI coordinate systems
- [FLIM phasor troubleshooting](percell4-flim-phasor-troubleshooting.md) — `setRect` vs `setTransform` for ImageItem
- [Code review findings phases 0-6](../architecture-decisions/percell4-code-review-findings-phases-0-6.md) — CellDataModel signal hub validation

## Prevention Checklist

- [ ] Never mutate `labels_layer.data` for visual filtering — use `DirectLabelColormap`
- [ ] Save original colormap before replacing; restore on clear
- [ ] Use `QTimer.singleShot(0)` to coalesce rapid-fire signals to the same handler
- [ ] Capture pyqtgraph ROI widget objects by identity in lambdas, not indices
- [ ] Use `FilterableProxyModel.invalidateFilter()` instead of replacing DataFrame
- [ ] Wrap all `_updating_selection` guards in `try/finally`
- [ ] Cache per-ROI masks; invalidate only the moved ROI
- [ ] Use `ScatterPlotItem.setData(x=, y=)` not dict-based `addPoints`
- [ ] Cache `filtered_df` with invalidation on `set_filter`/`set_measurements`
- [ ] Extract duplicated label-filtering patterns into shared helpers
