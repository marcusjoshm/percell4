---
title: "Phasor Plot ROI Desync from Axis Auto-Range and SI Prefix"
category: ui-bugs
tags: [pyqtgraph, phasor, roi, axis-scaling, auto-range, si-prefix]
module: gui/phasor_plot
date: 2026-03-26
symptom: "Phasor ROI mask highlighted pixels that appeared outside the visible histogram cloud, especially after resizing the ROI"
root_cause: "pyqtgraph auto-range and SI prefix scaling caused coordinate system mismatch between the ROI and histogram data"
---

# Phasor Plot ROI Desync from Axis Auto-Range and SI Prefix

## Symptoms

1. ROI placed in an empty region of the phasor histogram still showed a mask in napari
2. The S axis displayed "S (x0.001)" with values 0-700 instead of 0-0.7
3. Resizing the ROI caused the mask to appear in unexpected locations
4. The desync worsened with each interaction (resize, move)

## Root Causes

### SI Prefix Auto-Scaling
pyqtgraph automatically applies SI prefix scaling to axis labels when values are small. For the S axis range [0, 0.7], it scaled to [0, 700] with a "x0.001" multiplier. The ROI position was in actual data coordinates (0-0.7) but the visual labels suggested different values, making it appear the ROI was in the wrong place.

### Auto-Range Coordinate Shift
pyqtgraph's auto-range feature recalculates the view bounds when data changes or the user interacts with the plot. Each auto-range event could shift the coordinate system slightly, causing the ROI (which stays at fixed data coordinates) to visually drift relative to the histogram image.

## Fixes

### 1. Disable SI Prefix on Both Axes
```python
self._plot.getAxis("bottom").enableAutoSIPrefix(False)
self._plot.getAxis("left").enableAutoSIPrefix(False)
```
Applied both in `_build_ui` (initial setup) AND in `_refresh_histogram` (after each data update), because pyqtgraph may re-enable the prefix when data changes.

### 2. Disable Auto-Range
```python
self._plot.disableAutoRange()
```
Keeps the axes locked at G=[−0.005, 1.005] and S=[0, 0.7] regardless of interaction. The fixed range is set explicitly with `setXRange`/`setYRange`.

### 3. Fixed Axis Range After Each Refresh
```python
self._plot.setXRange(*g_range, padding=0)
self._plot.setYRange(*s_range, padding=0)
```
Re-applied after every histogram refresh to ensure consistency.

## Prevention Pattern

For any pyqtgraph PlotWidget where ROI position must match data coordinates:
1. **Always** `disableAutoRange()` — prevents coordinate system shifts
2. **Always** `enableAutoSIPrefix(False)` on both axes — prevents label confusion
3. Re-apply axis settings after every data refresh
4. Use `setRect(QRectF)` for ImageItem positioning (not `setTransform`)

## Related Issues

- ImageItem axis mapping (setTransform vs setRect) — fixed earlier in same session
- Phasor ROI mask using wrong data source (filtered vs unfiltered) — fixed by checking Filtered checkbox state in `_get_roi_mask`
