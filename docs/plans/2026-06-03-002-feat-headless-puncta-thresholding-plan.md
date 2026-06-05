---
title: "feat: Headless QC-free grouped thresholding for puncta detection"
type: feat
status: active
date: 2026-06-03
deepened: 2026-06-03
origin: docs/brainstorms/2026-06-03-headless-grouped-thresholding-puncta-requirements.md
---

# Headless QC-Free Grouped Thresholding for Puncta Detection

## Overview

Replace the per-group whole-cell Otsu step in grouped thresholding with a **pluggable, two-pass, multiscale spot detector** that runs fully headless, plus a **dev-time validation harness** that scores candidate methods against labeled ground truth so a method can be qualified once and then trusted to run without per-image QC.

The work spans pieces that all build on existing PerCell4 machinery:

1. Two interchangeable **registries** in the pure domain layer — *background estimators* and *detectors* — using the same flat-dict registry **idiom** as `THRESHOLD_METHODS` (with distinct, documented per-axis signatures).
2. A **pure two-pass per-group pipeline** (`domain/measure/puncta_pipeline.py`) wired into `_apply_threshold_frame` (`src/percell4/workflows/phases.py`) via a one-line dispatch; `apply_threshold_headless`, `threshold_compute_one`, and the workflow runner stay byte-identical.
3. A **validation harness** that ingests hybrid ground truth (existing QC masks + exhaustive napari-point labels), scores per-punctum recall/precision via a two-phase footprint+bipartite match, races the registry across a parameter grid, and locks the winning method+parameters.

The objective is **recall** — capture every stress-granule punctum — with a precision floor, retiring the laborious interactive threshold-QC step for any experimental condition where a method qualifies *and stays within its validated scale regime*.

---

## Problem Frame

PerCell4's grouped thresholding requires interactive per-group threshold QC (`src/percell4/gui/threshold_qc.py`) to be usable, which is prohibitive at scale. The root cause (verified against the origin screenshots): per-group Otsu over a whole cell assumes balanced foreground/background classes, so the threshold is dragged by whichever pixel population dominates — too high where bright foci dominate (under-capture, common) or too low where diffuse haze dominates (over-capture, rare). The manual ROI fix works because it simultaneously supplies local scale, local background, and local class balance.

Targets are small, numerous stress-granule (SG) foci on a spatially-varying background; foci sizes **vary widely within a field**. A focus is "real" when it is a compact bump above both the cell's true background and its local surroundings. Rare lysosome false positives are tolerated. The user has eye-confirmed ground truth and a large archive of QC-approved masks, which makes it possible to *measure* whether an automated method is good enough — the basis for retiring QC (see origin: `docs/brainstorms/2026-06-03-headless-grouped-thresholding-puncta-requirements.md`).

---

## Requirements Trace

**Production headless detection workflow**
- R1. Fully-headless thresholding mode producing a binary puncta mask per group, combined by union, identical in shape/dtype to today's grouped-threshold output. → U1, U4
- R2. Two-pass per-group structure (pass 1 coarse seed → calibrate/estimate-subtract background → pass 2 final detect → union). → U4, U6
- R3. Calibration/background estimation per intensity group; spot scale calibrated once per dataset. → U4, U6
- R4. Optimize for recall, accepting negligible rare false positives. → U3, U5
- R5. Registry of interchangeable detection methods selectable by name. → U3 (the background-estimator registry is traced by R6/R7)
- R6. Two orthogonal axes — background estimators and detectors/threshold-setters. → U2, U3
- R7. Seed background estimators (donut-median/-mean, gaussian-peak, percentile, MAD, background-surface). → U2
- R8. Seed detectors (bg+k·σ, white top-hat, à-trous wavelet, LoG/DoG, h-maxima, Otsu baseline). → U3, U9
- R9. ML pixel classifier as a future, held-in-reserve registry entry. → U8 (deferred)
- R10. Dev-time harness runs any registered method across a parameter grid and scores it. → U5
- R11. Hybrid ground truth — existing QC masks (precision/regression reference) + exhaustively point-labeled fields (recall ceiling). → U5
- R12. Per-punctum location-matched scoring (recall, precision, recall-weighted summary). → U5
- R13. Per-method/per-parameter score reporting to select a winner. → U5
- R14. Trust established once, globally: a validated method+parameters is locked and applied headless to comparable data — where "comparable" means within the validated scale regime — with no per-image review. → U5, U6
- R15. Output masks compatible with downstream consumers (per-particle donut, multichannel, dilute-phase) unchanged. → U4
- R16. Integrate into the existing batch workflow (`WorkflowConfig` / runner phases) and `/masks` + `/groups` storage. → U1, U4, U7

**Origin actors:** A1 (Researcher — labels GT, runs harness, locks method), A2 (Validation harness — dev-time scorer), A3 (Headless workflow runner — production applier)
**Origin flows:** F1 (Method race / validation, dev-time), F2 (Production headless thresholding, per dataset)
**Origin acceptance examples:** AE1 (R4, R12 — under-capture field recovers dim foci), AE2 (R2 — over-capture field, pass-2 flattens haze), AE3 (R14 — locked method runs with no interactive step)

---

## Scope Boundaries

- Segmentation (Cellpose) is unchanged; detection operates on existing cell labels.
- The interactive QC path is not removed; this adds a headless alternative alongside it.
- SG/lysosome discrimination is out of scope (rare, negligible).
- No new GUI for routine operation; the validation harness ships as a `percell4-batch-validate-puncta` CLI (and is notebook-importable). Ground-truth *labeling* uses the existing embedded napari Points layer (export to CSV) — no new labeling UI is built.
- Per-image confidence scoring is not the trust mechanism (trust is lock-once); the optional outlier-flag safety net is deferred (see Open Questions) but is the designated home for out-of-regime production datasets.
- Time-lapse reuses the existing per-frame apply path; the only addition is a dataset-level pre-pass for scale calibration.
- The à-trous wavelet detector is hand-rolled on scipy; adding `pywt` is out of scope.

### Deferred to Follow-Up Work

