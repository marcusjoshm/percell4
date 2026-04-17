---
status: pending
priority: p2
issue_id: "031"
tags: [code-review, shim, architecture, task-panels]
dependencies: ["030"]
---

# Decouple GroupedSegPanel from launcher (last transitional coupling)

## Problem Statement

All 4 task panels were decoupled from the launcher via callback injection, but `GroupedSegPanel` was left behind. It's passed `launcher=self` through `AnalysisPanel._launcher_for_grouped`. This is the last `self._launcher` reference in the task panel layer and is explicitly marked "transitional."

## Findings

- **Location**: `interfaces/gui/task_panels/analysis_panel.py` line 47: `launcher=None, # transitional: only for GroupedSegPanel`
- **Location**: `interfaces/gui/main_window.py` line 256: `launcher=self, # transitional: only for GroupedSegPanel`
- **Location**: `gui/grouped_seg_panel.py` — uses `self._launcher` for viewer access, store access, statusBar

## Proposed Solutions

### Option A: Apply callback injection pattern (same as other panels)
Give GroupedSegPanel `get_store`, `get_viewer_window`, `show_status` callbacks. Remove `launcher=` from AnalysisPanel and main_window. Follow the pattern documented in `docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md`.
- Effort: Small-Medium
- Risk: Low

## Acceptance Criteria

- [ ] Zero `launcher=` or `_launcher` in any task panel or GroupedSegPanel
- [ ] GroupedSegPanel receivable via constructor injection in tests
