---
title: Phasor ROI preview layers must be per-resource, not a single shared layer
module: phasor-plot
date: 2026-05-03
problem_type: ui_bug
component: frontend_stimulus
severity: medium
category: docs/solutions/ui-bugs/
symptoms:
  - "`_phasor_roi_preview` napari Labels layer survives after the last ROI is removed from the phasor window, leaving stale per-cell colored pixels"
  - "ROI list per-row visibility checkbox renders as `[✓]`/`[✗]` glyph text that looks clickable but is inert"
  - "Toggling all ROIs invisible leaves the previous combined preview frozen on the napari canvas"
root_cause: logic_error
resolution_type: code_fix
related_components:
  - main_window
  - napari_viewer
  - cell_data_model
tags:
  - phasor
  - napari
  - qt-signals
  - preview-layer
  - roi
  - signal-granularity
  - launcher-mediator
  - single-source-of-truth
---

# Phasor ROI preview layers must be per-resource, not a single shared layer

## Problem

Removing the last ROI from the phasor window — or toggling all ROIs invisible — left the `_phasor_roi_preview` napari Labels layer alive with stale colored cells, even though the phasor window's ROI list was empty. Separately, the per-row visibility checkbox in the ROI list rendered as glyph text but was inert; visibility was only togglable via the panel "Visible" checkbox after first selecting the row.

## Symptoms

- Phasor window's ROI list is empty (or all rows hidden) but napari still shows the labeled cells.
- Clicking the `[✓]` / `[✗]` glyph in a ROI list row does nothing.
- Renaming an ROI does not move/rename its napari layer.
- Reopening the phasor window does not refresh napari from the current ROI state.

## What Didn't Work

**Prior fix attempt — U8 from the GUI state-handling audit (session history, 2026-05-01):** A previous pass scoped to "Bug A — Phasor Remove must not corrupt mask state" deleted an off-label `session.set_active_mask(None)` call inside `_on_remove_roi` and routed status-bar reset through `_refresh_histogram` directly. That plan explicitly noted: "`_update_preview` early-returns when `not self._roi_widgets`, so it's not the hook point — `_refresh_histogram` is." The decision left the signal-emission gap intact. Its regression test (`test_phasor_remove_roi.py`) asserted `preview_mask_ready` emission with the post-removal ROI list, but the fixture had no real napari viewer, so actual layer survival was never asserted — and the bug shipped. (session history)

**Considered and rejected this round — minimal patch to drop the empty-list guard:**

```python
def _update_preview(self) -> None:
    if self._g_map is None:  # was: ... or not self._roi_widgets
        return
```

Rejected for three reasons:
1. The single-shared-layer architecture (`preview_mask_ready(mask, colormap)`) packs N ROIs into one Labels layer via a `DirectLabelColormap` with N entries. To "remove ROI X" the launcher would have to diff label sets between successive emissions to figure out which color slots to drop — fragile and order-dependent.
2. It does not address the visibility-checkbox bug at all.
3. It leaves `_compute_combined_mask`, `_colormap_dirty`, `_preview_colormap`, and the multi-entry `DirectLabelColormap` machinery in place — indirection that exists only because of the shared-layer choice.

## Solution

**Replace the coarse signal with three per-ROI signals.** One napari layer per phasor ROI; each layer's lifecycle matches one ROI's lifecycle.

```python
# PhasorPlotWindow
preview_roi_upserted = Signal(str, object, str, bool)  # name, mask, hex, visible
preview_roi_removed = Signal(str)                       # name
preview_all_cleared = Signal()
```

Remove emits immediately, with no debounce-then-early-return ambiguity:

```python
def _on_remove_roi(self) -> None:
    if self._selected_roi_index is None or not self._roi_widgets:
        return
    widget = self._roi_widgets.pop(self._selected_roi_index)
    removed_name = widget.phasor_roi.name
    # ... renumber labels, refresh list ...
    self.preview_roi_removed.emit(removed_name)
    self._preview_timer.start()
```

`_on_dataset_changed`, `_on_load_rois`, and `closeEvent` emit `preview_all_cleared`. A new `showEvent` re-triggers preview when the window is reopened. Rename routes through `_on_name_edited` emitting `preview_roi_removed(old_name)` followed by an upsert under the new name on the next debounce tick.

**Launcher mediates napari mutations** — peer-views never touch layers directly:

