---
title: "Mask layers incorrectly appearing in Active Segmentation dropdown"
category: ui-bugs
tags:
  - napari
  - segmentation
  - phasor
  - mask-layers
  - hdf5
  - dropdown
  - data-corruption
  - metadata
  - race-condition
module: viewer
symptom: >
  phasor_roi mask layers appear in the Active Segmentation dropdown,
  get set as active segmentation via unconditional FALLBACK in
  _sync_active_layers_from_viewer, and corrupt measurements when
  mask data is used where segmentation labels are expected.
root_cause: >
  Multi-layered: (1) add_mask() not idempotent, creating duplicate
  layers with napari auto-rename suffix [1]; (2) _sync_active_layers_from_viewer
  unconditional FALLBACK treating unknown layers as segmentation;
  (3) no metadata tagging on napari layers to distinguish masks from
  segmentations; (4) store write after layer add creating a race condition;
  (5) brittle hardcoded skip sets; (6) HDF5 store holding phasor_roi
  under both /labels/ and /masks/ with no filtering in dropdown population;
  (7) _refresh_active_combos firing spurious intermediate signals during
  repopulation.
severity: high
date: 2026-03-31
---

# Mask Layers Incorrectly Classified as Segmentation

## Problem

Mask layers (e.g., `phasor_roi`, `phasor_roi [1]`) appeared in the Active Segmentation dropdown and were set as the active segmentation. This caused measurements and particle analysis to use mask data where segmentation labels were expected, producing corrupt results.

### Observable symptoms

- `phasor_roi` or `phasor_roi [1]` visible in the Active Segmentation dropdown
- Debug output: `FALLBACK set_active_segmentation('phasor_roi [1]')`
- No options in Active Mask dropdown despite mask layers existing in napari
- After deleting the mask via Layer Management, `phasor_roi` still appeared as a segmentation (stale HDF5 data under `/labels/`)

## Root Cause Chain

Seven distinct failure modes combined:

1. **Non-idempotent `add_mask()`** — Re-applying a phasor mask called `viewer.add_labels()` without removing the existing layer. Napari auto-renamed duplicates to `phasor_roi [1]`.

2. **Unsafe sync fallback** — `_sync_active_layers_from_viewer` did exact name matching against `store.list_masks()`. When `phasor_roi [1]` didn't match `phasor_roi`, the fallback unconditionally called `set_active_segmentation()`.

3. **No layer metadata** — No mechanism to tag napari layers as "mask" vs "segmentation". Classification relied entirely on name matching against the HDF5 store.

4. **Store-write race condition** — `_on_phasor_mask_applied` added the layer to napari BEFORE writing to HDF5. Napari's synchronous `layers.selection.events.active` signal fired the sync callback, which queried the store — but the mask wasn't there yet.

5. **Brittle hardcoded skip sets** — Three locations used `{"phasor_roi", "_phasor_roi_preview"}` to identify masks. Would break for any new mask type.

6. **Stale HDF5 data** — The original bug caused `phasor_roi` to be set as active segmentation, and the HDF5 file ended up with `phasor_roi` under both `/labels/` and `/masks/`. Dropdown population from `store.list_labels()` included it without filtering.

7. **Combo refresh signal leak** — `_refresh_active_combos` called `combo.clear()` then `addItem()` without `blockSignals()`. The first `addItem` on an empty combo fires `currentTextChanged`, setting a wrong active layer.

## Solution

### Defense in depth — no single fix was sufficient

Each layer addresses a distinct failure mode. Removing any one reintroduces the bug through a different path.

### 1. Idempotent `add_mask()` (`viewer.py`)

```python
# Constants at module level
PERCELL_TYPE_KEY = "percell_type"
LAYER_TYPE_MASK = "mask"
LAYER_TYPE_SEGMENTATION = "segmentation"

def add_mask(self, data, name, color_dict=None, **kwargs):
    cmap = DirectLabelColormap(color_dict=color_dict)
    if name in self.viewer.layers:
        layer = self.viewer.layers[name]
        layer.data = data
        layer.colormap = cmap
        layer.metadata[PERCELL_TYPE_KEY] = LAYER_TYPE_MASK  # ensure correct tag
    else:
        self.viewer.add_labels(
            data, name=name, colormap=cmap,
            metadata={PERCELL_TYPE_KEY: LAYER_TYPE_MASK}, **kwargs,
        )
```

**Why in-place update is safe:** Setting `.data` emits `layer.events.data` but NOT `layers.selection.events.active` — so the sync callback is not triggered. No `_coerce_name` (no rename), no `layers.events.inserted`.

### 2. Metadata tagging on all Labels layers (`viewer.py`)

`add_labels()` tags `LAYER_TYPE_SEGMENTATION`; `add_mask()` tags `LAYER_TYPE_MASK`. Set via `metadata=` constructor kwarg for earliest availability (before events fire).

### 3. Three-tier sync classification (`launcher.py`)

```
1. Check layer.metadata["percell_type"] (fastest, survives renames)
2. Fall back to store lookup (for legacy untagged layers)
3. Unknown → DO NOTHING (safe default, replaces dangerous fallback)
```

