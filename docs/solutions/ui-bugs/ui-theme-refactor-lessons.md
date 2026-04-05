---
title: "UI Theme Refactor Lessons"
category: ui-bugs
tags: [qt-theme, fusion, stylesheet, dark-mode, cross-platform, scrollbar, arrows, checkboxes]
module: gui
symptom: "Inconsistent styling across windows, unreadable widgets on some machines, invisible arrows and scrollbars"
root_cause: "No centralized theme, no app.setStyle('Fusion'), per-file inline stylesheets with hardcoded hex values"
date: 2026-04-05
---

# UI Theme Refactor Lessons

Lessons from centralizing the PerCell4 dark theme into `gui/theme.py`. The refactor touched 12 files and uncovered 6 iterative bugs during visual testing.

## Decision: Centralized Theme Architecture

**Before:** ~60 `setStyleSheet` calls scattered across 12 files, 3 different background colors, zero scrollbar styling, no `app.setStyle("Fusion")`. Widgets without explicit styling fell through to macOS Aqua light theme.

**After:** `gui/theme.py` with named color constants + `APP_STYLESHEET` applied via `app.setStyleSheet()` at startup. Fusion style for cross-platform consistency.

**Key pattern:** Set `background-color` on `QWidget` globally (not just `QMainWindow` and `QDialog`), because nested `QWidget` containers inside `QScrollArea` don't inherit from `QDialog` — they need their own background rule.

## Bug 1: Dialog Backgrounds Went Light After Removing _apply_style()

**Symptom:** After removing per-dialog `_apply_style()` methods, the Compress dialog rendered with a light gray background — the dark theme was gone.

**Root cause:** The global stylesheet only set `background-color` on `QMainWindow, QDialog`. But the dialog's content is inside a `QScrollArea > QWidget` — that widget inherits from `QWidget`, not `QDialog`, so it got the system default background.

**Fix:** Change the base rule to include `QWidget`:
```css
QMainWindow, QDialog, QWidget { background-color: #1e1e1e; color: #e0e0e0; }
```

**Prevention:** When using a global stylesheet with Qt, always target `QWidget` for background colors. Testing with just `QDialog` is insufficient because most dialog content is nested inside plain `QWidget` containers.

## Bug 2: Two Different Checkbox Styles

**Symptom:** `QListWidget` checkboxes rendered with styled blue indicators, but standalone `QCheckBox` widgets rendered with Fusion's default white/gray indicators.

**Root cause:** The stylesheet styled `QListWidget::indicator` but not `QCheckBox::indicator`. Same bug for checkable `QGroupBox` which uses `QGroupBox::indicator`.

**Fix:** Add matching indicator rules for all three:
```css
QCheckBox::indicator, QGroupBox::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:unchecked, QGroupBox::indicator:unchecked { ... }
QCheckBox::indicator:checked, QGroupBox::indicator:checked { ... }
```

**Prevention:** When styling indicators for one widget type, grep for all `::indicator` pseudo-elements: `QCheckBox`, `QRadioButton`, `QGroupBox`, `QListWidget`, `QTreeWidget`. Style them all consistently.

## Bug 3: Invisible Arrows on SpinBox and ComboBox

**Symptom:** SpinBox up/down arrows and ComboBox dropdown arrows were invisible — dark on dark.

**Root cause:** Fusion's native arrows render in a color that's invisible against the dark button background. Three approaches were tried:
1. CSS border-triangle trick → rendered as gray rectangles, not triangles
2. Inline SVG data URIs → Qt stylesheet `url()` doesn't support data URIs on macOS
3. Temp SVG files written to disk → works reliably

**Fix:** Write small SVG arrow files to a temp directory at module load time, reference via file paths:
```python
_ARROW_DIR = Path(tempfile.mkdtemp(prefix="percell4_arrows_"))
_arrow_up_path = _ARROW_DIR / "arrow_up.svg"
_arrow_up_path.write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6">'
    '<polygon points="5,0 10,6 0,6" fill="#cccccc"/></svg>'
)
```

**Prevention:** For custom icons in Qt stylesheets, always use actual files — not data URIs or CSS tricks. Write SVGs to a temp directory at startup. This works cross-platform.

## Bug 4: Inconsistent Scrollbar Styles

**Symptom:** The Analysis tab had system-default light gray scrollbars while dialogs had no visible scrollbar styling at all.

**Fix:** Add `QScrollBar:vertical` and `QScrollBar:horizontal` rules to the global stylesheet with dark theme colors.

## Other Fixes Made During This Session

### Channel Renames Not Persisting

**Symptom:** Renaming a channel in the Data tab worked in the viewer but reverted on reload.

**Root cause:** `_on_rename_channel()` only renamed the napari layer — it never updated the `channel_names` list in HDF5 metadata.

**Fix:** Read `channel_names` from metadata, replace the old name, write back via `store.set_metadata()`.

### Mask Layer Blending

**Symptom:** Mask layers displayed with napari's default `translucent` blending instead of `additive`.

**Root cause:** `add_mask()` in `viewer.py` was the only add method that didn't set `blending="additive"` — `add_image()` and `add_labels()` both did.

**Fix:** Add `blending = kwargs.pop("blending", "additive")` to `add_mask()`, matching the other methods.

### Add Layer Flat Discovery Used Wrong Files

**Symptom:** In the Add Layer dialog's Discover TIFFs tab, importing from a flat directory with multiple dataset groups imported the wrong files (same as the batch compress bug).

**Root cause:** Same root cause as the batch compress bug — re-scanning the shared `source_dir` instead of using the per-dataset `files` list.

**Fix:** Use per-dataset file scanning in the import loop, same pattern as the batch compress fix.

## Section Header Style

**Decision:** Removed the `border-bottom` and box styling from section header labels ("Import / Export", "Analysis", etc.). Now plain bold text over the background — cleaner appearance.
