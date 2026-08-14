---
title: Headless QC-Free Puncta Thresholding — Method & Validation
date: 2026-06-04
status: current
origin:
  # Requirements and plan documents; on the `development` branch.
  - 2026-06-03-headless-grouped-thresholding-puncta-requirements.md
  - 2026-06-03-002-feat-headless-puncta-thresholding-plan.md
---

# Headless QC-Free Puncta Thresholding — Method & Validation

This document describes the headless puncta-detection thresholding subsystem: why
it exists, the detection pipeline that runs in production, every pluggable method
it ships, the validation harness that guardrails a method, and how the production
method was selected on the first real dataset (`Dish 2 TAOK2 KO 60min As + Noco`,
mNG channel) — by **visual spot-test**, the same judgment the manual ROI-QC step
encoded, against two hard requirements: pixel-accurate granule shapes and **zero
dilute-phase pickup**.

---

## 1. Problem & motivation

PerCell4's grouped thresholding clusters cells by intensity and applies a
per-group Otsu threshold. To be usable it required an interactive per-group
threshold-QC step (`src/percell4/gui/threshold_qc.py`) — laborious and
prohibitive at scale.

The root cause is that **per-group Otsu over a whole cell is the wrong detector
for small puncta.** Otsu chooses the threshold that best splits the histogram it
is given, implicitly assuming the two pixel classes are roughly balanced. Over a
whole cell the histogram is dominated by whichever population is largest, so:

- where bright foci dominate, the split lands high and **dim foci are missed**
  (under-capture — the common case), and
- where diffuse haze dominates, the split lands low and **haze is included**
  (over-capture — the rarer case).

The manual ROI fix worked because drawing a small box simultaneously supplied
local *scale*, local *background*, and local *class balance*. The targets are
small, numerous stress-granule (SG) foci on a spatially-varying background; foci
sizes vary widely within a field. The objective is **recall** — capture every
punctum — at a controlled precision, so the laborious QC step can be retired for
a condition once a method is proven good enough on labeled data.

---

## 2. Approach overview

Four pieces, all building on existing machinery, with the legacy Otsu path left
byte-identical:

1. **Two orthogonal pluggable registries** (pure domain) — *background
   estimators* and *detectors* — using the same flat-dict idiom as the existing
   `THRESHOLD_METHODS`.
2. **A two-pass per-group detection pipeline** that replaces per-group Otsu when
   a round opts in, dispatched from `_apply_threshold_frame`.
3. **A per-dataset spot-scale calibration** that bounds detector scale to a
   validated range.
4. **A dev-time validation harness** that scores candidate methods against
   labeled ground truth (centroid recall / precision). This is a *guardrail*,
   not the selector: the centroid metric is blind to granule shape and to
   dilute-phase pickup, which are the actual acceptance criteria, so final
   selection is a visual spot-test. Choose the recipe once, lock it, run headless
   = QC retired for that condition.

```
GROUND TRUTH (per condition)                 PRODUCTION (every dataset, headless)
────────────────────────────                 ────────────────────────────────────
exhaustive napari points (Tier A) ─┐          per intensity group g:
old QC mask (Tier B, recall floor) ─┤            isolate residual to g (out-of-group -> NaN)
                                    ▼            pass-1 seed detect (once)  ──┐
         HARNESS (guardrail) + EYE               background fallback ladder ◄─┘
        sweep detector × bg × window × k          signal-presence gate (empty, not accept-all)
        centroid recall/precision (narrows)       pass-2 detect at calibrated scale
        visual spot-test: shape + zero dilute      size filter
                    │                             union per group  →  /masks (0/1 uint8)
         choose recipe (JSON) ──────────────────►  via ThresholdingRound.puncta
```

Code: `src/percell4/domain/measure/{puncta_names,bg_estimators,puncta_detectors,atrous,puncta_pipeline,puncta_scoring}.py`,
`src/percell4/workflows/{models,phases,puncta_validation}.py`,
`src/percell4/interfaces/cli/batch_validate_puncta.py`.

---

## 3. The detection pipeline (what runs headless)