- ML pixel-classifier detector + the full outlier-flag / keep-QC safety net (U8): implemented only if classical detectors cannot clear the recall bar, or to add SNR/background-drift regime checks beyond the scale gate.
- Full `donut-surface` RBF estimator (U2): ships as a registry stub raising `NotImplementedError`; the full RBF-with-conditioning-gate implementation lands only if the harness shows the `gaussian-peak` fallback rung is insufficient for a qualifying lock.
- Full hand-rolled à-trous wavelet detector (U9): ships as a registry stub; full implementation lands only if LoG/DoG + white-tophat fall short of the recall bar in the harness (mirrors U8's evidence-gated trigger).
- Re-tuning the dilute-phase `dilation_radius_px` for spot-sized masks: downstream note, addressed only if the dilute-phase workflow consumes a puncta mask in practice.

---

## Context & Research

### Relevant Code and Patterns

- **Round config:** `ThresholdingRound` (frozen) `src/percell4/workflows/models.py:107-143`; validation in `__post_init__` (name regex `:28`, metric ∈ `BUILTIN_METRICS`, imported at `models.py:22`). `WorkflowConfig` `:268-346`.
- **Config round-trip (the real edit site):** `_round_to_dict` `src/percell4/workflows/artifacts.py:174-184` and `_round_from_dict` `:187-197`; backward-compat precedent is `_particle_from_dict` `:204-216` (the `d.get(key, default)` additive idiom). `config_from_dict` `:293` orchestrates. No schema-version field exists or is warranted.
- **Detection seam:** `_apply_threshold_frame` `src/percell4/workflows/phases.py:532-583` — per-group loop (`:554-560`), `np.isin(labels, list(cells_in_group))` (`:560`, keep the `list()` wrap), per-group Otsu (`:566-574`), the **accept-all constant-group fallback (`:569-571`)**, union `np.maximum(combined, group_mask.astype(np.uint8), out=combined)` (`:578`). Called from exactly two sites, both inside `apply_threshold_headless`: time-lapse per-frame `:630` (stacked to `(T,H,W)` at `:637`) and single-TP `:661`.
- **Store-write wrapper (unchanged):** `apply_threshold_headless` `:586-674`; writes `:665`/`:670` (single-TP), `:639`/`:649` (time-lapse). `threshold_compute_one` `:488-529` untouched.
- **Runner (unchanged):** per-round block `src/percell4/gui/workflows/single_cell/runner.py:380-436`; headless apply handler `:929-980` (used when `interactive_qc=False`); `_grouping_cache` keyed `(entry.name, round_spec.name)` (strings — does **not** hash the round).
- **Reusable pure helpers:** `region_and_donut_masks(label_mask, binary_mask, region_id, props_by_id, buffer_px, donut_px)` `src/percell4/domain/analysis/_impl/_shared.py:47-84` (donut ring = EDT band, **excludes `binary_mask` pixels** via `& ~crop_binary` `:79`); `label_and_filter(mask_img, min_size) -> (label_mask, binary_mask, region_ids, props_by_id)` `:23-44`; `estimate_bg_threshold(cap_img, k_sigma=2.5, log=None) -> (threshold, mu, sigma)` `src/percell4/domain/analysis/_impl/per_particle_donut.py:89-133` (threshold has a baked-in `k_sigma=2.5`); `nan_safe_gaussian_filter` `src/percell4/domain/image/gaussian.py`; `apply_gaussian_smoothing` `src/percell4/domain/measure/thresholding.py:79-100`.
- **Registry idiom to mirror (idiom only):** flat `dict[str, callable]` `THRESHOLD_METHODS` `src/percell4/domain/measure/thresholding.py:104-110`; string dispatch like `bg_mode` `per_particle_donut.py:226-231`. The intra-domain reuse precedent is `thresholding.py:95` importing `domain/image/gaussian`.
- **Grouping:** `GroupingResult(group_assignments: pd.Series[index=label, value=group_id], n_groups, group_means)` `src/percell4/domain/measure/grouper.py:21-34`; `MIN_CELLS_DEFAULT=10` (`:18`); deterministic (`random_state=42` at `:132/:151/:223`).
- **Storage contract:** `DatasetStore.write_mask` **coerces `uint8` but does NOT binarize** (docstring: "Values 0-255 supported", `src/percell4/store.py:584-609`); `_validate_layer_shape` `:497-528` accepts `(H,W)` always, `(T,H,W)` when `n_timepoints>1`. `/groups` is a CSV-string dataset `:452-477`; `_merge_group_dfs` **hard-rejects** any df whose columns `!= ["label", <one col>]` and silently drops the column (`src/percell4/workflows/phases.py:1111-1128`, guard at `:1121`); `measure_one` reads at `:1314`.
- **`RunLog`** `src/percell4/workflows/run_log.py:44-70`: `log(*, phase, dataset, event, **fields)` accepts arbitrary keyword fields and JSON-serializes them — provenance is added by **calling** `log(...)`, no schema edit needed.
- **Downstream mask consumers (all test `mask > 0`):** `measure_cells_with_masks`/`measure_multichannel_with_masks` `src/percell4/domain/measure/measurer.py:379-410,457-556`; `analyze_particles` `src/percell4/domain/measure/particle.py:174`; per-particle donut/multichannel via `label_and_filter`; dilute-phase picker `src/percell4/gui/workflows/dilute_phase/panel.py:343`.
- **Centroid extraction precedent:** `particle.py:_iter_particles` (`:124-171`, `regionprops(label(mask)).centroid` at `:141`); reuse this rather than adding a 4th copy.
- **Architecture constraint (import-linter `pyproject.toml:113-126`):** `percell4.domain` must not import `h5py`, Qt, `napari`, or `percell4.store`. **No** intra-domain partition exists today (so `domain/measure → domain/analysis/_impl` is allowed), but the reverse edge must be forbidden (see U2). `interfaces/cli/` holds console-script `:main` entries (`pyproject.toml:82-89`); `test_qt_free_imports.py` greps `workflows/*.py` for Qt imports.

### Institutional Learnings

- **Masks must be `0/1 uint8`, and the store does NOT enforce it.** `write_mask` only `.astype(uint8)`; a 255-valued array (e.g. copying the donut export at `per_particle_donut.py:212`, or `bool*255`) writes 255 and breaks napari's `DirectLabelColormap`. **Binarize `(combined > 0).astype(uint8)` inside the pipeline before returning to the caller**; regression-pin the `{0,1}` invariant on the value returned by `_apply_threshold_frame` / read back from `/masks`, not on store behavior.
- **`np.isin(labels, list(...))` keeps the `list()` wrap** — NumPy 2.x silent all-False regression; pin with a test.
- **NaN poisons morphology/convolution detectors** across the footprint; route through `nan_safe_gaussian_filter`/fill-mask, then `&= finite & cell_mask`.
- **Never `if array:`** — use `.any()/.all()/len()/is None`.
- **In-session HDF5 staleness:** never write-then-reread derived arrays; keep pass-1 results in memory.
- **Canonical-source / `ce-learnings-researcher` gate (verified via `scripts/learnings_applicability.py`):** `models.py` and `phases.py` have **no** canonical-source entries (the gate does **not** apply to them). The gate **does** apply to `artifacts.py` (atomic-write-contract) and to any touch of `domain/analysis/_impl/_shared.py` / `per_particle_donut.py` (registered-analysis-framework).
- **Size statistics use mean/median, never sum**; `scipy.stats.mode` (not `np.bincount`) on float / background-subtracted images.

### External References

- Olivo-Marin (2002), *Extraction of spots in biological images using multiscale products* — canonical à-trous (undecimated B3-spline) wavelet spot detection; basis for U9.
- Smal et al. (2010) spot-detector comparison — h-dome/h-maxima and multiscale methods lead on low-SNR recall; informs ~4 px matching tolerance and the multiscale default.
- ISBI Particle Tracking Challenge detection metrics — distance-gated matching; informs U5's two-phase match.
- ilastik pixel classification — RandomForest on `skimage.feature.multiscale_basic_features` for the deferred U8.
- Confirmed venv: numpy 2.4.4, scipy 1.17.1, scikit-image 0.26.0, sklearn 1.8.0, torch 2.11.0; **`pywt` absent**. `skimage.morphology.square/rectangle` deprecated (use `footprint_rectangle`); `rolling_ball(..., nansafe=True, workers=...)`.

---

## Key Technical Decisions

- **Add detection to `ThresholdingRound`, not a parallel round type.** A nested **frozen** `PunctaDetectorSettings` defaults to legacy Otsu so existing `run_config.json` round-trips. Round-tripped via `_round_to_dict`/`_round_from_dict` with the additive `d.get(..., legacy_sentinel)` idiom (precedent `_particle_from_dict`); no schema-version field.
- **The two-pass orchestration lives in a NEW pure module** `domain/measure/puncta_pipeline.py` (`detect_two_pass(...)`), not inlined into `phases.py`. `_apply_threshold_frame` keeps only store-fed iteration, `np.isin` membership, the `np.maximum` union, binarization, and a one-line dispatch (legacy-Otsu vs. pipeline). This keeps the highest-logic-density code unit-testable without the workflow layer.
- **Pass-1 is a deliberately-permissive coarse seed detector** (`seed_detector`, e.g. low-threshold LoG/top-hat at the coarse scale end), **not** the configured pass-2 detector. Pass-1's bootstrap background uses the robust background-peak (`mu` from `estimate_bg_threshold`). This prevents the two-pass structure from reintroducing the under-capture bug it exists to fix. `seed_detector_name` (and its pre-bound permissive params) **is a field of the locked `PunctaDetectorSettings`** — so the locked recipe is fully reproducible and the stability probe's pass-1 `k` is recorded — but it is held fixed across datasets within a locked condition, never re-tuned per image. The seed detector is an ordinary `DETECTORS` registry entry invoked with the same `(residual, group_mask, sigma, params)` contract (its bootstrap residual is `smoothed − mu_robust`), not a separate signature.
- **Background estimators return a typed `BackgroundEstimate(residual, sigma, is_empty)`**, never a bare `float | ndarray`. Every estimator returns the *corrected residual image* (scalar: `smoothed - scalar`; surface: `smoothed - surface`; white-tophat: its residual) plus an optional noise σ. Detectors take a uniform `(residual, sigma)` and never branch on type. White-tophat therefore lives in the **detector** axis only, not both.
- **Detection runs on a per-group-isolated residual.** Before invoking any detector, out-of-group pixels are set to NaN (and the work is done on a tight bbox crop of the group). This is mandatory for the multiscale default: `blob_log`/`blob_dog` `threshold_rel` and the LoG response normalize against the array's global maximum, so a full-field residual would let a bright group suppress a dim group's foci — the exact cross-group class imbalance the per-group design exists to eliminate. The NaN-fill/`& finite & group_mask` discipline already required for NaN-safety doubles as the isolation mechanism.
- **Default detector is multiscale** (LoG/DoG or à-trous), because foci sizes vary widely; scale calibration produces a `(min_sigma, max_sigma)` *range*.
- **Cold-start scale:** a fixed **bootstrap default scale range** (derived from known SG pixel size / the ~4 px Smal tolerance) seeds pass-1 on the first harness run when no prior exists; the harness grid also sweeps the scale range, so the locked prior is grid-selected.
- **Bounded per-dataset refinement:** production refinement may only *narrow within* the locked prior's range, never expand beyond it. In the classical-detector release (before U8 ships), a candidate range outside the locked bracket is **clamped to the locked prior and a `RunLog` warning is emitted** — it does not silently expand. The full route-to-keep-QC / outlier-flag behavior is part of the U8 safety net (the rerouting depends on the runner's interactive-QC infrastructure, which U6 must not reach into). R14's scale-regime gate is therefore a *partial* "comparable data" check; SNR/background-structure drift within the scale bracket is not detected until U8.
- **Empty-on-no-signal, never accept-all.** New detectors emit an empty group mask + `RunLog` warning when no signal clears the gate. The **legacy Otsu branch preserves** the accept-all fallback at `phases.py:569-571` so legacy runs stay byte-identical (dispatch on detector type).
- **Signal-presence gate is defined in the active detector's own terms** — gate on `residual > k·σ` when σ exists, else the detector-native criterion (e.g. "no blob above `threshold_rel`"). It never vetoes a detection the active detector would otherwise produce.
- **Harness match is a two-phase footprint+bipartite protocol** (see U5), because one-to-one `linear_sum_assignment` alone cannot credit a merged detection for multiple touching granules. Two safeguards make it sound: (a) **precision is counted at the detected-component level, not the GT level** — a component covering *k* GT contributes *k* toward recall but only **one** unit to the precision denominator, so flooding a cell with a few giant blobs cannot inflate precision toward 1.0 and slip past the 0.9 lock floor; (b) **phase 2 runs on remaining unclaimed detections *and* remaining unclaimed GT** — any GT credited inside a footprint in phase 1 is removed from the phase-2 cost matrix so recall is never double-counted.
- **Output is `0/1 uint8`; `/groups` stays exactly `["label","group_<channel>_<metric>"]`.** All detector/background/scale provenance goes to `RunLog`, never to `group_df` (the `_merge_group_dfs` 2-column guard silently drops extra columns).
- **à-trous wavelet is hand-rolled** on scipy (B3-spline `[1,4,6,4,1]/16` with hole insertion), since `pywt` is absent.
- **Scoring is micro-averaged**; precision/FP only against Tier-A exhaustive labels; Tier-B (old QC masks) is a recall lower-bound + over-capture alarm only. Tier-B floor and candidate recall are scored at the **same** tolerance; the lock is evaluated per-tolerance.
- **Lock criterion (user):** lock only if recall ≥ old-QC-mask recall **and** precision ≥ 0.9 on Tier-A, **and** the candidate passes a pass-1-`k`-perturbation stability probe; else the condition keeps interactive QC.

