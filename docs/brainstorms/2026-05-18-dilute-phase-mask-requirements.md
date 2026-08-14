---
status: active
created: 2026-05-18
type: requirements
feature: dilute-phase-mask-generation
---

# Dilute Phase Mask Generation — Requirements

## Problem

In phase-separated biological samples the **condensed phase** (bright
droplets / puncta) sits on top of a much dimmer **dilute phase**
(background protein inside the cell). A single pass of
`Grouped Threshold` cannot isolate the dilute phase: the bright
objects dominate the metric distribution, and any cell that looks
"bright" relative to them stays above threshold. The dilute phase —
the in-cell pixels that are *not* in any condensed cluster — is what
the user actually wants to measure for downstream analyses
(dilute-phase mean intensity, dilute-phase phasor signatures), but it
has no first-class tool today.

Manually subtracting the condensed mask and rerunning
`Grouped Threshold` works conceptually but is impractical: it would
require mutating the on-disk channel image, the user would lose
their place between rounds, and there is no provision for accumulating
"already accounted for" pixels.

## Goal

Add a guided, iterative workflow that produces **one** binary mask of
the in-cell dilute phase for the currently open dataset. Each
iteration peels off another layer of condensed-phase pixels until the
user is satisfied that nothing condensed remains. The final result is
one mask written to `/masks/<user_name>`; no intermediate state
persists to the `.h5`.

## Users / Where it lives

- **Primary user**: the researcher driving a single open dataset.
- **Surface**: a new entry on the **Workflows** sidebar tab in
  `src/percell4/interfaces/gui/main_window.py`, alongside the
  existing "Single-cell thresholding analysis workflow" button.
  Label: **"Dilute phase mask generation"**.
- **Scope difference from existing Workflows**: every existing
  Workflows entry is a multi-dataset batch runner. This is the first
  *interactive single-dataset* workflow. It operates on the currently
  open dataset, not a set of files chosen in a config dialog.

## Conceptual procedure

For the currently open dataset, with an active channel and active
segmentation:

1. **Configure once.** Before round 1, the user sets:
   - **Dilute mask name** (string, validated against existing
     `/masks/<name>` entries up-front).
   - **Dilation radius (px)** (single value used for every round).
   - **Grouped Threshold settings** (metric, algorithm + algorithm-
     specific options, σ) — same controls as
     `src/percell4/gui/grouped_seg_panel.py`. These are **locked** at
     the moment Round 1 starts. The settings UI then collapses /
     disables for the rest of the workflow. (Cancel + reopen is the
     only way to retune.)
2. **Round N.** Recompute the chosen per-cell metric from the current
   in-memory working buffer (round 1: original active channel; later
   rounds: prior buffer with NaN-filled subtraction holes), cluster
   the cells with the locked GT settings, and launch the existing
   `ThresholdQCController` modal so the user can accept / refine /
   reject the round's condensed mask. The QC is run with
   `write_measurements_to_store=False` so nothing leaks to the `.h5`.
3. **Dilate.** Morphologically dilate the accepted condensed mask by
   `dilation_radius_px` using `scipy.ndimage.binary_dilation` with a
   disk structuring element.
4. **Subtract = set to NaN.** Set those dilated pixels to **NaN** in
   the in-memory working buffer. The on-disk `/intensity` is never
   touched. NaN (not zero) is the chosen subtraction semantic so that
   the next round's metric ignores already-accounted-for pixels
   instead of clustering them as "very dim cells".
5. **Accumulate.** Union the dilated mask into a running
   `cumulative_condensed` mask kept in memory.
6. **Preview.** Show the new working buffer in napari as a temporary
   scalar layer, with the running cumulative-condensed union
   overlaid. The user inspects and decides:
   - **Run another round** — repeat steps 2-6 (settings stay locked).
   - **Done — Save** — exit the loop and persist the final mask
     (see Output).
   - **Cancel** — drop all in-memory state and restore the viewer.
     Nothing is written to disk.

## Output

On **Done**:

- Compute
  `dilute_mask = (pixel ∈ any cell in active segmentation)
                  AND NOT cumulative_condensed`.
  This is the **in-cell domain** decision: extracellular background is
  excluded so the mask captures the biological dilute phase, not
  empty space.
- Write it via `DatasetStore.write_mask(name, dilute_mask)` where
  `name` is the user-supplied dilute-phase mask name from step 1.
- Auto-select the new mask on the session
  (`session.set_active_mask(name)`) to match the **Add Layer**
  convention pinned by
  `tests/test_gui/test_add_layer_write_layer_sets_active.py`.

Nothing else is persisted: no intermediate subtraction buffers, no
per-round condensed masks, no per-round measurements.

## NaN propagation (consequence of step 4)