`detect_two_pass(smoothed_image, group_label_mask, settings, scale_range=None,
seeds=None) -> uint8` in `src/percell4/domain/measure/puncta_pipeline.py`, called
per intensity group from `_apply_threshold_frame`
(`src/percell4/workflows/phases.py`). Steps, in order:

1. **Per-group isolation.** Out-of-group pixels are set to `NaN` (on a bbox
   crop). This is mandatory for the multiscale default: `blob_log`/`blob_dog`
   `threshold_rel` and the LoG response normalize against the array maximum, so a
   full-field residual would let a bright group suppress a dim group's foci — the
   exact cross-group imbalance the per-group design eliminates.
2. **Pass 1 (once).** A deliberately-permissive `seed_detector` runs on a
   bootstrap residual (`smoothed − robust_mu`, where `(mu, sigma)` come from
   `estimate_bg_threshold`) to seed background sampling. Seeds are cached and
   reused so pass-1 never runs twice.
3. **Background estimate — fallback ladder.** The configured estimator, then the
   robust `gaussian-peak` rung (uses the background *mode* `mu`, robust to a
   bright-foci tail — never a plain mean over foci-contaminated pixels), then an
   empty mask. This matters because for under-capture fields, sparse pass-1 seeds
   is the *expected* state.
4. **Signal-presence gate.** Short-circuits to an empty mask when the brightest
   in-group residual fails `k·sigma` — i.e. nothing stands out at all (never
   accept-all). Because it only fires when the *maximum* fails the floor, it
   never vetoes a detection the active detector would otherwise produce.
5. **Pass 2.** The configured detector on the per-group-isolated residual at the
   (possibly refined) `scale_range`.
6. **Size filter.** Drop connected components outside `[min_spot_px,
   max_spot_px]`.
7. **Union + binarize.** Per-group masks union via `np.maximum`; the result is
   binarized to `{0,1} uint8` (the store does not binarize). `/groups` stays
   exactly `["label", "group_<channel>_<metric>"]`; all provenance goes to
   `RunLog`.

**Spot-scale calibration** (`calibrate_scale_range`, applied per frame in
`_apply_puncta_groups`): pass-1 seed sizes are pooled to derive a
`(min_sigma, max_sigma)` range by **bounded, narrow-only** refinement of the
locked prior — it may tighten within the prior but never expand beyond it; an
out-of-bracket candidate is clamped to the prior with a `RunLog` warning. With
fewer than `n_calib` seeds the prior is retained unchanged. Pass-1 results stay
in memory (no HDF5 round-trip).

The legacy per-group Otsu path is unchanged and selected whenever a round has no
`PunctaDetectorSettings` (or names the `otsu` detector).

---

## 4. The pluggable methods

Both axes are flat `dict[str, callable]` registries whose keys are the single
source of truth in `src/percell4/domain/measure/puncta_names.py`
(`DETECTOR_NAMES` / `BG_ESTIMATOR_NAMES`), validated at config-construction time.
**Availability is marked below; §6 records which were empirically raced.**

### 4.1 Background estimators (axis A) — `bg_estimators.py`

Each returns a typed `BackgroundEstimate(residual, sigma, is_empty)` — the
background-corrected residual image, an optional noise scale for the `k·σ` gate,
and an `is_empty` flag that drives the fallback ladder.

| Name | What it does | Notes |
|---|---|---|
| `gaussian-peak` | Fits the background peak (`estimate_bg_threshold`) and subtracts its mode `mu`; exposes `sigma`. | Robust to a bright-foci tail. **Used in validation.** The rung-2 fallback for all methods. Reuses only `(mu, sigma)`, never the baked-in threshold (avoids double-`k`). |
| `mad` | `bg = median`, `sigma = 1.4826·MAD`. | Robust scalar; cheap. **Default of the interactive whole-frame Adaptive Clip module** (matches the ImageJ reference macro; resists the black-background histogram spike that collapses `gaussian-peak` on a whole frame). |
| `stddev` | `bg = mean`, `sigma = std` (non-robust). | The non-robust counterpart of `mad`; mirrors the ImageJ macro's `stddev` noise option. Pulled up by a bright-foci tail. Available. |
| `percentile` | `bg = p-th percentile` (default 50). | Available. |
| `donut-median` / `donut-mean` | Per-seed Euclidean-distance donut rings (reusing `region_and_donut_masks`), aggregated by median / mean. | A declared **float fork** of the per-particle-donut analysis (no integer rounding, no Cap-specific zero exclusion). Available. |
| `rolling-ball` | `skimage.restoration.rolling_ball(..., nansafe=True)` surface subtraction. | For smoothly-varying background. Available. |
| `donut-surface` | *(stub)* RBF thin-plate-spline surface fit to donut samples. | **Not implemented** — raises `NotImplementedError`; deferred until evidence the `gaussian-peak` rung is insufficient. The ladder drops past it. |

