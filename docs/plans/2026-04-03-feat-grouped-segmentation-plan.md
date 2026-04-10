---
title: "feat: Grouped Segmentation with Interactive Thresholding"
type: feat
date: 2026-04-03
deepened: 2026-04-03
brainstorm: docs/brainstorms/2026-04-03-grouped-segmentation-brainstorm.md
---

# feat: Grouped Segmentation with Interactive Thresholding

## Enhancement Summary

**Deepened on:** 2026-04-03
**Agents used:** Python reviewer, Performance oracle, Architecture strategist, Simplicity reviewer, Pattern recognition specialist, Napari dock widget researcher, Institutional learnings researcher

### Key Improvements
1. Fixed critical bugs in `mode_intensity` (broken on float images) and `sg_ratio` (size-dependent, should use means not sums)
2. Added performance optimizations: lookup-table group masks, in-place mask combination, pre-compute Gaussian on full image
3. Added napari/Qt gotchas from institutional learnings: signal coalescing, blockSignals, blending mode, pyqtgraph axis fixes
4. Improved API design: `GroupingResult` uses pd.Series, `GroupStatus` enum, consistent parameter naming
5. Clarified store.py needs NO modification (existing generic API works), corrected `add_mask` argument order

## Overview

Intensity-based auto-thresholding for polyclonal protein expression data. Cells are grouped by expression level (GMM or K-means), each group is thresholded interactively with napari-based QC, and the resulting binary masks are unioned into a single mask. Group membership is stored as an integer column in the measurements DataFrame.

This is a proven workflow from PerCell v1/v2 and PerCell3, adapted to PerCell4's architecture (CellDataModel signals, napari viewer, HDF5 storage).

## Problem Statement

A single global intensity threshold fails on polyclonal expression data — dim cells are missed or bright cells are over-segmented. Grouping cells by expression level and thresholding each group independently adapts the threshold to each subpopulation, producing accurate binary masks across the full dynamic range.

## Technical Approach

### Architecture

The feature adds three new modules and one new UI panel:

```
src/percell4/
├── measure/
│   ├── metrics.py          ← ADD mode_intensity, sg_ratio metrics
│   ├── grouper.py           ← NEW: GMM/K-means clustering
│   └── thresholding.py      (existing, reused as-is)
├── gui/
│   ├── grouped_seg_panel.py  ← NEW: launcher panel (QWidget)
│   ├── threshold_qc.py       ← NEW: napari QC dock widget + controller
│   └── launcher.py           ← MODIFY: add panel to Workflows tab
└── model.py                   ← MODIFY: add group column persistence
```

**Data flow:**

```
User selects channel + metric + algorithm
         ↓
Auto-measure if needed (Worker thread)
         ↓
Cluster cells (Worker thread) → GroupingResult
         ↓
Group QC visualization (temporary colored labels + histogram dock)
  User clicks "Proceed" or "Re-group"
         ↓
Per-group interactive thresholding loop (napari)
  Accept / Skip / Skip Remaining / Back
         ↓
Union accepted masks → write_mask() to HDF5
Add group column to DataFrame → set_measurements()
```

### Implementation Phases

#### Phase 1: New Metrics

Add `mode_intensity` and `sg_ratio` to the measurement system.

**`src/percell4/measure/metrics.py`**

```python
# mode_intensity: most frequent intensity value in the cell mask
# Uses scipy.stats.mode for correctness on both integer and float images
# np.bincount only works on non-negative integers and breaks on float data
def mode_intensity(image: NDArray, mask: NDArray[bool]) -> float:
    values = image[mask]
    if len(values) == 0:
        return 0.0
    result = scipy.stats.mode(values, keepdims=False)
    return float(result.mode)


# sg_ratio: signal-to-ground contrast ratio
# mean(pixels >= 95th percentile) / mean(pixels <= 50th percentile)
# Uses MEANS not sums to be cell-size-invariant (sums are biased by area)
# Returns NaN when denominator is zero (common when median is zero in fluorescence)
def sg_ratio(image: NDArray, mask: NDArray[bool]) -> float:
    values = image[mask]
    if len(values) == 0:
        return 0.0
    p50, p95 = np.percentile(values, [50, 95])  # single sort, not two
    signal = values[values >= p95]
    ground = values[values <= p50]
    if len(ground) == 0 or ground.mean() == 0:
        return float("nan")  # NaN for div-by-zero is a documented departure from 0.0 convention
    return float(signal.mean() / ground.mean())
```

