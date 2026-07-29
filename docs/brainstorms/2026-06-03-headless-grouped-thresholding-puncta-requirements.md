---
date: 2026-06-03
topic: headless-grouped-thresholding-puncta
---

# Headless, QC-Free Grouped Thresholding for Puncta Detection

## Problem Frame

PerCell4's grouped thresholding (`apply_threshold_headless()` in `src/percell4/workflows/phases.py`, with grouping in `src/percell4/domain/measure/grouper.py` and per-group Otsu in `src/percell4/domain/measure/thresholding.py`) requires an interactive QC step (`src/percell4/gui/threshold_qc.py`) to be usable. QC works but is laborious and prohibitive at scale.

The QC burden traces to a single root cause, confirmed by example screenshots: **Otsu over a whole group is the wrong detector for small bright puncta on a spatially-varying background.** Otsu splits the histogram it is given and implicitly assumes balanced classes; over a whole cell the threshold is dragged by whichever pixel population dominates. Where bright foci dominate, it lands too high and misses dim foci (the common "under-capture" case); where diffuse haze dominates, it lands too low and floods (the rarer "over-capture" case). The manual fix — drawing a small ROI — works because it simultaneously fixes local scale, local background, and local class balance.

The targets are stress-granule (SG) foci under an experimental condition that makes them small and numerous. A focus is "real" when it is a compact spot that is both above the cell's true background **and** above its local surroundings. The objective is **maximum recall — capture every punctum.** Rare lysosome false positives are known (from orthogonal experiments) to be too few to affect results, so SG/lysosome discrimination is not required.

The user has eye-confirmed ground truth and a large archive of previously QC-approved masks, which makes it possible to *measure* whether an automated method is good enough — the basis for retiring QC.

---

## Actors

- A1. Researcher: labels ground-truth fields, runs the validation harness, selects and locks the winning method + parameters.
- A2. Validation harness: dev-time tool that runs registered methods over a parameter grid against ground truth and reports scores.
- A3. Headless workflow runner: production-time path (`src/percell4/gui/workflows/single_cell/runner.py` phases) that applies the locked method across datasets with no interaction.

---

## Key Flows