---

## Open Questions

### Resolved During Planning

- *à-trous availability?* No (`pywt` absent) → hand-roll on scipy.
- *How does a new mode plug in?* `PunctaDetectorSettings` on `ThresholdingRound`; one-line dispatch in `_apply_threshold_frame` into pure `puncta_pipeline.detect_two_pass`.
- *Mask contract?* `0/1 uint8` binarized in the pipeline (store does not enforce it); `/groups` columns unchanged.
- *Ground-truth labeling?* napari Points → per-field CSV of `(y,x)` centroids.
- *Lock bar?* recall ≥ QC recall AND precision ≥ 0.9 on Tier-A AND stability-probe pass; else keep QC.
- *Pass-1 vs pass-2 detector?* Pass-1 = permissive `seed_detector` (a `DETECTORS` entry with pre-bound params), recorded in the locked `PunctaDetectorSettings`; pass-2 = configured/locked detector. Pass-1 runs **once per group** and its seeds are reused (see U4/U6).
- *Per-group isolation?* Detectors run on a per-group-isolated residual (out-of-group → NaN, bbox crop), so multiscale `threshold_rel`/LoG normalize within the group.
- *Cold-start scale?* Fixed bootstrap default range on run zero; grid sweeps the range.
- *Production refinement vs validated regime?* Bounded (narrow-only); out-of-bracket → clamp + `RunLog` warning in the classical release; full keep-QC rerouting ships with the U8 safety net.
- *Background contract?* Typed `BackgroundEstimate(residual, sigma, is_empty)`.
- *Names validation source?* A skimage-free `puncta_names.py` holds `DETECTOR_NAMES`/`BG_ESTIMATOR_NAMES`; `models.py` imports from it (config-load stays skimage-free); registries assert their keys ⊆ the names tuples.
- *Match precision/recall accounting?* Precision counted at detected-component level; phase-2 dedups GT credited in phase 1.
- *Constant/all-background group?* New detectors → empty mask + warning; legacy Otsu → keep accept-all.
- *Persistence shape?* Frozen nested `PunctaDetectorSettings`; `spot_scale_prior` coerced list→tuple on load; `detector_params` normalized; validation key-set sourced from a skimage-free names tuple.
- *Scale handling?* Multiscale default + calibrated `(min_sigma, max_sigma)` range.

### Deferred to Implementation

- Exact default `k` / `k_gate` (proposed 2.5; swept 1.5–3.0) and per-detector ranges.
- Exact matching tolerance Δ⁰ (proposed ~4 px; swept) and `min_spot_px`/`max_spot_px` (proposed ~2 px floor; tied to scale range).
- `N` (min donut samples / RBF-conditioning gate to attempt a surface) and `N_calib` (min pooled foci to refine scale).
- à-trous plane count (proposed K=3, product of planes 2–3) and per-plane `k_d` (proposed ~3).
- Stability-probe Δ and acceptance band for the lock criterion.
- [Needs research] Whether `multiscale_basic_features` suffices for U8 if ever triggered.

---

## Output Structure

    src/percell4/domain/measure/
      puncta_names.py         # NEW (U1) - skimage-free DETECTOR_NAMES / BG_ESTIMATOR_NAMES (validation source)
      bg_estimators.py        # NEW (U2) - BACKGROUND_ESTIMATORS registry + BackgroundEstimate (donut-surface = stub); re-exports BG_ESTIMATOR_NAMES
      puncta_detectors.py     # NEW (U3) - DETECTORS registry (5 library detectors); re-exports DETECTOR_NAMES
      atrous.py               # NEW (U9) - a-trous wavelet detector STUB (NotImplementedError; full impl deferred)
      puncta_pipeline.py      # NEW (U4) - pure detect_two_pass(): per-group-isolated residual, pass1(once)->calibrate->bg->pass2->size-filter->gate
      puncta_scoring.py       # NEW (U5) - mask_to_centroids + two-phase match (component-level precision) + micro metrics (pure)
    src/percell4/workflows/
      puncta_validation.py    # NEW (U5) - harness orchestrator (store I/O, grid race, stability probe, lock); Qt-free
      models.py               # MOD (U1) - frozen PunctaDetectorSettings on ThresholdingRound
      artifacts.py            # MOD (U1) - _round_to_dict / _round_from_dict round-trip (atomic-write-contract gate)
      phases.py               # MOD (U4, U6) - one-line dispatch, binarization, scale-calibration pre-pass, RunLog provenance
    src/percell4/interfaces/cli/
      batch_validate_puncta.py  # NEW (U5) - percell4-batch-validate-puncta :main
    pyproject.toml            # MOD (U5) - [project.scripts] percell4-batch-validate-puncta
    tests/test_measure/
      test_bg_estimators.py       # NEW (U2)
      test_puncta_detectors.py    # NEW (U3)
      test_atrous.py              # NEW (U9)
      test_puncta_pipeline.py     # NEW (U4) - bulk of two-pass logic tests
      test_puncta_scoring.py      # NEW (U5)
    tests/test_workflows/
      test_puncta_validation.py   # NEW (U5)
      test_models.py              # MOD (U1)
      test_artifacts.py           # MOD (U1) - round-trip incl. tuple + hash(round)
      test_phases.py              # MOD (U4, U6) - I/O-and-iteration wiring only
      test_qt_free_imports.py     # MOD (U5) - allowlist puncta_validation

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

**Two registries (pure domain), same flat-dict idiom as `THRESHOLD_METHODS`, distinct per-axis signatures:**

```
BACKGROUND_ESTIMATORS: dict[str, fn(image_float, group_mask, seeds, params) -> BackgroundEstimate]
  BackgroundEstimate(residual: ndarray, sigma: float|None, is_empty: bool)
  donut-median | donut-mean | gaussian-peak | percentile | mad | rolling-ball | donut-surface(experimental)
DETECTORS: dict[str, fn(residual_float, group_mask, sigma, params) -> uint8 mask{0,1}]
  otsu(baseline) | bg-k-sigma | white-tophat | log | dog | h-maxima | atrous-wavelet
DETECTOR_NAMES / BG_ESTIMATOR_NAMES: tuple[str,...]   # skimage-free, imported by models.py for validation
```

**Pure per-group two-pass pipeline (`puncta_pipeline.detect_two_pass`), per group g:**

