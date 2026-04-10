---
title: "fix: Mask layers incorrectly classified as segmentation"
type: fix
date: 2026-03-31
deepened: 2026-03-31
---

# fix: Mask layers incorrectly classified as segmentation

## Enhancement Summary

**Deepened on:** 2026-03-31
**Research agents used:** Python reviewer, Pattern recognition, Architecture strategist, Code simplicity, Race condition reviewer, Codebase explorer, Learnings analyzer, Napari best practices

### Key Improvements
1. Tiered implementation plan (essential vs hardening) based on simplicity analysis
2. Discovered 2 additional race conditions (combo refresh signals, preview timer)
3. Added `_accept_threshold` to fix scope (same store-before-layer race)
4. Constants/enum for metadata keys to prevent magic string bugs
5. Complete layer creation site audit (9+ locations needing metadata)

### Reviewer Consensus
- **All reviewers agree:** Idempotent `add_mask` + remove fallback are essential
- **Tension:** Simplicity reviewer says metadata is YAGNI; architecture/pattern reviewers say it's worth the incremental cost. **Resolution:** Implement in two tiers — essential fixes first, then metadata hardening

---

## Overview

Mask layers (e.g., `phasor_roi [1]`) appear in the Active Segmentation dropdown and get set as the active segmentation. This corrupts measurements and particle analysis because a mask layer is used where a segmentation layer is expected.

## Root Cause Chain

1. User applies phasor mask → `phasor_roi` layer added to napari
2. User re-applies phasor mask → `_on_phasor_mask_applied` does **not** remove the old layer
3. Napari auto-renames the new layer to `phasor_roi [1]`
4. User clicks `phasor_roi [1]` in napari's layer list
5. `_sync_active_layers_from_viewer` checks `store.list_masks()` → returns `["phasor_roi"]`
6. Exact name match fails → **FALLBACK** fires → `set_active_segmentation("phasor_roi [1]")`
7. `_on_model_active_seg_changed` dynamically adds `phasor_roi [1]` to the segmentation dropdown
8. Subsequent measurements use the mask layer as if it were segmentation labels

### Additional Race Confirmed

Even on **first** mask apply (no rename), the sync race fires: `add_mask()` triggers napari's `layers.selection.events.active` synchronously → `_sync_active_layers_from_viewer` queries the store → store write hasn't happened yet → fallback fires. Qt/psygnal signals are synchronous in-thread, so this is deterministic, not probabilistic.

---

## Implementation Tiers

### Tier 1: Essential Fixes (~8 lines, fixes the bug)

These two changes sever the bug chain and are sufficient for a minimal fix.

### Tier 2: Hardening (~30 lines, prevents the class of bugs)

Metadata tagging, skip set replacement, and additional race fixes. Worth doing because: (a) the app already has threshold masks headed the same direction, (b) `layer.metadata` is napari's intended extension point (confirmed from source), (c) replaces 3 hardcoded skip sets.

---

## Tier 1: Essential Fixes

### 1. Make `viewer.add_mask()` idempotent

**File:** `src/percell4/gui/viewer.py:174-192`

If a layer with the same name already exists, update `.data` and `.colormap` in-place instead of calling `viewer.add_labels()`. This prevents napari's auto-rename at the source.

```python
def add_mask(self, data, name: str, color_dict: dict | None = None, **kwargs) -> None:
    from napari.utils.colormaps import DirectLabelColormap

    if color_dict is None:
        color_dict = {0: "transparent", 1: "yellow", None: "transparent"}
    kwargs.pop("colormap", None)
    cmap = DirectLabelColormap(color_dict=color_dict)

    if name in self.viewer.layers:
        # Update existing layer in-place — no rename, no insert event
        layer = self.viewer.layers[name]
        layer.data = data
        layer.colormap = cmap
    else:
        if "opacity" not in kwargs:
            kwargs["opacity"] = 0.5
        self.viewer.add_labels(data, name=name, colormap=cmap, **kwargs)
```

#### Research Insights

**Why in-place update is safe:**
- Setting `.data` emits `layer.events.data` but does NOT emit `layers.selection.events.active` — so `_sync_active_layers_from_viewer` is NOT triggered
- Setting `.colormap` emits `layer.events.colormap` — not connected to anything dangerous
- No `_coerce_name` (no rename risk), no `layers.events.inserted` (no spurious sync)
- Pattern already exists at `launcher.py:1140-1141` for preview layer

**Must update colormap too:** When user redraws phasor ROIs, the `color_dict` changes (per-ROI colors). Updating only `.data` would leave stale colors.