Register both in `BUILTIN_METRICS` dict. They automatically appear in the metric configuration dialog since the launcher iterates `BUILTIN_METRICS` at `launcher.py:1465`.

### Research Insights (Phase 1)

**Bug fixes from review:**
- `mode_intensity` original used `np.bincount(values.astype(int))` which breaks on float images (post-smoothing, normalized data) and negative values. Use `scipy.stats.mode` instead.
- `sg_ratio` original used sums which makes the ratio cell-size-dependent (more pixels = larger ground sum = smaller ratio). Using means makes it a true contrast metric independent of cell area.
- `sg_ratio` computes both percentiles in a single `np.percentile(values, [50, 95])` call — one sort instead of two, ~2x faster per cell.

**Convention note:** Existing metrics return `0.0` for empty masks. New metrics follow this convention for empty masks. `sg_ratio` returns `NaN` only for the division-by-zero case (non-empty mask but ground mean is zero), which is a documented departure.

**Parameter naming:** Existing metrics use `(image, mask)` not `(image_crop, cell_mask)`. New metrics match the established convention.

**Files:** `src/percell4/measure/metrics.py`

---

#### Phase 2: Cell Grouper Module

New module for clustering cells by a metric value.

**`src/percell4/measure/grouper.py`**

```python
@dataclass
class GroupingResult:
    """Result of clustering cells into groups."""
    group_assignments: pd.Series   # index=cell_label, value=group_id (1-indexed, int)
    n_groups: int
    group_means: list[float]       # mean metric value per group (ascending)


def group_cells_gmm(
    values: NDArray[np.float64],
    cell_labels: NDArray[np.int32],
    criterion: str = "bic",        # "bic" or "silhouette"
    max_components: int = 10,
    min_cells: int = 10,
) -> GroupingResult: ...


def group_cells_kmeans(
    values: NDArray[np.float64],
    cell_labels: NDArray[np.int32],
    n_clusters: int,
    min_cells: int = 10,
) -> GroupingResult: ...
```

**Key behaviors:**
- Groups always ordered by ascending mean metric value (group 1 = lowest)
- If fewer than `min_cells`, return single group
- GMM: test 1..max_k components, select best by BIC or silhouette score
- K-means: user specifies k directly
- Pure numpy/sklearn — no GUI or HDF5 dependency

### Research Insights (Phase 2)

**API design improvement:** Original plan used parallel arrays (`labels` and `cell_labels` coupled by index position). This is fragile. Using a `pd.Series` with `index=cell_label, value=group_id` makes the mapping explicit and eliminates the downstream dict construction in Phase 6 — `df["label"].map(result.group_assignments)` is one line.

**Metadata fields removed:** `algorithm`, `metric`, `channel`, `criterion` were echoing back input parameters the caller already knows. The dataclass holds results, not inputs.

**Silhouette scaling concern:** Silhouette score is O(n^2) for pairwise distances. With 2000 cells this is fine (~100ms). For datasets >5000 cells, subsample to 5000 for the silhouette computation. BIC is O(n) and should be the recommended default.

**Dependencies:** `scikit-learn` (already in requirements for Cellpose)

**Files:** `src/percell4/measure/grouper.py`

---

#### Phase 3: Grouped Segmentation Panel (Launcher UI)

New extracted panel class added to the **Workflows** tab (currently placeholder content).

**`src/percell4/gui/grouped_seg_panel.py`**

Panel layout:

```
┌─────────────────────────────────┐
│ Grouped Segmentation            │
├─────────────────────────────────┤
│ Channel:    [dropdown ▼]        │
│ Metric:     [dropdown ▼]        │
│ Algorithm:  [dropdown ▼]        │
│                                 │
│ ── GMM Options ──               │
│ Criterion:  [BIC / Silhouette]  │
│ Max components: [spinbox: 10]   │
│                                 │
│ ── K-means Options ──           │
│ Number of groups: [spinbox: 3]  │
│                                 │
│ ── Threshold Options ──         │
│ Gaussian σ: [spinbox: 0.0]     │
│                                 │
│ [Run Grouped Segmentation]      │
│                                 │
│ Status: Ready                   │
└─────────────────────────────────┘
```