### 4.2 Detectors (axis B) — `puncta_detectors.py` (+ `atrous.py`)

Uniform signature `detector(residual, group_mask, sigma, params) -> uint8{0,1}`;
each fills NaN before any morphology/convolution then restricts to
`group_mask & isfinite`, so per-group isolation holds and output is never 255.
Deterministic (no RNG).

| Name | Knob | What it does | Notes |
|---|---|---|---|
| `adaptive` | `window_px`, `k` | Local adaptive threshold: `threshold_local` (Gaussian-weighted, `block_size = window_px`) gives a per-pixel local background; a pixel fires iff `residual > local_bg + k·σ`. | **Production / locked method (§6).** Automates "circle a focus and threshold it vs its surroundings": the cut floats with the local dilute level, so locally-flat dilute phase never passes while compact foci do — with **true pixel boundaries**. Small `window_px` starves extended structures (streaks); `k` is the contrast floor (the recall ↔ dilute-rejection dial). The local fix for `bg-k-sigma`'s dilute pickup. |
| `log` | `threshold_rel` | Laplacian-of-Gaussian multiscale blobs (`blob_log`); each `(y,x,σ)` painted to a disk of radius `⌈σ√2⌉`. | High centroid recall/precision, but **paints uniform disks — wrong granule shape and size. Rejected**: the downstream per-particle morphology needs pixel-accurate, irregular boundaries (stress granules are not round). |
| `dog` | `threshold_rel` | Difference-of-Gaussians blobs (`blob_dog`), painted likewise. | Same disk-painting shape problem as `log`, and lost to it on centroid recall anyway. Rejected. |
| `bg-k-sigma` | `k` | `residual > k·σ` (one global per-group threshold). | True pixel shapes, high recall, but a **global** floor — picks up dilute phase wherever background varies within the group. Superseded by `adaptive`'s local floor. |
| `otsu-floored` | `floor_k` | NaN-out pixels at/below `floor_k·σ`, then Otsu on the survivors. | Removing the background bulk frees Otsu to drop toward the foci, but Otsu's split still caps dim-foci recall. Tested, not selected. |
| `local-otsu` | `window_r`, `k` | Per-pixel rank-Otsu in a sliding disk (`window_r`), with a `k·σ` group floor. | Windowed true-Otsu; bloats on class-imbalanced neighborhoods. Tested, not selected. |
| `refine-otsu` | `expand_px` | Seed via global Otsu, then re-Otsu inside each **particle** dilated by `expand_px`. | Per-particle automation of the manual ROI; Otsu's class-balance assumption still bloats. Tested, not selected. |
| `refine-cell-otsu` | `unit`, `expand_px` | Seed via global Otsu, then re-Otsu inside the foci-neighborhood ROI of each **cell/group** and apply that one threshold unit-wide. | Whole-cell ROI automation; closest Otsu variant to the manual workflow but the Otsu ceiling on dim foci persists. Tested, not selected. |
| `white-tophat` | `r`, `k` | `white_tophat(disk(r))` residual thresholded at `k·MAD`. | Implicit background removal; available, not selected. |
| `h-maxima` | `h = k·σ` | `h_maxima` on a LoG-prefiltered residual — regional maxima with prominence ≥ `h`. | Encodes "a bump that clears its surroundings by a margin." Available, not selected. |
| `otsu` | — | Otsu on the in-group residual. | Baseline / regression comparator — the behavior the puncta work replaces. |
| `atrous-wavelet` | — | *(stub)* hand-rolled B3-spline à-trous multiscale-product spot detection (Olivo-Marin 2002). | **Not implemented** — raises `NotImplementedError`; deferred until library detectors are shown insufficient. |

