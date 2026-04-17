# Napari Multi-Label Selection Brainstorm

**Date:** 2026-04-17
**Status:** Draft

## What We're Building

A modal multi-select tool in the napari viewer that lets users click accumulate multiple segmentation labels into a staged set, then commit the set as the application's ground-truth selection (`CellDataModel.selection`).

### Workflow

1. User opens a dataset with a labels layer loaded in the napari viewer.
2. User clicks a **Multi-select** button in the viewer dock (or presses `M`) — viewer enters an accumulating-selection mode.
3. Staging starts pre-filled with the current `CellDataModel.selection` (so the tool also works as "refine my current selection").
4. Each click on a label in the labels layer **toggles** that label in the staged set. Staged labels render in a distinct color (e.g. cyan `0.6α`), visually separate from committed selection (yellow) and filter-dim (gray).
5. A counter in the dock shows live staged cell count.
6. User commits with **Ctrl+Enter** or the **Commit** button — staged set becomes `CellDataModel.selection` via `Session.set_selection(frozenset)`. All existing consumers (DataPlot, CellTable, filter-from-selection, export, `SegmentationQCController.delete_selected`) react via the normal `SELECTION_CHANGED` → `StateChange(selection=True)` pathway.
7. User cancels with **Esc** or the **Cancel** button — staged set discarded, original selection intact.

### Downstream actions are separate primitives

The feature produces one thing: a `CellDataModel.selection`. Everything else is already in the app or belongs elsewhere:

- **Filter from selection** → existing filter machinery consumes the selection.
- **Delete selected labels** → `SegmentationQCController`'s existing delete button consumes `selected_label` / selection.
- **Export selected cells** → existing export consumers of `CellDataModel.selection`.

No new downstream features are bundled with this tool.

## Why This Approach

### Modal, not modifier-based

Napari already organizes labels-layer interaction as mutually exclusive modes on `layer.mode` (`pan_zoom`, `pick`, `paint`, `fill`, `erase`). Adding a new "multi-pick" tool is idiomatically consistent — users coming from napari or Cellpose GUI expect tool-switching. Modifier-based accumulation (`Ctrl+click`) would conflict with napari's conventions (`Shift` = brush size, `Alt+click` = pan) and with macOS's OS-level `Ctrl+click` → right-click synthesis before Qt ever sees the event.

### Pure-selection commit, not bundled actions

Multi-select is a primitive. Bundling group-tagging, destructive label ops, or mask writing into it creates overlap with existing features (`SegmentationQCController` for label edits, threshold QC for masks) and hides reusable plumbing behind the new button. Keeping the commit path as "becomes `CellDataModel.selection`" means every selection consumer in the app lights up for free.

### Matches existing prior art

`SegmentationQCController` already established the tool-dock + `Ctrl+Enter` accept / `Esc` cancel pattern with coalesced refresh and layer-visibility save/restore. Multi-select will follow the same shape so users have one mental model for "interactive viewer tools."

## Key Decisions

### 1. Interaction model

- **Modal**: dedicated "Multi-select" tool activated via button / `M` shortcut.
- Normal single-click-replaces behavior preserved outside the mode — no regression.
- Mode indicator visible in the viewer dock while active.

### 2. Click semantics

- **Toggle**: click a label to add, click again to remove.
- No "add-only" variant; no separate "remove" mode.
- Background clicks (label 0) are **no-ops**. Staging is not cleared by stray clicks — users must explicitly cancel or commit.

### 3. Staging initial state

- Mode entry **pre-fills** staging with the current `CellDataModel.selection`.
- Rationale: makes the tool serve as both "build a new selection" and "refine an existing one." Esc-and-restart gives users a clean slate in one keystroke if needed.

### 4. Commit / cancel

- **Ctrl+Enter** commits; **Esc** cancels. Matches `SegmentationQCController`.
- Explicit **Commit** and **Cancel** buttons in the viewer dock for non-keyboard paths.
- Commit writes staging via `Session.set_selection(frozenset(staged_labels))` — the one canonical mutation path.
- Cancel restores the viewer to pre-mode state with no side effects.

### 5. Visual feedback

- Staged cells render in a **distinct third color** (cyan `0.6α` as starting proposal), clearly separate from the committed-selection yellow and the filter-dim gray.
- Colormap updates follow the existing `DirectLabelColormap` pattern in `viewer.py:273-343`; a new visual tier is added to the priority hierarchy (staged > committed-selected > filtered > background).
- Dock shows **live cell counter** ("N cells staged"). Counter doubles as status.

### 6. State ownership

- Staging state lives in a small controller object owned by the viewer window while the mode is active — does **not** flow through `Session` or `CellDataModel`.
- The domain state only sees the final frozenset at commit time. No "candidate selection" concept pollutes `Session`.
- On cancel, the controller is simply destroyed; no cleanup of domain state needed.

### 7. Concurrency with other tools

- Entering multi-select mode while `SegmentationQCController` is active should be blocked (interactive QC already owns the viewer).
- Entering multi-select mode while a background `Worker` is running is allowed — staging is UI-only and doesn't conflict.
- Exiting the mode (commit or cancel) is idempotent and can be called from a parent teardown if the viewer window closes mid-mode.

## Open Questions

None remaining — all UX decisions resolved in the brainstorming round. Implementation details (exact cyan shade, exact dock layout, keyboard-shortcut registration with `LauncherWindow`'s shortcut manager if any, how to intercept napari's `selected_label` event vs adding a `mouse_drag_callback`) belong in the plan.

## Resolved Questions

- **Modal vs modifier-based accumulation?** Modal, to match napari idioms and avoid macOS `Ctrl+click` conflicts.
- **What does commit do?** Pure selection — `CellDataModel.selection` replacement via `Session.set_selection`. No bundled downstream actions.
- **Entry point?** Button in the viewer dock, keyboard shortcut `M`. Not in the launcher sidebar (viewer-local state).
- **Click semantics?** Toggle.
- **Start state?** Pre-filled with current `CellDataModel.selection`.
- **Commit / cancel UX?** `Ctrl+Enter` / `Esc` plus explicit buttons.
- **Visual feedback?** Distinct staging color (cyan), live cell counter in dock.
- **Backgrounds clicks clear staging?** No — explicit cancel only.

## References

- Existing selection infra: `src/percell4/model.py`, `src/percell4/application/session.py:50,120-122,144-148,162-167`.
- Viewer integration: `src/percell4/gui/viewer.py:239-264,273-343,355-388`.
- Prior-art tool-mode pattern: `src/percell4/gui/workflows/single_cell/seg_qc.py` (layer visibility save/restore, Ctrl+Enter accept, Esc cancel, coalesced refresh).
- Prior-art toggle-click pattern: `src/percell4/gui/data_plot.py:307-330` (Ctrl+click toggle).
- Architecture contracts: `pyproject.toml:91-136` (importlinter — viewer may import Qt+napari; domain/application stay Qt-free).