**Behavior:**
- Channel dropdown populated from loaded images (same as existing thresholding)
- Metric dropdown populated from `BUILTIN_METRICS` keys
- Algorithm dropdown: GMM, K-means. Switching toggles visibility of algorithm-specific options
- GMM/K-means option groups shown/hidden based on algorithm selection
- Gaussian σ set once here, applied uniformly to all groups during QC (not adjustable per-group)
- "Run" button:
  1. Check if measurements exist for the selected channel+metric. If not, auto-compute via `measure_cells()` in a Worker thread
  2. Extract metric values for filtered cells (or all cells if no filter)
  3. Run clustering in a Worker thread
  4. On completion, launch the Group QC visualization

**Re-run detection:**
- Before running, check if a mask named `grouped_{channel}_{metric}` exists in the HDF5 store via `store.list_masks()`
- If it exists, show a `QMessageBox` with "Overwrite" / "Save as new name" / "Cancel" options
- "Save as new name" appends `_v2`, `_v3`, etc. (auto-increment by scanning existing mask names)

**Filter integration:**
- If `data_model.is_filtered`, extract metric values only for `data_model.filtered_ids`
- Pass filtered cell labels to the grouper
- Unfiltered cells get `NaN` in the group column

**Files:** `src/percell4/gui/grouped_seg_panel.py`, `src/percell4/gui/launcher.py` (add panel to Workflows tab)

---

#### Phase 4: Group QC Visualization

After clustering completes, show a temporary validation step before thresholding begins.

**Visualization (temporary, not saved):**

1. **Colored cells in napari:** Create a temporary labels layer (`_group_preview`) where each cell's value is its group number. Use a `DirectLabelColormap` with distinct colors per group (e.g., from a categorical colormap). Non-grouped cells (unfiltered) get color `None` (transparent).

2. **Histogram dock widget in napari:** A pyqtgraph `PlotWidget` added as a napari dock widget via `viewer.window.add_dock_widget()`. Shows the distribution of the grouping metric with colored regions per group (one histogram per group, stacked or overlaid with matching colors).

**Controls in the dock widget:**

```
┌──────────────────────────────────┐
│ Group Preview                    │
│                                  │
│ [histogram plot]                 │
│                                  │
│ Groups found: 3                  │
│ Group 1: 45 cells (mean: 120.3)  │
│ Group 2: 89 cells (mean: 450.7)  │
│ Group 3: 22 cells (mean: 1203.1) │
│                                  │
│ [Proceed to Thresholding]        │
│ [Re-group]  [Cancel]             │
└──────────────────────────────────┘
```

- **Proceed:** Removes the preview layer and histogram, starts the per-group thresholding loop
- **Re-group:** Removes preview, returns control to the panel so user can adjust parameters
- **Cancel:** Removes preview, discards grouping result entirely

**Critical napari gotchas** (from learnings):
- Tag the preview layer with `metadata[PERCELL_TYPE_KEY] = "group_preview"` — NOT as `LAYER_TYPE_SEGMENTATION` or `LAYER_TYPE_MASK` — so it doesn't appear in active segmentation/mask dropdowns
- Use `DirectLabelColormap` (never mutate `labels_layer.data` for visual filtering)
- Never use `events.colormap.blocker()` — it breaks napari's render pipeline

### Research Insights (Phase 4)

**Dock widget pattern:** PerCell4 does not currently use napari dock widgets. PerCell3 has an established pattern — class-based widgets with `self.widget` attribute:
```python
class GroupPreviewWidget:
    def __init__(self, viewer, ...):
        from qtpy.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel
        self.widget = QWidget()
        layout = QVBoxLayout()
        # ... build UI ...
        self.widget.setLayout(layout)

viewer.window.add_dock_widget(widget.widget, name="Group Preview", area="right")
```

**Pyqtgraph histogram gotchas (from phasor-plot-axis-desync learnings):**
- Call `plot_widget.getAxis('bottom').enableAutoSIPrefix(False)` and same for `'left'` axis — otherwise pyqtgraph applies SI prefix scaling (showing "x0.001" with inflated values) for small metric values like sg_ratio (0-1 range)
- Re-apply `enableAutoSIPrefix(False)` after each data refresh — pyqtgraph may re-enable it
- Use `disableAutoRange()` + explicit `setXRange`/`setYRange` if the histogram has interactive group boundary markers, to prevent visual drift on data updates

