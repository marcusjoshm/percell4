---
title: Seg-QC recovery options for the single-cell thresholding workflow
type: feat
status: active
date: 2026-05-26
origin: docs/brainstorms/2026-05-26-seg-qc-recovery-options-requirements.md
---

# Seg-QC recovery options for the single-cell thresholding workflow

## Overview

Make the segmentation QC step in the single-cell thresholding workflow
a genuine recovery surface when Cellpose's initial pass is empty or
visibly wrong. Three new affordances inside the existing QC dock:

1. **Invert the empty-labels auto-skip** so the QC window opens for
   hand-drawing instead of bypassing the user.
2. **Re-run Cellpose** with locally edited knobs (diameter, channel,
   thresholds, model, min size) — always replacing the current
   in-QC labels.
3. **Modify Channel** in memory with a histogram + draggable
   clip-and-stretch handles and an ImageJ-style Saturation%, with a
   live napari preview. The modified image is fed to Cellpose Re-run
   when active; the on-disk `/intensity` is never touched.

The prerequisite — `segment_one` writing an empty `/labels/cellpose_qc`
instead of recording `SEGMENTATION_EMPTY` — is already implemented on
branch `fix/single-cell-workflow-tile-stitching` (uncommitted) and
must be either merged or carried forward as a prerequisite commit on
the new branch before this plan can deliver its end-to-end value.

---

## Problem Frame

Today, a Cellpose miss on any dataset in a batch wedges the workflow:
the runner used to record `SEGMENTATION_EMPTY` and skip the dataset
through every downstream phase; with the in-flight fix it now
persists empty labels, but `src/percell4/gui/workflows/single_cell/seg_qc.py:165-174`
still auto-skips the QC window when `labels.max() == 0`. Either way,
the researcher has no in-workflow recovery: they have to
preprocess in ImageJ, change the workflow config, and restart the
batch from scratch. Empirical evidence from the
`/Volumes/NX-01-A/2026-05-25_FRET_export` dataset (see origin doc):
Cellpose at diameter=300 on `mNG` finds 0 cells; the same channel
after an ImageJ "Apply LUT" with `hi≈1000` finds 19 ROIs.

This plan turns the QC step into the recovery surface so a Cellpose
miss is a routine, in-place correction rather than a batch-killer.

(see origin: `docs/brainstorms/2026-05-26-seg-qc-recovery-options-requirements.md`)

---

## Requirements Trace

- R1. Remove the auto-skip when `labels.max() == 0` in `seg_qc.py:165-174`.
- R2. Hand-draw / delete / cleanup tools work on an empty labels layer (verify, not re-implement).
- R3. New collapsible **Re-run Cellpose** group below the Cleanup group, collapsed by default.
- R4. Re-run group exposes diameter (px), segmentation channel, flow threshold, cellprob threshold, model name, and min cell size — seeded from workflow config, locally editable, scope-bounded to this QC session.
- R5. New collapsible **Modify Channel** group below Re-run, collapsed by default. Expanding activates the napari preview; collapsing reverts.
- R6. Modify Channel contains: histogram + draggable lo/hi handles, linked numeric readouts, Saturation% input (default 1.0), and an **Auto** button that recomputes `hi = percentile(channel, 100 − X)`, `lo = channel.min()`. Handles cannot cross.
- R7. Re-run **always replaces** the in-QC labels.
- R8. Re-run uses the Modify Channel transformation as Cellpose input when that group is active.
- R9. The clip-and-stretch transformation is in-memory only; `/intensity` is never mutated.

**Origin actors:** A1 (Microscopy researcher)
**Origin flows:** F1 (Recover empty-Cellpose by hand-drawing), F2 (Re-run with different settings), F3 (Modify channel + re-run)
**Origin acceptance examples:** AE1 (Covers R1, R2), AE2 (Covers R3, R4, R7), AE3 (Covers R5, R6, R9), AE4 (Covers R8), AE5 (Covers R5)

---

## Scope Boundaries