- F1. Method race / validation (dev-time, one-time per condition)
  - **Trigger:** Researcher wants to qualify an automated method for a class of data.
  - **Actors:** A1, A2
  - **Steps:** Assemble labeled data (existing QC'd masks + a few exhaustively point-labeled fields) → harness runs each registered method across a parameter grid → harness scores per-punctum recall/precision against the hybrid ground truth → researcher reviews scores → researcher picks and locks method + parameters.
  - **Outcome:** A named method + parameter set is qualified and recorded for headless use.
  - **Covered by:** R5, R10, R11, R12, R13, R14

- F2. Production headless thresholding (per dataset, no QC)
  - **Trigger:** Batch workflow reaches the thresholding phase with the headless mode selected.
  - **Actors:** A3
  - **Steps:** For each intensity group — pass 1 produces an initial detection → pass-1 results calibrate parameters and/or estimate background → background subtraction / re-detection in pass 2 → per-group masks combined by union. Result stored to `/masks/<round>` (+ `/groups/<round>`); downstream measurement proceeds. No interactive step.
  - **Outcome:** A binary puncta mask per round, identical in shape/dtype to today's grouped-threshold output, produced without human review.
  - **Covered by:** R1, R2, R3, R4, R15, R16

---

## Pipeline at a glance

```
DEV-TIME (once per condition)                 PRODUCTION (every dataset, headless)
─────────────────────────────                 ────────────────────────────────────
existing QC'd masks ─┐                          for each intensity group:
exhaustive labels ───┤                            pass 1: initial detect ───┐
                     ▼                                                       ▼
        ┌────────────────────────┐               calibrate params  +  estimate background
        │  VALIDATION HARNESS     │                 (per group; spot scale per dataset)
        │  run registry × grid    │                              │
        │  score recall/precision │                              ▼
        └───────────┬─────────────┘               pass 2: background-subtract / re-detect
                    ▼                                            │
        pick + LOCK method+params  ───────────────►   union per group/round → /masks
```

Two orthogonal pluggable axes feed the detector:
- **Background estimator** (value or surface): donut-median, donut-mean, Gaussian-peak-fit, percentile, MAD, background-surface.
- **Detector / threshold-setter**: bg + k·σ, white top-hat, à trous wavelet, LoG/DoG, h-maxima, (Otsu baseline).

---

## Requirements

**Production headless detection workflow**
- R1. Provide a fully-headless thresholding mode (a `ThresholdingRound` variant) that requires no interactive QC and produces a binary puncta mask per group, combined by union, identical in shape and dtype to today's grouped-threshold output.
- R2. Use a two-pass per-group structure: pass 1 produces an initial detection over each group; pass 2 uses pass-1 results to calibrate parameters and/or estimate and subtract background, then re-detects. Masks are combined across passes and groups by union.
- R3. Perform calibration and background estimation per intensity group (existing GMM/kmeans grouping retained), with spot scale/size optionally calibrated once per dataset across all foci rather than per group.
- R4. Optimize for recall — capture every punctum — accepting rare false positives (e.g., lysosomes) known to be negligible in downstream results.

**Pluggable method registry**
- R5. Provide a registry of interchangeable detection methods selectable by name, mirroring the existing `THRESHOLD_METHODS` / donut `bg_mode` pattern, so methods can be swapped and compared without changes elsewhere.
- R6. Expose two orthogonal pluggable axes — (a) background estimators (value or surface) and (b) detectors / threshold-setters — where a run names one of each, or names a detector that needs no separate background step.
- R7. Seed background estimators with: donut-median, donut-mean, Gaussian background-peak-fit (reuse `estimate_bg_threshold()` in `src/percell4/domain/analysis/_impl/per_particle_donut.py`), percentile, MAD-based, and a background-surface built by interpolating donut samples or by rolling-ball.
- R8. Seed detectors with: background-subtract then `background + k·σ` threshold; white top-hat; à trous wavelet spot detection; LoG/DoG; h-maxima local-prominence. Retain Otsu as a baseline for comparison.
- R9. Treat an example-trained pixel classifier (ilastik-style random forest or small U-Net) as an explicitly supported future registry entry held in reserve — not required for v1.

**Validation harness & ground truth**
- R10. Provide a dev-time harness that runs any registered method across a parameter grid over a labeled dataset and scores it.
- R11. Establish ground truth as a hybrid: existing QC-approved masks as a broad precision/baseline reference, plus a small set of fields where every focus (including faint ones) is exhaustively point-labeled as the recall ceiling.
- R12. Score per-punctum and location-matched: report recall and precision (and a recall-weighted summary) so a method is credited for foci the old method missed and penalized for flooding.
- R13. Report per-method and per-parameter scores so the winning method + parameters can be selected from real data.

**Trust model (QC retirement)**
- R14. Establish trust once, globally: a method + parameters validated on labeled data is locked and applied headless to all comparable data with no per-image review, replacing per-image QC.

**Compatibility & integration**
- R15. Keep output masks compatible with downstream consumers (per-particle donut, per-particle multichannel, dilute-phase) with no changes required there.
- R16. Integrate the headless mode into the existing batch workflow (`WorkflowConfig` / single-cell runner phases) and per-round `/masks` + `/groups` storage.

---

## Acceptance Examples

- AE1. **Covers R4, R12.** Given an under-capture field where grouped Otsu marked only the brightest foci, when the qualified method runs, then it recovers the dim foci visible by eye, raising recall against the exhaustive labels, without precision collapsing on the QC'd reference.
- AE2. **Covers R2.** Given an over-capture field where global Otsu flooded the cell with diffuse haze, when pass 2 flattens background and re-detects, then the diffuse haze is excluded and discrete puncta remain.
- AE3. **Covers R14.** Given a locked method + parameters, when it is run on a new comparable dataset, then masks are produced with no interactive step.

---

## Success Criteria

- On representative data, the chosen method matches or beats the researcher's by-eye QC'd masks on recall (captures the dim foci) while keeping false positives negligible, so large datasets can be processed without manual QC.
- A downstream implementer (`/ce-plan`) can build this without inventing detector semantics, the registry interface, the scoring metric, or the trust protocol — all are specified here.

---

## Scope Boundaries

- Segmentation (Cellpose) is unchanged; the workflow operates on existing cell labels.
- The interactive QC path is not removed; this adds a headless alternative alongside it.
- SG/lysosome discrimination is out of scope (rare, negligible per orthogonal experiments).
- The ML detector (R9) is not required for v1; classical methods are raced first.
- No new GUI is required for routine operation (it is headless); the validation harness may be a CLI or notebook tool.
- Per-image confidence scoring is not the trust mechanism; optional outlier-flagging is deferred (see Outstanding Questions).
- Time-lapse handling beyond the existing per-frame behavior is not expanded in v1.

---

## Key Decisions

- Per-group independence retained: the user chose to keep the GMM/kmeans grouping as the calibration/background unit, preserving existing architecture and `/groups` + `/masks` storage.
- Recall-first objective with tolerance for rare lysosome false positives, grounded in orthogonal experimental confirmation that lysosomes are too few to affect results.
- Trust is established by one-time validation against hybrid ground truth, not per-image confidence — this is the mechanism that actually retires QC.
- The primary lever is detector choice, not just the background value: screenshots show Otsu's class-balance assumption is the root failure, so the registry spans true spot detectors, with background estimators as one orthogonal axis.
- Harness-first: build the pluggable registry + validation harness and race methods on real data rather than committing to one method up front (matches the user's stated intent to test multiple methods).

---

## Dependencies / Assumptions

- Reuses existing machinery: `region_and_donut_masks()` (`src/percell4/domain/analysis/_impl/_shared.py`), `estimate_bg_threshold()` and the donut `bg_mode` pattern (`src/percell4/domain/analysis/_impl/per_particle_donut.py`), `THRESHOLD_METHODS` (`src/percell4/domain/measure/thresholding.py`), `apply_threshold_headless()` (`src/percell4/workflows/phases.py`), and `grouper.py`.
- Assumes spot scale is approximately constant within a condition, which justifies dataset-level scale calibration. [assumption — validate against labeled fields]
- Assumes the existing QC-approved mask archive is available and trustworthy as a precision reference (its recall is treated as a lower bound, not the ceiling).
- à trous wavelet / white top-hat / h-maxima / LoG are expected to be available via scikit-image / scipy; exact APIs to be confirmed in planning. [Needs research]

---

## Outstanding Questions

### Resolve Before Planning

- (none — planning can proceed)

### Deferred to Planning

- [Affects R11][User decision] Labeling mechanism (e.g., a napari points layer) and how many fields/conditions constitute the exhaustive recall-ceiling set.
- [Affects R8][Needs research] Exact à trous / white top-hat / h-maxima / LoG APIs and parameterization in scikit-image / scipy.
- [Affects R12][Technical] Punctum-matching tolerance and how to aggregate recall/precision across groups and fields into a single comparable score.
- [Affects R7][Technical] Background-surface fitting method (interpolation vs rolling-ball vs low-order polynomial) and its stability when pass-1 foci are sparse.
- [Affects R14][User decision] Whether to add an optional outlier-flagging safety net (flag images whose statistics fall outside the validated regime for spot-checking) without reintroducing routine QC.

---

## Next Steps

-> `/ce-plan` for structured implementation planning