**Files:** `src/percell4/gui/threshold_qc.py` (group QC section)

---

#### Phase 5: Per-Group Interactive Thresholding (Core QC Loop)

The main interactive workflow. For each group, open a napari-based QC session.

**`src/percell4/gui/threshold_qc.py`**

**State machine:**

```python
class GroupStatus(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    SKIPPED = "skipped"

@dataclass
class GroupState:
    group_id: int                       # 1-indexed
    cell_labels: NDArray[np.int32]      # cell label IDs in this group
    status: GroupStatus = GroupStatus.PENDING
    mask: NDArray | None = None
    threshold_value: float | None = None

class ThresholdQCController:
    _groups: list[GroupState]
    _current_index: int = 0
    _channel_image: NDArray
    _smoothed_image: NDArray | None     # pre-computed once if sigma > 0
    _segmentation_labels: NDArray
    _label_to_group: NDArray[np.int32]  # lookup table: label_value → group_id
    _group_image_buffer: NDArray        # reusable buffer, allocated once
    _gaussian_sigma: float
    _mask_name: str
```

**Per-group napari setup:**
1. Create group image using reusable buffer: `_group_image_buffer[:] = 0; np.copyto(_group_image_buffer, _smoothed_image or _channel_image, where=group_cell_mask)` — avoids allocating a new array per group
2. Add/update temporary image layer `_group_image` in napari
3. Add/update threshold preview layer `_group_threshold_preview` (yellow `DirectLabelColormap`, 0.5 opacity)
4. Add/update shapes layer `_group_roi` for ROI rectangles
5. Add/update QC dock widget with controls

**QC dock widget layout:**

```
┌──────────────────────────────────┐
│ Group 2 of 3                     │
│                                  │
│ Method: [Otsu ▼]                 │
│                                  │
│ Threshold: 127.4                 │
│ Positive pixels: 12,345          │
│ Positive fraction: 0.23          │
│                                  │
│ [Accept] [Skip]                  │
│ [Back]   [Skip Remaining]        │
└──────────────────────────────────┘
```

**Live preview update:**
- Connect `_group_roi.events.data` to `_on_roi_changed` callback
- On ROI change or method switch:
  1. Extract pixels within ROI rectangles AND group cell mask
  2. If Gaussian σ > 0, apply smoothing to the group image (pre-compute once, not per-update)
  3. Compute threshold using selected method on extracted pixels
  4. Apply threshold to full group cell mask region to produce preview
  5. Update preview layer data and statistics labels
- If no ROI drawn, threshold uses all pixels within the group cell mask

**Button behaviors:**

| Button | Action |
|--------|--------|
| **Accept** | Store mask + threshold value in `_groups[_current_index]`, set status="accepted", advance to next group |
| **Skip** | Set status="skipped", mask=zeros, advance to next group |
| **Back** | If `_current_index > 0`: decrement index, reset target group to status="pending", reload that group's QC |
| **Back (group 1)** | Disabled (gray out the button) |
| **Skip Remaining** | Set current + all remaining groups to status="skipped" with zero masks, proceed to combine step |

**Advancing past the last group** triggers the combine step automatically.

