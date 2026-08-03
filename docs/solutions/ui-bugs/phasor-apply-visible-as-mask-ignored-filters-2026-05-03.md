---
title: Phasor "Apply Visible as Mask" must AND the ROI with the visible-pixel pipeline
module: phasor-plot
date: 2026-05-03
problem_type: ui_bug
component: frontend_stimulus
severity: high
category: docs/solutions/ui-bugs/
canonical_source: src/percell4/interfaces/gui/peer_views/phasor_plot.py
applies_to:
  - "src/percell4/interfaces/gui/peer_views/phasor_plot.py"
symptoms:
  - "'Apply Visible as Mask' saved an .h5 mask covering the full ROI region, ignoring filter_ids, active mask, intensity threshold, and reference circle"
  - "Saved mask did not match the napari preview layer for the same ROI"
  - "Saved mask did not match the visible-pixel histogram driving the phasor view"
  - "Downstream per-cell measurements ran against the wrong pixel set"
root_cause: logic_error
resolution_type: code_fix
related_components:
  - napari_viewer
  - main_window
tags:
  - phasor
  - flim
  - mask-export
  - single-source-of-truth
  - derived-state
  - wysiwyg
  - regression-test
  - action-contract
---

# Phasor "Apply Visible as Mask" must AND the ROI with the visible-pixel pipeline

## Problem

The phasor plot's `Apply Visible as Mask` button saved a binary mask that ignored every FLIM-tab filter the visible histogram applies (active mask, `filter_ids`, intensity threshold, reference circle). The saved `Type_*` mask covered the full ROI footprint instead of the intersection of the ROI with the currently visible pixels — so the saved artifact disagreed with both the napari preview and the histogram the user had been looking at when they clicked the button. Downstream per-cell measurements then ran against the wrong pixel set.

## Symptoms

- With `Filter by active mask` enabled and an ROI placed on the (filtered) histogram, the napari preview layer correctly showed sparse green pixels constrained to the active mask
- After clicking `Apply Visible as Mask`, the new `Type_1+2` napari layer covered a much larger area — the full ROI region, not the active-mask intersection
- Saved binary in `/masks/Type_*` was a strict superset of the preview the user just confirmed
- Auto-selecting the new mask as active redrew the histogram with the unfiltered ROI mask as the filter, masking the regression visually

## What Didn't Work

This was a one-shot diagnosis with no failed prior attempts. The systemic gap that allowed the bug to land undetected: 125 passing tests across the phasor and FLIM suites covered `_refresh_histogram` and `_compute_filtered_binary` independently, but **zero tests** exercised `_on_apply_mask` or the `mask_applied` signal payload. Independent per-path tests cannot detect cross-path drift — only a structural equality test between paths can.

Closely related prior work that did not address this specific divergence (session history):

- A previous bug in the same `_on_apply_mask` handler (documented in [`logic-errors/phasor-roi-to-mask-api-mismatch.md`](../logic-errors/phasor-roi-to-mask-api-mismatch.md)) was a function-signature mismatch — `phasor_roi_to_mask` was being called with a `PhasorROI` object instead of kwargs. That fix corrected the call site but did not change the filter coverage of the save path.
- The previous-day preview-signal redesign (commit `ac9a20e`, documented in [`phasor-roi-preview-layer-ownership-2026-05-03.md`](./phasor-roi-preview-layer-ownership-2026-05-03.md)) split a single `preview_mask_ready` signal into `preview_roi_upserted`/`preview_roi_removed`/`preview_all_cleared`. That change touched the preview pipeline propagating `w.cached_mask` to napari, but `_on_apply_mask` was reading the same `cached_mask` directly without applying any filters on top — the redesign did not surface the divergence.

## Solution

Three code paths derived "visible valid pixels" with different filter sets:

| Path | Method | Filters applied |
|---|---|---|
| Histogram | `_refresh_histogram` | validity + filter_ids + active mask + intensity threshold + ref circle |
| Preview | `_compute_filtered_binary` | filter_ids + active mask only |
| Apply (save) | `_on_apply_mask` | none |

The fix introduced `_compute_visible_valid_2d()` ([`phasor_plot.py:1190`](../../../src/percell4/interfaces/gui/peer_views/phasor_plot.py)) — a pure helper that wraps `compute_valid_phasor_pixels` (in `domain/flim/phasor_display.py`) with the full 5-filter chain and returns a 2D boolean mask aligned with the active G/S maps:

```python
def _compute_visible_valid_2d(self) -> np.ndarray | None:
    if self._g_map is None or self._s_map is None:
        return None
    g, s = self._get_active_gs_maps()
    valid_flat = compute_valid_phasor_pixels(
        g.ravel(), s.ravel(),
        labels_flat=self._labels_flat,
        filter_ids=self._session.filter_ids,
        mask_flat=self._load_active_mask_flat(),
        intensity_flat=(self._intensity.ravel() if self._intensity is not None else None),
        intensity_threshold=self._intensity_threshold,
        ref_circle_center=self._ref_circle_center,
        ref_circle_radius=self._ref_circle_radius,
    )
    return valid_flat.reshape(g.shape)
```

`_compute_filtered_binary` (preview) now ANDs the cached ROI membership with that helper, and `_on_apply_mask` (save) routes through `_compute_filtered_binary` for every visible ROI:

```python
def _compute_filtered_binary(self, widget: _ROIWidget) -> np.ndarray:
    g, s = self._get_active_gs_maps()
    if widget.cached_mask is None:
        widget.cached_mask = phasor_roi_to_mask(g, s, ...)
    visible = self._compute_visible_valid_2d()
    binary = np.zeros(g.shape, dtype=np.uint8)
    keep = widget.cached_mask & visible if visible is not None else widget.cached_mask
    binary[keep] = 1
    return binary

def _on_apply_mask(self) -> None:
    ...
    for w in self._roi_widgets:
        if not w.phasor_roi.visible:
            continue
        binary = self._compute_filtered_binary(w)
        roi_masks.append((w.phasor_roi.name, binary, w.phasor_roi.color))
    self.mask_applied.emit(roi_masks)
```

Commit: `a368f1a`.

## Why This Works

The bug was structural: three call sites independently rebuilt the same conceptual operation ("which pixels are visible right now?") and naturally drifted as filters were added over time. Centralizing the visibility computation in `_compute_visible_valid_2d` collapses the three derivations into one source of truth — the histogram, the napari preview, and the saved mask are now provably equal pixel-for-pixel by construction. Any future filter added to the histogram path automatically propagates to the preview and apply paths through the shared helper; drift becomes impossible without editing the one helper.

This also closes the `Action`-contract loop established in [`architecture-patterns/gui-action-contract-exhaustiveness.md`](../architecture-patterns/gui-action-contract-exhaustiveness.md): an `Apply Visible` button labelled "apply what is visible" must structurally apply what is visible. Before the fix, the contract was satisfied only coincidentally (when no filters were active); now it is satisfied by construction.

## Prevention

- **Structural equality test as the load-bearing contract**: `test_apply_equals_napari_preview` asserts the `mask_applied` payload equals the `preview_roi_upserted` payload pixel-for-pixel. Any new filter must show up in both payloads or this test fails.
- **Per-filter regression coverage** in `tests/test_gui_workflows/test_phasor_apply_visible_as_mask.py`:
  - `test_apply_respects_active_mask_filter` — saved binary is a subset of the active mask
  - `test_apply_respects_filter_ids` — no nonzero pixels on excluded labels
  - `test_apply_respects_intensity_threshold` — sub-threshold pixels are 0
  - `test_apply_respects_reference_circle` — pixels outside the circle are 0
  - `test_apply_with_no_filters_equals_raw_roi` — pin the no-filter baseline
- **Pattern**: when N code paths must derive from the same logical view (here: "visible valid pixels"), share one pure helper rather than rebuilding the chain at each call site. Independent per-path tests will not catch cross-path drift — write a structural equality test instead.
- **Triggers to look for in code review**: any new "save what you see" or "export current view" action; any new filter checkbox added to a plot. Both are cases where the save/export path must call the same predicate function as the render path.

## Related

- [`logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`](../logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md) — primary parent. Vector 4 invalidates the active-mask cache on the producer side; this fix completes the consumer-side centralization for the same `Filter by active mask` pipeline.
- [`architecture-patterns/gui-action-contract-exhaustiveness.md`](../architecture-patterns/gui-action-contract-exhaustiveness.md) — pattern citation. Fresh canonical example: an `Action` must structurally do what its label says.
- [`logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`](../logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md) — conceptual sibling. Same "derive from one source rather than letting consumers reimplement the chain" lesson at a different layer.
- [`logic-errors/phasor-roi-to-mask-api-mismatch.md`](../logic-errors/phasor-roi-to-mask-api-mismatch.md) — historical predecessor in the same `_on_apply_mask` handler.
- [`ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md`](./phasor-roi-preview-layer-ownership-2026-05-03.md) — same-day companion in the phasor preview pipeline.