```
# detect_two_pass(smoothed, group_label_mask, settings, scale_range, seeds=None)
# scale_range = U6 refined (min_sigma,max_sigma); seeds = pass-1 result cached by U6 (None -> compute once here)
iso = isolate(smoothed, gmask)          # out-of-group -> NaN, tight bbox crop; threshold_rel normalizes WITHIN group
# PASS 1 (run once): permissive seed_detector on the bootstrap residual, same detector contract
if seeds is None:
    seeds = seed_detector(iso - mu_robust(iso[gmask]), gmask, sigma_robust, settings.seed_params)  # mu,sigma from estimate_bg_threshold
# CALIBRATE/BACKGROUND from pass-1 seeds, in memory (no disk reread). Fallback ladder:
labels1, binary1, ids1, props1 = label_and_filter(seeds, min_size=1)   # required before region_and_donut_masks
est = background_estimator(iso, gmask, seeds=(labels1,binary1,ids1,props1), params)
#   rung 1: donut-surface (RBF) iff impl'd AND >= N samples AND well-conditioned   (v1: stub -> falls to rung 2)
#   rung 2: gaussian-peak mu (robust to a foci tail) over group pixels   <-- NEVER plain mean/median, NEVER donut-over-sparse-seeds
#   rung 3: is_empty -> all-zero mask
# PASS 2 on est.residual (per-group isolated), at scale_range:
if gate(est.residual, est.sigma, detector) is no-signal:  group_mask = zeros(labels.shape)   # NEVER accept-all
else: group_mask = detector(est.residual, gmask, est.sigma, {**params, scale_range})
group_mask = size_filter(group_mask, min_spot_px, max_spot_px)   # also closes fragments within scale
return group_mask.astype(uint8)   # ALWAYS labels.shape, never None
```

`phases.py._apply_threshold_frame` then: `if legacy: <Otsu incl. accept-all @569-571> else: combined = max over groups of detect_two_pass(...)`, finally `write_mask(name, (combined>0).astype(uint8))`.

**Validation harness (dev-time, F1) — two-phase match:**

```
GT_A = napari-point CSVs (exhaustive centroids)   # recall ceiling + precision/FP
GT_B = old /masks via store                       # recall floor + over-capture alarm only
for (detector, bg, k, r) in grid (incl. scale range):
  mask = detect over each Tier-A field; centroids = mask_to_centroids(mask)
  # Phase 1: credit every GT inside a detected component footprint (merges/touching foci)
  # Phase 2: linear_sum_assignment on remaining centroids<->GT; DROP assigned pairs with dist > r
  accumulate micro TP/FP/FN  (fragments within calibrated scale closed first -> 1 TP/0 FP)
recall, precision, F_beta(beta=2..3) per (method,params); Tier-B floor scored at the SAME r
stability probe: re-run candidate at pass-1 k +/- delta; require recall/precision delta within band
lock IF recall>=QC_recall AND precision>=0.9 AND stability-pass -> emit frozen PunctaDetectorSettings + validated scale regime
```

---

## Implementation Units

> Note: U-IDs are stable. U9 is the à-trous detector **split out of U3** during deepening; it takes the next unused number and sits after U3 in reading order by dependency, not by numeric sequence.

- U1. **Detector config schema + round-trip + validation**

**Goal:** Add a nested frozen `PunctaDetectorSettings` to `ThresholdingRound` (detector, seed detector ref, background estimator, params, size filter, `spot_scale_prior`), defaulting to legacy Otsu so existing configs round-trip losslessly.

**Requirements:** R1, R5, R6, R16

**Dependencies:** None (creates the skimage-free `puncta_names.py` it validates against)

**Files:**
- Create: `src/percell4/domain/measure/puncta_names.py` (skimage-free `DETECTOR_NAMES` / `BG_ESTIMATOR_NAMES` tuples — the single validation source)
- Modify: `src/percell4/workflows/models.py` (`ThresholdingRound` `:107-143`, `__post_init__`)
- Modify: `src/percell4/workflows/artifacts.py` (`_round_to_dict` `:174-184`, `_round_from_dict` `:187-197`)
- Test: `tests/test_workflows/test_models.py`; `tests/test_workflows/test_artifacts.py`

**Approach:**
- `PunctaDetectorSettings` is a **frozen** dataclass with JSON-scalar or explicitly-coerced fields: `detector_name: str`, `seed_detector_name: str`, `background_estimator_name: str`, `detector_params: dict`, `min_spot_px: int`, `max_spot_px: int | None`, `spot_scale_prior: tuple[float, float] | None`. Optional/sentinel on `ThresholdingRound` so legacy rounds behave exactly as today.
- Round-trip: `_round_to_dict` emits a nested dict under key `puncta_detector`; `_round_from_dict` reads `d.get("puncta_detector", None)` and **coerces `spot_scale_prior` JSON-list→tuple** on load (JSON has no tuple; a list breaks frozen `__eq__` and `hash`). Mirror `_particle_from_dict`. Normalize `detector_params` to a plain dict with no tuple values.
- Validation in `__post_init__`: `detector_name ∈ DETECTOR_NAMES`, `seed_detector_name ∈ DETECTOR_NAMES`, `background_estimator_name ∈ BG_ESTIMATOR_NAMES`, **imported from the dedicated skimage-free `src/percell4/domain/measure/puncta_names.py`** (not from the registry modules, which import scikit-image) so constructing any legacy `ThresholdingRound` does not drag scikit-image into config-load — mirrors the existing `BUILTIN_METRICS` import at `models.py:22`. U2/U3 re-export their names from `puncta_names.py` and assert at module load that their registry keys are exactly the declared tuples (a drift guard).

**Execution note:** `artifacts.py` carries the atomic-write-contract canonical entry — invoke `compound-engineering:ce-learnings-researcher` on `artifacts.py` before editing. (`models.py` has no canonical entry; no gate.)

**Patterns to follow:** `_particle_from_dict` additive `d.get` idiom; `CellposeSettings` nested-dataclass serialization; `BUILTIN_METRICS` import-for-validation at `models.py:22`.

**Test scenarios:**
- Happy path: a round with full `PunctaDetectorSettings` constructs and validates.
- Backward-compat: a round dict with **no** `puncta_detector` key reconstructs as legacy Otsu (pin at the `_round_from_dict` boundary; extend the `test_pre_evolution_config_loads_*` family).
- Edge case: `min_spot_px <= 0` or `max_spot_px < min_spot_px` raises a clear `ValueError`.
- Error path: unknown `detector_name`/`seed_detector_name`/`background_estimator_name` raises `ValueError` naming the bad key.
- Integration: `config_from_dict(config_to_dict(cfg)) == cfg` for a config with both a legacy round and a puncta round built with a **real tuple** `spot_scale_prior` (round-trip equality), **and** `hash(round)` does not raise (frozen-dataclass dict/tuple hazard). Extend `test_post_evolution_round_trip_preserves_new_fields`.
- Integration: constructing a legacy `ThresholdingRound` does **not** import scikit-image (assert via module-not-in-`sys.modules` or import-time probe).

**Verification:** Old and new configs round-trip losslessly; legacy behavior is byte-identical; validation rejects bad keys; config-load stays skimage-free.

---

- U2. **Background-estimator registry (pure domain)**

**Goal:** A `BACKGROUND_ESTIMATORS` registry returning a typed `BackgroundEstimate(residual, sigma, is_empty)`, seeding R7, reusing donut geometry and the gaussian-peak fit.

**Requirements:** R6, R7

**Dependencies:** U1 (re-exports `BG_ESTIMATOR_NAMES` from `puncta_names.py`)

**Files:**
- Create: `src/percell4/domain/measure/bg_estimators.py` (defines `BackgroundEstimate`, `BACKGROUND_ESTIMATORS`; re-exports `BG_ESTIMATOR_NAMES` from `puncta_names.py` and asserts keys match)
- Reuse: `src/percell4/domain/analysis/_impl/_shared.py` (`region_and_donut_masks`, `label_and_filter`), `per_particle_donut.py` (`estimate_bg_threshold` — for `(mu, sigma)` only)
- New CLAUDE.md note: `src/percell4/domain/measure/CLAUDE.md` documenting the sanctioned one-way edge `domain/measure → domain/analysis/_impl`
- Modify: `pyproject.toml` import-linter — add a `forbidden` contract blocking the reverse edge (`percell4.domain.analysis` must not import `percell4.domain.measure`)
- Test: `tests/test_measure/test_bg_estimators.py`

