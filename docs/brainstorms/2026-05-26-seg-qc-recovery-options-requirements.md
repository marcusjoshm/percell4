---
date: 2026-05-26
topic: seg-qc-recovery-options
---

# Seg-QC recovery options for the single-cell thresholding workflow

## Problem Frame

When Cellpose finds zero (or visibly wrong) cells on a dataset in the
single-cell thresholding workflow, the run has no recovery path. The
recent fix in `src/percell4/workflows/phases.py:segment_one` removed
the fatal `SEGMENTATION_EMPTY` failure and persists an empty
`/labels/cellpose_qc`, but a second blocker downstream silently
auto-skips the same case: `src/percell4/gui/workflows/single_cell/seg_qc.py:165-174`
detects `labels.max() == 0` and bypasses the QC window. The net
effect is still "no opportunity to fix this dataset", just one layer
deeper.

Beyond the empty case, users routinely hit datasets where Cellpose
finds *something* but visibly wrong — wrong scale, wrong channel,
long-tail intensity outliers (dust, hot pixels) skewing Cellpose's
internal percentile normalization. Today the only available remedies
are out-of-band: pre-process in ImageJ (LUT clamp), reconfigure the
workflow dialog, and re-run the entire workflow. That is the cost
of every misclassified dataset in a batch.

Empirical evidence captured during diagnosis on the
`/Volumes/NX-01-A/2026-05-25_FRET_export` dataset: Cellpose at
diameter=300 on `mNG` finds 0 cells; the same channel after an ImageJ
"Apply LUT" with `hi≈1000` (everything above 1000 saturated to max)
finds 19 ROIs at the same diameter. The user empirically tested
exposing Cellpose's `norm_percentiles` (1.0–99.9) and it does *not*
recover the cells — the long-tail outliers must be clipped out of the
input image entirely, not just down-weighted by percentile
normalization.

The fix is to make the seg QC step a real recovery surface: a place
where users can draw labels, re-run Cellpose with different
parameters, and / or feed Cellpose a temporarily-clipped image — all
without leaving the workflow or persisting the modified intensities
to the dataset.

---

## Actors

- A1. **Microscopy researcher** running a batch single-cell thresholding analysis. Currently QCs Cellpose's output per dataset; needs the same window to be a recovery surface when Cellpose's first attempt is inadequate.

---

## Key Flows

- F1. **Recover an empty-Cellpose dataset by hand-drawing**
  - **Trigger:** Cellpose returned 0 cells; the QC window opens with an empty labels layer instead of auto-skipping.
  - **Actors:** A1
  - **Steps:**
    1. QC window opens with the channel raster visible and an empty `/labels/cellpose_qc` layer selected.
    2. Researcher uses napari's existing draw / brush tools to create cell labels by hand.
    3. Researcher clicks Accept; labels persist; workflow advances.
  - **Outcome:** A dataset Cellpose couldn't segment is recovered with hand-drawn labels and continues into downstream phases.
  - **Covered by:** R1, R2, R7