The morphological top-hat spans both axes (it is simultaneously a background
subtractor and a detector); that is acceptable and it lives on the detector axis.

**Why a pixel-threshold detector, not a blob detector.** `log`/`dog` score well
on centroid recall/precision but paint every detection as an idealized disk —
the centroid is right, the morphology is wrong. Because each granule's true,
irregular shape feeds the downstream per-particle measurement, the family that
won is the per-pixel local-threshold one (`adaptive`), which traces the actual
boundary. Otsu-family refinements (`otsu-floored`, `local-otsu`, `refine-*`) keep
true shapes but inherit Otsu's class-balance ceiling and cannot reach the dimmest
foci. `adaptive`'s `local_bg + k·σ` test has no such ceiling.

---

## 5. Validation harness & scoring

### 5.1 Ground truth (hybrid)

- **Tier A — exhaustive napari-point labels.** Every focus (including dim ones)
  clicked on the detection channel and exported as a `y,x` CSV (see
  `scripts/label_foci.py`). This is the recall ceiling and the **only** source of
  precision.
- **Tier B — an existing approved `/masks/<name>`.** Scored against Tier A at the
  same tolerance to give a **recall floor**: a candidate must match or beat it.
  Tier B is never the precision oracle (old masks under-count dim foci).

### 5.2 Per-punctum scoring — `puncta_scoring.py`

Centroids come from `regionprops(label(mask)).centroid` (optionally after a
small morphological closing so fragments of one granule within scale merge). The
**two-phase match** (`match_detections`):

1. **Footprint credit.** Every GT point falling inside a detected component's
   footprint is a recall TP, and that component is marked as covering ≥1 GT
   (handles merged / touching foci: one component over *k* GT = *k* recall hits).
   Credited GT are removed from the pool.
2. **Bipartite on the remainder.** `scipy.optimize.linear_sum_assignment` matches
   remaining detection centroids to remaining GT; assigned pairs beyond `tol` are
   dropped (boundary `≤ tol`).

**Precision is counted at the detected-component level, recall at the GT level.**
A component covering *k* GT contributes *k* to recall but only **one** unit to
the precision denominator, so flooding a cell with a few giant blobs cannot
inflate precision toward 1.0. Counts are micro-averaged across fields, then
`recall`, `precision`, and `F_β` (β = 2, recall-weighted) are computed.

### 5.3 Stability probe & the role of the harness

Each candidate is re-scored with the pass-1 seed `k` perturbed by `± δ`; recall
and precision must each move by `≤ band` to be `stable`. The harness ranks by
`F_β` subject to `recall ≥ Tier-B recall floor`, `precision ≥ precision_floor`,
and the stability probe.

**The harness is a guardrail, not the selector.** Its score is centroid-based —
it confirms a method finds foci in roughly the right places and is stable, but it
is blind to the two things that actually decide acceptance: whether each granule's
**pixel shape** is faithful (a disk-painting detector can score perfectly and
still be unusable downstream) and whether any **dilute phase** is picked up. So
the harness narrows the field, and the final operating point is chosen by a
**visual spot-test** over the candidate `/masks` (see §6). The chosen
`PunctaDetectorSettings` is written as JSON.

### 5.4 Running it

```
percell4-batch-validate-puncta <dataset.h5> \
    --gt-dir labels/ --channel <ch> --seg-name <labels> \
    --tier-b-mask <old_mask> \
    --detectors log dog --backgrounds gaussian-peak \
    --threshold-rel 0.04 0.05 0.06 --tol 4 \
    --scale-min 1.0 --scale-max 4.0 --out locked.json
```

(Module form `python -m percell4.interfaces.cli.batch_validate_puncta …` works
without reinstalling.) `--threshold-rel` is the `log`/`dog` recall knob; `--k`
drives the gate / `bg-k-sigma` / `h-maxima`. The Tier-B floor is scored at the
first `--tol` value; keep `--tol` fixed for an apples-to-apples lock.

---

