---
title: "feat: Interactive CNR histogram segmenter (manual divider-based subpopulations)"
type: feat
status: active
date: 2026-06-23
---

# feat: Interactive CNR histogram segmenter

## Overview

A pop-up interactive module for direct, manual CNR-based segmentation of an
extracted feature mask. A pyqtgraph **histogram of per-focus CNR** opens with
draggable vertical **divider** lines; the user can add/remove any number of
dividers to partition the CNR axis into N+1 segments. A **live multi-value labels
overlay** in napari recolors as the dividers move, so the user sees the
segmentation update in real time. On **Save**, each CNR segment is written as its
own binary mask (`<base>_seg1 … <base>_segN`) via the existing Creator path.

This is a sibling to the existing automatic "Classify Mask by CNR" (discover /
guided / forced) — both stay; the auto one is for screening, this one for direct
control. Both reuse `measure_cnr`.

---

## Problem Frame

The automatic classifier decides *whether* and *where* to split by a gap test.
Often the user knows their biology and wants to place the CNR boundaries
themselves and see the result immediately. A histogram with draggable dividers
and a live napari preview gives that direct control with no statistical
assumptions.

---

## Requirements Trace

- R1. Open a pop-up window with a histogram of the source mask's per-focus CNR.
- R2. Draggable vertical dividers; **add** any number, **remove** them; N dividers
  → N+1 segments.
- R3. A live napari labels overlay (0=bg, 1..N+1 by CNR segment) updates as
  dividers move (debounced).
- R4. **Save** writes one `{0,1}` binary mask per non-empty segment
  (`<base>_seg{i}`) via the Creator path; closes the window and removes the
  preview.
- R5. Launched from a new "Segment by CNR (interactive)" button on the
  AdaptiveClipPanel, alongside the existing auto classifier.
- R6. Prerequisites: active channel + active segmentation + single-frame + a
  selected source mask (read from the store); abort cleanly otherwise. Heavy CNR
  measurement runs off the UI thread.

---

## Scope Boundaries

- **Not** changing or removing the automatic CNR classifier (kept alongside).
- **No** persisted multi-value labels image or per-focus table on Save (the
  labels overlay is preview-only; output is per-segment binary masks — explicit
  user decision).
- **Linear CNR x-axis** only (the literal "histogram of the CNRs"); a log-axis
  toggle is a possible follow-up, not in scope.
