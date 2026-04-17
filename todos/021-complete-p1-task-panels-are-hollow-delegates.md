---
status: complete
priority: p1
issue_id: "021"
tags: [code-review, architecture, task-panels]
dependencies: []
---

# Task panels are hollow delegates — they call back into launcher

## Problem Statement

All four task panels (io_panel.py, analysis_panel.py, flim_panel.py, data_panel.py) receive a `launcher` reference and call back into it via private methods. IoPanel is the worst case — 6 methods that each just call `self._launcher._on_X()`. These panels moved lines out of the launcher without moving responsibility. The hex architecture plan says panels should own behavior via injected use cases.

## Findings

- **Source**: Architecture strategist + code simplicity reviewer
- **Locations**:
  - `interfaces/gui/task_panels/io_panel.py` — 6 pure passthrough methods
  - `interfaces/gui/task_panels/analysis_panel.py` — uses `self._launcher._windows.get("viewer")`, `self._launcher._get_phasor_roi_names()`
  - `interfaces/gui/task_panels/flim_panel.py` — uses `self._launcher._windows.get("viewer")`
  - `interfaces/gui/task_panels/data_panel.py` — uses `self._launcher._current_store`
- **Impact**: The panels cannot be tested without a full launcher. The architectural boundary is cosmetic, not real.

## Proposed Solutions

### Option A: Inject use cases + viewer accessor at construction (Recommended)
Panels receive use cases, Session, and a viewer callback at construction instead of the launcher. IoPanel handlers call use cases directly. AnalysisPanel/FlimPanel get a `get_viewer_window` callback for napari operations.
- Effort: Large
- Risk: Medium — requires updating launcher panel construction and all handler methods

### Option B: Accept transitional coupling, document cleanup path
Mark the launcher references as tech debt. Focus on making the panels functional. Clean up when the launcher is fully retired.
- Effort: Small
- Risk: Low — but architectural promise remains unfulfilled

## Acceptance Criteria

- [ ] No task panel imports from or references `launcher` or `LauncherWindow`
- [ ] Task panels receive dependencies via constructor injection
- [ ] Task panels can be instantiated in tests with mock dependencies