## 6. Selection — `Dish 2 TAOK2 KO 60min As + Noco`, mNG channel

**Ground truth (guardrail):** 4,664 foci exhaustively labeled by eye on the mNG
channel (`scripts/label_foci.py`); cells from `/labels/cp_mask`. The centroid
harness confirmed the multiscale detectors find foci in the right places and that
the per-group isolation works end-to-end on the full 2048×2048 field. But the
centroid score cannot see shape or dilute pickup, so it was used only to narrow,
not to choose (§5.3).

### 6.1 Why the centroid race did not pick the method

`log`/`dog` scored well on centroid recall/precision, but every detection is
painted as a uniform disk of radius `⌈σ√2⌉`: the centroid lands right while the
**shape and size are wrong**. Because each granule's true (irregular) boundary
feeds the downstream per-particle measurement, a disk-painting detector is
unusable however good its centroid numbers — confirmed by eye, the `log` masks
made every granule the wrong size and shape. Selection therefore moved to the
per-pixel `adaptive` detector and a visual spot-test.

### 6.2 The selection criterion (by eye)

Each candidate `/masks` layer was overlaid on the mNG channel and compared
against the manual `/masks/SG_mask` (`scripts/compare_masks.py --solo`), walking
the contrast floor from permissive toward conservative and stopping at the
**first mask with zero dilute-phase pickup** while still retaining the small dim
puncta. The constraints, in priority order:

1. **Hard: zero dilute phase.** Non-negotiable. Oversampling is unacceptable;
   undersampling the dimmest foci is acceptable.
2. **Keep the small/dim puncta** — the differentiator over existing SG methods.
3. **Pixel-accurate, irregular shapes** for the per-particle analysis.
4. **Bar: match or beat the manual mask.**

### 6.3 The `adaptive` sweep (window `w`, contrast `k`)

Foci = connected components; mean px = mean component area. Listed
most-permissive → most-conservative:

| recipe | `window_px` | `k` | px | foci | mean px | by eye |
|---|---:|---:|---:|---:|---:|---|
| `adapt_w15` | 15 | 2.00 | 83,948 | 4,698 | 17.9 | catches everything **incl. dilute streaks** |
| **`aw15_k225`** | **15** | **2.25** | **75,416** | **4,247** | **17.8** | **first fully dilute-free — SELECTED** |
| `aw15_k25` | 15 | 2.50 | 68,334 | 3,921 | 17.4 | clean; begins shedding dim foci |
| `aw15_k275` | 15 | 2.75 | 62,296 | 3,622 | 17.2 | clean; more undersampling |
| `aw15_k30` | 15 | 3.00 | 56,999 | 3,344 | 17.0 | clean; further undersampling |
| `aw11_k20` | 11 | 2.00 | 55,314 | 3,651 | 15.2 | smaller window — tightest shapes |
| `aw11_k225` | 11 | 2.25 | 48,934 | 3,308 | 14.8 | clean (small-window alternative) |
| `aw9_k20` | 9 | 2.00 | 37,895 | 2,890 | 13.1 | clean; most undersampled |
| `SG_mask` (manual) | — | — | 61,929 | 3,570 | 17.3 | the bar |

Two levers: **smaller `window_px`** makes extended structures (streaks, dilute
patches) their own local background so they fail the local test, preferentially
starving dilute without a global contrast cost; **higher `k`** raises the contrast
floor uniformly, removing the faintest pickups (dilute first, then dim granules).

### 6.4 The selected recipe

**`adaptive` + `gaussian-peak`, `window_px = 15`, `k = 2.25`** (`aw15_k225`) — the
least-aggressive dial-back of the permissive `adapt_w15` that the eye confirmed
has **zero dilute phase**, while keeping the small dim puncta:

| | foci | mean px | dilute (by eye) |
|---|---:|---:|:--:|
| old `SG_mask` (hand QC) | 3,570 | 17.3 | none |
| **selected `adaptive` recipe** | **4,247** | **17.8** | **none** |

It captures **~19% more granules than the laborious hand mask** (the small dim
foci the old method missed) at faithful pixel shapes (mean 17.8 ≈ 17.3 px), fully
headless. The production mask is written as `/masks/SG_auto`.

