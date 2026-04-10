# Grouped Segmentation Brainstorm

**Date:** 2026-04-03
**Status:** Draft

## What We're Building

An intensity-based auto-thresholding feature for microscopy data with polyclonal protein expression. Cells are grouped by fluorescent protein expression level, each group is thresholded independently with interactive QC, and the resulting binary masks are combined into a single mask.

### Workflow

1. **Measure** — Automatically compute per-cell metrics on the target channel (or reuse existing measurements)
2. **Group** — Cluster cells by expression level using GMM or K-means
3. **Threshold + QC** — For each group, open an interactive napari session with live threshold preview, ROI drawing, and method selection. User accepts or skips each group.
4. **Combine** — Union all accepted group masks into a single binary mask. Store group membership as a column in the measurements DataFrame.

## Why This Approach

Polyclonal expression means cells in the same field have vastly different fluorescence levels. A single global threshold fails — it either misses dim cells or over-segments bright ones. Grouping by expression level and thresholding each group independently adapts the threshold to each subpopulation, producing accurate masks across the full dynamic range.

This approach is proven in PerCell v1/v2 and PerCell3, and the interactive QC step catches cases where automatic thresholding picks up the wrong structures.

## Key Decisions

### 1. Measurements

- **Same channel for grouping and thresholding** — no cross-channel grouping
- Measurements are auto-computed if not already available for the target channel
- **Metrics available:** mean, median, integrated intensity, max, mode, plus **SG ratio** (signal-to-ground: sum of pixels >= 95th percentile / sum of pixels <= 50th percentile)
- SG ratio is included because it's specifically useful for distinguishing cells where absolute intensity varies but signal-to-background contrast is the differentiator
- **Note:** SG ratio and mode are new metrics not yet in PerCell4's measurement system — must be added

### 2. Grouping Algorithm

- **GMM** with auto-selection of component count:
  - User chooses between **BIC** and **silhouette score** for auto-selection criterion
  - Groups ordered by ascending mean metric value (group 1 = lowest expression)
  - Minimum cell count threshold (default: 10) — fewer cells defaults to single group
- **K-means** as alternative:
  - User specifies number of groups manually
  - Same ascending-mean ordering convention
- User selects algorithm and parameters before grouping begins

### 3. Interactive Thresholding & QC (per-group)

Adopting PerCell3's napari-based interactive approach with enhancements:

- **Image layer:** Channel data with non-group cells zeroed out
- **Preview layer:** Live-updating threshold mask overlay (yellow)
- **ROI layer:** Shapes layer for drawing rectangles to restrict threshold computation region
- **Threshold method selector:** User can switch between Otsu, Triangle, Li, Adaptive during QC (not just Otsu as in PerCell3)
- **Buttons:** Accept, Skip, Skip Remaining, Back (returns to previous group's QC)
- **Live statistics:** Threshold value, positive pixel count, positive fraction
- **Gaussian smoothing:** Optional pre-smoothing sigma parameter

When user draws/modifies ROI, threshold recomputes using only pixels within ROI and cell mask, preview updates in real-time.

### 4. Mask Combination

- **Union only** (logical OR across all accepted group masks)
- Automatic — no manual combine step since groups contain non-overlapping cells by definition
- Combined mask stored as a single layer in HDF5 at `/masks/<name>`

### 5. Metadata Storage

- **Group column in measurements DataFrame** — integer values (1, 2, 3, ...) ordered by ascending mean metric value. Numeric type enables mathematical operations on group IDs downstream.
- Column name encodes context: e.g., `group_GFP_mean` for GFP channel grouped by mean intensity
- No separate status column needed — skipped groups contribute an empty mask (no particles), the correct biological result for groups without the target feature
- Unfiltered cells (not part of grouping): NaN
- Immediately available for filtering, plotting, coloring, and CSV export
- Stored in HDF5 with measurements (no separate dataset needed)

### 6. Filtering Integration

- **Filtered cells only** — grouping algorithm operates on the current filter (filtered_ids from CellDataModel)
- If no filter active, all cells are used
- Threshold mask is generated only for filtered cells; unfiltered cells get no mask contribution
- Group column values for unfiltered cells: left empty/NaN

### 7. Multiple Mask Layers

- All mask layers visible simultaneously with different colors in the viewer
- User can toggle visibility per-mask via napari's layer controls
- `set_active_mask()` on CellDataModel determines which mask is used for downstream analysis (particle counting, etc.)

### 8. Re-runs

- If a previous grouped segmentation exists, **prompt user to overwrite or save as a new named run**
- Named runs allow iteration (e.g., 'GFP_groups', 'GFP_groups_v2')
- Each run has its own mask layer and group column

## Gaps Identified & Solutions

### Gap 1: Group Visualization Before Thresholding
After grouping but before thresholding, the user should see which cells belong to which group. **Solution:** Temporary QC visualization only (not saved). Show both: colored cells in napari label layer by group assignment AND a histogram of the grouping metric with colored regions per group. This lets the user validate spatial distribution and cluster separation before proceeding.

### Gap 2: Undo Within QC Session
If user accepts a group then realizes the threshold was wrong, there's no way back. **Solution:** "Back" button included in QC dock widget (see Section 3). Returns to previous group's QC, discarding that group's accepted threshold.

### Gap 3: Progress Indication
With many groups, the user needs to know where they are. **Solution:** Display "Group X of Y" in the QC dock widget title or header.

### Gap 4: Edge Case — Empty Groups After Filtering
If filtering leaves very few cells, a group might have 0-1 cells. **Solution:** Skip groups with fewer than a configurable minimum cell count (default 1). Log a warning.

### Gap 5: Gaussian Sigma Per-Group vs Global
**Solution:** Set once at the start, applied uniformly to all groups. Not adjustable during QC.

### Gap 6: What Happens to Skipped Groups' Cells
Skipping a group means "no particles exist in these cells" — the group contributes an empty mask (all zeros) to the final union. This is biologically correct for conditions like stress granule disassembly where some expression-level groups genuinely have no features to threshold. Group column still records the integer group assignment (e.g., 2); downstream particle analysis will simply report zero particles for these cells.

## Resolved Questions

1. **Group preview visualization:** Both — colored cells in napari AND histogram with group boundaries. Temporary QC only, not saved.
2. **Back button priority:** Include in initial implementation.
3. **Skipped cell handling:** Keep group ID in group column. Skipped groups contribute empty mask (no particles). No status column needed.
