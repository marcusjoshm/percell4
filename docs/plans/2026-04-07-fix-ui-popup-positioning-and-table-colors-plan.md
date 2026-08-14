---
title: Fix UI Popup Positioning and Table Row Colors
type: fix
date: 2026-04-07
---

# Fix UI Popup Positioning and Table Row Colors

Two UI improvements for better user experience: (1) grouped thresholding popup windows appear behind the Viewer, and (2) Cell Table alternating row colors are hard to read.

## Fix 1: Threshold QC Popup Windows Appear Behind Viewer

### Problem

When running grouped thresholding, the Preview and QC popup windows (`QMainWindow` instances in `threshold_qc.py`) are shown via `win.show()` with no positioning or raise logic. The OS places them behind the napari Viewer window, forcing the user to manually move/minimize the Viewer to access them.

Compounding the issue: `_show_group_preview()` calls `self._viewer_win.show()` (line 180) which internally calls `raise_()` on the Viewer, stealing focus back even if the popup were raised.

### Proposed Solution

After each popup's `win.show()`, call `win.raise_()` and `win.activateWindow()` to bring it to the front. Ensure this happens **after** any `self._viewer_win.show()` call so the viewer doesn't steal focus back.

On macOS, `raise_()` on a parentless `QMainWindow` can be unreliable. If needed, parent the popup to the viewer's Qt window for reliable z-ordering.

### Files to Change

- `src/percell4/gui/threshold_qc.py`
  - **Line ~299** (`_build_preview_dock`): After `win.show()`, add `win.raise_()` and `win.activateWindow()`
  - **Line ~498** (`_build_qc_dock`): After `win.show()`, add `win.raise_()` and `win.activateWindow()`
  - **Line ~180** (`_show_group_preview`): Ensure popup raise occurs **after** `self._viewer_win.show()` — either reorder or add a second `raise_()` at the end of the method

### Edge Cases

- **QC window recreated per group** (Back/Accept/Skip): The `raise_()` must be added inside `_build_qc_dock` so it fires on every recreation, not just the first
- **macOS focus policy**: If `raise_()` alone doesn't work, set the popup's parent to the viewer's Qt window: `QMainWindow(parent=viewer_qt_window)`
- **Multi-monitor**: `raise_()` approach avoids geometric positioning entirely, so no off-screen risk

## Fix 2: Cell Table Uniform Row Colors

### Problem

`cell_table.py:181` enables `setAlternatingRowColors(True)`, but the global theme in `theme.py` does not define `alternate-background-color` for `QTableView`. Qt falls back to a default that clashes with the dark theme, making rows hard to read.

### Proposed Solution

Set `setAlternatingRowColors(False)` to give all rows a uniform background color from the theme.

**Alternative considered**: Adding `alternate-background-color: #242424;` to the theme's `QTableView` block would fix contrast while preserving row differentiation. However, the user specifically wants uniform colors.

### Files to Change

- `src/percell4/gui/cell_table.py`
  - **Line 181**: Change `self._table.setAlternatingRowColors(True)` → `self._table.setAlternatingRowColors(False)`

## Acceptance Criteria

- [x] Threshold QC Preview window appears in front of (not behind) the Viewer when grouped thresholding starts
- [x] Threshold QC per-group window appears in front when transitioning between groups (Accept/Skip/Back)
- [x] Cell Table rows all display with uniform background color
- [ ] Verify on macOS that popup windows reliably come to front