```python
_PHASOR_PREVIEW_PREFIX = "_phasor_roi_preview_"

def _on_phasor_preview_upserted(self, roi_name, binary_mask, hex_color, visible):
    layer_name = f"{self._PHASOR_PREVIEW_PREFIX}{roi_name}"
    color_dict = {0: "transparent", 1: hex_color, None: "transparent"}
    layers = viewer_win._viewer.layers
    if layer_name in layers:
        layer = layers[layer_name]
        layer.data = binary_mask
        layer.colormap = DirectLabelColormap(color_dict=color_dict)
        layer.visible = visible
    else:
        viewer_win._viewer.add_labels(binary_mask, name=layer_name, ...)
```

`preview_all_cleared` sweeps every layer prefixed `_phasor_roi_preview_`. Apply-as-Mask removes only the preview layers for the visible ROIs that got applied.

**Checkbox fix** — real Qt checkboxes, not glyph text:

```python
def _refresh_roi_list(self) -> None:
    self._roi_list.blockSignals(True)
    self._roi_list.clear()
    for w in self._roi_widgets:
        item = QListWidgetItem(w.phasor_roi.name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if w.phasor_roi.visible else Qt.Unchecked)
        self._roi_list.addItem(item)
    self._roi_list.blockSignals(False)
```

Both the panel checkbox and the list-row checkbox route through one `_set_roi_visibility(index, checked)` helper that mirrors into the sibling widget under `blockSignals` to prevent ping-pong.

Deletes: `_compute_combined_mask`, `_colormap_dirty`, `_preview_colormap`, the N-entry `DirectLabelColormap` packing.

## Why This Works

- **Granular signals give precise removal targets.** `preview_roi_removed(name)` is an unambiguous "drop this layer" message — the launcher needs no diffing, no state reconstruction, no inference from "an emission with fewer label values than last time." The early-return-on-empty class of bug cannot exist because remove is its own message that fires regardless of how many ROIs remain.
- **One napari layer per ROI matches lifecycles.** Visibility now flips `layer.visible` rather than rebuilding a multi-entry colormap, so opacity tweaks survive toggles.
- **Checkbox fix is a pure widget-flag oversight.** `Qt.ItemIsUserCheckable` + `setCheckState` is what makes a list item interactive; glyph text in the label is not. Routing both affordances through `_set_roi_visibility` removes the divergence risk that two parallel writers would create.

## Prevention

- **Qt signals that drive external side effects must not silently no-op when the source becomes empty.** If the receiver needs to be told "there are now zero things," either keep firing with an empty payload or model teardown as its own signal. Early-return shape `if not self._items: return` inside a debounced slot breaks teardown contracts.
  - *Test guardrail:* add an ROI, remove it, process Qt events, and assert no napari layer with the preview prefix remains. Tests that lack a real napari viewer must NOT be the only coverage for cross-window cleanup contracts — assertion-by-signal-emission misses the actual side effect. (session history: U8's regression test made exactly this mistake)
- **Prefer per-resource signals (upsert/remove/clear) over coarse "rebuild from this aggregate payload" signals when the receiver manages discrete resources.** Coarse signals force the receiver to diff to discover removals, which is order-dependent and fragile.
- **`QListWidgetItem` checkboxes require `Qt.ItemIsUserCheckable` flag plus `setCheckState`.** Static `[✓]`/`[✗]` glyphs in item text look clickable but aren't.
  - *Test guardrail:* widget test that checks `item.flags() & Qt.ItemIsUserCheckable` on each row.
- **When two UI affordances write the same field, route both through one `_set_state(index, value)` helper that mirrors into the sibling widget under `blockSignals`.** Eliminates ping-pong and divergence between "selected row state" and "all rows state."
- **The "windows never talk to each other directly" rule extends to peer-view → launcher → napari.** Peer-views (like `PhasorPlotWindow`) emit Qt signals; the launcher owns all napari mutations. Peer-views must never reach into `viewer._viewer.layers` themselves.

## Related Issues

- `docs/solutions/architecture-patterns/session-to-napari-one-way-push.md` — codifies one-way session → napari push and forbids napari → session for layer-list events. This doc operationalizes the inverse direction (peer window → launcher → napari) for non-session-tracked overlay layers.
- `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md` — the Selector / Creator / Action taxonomy. "Remove ROI" is an Action that *failed to* clean up its side artifact; this doc reinforces "what an Action must do," whereas that one reinforces "what an Action must NOT do."
- `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md` — establishes "dedicated overlay layer for transient visualization, never mutate primary layer." Same overlay-ownership theme; this doc extends it to *per-resource* overlay layers.
- `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md` — covers the canonical `_phasor_roi_preview` name and the layer metadata tagging that distinguishes mask vs segmentation. Verify the new `_phasor_roi_preview_<name>` naming does not reintroduce dropdown-pollution risks that doc warned about.
- `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md` — uses a single bridged signal pattern. Per-ROI signal proliferation here is justified because the receiver manages discrete napari layers, not a single bridged session field.