### 2. Remove the unconditional FALLBACK in sync

**File:** `src/percell4/gui/launcher.py:1061-1064`

Replace the fallback `self.data_model.set_active_segmentation(name)` with a `return`. Unknown layers should be ignored, not guessed at.

```python
# BEFORE (line 1064):
self.data_model.set_active_segmentation(name)

# AFTER:
import logging
logger = logging.getLogger(__name__)
logger.debug("_sync_active_layers: unknown layer %r, ignoring", name)
return
```

Also: remove the debug `print()` statements at lines 1052-1061. Replace with `logger.debug()` calls or remove entirely.

---

## Tier 2: Hardening

### 3. Tag layers with metadata at creation

**Files:** `src/percell4/gui/viewer.py`

Set `layer.metadata["percell_type"]` when creating Labels layers. Use napari's `metadata=` constructor kwarg for earliest availability (before any events fire).

#### Use constants, not magic strings

```python
# At top of viewer.py (or a shared constants module if preferred)
PERCELL_TYPE_KEY = "percell_type"
LAYER_TYPE_MASK = "mask"
LAYER_TYPE_SEGMENTATION = "segmentation"
```

#### Tag in `add_mask`:

```python
# In the else branch (new layer):
self.viewer.add_labels(
    data, name=name, colormap=cmap,
    metadata={PERCELL_TYPE_KEY: LAYER_TYPE_MASK},
    **kwargs,
)
# In the if branch (existing layer) — metadata already set from first add
```

#### Tag in `add_labels`:

```python
def add_labels(self, data, name: str, **kwargs) -> None:
    kwargs.setdefault("metadata", {})[PERCELL_TYPE_KEY] = LAYER_TYPE_SEGMENTATION
    self.viewer.add_labels(data, name=name, **kwargs)
```

#### All layer creation sites that need tagging

| Location | Layer Name | Type | Action |
|---|---|---|---|
| `viewer.py:169` `add_labels()` | segmentation names | segmentation | Tag via method |
| `viewer.py:174` `add_mask()` | mask names | mask | Tag via method |
| `launcher.py:939` | loaded segmentations | segmentation | Goes through `add_labels` |
| `launcher.py:944` | loaded masks | mask | Goes through `add_mask` |
| `launcher.py:1144` | `_phasor_roi_preview` | preview | Tag as `"preview"` or rely on `_` prefix |
| `launcher.py:1275` | `_threshold_preview` | preview | Tag as `"preview"` or rely on `_` prefix |
| `launcher.py:1401` | threshold mask | mask | Goes through `add_mask` |
| `launcher.py:1160` | phasor mask | mask | Goes through `add_mask` |
| `segmentation_panel.py:309,346,373,391` | cellpose/manual/ROI | segmentation | Goes through `add_labels` |
| `segmentation_panel.py:537` | `_cleanup_preview` | preview | Tag or rely on `_` prefix |

**Decision on preview layers:** Keep the `_` prefix convention for previews. They are transient and don't need metadata classification — the `_` prefix already excludes them from `_hide_mask_layers` (line 333: `not layer.name.startswith("_")`).

### 4. Enhance `_sync_active_layers_from_viewer` with metadata-first classification

**File:** `src/percell4/gui/launcher.py:1048-1064`

```
1. Check layer.metadata.get("percell_type") first
   - "mask" → set_active_mask, return
   - "segmentation" → set_active_segmentation, return
2. Fall back to store lookup (existing logic) for untagged layers
3. If neither matches → log debug, return early (DO NOTHING)
```

### 5. Reorder `_on_phasor_mask_applied` AND `_accept_threshold`

**Files:** `src/percell4/gui/launcher.py:1150-1167` and `launcher.py:~1401-1408`

Both methods have the same race: layer add before store write.

**`_on_phasor_mask_applied` new order:**
1. Remove preview layer
2. **Stop preview timer** (`self._preview_timer.stop()` — prevents stale preview from reappearing after apply)
3. Write to HDF5 store
4. Add layer to viewer (sync can now find it in store)
5. Set active mask

**`_accept_threshold` new order:** Same pattern — store write before `add_mask`.

#### Research Insight: Preview timer race

The race condition reviewer discovered: if the user drags an ROI and immediately clicks "Apply", the `_preview_timer` (100ms debounce) may still be pending. It fires after apply, re-creating the preview layer on top of the finalized mask. Fix: `_preview_timer.stop()` at the top of apply.

### 6. Replace hardcoded skip sets with metadata checks

