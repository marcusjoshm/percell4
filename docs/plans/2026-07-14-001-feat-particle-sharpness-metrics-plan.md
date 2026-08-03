---
title: "feat: Per-particle sharpness/focus metrics for out-of-focus filtering"
type: feat
status: active
date: 2026-07-14
origin: docs/brainstorms/2026-07-14-out-of-focus-particle-sharpness-metrics-requirements.md
---

# feat: Per-particle sharpness/focus metrics for out-of-focus filtering

## Overview

Add three per-particle focus/sharpness metrics — **edge-skirt ratio**, **boundary gradient**, and **Laplacian variance** — to the per-particle detail export (`particles.csv`). These give the researcher the missing "sharpness" axis of a size × sharpness feature space so out-of-focus P-bodies (dim, hazy, spread) can be manually gated out of the `intermediate_mask` and `P-body_mask` populations. This is a **measurement-only** change: no thresholds, no new masks, no automatic classification. The researcher explores the resulting distributions and sets the filter by eye.

The change is deliberately narrow. Because `particles.csv` is written **wholesale** (every column `analyze_particles_detail` emits is written, unfiltered), the entire *shipped* change is contained in `src/percell4/domain/measure/particle.py` plus tests — no CSV-column-selection, GUI-dialog, or CLI plumbing is touched. A separate throwaway script (U3) validates real-data separation but ships nothing.

---

## Problem Frame

Three particle classes must be analyzed as clean, separate populations: in-focus P-bodies, true intermediate assemblies, and out-of-focus P-bodies (contamination). The two real populations are currently split by CNR (a contrast/brightness axis) in `domain/measure/cnr_classification.py`. Out-of-focus P-bodies are genuinely low-contrast, so they fall into the same low-CNR bin as true intermediate assemblies and CNR cannot expel them.

Data analysis of 13,100 particles (see origin) confirmed **no existing feature cleanly separates the populations** (best AUC ~0.86, heavy overlap), and every sharpness proxy derivable from the existing summary columns is weak (0.16–0.39). This is structural: "sharp cutoff vs. haze" is a property of the **radial intensity profile at the particle edge**, which mean/max/std over interior pixels cannot capture, and the detection pipeline presmooths (σ=1px), erasing edge steepness. The `intermediate_mask` even contains particles up to 2.5 µm² — physically impossible for a diffraction-limited assembly, i.e. demonstrable out-of-focus contamination. The fix is a new spatial sharpness axis measured directly on the particle boundary (see origin: docs/brainstorms/2026-07-14-out-of-focus-particle-sharpness-metrics-requirements.md).

---

## Requirements Trace

- R1. Compute three per-particle focus/sharpness metrics inside `_iter_particles`, using each particle's boolean mask, the cell crop, and (for skirt restriction) the cell mask. Buffer choice is set per metric in Key Technical Decisions (raw for the ratio metric; a lightly-presmoothed crop for the two derivative metrics — a deliberate, documented deviation from the origin's blanket "raw pixels" wording).
- R2. **Edge-skirt ratio** — mean **raw** intensity in a thin annulus just outside the particle ÷ particle peak; annulus restricted to the owning cell and excluding other particles. Low = sharp cutoff; high = haze past the edge.
- R3. **Boundary gradient** — mean gradient magnitude at the particle boundary (computed on a lightly-presmoothed crop), normalized by the particle **peak** (not by a contrast term). High = steep edge (in-focus).
- R4. **Laplacian variance** — variance of the Laplacian (on a lightly-presmoothed crop) over the particle, normalized to be intensity-scale-invariant. High = sharp high-frequency content (in-focus).
- R5. Compute each metric per channel, matching the existing `{channel}_<metric>` column convention.
- R6. Surface the three metrics as new columns in `analyze_particles_detail` → `particles.csv`; handle degenerate inputs (single-pixel particle, flat region, particle on the cell-bbox edge) with a well-defined sentinel rather than raising.
- R7. Measurement only — no threshold, no new mask, no reclassification, no change to `cnr_classification.py` or the `_low`/`_high` split.