**Approach:**
- Uniform signature `estimator(image_float, group_mask, seeds, params) -> BackgroundEstimate`. `seeds` is the `(label_mask, binary_mask, region_ids, props_by_id)` tuple from `label_and_filter(pass1_seeds, min_size=1)`; estimators that don't need seeds ignore it.
- **Donut estimators** (`donut-median`/`donut-mean`): loop `region_and_donut_masks` per seed region (pass the pass-1 seed set as `binary_mask`, since the ring **excludes** `binary_mask` pixels), aggregate ring pixels on the **float** residual. This is a **declared fork** of `analyze_regions`' donut branch (`per_particle_donut.py:226-229`): float, no integer rounding, no Cap-specific `exclude_cap_zero`. Cite the forked source; reuse only the geometry helpers (byte-identical).
- `gaussian-peak`: wrap `estimate_bg_threshold` for `(mu, sigma)` **only**; never forward its baked-in `threshold` (k is applied exactly once, in the detector). `residual = smoothed - mu`, `sigma = sigma`.
- `percentile`/`mad`: robust scalar `bg`; `residual = smoothed - bg`, `sigma` from MAD where applicable.
- `rolling-ball`: `rolling_ball(image, radius=R, nansafe=True)` surface; `residual = image - surface`.
- `donut-surface` (deferred): ship as a **registry stub raising `NotImplementedError`** with a docstring describing the intended `RBFInterpolator(kernel="thin_plate_spline", smoothing>0, neighbors=k)` surface and the RBF-conditioning gate (residual/condition-number/spatial coverage, not just sample count). The fallback ladder's rung 1 is skipped while it is a stub, dropping to the robust `gaussian-peak` rung. Implement fully only if the harness shows the `gaussian-peak` rung is insufficient for a qualifying lock.
- All estimators set `is_empty=True` (driving the pipeline fallback ladder) when they cannot place samples; operate on finite pixels only.

**Execution note:** Reuses `domain/analysis/_impl/_shared.py` + `per_particle_donut.py` (registered-analysis-framework) — invoke `compound-engineering:ce-learnings-researcher` before touching those reuse surfaces. Implement test-first against synthetic backgrounds.

**Patterns to follow:** `THRESHOLD_METHODS` flat-dict idiom; intra-domain reuse precedent `thresholding.py:95`.

**Test scenarios:**
- Happy path: constant-background image → scalar estimators' `residual ≈ 0`, `sigma ≈ noise`.
- Happy path: linear-ramp / Gaussian-bump background → `rolling-ball`/`donut-surface` `residual` flattens the gradient within tolerance.
- Double-k pin: the `gaussian-peak` wrapper's `sigma` equals `estimate_bg_threshold`'s `sigma` on a fixed input, and the estimator's `threshold` scalar is never used downstream.
- Edge case: empty/sparse seeds → donut estimators return `is_empty=True` (no raise); `donut-surface` returns `is_empty=True` when ill-conditioned (not just low count).
- Edge case: all-NaN group → defined `is_empty` result, no NaN propagation.
- Integration: `BackgroundEstimate.residual` shape == image shape; `sigma` is `float|None` per declared contract.

**Verification:** Every estimator returns a correct `BackgroundEstimate`; k is applied once; ill-posed surface fits degrade to `is_empty`; the reverse import-linter contract passes.

---

- U3. **Detector registry — five library-backed detectors (pure domain)**

**Goal:** A `DETECTORS` registry producing `0/1 uint8` masks from a `(residual, sigma)` input, seeding R8's library-backed detectors with a multiscale default and full NaN-guarding. (à-trous is split to U9.)

**Requirements:** R4, R5, R6, R8

**Dependencies:** U1 (`DETECTOR_NAMES`), U2 (`BackgroundEstimate` contract)

**Files:**
- Create: `src/percell4/domain/measure/puncta_detectors.py` (defines `DETECTORS`; re-exports `DETECTOR_NAMES` from `puncta_names.py` and asserts keys match)
- Reuse: `src/percell4/domain/image/gaussian.py`; `skimage.feature.blob_log/blob_dog`, `skimage.morphology.white_tophat/h_maxima/disk`, `skimage.draw.disk`
- Test: `tests/test_measure/test_puncta_detectors.py`

**Approach:**
- Uniform signature `detector(residual_float, group_mask, sigma, params) -> np.uint8 {0,1}`; restrict output to `group_mask & np.isfinite(residual)`. Document the per-axis return contract in the module docstring (this is the *idiom* of `THRESHOLD_METHODS`, **not** its `(mask, value)` 2-tuple return).
- Detectors assume the `residual` is **already per-group-isolated by the caller** (out-of-group pixels are NaN on a bbox crop). This is what makes `threshold_rel` / LoG response normalize within the group rather than across the field; a detector must not be handed a full-field residual.
- `otsu` (baseline): `threshold_otsu` on in-group residual — kept for regression comparison.
- `bg-k-sigma`: `mask = residual > k·sigma` (k from params; sigma from `BackgroundEstimate`).
- `white-tophat`: `white_tophat(filled, disk(r))` then threshold at `k·MAD`. Lives in the **detector axis only**.
- `log`/`dog` (multiscale default): `blob_log(min_sigma, max_sigma, num_sigma, threshold_rel=..., exclude_border=False)` on the per-group-isolated residual (so `threshold_rel` normalizes within the group); `min_sigma`/`max_sigma` come from the passed `scale_range`. **Paint each `(y,x,σ)` to a disk of radius `ceil(σ·√2)`**. `threshold_rel` is the recall knob.
- `h-maxima`: `h_maxima(prefiltered, h)` on a LoG/top-hat-prefiltered residual, `h = k·sigma`.
- **NaN rule:** fill NaN before morphology/convolution, then `&= finite & group_mask`. **No 255** — binarize to `{0,1}`.

**Execution note:** Test-first against synthetic Gaussian spots of **mixed sizes** on a sloped background; the key assertion is dim-foci recall.

**Patterns to follow:** `nan_safe_gaussian_filter`; `apply_gaussian_smoothing`.

**Test scenarios:**
- Happy path: mixed-size bright+dim spots on a ramp → multiscale `log`/`dog` recovers ≥95% incl. dim ones; `otsu` baseline misses the dim ones (documents the win).
- Cross-group isolation: one bright group and one dim group in the same field, each detected on its own isolated residual → dim-group recall is unchanged by the bright group's presence (regression pin for the `threshold_rel`-normalizes-globally bug).
- Happy path: `bg-k-sigma` on known residual+σ marks exactly `residual > k·σ`.
- Edge case: NaN block inside the cell → output restricted to finite pixels, no NaN propagation.
- Edge case: `np.isin(..., list(...))`-derived `group_mask` exercised; non-empty pin (NumPy 2.x).
- Edge case: signal-free (pure-noise) residual → all-zero mask, no flood.
- Error path: empty `group_mask` → all-zero mask, no exception.
- Determinism: same `(residual, sigma, params)` → bit-identical mask across runs (no detector RNG).
- Integration: every entry returns `{0,1} uint8` of `residual.shape`; conforms to the declared signature (assert per-entry).

**Verification:** All five detectors emit `{0,1}` masks restricted to the cell; multiscale recovers dim mixed-size foci; NaN/signal-free never flood; deterministic.

---

- U9. **Hand-rolled à-trous wavelet detector (split from U3)**