- F2. **Re-run Cellpose with different settings inside QC**
  - **Trigger:** Researcher disagrees with the cells Cellpose found (or didn't find) and wants to try different parameters without leaving the QC window.
  - **Actors:** A1
  - **Steps:**
    1. Researcher expands the **Re-run Cellpose** group in the QC dock.
    2. Diameter / segmentation channel / flow / cellprob / model / min_size fields are pre-seeded from the workflow config but locally editable.
    3. Researcher edits any of the knobs and clicks **Re-run**.
    4. The Cellpose run uses the live values (and the modified-channel preview, when active — see F3) and the resulting labels *replace* whatever was previously in the QC labels layer.
    5. Researcher iterates as needed, then Accepts (or Cancels the whole run).
  - **Outcome:** The dataset gets segmented with parameters tuned for it specifically — no full-workflow restart required.
  - **Covered by:** R3, R4, R7, R8

- F3. **Modify the segmentation channel in memory and re-run Cellpose**
  - **Trigger:** Cellpose at sensible diameters finds nothing or finds wrong things because the channel has long-tail intensity outliers (dust, hot pixels) that skew its internal normalization.
  - **Actors:** A1
  - **Steps:**
    1. Researcher expands the **Modify Channel** group in the QC dock.
    2. A histogram of the channel's intensity distribution appears with two draggable handles (lo, hi). Saturation% input defaults to 1; the **Auto** button sets `hi = p99` of the channel (1% of pixels above the clip) and `lo = channel_min`.
    3. The napari intensity layer immediately renders the clipped + stretched preview (`clip(orig, lo, hi)` linearly mapped to the channel's dtype range).
    4. Researcher drags handles or types numbers to tune the LUT; the napari preview updates live.
    5. Researcher clicks **Re-run Cellpose** (in the Re-run group, F2). The Cellpose call uses the modified intensities.
    6. Researcher Accepts. The persisted labels are real; the LUT is discarded — the on-disk `/intensity` is never touched.
  - **Outcome:** Cellpose gets a clean, high-contrast image as input; finds cells it couldn't on the raw channel. Persistent dataset state remains untouched.
  - **Covered by:** R5, R6, R7, R8, R9

---

## Requirements

**Recovery surface**

- R1. Remove the auto-skip in `src/percell4/gui/workflows/single_cell/seg_qc.py:165-174`. When `labels.max() == 0`, the QC window opens with the empty labels layer instead of auto-completing the phase.
- R2. The existing draw / delete / cleanup tools must function on an empty labels layer (verify, don't reimplement). This is the hand-draw recovery path.

**Re-run Cellpose group (new collapsible dock group)**

- R3. The Re-run group appears below the existing **Cleanup** group, collapsed by default. Expanding it reveals the controls below and a **Re-run** action button.
- R4. The Re-run group exposes editable fields for: diameter (px), segmentation channel (dropdown over the dataset's channel names), flow threshold, cellprob threshold, model name, and min cell size. Initial values are seeded from the workflow's `CellposeSettings` plus the configured `seg_channel_name`; edits are local to this QC session and do not propagate to the workflow config or to other datasets.
- R7. **Re-run always replaces.** Clicking **Re-run** discards the current QC labels layer entirely and replaces it with the new Cellpose output. No merge, no prompt. If the researcher wants to keep both, they accept-and-undo or use Cancel-and-restart-dataset (which is the workflow's existing cancel semantic).
- R8. Re-run uses the current contents of the **Modify Channel** group if that group is active: Cellpose receives the clipped + stretched intensity, not the on-disk raw channel.

**Modify Channel group (new collapsible dock group)**

- R5. The Modify Channel group appears below the **Re-run** group, collapsed by default. Expanding it activates the modified-image preview in the napari viewer; collapsing it restores the original channel view.
- R6. The group contains: a histogram of the channel's intensity distribution with two draggable vertical handles for lo and hi clip values; numeric readouts for lo and hi (two-way linked with the handles); a **Saturation %** numeric input (default 1.0) with an **Auto** button that recomputes the handles using the formula `hi = percentile(channel, 100 − X)`, `lo = channel.min()` where X is the saturation %. Dragging a handle past the other one snaps both to a 1-unit separation; the handles are never crossed.
- R9. The clipped + stretched intensity is computed as `clip((orig − lo) / (hi − lo), 0, 1) × dtype_max`, producing the same dtype as the source channel. The transformation is in-memory only — the on-disk `/intensity` and any other persisted layers are never modified.

---

## Acceptance Examples

- AE1. **Covers R1, R2.** Given a dataset where Cellpose returned 0 cells, when seg QC starts for that dataset, then the QC window opens with the channel raster visible and an empty labels layer selected (rather than auto-completing the phase). The researcher draws three labels with napari's draw tool and clicks Accept; `/labels/cellpose_qc` persists with 3 cells.
- AE2. **Covers R3, R4, R7.** Given a dataset with Cellpose's initial output of 5 cells in QC, when the researcher expands Re-run, sets diameter from 300 to 60, and clicks **Re-run**, then the labels layer is replaced with whatever the new Cellpose call returned (which could be 0, 5, or any other count). No merge with the original 5 occurs.
- AE3. **Covers R5, R6, R9.** Given a dim channel with max=65535 and p99=1598, when the researcher expands Modify Channel and clicks **Auto** at saturation=1.0%, then the napari image layer renders the channel with `hi=1598`, `lo=channel_min`, and the dataset's on-disk `/intensity` is unchanged after the user Accepts QC.
- AE4. **Covers R8.** Given Modify Channel is active with hi=1000, when the researcher clicks **Re-run** in the Re-run group, then the Cellpose call receives the clipped + stretched array (not the raw channel). The resulting labels reflect Cellpose's analysis of the clipped image.
- AE5. **Covers R5.** Given Modify Channel is active and the napari intensity layer is showing the modified preview, when the researcher collapses the Modify Channel group, then the napari intensity layer immediately reverts to displaying the raw channel.

---

## Success Criteria

- A dataset where Cellpose found 0 cells can be recovered to a non-empty `/labels/cellpose_qc` without leaving the QC window or restarting the workflow.
- The user-demonstrated ImageJ workflow (apply LUT with hi≈1000 → Cellpose at d=300 finds ~19 ROIs) reproduces inside PerCell4: Modify Channel with hi=1000 + Re-run produces a comparable count, with the on-disk `/intensity` byte-identical to before the QC session.
- The dock remains usable for the happy path: a researcher whose Cellpose result is already good can ignore both new groups and Accept as before.
- Downstream phases (threshold-compute, measure, export) see no semantic change — they receive labels from `/labels/cellpose_qc` exactly as today, no new metadata to interpret.

---

## Scope Boundaries

- Modify Channel persists only across the lifetime of one dataset's QC session. Switching to the next dataset resets the LUT to identity. No global "remember last LUT" setting in v1.
- The LUT is never persisted to the .h5 dataset. Modify Channel is strictly a Cellpose-input pre-processor.
- Re-run does not offer a merge-with-existing-labels mode. Users who want both Cellpose's output and their hand-drawn cells must draw on top after Re-run completes.
- No per-dataset "skip this dataset but continue the workflow" option. Cancel still cancels the whole run, as today.
- No new failure types or run-log events for in-QC re-run / modify activity. The runner only sees the final Accept / Cancel, same as today.
- The 1% saturation default clips at the high end only (`hi = p99`, `lo = channel_min`). Bilateral saturation (0.5% each tail) is an open detail (see Outstanding Questions) — not in v1 unless explicitly requested.
- No persistence of the in-QC Cellpose tweaks (per-dataset diameter, channel, etc.) back to the workflow config. The workflow config is "the initial guess"; QC tweaks are local.
- No Cellpose progress indicator UI design specified here — pick a reasonable status display in planning. The Re-run button blocking is acceptable.
- Visual feedback for the modified channel preview uses the existing napari image-layer plumbing — no new colormap or contrast widget on the layer rail.

---

## Key Decisions

- **Auto-skip inversion is the prerequisite, not an option.** Until `seg_qc.py:165-174` lets the empty-labels case through, none of the new groups are reachable for the most-pained case.
- **Manual clip-and-stretch (Option 3-B), not Cellpose normalize dict (Option 3-A).** Empirically tested: tuning `norm_percentiles` from `(1, 99)` to `(1, 99.9)` did not recover cells on the troublesome dataset. The long-tail outliers must be clipped out of the input image, not merely down-weighted.
- **Two collapsible groups, both inside the existing QC dock.** Tabs separate the recovery tools from the happy-path tools too sharply; a separate dialog adds friction; an accordion adds clicks. The dock already follows a vertical-group pattern (Edit, Cleanup) — extending it is the minimum-surprise option.
- **Re-run always replaces.** Merge / prompt-each-time both add product complexity that the user pre-emptively rejected in favor of a simpler iteration loop. Cancel-and-restart-dataset is the escape hatch.
- **Modify Channel is visible-by-default in napari.** "Invisible Cellpose-only preprocessor" was considered and rejected — the user explicitly wants to see what Cellpose will see (mirrors their ImageJ workflow). Collapsing the group reverts the view.
- **Saturation % auto-mode mimics ImageJ's *Enhance Contrast*.** Familiar mental model — a microscopy researcher already knows what saturation% does.

---

## Dependencies / Assumptions

- The empty-Cellpose recovery fix in `src/percell4/workflows/phases.py:segment_one` (already shipped on branch `fix/single-cell-workflow-tile-stitching`) is a prerequisite — it's what creates the `/labels/cellpose_qc` resource that the QC window then loads.
- Cellpose 4's `CellposeModel.eval()` accepts a 2D float array as `image`; the existing `src/percell4/adapters/cellpose.py:run_cellpose` is the integration point reused by Re-run. No new adapter required.
- `pyqtgraph` is already a project dependency (used in the phasor view) — the histogram + draggable handles widget can be built on it. **Assumption** (not yet verified): `pyqtgraph.LinearRegionItem` over a `PlotItem` of the histogram is sufficient for the lo/hi handles UI.
- napari's image layer supports replacing the `.data` attribute live for the Modify Channel preview. **Assumption** (not yet verified): a `set_data` (or equivalent) reassignment on the existing layer refreshes the canvas without re-creating the layer.
- The runner does not need to know anything new — Accept / Cancel are the only signals it consumes from the QC controller. No `run_log.jsonl` schema changes.

---

## Outstanding Questions

### Resolve Before Planning

*(none — all product decisions have been made.)*

### Deferred to Planning

- [Affects R6][Technical] Best pyqtgraph widget composition for histogram + two-handle clip range. `LinearRegionItem` is the leading candidate but evaluate `InfiniteLine`-pair alternatives if it doesn't handle the snap-no-cross constraint.
- [Affects R5, R9][Technical] How to swap napari image layer data without flicker or zoom reset. Likely `layer.data = arr` plus a `_keep_auto_scale` toggle, but confirm against napari 0.7 behavior.
- [Affects R7][Technical] Threading: should Re-run block the dock or run in a `QThread` worker? Cellpose on a 1024×1536 image takes ~5–30 s depending on GPU; a worker is the right shape but the simplest acceptable v1 is a synchronous call with a `QProgressDialog`.
- [Affects R6][Needs research] Should bilateral 1% saturation (0.5% each tail) be the default instead of top-only? Confirm with users on dim microscopy data whether the bottom tail ever has meaningful signal worth preserving from the clip.
- [Affects R8][Technical] When Modify Channel is collapsed *after* a Re-run that used the modified intensities, do the labels in the QC layer remain (and get accepted against the original intensities)? Confirm the semantic: the labels are a region map, not a re-derivation of the image — they survive a channel-view revert. The accepted labels are valid against the on-disk channel regardless of which intensity Cellpose saw.

---

## Next Steps

`-> /ce-plan` for structured implementation planning. All product decisions are resolved; the Deferred-to-Planning questions are technical and belong in the planning pass.