### 4. Store-before-layer ordering (`launcher.py`)

Both `_on_phasor_mask_applied` and threshold accept write to HDF5 BEFORE calling `add_mask()`. The store write is inert (no Qt/napari signals), so this is safe.

### 5. Metadata-based skip sets (`viewer.py`, `launcher.py`)

Replaced all `{"phasor_roi", "_phasor_roi_preview"}` with `layer.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_MASK`. Scales to future mask types.

### 6. Mask filtering in dropdown population (`launcher.py`)

```python
mask_set = set(store.list_masks())
for label_name in store.list_labels():
    if label_name not in mask_set:
        self._active_seg_combo.addItem(label_name)
```

Applied at: initial load, `_refresh_active_combos`. Management combos intentionally show ALL entries so users can delete stale data.

### 7. Signal blocking during combo refresh (`launcher.py`)

```python
self._active_seg_combo.blockSignals(True)
# ... clear, addItem, setCurrentText ...
self._active_seg_combo.blockSignals(False)
```

## Prevention Rules

| Rule | Why | How to apply |
|------|-----|--------------|
| **Always use `add_mask()`/`add_labels()` wrappers** | They handle metadata tagging and idempotency | Never call `viewer.add_labels()` directly for masks or segmentations |
| **Write store before adding layer** | Sync callback fires synchronously during layer add | In any `_on_*_applied` handler, call `store.write_*()` before `viewer_win.add_mask()` |
| **Never assume unknown layers are segmentations** | The FALLBACK pattern is the core of this bug | Any layer not identified by metadata or store should be ignored |
| **Block signals during combo repopulation** | Qt fires `currentTextChanged` during `clear()`/`addItem()` | Wrap `clear()` + `addItem()` loops with `blockSignals(True/False)` |
| **Filter masks from segmentation lists** | HDF5 can have names under both `/labels/` and `/masks/` | Always compute `mask_set` and exclude from segmentation queries |
| **Update metadata on in-place layer updates** | Layer may have been created with wrong metadata | Always set `layer.metadata[PERCELL_TYPE_KEY]` in the update path too |

## Key Pattern: Napari Layer Metadata Tagging

```python
# viewer.py module-level constants
PERCELL_TYPE_KEY = "percell_type"
LAYER_TYPE_MASK = "mask"
LAYER_TYPE_SEGMENTATION = "segmentation"

# Classification check (used in sync, hide, skip-set logic)
if layer.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_MASK:
    # This is a mask layer
```

**Gotcha:** Mutating `layer.metadata["key"] = val` in-place does NOT fire `layer.events.metadata`. Only full reassignment does. This is fine for classification tags (read-only after creation).

## Checklist: Adding a New Layer Type

When adding a new type of Labels layer (tracking overlay, classification mask, etc.):

- [ ] Define a new `LAYER_TYPE_*` constant in `viewer.py`
- [ ] Create a dedicated `add_*()` method in `ViewerWindow` that sets metadata
- [ ] Make the method idempotent (check `if name in self.viewer.layers`)
- [ ] Use a new HDF5 group (e.g., `/tracking/`), not `/labels/`
- [ ] Add to `_get_active_labels_layer()` skip logic
- [ ] Add to `_hide_mask_layers()` logic if needed
- [ ] Add to `_sync_active_layers_from_viewer()` metadata dispatch
- [ ] Exclude from segmentation dropdown in `_refresh_active_combos()`
- [ ] Test: add twice by name — no `[1]` suffix
- [ ] Test: click in napari — does NOT set `active_segmentation`
- [ ] Test: close/reopen dataset — correct metadata survives

## Warning Signs of Recurrence

1. **Layer names with `[1]` suffixes** — non-idempotent add called twice
2. **Sync logging "unknown layer ... ignoring"** — metadata missing, likely bypassed ViewerWindow API
3. **Clicking mask changes active segmentation** — direct symptom of original bug
4. **Segmentation combo contains mask names after load** — `list_labels()` returning masks, filter incomplete
5. **Combo flickers during dataset load** — `blockSignals` not used during repopulation
6. **`isinstance(layer, Labels)` without metadata check** — code smell, grep and verify

## Files Modified

- `src/percell4/gui/viewer.py` — Constants, idempotent `add_mask`, metadata tagging in `add_labels`/`add_mask`, metadata-based skip sets in `_hide_mask_layers` and `_get_active_labels_layer`
- `src/percell4/gui/launcher.py` — Three-tier sync, store-before-layer ordering (phasor + threshold), mask filtering in combo population, `blockSignals` in `_refresh_active_combos`, logging setup

## Related Documentation

- [DirectLabelColormap rendering blocked by events](napari-direct-label-colormap-rendering-blocked-by-events.md) — Mask layer rendering and colormap assignment patterns
- [PerCell4 phases 0-6 napari/Qt learnings](percell4-phases-0-6-napari-qt-learnings.md) — Layer lifecycle, signal timing, viewer recreation
- [Selection filtering multi-ROI patterns](percell4-selection-filtering-multi-roi-patterns.md) — Signal coalescing, DirectLabelColormap usage, combo sync
