---
title: "feat: Save each phasor ROI as a separate binary mask"
type: feat
date: 2026-04-17
brainstorm: docs/brainstorms/2026-04-17-phasor-roi-separate-masks-brainstorm.md
---

# feat: Save each phasor ROI as a separate binary mask

## Overview

Change "Apply Visible as Mask" from saving one multi-label mask (`/masks/phasor_roi` with values 0,1,2,3...) to saving each visible ROI as its own binary mask (`/masks/Fast`, `/masks/Slow`). Each gets its own napari layer and can be independently selected as the active mask.

## Problem

1. ROI names ("Fast", "Slow") don't propagate — mask is always named "phasor_roi"
2. Particle analysis collapses multi-label to binary (`mask > 0`) — per-ROI analysis impossible
3. Label ordering is confusing and can't be changed after saving

## Implementation

### 1. Change `mask_applied` signal payload

`src/percell4/interfaces/gui/peer_views/phasor_plot.py`

Change the signal from emitting one combined mask to emitting a list of per-ROI masks:

```python
# BEFORE (line 112):
mask_applied = Signal(object, object, str)  # (mask, color_dict, mask_name)

# AFTER:
mask_applied = Signal(object)  # list[tuple[str, ndarray, str]]
#                                 [(roi_name, binary_mask, hex_color), ...]
```

Update `_on_apply_mask` (line 644):

```python
def _on_apply_mask(self) -> None:
    if self._g_map is None or not self._roi_widgets:
        self._status.showMessage("No phasor data or ROIs", 3000)
        return

    roi_masks = []
    for w in self._roi_widgets:
        if not w.phasor_roi.visible:
            continue
        if w.cached_mask is None:
            w.cached_mask = phasor_roi_to_mask(
                self._g_map, self._s_map, w.phasor_roi
            )
        binary = np.zeros(self._g_map.shape, dtype=np.uint8)
        binary[w.cached_mask] = 1
        roi_masks.append((w.phasor_roi.name, binary, w.phasor_roi.color))

    if not roi_masks:
        self._status.showMessage("No visible ROIs to apply", 3000)
        return

    self.mask_applied.emit(roi_masks)
    n = len(roi_masks)
    names = ", ".join(name for name, _, _ in roi_masks)
    self._status.showMessage(f"Applied {n} mask(s): {names}", 5000)
```

### 2. Update launcher handler

`src/percell4/interfaces/gui/main_window.py`

Update `_on_phasor_mask_applied` (line 1026) to handle the new payload:

```python
def _on_phasor_mask_applied(self, roi_masks) -> None:
    """Handle phasor ROI masks: one binary mask per ROI."""
    viewer_win = self._windows.get("viewer")

    # Remove preview layer
    if viewer_win is not None and viewer_win._is_alive():
        try:
            viewer_win._viewer.layers.remove("_phasor_roi_preview")
        except ValueError:
            pass
        if hasattr(self, "_preview_timer"):
            self._preview_timer.stop()

    store = getattr(self, "_current_store", None)
    last_name = None

    for roi_name, binary_mask, hex_color in roi_masks:
        # Store-before-layer invariant
        if store is not None:
            store.write_mask(roi_name, binary_mask)

        if viewer_win is not None and viewer_win._is_alive():
            color_dict = {0: "transparent", 1: hex_color, None: "transparent"}
            viewer_win.add_mask(binary_mask, name=roi_name, color_dict=color_dict)

        last_name = roi_name

    # Set the last applied mask as active
    if last_name:
        self.data_model.set_active_mask(last_name)
```

### 3. Update signal connection

`src/percell4/interfaces/gui/main_window.py` line 563

The signal connection stays the same — just the payload shape changes:

```python
window.mask_applied.connect(self._on_phasor_mask_applied)
```

### 4. Remove old combined mask from HDF5 on re-apply

When the user re-applies ROIs (e.g., after editing), the old per-ROI masks should be overwritten. Since each ROI has a unique name, `store.write_mask(roi_name, ...)` already overwrites. No special cleanup needed — names are unique and idempotent.