- Modify Channel state is per-dataset and per-QC-session. No global "remember last LUT" toggle in v1; switching datasets resets to identity.
- The LUT is never written to the .h5; no new metadata keys, no new `/intensity` variants.
- No merge mode for Re-run. Users wanting both Cellpose output and hand-drawn cells must draw on top after Re-run completes.
- No per-dataset "skip this dataset but continue the workflow" affordance. Cancel still cancels the whole run, as today.
- No new failure types or run-log events for in-QC re-run / modify activity. The runner only sees the final Accept / Cancel.
- Default Saturation% behavior is top-only (`hi = p99`, `lo = channel_min`) — bilateral (0.5% each tail) is explicitly deferred (see origin Deferred-to-Planning).
- No persistence of in-QC Cellpose tweaks back into `WorkflowConfig`. The workflow config is "the initial guess"; QC tweaks are local.

### Deferred to Follow-Up Work

- **Cellpose-in-QC worker pattern doc** (`/ce-compound`): once U2 lands, capture the QThread + Worker pattern for Cellpose inside a QC controller as a `docs/solutions/` entry — there is no codified pattern today (per learnings research). This is a follow-up doc, not in this plan's PR.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/gui/workflows/single_cell/seg_qc.py` — the target file. `SegmentationQCController` lifecycle: `start()` loads dataset + labels; `_build_window` lays out the QC dock; `_on_accept_clicked` / `_on_cancel_clicked` exit points; `_finish` is the single idempotent teardown.
- `src/percell4/gui/segmentation_panel.py:_on_run_cellpose` (around line 395) — canonical pattern for spawning Cellpose via `Worker(run_cellpose, image, model_type, diameter, gpu)`. Reuse the same `Worker` API; do **not** invent a new threading shape.
- `src/percell4/gui/workers.py:Worker(QThread)` — emits `finished` (return value) and `error` (`WorkerError`) signals. Already battle-tested.
- `src/percell4/adapters/cellpose.py:run_cellpose` — the pure function this plan calls from the Re-run worker. Accepts `model=cellpose_model` for hoisted-model reuse.
- `src/percell4/gui/threshold_qc.py` — canonical reference for the **Creator-contract four-step sequence** (store write → viewer layer add/update → resource list refresh → active resource set). The Re-run replace path must honor it.
- `src/percell4/interfaces/gui/peer_views/phasor_plot.py` — internal precedent for `pg.LinearRegionItem` + a pyqtgraph plot with handle interaction.
- Test pattern: `tests/test_gui_workflows/test_seg_qc_timelapse.py` — `qtbot` + `ViewerWindow` + `DatasetStore` tmp fixture pattern. Reuse verbatim.

### Institutional Learnings