- Single-frame only (per-cell σ); time-lapse out of scope (mirrors the per-cell
  modes).

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/gui/threshold_qc.py` — the canonical interactive pattern: a
  separate window with a `pg.PlotWidget` + `pg.BarGraphItem` histogram, theme
  styling, a live `viewer.add_labels` preview, and a debounced
  `QTimer.singleShot(50, self._update_preview)`. Mirror its window/preview shape.
- `src/percell4/domain/measure/cnr_classification.py` — `measure_cnr` (per-focus
  records incl. component `label` + `cnr`); add the pure segment helpers here.
- `src/percell4/gui/adaptive_clip_panel.py` — `_on_classify` pre-flight
  (channel/seg/single-frame/source-mask via `store.read_mask`), `Worker` dispatch,
  `AcceptPunctaMask` + `viewer.add_mask` Creator save; `prompt_for_resource_name`.
- `src/percell4/gui/viewer.py` — `add_labels(data, name, colormap=…)` (multi-value
  overlay with a `DirectLabelColormap`), `add_mask` (binary).
- pyqtgraph `InfiniteLine(movable=True, angle=90)` is the draggable divider;
  `sigPositionChanged` (debounced live update) + `sigPositionChangeFinished`.

### Institutional Learnings

- `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md`
  — per-segment masks each run the Creator four-step (store→add→refresh→select).
- Worker rule: numpy-only off-thread; store/napari on the main thread.
- Reuse `per_cell_sigma`/`measure_cnr`; do not recompute CNR a second way.

---

## Key Technical Decisions

- **Pure segment math in the domain** (`cnr_classification.py`): `assign_segments`
  (np.digitize), `segment_label_image` (scatter focus→component→segment, one
  fancy-index over the component-label image — fast enough for live drags), and
  `segment_masks_from_label_image`. Unit-tested without Qt.
- **Live preview = a single multi-value labels layer**, updated in place
  (`layer.data = seg_img`) and recolored via a generated `DirectLabelColormap`
  (palette from a matplotlib colormap). Debounced like `threshold_qc`.
- **Component-label image computed once** in a worker alongside `measure_cnr`
  (same `scipy.ndimage.label` call) so focus `label` ids match the image, then
  reused for every live update (only the per-component→segment LUT changes).
- **Save = per-segment binary masks only** via `AcceptPunctaMask` (no labels
  image, no table — user decision). Skip empty segments. Hard-block name
  collisions through the existing prompt + `existing_names`.
- **Off-thread measure, then open the window** in the finished handler (mirrors
  the classify dispatch); the window itself does only fast LUT/index work.
- **New divider placement** = the midpoint of the widest current segment;
  **remove** drops the last divider. Zero dividers → one segment (all foci).

---

## Open Questions

### Resolved During Planning
- Launch → new button alongside the auto classifier.
- Save output → per-segment binary masks only.
- Axis → linear CNR.
- Preview representation → in-place multi-value labels layer.

### Deferred to Implementation
- Exact preview layer name + palette colormap choice.
- Debounce interval (start at 50 ms, matching `threshold_qc`).
- Initial divider count/position (default: one divider at the CNR median).

---

## Implementation Units

- U1. **Pure segment helpers in `cnr_classification.py`**

**Goal:** Testable CNR→segment math shared by the live preview and the save.

**Requirements:** R2, R3, R4

**Dependencies:** None

**Files:**
- Modify: `src/percell4/domain/measure/cnr_classification.py`
- Modify: `tests/test_measure/test_cnr_classification.py`

**Approach:**
- `assign_segments(cnr, dividers) -> int array` — `np.digitize(cnr,
  sort(dividers)) + 1` → 1..(len(dividers)+1). Empty dividers → all 1.
- `segment_label_image(component_labels, focus_labels, focus_cnr, dividers) ->
  int32 image` — scatter each valid focus's segment into a
  `seg_of_component[component_labels]` LUT index; foci absent/invalid → 0.
- `segment_masks_from_label_image(seg_img, n_segments) -> list[np.ndarray]` —
  `(seg_img == i)` uint8 for i in 1..N.

**Test scenarios:**
- `assign_segments`: 2 dividers → values map to segments 1/2/3 at the right
  boundaries; empty dividers → all 1; unsorted dividers handled.
- `segment_label_image`: a known component-label image + focus CNRs + 1 divider →
  correct 0/1/2 image; invalid-CNR focus → 0.
- `segment_masks_from_label_image`: N segments → N binary masks; empty segment →
  all-zero mask.

**Verification:** helper tests pass; live update can be expressed as one
`assign_segments` + one `segment_label_image` call.

---

- U2. **`CnrSegmenterWindow` interactive GUI**

**Goal:** The histogram + dividers + live preview + save window.

**Requirements:** R1, R2, R3, R4

**Dependencies:** U1

**Files:**
- Create: `src/percell4/gui/cnr_segmenter.py`
- Create: `tests/test_gui/test_cnr_segmenter.py`

**Approach:**
- `CnrSegmenterWindow(QWidget)` (or `QMainWindow`) taking `records`,
  `component_labels`, `channel_name`, `get_viewer_window`, `get_store`,
  `get_repo`, `session`. Filters to valid foci (finite CNR > 0); stores
  `cnr_arr` + `focus_label_arr`.
- pyqtgraph `PlotWidget` + `BarGraphItem` histogram (linear CNR, theme-styled);
  one initial `InfiniteLine(movable=True)` at the CNR median.
- Buttons: **Add divider** (midpoint of widest segment), **Remove divider** (drop
  last), **Save segments**, **Close**. A status label shows segment counts.
- `_update_preview()` (debounced via `QTimer.singleShot`): `seg =
  assign_segments(...)`; `seg_img = segment_label_image(...)`; create/update one
  labels layer (`layer.data = seg_img`) with a generated `DirectLabelColormap`
  (rebuild colormap when N changes). Connect each line's `sigPositionChanged` →
  schedule; `sigPositionChangeFinished` → immediate.
- `_on_save()`: prompt base name (`existing_names = store.list_masks()`); for each
  non-empty segment write `<base>_seg{i}` via `AcceptPunctaMask` + `add_mask`;
  remove the preview layer; close.
- `closeEvent`: remove the preview layer.

**Patterns to follow:** `threshold_qc.py` window/preview/debounce; `_on_classify_done`
Creator save loop; `viewer.add_labels`/`add_mask`.

**Test scenarios:**
- Init builds the histogram and one divider; the preview layer is added with a
  0/1/2 image (one divider → 2 segments).
- Add divider → segment count +1 and the preview relabels; remove → −1; below 0
  dividers floors at one segment.
- Moving a divider updates the preview image (segment assignment changes).
- Save writes one binary mask per non-empty segment (`_seg1.._segN`), each
  Creator-selected, preview removed, window closed.
- Save skips an empty segment.

**Verification:** window tests pass under the offscreen-Qt harness with a fake
viewer/store/repo.

---

- U3. **Panel wiring: button + off-thread measure + open window**

**Goal:** Launch the segmenter from the panel after measuring CNR off-thread.

**Requirements:** R5, R6

**Dependencies:** U2

**Files:**
- Modify: `src/percell4/gui/adaptive_clip_panel.py`
- Modify: `tests/test_gui/test_adaptive_clip_panel.py`

**Approach:**
- `run_cnr_measure(image, feature_mask, labels) -> (records, component_labels)`
  worker body (calls `measure_cnr` + `scipy.ndimage.label`).
- "Segment by CNR (interactive)" button under the existing classify button,
  sharing the `CnrClassifySettingsWidget` source-mask selector (its `mode` is
  irrelevant here — only the source mask is used).
- `_on_segment_cnr`: same pre-flight as `_on_classify` (channel/seg/single-frame/
  source mask via `store.read_mask`); dispatch `run_cnr_measure` in a `Worker`;
  on finished, open `CnrSegmenterWindow` (held on `self._cnr_segmenter`) and
  surface "no foci" cleanly; on error, status + re-enable.
- Hold the window reference so it isn't garbage-collected.

**Patterns to follow:** `_on_classify` pre-flight + worker dispatch; the
`self._worker`/reference-holding convention.

**Test scenarios:**
- Pre-flight: no segmentation / time-lapse / no source mask → status, no window.
- Reads the source mask via `store.read_mask` and dispatches `run_cnr_measure`.
- On finished with foci → a `CnrSegmenterWindow` is created/held; with no valid
  foci → status, no window.

**Verification:** panel tests pass; clicking the button opens the segmenter for a
valid mask.

---

## System-Wide Impact

- **Interaction graph:** one new button + worker + window; a transient preview
  labels layer in napari (removed on save/close). Per-segment masks fire the
  Creator four-step each. No session-selection or existing-mode changes.
- **Error propagation:** pre-flight aborts before any worker; measure errors via
  the worker error signal; save failures surfaced, controls restored.
- **State lifecycle:** the preview layer must be removed on both Save and Close
  (and not persisted). N masks = N Creator sequences; the last `set_active_mask`
  wins.
- **Unchanged invariants:** the auto classifier, the `{0,1}` mask contract, and
  the existing panel modes.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Live relabel lag on large images | Precompute the component-label image once; each update is one LUT scatter + one fancy-index; debounce at 50 ms. |
| Preview layer left behind on close | Remove it in both `_on_save` and `closeEvent`. |
| Many foci make `measure_cnr` slow | Run it off-thread in a `Worker`; open the window only on finished. |
| Segment-name collisions | `prompt_for_resource_name` + `existing_names`; skip empty segments. |
| pyqtgraph absent (headless CI) | Guard the import like `threshold_qc`; the window is GUI-only (tests use the offscreen Qt harness that already loads pyqtgraph). |

---

## Sources & References

- Sibling plan: [docs/plans/2026-06-23-001-feat-cnr-subpopulation-classification-plan.md](docs/plans/2026-06-23-001-feat-cnr-subpopulation-classification-plan.md)
- Pattern: `src/percell4/gui/threshold_qc.py` (interactive histogram + live preview)
- Key code: `src/percell4/domain/measure/cnr_classification.py`,
  `src/percell4/gui/adaptive_clip_panel.py`, `src/percell4/gui/viewer.py`
