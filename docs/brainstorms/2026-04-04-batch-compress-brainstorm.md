# Batch Compress Brainstorm

**Date:** 2026-04-04
**Status:** Draft

## What We're Building

A unified "Compress TIFF Dataset" dialog that discovers TIFF datasets in a directory and converts them to `.h5` files. It replaces the current "Import TIFF Dataset" (renamed to "Compress TIFF Dataset") and the "Batch Import" placeholder. The dialog auto-adapts: one discovered dataset behaves as single compress, multiple datasets behaves as batch compress.

### Naming Changes
- "Import TIFF Dataset..." -> "Compress TIFF Dataset..."
- "Load Existing .h5 Dataset..." -> "Load Dataset..."
- "Batch Import" placeholder -> removed (absorbed into Compress dialog)
- Compress never loads into the viewer; Load is always a separate action

## Why This Approach

- **Single entry point** reduces cognitive load -- user doesn't choose between "import" vs "batch import"
- **Auto-detect single vs batch** from discovery results eliminates a mode toggle
- **Compress-only behavior** enforces clean separation: compress = convert files, load = open for analysis
- Mirrors PerCell3's auto/manual import split but in a GUI instead of TUI

## Key Decisions

### 1. Dataset Discovery: Two Strategies

Support both, user selects which:
- **Subdirectory-based** (default): each immediate subdirectory = one dataset
- **Token-based grouping**: extract a FOV/dataset token from filenames to group files in a flat directory

### 2. Dataset Selection: Tree View with Checkboxes

- Top-level nodes = discovered datasets (with file count)
- Child nodes = individual files within each dataset
- Checkbox on each node; checking parent checks all children
- Select All / Deselect All buttons
- Expand/collapse to inspect files

### 3. Auto vs Manual Mode: Toggle in Dialog

- **Auto mode**: all files become channels, names auto-derived from tokens, .h5 names auto-derived from dataset name. Shows summary list, "Compress" button.
- **Manual mode**: full tree view appears with:
  - Per-file **layer type dropdown**: Channel (default) | Segmentation | Mask
  - Per-file **editable name field**: defaults to auto-derived name, user can rename (e.g., "ch0" -> "GFP")
  - Per-dataset **stitching config** in side panel (rows, cols, pattern, start corner)
  - Per-dataset **FLIM config** (laser frequency, calibration values per channel)

### 4. Per-Dataset Stitching Config

When tiles are detected, clicking a dataset in the tree shows stitching settings in a side panel:
- Grid rows, cols
- Grid pattern (row_by_row, snake_by_row, etc.)
- Start corner
- Each dataset can have independent stitching parameters

### 5. Per-Dataset FLIM Config

Each dataset can have its own FLIM/TCSPC configuration:
- Laser frequency
- Per-channel calibration (phase, modulation)
- .bin file dimensions if applicable

### 6. Output Path

Default to same directory as source. Show output path in dialog, user can change it.

### 7. Progress Feedback

Progress dialog with:
- Current dataset name and count (e.g., "Compressing... 2/4")
- Progress bar
- Cancel button

### 8. Single vs Batch Auto-Detection

- 1 dataset discovered -> single compress mode (same UX, just no batch tree)
- Multiple datasets discovered -> batch mode with tree selection
- No explicit mode toggle needed

## Dialog Flow

```
1. User clicks "Compress TIFF Dataset..."
2. File picker: select source directory
3. Dialog opens, scans directory
4. Discovery strategy selector (subdirectory vs token)
5. Results appear:
   - Single dataset: simplified view (like current ImportDialog)
   - Multiple datasets: tree view with checkboxes
6. Auto/Manual toggle:
   - Auto: summary + "Compress" button
   - Manual: tree with dropdowns, names, side panel for stitching/FLIM
7. User configures, clicks "Compress"
8. Progress dialog runs
9. .h5 files created in output directory
10. Dialog closes. User can now "Load Dataset" to open one.
```

## Launcher I/O Panel (After Changes)

```
Import
  [Compress TIFF Dataset...]
  [Load Dataset...]
  [Close Dataset]

Export
  [Prism Export]        (or placeholder)
  [Batch Export]        (placeholder)
```

## Resolved Questions

1. **Z-projection in batch**: Global -- one z-projection dropdown applies to all datasets. Most experiments use the same method.
2. **Token regex customization**: Sensible defaults with a collapsible "Advanced: Token Patterns" section for override.
3. **Existing .h5 collision**: Prompt per collision -- ask the user to overwrite or skip each conflict.
4. **Project CSV integration**: No project.csv interaction. Batch compress is purely a file conversion tool. Project membership is handled elsewhere.

## Open Questions

(none)

## Technical Notes

- Current import runs synchronously on main thread (comment about bus errors with QThread + external drive I/O) -- batch will need a threading strategy that avoids this issue
- PerCell4 scanner (`io/scanner.py`) already handles token extraction but lacks FOV/dataset grouping -- needs extension
- `DatasetStore.create_atomic()` handles safe .h5 creation (write to temp, rename)
- Existing `ImportDialog` and `import_dataset()` pipeline can be refactored to support both single and batch flows