Subtraction = NaN is the cleanest semantic but requires every
downstream operation in the round to be NaN-aware:

- **σ blur** in the Grouped Threshold path —
  `scipy.ndimage.gaussian_filter` is NOT NaN-safe. Use a
  normalized-convolution variant (blur an `isfinite` mask with the
  same kernel, blur a NaN-filled-with-zero copy, divide; pixels with
  zero kernel weight stay NaN). Equivalent libraries:
  `astropy.convolution` if available, else implement the two-pass
  form inline.
- **Per-cell metric** — switch to `np.nanmean`, `np.nanmedian`, etc.
  for the recomputed-per-round metric pass. Cells whose entire pixel
  set is NaN after subtraction drop out of the next round's
  clustering (their metric is NaN; the clustering step already drops
  NaN rows). This is intended behaviour — the cell has been fully
  accounted for.
- **napari display** — scalar layers render NaN as transparent, which
  is the right visual cue.

## UI

A dedicated panel (`DilutePhaseMaskPanel` or similar) opens when the
user clicks the Workflows entry.

### Setup block (active before Round 1; disabled afterward)

- **Active dataset / channel / segmentation read-out** — disabled
  text fields. If any of the three is unset, the Start button is
  disabled with an inline message naming the missing field.
- **Dilute mask name** — line edit, required, default suggestion
  `"dilute_phase"`. Inline uniqueness check against existing
  `/masks/<name>`; if collision, suggest `<name>_2` etc. (mirrors
  `add_layer_dialog`'s name validation).
- **Dilation radius (px)** — `QSpinBox`, range `0..50`, default `5`.
  Locked once Round 1 runs.
- **Grouped Threshold settings group** — exactly the controls from
  `src/percell4/gui/grouped_seg_panel.py`: metric combo, algorithm
  combo (GMM / K-means), GMM criterion + max components, K-means
  n_clusters, Gaussian σ. Reuse the widget factory rather than
  reimplementing. Locked once Round 1 runs.
- **Start** button — disabled until the three required upstream
  fields and the mask name are valid.

### Iteration block (visible during the loop)

- **Round counter / status** — `"Round N — condensed pixels removed:
  P% of in-cell area"` (P =
  `cumulative_condensed[in_cell].mean() * 100`).
- **Per-round buttons**:
  - **Run another round** — recompute metric on working buffer,
    cluster, launch `ThresholdQCController`, dilate accepted mask,
    apply NaN subtraction, accumulate union, refresh preview layers.
  - **Done — Save dilute phase mask** — compose the final mask and
    write it.
  - **Cancel** — drop state, restore viewer, do not write.

### Viewer side effects (transient, never written)

While the workflow is active:

- A scalar napari layer named `"_dilute_workflow_view"` holds the
  current working buffer (NaN-subtracted image).
- A labels overlay `"_dilute_workflow_condensed"` shows the
  cumulative condensed union.
- Both layers are removed on Done, Cancel, or workflow close.
- The session's `active_channel` is not changed. The Grouped
  Threshold subroutine reads the working buffer directly, bypassing
  `session.active_channel`.
- The `ThresholdQCController` modal already manages its own layer
  visibility save/restore; the workflow does not double-touch those.

## Reuse plan

- **Grouped Threshold core** —
  `percell4.domain.measure.grouper.group_cells_gmm` /
  `group_cells_kmeans` are pure-numpy and reusable as-is.
- **ThresholdQCController** — the existing per-round QC
  (`src/percell4/gui/threshold_qc.py`) is reused with
  `write_measurements_to_store=False` (mirroring
  `gui/workflows/single_cell/threshold_qc_queue.py`) so the round's
  accepted condensed mask is captured in-memory.
- **Grouped Threshold settings widget** — extract the settings block
  from `grouped_seg_panel.py` into a reusable factory used by both
  the original panel and the new workflow. Single source of truth
  beats two copies that will drift.
- **NaN-safe Gaussian** — small inline helper in
  `src/percell4/domain/image/` (or wherever the σ blur currently
  lives in the GT pipeline). Avoid pulling `astropy` just for this.
- **Dilation** — `scipy.ndimage.binary_dilation` with a disk
  structuring element of radius `dilation_radius_px`.
- **Auto-select on save** — same `set_active_mask(name)` pattern
  added to `add_layer_dialog._write_layer` in commit `bef67b0`.

## Non-functional

- **No persistence of intermediates.** Only the final
  `/masks/<name>` write hits the `.h5`. Walking through ten rounds
  and then **Cancel** must produce zero `.h5` diffs.
- **Cancellation restores state.** On Cancel: workflow layers
  removed, session selection fields unchanged, working buffer
  released.
- **Re-entry guard.** Only one workflow at a time; matches the
  existing `is_workflow_locked` pattern in `main_window.py`.
- **No background threads required beyond what already exists.**
  Grouped Threshold's clustering already runs in a `Worker`; the
  panel waits on the same `finished` / `error` signals. Dilation +
  NaN subtraction are fast enough for synchronous execution on the
  main thread at typical PerCell4 image sizes.

## Scope boundaries

In scope:
- Iterative loop: GT → user-QC → dilate → NaN-subtract → preview.
- Locked-at-start GT settings and dilation radius.
- In-cell-domain final mask, written once.
- Per-round display of NaN-subtracted buffer + cumulative-condensed
  overlay.

Out of scope (v1):
- Per-round undo / step-back. Cancel-and-restart is the recovery
  path.
- Re-tuning GT settings mid-flow.
- Saving intermediate per-round condensed masks.
- Saving the round-by-round working buffers.
- Batch / multi-dataset version. This workflow is single-dataset.
- Working under `view_bin > 1` is not blocked but is also not a
  design goal; the workflow runs at whatever the active bin is and
  writes the mask at the same resolution as every other Creator.
- Anything that mutates `/intensity`.

## Open questions for planning

Technical / sequencing decisions that belong in `/ce-plan`:

1. **Settings-widget extraction shape.** Pull GT settings out of
   `grouped_seg_panel.py` into a standalone `QWidget` subclass with
   a `current_config()` method, vs. keep both panels self-contained
   and duplicate controls. Recommendation: extract.
2. **ThresholdQCController coupling.** Today it takes
   `channel_image` directly — fortunate, because the workflow can
   pass the in-memory working buffer. Confirm in planning that
   nothing in the controller silently re-reads channel/segmentation
   from the store.
3. **Per-cell metric recomputation per round.** The existing GT
   panel reads `df[f"{channel}_{metric}"]` from `CellDataModel` —
   precomputed against the on-disk channel. The dilute workflow
   must **recompute** the metric per round from the NaN-subtracted
   working buffer instead of trusting the precomputed column. Locate
   the per-cell metric kernel used by the GT pipeline and call it
   directly with the working buffer + the active segmentation labels.
4. **NaN-safe Gaussian helper placement.** Pick a module under
   `src/percell4/domain/image/` and keep a single canonical
   implementation. Add tests for the boundary case where a cell's
   pixels are entirely NaN.
5. **napari layer collision sentinel.** `_dilute_workflow_view` /
   `_dilute_workflow_condensed` are reserved names; document the
   reservation alongside the existing single-cell workflow's layer
   discipline.

## Acceptance examples

- **AE-1 — Three-round happy path.** Open a dataset with active
  channel + active segmentation. Click *Dilute phase mask
  generation*. Enter `dilute_v1`, dilation radius 5, default GT
  settings. Click Start. Round 1 → QC modal → accept → preview
  shows brightest puncta NaN'd out. Run round 2 → QC modal →
  accept → next tier gone. Run round 3 → QC modal → accept →
  nothing meaningful left. Click **Done**.
  Result: `/masks/dilute_v1` exists, equals
  `(in_cell) AND NOT cumulative_dilated_condensed`,
  `session.active_mask == "dilute_v1"`, workflow viewer layers gone.
- **AE-2 — Cancel after rounds.** Same setup, run two rounds, click
  **Cancel**. Result: no `/masks/dilute_v1` in the store; workflow
  viewer layers gone; `session.active_mask` unchanged.
- **AE-3 — Missing prerequisites.** No active segmentation. Open
  the workflow. Start is disabled, panel shows
  `"Select an active segmentation in the Session window."`
- **AE-4 — Duplicate mask name.** A `/masks/dilute_v1` already
  exists. Enter that name. Result: inline validation error, Start
  disabled, suggested name `dilute_v1_2`.
- **AE-5 — Cell entirely subtracted.** After round 2, every pixel
  of cell #17 is NaN. In round 3, cell #17's metric is NaN and the
  clustering step drops it. The viewer's cumulative-condensed
  overlay still covers cell #17 (it was accounted for in round 1
  or 2), and cell #17 contributes zero pixels to the final dilute
  mask. No error, no warning.

## References

- Existing Grouped Threshold panel: `src/percell4/gui/grouped_seg_panel.py`
- Existing Threshold QC controller: `src/percell4/gui/threshold_qc.py`
- Multi-dataset reuse of the QC: `src/percell4/gui/workflows/single_cell/threshold_qc_queue.py`
- Add Layer auto-select convention: `tests/test_gui/test_add_layer_write_layer_sets_active.py`
- Workflows sidebar host: `src/percell4/interfaces/gui/main_window.py:325-348`
- Multi-dataset workflow runner base: `src/percell4/gui/workflows/base_runner.py`
- Mask write contract: `src/percell4/store.py` `write_mask`
