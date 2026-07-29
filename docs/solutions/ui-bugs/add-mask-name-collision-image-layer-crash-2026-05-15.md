---
title: "ViewerWindow.add_mask crashes when mask name collides with a non-Labels layer"
date: 2026-05-15
category: ui-bugs
module: percell4.gui.viewer
problem_type: ui_bug
component: tooling
severity: high
symptoms:
  - "TypeError: DirectLabelColormap can only be used with int raised inside napari intensity_mixin._update_thumbnail during dataset load"
  - Dataset load aborts partway through _populate_viewer_from_store — channels appear in the viewer, then the first colliding mask fails
  - Stack ends at gui/viewer.py add_mask `layer.colormap = cmap` with no clue to the user that the actual cause is a layer-name collision
root_cause: logic_error
resolution_type: code_fix
tags:
  - napari
  - viewer
  - add-mask
  - layer-collision
  - directlabelcolormap
  - phasor-roi
  - hdf5
  - thumbnail-crash
applies_to:
  - "src/percell4/gui/viewer.py"
canonical_source: "src/percell4/gui/viewer.py"
---

# ViewerWindow.add_mask crashes when mask name collides with a non-Labels layer

## Problem

Loading certain PerCell4 `.h5` datasets crashed with `TypeError: DirectLabelColormap can only be used with int` deep inside napari's thumbnail update. The trigger was a flat-namespace collision: an `Image` channel and a mask shared the same name (`"CA-SiR"`), and `ViewerWindow.add_mask`'s "idempotent in-place refresh" branch silently tried to assign a `DirectLabelColormap` to the existing `Image` layer.

## Symptoms

- `python main.py` succeeds; clicking **Load** on an affected `.h5` (e.g. `UT_WT_1B-Dcp2_Split_Halo_Sensor.h5`, `As_WT_1B-Dcp2_Split_Halo_Sensor.h5`) raises:

  ```
  TypeError: DirectLabelColormap can only be used with int
  ```

- Traceback (abbreviated):

  ```
  src/percell4/interfaces/gui/main_window.py:901  _populate_viewer_from_store
  src/percell4/gui/viewer.py:337                  add_mask    layer.colormap = cmap
  napari/layers/intensity_mixin.py:93             colormap.setter
  napari/layers/intensity_mixin.py:97             _set_colormap
  napari/layers/image/image.py:552                _update_thumbnail
  napari/utils/colormaps/colormap.py:529          DirectLabelColormap.map
  ```

- The viewer is left partially populated: channels render, but loading stops at the first colliding mask. No actionable error message reaches the user.
- The same dataset fails on both Windows and macOS — initially reported as Windows-only.

## What Didn't Work

- **The Windows red herring.** First report came from Windows, so we briefly considered platform-specific causes (different napari version on Windows, layer-ordering differences, `Path` handling). Reproducing the exact crash on macOS with the same dataset killed that line — the bug is dataset/codepath dependent, not OS dependent. (session history)
- **Reading `if name in self.viewer.layers` as a Labels-layer check.** The conditional looked benign in isolation. Until we traced `_populate_viewer_from_store`'s call order (`add_image` for every channel first, then `add_mask` for every mask), we didn't see that the same-name layer could be an `Image` because a user-named phasor ROI had been written to `/masks/<channel-name>`.

## Solution

Two layers — code fix is durable, data cleanup is one-off for already-corrupted datasets.

### Code fix (`src/percell4/gui/viewer.py`)

**Before** — silently refresh in place; assume Labels:

```python
if name in self.viewer.layers:
    layer = self.viewer.layers[name]
    layer.data = data
    layer.colormap = cmap                # ← crashes when layer is an Image
    layer.blending = blending
    layer.metadata[PERCELL_TYPE_KEY] = LAYER_TYPE_MASK
else:
    ...
    self.viewer.add_labels(data, name=name, colormap=cmap, ...)
```

**After** — hard-block any same-name collision; always create fresh otherwise:

```python
if name in self.viewer.layers:
    existing = self.viewer.layers[name]
    from qtpy.QtWidgets import QMessageBox

    QMessageBox.warning(
        self._qt_window,
        "Mask name conflict",
        f"Can't add mask {name!r}: a "
        f"{type(existing).__name__} layer with that name already "
        f"exists. Rename the new mask or remove the existing "
        f"layer before retrying.",
    )
    return

# ... no collision: always create a fresh Labels layer ...
self.viewer.add_labels(data, name=name, colormap=cmap, ...)
```

### Regression tests (`tests/test_gui_workflows/test_viewer_add_mask_collision.py`)

Three cases, all passing:

