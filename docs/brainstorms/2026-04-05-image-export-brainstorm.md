# Image Export Brainstorm

**Date:** 2026-04-05
**Status:** Draft

## What We're Building

An "Export Images..." button in the I/O tab's Export section that exports layers from the loaded HDF5 dataset as individual TIFF files. The user selects which layers to export (channels, segmentations, masks), picks an output folder, and the exporter writes one TIFF per layer.

Designed to be extensible for future file formats beyond TIFF.

## Why This Approach

- **Selectable layers** — users often only need specific channels or masks, not the entire dataset
- **Separate TIFFs per channel** — maximum compatibility with ImageJ, FIJI, and other tools
- **Preserve original dtypes** — lossless round-trip (float32 intensity, int32 labels, uint8 masks)
- **Button in existing Export section** — consistent with "Export Measurements to CSV..." pattern, no new window type needed

## Key Decisions

### 1. Export Scope: All Layer Types
Export intensity channels, segmentation labels, and masks. Each as individual TIFF files.

### 2. Layer Selection: Checklist Dialog
Dialog shows all available layers grouped by type (Channels, Segmentations, Masks) with checkboxes. User selects which to export. Select All / Deselect All per group.

### 3. Output Naming: Dataset_LayerName.tif
Files named with dataset prefix + layer name:
- `FLIM_mNG-mask_ch00.tif`
- `FLIM_mNG-mask_nuclei.tif`
- `FLIM_mNG-mask_threshold_mask.tif`

User picks the output folder via directory picker.

### 4. Multi-Channel: Separate Files
Each channel in the intensity array exports as its own TIFF. No multi-page TIFF option (keeps it simple, maximum compatibility).

### 5. Data Types: Preserve Original
- Intensity → float32 TIFF
- Labels → int32 TIFF
- Masks → uint8 TIFF

### 6. UI: Button in I/O Tab Export Section
"Export Images..." button next to "Export Measurements to CSV...". Opens a dialog with:
- Layer selection (grouped checkboxes)
- Output folder picker
- Export button

### 7. Extensibility
The export module (`io/exporter.py` or similar) takes a store + layer list + output path + format. Adding new formats later means adding a format dropdown to the dialog and a new writer function.

## Dialog Layout

```
┌─────────────────────────────────────────────┐
│ Export Images                                │
├─────────────────────────────────────────────┤
│ Output folder: [/path/to/output]  [Browse]  │
├─────────────────────────────────────────────┤
│ Channels          ☑ Select All              │
│   ☑ ch00                                    │
│   ☑ ch01                                    │
│   ☐ ch02                                    │
│                                             │
│ Segmentations     ☑ Select All              │
│   ☑ nuclei                                  │
│                                             │
│ Masks             ☑ Select All              │
│   ☑ threshold_mask                          │
├─────────────────────────────────────────────┤
│ Format: [TIFF ▼]                            │
│                           [Export] [Cancel]  │
└─────────────────────────────────────────────┘
```

## Resolved Questions

1. **What to export?** — All layer types (channels, segmentations, masks)
2. **Selection?** — Checklist per layer type
3. **Naming?** — Dataset_LayerName.tif
4. **Multi-channel handling?** — Separate TIFFs per channel
5. **Data types?** — Preserve originals (float32, int32, uint8)
6. **UI entry point?** — Button in I/O tab Export section

## Open Questions

(none)
