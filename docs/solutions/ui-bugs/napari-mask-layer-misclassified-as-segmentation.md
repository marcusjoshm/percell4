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
last_refreshed: 2026-05-15
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

### 1. Collision-blocking `add_mask()` (`viewer.py`)

```python
# Constants at module level
PERCELL_TYPE_KEY = "percell_type"
LAYER_TYPE_MASK = "mask"
LAYER_TYPE_SEGMENTATION = "segmentation"

def add_mask(self, data, name, color_dict=None, **kwargs):
    if name in self.viewer.layers:
        existing = self.viewer.layers[name]
        QMessageBox.warning(
            self._qt_window,
            "Mask name conflict",
            f"Can't add mask {name!r}: a "
            f"{type(existing).__name__} layer with that name already "
            f"exists. Rename the new mask or remove the existing "
            f"layer before retrying.",
        )
        return
    cmap = DirectLabelColormap(color_dict=color_dict)
    self.viewer.add_labels(
        data, name=name, colormap=cmap,
        metadata={PERCELL_TYPE_KEY: LAYER_TYPE_MASK}, **kwargs,
    )
```

**Why hard-block, not in-place refresh:** The original refresh path (`layer.data = data; layer.colormap = cmap`) avoided the `[1]` auto-rename but assumed every same-name layer was a Labels layer. That assumption broke when a user-named phasor ROI collided with an intensity channel — assigning a `DirectLabelColormap` to the existing `Image` layer crashed deep inside napari's thumbnail update with `TypeError: DirectLabelColormap can only be used with int`. Hard-blocking on any same-name collision still prevents the `[1]` suffix bug AND surfaces cross-type collisions as a naming error rather than a crash. See [add_mask cross-type name collision](add-mask-name-collision-image-layer-crash-2026-05-15.md) for the full incident.

### 2. Metadata tagging on all Labels layers (`viewer.py`)

`add_labels()` tags `LAYER_TYPE_SEGMENTATION`; `add_mask()` tags `LAYER_TYPE_MASK`. Set via `metadata=` constructor kwarg for earliest availability (before events fire).

### 3. Three-tier sync classification — SUPERSEDED, do not apply

This section described a napari -> session sync that classified layers as they
were selected in the viewer. **That mechanism no longer exists** and applying it
today would reintroduce an edge the architecture now forbids: a repo-wide search
for `_sync_active_layers_from_viewer` or a `layers.selection.events` subscription
returns nothing.

The replacement is a one-way push in the other direction — session state drives
napari, never the reverse. See
[`../architecture-patterns/session-to-napari-one-way-push.md`](../architecture-patterns/session-to-napari-one-way-push.md),
which names this section's approach explicitly as the anti-pattern it removed.

The rest of this document still holds; only this section was superseded.

### 4. Store-before-layer ordering (`main_window.py`)

Both `_on_phasor_mask_applied` and threshold accept write to HDF5 BEFORE calling `add_mask()`. The store write is inert (no Qt/napari signals), so this is safe.

### 5. Metadata-based skip sets + leading-underscore convention (`viewer.py`, `main_window.py`)

Replaced all `{"phasor_roi", "_phasor_roi_preview"}` with `layer.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_MASK`. Scales to future mask types.