Three locations use hardcoded `{"phasor_roi", "_phasor_roi_preview"}` skip sets:

| Location | Line | Current | New |
|---|---|---|---|
| `viewer.py` `_hide_mask_layers` | ~329 | `{seg_name, "_phasor_roi_preview"}` | `layer.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_MASK` |
| `viewer.py` `_get_active_labels_layer` | ~369 | `{"phasor_roi", "_phasor_roi_preview"}` | `layer.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_MASK` |
| `launcher.py` `_get_active_labels` | ~1103 | `{"phasor_roi", "_phasor_roi_preview"}` | `layer.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_MASK` |

Keep the `layer.name.startswith("_")` check for preview layers — it is a separate concern from mask detection.

### 7. Fix `_refresh_active_combos` spurious signals

**File:** `src/percell4/gui/launcher.py:2154-2173`

`_refresh_active_combos` calls `combo.clear()` then `addItem()` in a loop without blocking signals. The first `addItem` on an empty combo fires `currentTextChanged`, which calls `set_active_segmentation` with the wrong name before the desired name is set.

```python
def _refresh_active_combos(self) -> None:
    store = getattr(self, "_current_store", None)
    if hasattr(self, "_active_seg_combo"):
        self._active_seg_combo.blockSignals(True)
        current = self._active_seg_combo.currentText()
        self._active_seg_combo.clear()
        if store is not None:
            for name in store.list_labels():
                self._active_seg_combo.addItem(name)
        if current and self._active_seg_combo.findText(current) >= 0:
            self._active_seg_combo.setCurrentText(current)
        self._active_seg_combo.blockSignals(False)
    # Same pattern for _active_mask_combo
```

---

## Edge Cases Addressed

- **Store not loaded:** Metadata check still works (doesn't depend on store). Untagged layers fall through to "do nothing" — safe.
- **Multiple mask types:** Metadata tagging scales — any future mask type just needs `LAYER_TYPE_MASK`
- **User manually adds Labels layer:** No metadata → no match → sync does nothing (safe default)
- **First-time mask apply race:** Reordering store-write-first eliminates the window where sync can't find the mask
- **Re-apply with different ROI colors:** Idempotent `add_mask` updates both `.data` and `.colormap`
- **Preview timer race:** `_preview_timer.stop()` at top of apply prevents stale preview reappearing
- **Combo refresh race:** `blockSignals(True)` during refresh prevents intermediate garbage state
- **Napari layer rename by user:** Metadata survives renames (napari doesn't touch `metadata` dict during `_coerce_name`)

## Acceptance Criteria

- [x] Re-applying phasor mask updates the existing layer in-place (no `[1]` suffix)
- [x] Clicking a mask layer in napari sets active **mask**, not active segmentation
- [x] Active Segmentation dropdown never contains mask layer names
- [x] Active Mask dropdown correctly shows mask layers
- [x] Hardcoded skip sets replaced with metadata checks
- [x] No regression in selection highlighting (mask layers still hidden/restored correctly)
- [x] Measurements use only the segmentation layer, never a mask layer
- [x] Preview timer stopped on mask apply (no ghost preview layer)
- [x] Combo refresh does not fire spurious intermediate signals
- [x] Debug print statements replaced with `logging.debug`

## Files to Modify

- `src/percell4/gui/viewer.py` — `add_mask` idempotency + metadata tagging, `add_labels` metadata tagging, skip set replacement, constants
- `src/percell4/gui/launcher.py` — sync logic, `_on_phasor_mask_applied` reorder + timer stop, `_accept_threshold` reorder, skip set replacement, combo refresh fix, debug prints → logging
- `src/percell4/model.py` — no changes needed (model is correctly store-agnostic)

## Implementation Order

1. **Tier 1 first:** Idempotent `add_mask` + remove fallback. Test that the bug is fixed.
2. **Tier 2:** Add constants, metadata tagging, enhance sync, reorder both apply methods, replace skip sets, fix combo refresh.
3. **Cleanup:** Remove debug print statements.

## References

- Debug output showing the bug: FALLBACK path at `launcher.py:1061-1064`
- Existing in-place update pattern: `launcher.py:1140-1141` (preview layer)
- Napari `layer.metadata` source: `.venv/.../napari/layers/base/base.py:577,862-871`
- Napari name coercion: `.venv/.../napari/components/layerlist.py:203-222`
- Solution docs: `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`
- Solution docs: `docs/solutions/ui-bugs/percell4-phases-0-6-napari-qt-learnings.md`
- Solution docs: `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`