**Origin acceptance examples:** AE1 (new columns present per channel — covers R1/R2/R5/R6), AE2 (in-focus vs out-of-focus particle of similar integrated intensity separates on ≥1 metric in the expected direction — covers R2–R4), AE3 (degenerate particle emits sentinel without erroring — covers R6).

**Deliberate deviations from origin:** (1) origin R1/R3/R4 specify "raw pixels" for all three metrics; this plan keeps raw for the edge-skirt ratio but uses a *lightly-presmoothed* crop (σ lighter than detection's 1px) for the two derivative metrics, because differentiating raw counts amplifies noise (per the `domain/measure` "presmooth before differentiating" convention). (2) **AE2 (real in-focus vs out-of-focus separation) is verified by researcher eye-validation on real crops, not by an automated test** — the automated directionality check proves the operators respond to blur, not that real optical defocus separates. The real-data check is U3.

---

## Scope Boundaries

- No automatic out-of-focus classification or filtering — export columns only; thresholds set downstream by eye.
- No new mask creation and no change to CNR classification or the intermediate/P-body split.
- **Not added to the per-cell summary** (`analyze_particles` → `combined.csv`). Keeping the metrics out of the per-cell rollup is what lets the change avoid `BUILTIN_METRICS`, `_PARTICLE_AGGREGATORS`, `csv_columns.py`, the config-dialog pickers, and `batch_measure.py` entirely.
- Detection/masking untouched — particles come from existing masks as-is.
- No change to particle identity/labeling/area/size-filter logic in `particle.py` (would break the "reproduces exactly" join-key contract in `domain/analysis/_impl/per_particle_multichannel.py:145`).
- The donut/dilute analysis (`per_particle_multichannel.py`) does not receive these metrics.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/domain/measure/particle.py` — `_iter_particles` (the extension point: `cell_mask` at ~line 115, cell-bbox `channel_crops` at ~135, `this_particle` at ~143 are all in scope), `_ParticleRecord` (~line 72, add a parallel field alongside `metric_values`), `analyze_particles_detail` (~256; extend both the per-row build ~291-294 and the empty-frame column list ~297-303).
- **Wholesale write confirmed:** `src/percell4/workflows/phases.py::export_run` (~2714-2743) writes `particles.csv` with `to_csv(..., float_format="%.6g")` and **no `columns=` argument**; in-code comment at ~`phases.py:2567` states "particles.csv writes all columns." `measure_particles_one` (~2273) / `_measure_particles_timelapse` (~2198) call `analyze_particles_detail` and add `round_name` + `area_um2`.
- Existing metric signature `fn(image_crop, mask) -> float` (`metrics.py:22`, `BUILTIN_METRICS` at `metrics.py:113`) reads only in-mask pixels and is **not** passed `cell_mask` — the reason these new metrics are computed in-loop, not as `BUILTIN_METRICS`.
- "Fill-NaN-then-restrict" discipline (measure `CLAUDE.md`, puncta detectors): compute a convolution/derivative operator over the full crop, then restrict/reduce over the particle mask — never mask before differentiating (loses boundary context).

### Institutional Learnings

- `docs/solutions/conventions/gui-panel-and-batch-workflow-method-parity-2026-07-13.md` — GUI and batch surfaces must map 1:1. **Applies inversely here:** because `particles.csv` is wholesale and the metrics stay out of the per-cell pickers, there are no two surfaces to keep in parity. If a future change surfaces these in the per-cell summary, the parity guard becomes mandatory.
- `docs/solutions/conventions/um2-area-sibling-columns-2026-06-29.md` — `csv_columns`/area logic auto-emits a `<col>_um2` sibling for columns matching `_is_area_column` (`csv_columns.py:66`, applied at `csv_columns.py:123`). **Constraint:** the three metric column names must not end in `_area` / contain `_area_in_` / end in `_particle_area`. `edge_skirt_ratio`, `boundary_gradient`, `laplacian_variance` are safe.
- `docs/solutions/architecture-patterns/registered-analysis-framework.md` (adjacent) — Rule 2: pin numeric parity with a characterization fixture built **before** the change. Applied as the characterization test in U1.
- `docs/solutions/conventions/adaptive-clip-window-and-k-rules-2026-06-23.md` — `domain/measure` convention is "presmooth before differentiating" (derivatives amplify noise). Informs the Key Decision on the presmoothing level below.

### External References

- None needed — Sobel/Tenengrad gradient energy and variance-of-Laplacian are textbook autofocus metrics; `scipy.ndimage` (`sobel`, `laplace`, `binary_dilation`, `binary_erosion`) and `skimage` are already `domain/measure` dependencies.

---

## Key Technical Decisions

- **Keep the metrics out of `BUILTIN_METRICS`; compute them in `_iter_particles`.** Adding to `BUILTIN_METRICS` would leak them into per-cell measurement (`measurer.py`), threshold-metric validation (`models.py`), and 4+ GUI metric pickers, and would require a `_PARTICLE_AGGREGATORS` entry to avoid a `KeyError` at `particle.py:244`. In-loop computation also gives access to `cell_mask`, which the `fn(image, mask)` signature lacks and which the edge-skirt metric needs to avoid bleeding into adjacent cells.
- **Dedicated `_ParticleRecord` field.** Store results in a new `sharpness_values: dict[str, dict[str, float]]` (channel → metric → value), parallel to `metric_values`, emitted only by `analyze_particles_detail`. `analyze_particles` (per-cell) ignores it, so no aggregator is needed and no per-cell columns appear.
- **Presmoothing level (resolves the raw-vs-smoothed tension).** The edge-skirt **ratio** is an annulus mean over a peak — noise-averaging, not a derivative — computed on the **raw** crop. The two derivative metrics (boundary gradient, Laplacian variance) are computed on a **lightly** presmoothed crop, deliberately lighter than the detection σ=1px (default ≈0.5px) so pixel noise is tamed without erasing the focus signal the brainstorm relies on. The exact σ is a tunable knob (see Deferred).
- **Boundary gradient is normalized by particle peak, not by `(peak − background)`.** The target population (out-of-focus P-bodies) is *by definition* low-contrast, so `peak ≈ local background` and a `(peak − background)` denominator collapses toward zero (or flips sign under noise) — a hazy particle would then register a huge/inverted gradient and read as *in-focus*, backwards. Worse, `(peak − background)` is exactly the CNR numerator (`cnr_classification` computes `CNR = (interior − background)/σ`), so normalizing by it re-imports the very contrast confound this axis exists to be orthogonal to. Normalizing by `peak` (a strictly positive, detected quantity) gives a dimensionless relative edge-steepness that stays finite for dim particles and does not track contrast. The exact normalizer remains explorable during eye-validation, but `(peak − background)` is explicitly rejected.
- **Compute operators once per (cell, channel), reduce per particle.** Sobel-magnitude and Laplacian arrays depend only on the cell crop, not the particle, so compute them once per cell crop and index per particle — avoids O(particles) re-convolution of the same crop.
- **Gate the sharpness computation with a `compute_sharpness` flag on `_iter_particles`, default `False`.** `_iter_particles` is shared with `analyze_particles` (the per-cell `combined.csv` summary), which discards `sharpness_values`. Without a gate, every `combined.csv` run would pay the full presmooth+Sobel+Laplacian cost for nothing. `analyze_particles_detail` sets the flag `True`; `analyze_particles` leaves it `False`.
- **Sentinel policy separates *geometrically uncomputable* from *low-signal-but-valid*.** Emit `NaN` **only** for genuine geometric degeneracy where no value exists (single-pixel particle with no interior/boundary; skirt annulus entirely outside the cell bbox or fully occupied by neighbors). A dim, hazy, or flat particle is **not** uncomputable — it must produce a real finite value that places it in the out-of-focus region (high edge-skirt ratio, low gradient, low Laplacian variance); routing those to `NaN` would silently delete the exact population the feature exists to surface from the researcher's eye-gated distributions. Because even geometric-`NaN` rows can correlate with large out-of-focus particles, U3 measures the per-population `NaN` rate on the baseline run, and the export adds a per-particle validity indicator (a `sharpness_computable` flag or per-metric reason) so dropped rows stay visible and countable rather than vanishing from histograms. `NaN` (not `0.0`) is still used for the truly-uncomputable case to avoid a false spike at zero.
- **Per-channel for all channels.** The export function does not know which channel is the detection channel, and per-channel matches every existing particle column. The researcher reads the detection channel's columns and ignores the rest.

---

## Open Questions

### Resolved During Planning

- Is `particles.csv` column-filtered? **No** — written wholesale; only `analyze_particles_detail` must emit the columns.
- Which surfaces must change? **Only `particle.py` + tests.** `csv_columns.py`, `config_dialog.py`, `batch_measure.py`, `per_particle_multichannel.py` are untouched.
- Do the metrics belong in `BUILTIN_METRICS`? **No** (leak surface + missing `cell_mask`).
- Should they enter the per-cell summary? **No** (out of scope; keeps the change contained).

### Deferred to Implementation

- Exact edge-skirt annulus width (1px vs 2px) and how strictly to exclude neighbor particles — tune on real crops during eye-validation.
- Exact presmoothing σ for the derivative metrics (≈0.5px starting point) and the remaining normalizers (e.g. Laplacian variance ÷ mean² or ÷ peak²; boundary gradient ÷ `peak` — `(peak − background)` is rejected per Key Decisions) — validate stability on the dimmest out-of-focus particles.
- Boundary-pixel definition for the gradient metric (inner boundary `this_particle & ~erode(this_particle)` vs the full perimeter) — pick whichever is more stable on 1–2px particles.
- Form of the validity indicator (a single `sharpness_computable` boolean column vs per-metric reason codes) and the exact geometric-degeneracy predicates that trigger it.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

Integration inside the existing `_iter_particles` cell loop:

```
# analyze_particles_detail calls _iter_particles(..., compute_sharpness=True);
# analyze_particles (per-cell summary) leaves it False, so this block is skipped there.
for each cell (crop = images[ch][sl], cell_mask, particle_labels):
    # once per (cell, channel) — not per particle:
    work_ch   = light_presmooth(crop_ch, sigma≈0.5)        # derivative buffer (lighter than detection's 1px)
    grad_ch   = sobel_magnitude(work_ch)                   # for boundary gradient
    lap_ch    = laplacian(work_ch)                         # for Laplacian variance

    for each particle pid in cell:
        this_particle = particle_labels == pid
        other_particles = (particle_labels > 0) & ~this_particle
        for ch in channels:
            peak = raw_crop_ch[this_particle].max()        # strictly positive for a detected particle
            # edge-skirt ratio (raw). A dim/hazy particle yields a HIGH ratio (a real value),
            # NOT NaN — NaN only when the annulus is geometrically empty (bbox edge / neighbors).
            skirt = dilate(this_particle, w) & ~this_particle & cell_mask & ~other_particles
            skirt_ratio = mean(raw_crop_ch[skirt]) / peak      # NaN only if skirt geometrically empty
            # boundary gradient (light-smoothed), normalized by PEAK (not peak-bg; see Key Decisions):
            boundary = this_particle & ~erode(this_particle)
            bgrad = mean(grad_ch[boundary]) / peak
            # Laplacian variance (light-smoothed), scale-normalized (e.g. / mean^2):
            lvar  = var(lap_ch[this_particle]) / normalizer
            # low-signal particles -> real low values (out-of-focus region), not NaN.
            sharpness_values[ch] = {edge_skirt_ratio, boundary_gradient, laplacian_variance}
    # + a per-particle validity indicator so geometric-NaN rows stay visible/countable.
```

Column output in `analyze_particles_detail`: `{channel}_edge_skirt_ratio`, `{channel}_boundary_gradient`, `{channel}_laplacian_variance` per channel plus a per-particle validity indicator, appended after the existing `{channel}_<metric>` columns.

---

## Implementation Units

- U1. **Sharpness metric computation + detail-export columns**

**Goal:** Compute the three per-particle sharpness metrics in `_iter_particles` and emit them as new per-channel columns from `analyze_particles_detail`, without altering any existing column or particle-identity logic.

**Requirements:** R1, R2, R3, R4, R5, R6, R7

**Dependencies:** None

**Files:**
- Modify: `src/percell4/domain/measure/particle.py`
- Test: `tests/test_measure/test_particle.py`

**Approach:**
- Add a `sharpness_values: dict[str, dict[str, float]]` field to `_ParticleRecord`.
- Add a `compute_sharpness: bool = False` parameter to `_iter_particles`; `analyze_particles_detail` passes `True`, `analyze_particles` (per-cell summary) leaves the default so it pays no derivative-filter cost.
- When enabled, after `particle_labels`/`cell_mask`/`channel_crops` are established, precompute per (cell, channel): a lightly-presmoothed buffer, its Sobel-magnitude, and its Laplacian (once per cell, not per particle). Then per particle × channel compute the three metrics per the Technical Design, restricting the skirt to `cell_mask & ~other_particles`, normalizing the boundary gradient by `peak`.
- Sentinel discipline: emit `NaN` **only** for genuine geometric degeneracy (single-pixel particle, geometrically empty skirt); dim/hazy/flat particles must yield real finite values (they belong in the out-of-focus region, not deleted). Emit a per-particle validity indicator (`sharpness_computable` flag or per-metric reason) so geometric-`NaN` rows remain visible and countable.
- Emit `{channel}_edge_skirt_ratio`, `{channel}_boundary_gradient`, `{channel}_laplacian_variance` (plus the validity column) in `analyze_particles_detail` — in **both** the per-row build and the empty-frame column list, preserving existing column order (new columns appended last).
- Do **not** modify `analyze_particles` (per-cell summary), `_PARTICLE_INTENSITY_METRICS`, `_PARTICLE_AGGREGATORS`, `BUILTIN_METRICS`, or the connected-component/size-filter logic.
- Name columns to avoid the `_is_area_column` matcher (no `_area` suffix).

**Execution note:** Characterization-first — before adding metrics, add/confirm a test that locks the current `analyze_particles_detail` column set and representative values on a small synthetic fixture, so the change is proven to only *append* columns and leave existing values byte-identical.

**Patterns to follow:**
- `_iter_particles` in `src/percell4/domain/measure/particle.py` for the crop/mask/loop structure.
- "Fill-NaN-then-restrict" discipline (measure `CLAUDE.md`): operator over the full crop, then reduce over the particle mask.
- Existing per-channel column naming `{prefix}{metric}` (`analyze_particles_detail` ~291-294).

**Test scenarios:**
- Happy path — `Covers AE1.` Given a two-channel synthetic image with a few particles, when `analyze_particles_detail` runs, the frame contains `<ch>_edge_skirt_ratio`, `<ch>_boundary_gradient`, `<ch>_laplacian_variance` (plus the validity column) for every channel, one finite value per in-focus particle.
- Operator wiring / directionality (sanity check — does **not** cover AE2) — Given a sharp synthetic particle (compact bright core, steep edge) and a Gaussian-blurred copy of equal integrated intensity, the blurred copy has **higher** edge-skirt ratio, **lower** boundary gradient, and **lower** Laplacian variance. This proves the operators are wired and respond monotonically to blur; it is true by construction and does **not** establish real optical-defocus separation (that is AE2, validated in U3).
- Low-signal is not NaN — Given a dim, low-contrast synthetic particle (peak just above background, hazy edge), all three metrics return **finite** values landing it in the out-of-focus region (high edge-skirt ratio, low gradient, low Laplacian variance), and the validity indicator reads computable. Guards against the failure mode where the target population is silently dropped to `NaN`.
- Boundary-gradient stability — Given a particle with `peak ≈ background`, the boundary gradient (normalized by `peak`) stays finite and does not blow up or invert (regression guard for the rejected `(peak − background)` denominator).
- Characterization / non-regression — Given the pre-existing fixture, all original columns (`cell_id`, `particle_id`, `area`, `centroid_*`, `{channel}_<intensity metric>`) are present, in the same order, with unchanged values; the new columns are appended after them.
- Edge case — `Covers AE3.` Given a single-pixel particle and a particle whose dilated skirt falls outside the cell bbox / is fully occupied by neighbor particles, the metric is emitted as `NaN`, the validity indicator reads not-computable, and no exception is raised.
- Edge case — Given a particle adjacent to another particle, the edge-skirt annulus excludes the neighbor's pixels (skirt restricted to `cell_mask & ~other_particles`), verified by constructing two touching particles with distinct surround intensities.
- Edge case — Given a channel crop containing a neighboring cell's bright pixels just outside the particle, the edge-skirt ratio does not include them (cell-mask restriction).
- Compute-gating — Given `analyze_particles` (per-cell summary), `_iter_particles` is invoked with `compute_sharpness=False` and no sharpness/derivative work is performed (verified via call assertion or absence of sharpness columns in the summary output).

**Verification:**
- `analyze_particles_detail` returns a frame with exactly the original columns plus the new per-channel sharpness columns and validity indicator; existing values are unchanged; new values are finite for well-formed and low-signal particles and `NaN` only for geometrically degenerate ones; the per-cell summary path performs no sharpness computation.

---

- U2. **Export & CLI propagation guard**

**Goal:** Prove the new columns flow end-to-end into `particles.csv` through the workflow export and the `percell4-batch-measure` CLI, and guard against a future change silently dropping them (e.g. someone adding column filtering to the particle export).

**Requirements:** R5, R6

**Dependencies:** U1

**Files:**
- Test: `tests/test_workflows/test_phases.py`
- Test: `tests/test_cli_batch_measure.py`

**Approach:**
- No source change expected — the particle export is wholesale. These are guard tests confirming the columns survive `measure_particles_one` → staging parquet → `export_run` → `particles.csv`, and the CLI path.
- If a test reveals the columns are dropped somewhere (unexpected), the fix lands here and the plan's "wholesale" assumption is revisited.

**Test scenarios:**
- Integration — `Covers AE1.` Given a small synthetic dataset run through the particle-measure + `export_run` path, the written `particles.csv` header contains the three new per-channel columns with populated values.
- Integration — Given `percell4-batch-measure` run end-to-end on a fixture, `particles.csv` includes the three new columns.
- Non-regression — the per-cell `combined.csv` does **not** gain any `edge_skirt_ratio` / `boundary_gradient` / `laplacian_variance` columns (confirms the metrics stayed out of the per-cell summary and its column machinery).

**Verification:**
- Both CSV-producing paths yield `particles.csv` containing the three new per-channel columns; `combined.csv` is unchanged.

---

- U3. **Real-data validation gate (AE2)**

**Goal:** Establish that the metrics actually separate in-focus from out-of-focus particles on **real** data — the origin's true acceptance criterion (AE2), which the synthetic U1 test cannot prove — and quantify how much of the target population is lost to the `NaN` sentinel before the researcher trusts the distributions.

**Requirements:** R2, R3, R4 (AE2); guards the NaN-visibility decision

**Dependencies:** U1

**Files:**
- Create: `scripts/validate_sharpness_metrics.py` (throwaway/one-off analysis script; not part of the shipped package)

**Approach:**
- Run the extended particle analysis on the researcher's real datasets (e.g. the cited `run_2026-07-14T162241Z_74f54dcf` and a Dcp1B set where the intermediate bin is known to be dominated by out-of-focus P-bodies).
- Compute, per population (`P-body_mask` / `intermediate_mask`) and per metric: the distribution, a separation measure (e.g. AUC on hand-labeled in-focus vs out-of-focus crops), and the **`NaN` rate** — to confirm the sentinel is not deleting the out-of-focus cluster.
- Present the distributions to the researcher for eye-validation; **this manual sign-off is the AE2 gate**. If no metric separates on real data (the origin warned intuitive proxies already failed once), that is a feature-level finding to route back to `ce-brainstorm`, not a code bug.

**Execution note:** This is validation, not shipped code — it does not block U1/U2 landing, but the feature is **not "done" (AE2 unmet)** until this real-data check passes by eye.

**Test scenarios:**
- `Test expectation: none` — this unit is an analysis/validation script, not feature-bearing code. Its output is distributions and NaN-rates for human judgment, not an assertion suite.

**Verification:**
- The researcher confirms, on real eye-labeled particles, that at least one metric separates in-focus from out-of-focus in the expected direction (AE2), and the per-population `NaN` rate is low enough that the out-of-focus cluster is actually visible in the gated distributions.

---

## System-Wide Impact

- **Interaction graph:** Only `analyze_particles_detail` consumers are affected, and only additively. `_iter_particles` is shared with `analyze_particles` (per-cell), which reads `metric_values` and ignores `sharpness_values`; the new `compute_sharpness=False` default means the per-cell summary path performs no sharpness computation at all — no behavior *or* cost change there.
- **Error propagation:** `NaN` is emitted only for genuine geometric degeneracy (with a validity indicator so those rows stay countable); low-signal particles yield real finite values. A metric failure never aborts a particle row or the export. This diverges deliberately from the intensity metrics' `try/except → 0.0`: `0.0` is a valid low-sharpness value here, so it is reserved for real low values, and `NaN` means "not computable," never "low."
- **State lifecycle risks:** None — pure in-memory measurement; no persistence, cache, or mask writes.
- **API surface parity:** `particles.csv` (detail) gains columns; `combined.csv` (per-cell) intentionally does not. The donut analysis (`per_particle_multichannel.py`) is unaffected.
- **Unchanged invariants:** Particle identity/labeling/area/size-filter in `particle.py` is untouched, preserving the "reproduces exactly" join-key contract at `per_particle_multichannel.py:145`. Existing `particles.csv` columns keep their names, order, and values. `BUILTIN_METRICS` and all metric pickers are unchanged.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| A chosen metric doesn't actually separate in-focus from out-of-focus on real data (intuitive proxies already failed once in the origin analysis). | Ship all three; **U3 gates AE2 on real-data eye-validation** before the feature is called done. The synthetic U1 test only proves operator wiring, not separation. A null result routes back to `ce-brainstorm`. |
| Boundary gradient blows up / inverts on low-contrast particles, or re-imports the CNR confound. | Normalize by `peak` (strictly positive, detected), not `(peak − background)`; regression test asserts stability at `peak ≈ background`. |
| The `NaN` sentinel silently deletes the out-of-focus population from the researcher's eye-gated distributions. | `NaN` only for geometric degeneracy; low-signal particles yield real values; a validity indicator keeps dropped rows countable; U3 measures the per-population `NaN` rate on the baseline. |
| Derivative metrics are noisiest on small (1–2px) particles — the residual case size can't already separate — so the axis may not close the gap. | Light presmoothing before differentiation; boundary-pixel definition tuned for small particles (Deferred); U3 checks separation specifically on the small/overlapping regime, not just large particles. |
| Per-particle re-convolution slows large datasets. | Compute Sobel/Laplacian once per (cell, channel), index per particle; `compute_sharpness=False` skips it entirely on the per-cell summary path. |
| A future change adds column filtering to the particle export and silently drops the metrics. | U2 guard test asserts the columns reach `particles.csv`. |
| Accidental change to particle identity/area while editing the loop. | Characterization test (U1) locks existing columns/values byte-identical. |

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-07-14-out-of-focus-particle-sharpness-metrics-requirements.md](docs/brainstorms/2026-07-14-out-of-focus-particle-sharpness-metrics-requirements.md)
- Extension point: `src/percell4/domain/measure/particle.py` (`_iter_particles`, `analyze_particles_detail`, `_ParticleRecord`)
- Wholesale export: `src/percell4/workflows/phases.py` (`export_run` ~2714-2743, comment ~2567)
- Learnings: `docs/solutions/conventions/gui-panel-and-batch-workflow-method-parity-2026-07-13.md`, `docs/solutions/conventions/um2-area-sibling-columns-2026-06-29.md`, `docs/solutions/conventions/adaptive-clip-window-and-k-rules-2026-06-23.md`, `docs/solutions/architecture-patterns/registered-analysis-framework.md`