- `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md` — canonical to `src/percell4/gui/**/*.py`. Re-run that writes to `/labels/cellpose_qc` must follow store-write → layer update → `refresh_resource_lists` → `set_active_segmentation`. Skipping the refresh step desyncs the SessionWindow's segmentation combo and the Layer Management drop-downs.
- `docs/solutions/logic-errors/grouped-thresholding-development-lessons.md` — three direct hits: pass `np.histogram` output (counts + edges) directly to pyqtgraph (do **not** compute bin midpoints); use `scipy.stats.mode` on float intensity, never `np.bincount`; QC controls live in a standalone `QMainWindow`, not a napari dock.
- `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md` — when assigning `layer.data = new_array` for the Modify Channel preview, do **not** wrap the mutation in any `events.*.blocker()`. Block context suppresses GPU texture upload and the layer goes blank.
- `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md` — for transient preview layers (Modify Channel's clipped/stretched image), use an underscore-prefixed name and remove on QC close. `QTimer.singleShot(0)` coalesced refresh; `_torn_down` flag early-returns from timer fires after teardown; `timer.stop()` synchronously in `_finish`.
- `docs/solutions/architecture-patterns/session-to-napari-one-way-push.md` — Re-run must not subscribe to napari layer-selection events to detect the new labels. The controller writes labels and pushes session changes after the worker returns, guarded by `_is_originator`. The Modify Channel channel-override combo is a Selector (read-only) — must not write back to `session.active_channel`.
- `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md` Pattern 2 — singleShot(0) coalescing for histogram drag updates.

### External References

None required. All patterns are codified in repo solutions and existing code.

---

## Key Technical Decisions

- **Histogram widget composition**: `pyqtgraph.PlotItem` + `pg.BarGraphItem` (counts vs bin centers) + `pg.LinearRegionItem(orientation="vertical", movable=True)` for the lo/hi handles. `LinearRegionItem` natively supports two-handle dragging, emits `sigRegionChanged` on every drag tick, and can be constrained programmatically. Two-way bind to the lo/hi numeric readouts via a `_setting_region` guard so the spinboxes setting region values don't re-emit region-changed back. Resolves the origin's Deferred-to-Planning Q1.
- **napari layer data refresh**: `layer.data = new_array` directly on the existing channel `Image` layer, **without** any `events.*.blocker()` context (per the colormap learning). The layer's `_keep_auto_range = False` to prevent contrast auto-rescaling on every preview update. Reverting on Modify-Channel-collapse: save the original `image.data` reference at expand-time and restore on collapse. Resolves Deferred-to-Planning Q2.
- **Re-run threading**: reuse `Worker(QThread)` from `src/percell4/gui/workers.py`; pattern mirrors `segmentation_panel._on_run_cellpose`. Disable the Re-run button while in flight; show a `QProgressDialog` with a Cancel button (which calls `worker.requestInterruption()` — Cellpose itself won't honor it, but the UI signals stay clean and the next worker fire is gated until this one returns). On `finished`, route through a `_torn_down` early-return then through the Creator four-step. Resolves Deferred-to-Planning Q3.
- **Saturation default**: top-only — `hi = percentile(channel, 99)`, `lo = channel.min()`. Bilateral is **out of v1** per origin scope. Resolves Deferred-to-Planning Q4.
- **Label survival across LUT revert**: the persisted labels layer is independent of the intensity layer; reverting the preview does not touch labels. Confirm via test scenario in U3 (drawn labels survive a Modify Channel collapse). Resolves Deferred-to-Planning Q5.
- **Re-run replaces, not merges**: clicking Re-run unconditionally overwrites `viewer.layers["cellpose_qc"].data` with the new Cellpose output. No prompt, no merge mode (per origin R7 and key decision).
- **Modify Channel preview surface**: the **existing** channel `Image` layer's `.data` is reassigned with the clipped/stretched array — no new transient overlay layer. The on-disk channel is untouched; we save `_original_channel_view` at group-expand time and restore on collapse / accept / cancel / next dataset. Underscore-prefixed transient layers (per the modal-tool overlay pattern) are still the right shape for *new* layers, but a Cellpose-input preview is naturally hosted by the existing channel layer.
- **Re-run image source when Modify Channel is active**: a single source-of-truth function `_cellpose_input_image()` reads the currently-displayed channel layer's `.data`. When Modify Channel is collapsed, it returns the raw channel; when active, it returns the clipped/stretched preview. This keeps R8's wiring trivial — Re-run never needs to know the LUT state.

---

## Open Questions

### Resolved During Planning

- *pyqtgraph composition for histogram+handles*: `BarGraphItem` + `LinearRegionItem` (see Key Technical Decisions).
- *napari layer refresh without zoom reset*: direct `layer.data = arr` assignment without event blockers (per the colormap-blocker solution doc).
- *Threading model for Re-run*: reuse `Worker(QThread)` from `gui/workers.py`, mirror `segmentation_panel._on_run_cellpose`.
- *Saturation default*: top-only (`hi = p99`, `lo = channel_min`).
- *Label survival across LUT revert*: labels are a separate layer; reverting the channel preview leaves them intact (no special handling needed; verified by test).

### Deferred to Implementation

- *Exact `QProgressDialog` styling for Re-run-in-flight*: theme integration with `gui/theme.py`. Apply existing dark-theme constants; final visual touch decided at implementation time.
- *Histogram bin count for the Modify Channel widget*: 100 bins is a sensible default; tune at implementation time if performance is an issue on time-lapse stacks (which use the segmentation-channel summary plane only, so volume is bounded).
- *Whether time-lapse should compute the histogram from the first frame, the mean projection, or all frames pooled*: defer to implementation. First-frame is cheapest and matches what the user is segmenting per-frame anyway.

---

## Implementation Units

- U1. **Invert the empty-labels auto-skip in seg_qc.py**

**Goal:** When `labels.max() == 0`, open the QC window with an empty labels layer instead of auto-completing the phase. Make hand-drawing the obvious recovery path.

**Requirements:** R1, R2

**Dependencies:** Prerequisite — the empty-Cellpose fix in `src/percell4/workflows/phases.py:segment_one` from branch `fix/single-cell-workflow-tile-stitching` must be on this branch's base. Without it, `/labels/cellpose_qc` does not exist on disk for empty-cellpose datasets and the QC `start()` raises before reaching the inversion point. Include the prerequisite commit by cherry-picking or by branching from the fix branch's HEAD.

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/seg_qc.py`
- Test: `tests/test_gui_workflows/test_seg_qc_empty_labels_recovery.py`

**Approach:**
- Replace the early-return-with-auto-accept at lines 165-174 with the same code path as the non-empty case: `_hide_existing_layers()` → `_load_into_viewer()` → `_build_window()` → show. The empty labels array is loaded into napari as-is; existing draw / brush tools work on it without modification.
- Update the loading status messages in `_build_window` to surface "0 cells from Cellpose — draw, re-run, or modify channel to recover" when entering with empty labels. Drives the user's attention to the new affordances U2 and U3 will provide.

**Patterns to follow:**
- The non-empty path in `start()` directly above the early-return. Same teardown contract via `_finish`.

**Test scenarios:**
- **Covers AE1.** Happy path: given a dataset with `/labels/cellpose_qc` shape `(H, W)` all zeros, when `SegmentationQCController.start()` is called, then the QC window is shown (`_window is not None`, `_window.isVisible()`), the labels layer is loaded into napari, and `on_complete` has **not** been called.
- Happy path: same setup but the user uses napari's draw tool to add labels and clicks Accept; `/labels/cellpose_qc` is rewritten with the drawn pixels and `on_complete` fires with `success=True`.
- Edge case: empty labels + user clicks Cancel; `on_complete` fires with `success=False, message contains "cancelled"` (unchanged Cancel semantic).
- Edge case: non-empty labels (existing behavior); confirm window still opens (regression guard).
- Integration: opening the QC window with empty labels does not record any failure in `RunMetadata.failures` (the runner is not yet doing anything new, but this confirms the auto-skip removal doesn't leak through the runner's accounting).

**Verification:**
- Opening the QC window on a dataset whose `/labels/cellpose_qc.max() == 0` displays the dock and a visible (empty) labels overlay; the workflow does not advance past this dataset until the user acts.

---

- U2. **Re-run Cellpose group in the QC dock**

**Goal:** Add a collapsible **Re-run Cellpose** group below the existing Cleanup group with editable Cellpose knobs and a Re-run button. Clicking Re-run runs Cellpose in a worker thread on the **current channel display data** (which U4 will allow Modify Channel to influence) and replaces the in-QC labels layer with the new output.

**Requirements:** R3, R4, R7

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/seg_qc.py` — add `_build_rerun_group()`, `_on_rerun_clicked()`, `_on_rerun_finished()`, `_on_rerun_error()`, and a `_cellpose_input_image()` helper that returns the raw channel today (U4 makes it variable).
- Test: `tests/test_gui_workflows/test_seg_qc_rerun.py`

**Approach:**
- New collapsible `QGroupBox` (use `setCheckable(True)`-style collapse, matching the existing Cleanup group's expand affordance — verify the existing pattern and mirror it). Default collapsed.
- Six controls in the group:
  - Diameter: `QSpinBox` (range 0..1000, default seeded from `WorkflowConfig.cellpose.diameter`)
  - Segmentation channel: `QComboBox` populated from `store.metadata["channel_names"]`, default seeded from `WorkflowConfig.seg_channel_name`
  - Flow threshold: `QDoubleSpinBox` (range 0..3, step 0.05, default 0.4)
  - Cellprob threshold: `QDoubleSpinBox` (range -6..6, step 0.1, default 0.0)
  - Model: `QComboBox` over `("cpsam", "cyto3", "cyto2", "cyto", "nuclei")`, default `cpsam`
  - Min cell size: `QSpinBox` (range 0..10000, default 15)
- "Re-run" `QPushButton` at the bottom of the group. When clicked: disable the button, show a `QProgressDialog` with Cancel button, spawn `Worker(run_cellpose, image, model_type=..., diameter=..., gpu=cfg.cellpose.gpu, flow_threshold=..., cellprob_threshold=..., min_size=...)`, connect `finished` → `_on_rerun_finished` and `error` → `_on_rerun_error`.
- `_on_rerun_finished(labels: NDArray)`: early-return if `_torn_down`. Replace `viewer.layers["cellpose_qc"].data` with the new labels (no merge). The store is **not** written here — Accept still owns persistence. Re-enable the Re-run button. Close the progress dialog.
- `_on_rerun_error(err: WorkerError)`: show a non-modal status message in the dock; re-enable the button; close the progress dialog. Do not finish the phase.
- Changing the channel in this group changes `self._channel_idx` for the rest of the QC session **for the purposes of Re-run only** — the napari image layer is NOT swapped to the new channel until U4 wires the preview. (Out of v1: a "swap displayed channel" button alongside the picker.)

**Patterns to follow:**
- `src/percell4/gui/segmentation_panel.py:_on_run_cellpose` for the Worker setup (lines 395-510 approx). Mirror the worker lifecycle and signal connections.
- `src/percell4/gui/workers.py:Worker` for the QThread shape.
- The `creator-contract-four-step-sequence` learning for the eventual Accept-time write — Re-run does NOT itself persist; Accept handles that.
- The existing `_build_edit_group` / `_build_cleanup_group` methods for `QGroupBox` styling and the dock layout pattern.

**Test scenarios:**
- **Covers AE2 (Re-run always replaces).** Happy path: pre-load QC with `/labels/cellpose_qc` containing 5 cells; monkeypatch `run_cellpose` to return labels with 3 cells; click Re-run; await worker completion; `viewer.layers["cellpose_qc"].data.max() == 3`. The original 5 cells are gone.
- Happy path (knob plumbing): set diameter=60, flow=0.8, cellprob=-2.0 in the group widgets, click Re-run; assert `run_cellpose` was called with those exact kwargs.
- Edge case: clicking Re-run with diameter=0 → passes `diameter=None` to `run_cellpose` (auto-detect mode), since `CellposeSettings.diameter = 0` means auto per existing code.
- Edge case: channel picker set to a channel different from `seg_channel_name`; the Cellpose call receives `store.read_channel("intensity", new_channel_idx)`, not the original channel.
- Error path: worker emits `error` (e.g., monkeypatched `run_cellpose` raises); progress dialog closes, Re-run button re-enables, labels layer is unchanged from before the Re-run attempt.
- Error path: clicking Re-run twice quickly — the second click is gated by the disabled button; only one worker spawns.
- Integration: post-Re-run, clicking Accept persists the **new** labels (not the original Cellpose ones) to `/labels/cellpose_qc`, confirming the in-memory replace path lands on disk only at Accept.

**Verification:**
- Re-run replaces the in-QC labels with new Cellpose output, no failure recorded, Accept persists the replaced labels. Mid-flight cancellation via the progress dialog cleanly aborts and leaves the labels untouched.

---

- U3. **Modify Channel group with histogram + draggable handles + napari preview**

**Goal:** Add a collapsible **Modify Channel** group below Re-run with a histogram of the current channel's intensity distribution, draggable lo/hi handles, a Saturation% input + Auto button, and a live napari preview that swaps the displayed channel data to the clipped/stretched version while the group is active. Reverts cleanly on collapse / accept / cancel.

**Requirements:** R5, R6, R9

**Dependencies:** U1 (window must be open). U2 is independent — U3 can stand alone but is most useful with U2 (wired in U4).

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/seg_qc.py` — add `_build_modify_channel_group()`, the histogram + region widget, handle/spinbox bidirectional wiring, expand/collapse handler, preview install/revert.
- Test: `tests/test_gui_workflows/test_seg_qc_modify_channel.py`

**Approach:**
- Group composition: `QGroupBox(checkable=True, checked=False)` for the collapse affordance. When toggled to checked → expand-active hook: snapshot `viewer.layers[channel_name].data` into `self._original_channel_view`, then build/show the histogram widget and install the initial preview. When toggled to unchecked → restore from snapshot, hide histogram, free snapshot reference.
- Histogram widget (built lazily on first expand to avoid startup cost for the common ignore-this-group case):
  - `pyqtgraph.PlotWidget` with X axis = intensity, Y axis = pixel count
  - `np.histogram(self._intensity_for_histogram(), bins=100)` → pass `counts` + bin edges to `pg.BarGraphItem(x=edges[:-1], height=counts, width=bin_width)`. Do NOT compute midpoints (per the grouped-thresholding learning).
  - `pg.LinearRegionItem(values=(lo, hi), orientation="vertical", movable=True, brush=(0, 150, 200, 50))` over the plot. `setBounds((channel_min, channel_max))`.
  - `sigRegionChanged` → coalesced refresh via `QTimer.singleShot(0)`-style flag (per Pattern 2 of the multi-ROI learning + `napari-modal-tool-overlay-pattern` rules 4 and 9). Stop the timer in `_finish`.
- Numeric controls:
  - Lo and Hi `QDoubleSpinBox`es, two-way linked with the `LinearRegionItem` via a `_setting_handles` guard.
  - Saturation% `QDoubleSpinBox` (range 0.0..50.0, default 1.0).
  - **Auto** `QPushButton`: on click, compute `lo = float(channel.min())`, `hi = float(np.percentile(channel, 100 - sat_percent))`; assign to the region and the spinboxes.
  - On first expand, automatically perform an Auto with the default 1% — so the user sees a sensible default LUT applied immediately.
- Preview: assign `viewer.layers[channel_name].data = clipped_stretched_array` directly. Do **not** wrap in any `events.*.blocker()` (per the colormap learning). Set the layer's `contrast_limits` to `(0, dtype_max)` so napari's display uses the full new range without rescaling. Do not touch `contrast_limits_range`.
- Transformation function `_apply_lut(channel: NDArray, lo: float, hi: float) -> NDArray`: pure-numpy `clip + linear stretch + dtype cast`. Direct implementation, not a new module — keep it as a private helper near the controller; if a second caller appears later, promote to `src/percell4/domain/io/` or similar.
- Snap-no-cross constraint: in the lo/hi spinbox handlers, clamp `lo <= hi - epsilon` where epsilon is one unit in the channel's native quantization (1 for uint16). Same constraint on the `LinearRegionItem` via `setBounds` is not enough — `LinearRegionItem` enforces ordering but the spinboxes can be typed out of order; the guard belongs in both setter paths.
- Handle the time-lapse case: the histogram is built from the current single frame (or, simpler, from the same array passed to `_load_into_viewer` — which for time-lapse is the (T,H,W) stack's first frame in the controller's existing pattern). Persist the choice as a deferred implementation note; do not over-design.

**Patterns to follow:**
- `src/percell4/interfaces/gui/peer_views/phasor_plot.py` for `pg.LinearRegionItem` setup and the bidirectional spinbox sync.
- `src/percell4/gui/threshold_qc.py` for `BarGraphItem`-based histograms — count units + bin edges from `np.histogram`.
- The `napari-direct-label-colormap-rendering-blocked-by-events` solution for `.data =` assignment without blockers.

**Test scenarios:**
- **Covers AE3 (Auto seeds hi = p99).** Happy path: dataset with mNG channel max=65535, p99=1598; expand Modify Channel; the lo/hi spinboxes show `lo=channel.min()`, `hi=1598`; `viewer.layers["mNG"].data` reflects `clip(orig - lo, 0, hi - lo) / (hi - lo) * 65535`.
- **Covers AE5 (revert on collapse).** Happy path: expand Modify Channel; assert `viewer.layers["mNG"].data` differs from the original; collapse the group; assert `viewer.layers["mNG"].data` is now bit-identical to the on-disk channel via `np.array_equal`.
- Happy path: change Saturation% from 1.0 to 5.0 then click Auto; new hi equals `np.percentile(channel, 95)`.
- Edge case: drag the lo handle right past the hi handle position; assert lo is clamped to `hi - epsilon` and the region never inverts.
- Edge case: user types `hi=42` into the spinbox while `lo=100` is set; the constraint clamps to `lo + epsilon` or rejects; either way the region never inverts.
- Edge case: empty image (all zeros, e.g., the empty-labels-recovery case where the user might still want to LUT a dim channel) — Auto sets `lo = hi = 0`; the preview transformation must not divide by zero. Define the behavior: when `hi == lo`, the preview is identity (no transformation); confirm in the test.
- Edge case: drawn labels survive the preview install/revert. Pre-load labels with 3 cells, expand Modify Channel, assert `viewer.layers["cellpose_qc"].data.max() == 3`; collapse, assert still 3 cells; click Accept, assert persisted labels still show 3 cells against the on-disk **original** channel.
- Integration: opening Modify Channel does not call `store.write_array` for `/intensity`; verify by snapshotting the .h5's `/intensity` bytes before and after the QC session.

**Verification:**
- Expanding the group shows the histogram with a sensible default LUT applied; the napari image layer shows the clipped/stretched preview; collapsing restores the original channel pixel-for-pixel; on-disk `/intensity` is byte-identical after Accept.

---

- U4. **Wire Modify Channel preview as Re-run's Cellpose input**

**Goal:** When the user clicks Re-run with Modify Channel active, Cellpose receives the clipped/stretched array — not the raw channel. When Modify Channel is collapsed, Re-run uses the raw channel as before. This is the integration glue between U2 and U3 and is the smallest unit; it ships only after both upstream units are stable.

**Requirements:** R8

**Dependencies:** U2 and U3

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/seg_qc.py` — update `_cellpose_input_image()` (introduced in U2 as a constant-returning stub) to return the modified-channel preview when the Modify Channel group is checked, else the raw channel.

**Approach:**
- Single function change. `_cellpose_input_image()` reads `viewer.layers[self._channel_name].data` — which is naturally the modified preview when U3 is active and the raw channel otherwise. No state coupling, no flag check needed if the preview-install/revert path is correct.
- This validates the U3 design: by routing Re-run through the napari layer's `.data` rather than a parallel state field, U4 is essentially free.

**Patterns to follow:**
- The single-source-of-truth pattern from U2's `_cellpose_input_image()` stub.

**Test scenarios:**
- **Covers AE4.** Happy path: open QC with non-empty labels, expand Modify Channel with `hi=1000`, click Re-run; assert `run_cellpose` was called with the clipped/stretched array (compare against an expected np array computed in the test).
- Happy path: Modify Channel collapsed, click Re-run; assert `run_cellpose` was called with the raw channel array (compare via `np.array_equal` against `store.read_channel("intensity", channel_idx)`).
- Edge case: expand Modify Channel → Re-run → collapse Modify Channel → Re-run again. The second Re-run uses the raw channel. The labels from the first Re-run are still in the QC layer (no implicit revert of labels when the channel preview is reverted).
- Integration: Re-run with Modify Channel active → Accept; persisted labels are saved against the **original** channel intensities in the .h5 (the channel's on-disk pixels are unchanged), but the labels themselves reflect what Cellpose found on the modified image.

**Verification:**
- The Modify Channel transformation is consumed by Re-run when active; the persisted state contains only the final labels and the original (unmodified) `/intensity`.

---

## System-Wide Impact

- **Interaction graph:**
  - `SegmentationQCController` ↔ napari `ViewerWindow` (preview install/revert via direct `layer.data` assignment).
  - `SegmentationQCController` ↔ `Worker` (Re-run thread; signals `finished` / `error`).
  - `SegmentationQCController` ↔ `DatasetStore` (label persistence remains in Accept path only).
- **Error propagation:** Re-run worker `error` signal routes to a non-modal status message in the dock. No phase-level failure recorded for Re-run / Modify Channel errors — the user can simply try again.
- **State lifecycle risks:** A timer fire (singleShot-coalesced histogram refresh) after `_finish` could re-touch torn-down napari layers. Guard with `_torn_down` + synchronous `timer.stop()` in `_finish` (per the modal-tool overlay pattern). The Modify Channel preview must be reverted in `_finish` so the next dataset's QC starts clean — fold the revert call into the existing `_finish` teardown.
- **API surface parity:** None. No new endpoints, no new persisted columns, no new `run_log.jsonl` events.
- **Integration coverage:** The full F1 / F2 / F3 paths from the origin doc are exercised in test scenarios across U1–U4. Critical cross-layer scenarios: (a) Re-run replaces labels mid-QC and Accept persists the replaced version; (b) Modify Channel preview survives an unrelated re-render (e.g., changing napari z slider in time-lapse) without losing the LUT — verify in U3.
- **Unchanged invariants:**
  - Workflow runner's view of the QC phase: still receives a single Accept / Cancel signal. No new states.
  - Cellpose adapter (`run_cellpose`): no changes; called as today.
  - On-disk `/intensity` for any channel: byte-identical before and after a QC session, even if Modify Channel and Re-run are both used heavily.
  - `RunMetadata.failures` semantics: no new failure types and no new code paths that record one.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Timer-driven histogram refresh fires after `_finish` and corrupts the next dataset's view | `_torn_down` early-return + synchronous `timer.stop()` in `_finish`, per `napari-modal-tool-overlay-pattern` rules 4 + 9. Test scenario: expand Modify Channel, then call `_finish` immediately; assert no exceptions and napari layers are clean. |
| Modify Channel preview leaks across datasets in the QC queue | `_finish`'s teardown always reverts the channel layer to the snapshot if Modify Channel was expanded. The next dataset's `start()` re-reads `/intensity` from disk anyway, so a missed revert is recoverable, but the test must verify pixel-identity post-`_finish`. |
| Worker references survive the controller's `_finish` and emit signals on a dead controller | `Worker` is owned by the controller as `self._rerun_worker`; on `_finish`, disconnect signals before nulling. Mirrors `segmentation_panel`'s pattern. |
| `LinearRegionItem`'s `sigRegionChanged` storms during a drag (one signal per pixel of mouse motion) | Coalesce via `QTimer.singleShot(0)` flag (Pattern 2 of the multi-ROI learning). One actual histogram-and-preview update per event-loop tick, not per pixel. |
| Cellpose 4 model construction is heavy (seconds) on cold start; Re-run feels laggy | First Re-run after entering QC pays the cost; subsequent Re-runs reuse `self._cellpose_model` (the same hoisted-model field the runner already uses). Initialize it lazily on first Re-run, not at QC entry. |
| User changes the channel picker in Re-run to a channel that has no intensity data | Defensive: validate the picker against `store.metadata["channel_names"]` (which is already the picker's source); failure is impossible without a programming error. Add a defensive `KeyError` → status message anyway. |
| Cellpose 4 `eval()` API drift between versions | Already handled by `src/percell4/adapters/cellpose.py:run_cellpose` (v3 + v4 compatible). Re-run does not re-implement; it calls `run_cellpose`. |
| The prerequisite empty-Cellpose fix is on a separate branch and not yet committed | This plan's U1 is unbuildable without that fix on the same branch. Either rebase / cherry-pick the `segment_one` change onto this plan's branch as its first commit, or merge `fix/single-cell-workflow-tile-stitching` to main first. Document in the U1 implementer note. |

---

## Documentation / Operational Notes

- After U2 lands, capture the "Cellpose-in-QC worker" pattern with `/ce-compound` — the learnings researcher confirmed no `docs/solutions/` entry codifies this pattern today, and it'll recur (e.g., dilute-phase QC).
- No user-facing docs changes required for v1. The new groups are self-documenting via tooltips and the existing QC entry hint. A short README addition under `src/percell4/gui/workflows/single_cell/` (per-module CLAUDE.md convention — current state only, no plans) can describe the QC-as-recovery-surface model once all four units are in.
- No operational / rollout concerns: this is a single-process Qt UI change, no migrations, no API consumers.

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-26-seg-qc-recovery-options-requirements.md](../brainstorms/2026-05-26-seg-qc-recovery-options-requirements.md)
- Prerequisite fix on branch `fix/single-cell-workflow-tile-stitching` (uncommitted): `src/percell4/workflows/phases.py:segment_one` writes empty `/labels/cellpose_qc` instead of recording `SEGMENTATION_EMPTY`. Required before U1.
- Related code:
  - `src/percell4/gui/workflows/single_cell/seg_qc.py:165-174` (the auto-skip to invert)
  - `src/percell4/gui/segmentation_panel.py:_on_run_cellpose` (Worker pattern to mirror)
  - `src/percell4/gui/workers.py:Worker` (QThread wrapper)
  - `src/percell4/adapters/cellpose.py:run_cellpose` (downstream call)
  - `src/percell4/gui/threshold_qc.py` (pyqtgraph BarGraphItem precedent)
  - `src/percell4/interfaces/gui/peer_views/phasor_plot.py` (LinearRegionItem precedent)
  - `tests/test_gui_workflows/test_seg_qc_timelapse.py` (test fixture pattern)
- Institutional learnings:
  - `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md`
  - `docs/solutions/logic-errors/grouped-thresholding-development-lessons.md`
  - `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`
  - `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md`
  - `docs/solutions/architecture-patterns/session-to-napari-one-way-push.md`
  - `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md` (Pattern 2)