**Goal:** Provide the highest-correctness-risk detector — undecimated B3-spline à-trous spot detection — as its own unit. Ship a **registry stub** in Phase 1 (so the registry/names are complete and U4 can proceed on the library detectors); the full hand-rolled implementation is **evidence-gated to Phase 3**, triggered only if LoG/DoG + white-tophat fall short of the recall bar in the U5 harness (mirroring U8's conditional trigger).

**Requirements:** R8

**Dependencies:** U3 (registry contract)

**Files:**
- Create: `src/percell4/domain/measure/atrous.py` (Phase 1: stub raising `NotImplementedError`, registered into `DETECTORS`; Phase 3: full implementation)
- Reuse: `scipy.ndimage`/`scipy.signal` convolution; `src/percell4/domain/image/gaussian.py` for NaN routing
- Test: `tests/test_measure/test_atrous.py`

**Approach:**
- Phase 1: a stub `DETECTORS["atrous-wavelet"]` raising `NotImplementedError`, present only so the registry/names are complete (it is never selected as a default and the harness simply skips it until implemented).
- Phase 3 (if triggered): B3-spline à-trous transform — separable convolution with `[1,4,6,4,1]/16` and à-trous hole insertion per level; wavelet plane `i` = `level_i − level_{i+1}`; multiscale product of planes 2–3; per-plane threshold `k_d·σ_plane` (per-plane MAD). Owns its convolution → routes NaN through normalized handling/fill-mask. Operates on the per-group-isolated residual and conforms to the U3 `detector(residual, group_mask, sigma, params)` contract.

**Execution note:** Validate against synthetic spots per Olivo-Marin (2002); test-first.

**Patterns to follow:** the U3 detector contract; `nan_safe_gaussian_filter` NaN routing.

**Test scenarios:**
- Happy path: synthetic mixed-size spots → recovers dim foci comparably to `log`.
- Correctness: B3-spline kernel + hole insertion produce the expected wavelet planes on a known input (e.g. a single delta / Gaussian).
- Edge case: NaN block → no propagation across the convolution footprint.
- Edge case: flat field → empty mask (no spurious planes).
- Determinism: same input → bit-identical mask.

**Verification:** à-trous conforms to the detector contract, reproduces expected wavelet planes, recovers dim foci, and is NaN-safe and deterministic.

---

- U4. **Pure two-pass pipeline + `_apply_threshold_frame` dispatch**

**Goal:** Implement `detect_two_pass` (pass-1 seed → calibrate/estimate-subtract background → pass-2 → size-filter → empty-on-no-signal gate) as a pure module, and dispatch to it from `_apply_threshold_frame`, leaving the union/write surface unchanged and legacy Otsu byte-identical.

**Requirements:** R1, R2, R3, R4, R15, R16

**Dependencies:** U1, U2, U3 (U9 optional at wiring time)

**Files:**
- Create: `src/percell4/domain/measure/puncta_pipeline.py` (`detect_two_pass(smoothed_image, group_label_mask, settings, scale_range, seeds=None) -> uint8`)
- Modify: `src/percell4/workflows/phases.py` (`_apply_threshold_frame` `:532-583` — one-line dispatch + binarization; passes the U6-refined `scale_range` and the cached per-group `seeds`)
- Test: `tests/test_measure/test_puncta_pipeline.py` (bulk of two-pass logic); `tests/test_workflows/test_phases.py` + `tests/test_workflows/test_phases_threshold_timelapse.py` (I/O-and-iteration wiring only)

**Approach (pure pipeline):**
- Implements the High-Level Technical Design flow. **Pass-2 detector runs on a per-group-isolated residual** (out-of-group → NaN on a bbox crop) so `threshold_rel`/LoG normalize within the group (resolves the cross-group-imbalance hole). The multiscale pass-2 uses the `scale_range` argument (U6's per-dataset refined range), not the locked prior directly.
- **Single pass-1 execution:** `seed_detector` (a `DETECTORS` entry with pre-bound permissive params) runs **once per group**; its seeds feed only the background estimator. When the U6 calibration pre-pass has already computed a group's pass-1 seeds, it passes them via `seeds=` and the pipeline does not recompute pass-1. Pass-1 bootstrap background uses `estimate_bg_threshold`'s robust `mu`. Pin that calibration and detection use the identical `seed_detector` + scale so the foci that drove calibration are the foci the pipeline seeds from.
- **Fallback ladder:** rung 1 `donut-surface` iff ≥ N samples **and** well-conditioned; rung 2 **`gaussian-peak` `mu`** (robust to a foci tail — **never** a plain mean/median over all group pixels, **never** donut-over-sparse-seeds); rung 3 `is_empty` → all-zero mask. Rung 2 is the *common* case for under-capture fields and must not be inflated by undetected foci.
- **Signal-presence gate** defined in the active detector's terms (residual > `k_gate·σ` when σ exists, else detector-native). Never vetoes a detection the detector would produce.
- **Size filter** closes fragments within the calibrated scale and applies `min_spot_px`/`max_spot_px` before returning.
- **Always returns a `labels.shape` uint8 array (all-zero on empty/signal-free), never `None`** (so the time-lapse `np.stack` at `phases.py:637` cannot crash). Keeps pass-1 results in memory (no write-then-reread).

**Approach (`_apply_threshold_frame` dispatch):**
- Reuse the per-group loop, `np.isin(..., list(...))` membership, `combined = np.zeros(labels.shape, uint8)`, and the `np.maximum` union verbatim.
- **Dispatch on detector type:** `if puncta_settings is None or detector_name == "otsu"` → keep the legacy Otsu branch **including the accept-all fallback at `:569-571`** (byte-identical); else → `combined = max over groups of puncta_pipeline.detect_two_pass(...)`.
- **Binarize** `(combined > 0).astype(uint8)` before returning (the store does **not** binarize).
- `group_df` stays **exactly** `["label", "group_<channel>_<metric>"]` (single-TP); no provenance columns (the `_merge_group_dfs` 2-column guard at `phases.py:1121` silently drops extras).

**Execution note:** `phases.py` has no canonical-source gate, but add a characterization test locking the legacy Otsu output first, then add the puncta dispatch.

**Patterns to follow:** existing per-group loop/union in `_apply_threshold_frame`; dilute-phase working-buffer precedent.

**Test scenarios (pipeline, in `test_puncta_pipeline.py`):**
- Happy path: mixed-size spots on a ramp → recovers dim foci. *Covers AE1.*
- Happy path: over-capture haze field → pass-2 background subtraction excludes haze. *Covers AE2.*
- Under-capture fallback: 1–2 pass-1 seeds → rung-2 `gaussian-peak` background is **not** inflated by undetected foci; dim foci recovered (AE1 via the fallback path, distinct from the donut-surface happy path).
- Gate: signal-free group → all-zero mask + warning; **assert not accept-all** (regression pin vs `phases.py:569-571`); assert the gate never vetoes a detection the active detector would produce.
- Empty return: signal-free frame returns an all-zero `labels.shape` array, never `None`.
- Edge case: `<10` cells → single-group path still applies local-background-aware detection.
- Pass-1 once: when `seeds=` is supplied, the seed detector is not re-invoked (spy/counter asserts a single pass-1 per group).
- Scale threading: the refined `scale_range` (not the locked prior) drives pass-2 `min/max_sigma` (assert the multiscale call receives the refined values).
**Test scenarios (wiring, in `test_phases.py`):**
- Legacy round (no puncta settings) → byte-identical mask to pre-change Otsu (characterization).
- Puncta round → `/masks/<name>` is `{0,1} uint8` (read-back pin, not store-enforced); `/groups/<name>` exactly 2 columns (regression pin on the guard).
- Integration: time-lapse `(T,H,W)` routes per-frame; `np.stack` succeeds incl. a fully-signal-free frame.
- Integration: stored mask consumed by `analyze_particles`/`measure_cells_with_masks` without error.

**Verification:** Headless puncta rounds write spec-compliant `{0,1}` masks recovering dim foci via both donut-surface and fallback paths; legacy rounds unchanged; signal-free never floods; time-lapse and downstream consumers work.

---

- U5. **Validation harness + pure scoring module + CLI**

**Goal:** Score candidate methods against hybrid ground truth via a two-phase match, run a stability probe, and lock a qualifying winner; matching/metrics math pure-domain, store/grid orchestration Qt-free, shipped as a CLI.

**Requirements:** R4, R10, R11, R12, R13, R14

**Dependencies:** U2, U3 (U9 optional)

**Files:**
- Create: `src/percell4/domain/measure/puncta_scoring.py` (`mask_to_centroids`, two-phase match, micro metrics — pure)
- Create: `src/percell4/workflows/puncta_validation.py` (ingest CSVs + old `/masks` via `DatasetStore`, grid race, stability probe, lock; **Qt-free**)
- Create: `src/percell4/interfaces/cli/batch_validate_puncta.py` (`:main`)
- Modify: `pyproject.toml` (`[project.scripts]` → `percell4-batch-validate-puncta`); `tests/test_workflows/test_qt_free_imports.py` (add `percell4.workflows.puncta_validation` to the hardcoded module tuple in `test_workflows_package_imports_without_qt` — there is no allowlist; the source-grep test already covers the file via `rglob`)
- Test: `tests/test_measure/test_puncta_scoring.py`; `tests/test_workflows/test_puncta_validation.py`

**Approach (scoring, pure):**
- `mask_to_centroids(mask) -> ndarray[(N,2)]` reuses `skimage.measure.regionprops(label(mask)).centroid` so it is byte-identical to `particle.py:141` (do not add a 4th independent copy).
- **Two-phase match:** (1) greedily credit every GT whose centroid falls inside a detected component's footprint (handles merged/touching foci); (2) `linear_sum_assignment` on the *remaining unclaimed detection-centroids* vs *remaining unclaimed GT* — **GT credited in phase 1 is removed from the phase-2 cost matrix** so recall is never double-counted (A2) — then **drop** assigned pairs with distance `> tol` (re-count dropped detection as FP, dropped GT as FN). Boundary `<= tol`. Fragments of one granule within the calibrated scale are morphologically closed/clustered first → 1 TP / 0 FP.
- **Precision is counted at the detected-component level, recall at the GT level.** A component covering *k* GT contributes *k* toward recall but only **one** unit toward the precision denominator (TP-component or FP-component), so a method that floods a cell with a few giant blobs cannot inflate precision toward 1.0 and satisfy the 0.9 lock floor (A1). Micro-average raw counts → `recall`, `precision`, `F_β` (β default 2–3). **Precision/FP only against Tier-A.**

**Approach (harness, workflow):**
- Ingest Tier-A napari-point CSVs (`field,y,x`) and Tier-B old `/masks` via `DatasetStore`.
- Grid over `{detector} × {background} × {k} × {matching radius r}` **and the scale range**; on run zero use the **bootstrap default scale range**; pool all Tier-A foci → robust percentile range to propose the prior.
- Tier-B is a recall lower-bound + over-capture alarm only; **Tier-B floor and candidate recall scored at the SAME r**; lock evaluated per-tolerance.
- **Stability probe:** re-run each candidate at pass-1 `k ± Δ`; require recall/precision delta within a band.
- **Lock criterion:** select the top method with `recall ≥ Tier-B/QC recall` AND `precision ≥ 0.9` on Tier-A AND stability-pass; emit a frozen `PunctaDetectorSettings` plus the validated scale regime. If none qualifies → no lock, report "keep interactive QC for this condition".
- **Determinism:** iterate fields sorted by name so micro-accumulation and tie-breaks are run-order-independent. `puncta_validation.py` stays Qt-free (DatasetStore I/O allowed).

**Execution note:** Pure scoring implemented test-first with synthetic GT where TP/FP/FN are known by construction.

**Patterns to follow:** `interfaces/cli/` `:main` console-script convention; `DatasetStore` typed reads; `particle.py:141` centroid extraction.

**Test scenarios (scoring):**
- Happy path: detections on GT → recall=1, precision=1; `F_β` correct.
- Touching foci: one component over 3 GT footprints → 3 recall-TP but 1 precision-component (not 3). *Covers R12.*
- Flooding guard: a few giant components covering many GT → precision is penalized, **not** inflated toward 1.0 (regression pin for A1; assert the 0.9 floor would reject it).
- Phase-2 dedup: a GT credited in phase 1 that also lies within `tol` of a leftover detection centroid → counted once, not double (regression pin for A2).
- Fragmentation: 2-fragment detection of one granule within scale → 1 TP / 0 FP.
- Boundary: pair at exactly `tol` → matched (`<=`); just beyond → FN+FP after the post-assignment drop.
- New-true-positive: a focus the old mask lacks but Tier-A confirms → TP, not FP. *Covers AE1, R12.*
**Test scenarios (harness):**
- Grid race returns a ranked table; lock selects a qualifying method and emits valid `PunctaDetectorSettings`. *Covers R13, R14.*
- No method clears the bar (or fails stability) → no lock, "keep QC". *Covers R14.*
- Cold start: first run with no prior uses the bootstrap range and proposes a prior.
- Determinism: shuffled field order → identical micro-averaged scores.
- Integration: emitted `PunctaDetectorSettings` validates against U1's `__post_init__`; `puncta_validation` passes `test_qt_free_imports`.

**Verification:** Pure scoring matches hand-computed TP/FP/FN incl. merge/fragment/boundary cases; the harness never penalizes new true positives, scores floor and candidate at one tolerance, runs a stability probe, and locks only on the full bar (or reports keep-QC).

---

- U6. **Spot-scale calibration (bootstrap, bounded refine, in-memory)**

**Goal:** Derive a `(min_sigma, max_sigma)` range for multiscale detectors; bootstrap on run zero, bound per-dataset refinement to the validated regime, persist provenance to `RunLog` — without editing `run_log.py`.

**Requirements:** R2, R3, R14

**Dependencies:** U3, U4

**Files:**
- Modify: `src/percell4/workflows/phases.py` (a dataset-level calibration pre-pass before the per-group loop; for time-lapse, **before the per-frame loop at `:617`**)
- Test: `tests/test_workflows/test_phases.py`

**Approach:**
- Calibrate the scale *range* from pass-1 foci sizes pooled across the dataset (and across frames for time-lapse), using robust percentiles (sizes vary widely). The pre-pass runs the same `seed_detector` + scale the pipeline uses; it **caches the per-group pass-1 seeds in memory** and threads them, plus the refined `scale_range`, into `detect_two_pass(..., scale_range, seeds=...)` so pass-1 executes exactly once per group (resolves the double-pass-1 hole) and pass-2 uses the refined range.
- **Cold start:** when no `spot_scale_prior` exists, seed pass-1 with the fixed bootstrap default range.
- **Bounded refinement (classical release):** per-dataset refinement may only *narrow within* the locked `spot_scale_prior`. A candidate range outside the locked bracket is **clamped to the locked prior and a `RunLog` warning is emitted** — U6 does **not** reach into the runner's interactive-QC path. The full route-to-keep-QC / outlier-flag behavior ships with the U8 safety net. Assert refined-range ⊆ validated regime.
- **In-memory only:** pool pass-1 foci in memory; re-reading source `/intensity` frames via `read_array_frame` is allowed; **writing/reading derived pass-1 masks to HDF5 is forbidden**.
- **Provenance via `RunLog.log(...)`** (no `run_log.py` edit): record bootstrap/locked/refined ranges and per-group background mode. **Skip the provenance writer for legacy Otsu rounds** (no `PunctaDetectorSettings`) so it does not emit None/empty entries.
- Deterministic (no RNG beyond the seeded grouper).

**Execution note:** `phases.py` has no canonical gate; characterization first.

**Patterns to follow:** `RunLog.log(**fields)` provenance; grouper determinism.

**Test scenarios:**
- Happy path: mixed-size foci → calibrated range brackets true min/max σ.
- Cold start: no prior → bootstrap default range used; `RunLog` records it.
- Bounded refine: dense field whose natural range exceeds the locked prior → refinement clamps to the prior (narrow-only); an out-of-bracket candidate is clamped + `RunLog`-warned (classical release; full keep-QC rerouting is U8).
- Pass-1 reuse: the cached pre-pass seeds are passed into `detect_two_pass` so the seed detector is invoked once per group across calibration + detection (counter assertion).
- In-memory: calibration does not write/read derived masks to HDF5 (assert no extra `/masks` writes during calibration).
- Time-lapse: foci pooled across frames in a pre-pass before the per-frame loop → one dataset-level range.
- Legacy round: provenance writer is skipped (no None entries).
- Determinism: same inputs → same range.

**Verification:** Calibration yields a sensible bounded range, bootstraps on run zero, never expands beyond the validated regime, stays in-memory, and logs provenance only for puncta rounds.

---

- U7. **Runner integration smoke (Qt) — end-to-end headless puncta round**

**Goal:** Confirm a headless run with `interactive_qc=False` flows a puncta-mode round through `_make_threshold_apply_headless_handler` unchanged, producing a valid mask end-to-end.

**Requirements:** R1, R16

**Dependencies:** U4

**Files:**
- Test: `tests/test_gui_workflows/test_single_cell_runner.py` (marker `gui`) — no production runner files change.

**Approach:** Drive `SingleCellThresholdingRunner` with `interactive_qc=False` and a `WorkflowConfig` with one puncta round on a small synthetic dataset; assert the UNATTENDED apply handler runs and `/masks/<round>` is `{0,1} uint8`.

**Patterns to follow:** existing headless runner tests; `_grouping_cache` keying.

**Test scenarios:**
- Integration: full headless sequence with a puncta round writes `/masks` + `/groups`, no failure.
- Edge case: config mixing one legacy Otsu round and one puncta round produces both masks correctly.
- Integration: a dataset yielding a signal-free group completes with a warning, not a `DatasetFailure`.

**Verification:** A headless run with a puncta round completes through the existing runner with no runner code changes and writes spec-compliant masks.

---

- U8. **(Deferred, R9) RandomForest pixel-classifier detector + outlier-flag safety net**

**Goal:** Add an ML detector (ilastik-style RF on a multiscale feature stack) and the optional outlier-flag safety net — only if classical detectors cannot clear the recall bar in U5.

**Requirements:** R9

**Dependencies:** U3, U5

**Files:**
- Modify: `src/percell4/domain/measure/puncta_detectors.py` (add `random-forest` entry)
- Test: alongside `tests/test_measure/test_puncta_detectors.py`

**Approach:**
- `RandomForestClassifier(class_weight="balanced", random_state=<fixed>)` on `skimage.feature.multiscale_basic_features` → probability map → threshold; restrict to finite cell pixels (sklearn rejects NaN). **`random_state` is mandatory** for a QC-retiring locked method (reproducibility).
- The outlier-flag safety net is the production home for out-of-regime datasets surfaced by U6's bounded-refinement gate.

**Execution note:** Build only on U5 evidence that classical methods plateau below the recall bar.

**Test scenarios:**
- Happy path: RF trained on a labeled field recovers held-out foci above the classical baseline.
- Edge case: NaN cell pixels excluded from features without crashing.
- Determinism: fixed `random_state` → reproducible probability map.
- Test expectation: full scenarios authored when/if U8 is triggered.

**Verification:** Only pursued on evidence; RF conforms to the detector contract, is reproducible, and the safety net flags out-of-regime images.

---

## System-Wide Impact

- **Interaction graph:** Only `_apply_threshold_frame` changes behavior, via a one-line dispatch into the pure `puncta_pipeline`. `apply_threshold_headless`, `threshold_compute_one`, and the runner are call-compatible and unchanged (adversarially confirmed: helper called only at `phases.py:630`/`:661`, identical signature). New pure modules are leaf dependencies.
- **Error propagation:** Per-dataset failures use the existing `DatasetFailure`/`FailureRecord` mechanism. Signal-free groups are "success with warning" (`RunLog`), not failures; keep `THRESHOLD_EMPTY` (zero groups) and `THRESHOLD_ERROR` (exceptions) — no new enum values.
- **State lifecycle risks:** Two-pass and scale calibration keep pass-1 results in memory (no write-then-reread). Mask write stays atomic via `DatasetStore`. **The store does not binarize** — binarization happens in the pipeline; the `{0,1}` invariant is pinned on the returned/read-back value.
- **API surface parity:** The interactive threshold-QC path (`gui/threshold_qc.py`) is untouched and remains available; only the headless apply path gains the new mode. The validation harness is a new `percell4-batch-validate-puncta` CLI surface (Qt-free).
- **Integration coverage:** Cross-layer behaviors proven by U4 (downstream consumers read the new mask) and U7 (runner end-to-end).
- **Unchanged invariants:** `/masks` = `{0,1} uint8` of `labels` shape; `/groups` = exactly `["label","group_<channel>_<metric>"]` (the `_merge_group_dfs` 2-column guard at `phases.py:1121` would silently drop extras — provenance goes to `RunLog`); downstream `mask > 0` semantics; grouper determinism; legacy Otsu byte-identical.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Inheriting the accept-all constant-group fallback (`phases.py:569-571`) reintroduces over-capture | Dispatch on detector type: legacy keeps accept-all (byte-identical); new detectors emit empty + warning; regression test pins "not accept-all" |
| Masks written as 255 break napari / downstream (store does NOT binarize) | Binarize `(combined>0).astype(uint8)` in the pipeline; `{0,1}` pin on the returned/read-back value, not on store behavior |
| Pass-1 = configured detector reintroduces the under-capture bug | Pass-1 is a fixed permissive `seed_detector` with robust-`mu` bootstrap background; test pass-1 over-detects, pass-2 controls precision |
| Fallback rung-2 background inflated by undetected foci (the common under-capture case) | Rung-2 uses robust `gaussian-peak` `mu`, never plain mean/median or donut-over-sparse-seeds; under-capture fallback recall test |
| Signal-presence gate (`bg+k·σ`) vetoes true multiscale recall | Gate defined in the active detector's own terms; test that it never vetoes a detection the detector would produce |
| Cold-start: pass-1 needs a scale before any prior exists | Fixed bootstrap default range on run zero; grid sweeps the range |
| Production scale-refinement drifts outside the validated regime → breaks R14 trust | Bounded narrow-only refinement; out-of-regime → keep-QC/outlier-flag; assert ⊆ validated regime |
| `donut-surface` non-monotone in pass-1 threshold → unstable lock | Gate on RBF conditioning (not just count); pass-1-`k`-perturbation stability probe in the lock; keep out of any locked default until it passes |
| Harness merged-credit incompatible with one-to-one assignment | Two-phase match: footprint-credit then bipartite-on-remainder with post-assignment distance drop; phase-2 dedups phase-1-credited GT |
| Merged giant blobs inflate precision past the 0.9 lock floor | Precision counted at detected-component level (not GT level); flooding-guard regression test |
| Multiscale `threshold_rel`/LoG normalize across the whole field → bright group suppresses dim group | Detectors run on a per-group-isolated residual (out-of-group → NaN, bbox crop); cross-group isolation regression test |
| Refined scale range never reaches the pure pipeline; pass-1 runs twice | `detect_two_pass(..., scale_range, seeds=)`; U6 caches per-group pass-1 seeds and threads the refined range; pass-1-once test |
| `group_df` provenance columns silently dropped by `_merge_group_dfs` (`:1121`) | Keep `group_df` exactly 2 columns; all provenance to `RunLog`; regression pin on the guard |
| Time-lapse `np.stack` (`:637`) crash on a `None`/empty frame | Pipeline always returns a `labels.shape` all-zero array, never `None`; signal-free-frame test |
| Double-`k` (estimator threshold + detector `k`) | Estimator exposes `(mu, sigma)` only; `k` applied once in the detector; pinned test |
| Frozen-dataclass round-trip: JSON list vs tuple, dict unhashable | Frozen `PunctaDetectorSettings`; coerce `spot_scale_prior` list→tuple on load; normalize `detector_params`; round-trip `==` + `hash()` tests |
| skimage dragged into config-load via validation | Validation key-sets from a skimage-free `DETECTOR_NAMES`/`BG_ESTIMATOR_NAMES` tuple; import-probe test |
| New `domain/measure → domain/analysis/_impl` edge / future reverse cycle | Document the sanctioned one-way edge; add import-linter `forbidden` contract blocking the reverse |
| Editing `artifacts.py` (atomic-write-contract canonical entry) | `ce-learnings-researcher` gate on `artifacts.py`; additive `d.get` round-trip |

---

## Alternative Approaches Considered

- **Inlining the two-pass orchestrator in `phases.py`** (instead of a pure `puncta_pipeline.py`): rejected — strands the highest-logic-density, most-testable code in the workflow layer; the pure cut is unit-testable without store/Qt and shrinks the `phases.py` edit to a dispatch.
- **Parallel round type for puncta detection** (instead of a field on `ThresholdingRound`): rejected — duplicates compute/cache/apply/runner/storage for no behavioral gain.
- **`float | np.ndarray` background union** (instead of typed `BackgroundEstimate`): rejected — forces `isinstance` branching and cannot distinguish a surface-to-subtract from an already-subtracted residual; the typed result also makes the signal gate well-defined and removes white-tophat from two registries.
- **`register_analysis` decorator framework for the registries** (instead of flat dicts): rejected for the detection path — that framework is Scripts-tab/analysis-driven; the headless path is `WorkflowConfig`-driven and the flat-dict idiom matches.
- **Pass-1 = configured detector**: rejected — reintroduces the under-capture failure mode; pass-1 is a fixed permissive seed detector.
- **Single-scale top-hat default**: rejected as default given wide size variation — kept as a registry baseline.
- **Per-image confidence as the trust mechanism**: rejected per origin scope — trust is lock-once; an optional outlier-flag safety net is the deferred home for out-of-regime data.

---

## Phased Delivery

### Phase 1 — Pure foundation (parallelizable)
- U1 (config schema + round-trip + `puncta_names.py`), U2 (background estimators + `BackgroundEstimate`; `donut-surface` stub), U3 (five library detectors). U9 ships only its à-trous **stub** here so the registry/names are complete; U2's and U3's work can run in parallel with U1 once `puncta_names.py` exists. No behavior change to existing runs.

### Phase 2 — Production path + selection (parallelizable after Phase 1)
- U4 (pure pipeline + dispatch) + U6 (scale calibration) for the production headless path.
- U5 (validation harness + scoring + CLI) to race methods, run the stability probe, and emit a locked `PunctaDetectorSettings`. U5's lock output feeds real configs but is not a code dependency of U4.

### Phase 3 — Integration and evidence-gated escalation
- U7 (runner end-to-end smoke).
- U9 full à-trous implementation only if LoG/DoG + white-tophat fall short of the recall bar in U5.
- U2 full `donut-surface` RBF estimator only if the `gaussian-peak` fallback rung proves insufficient for a qualifying lock.
- U8 (RF detector + full keep-QC/outlier-flag safety net) only if U5 shows classical methods plateau below the recall bar, or to add SNR/background-drift regime checks beyond the scale gate.

---

## Documentation Plan

- Add `src/percell4/domain/measure/CLAUDE.md` documenting the two-axis registry contract (`BackgroundEstimate`; per-axis signatures) and the sanctioned one-way `domain/measure → domain/analysis/_impl` edge (current state only).
- Document the `percell4-batch-validate-puncta` harness usage (inputs: napari-point CSVs + dataset `/masks`; output: ranked table + locked `PunctaDetectorSettings` + validated scale regime) in the CLI/module docstring.
- Archive the origin brainstorm reference; this plan supersedes it for implementation.

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-06-03-headless-grouped-thresholding-puncta-requirements.md](docs/brainstorms/2026-06-03-headless-grouped-thresholding-puncta-requirements.md)
- Integration seams: `src/percell4/workflows/phases.py:532-674,1111-1128,1314`, `src/percell4/workflows/models.py:107-143`, `src/percell4/workflows/artifacts.py:174-216,293`, `src/percell4/gui/workflows/single_cell/runner.py:380-436,929-980`
- Reusable domain: `src/percell4/domain/analysis/_impl/_shared.py:23-84`, `src/percell4/domain/analysis/_impl/per_particle_donut.py:89-133`, `src/percell4/domain/measure/thresholding.py:79-110`, `src/percell4/domain/measure/grouper.py:21-34`, `src/percell4/domain/measure/particle.py:124-171`, `src/percell4/domain/image/gaussian.py`
- Storage contract: `src/percell4/store.py:452-477,497-528,584-609`; `src/percell4/workflows/run_log.py:44-70`
- Architecture: `pyproject.toml:82-89,113-126` (import-linter + scripts); canonical sources via `scripts/learnings_applicability.py`
- External: Olivo-Marin (2002) à-trous multiscale products; Smal et al. (2010) spot-detector comparison; ISBI Particle Tracking Challenge detection metrics; ilastik pixel classification
