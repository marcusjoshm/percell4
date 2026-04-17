---
status: pending
priority: p2
issue_id: "024"
tags: [code-review, architecture, cleanup]
dependencies: ["021"]
---

# Two parallel architectures coexist (old gui/ + new interfaces/)

## Problem Statement

The legacy `gui/` package is fully intact alongside the new `interfaces/gui/`. Both `app.py` (old) and `interfaces/gui/app.py` (new) exist as entry points. Old peers (gui/cell_table.py etc.) depend on CellDataModel; new peers (interfaces/gui/peer_views/) depend on Session. Import-linter contracts only validate new packages while production runs through the old architecture. SegmentationPanel and GroupedSegPanel are stranded in the old location with no corresponding new location.

## Findings

- **Source**: Architecture strategist + pattern recognition specialist
- **Locations**: `gui/launcher.py` (shim), `gui/cell_table.py` (shim), `gui/data_plot.py` (shim), `gui/phasor_plot.py` (shim), `gui/segmentation_panel.py` (NOT shimmed), `gui/grouped_seg_panel.py` (NOT shimmed)

## Proposed Solutions

### Option A: Migrate remaining old files, delete shims
Move SegmentationPanel and GroupedSegPanel to interfaces/gui/task_panels/. Delete all gui/ shim files. Update all imports. One canonical path per module.
- Effort: Large
- Risk: Medium

### Option B: Accept coexistence, document migration path
Keep shims for backward compat. Migrate one file per PR as they're touched.
- Effort: Small (ongoing)
- Risk: Low

## Acceptance Criteria

- [ ] Each module has exactly one canonical location
- [ ] No re-export shim files exist in gui/
- [ ] All imports reference the canonical location
