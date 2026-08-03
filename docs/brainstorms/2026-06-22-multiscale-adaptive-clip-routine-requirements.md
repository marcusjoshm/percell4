# Multi-scale Adaptive Local Clipping routine — Requirements

**Date:** 2026-06-22
**Status:** Requirements (ready for `/ce-plan`)
**Scope:** Standard feature — a new mode in the interactive Adaptive Local Clipping panel

## Problem

The per-cell adaptive-clip detector uses a **single** local-background window. A
window detects a particle cleanly only when it is several × larger than that
particle (so the Gaussian-blur local background is the surrounding cell, not the
particle subtracting itself). When a cell's particles span a **wide size range**,
no single window works: a small window hollows out / under-detects the large
particles; a large window loses local adaptation for the small ones. The user
needs particles across the whole size range thresholded properly in one pass.

## Approach (chosen)

A **multi-scale** routine: run the existing per-cell adaptive clip at a doubling
sequence of windows and **OR-combine** the masks (a pixel is foreground if *any*
pass marks it). Window scale is seeded from a first-pass Otsu particle-size
assessment. This is a band-pass union — each window detects a particle size band,
and the union covers small→large.

## The routine

1. **First-pass size assessment — per-cell Otsu.** On the active channel, run Otsu
   **per cell** (each cell its own threshold, within the active segmentation),
   label connected components, and pool their equivalent diameters across cells.
   Compute:
   - **Raw min and raw max** particle diameter — always reported (see Debug),
     independent of any setting, for calibration.
   - Over particles **≥ the user cutoff**: smallest, **mean**, largest, and range
     (largest − smallest).
2. **Window sizing.** `k = 1` (fixed, all passes).
   - **Auto:** starting window `W0 = ½ × mean particle diameter` (px; forced odd;
     floored at the self-subtraction minimum, 3 px).
   - **Manual:** `W0 =` a user-entered window in **px or µm** (µm → px via the
     dataset pixel size). Otsu (step 1) still runs to get the largest particle
     (for the stop condition) and the raw min/max (debug).
3. **Iterative doubling + combine.** Run the per-cell adaptive clip
   (`detect_adaptive_per_cell`, per-cell σ, `k=1`) at windows `W0, 2·W0, 4·W0, …`.
   **Stop** after the first pass whose window exceeds the largest particle
   diameter from step 1 (that pass is included). **OR-combine** all pass masks
   into one (pixel = 1 if any pass = 1).
4. **Output.** One combined per-cell mask, written as a `/masks/<name>` resource
   (Creator), auto-selected and shown in napari — same as the other panel modes.

## Decisions (resolved in brainstorm)

- **Surface:** new mode in the interactive `AdaptiveClipPanel` (not headless yet).
- **Start window:** `½ × mean particle` (auto) + manual px/µm override.
- **Size cutoff = stats floor only.** Particles below the cutoff are excluded from
  the size stats (so the mean — and therefore the window — is not dragged down by
  noise). The cutoff does **not** filter the final mask; the passes' detections are
  kept as-is.
- **Combine:** logical OR across all passes.
- **Doubling:** ×2 per pass; stop at `window > largest particle`.

## Reused vs new

- **Reuse:** `detect_adaptive_per_cell(image, labels, *, window_px, min_spot_px, k,
  presmooth)` (per pass); `resolve_window_px` (manual px/µm → odd px); the panel's
  terminal debug-print pattern; the Creator save path (`AcceptPunctaMask`).
- **New:** a per-cell Otsu **size-assessment** helper (pooled component diameters +
  stats; the existing `otsu_smallest_particle` is a single global-in-cell Otsu, not
  per-cell); the multi-scale **orchestrator** (window sequence + OR-combine).

## Debug output (terminal)

Every run prints, in the existing debug style:
- All settings (mode, cutoff, auto/manual start, k).
- **Otsu raw min and max particle size** (before the cutoff) — the calibration
  instrument for refining the method.
- Post-cutoff smallest / mean / largest / range.
- The **window sequence** actually run (`W0, 2·W0, …`) and the stop window.

## Scope boundaries

**In:** the panel mode, auto + manual start, per-cell Otsu assessment, doubling +
OR-combine, debug output.

**Out / deferred:**
- Headless / batch-workflow version (panel first; port the shared core later).
- Per-cell *different* windows (the window is one value per pass; only σ is
  per-cell).
- A final-mask size filter (cutoff is stats-only by decision).
- Changing the existing modes (granule-size / otsu-mean / Otsu-detect-particle /
  manual).

## Open questions / calibration points (for planning + eye-validation)

1. **Stop condition strength.** `window > largest` may only *partially* fill the
   largest particle (clean detection wants window ≈ several × the particle). The
   debug exposes the largest size, so this can be calibrated; consider whether the
   stop should be `window > factor × largest`. **The ½×mean start and the stop are
   both explicitly provisional — the debug output is how we refine them.**
2. **Noise accumulation.** OR-combining several passes with no size filter will
   keep every sub-cutoff speck any pass detects; revisit a size filter if results
   are noisy.
3. **Units.** Cutoff and size stats — px vs µm (default px; µm needs a pixel size).
4. **Prerequisites.** Per-cell ⇒ requires an active segmentation and single-frame;
   define behavior with no segmentation (likely abort, matching the per-cell modes).

## Success criteria

- On a dataset with a wide particle size range, the combined mask fills **both**
  small and large particles (large ones not hollowed), confirmed by eye.
- The terminal prints Otsu raw min/max + mean/range + the window sequence each run.
- Auto and manual-start both work; manual µm requires a known pixel size.
