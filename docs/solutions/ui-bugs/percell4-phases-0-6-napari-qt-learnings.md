---
title: "PerCell4 Phases 0-6: Napari + Qt UI/UX Learnings"
category: ui-bugs
tags: [napari, qt, pyqtgraph, multi-window, event-timing, dark-theme, cellpose, state-management]
module: gui
date: 2026-03-26
symptom: "Multiple UI bugs and UX issues discovered during iterative testing of Phases 0-6"
root_cause: "Napari event timing, Qt widget lifecycle, dark theme CSS specificity, missing app-wide state"
---

# PerCell4 Phases 0-6: Napari + Qt UI/UX Learnings

16 distinct issues fixed across 26 commits during Phases 0-6 implementation. This document captures the patterns, fixes, and prevention strategies discovered through iterative user testing.

## Bug Fixes

### 1. Napari Viewer Crash on Close/Reopen

**Symptom:** `RuntimeError: wrapped C/C++ object has been deleted` when clicking "Open Viewer" after closing the napari window.

**Root cause:** Napari's `_qt_window` is destroyed by Qt when the user closes it. The Python reference becomes a dangling pointer.

**Fix:** `_is_alive()` check probes the Qt object with a harmless `.isVisible()` inside try/except. `_ensure_viewer()` recreates the viewer if the old one is dead.

```python
def _is_alive(self) -> bool:
    if self._qt_window is None:
        return False
    try:
        self._qt_window.isVisible()
        return True
    except RuntimeError:
        return False
```

### 2. Napari event.value AttributeError

**Symptom:** `AttributeError: 'Event' object has no attribute 'value'` on label selection.

**Root cause:** Napari's event object API varies between versions. `.value` is not reliable.

**Fix:** Read from `event.source.selected_label` instead of `event.value`:

```python
def _on_label_selected(self, event) -> None:
    try:
        source = event.source
        label_id = source.selected_label
    except AttributeError:
        return
```

### 3. Polygon Tool Not Activating on First Click

**Symptom:** "Add New Label" button does nothing on first click, works on second.

**Root cause:** Napari processes layer selection asynchronously. Setting `mode = "polygon"` immediately after `layers.selection.active = layer` silently fails because the layer isn't fully selected yet.

**Fix:** Three-part approach: (1) `processEvents()` to flush Qt queue, (2) `QTimer.singleShot(100, ...)` to defer mode change, (3) correct ordering (select layer first, set label ID, then defer mode):

```python
QApplication.processEvents()
labels_layer.selected_label = next_id

def _activate_polygon():
    try:
        labels_layer.mode = "polygon"
    except Exception:
        pass

QTimer.singleShot(100, _activate_polygon)
```

### 4. Dark Background Lost on QScrollArea

**Symptom:** Panels turn white after adding scroll areas.

**Root cause:** `QScrollArea` with `transparent` background falls through to macOS system theme. The scroll area's viewport widget also needs explicit background.

**Fix:** Target the viewport child explicitly:

```python
scroll.setStyleSheet(
    "QScrollArea { background-color: #121212; border: none; }"
    " QScrollArea > QWidget > QWidget { background-color: #121212; }"
)
```

### 5. Channel Label Not Updating Live

**Symptom:** Active Channel label stays fixed after switching layers in napari.

**Root cause:** No connection to napari's `layers.selection.events.active` signal.

**Fix:** Connect with identity-based re-wiring guard (tracks `id(viewer)` to handle viewer recreation):

```python
viewer_win.viewer.layers.selection.events.active.connect(callback)
self._wired_viewer_id = viewer_id  # prevents double-wiring
```

### 6. Cellpose v3/v4 API Incompatibility

**Symptom:** `AttributeError` when instantiating Cellpose model.

**Root cause:** v4 renamed `Cellpose` to `CellposeModel`, changed eval() return from 4-tuple to 3-tuple, deprecated `channels` parameter.

**Fix:** `getattr()` fallback pattern:

```python
model_cls = getattr(models, "CellposeModel", None) or getattr(models, "Cellpose")
```

### 7. Additive Blending for Microscopy

**Symptom:** Multi-channel images render as opaque stack; labels obscure images.

**Root cause:** Napari defaults to `translucent_no_depth` blending, wrong for fluorescence.

**Fix:** Set `blending="additive"` on all image and label layers. Color cycle for unrecognized channel names (green, magenta, cyan, yellow, red, blue).

---

## Architectural Decisions from User Feedback

### Segmentation: Separate Window -> Sidebar Tab
Segmentation was initially a standalone `SegmentationWindow`. User feedback: too much context switching. Moved to `SegmentationPanel` embedded as its own "Segment" sidebar tab in the launcher.

### Import/Export: File Menu -> I/O Tab
Import and Export were scattered across the File menu and Data panel. Consolidated into a dedicated "I/O" tab as the first sidebar item.

### Active Layer State: App-Wide via CellDataModel
`CellDataModel` gained `active_segmentation` and `active_mask` properties with change signals. Bidirectional sync: napari layer click -> model -> Data tab dropdown, and vice versa. All operations reference the model's active layer.

### Dataset Persistence Across Viewer Close/Reopen
Dataset stored as app-level state (`_current_store`, `_current_h5_path`). Viewer auto-repopulates from store on reopen. Load pipeline split into three functions for reusability.

### Napari Viewer: No Custom Dock Widget
Initially had a PerCell4 side panel in napari. Removed per user request — napari is kept as a pure viewer with all its built-in controls. All PerCell4 controls live in the launcher sidebar.

---

## Prevention Patterns (Checklist)

| Pattern | When to Apply | What Happens if Skipped |
|---------|---------------|------------------------|
| `_is_alive()` before Qt access | Every Qt widget/window touch | `RuntimeError` crash |
| `_ensure_*()` for recreatable resources | Before using viewer, sub-windows | Stale reference crash |
| `QTimer.singleShot(100, ...)` for napari mode | After programmatic layer selection | Mode change silently fails |
| `blockSignals(True/False)` on widget updates | Model signal -> combo/widget update | Infinite feedback loop |
| `if new != old` in model setters | Every setter that emits a signal | Cascading redundant updates |
| `id()` tracking for signal re-wiring | Connecting to recreatable objects | Double-fire or dead connection |
| `event.source.*` not `event.value` | All napari event handlers | `AttributeError` crash |
| Explicit `background-color` on scroll areas | Every `QScrollArea` in dark theme | White background leak |
| `blending="additive"` on image/label layers | Every `add_image` / `add_labels` call | Opaque stack, invisible layers |
| Windows communicate only via `CellDataModel` | All inter-window communication | Tight coupling, null crashes |

---

## Key Napari Gotchas

1. **Event `.value` is unreliable** — always use `event.source.property`
2. **Layer selection is async** — defer mode changes with `QTimer.singleShot`
3. **`_qt_window` can be deleted independently** — always check `_is_alive()`
4. **`show=False` on Viewer construction is mandatory** when managing visibility yourself
5. **`layers.events.inserted` gives the layer** — connect to the layer's own events in the callback