**ROI handling:**
- ROIs reset when moving between groups (each group's cells have different spatial distributions)
- ROIs are NOT restored when using Back — user draws fresh ROIs

**Adaptive thresholding consideration:**
- When computing adaptive threshold on a group image with zeroed-out regions, restrict computation to pixels within the group cell mask only
- Pass `cell_mask` to the thresholding function to avoid artifacts at zeroed boundaries
- For performance: restrict `threshold_local` to the bounding box of the group's cells (computed once from the group mask). For sparse groups, this reduces the processed area by 50-80% and keeps adaptive under 500ms on 2048x2048.

**Napari close mid-QC:**
- Use `installEventFilter` on `viewer.window._qt_window` to intercept `QCloseEvent` — this allows calling `event.ignore()` to cancel the close. Do NOT use napari's `closing` signal (fires after close is committed, cannot cancel).
- On close: prompt with `QMessageBox("Save partial results?", "Save accepted groups so far" / "Discard all" / "Cancel close")`
- "Save accepted" → run combine step with whatever groups were accepted, then allow close
- "Discard" → clean up all state, allow close
- "Cancel" → call `event.ignore()` to prevent the close

**Cleanup on completion/cancellation:**
- Remove temporary layers: `_group_image`, `_group_threshold_preview`, `_group_roi`
- Remove QC dock widget (wrap in try/except — napari raises if widget was already removed by user)
- Remove group preview histogram dock widget (if still present)
- Nullify all layer/widget references before viewer destruction to avoid dangling pointers

### Research Insights (Phase 5)

**Performance optimizations:**
- **Lookup-table group masks:** Build all group masks in a single O(H*W) pass instead of N separate `np.isin` calls:
  ```python
  label_to_group = np.zeros(labels.max() + 1, dtype=np.int32)
  for group in groups:
      for cell_label in group.cell_labels:
          label_to_group[cell_label] = group.group_id
  group_index = label_to_group[labels]  # vectorized, single pass
  current_group_mask = group_index == current_group_id
  ```
  This is 5x faster than per-group `np.isin` for 5 groups at 2048x2048.
- **Pre-compute Gaussian on full image:** Smooth the full channel image once (`_smoothed_image`), then mask per group via `np.copyto`. This avoids smoothing artifacts at zero boundaries AND reduces computation from O(n_groups * H * W) to O(H * W).
- **Reusable image buffer:** Pre-allocate `_group_image_buffer` once and reuse across groups with `np.copyto(..., where=)`. Saves ~32 MB of transient allocations for 5 groups at 2048x2048.

**Napari/Qt gotchas (from institutional learnings):**
- **Signal coalescing:** If ROI change and method switch fire in quick succession, the threshold recomputes twice. Use `QTimer.singleShot(0, self._update_preview)` with a `_preview_pending` flag to coalesce into one update.
- **`blockSignals(True/False)`:** When advancing to the next group and programmatically updating the method dropdown, block signals to prevent triggering a recomputation cycle. Use `try/finally` pattern.
- **`_is_alive()` check:** Before accessing any napari layer or dock widget, check that the viewer is still alive (`.isVisible()` in try/except). The viewer may have been destroyed between groups.
- **`QTimer.singleShot(100)` for mode changes:** After adding a shapes layer and setting it active for ROI drawing, defer the mode change — napari processes layer selection asynchronously.
- **`blending="translucent"`:** Threshold preview labels layer must use `blending="translucent"` (not default "opaque") or it obscures the underlying image.
- **Event attribute access:** Use `event.source.property` not `event.value` in napari event handlers — `event.value` is unreliable across napari versions.

**Files:** `src/percell4/gui/threshold_qc.py`

---

#### Phase 6: Mask Combination and Metadata Storage

After all groups are processed (accepted or skipped):

**Mask combination:**
```python
# In-place combination avoids allocating temporary arrays per group
combined = np.zeros_like(channel_image, dtype=np.uint8)
for group in groups:
    if group.mask is not None:
        np.maximum(combined, group.mask, out=combined)
```

This is equivalent to logical OR since masks are binary (0/1 uint8). Using `out=combined` eliminates ~20 MB of transient allocations for 5 groups at 2048x2048.

**HDF5 storage:**
- `store.write_mask(mask_name, combined)` — saves at `/masks/{mask_name}`
- `mask_name` = `grouped_{channel}_{metric}` (e.g., `grouped_GFP_mean`)
- For re-runs with new names: `grouped_GFP_mean_v2`, `grouped_GFP_mean_v3`, etc.

**DataFrame update:**
- Add integer group column using `GroupingResult.group_assignments` (pd.Series):
  ```python
  col_name = f"group_{channel}_{metric}"  # e.g., "group_GFP_mean"
  df = data_model.df.assign(
      **{col_name: data_model.df["label"].map(result.group_assignments)}
  )  # NaN for unfiltered cells; assign() returns new df, shares memory via copy-on-write
  data_model.set_measurements(df)
  ```
- Save updated DataFrame to HDF5 via `store.write_dataframe("/measurements", df)`

**Group column persistence across re-measurement:**
- When `set_measurements()` is called with a new DataFrame (e.g., after re-measuring), the group column will be lost because the new DataFrame comes from `measure_cells()` which doesn't know about group columns
- **Solution:** Store the group mapping as a separate DataFrame in HDF5:
  ```python
  store.write_mask(mask_name, combined)
  # Write group mapping separately — existing generic API works, no store.py changes needed
  group_df = result.group_assignments.reset_index()
  group_df.columns = ["label", col_name]
  store.write_dataframe(f"/groups/{mask_name}", group_df)
  ```
- When measurements are reloaded or recomputed, check `/groups/` for stored group mappings and merge them back into the DataFrame
- **Centralize the merge:** Create a `_merge_group_columns(df, store)` helper in the launcher, called immediately before every `set_measurements()` invocation. Document this invariant with a comment to prevent future measurement pathways from silently dropping group columns.

**Edge case — all groups skipped:**
- Combined mask is all zeros — still save it (represents "no particles in any group")
- Show a brief info message: "All groups were skipped. Empty mask saved."
- `set_active_mask()` is called normally; downstream particle analysis will report zero particles

**Viewer integration:**
- Call `viewer_win.add_mask(combined, name=mask_name)` to display the final mask (note: existing `add_mask` signature is `(data, name=name)`, not `(name, data)`)
- Call `data_model.set_active_mask(mask_name)` to make it the active mask
- The mask appears in the viewer with all other masks visible simultaneously (user toggles via napari layer controls)

**Files:** `src/percell4/gui/threshold_qc.py` (combine logic), `src/percell4/gui/launcher.py` (group column merge on measurement load)

---

### Auto-Compute Measurements Strategy

When the user clicks "Run Grouped Segmentation," the panel checks if the needed metric column exists in the DataFrame:

```python
col_name = f"{channel}_{metric}"  # e.g., "GFP_mean_intensity"
if col_name not in data_model.df.columns:
    # Measure only this channel with this metric
    # Note: measure_cells() doesn't exist with metrics= param yet.
    # Use measure_multichannel() with a single-channel dict and single-metric list,
    # or add a metrics= parameter to measure_cells().
    worker = Worker(measure_multichannel, {channel: image}, labels, metrics=[metric])
    worker.finished.connect(self._on_measurement_done)
```

If the column already exists, skip measurement and proceed directly to clustering. This avoids re-measuring all channels — only the needed metric for the target channel is computed.

**Merging new measurements:** If the DataFrame already has measurements for other channels, merge the new channel's columns into the existing DataFrame rather than replacing it:

```python
def _on_measurement_done(self, new_df):
    existing = self._data_model.df
    # Merge only the metric column — dict(zip()) avoids intermediate DataFrame copy from set_index()
    metric_col = f"{channel}_{metric}"
    label_to_val = dict(zip(new_df["label"], new_df[metric_col]))
    df = existing.assign(**{metric_col: existing["label"].map(label_to_val)})
    self._data_model.set_measurements(df)
```

## Acceptance Criteria

### Functional Requirements

- [ ] `mode_intensity` and `sg_ratio` metrics registered in `BUILTIN_METRICS` and available in metric config dialog
- [ ] GMM clustering with BIC and silhouette score auto-selection produces correct group assignments ordered by ascending mean
- [ ] K-means clustering with user-specified k produces correct group assignments
- [ ] Cells below `min_cells` threshold (default 10) result in a single group
- [ ] Group QC visualization shows colored cells in napari + histogram with per-group colors in a dock widget
- [ ] "Proceed" / "Re-group" / "Cancel" buttons work correctly from group QC visualization
- [ ] Per-group thresholding shows zeroed-out non-group image, live preview, ROI layer, and method selector
- [ ] Live preview updates when ROI is drawn/modified or threshold method is changed
- [ ] Accept, Skip, Skip Remaining, Back buttons follow the defined state machine
- [ ] Back button is disabled on group 1
- [ ] Combined mask (union) is saved to HDF5 at `/masks/grouped_{channel}_{metric}`
- [ ] Integer group column added to measurements DataFrame and persisted
- [ ] Group column survives re-measurement via `/groups/{mask_name}` HDF5 storage
- [ ] Filtered datasets: only filtered cells participate in grouping; unfiltered cells get NaN
- [ ] Re-run detection: prompt to overwrite or save as new named run
- [ ] Multiple mask layers visible simultaneously with different colors
- [ ] Closing napari mid-QC prompts to save partial results or discard
- [ ] Gaussian σ set once at start, applied uniformly to all groups

### Non-Functional Requirements

- [ ] GMM/K-means clustering runs in Worker thread (no UI freeze)
- [ ] Measurement auto-compute runs in Worker thread
- [ ] Live threshold preview updates in < 500ms for 2048x2048 images
- [ ] All temporary napari layers cleaned up on workflow completion, cancellation, or viewer close
- [ ] Group preview layer tagged with custom metadata type (not `LAYER_TYPE_SEGMENTATION` or `LAYER_TYPE_MASK`) to avoid dropdown pollution
- [ ] `np.isin()` calls always use `list()` wrapper on Python sets (NumPy 2.x compatibility)
- [ ] Never use `events.colormap.blocker()` on napari layers
- [ ] Use `blockSignals(True/False)` when programmatically updating dock widget controls during group transitions
- [ ] Use `QTimer.singleShot(0)` for signal coalescing on rapid ROI/method changes
- [ ] Pyqtgraph histogram axes: `enableAutoSIPrefix(False)` to prevent SI prefix scaling
- [ ] Label preview layers use `blending="translucent"` not default opaque
- [ ] No module-level mutable state — all state on instance variables
- [ ] Worker progress routed through Qt signals only — never pass GUI-touching callbacks to worker functions

## Dependencies & Prerequisites

- Segmentation labels must exist (Cellpose or imported) before running grouped segmentation
- At least one image channel must be loaded
- `scikit-learn` already available (dependency of Cellpose)
- `pyqtgraph` already available (used for data plots)

## Risk Analysis & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Group column lost on re-measurement | Data loss | Store group mapping in `/groups/` HDF5 path, merge back on load |
| Napari close mid-QC loses progress | User frustration | Connect to close signal, prompt to save partial results |
| Adaptive threshold artifacts at zeroed boundaries | Incorrect mask | Restrict adaptive computation to group cell mask pixels only |
| SG ratio division by zero | Crash | Return NaN when denominator is zero |
| Large datasets slow preview | UX degradation | Pre-compute smoothed image once; threshold only within ROI + mask |
| Group preview layer pollutes dropdowns | Confusion | Use custom metadata type, not standard mask/segmentation types |
| Silhouette O(n^2) on large datasets | Slow clustering | Subsample to 5000 cells; recommend BIC as default |
| Filter changes during QC | Stale group assignments | Document as known limitation; consider disabling filter during QC |
| Dock widget already removed by user | Crash on cleanup | Wrap `remove_dock_widget()` in try/except |

## File Summary

| File | Action | Description |
|------|--------|-------------|
| `src/percell4/measure/metrics.py` | MODIFY | Add `mode_intensity` and `sg_ratio` to `BUILTIN_METRICS` |
| `src/percell4/measure/grouper.py` | NEW | GMM and K-means clustering with `GroupingResult` dataclass |
| `src/percell4/gui/grouped_seg_panel.py` | NEW | Launcher panel with parameter controls and run button |
| `src/percell4/gui/threshold_qc.py` | NEW | Group QC visualization + per-group thresholding controller |
| `src/percell4/gui/launcher.py` | MODIFY | Add `GroupedSegPanel` to Workflows tab, add `_merge_group_columns()` helper |
| `src/percell4/store.py` | NO CHANGE | Existing generic `write_dataframe`/`read_dataframe` already supports `/groups/` path |
| `src/percell4/model.py` | NO CHANGE | CellDataModel is schema-agnostic; group column is just another DataFrame column |

## References

- Brainstorm: `docs/brainstorms/2026-04-03-grouped-segmentation-brainstorm.md`
- PerCell3 grouped segmentation: `/Users/leelab/percell3/src/percell3/measure/cell_grouper.py`, `threshold_viewer.py`, `thresholding.py`
- PerCell v1 GMM: `/Users/leelab/percell/percell/application/image_processing_tasks.py:734-836`
- Mask classification gotcha: `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`
- NumPy isin gotcha: `docs/solutions/logic-errors/numpy-isin-fails-with-python-sets.md`
- DirectLabelColormap gotcha: `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`
- Selection/filtering/multi-ROI patterns: `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`
- Napari/Qt learnings (signal coalescing, blockSignals, blending): `docs/solutions/ui-bugs/percell4-phases-0-6-napari-qt-learnings.md`
- Pyqtgraph axis desync (SI prefix, auto-range): `docs/solutions/ui-bugs/percell4-phasor-plot-axis-desync.md`
- Architecture review findings (thread safety, mutable state): `docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md`
