---
title: Headless QC-Free Puncta Thresholding — Method & Validation
date: 2026-06-04
status: current
origin:
  - docs/brainstorms/2026-06-03-headless-grouped-thresholding-puncta-requirements.md
  - docs/plans/2026-06-03-002-feat-headless-puncta-thresholding-plan.md
---

# Headless QC-Free Puncta Thresholding — Method & Validation

This document describes the headless puncta-detection thresholding subsystem: why
it exists, the detection pipeline that runs in production, every pluggable method
it ships, the validation harness that qualifies a method, and the empirical
results from the first real validation (`Dish 2 TAOK2 KO 60min As + Noco`, mNG
channel) — including the method that passed the stability-gated lock.

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
   labeled ground truth and locks a qualifying winner. Validate once, lock the
   recipe, run headless = QC retired for that condition.

```
GROUND TRUTH (per condition)                 PRODUCTION (every dataset, headless)
────────────────────────────                 ────────────────────────────────────
exhaustive napari points (Tier A) ─┐          per intensity group g:
old QC mask (Tier B, recall floor) ─┤            isolate residual to g (out-of-group -> NaN)
                                    ▼            pass-1 seed detect (once)  ──┐
              VALIDATION HARNESS                 background fallback ladder ◄─┘
        race detector × bg × k × t_rel × tol      signal-presence gate (empty, not accept-all)
        score per-punctum recall / precision      pass-2 detect at calibrated scale
        stability probe + lock criterion          size filter
                    │                             union per group  →  /masks (0/1 uint8)
          lock recipe (JSON) ──────────────────►  via ThresholdingRound.puncta
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
| `mad` | `bg = median`, `sigma = 1.4826·MAD`. | Robust scalar; cheap. Available. |
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
| `log` | `threshold_rel` | Laplacian-of-Gaussian multiscale blob detection (`skimage.feature.blob_log`); each `(y,x,σ)` painted to a disk of radius `⌈σ√2⌉`. | **Multiscale default. The locked method (§6).** `threshold_rel` is the recall knob (lower = more recall). |
| `dog` | `threshold_rel` | Difference-of-Gaussians blobs (`blob_dog`), painted likewise. | Faster cousin of `log`; **raced in validation** (lost to `log`). |
| `white-tophat` | `radius`, `k` | `white_tophat(disk(r))` residual thresholded at `k·MAD`. | Implicit background removal; available, not yet raced. |
| `h-maxima` | `h = k·σ` | `h_maxima` on a LoG-prefiltered residual — regional maxima with prominence ≥ `h`. | Directly encodes "a bump that clears its surroundings by a margin." Available, not yet raced. |
| `bg-k-sigma` | `k` | `residual > k·σ`. | Simple statistical threshold on the corrected residual. Available. |
| `otsu` | — | Otsu on the in-group residual. | Baseline / regression comparator. |
| `atrous-wavelet` | — | *(stub)* hand-rolled B3-spline à-trous multiscale-product spot detection (Olivo-Marin 2002). | **Not implemented** — raises `NotImplementedError`; deferred until library detectors are shown insufficient. |

The morphological top-hat spans both axes (it is simultaneously a background
subtractor and a detector); that is acceptable and it lives on the detector axis.

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

### 5.3 Stability probe & lock criterion

Each candidate is re-scored with the pass-1 seed `k` perturbed by `± δ`; recall
and precision must each move by `≤ band` to be `stable`. The harness **locks**
the candidate with the best `F_β` that also clears:

- `recall ≥ Tier-B recall floor`, **and**
- `precision ≥ precision_floor` (default 0.90), **and**
- the stability probe.

If nothing qualifies, it reports "keep interactive QC for this condition." The
locked `PunctaDetectorSettings` is written as JSON.

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

## 6. Validation results — `Dish 2 TAOK2 KO 60min As + Noco`, mNG channel

**Ground truth:** 4,664 foci exhaustively labeled by eye on the mNG channel
(`scripts/label_foci.py`). Cells from `/labels/cp_mask`.
**Tier-B recall floor:** the existing `/masks/SG_mask` recovers **0.670** of the
4,664 labels at `tol = 4 px`. (That the hand-QC mask only reaches 67% confirms
the labels include many dim foci the old method missed — the under-capture story.)
**Fixed parameters:** `tol = 4`, `scale = (1.0, 4.0)`, `min_spot_px = 2`,
background `gaussian-peak`, gate `k = 2.5`, precision floor 0.90, `F_β` β = 2,
stability probe `k ± 0.5`, band 0.10.

### 6.1 Initial sanity check

`log`, `threshold_rel = 0.1` → recall ≈ 0.46–0.50, precision ≈ 0.99. Confirmed
the harness runs end-to-end on the full 2048×2048 field and that the default
`threshold_rel` is far too conservative (very high precision, low recall).

### 6.2 Coarse race — `log` vs `dog` × `threshold_rel`

| detector | `threshold_rel` | recall | precision | F_β | stable |
|---|---:|---:|---:|---:|:--:|
| log | 0.02 | 0.973 | 0.542 | 0.839 | ✅ |
| log | 0.04 | 0.895 | 0.842 | 0.883 | ✅ |
| log | 0.07 | 0.669 | 0.968 | 0.713 | ✅ |
| log | 0.10 | 0.501 | 0.986 | 0.555 | ✅ |
| dog | 0.02 | 0.950 | 0.594 | 0.848 | ✅ |
| dog | 0.04 | 0.839 | 0.862 | 0.844 | ✅ |
| dog | 0.07 | 0.613 | 0.970 | 0.661 | ✅ |
| dog | 0.10 | 0.457 | 0.985 | 0.512 | ✅ |

`log` dominates `dog` (higher recall at comparable precision) at every setting,
and the precision-≥0.90 frontier sits between `threshold_rel` 0.04 and 0.07.

### 6.3 Refined race — `log`, `threshold_rel` 0.045–0.065

| detector | `threshold_rel` | recall | precision | F_β | stable | qualifies (recall≥0.670 ∧ precision≥0.90 ∧ stable) |
|---|---:|---:|---:|---:|:--:|:--:|
| log | 0.045 | 0.856 | 0.879 | 0.861 | ✅ | ✗ (precision < 0.90) |
| **log** | **0.050** | **0.824** | **0.910** | **0.840** | ✅ | **✓ — LOCKED** |
| log | 0.055 | 0.780 | 0.930 | 0.806 | ✅ | ✓ |
| log | 0.060 | 0.739 | 0.945 | 0.773 | ✅ | ✓ |
| log | 0.065 | 0.700 | 0.955 | 0.740 | ✅ | ✓ |

### 6.4 The method that passed — locked recipe

**`log` + `gaussian-peak`, `threshold_rel = 0.05`** — the highest-recall setting
that still clears the 0.90 precision floor, and stable to the pass-1-`k`
perturbation:

| | recall | precision | stable |
|---|---:|---:|:--:|
| old `SG_mask` (hand QC) | 0.670 | — | — |
| **locked automated method** | **0.824** | **0.910** | ✅ |

The automated, fully-headless method captures **~82% of every labeled focus vs
~67% for the laborious hand mask** (+~23 points of recall — the dim foci the old
method missed) at **91% precision**, with no per-image QC. For this condition,
the lock-once trust model is satisfied.

`locked_SG.json`:

```json
{
  "detector_name": "log",
  "seed_detector_name": "log",
  "background_estimator_name": "gaussian-peak",
  "detector_params": { "k": 2.5, "threshold_rel": 0.05 },
  "seed_params": { "k": 2.5 },
  "min_spot_px": 2,
  "max_spot_px": null,
  "spot_scale_prior": [1.0, 4.0]
}
```

If a different operating point is wanted, the frontier above is explicit: e.g.
`threshold_rel = 0.045` reaches 0.856 recall at 0.879 precision (lower
`--precision-floor` to allow it), or 0.055–0.065 trade recall for precision up to
0.955.

---

## 7. Using the locked recipe

Put the locked fields on a round's `puncta`:

```python
ThresholdingRound(
    name="SG", channel="mNG", metric="mean_intensity",
    algorithm=ThresholdAlgorithm.KMEANS, gaussian_sigma=1.0,
    puncta=PunctaDetectorSettings(
        detector_name="log", seed_detector_name="log",
        background_estimator_name="gaussian-peak",
        detector_params={"k": 2.5, "threshold_rel": 0.05},
        min_spot_px=2, spot_scale_prior=(1.0, 4.0),
    ),
)
```

That round runs fully headless in the batch single-cell workflow
(`interactive_qc=False`), writing `/masks/SG` as a `{0,1}` uint8 mask, with no
threshold-QC step. The settings round-trip through `run_config.json`.

---

## 8. Trust model & caveats

- **Lock-once, per condition.** A recipe is qualified on labeled data and then
  trusted headless on comparable datasets. "Comparable" is currently a *scale*
  regime check (bounded refinement); SNR / background-structure drift beyond
  scale is not yet caught — that is the deferred outlier-flag safety net.
- **Single-field lock.** The `threshold_rel = 0.05` result was locked on one
  field of one dataset. Before retiring QC for the whole condition, validate the
  same recipe on 1–2 more `TAOK2 KO 60min As+Noco` dishes (label them with
  `scripts/label_foci.py`, re-run the harness) to confirm it generalizes.
- **Deferred, evidence-gated.** `donut-surface` (RBF estimator), `atrous-wavelet`
  (hand-rolled wavelet detector), and the ML pixel-classifier + full keep-QC /
  outlier-flag safety net ship as stubs / future work — to be implemented only if
  the library detectors and the `gaussian-peak` rung prove insufficient.
- **Not yet empirically raced** on this dataset: `white-tophat`, `h-maxima`,
  `bg-k-sigma`, and the donut / `mad` / `percentile` / `rolling-ball` background
  estimators. They are implemented and available; the harness can race them by
  adding them to `--detectors` / `--backgrounds`.

---

## 9. References

- Requirements: `docs/brainstorms/2026-06-03-headless-grouped-thresholding-puncta-requirements.md`
- Plan: `docs/plans/2026-06-03-002-feat-headless-puncta-thresholding-plan.md`
- Registries & pipeline: `src/percell4/domain/measure/{puncta_names,bg_estimators,puncta_detectors,atrous,puncta_pipeline,puncta_scoring}.py`
- Config & dispatch: `src/percell4/workflows/models.py` (`PunctaDetectorSettings`), `src/percell4/workflows/phases.py` (`_apply_puncta_groups`, `_apply_threshold_frame`)
- Harness & CLI: `src/percell4/workflows/puncta_validation.py`, `src/percell4/interfaces/cli/batch_validate_puncta.py`
- Labeling helper: `scripts/label_foci.py`
- External: Olivo-Marin (2002), à-trous multiscale products; Smal et al. (2010), spot-detector comparison; ISBI Particle Tracking Challenge detection metrics.
