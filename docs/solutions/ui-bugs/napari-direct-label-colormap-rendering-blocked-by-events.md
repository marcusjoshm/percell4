---
title: "DirectLabelColormap not rendering on first application when using events.colormap.blocker()"
date: 2026-03-29
module: src/percell4/gui/viewer.py
tags: [napari, DirectLabelColormap, colormap-blocker, selection-highlight, rendering, show_selected_label]
severity: high
type: ui-bug
---

# DirectLabelColormap Not Rendering Due to events.colormap.blocker()

## Symptoms

- Multi-cell selection via Ctrl+click showed cells in **cyan instead of yellow** on the first and second selection
- Third and subsequent selections rendered correctly
- Clearing selection (Escape or clicking background) failed to restore the original napari multicolor colormap
- Non-selected cells appeared brown/orange instead of transparent

## Affected Components

- `src/percell4/gui/viewer.py` — `_update_label_display`, `_restore_colormap`
- Downstream: all windows that trigger selection changes (data_plot, cell_table, launcher)

## Root Cause

Four compounding issues:

### 1. `events.colormap.blocker()` suppresses napari's rendering pipeline

napari's rendering pipeline listens on the `colormap` event to trigger a GPU texture update. `events.colormap.blocker()` prevents that event from firing. The colormap object is assigned to the layer property but never actually rendered. `layer.refresh(extent=False)` was called as a workaround but does not re-trigger the full colormap upload path.

**Before:**
```python
with labels_layer.events.colormap.blocker():
    labels_layer.colormap = DirectLabelColormap(color_dict=color_dict)
labels_layer.refresh(extent=False)
```

**After:**
```python
labels_layer.colormap = DirectLabelColormap(color_dict=color_dict)
```

The blocker was originally added to prevent feedback loops, but the `_is_originator` flag already guards against re-entrancy. The blocker was redundant and harmful.

Same fix applied to `_restore_colormap`:

**Before:**
```python
def _restore_colormap(self, layer):
    if layer.name in self._original_colormaps:
        with layer.events.colormap.blocker():
            layer.colormap = self._original_colormaps.pop(layer.name)
        layer.refresh(extent=False)
```

**After:**
```python
def _restore_colormap(self, layer):
    if layer.name in self._original_colormaps:
        layer.colormap = self._original_colormaps.pop(layer.name)
```

### 2. `show_selected_label` mode transition poisons DirectLabelColormap

napari's `show_selected_label` activates an internal rendering path. When transitioning out of this mode into a DirectLabelColormap, the first colormap assignment silently fails to render. This appears to be a napari state machine issue where the label rendering mode doesn't fully reset.

The original code used `show_selected_label` for single-cell selection (optimization), then switched to DirectLabelColormap for multi-cell. The 1-cell to 2-cell transition was the trigger.

**Fix:** Always use DirectLabelColormap for all selection states. Never mix with `show_selected_label`.

```python
# Any selection or filter: use DirectLabelColormap exclusively.
# We avoid napari's show_selected_label because transitioning
# from that mode to DirectLabelColormap causes a rendering glitch.
labels_layer.show_selected_label = False
```

### 3. Additive blending artifacts from dim gray non-selected cells

Non-selected cells were assigned `[0.5, 0.5, 0.5, 0.15]` (dim gray). With labels layers defaulting to additive blending, this gray blended with the fluorescence image underneath, producing confusing brown/orange artifacts.

**Fix:** Use fully transparent `[0.0, 0.0, 0.0, 0.0]` for non-selected cells. Transparent contributes nothing under additive blending.

### 4. Mask layers override selection colormap colors

Mask layers (e.g., `phasor_roi`) are Labels layers that sit above the segmentation layer. napari composites top-down, so the mask's colors override the selection highlight on the segmentation layer below.

**Fix:** Temporarily hide mask layers during selection/filter highlighting:

```python
def _hide_mask_layers(self):
    seg_name = self.data_model.active_segmentation
    _skip = {seg_name, "_phasor_roi_preview"}
    for layer in self._viewer.layers:
        if (isinstance(layer, napari.layers.Labels)
                and layer.name not in _skip
                and not layer.name.startswith("_")):
            if layer.name not in self._hidden_mask_layers and layer.visible:
                self._hidden_mask_layers[layer.name] = layer.opacity
                layer.visible = False

def _restore_mask_layers(self):
    for name, opacity in list(self._hidden_mask_layers.items()):
        try:
            layer = self._viewer.layers[name]
            layer.visible = True
            layer.opacity = opacity
        except KeyError:
            pass
    self._hidden_mask_layers.clear()
```

## Why All Four Fixes Were Required

These issues compounded each other. Removing the blocker (fix 1) was necessary but not sufficient — the `show_selected_label` transition (fix 2) would still cause the first post-transition colormap to fail. Even with both rendering issues fixed, mask layer occlusion (fix 4) would hide the correctly-rendered selection colors. The additive blending artifact (fix 3) was independently visible whenever dim gray was used.

## Prevention Rules

| Rule | Severity | Rationale |
|------|----------|-----------|
| Never use `events.colormap.blocker()` on label layers | Critical | Suppresses GPU texture update; colormap appears blank |
| Set `show_selected_label = False` before assigning DirectLabelColormap | Critical | Mode transition leaves internal rendering state inconsistent |
| Never combine `show_selected_label = True` with a custom colormap | High | These are mutually exclusive display modes |
| Never mutate a DirectLabelColormap in place; always create and reassign | High | In-place mutation does not trigger the colormap event |
| Always include background/default color in `color_dict` (key `0` or `None`) | Medium | Unlabeled pixels render unpredictably otherwise |
| Use `_is_originator` flag for re-entrancy protection, not event blockers | Medium | Blockers suppress internal pipeline; flags only suppress your own code |

## Related Documentation

- `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md` — Covers DirectLabelColormap patterns, signal coalescing, and colormap save/restore
- `docs/solutions/ui-bugs/percell4-phases-0-6-napari-qt-learnings.md` — Napari event timing, async layer selection, Qt widget lifecycle
- `docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md` — Colormap index fix, thread safety
