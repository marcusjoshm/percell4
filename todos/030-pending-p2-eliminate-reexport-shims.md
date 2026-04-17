---
status: pending
priority: p2
issue_id: "030"
tags: [code-review, shim, architecture, cleanup]
dependencies: []
---

# Eliminate 20 re-export shim files (37 stale import paths)

## Problem Statement

19 re-export shim files exist in `measure/`, `flim/`, `segment/`, `io/`, `gui/` that redirect imports to their canonical locations in `domain/`, `adapters/`, `interfaces/`. Plus 1 launcher shim. 37 files across the codebase still import from the old paths. Every shim is a maintenance trap — changes to the canonical file work, but the shim must stay in sync, and developers may unknowingly add new code to the old path.

## Findings

**Shim files (20):**
- `measure/` (6): measurer, metrics, grouper, particle, thresholding, (missing one)
- `flim/` (2): phasor, wavelet_filter
- `segment/` (3): cellpose, postprocess, roi_import
- `io/` (6): assembler, discovery, importer, models, readers, scanner
- `gui/` (3): launcher, cell_table, data_plot, phasor_plot

**Consumers importing from old paths (37):**
- `gui/workflows/` (7 files)
- `gui/segmentation_panel.py` (5 imports)
- `gui/grouped_seg_panel.py` (4 imports)
- `gui/threshold_qc.py` (6 imports)
- `gui/compress_dialog.py`, `gui/add_layer_dialog.py` (5 imports)
- `interfaces/gui/task_panels/` (3 imports)
- `interfaces/gui/main_window.py` (1 import)

## Proposed Solutions

### Option A: Update all imports, delete shims (Recommended)
Batch find-and-replace all 37 old-path imports to canonical paths. Delete all 20 shim files. One large PR but mechanically simple.
- Effort: Medium (mostly sed/grep)
- Risk: Low — shims prove the paths work; just updating import strings

### Option B: Gradual migration
Update imports one file at a time as files are touched. Shims stay until all consumers migrate.
- Effort: Small per-touch
- Risk: Low — but shims persist indefinitely

## Acceptance Criteria

- [ ] Zero re-export shim files in the codebase
- [ ] All imports reference canonical locations (domain/, adapters/, interfaces/)
- [ ] All tests pass
- [ ] Import-linter contracts pass