### 5. Multi-ROI measurement (keep existing path)

`src/percell4/interfaces/gui/task_panels/analysis_panel.py`

The existing `_get_phasor_roi_names()` callback and `measure_multichannel_multi_roi` path in `MeasureCells` still works. The launcher's `_get_phasor_roi_names` returns `{label: name}` from the phasor plot. The `MeasureCells` use case reads the active mask and checks `mask.max() > 1` to decide between single and multi-ROI measurement.

For separate binary masks, multi-ROI measurement needs a small update: reconstruct a multi-label mask from the individual binary masks at measurement time. Add a helper:

```python
# In analysis_panel.py or main_window.py
def _build_multi_roi_mask(self) -> tuple[np.ndarray, dict[int, str]] | None:
    """Combine all phasor-origin masks into a temporary multi-label mask."""
    store = self._get_store()  # or self._get_repo()
    if store is None:
        return None

    roi_names = self._get_phasor_roi_names()
    if not roi_names:
        return None

    combined = None
    label_map = {}
    for i, (label, name) in enumerate(roi_names.items(), start=1):
        try:
            binary = store.read_mask(name)
            if combined is None:
                combined = np.zeros_like(binary, dtype=np.uint8)
            combined[binary > 0] = i
            label_map[i] = name
        except KeyError:
            continue

    if combined is None or combined.max() == 0:
        return None
    return combined, label_map
```

This is only needed if the user clicks "Measure Cells" while multiple phasor masks exist. The active mask path (single binary) works without changes.

## Files Changed

| File | Change |
|------|--------|
| `interfaces/gui/peer_views/phasor_plot.py` | Signal payload: list of `(name, mask, color)` tuples |
| `interfaces/gui/main_window.py` | Handler loops over per-ROI masks, store-before-layer for each |
| `interfaces/gui/task_panels/analysis_panel.py` | Multi-ROI measurement helper (optional — can defer) |

## What Does NOT Change

- Phasor ROI creation, editing, preview (unchanged)
- `_on_phasor_preview` handler (preview is still one combined mask)
- `phasor_roi_to_mask` domain function (unchanged)
- `analyze_particles` (already binary — works with single-label masks)
- Session/DataPanel active_mask (already tracks a single mask name)
- Threshold masks (already separate binary masks)
- ROI save/load JSON (unchanged)

## Acceptance Criteria

- [x] "Apply Visible as Mask" with 2 ROIs creates 2 separate `/masks/` entries in HDF5
- [x] Each mask layer in napari is named after the ROI (e.g., "Fast", "Slow")
- [x] Active Mask dropdown in Data tab shows individual ROI names
- [x] Particle analysis works with any single selected mask
- [ ] Multi-ROI measurement still produces per-ROI columns in the DataFrame (deferred — needs helper to reconstruct multi-label mask)
- [x] Phasor preview (live overlay while editing) still works as a combined mask
- [x] Re-applying ROIs overwrites existing masks with same names
- [x] Store-before-layer invariant preserved (write HDF5 before adding napari layer)

## Risks

| Risk | Mitigation |
|------|-----------|
| Existing HDF5 files have `/masks/phasor_roi` (multi-label) | Old format still loads fine — it's just another mask. Users can delete it via Data tab. |
| ROI name collisions with threshold masks | ROI name uniqueness is enforced in phasor plot. Threshold masks use `method_channel` pattern. Collision unlikely but harmless (overwrites). |
| Many ROIs = many mask layers in napari | Max 10 ROIs enforced. 10 layers is manageable. |

## References

- Brainstorm: `docs/brainstorms/2026-04-17-phasor-roi-separate-masks-brainstorm.md`
- Store-before-layer invariant: `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`
- Mask idempotency pattern: `viewer.py:add_mask()` already handles in-place updates
- Current signal: `phasor_plot.py:112` — `mask_applied = Signal(object, object, str)`
- Current handler: `main_window.py:1026` — `_on_phasor_mask_applied`