`locked_SG.json`:

```json
{
  "detector_name": "adaptive",
  "seed_detector_name": "otsu",
  "background_estimator_name": "gaussian-peak",
  "detector_params": { "window_px": 15, "k": 2.25 },
  "seed_params": { "k": 2.5 },
  "min_spot_px": 3,
  "max_spot_px": null,
  "spot_scale_prior": [1.0, 4.0]
}
```

To trade recall for an even cleaner margin, the sweep is explicit: raise `k`
(15/2.5 → 15/3.0) or shrink the window (11/2.25, 9/2.0) — each undersamples the
dimmest foci further while staying dilute-free.

---

## 7. Using the locked recipe

Put the locked fields on a round's `puncta`:

```python
ThresholdingRound(
    name="SG", channel="mNG", metric="mean_intensity",
    algorithm=ThresholdAlgorithm.KMEANS, gaussian_sigma=1.0,
    puncta=PunctaDetectorSettings(
        detector_name="adaptive", seed_detector_name="otsu",
        background_estimator_name="gaussian-peak",
        detector_params={"window_px": 15, "k": 2.25},
        min_spot_px=3, spot_scale_prior=(1.0, 4.0),
    ),
)
```

That round runs fully headless in the batch single-cell workflow
(`interactive_qc=False`), writing `/masks/SG` as a `{0,1}` uint8 mask, with no
threshold-QC step. The settings round-trip through `run_config.json`.

---

## 8. Trust model & caveats

- **Choose-once, per condition.** A recipe is selected on labeled data and then
  trusted headless on comparable datasets. "Comparable" is currently a *scale*
  regime check (bounded refinement); SNR / background-structure drift beyond
  scale is not yet caught — that is the deferred outlier-flag safety net.
- **By-eye selection on a single field.** `window_px = 15, k = 2.25` was chosen by
  visual spot-test on one field of one dataset against the manual `SG_mask`. Before
  retiring QC for the whole condition, eyeball the same recipe on 1–2 more `TAOK2
  KO 60min As+Noco` dishes (generate with `scripts/gen_puncta_masks.py`, compare
  with `scripts/compare_masks.py`) to confirm it stays dilute-free and keeps the
  dim foci.
- **The centroid harness is a guardrail, not the judge.** It cannot see granule
  shape or dilute-phase pickup — the two acceptance criteria — so a high `F_β` is
  necessary-but-insufficient. Trust the overlay, not the number.
- **Deferred, evidence-gated.** `donut-surface` (RBF estimator), `atrous-wavelet`
  (hand-rolled wavelet detector), and the ML pixel-classifier + full keep-QC /
  outlier-flag safety net ship as stubs / future work — to be implemented only if
  the shipped detectors prove insufficient.
- **Available but not selected** on this dataset: `log`/`dog` (disk-shape,
  rejected), the Otsu-family refinements (`otsu-floored`, `local-otsu`,
  `refine-otsu`, `refine-cell-otsu`), `white-tophat`, `h-maxima`, `bg-k-sigma`,
  and the donut / `mad` / `percentile` / `rolling-ball` background estimators.
  All are implemented; re-race them via `--detectors` / `--backgrounds`.

---

## 9. References

- Requirements and plan: `2026-06-03-headless-grouped-thresholding-puncta-requirements.md`
  and `2026-06-03-002-feat-headless-puncta-thresholding-plan.md`, both on the
  `development` branch
- Registries & pipeline: `src/percell4/domain/measure/{puncta_names,bg_estimators,puncta_detectors,atrous,puncta_pipeline,puncta_scoring}.py`
- Config & dispatch: `src/percell4/workflows/models.py` (`PunctaDetectorSettings`), `src/percell4/workflows/phases.py` (`_apply_puncta_groups`, `_apply_threshold_frame`)
- Harness & CLI: `src/percell4/workflows/puncta_validation.py`, `src/percell4/interfaces/cli/batch_validate_puncta.py`
- Labeling helper: `scripts/label_foci.py`
- External: Olivo-Marin (2002), à-trous multiscale products; Smal et al. (2010), spot-detector comparison; ISBI Particle Tracking Challenge detection metrics.