**Parallel safety mechanism:** all three classification consumers (`_hide_mask_layers`, `_get_active_labels_layer`, and the launcher's segmentation-fallback) check `layer.name.startswith("_")` *before* the metadata check. Layers whose names begin with `_` (e.g., `_phasor_roi_preview_<roi_name>` per [Phasor ROI preview layer ownership](phasor-roi-preview-layer-ownership-2026-05-03.md)) are treated as transient overlays and excluded from segmentation classification regardless of whether they carry `PERCELL_TYPE_KEY` metadata. This means transient overlay layers may legitimately bypass the `add_mask` / `add_labels` wrappers and call `viewer._viewer.add_labels` directly, *as long as* their name uses the `_` prefix convention.

### 6. Mask filtering in dropdown population (`main_window.py`, `hdf5_store.py`)

The exclusion is **surface-dependent**, and getting this backwards breaks
something either way:

| Surface | Lists | Why |
|---|---|---|
| **Selection** — the active-segmentation choices | label sets *not* also present as masks | offering a mask as an object labelling is this bug |
| **Management** — the rename / delete combos | **every** label set, unfiltered | a shadowed label set must stay reachable, or the user cannot rename or delete it |

Applied at dataset load, when the viewer is populated, and by each flow that
publishes a new segmentation list after writing one. The management combos
re-list straight from the store on purpose — **do not "fix" them to filter.**

Known benign divergence: the data panel's rename and delete paths republish the
selection list without the exclusion. It is currently unobservable, because the
combo those paths actually refresh is a management combo that reads the store
directly, and any later publisher restores the filtered list. It would only
surface for a dataset holding one name under both namespaces, in the window
between that rename/delete and the next publish. Worth aligning if the code is
touched for another reason; not worth a change on its own.

### 7. Re-entrancy guarding during combo refresh (`session_window.py`)

```python
self._active_seg_combo.blockSignals(True)
# ... clear, addItem, setCurrentText ...
self._active_seg_combo.blockSignals(False)
```

## Prevention Rules

| Rule | Why | How to apply |
|------|-----|--------------|
| **Use `add_mask()`/`add_labels()` wrappers for persistent layers** | They handle metadata tagging and idempotency | Never call `viewer.add_labels()` directly for masks or segmentations. **Exception:** transient overlay layers whose name starts with `_` (e.g., `_phasor_roi_preview_<name>`) are excluded from segmentation classification by name convention and may call raw `viewer._viewer.add_labels` directly. See [Phasor ROI preview layer ownership](phasor-roi-preview-layer-ownership-2026-05-03.md) for the launcher-mediated per-resource pattern. |
| **Write store before adding layer** | Sync callback fires synchronously during layer add | In any `_on_*_applied` handler, call `store.write_*()` before `viewer_win.add_mask()` |
| **Write store deletion before/with layer removal** | Mirror of the rule above — channels reappeared on reload because deletion only removed napari layers | In delete handlers, mutate `/intensity`, `/labels/<name>`, `/masks/<name>` (and any FLIM-derived groups like `/decay/<ch>`, `/phasor/<ch>`, `/provenance/decay/<ch>`) before / alongside `viewer.layers.remove(name)`. See [`flim-phasor-cross-layer-alignment-2026-04-29.md`](../logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md) for the channel-deletion adjacent fix. |
| **Never assume unknown layers are segmentations** | The FALLBACK pattern is the core of this bug | Any layer not identified by metadata or store should be ignored |
| **Suppress echo during combo repopulation** | Qt fires `currentTextChanged` during `clear()`/`addItem()` | Guard the repopulate with a re-entrancy flag every slot checks — see Section 7 |
| **Exclude masks from *selection* lists — not from management lists** | A name can exist under both `/labels/` and `/masks/`. Offering a mask as an object labelling is this bug; hiding it from the rename/delete UI would strand it | Exclude when publishing the active-segmentation choices. Do **not** exclude in management combos — see Section 6 |
| **Hard-block, never coerce, on same-name layer collision** | napari's layer namespace is flat across types — same-name layer can be Image, Labels, anything | In `add_mask`, refuse and surface a `QMessageBox.warning` rather than assigning a `DirectLabelColormap` to a non-Labels layer |

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
- [ ] Decide collision policy explicitly — hard-block (recommended, mirrors `add_mask`) or type-narrowed refresh — and never silently coerce across types
- [ ] Use a new HDF5 group (e.g., `/tracking/`), not `/labels/`
- [ ] Add to `_get_active_labels_layer()` skip logic
- [ ] Add to `_hide_mask_layers()` logic if needed
- [ ] Exclude from the segmentation list where it is computed — see Section 6
- [ ] Test: add twice by name — collision is surfaced (warning/refuse), never a silent `[1]` suffix
- [ ] Test: pre-existing layer of a *different* type with the same name — collision is surfaced, no crash inside `intensity_mixin._update_thumbnail`
- [ ] Test: click in napari — does NOT set `active_segmentation`
- [ ] Test: close/reopen dataset — correct metadata survives

## Warning Signs of Recurrence

1. **Layer names with `[1]` suffixes** — `add_mask` was bypassed; collision-block not in effect
2. **`TypeError: DirectLabelColormap can only be used with int` in a load stack** — same-name collision between a mask and a non-Labels layer; check that `add_mask`'s hard-block is still present and that `_populate_viewer_from_store`'s `clear()` ran
3. **Sync logging "unknown layer ... ignoring"** — metadata missing, likely bypassed ViewerWindow API
4. **Clicking mask changes active segmentation** — direct symptom of original bug
5. **Segmentation combo contains mask names after load** — `list_labels()` returning masks, filter incomplete
6. **Combo flickers during dataset load** — `blockSignals` not used during repopulation
7. **`isinstance(layer, Labels)` without metadata check** — code smell, grep and verify

## Files Modified

- `src/percell4/gui/viewer.py` — Constants, collision-blocking `add_mask` (hard-blocks any same-name layer with `QMessageBox.warning`), metadata tagging in `add_labels`/`add_mask`, metadata-based skip sets in `_hide_mask_layers` and `_get_active_labels_layer`
- `src/percell4/interfaces/gui/main_window.py` — store-before-layer ordering (phasor + threshold), mask filtering in list population, logging setup. (Was `src/percell4/gui/launcher.py`; that re-export shim was deleted in `ea94abb`.)
- `src/percell4/adapters/hdf5_store.py` — the mask-filtering rule's primary home today: `segmentation_names` is computed as the label names *not* also present as masks.
- `src/percell4/interfaces/gui/peer_views/session_window.py` — combo repopulation, now guarded by a re-entrancy flag rather than `blockSignals`.

## Related Documentation

- [Session -> napari one-way push](../architecture-patterns/session-to-napari-one-way-push.md) — **supersedes Section 3.** The canonical rule for which direction state flows between the session and the viewer.

- [add_mask cross-type name collision crash](add-mask-name-collision-image-layer-crash-2026-05-15.md) — The follow-on incident that motivated the hard-block collision policy in Section 1
- [DirectLabelColormap rendering blocked by events](napari-direct-label-colormap-rendering-blocked-by-events.md) — Mask layer rendering and colormap assignment patterns
- [PerCell4 phases 0-6 napari/Qt learnings](percell4-phases-0-6-napari-qt-learnings.md) — Layer lifecycle, signal timing, viewer recreation
- [FLIM phasor cross-layer alignment](../logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md) — Extends "Write store before adding layer" to the deletion mirror, plus invalidating `/phasor/<ch>` whenever `/decay/<ch>` is rewritten so cached derived layers can't be displayed against fresh source.
- [Selection filtering multi-ROI patterns](percell4-selection-filtering-multi-roi-patterns.md) — Signal coalescing, DirectLabelColormap usage, combo sync
- [Phasor ROI preview layer ownership](phasor-roi-preview-layer-ownership-2026-05-03.md) — Per-ROI preview layers (`_phasor_roi_preview_<name>`) created via raw `viewer._viewer.add_labels` and excluded from segmentation by the `_` prefix convention; per-resource Qt signals for upsert / remove / clear.