- `test_add_mask_blocks_when_image_layer_with_same_name_exists` — the original bug.
- `test_add_mask_blocks_when_labels_layer_with_same_name_exists` — same-type collision still surfaces.
- `test_add_mask_creates_labels_when_no_collision` — happy path.

The full add_mask-consumer suite (64 tests) continues to pass.

### Data cleanup (one-off, for already-corrupted datasets)

```python
import h5py
for p in [
    "/Volumes/<lab-server>/<datasets>/<dataset-1>.h5",
    "/Volumes/<lab-server>/<datasets>/<dataset-2>.h5",
]:
    with h5py.File(p, "a") as f:
        del f["masks/CA-SiR"]   # collided with channel CA-SiR
```

Verified safe before deletion: only `/decay/CA-SiR.channel` and `/phasor/CA-SiR/*.channel` attrs reference the name `"CA-SiR"`, and those reference the channel, not the mask. Both files had a properly-named sibling `/masks/CA-SiR_mask`; the stray `/masks/CA-SiR` was the corrupted entry from a misnamed phasor ROI. (session history)

## Why This Works

napari's layer namespace is **flat across types** — `Image`, `Labels`, `Shapes`, etc. all live in a single `viewer.layers` list keyed by name. The old idempotent-refresh path used `if name in self.viewer.layers` as a proxy for "this is a Labels layer I created earlier and can safely update," but that's a type-unsafe shortcut: the same-name layer can be of any type, from any source.

When the pre-existing layer was an `Image`, assigning a `DirectLabelColormap` (which only maps integer label arrays) tripped napari's thumbnail update three frames removed from the call site. The user got a `TypeError` with no path back to "you named a phasor ROI the same as a channel".

Hard-blocking on any same-name layer surfaces the collision as a **naming conflict** at the exact creation site, with an actionable message. Always creating a fresh `Labels` layer in the non-collision case removes the type-assumption footgun entirely. The "lost" idempotent refresh affordance was illusory: callers that legitimately want to update mask data should hold the layer reference, not rely on name-based rediscovery.

## Prevention

- **Test new code paths against viewers that already contain same-name layers of *different* types**, not just empty viewers. Empty-viewer tests never caught this; channels-then-masks ordering only manifests once channels are loaded first.
- **In a flat namespace with heterogeneous types, `name in container` is not a substitute for type checking.** Either narrow with `isinstance(container[name], ExpectedType)` or refuse to operate on collisions at all. The strict name+type matcher in `src/percell4/gui/viewer.py:_find_layer_by_name_and_type` (used by the session → napari one-way push) is the precedent worth borrowing.
- **Surface name collisions at creation time** with a clear message, rather than letting the operation crash deep inside third-party render code.
- **Upstream improvement worth doing (not in this fix):** phasor-ROI naming prompts should pre-check candidate names against `channel_names` / `list_labels` / `list_masks` and reject collisions before the user commits. The defensive `add_mask` block is the last line of defense; a name-validation rule at the prompt level would mean users never reach it. The intended naming convention (visible in the unaffected sibling `CA-SiR_mask`) is `<channel>_mask`, not bare `<channel>`. (session history)
- **Convention reminder:** `_populate_viewer_from_store` (`main_window.py:853`) iterates intensity channels first via `add_image`, then label sets, then masks via `add_mask`. Whichever layer type "wins" the namespace is determined by this order — useful to remember when adding new layer-creation paths.

## Related Issues

- [`napari-mask-layer-misclassified-as-segmentation.md`](./napari-mask-layer-misclassified-as-segmentation.md) — earlier `add_mask` correctness work that established the in-place refresh pattern and `PERCELL_TYPE_KEY` tagging. The "Adding a New Layer Type" checklist in that doc predates this collision class and may need a refresh.
- [`napari-direct-label-colormap-rendering-blocked-by-events.md`](./napari-direct-label-colormap-rendering-blocked-by-events.md) — sibling `DirectLabelColormap` failure (silent render stall), different root cause but same colormap class and same file.
- [`phasor-roi-preview-layer-ownership-2026-05-03.md`](./phasor-roi-preview-layer-ownership-2026-05-03.md) — where colliding phasor-ROI names originate; the `_phasor_roi_preview_<name>` underscore prefix protects the preview layer but not the eventual saved mask name.
- [`phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md`](./phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md) — the "Apply Visible as Mask" Action that ultimately calls `add_mask`.
- [`../architecture-patterns/session-to-napari-one-way-push.md`](../architecture-patterns/session-to-napari-one-way-push.md) — `_find_layer_by_name_and_type` strict-match precedent used on the read path.
- Commit `1fa2bf2` — "fix(viewer): block add_mask on any same-name layer collision".
