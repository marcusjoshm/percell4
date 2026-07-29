---
title: "PerCell4 Code Review Findings — Phases 0-6"
category: architecture-decisions
tags: [code-review, python, qt, napari, dead-code, thread-safety, type-safety, simplicity]
module: gui, store, io, segment, measure
date: 2026-03-26
symptom: "Code review after Phases 0-6 implementation revealed dead code, thread safety issue, global mutable state, and stale references"
root_cause: "Iterative development with refactors left artifacts; GUI progress callback bypassed Qt thread safety"
---

# PerCell4 Code Review Findings — Phases 0-6

4 review agents (Python reviewer, Architecture strategist, Code simplicity reviewer, Pattern recognition specialist) analyzed the full codebase after Phases 0-6.

## Issues Found and Fixed

### 1. Dead segmentation_window.py (780 LOC)

**Problem:** `segmentation_window.py` was a near-complete duplicate of `segmentation_panel.py`. It was the original standalone window implementation that was replaced when segmentation moved to a sidebar tab, but never deleted.

**Fix:** Deleted the file. 780 LOC removed.

**Prevention:** When refactoring a component from one form to another (window -> panel), delete the old file in the same commit.

### 2. Unused _types.py (27 LOC)

**Problem:** Defined `DatasetMetadata`, `LabelArray`, `IntensityImage`, `BinaryMask` — none imported anywhere in the codebase.

**Fix:** Deleted the file. Type aliases can be re-added when actually needed.

**Prevention:** Don't create type definitions until they're consumed by real code.

### 3. Thread-Unsafe Import Progress Callback

**Problem:** `import_dataset()` received `progress_callback=self._on_import_progress` which was called from the Worker's background thread. The callback called `self.statusBar().showMessage()` — a GUI operation that Qt requires to happen on the main thread. Could cause intermittent crashes.

**Fix:** Removed the direct callback. Import progress now routes through the Worker's `progress` signal, which Qt's signal/slot mechanism delivers safely across threads.

**Prevention:** Never pass a GUI-touching callback to a function running in a Worker thread. Always use Qt signals for cross-thread communication.

### 4. Global Mutable _color_index

**Problem:** Module-level `_color_index = 0` in viewer.py, mutated via `global _color_index`. Would break if multiple ViewerWindow instances existed.

**Fix:** Moved to `self._color_index` instance variable on ViewerWindow. `_colormap_for_channel()` now takes and returns the index.

**Prevention:** Never use module-level mutable state. Use instance variables.

### 5. Stale _seg_channel_label Reference

**Problem:** `launcher.py` referenced `self._seg_channel_label` which no longer existed after segmentation moved to its own panel. The `hasattr` guard prevented a crash but the code was dead.

**Fix:** Removed the two dead lines.

**Prevention:** When moving UI elements between components, grep for all references in the old location.

### 6. Duplicated Cleanup Filter Logic

**Problem:** `_on_cleanup_preview()` and `_on_cleanup_apply()` in segmentation_panel.py contained nearly identical filter logic (read labels, apply edge filter, apply area filter, compute totals).

**Fix:** Extracted `_run_cleanup_filters()` helper returning `(filtered, edge_removed, small_removed, total_removed)`.

**Prevention:** When two methods share the same multi-step computation, extract it immediately.

## Noted for Future (Not Blocking)

| Issue | When to Address |
|-------|-----------------|
| Missing type annotations (7 instances in store.py, assembler.py, importer.py) | As files are touched |
| `isinstance` vs `__class__.__name__` inconsistency for napari layers | Standardize in Phase 7+ |
| Launcher accumulating responsibilities (~1200 LOC) | Extract panels as features are built |
| SegmentationPanel reaches into launcher private attrs | Define accessor interface when refactoring |
| PandasTableModel linear label search | Build dict index when performance matters |

## Positive Findings (All Reviewers Agreed)

- CellDataModel signal hub is clean and correctly implemented
- Domain modules (io, segment, measure) are properly decoupled — zero GUI/HDF5 coupling
- Atomic writes in DatasetStore and ProjectIndex are crash-safe
- Architecture matches the brainstorm document
- Consistent naming conventions and file organization
- Frozen dataclasses with validation for configs
- Lazy imports of heavy dependencies (cellpose, napari)

## Key Patterns Validated

1. **Signal-based communication** — windows never talk to each other, only through CellDataModel
2. **Functions, not framework** — analysis code is plain functions with numpy in, DataFrame out
3. **Ephemeral DataFrame** — measurements are transient for exploration, persistent in .h5
4. **Hide-on-close for windows** — preserves state, signal connections, and geometry
5. **Worker threads for heavy computation** — GUI never freezes during Cellpose/import
