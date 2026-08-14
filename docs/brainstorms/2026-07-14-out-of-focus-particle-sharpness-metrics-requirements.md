---
date: 2026-07-14
topic: out-of-focus-particle-sharpness-metrics
---

# Out-of-Focus Particle Filtering — Per-Particle Sharpness Metrics

## Problem Frame

P-body datasets (Dcp1A, Dcp1B, Dcp2) contain three particle classes the researcher
must analyze as clean, separate populations:

1. **In-focus P-bodies** — bright, compact, sharp intensity cutoff at the edge.
2. **Intermediate assemblies** — real, small (near diffraction-limited), fast-moving.
3. **Out-of-focus P-bodies** — dim, spread, hazy gradient edges. **Contamination** to be excluded from *both* real populations.

The two real populations are currently split by CNR (contrast-to-noise) in
`domain/measure/cnr_classification.py` into `<round>_low` / `<round>_high` masks
(renamed to `intermediate_mask` / `P-body_mask`). CNR is a *contrast/brightness* axis,
and out-of-focus P-bodies are genuinely low-contrast — so they fall into the same
low-CNR bin as true intermediate assemblies. For Dcp1B the intermediate bin is *mostly*
out-of-focus P-bodies; for Dcp1A they leak in too. CNR alone can never expel them.

**Data verdict (13,100 particles, `run_2026-07-14T162241Z_74f54dcf/particles.csv`):**
No existing per-particle feature cleanly separates the populations. Best separators are
all brightness/size proxies with heavy overlap: integrated intensity (AUC 0.86), std
(0.85), area (0.85), max (0.84). Every *sharpness proxy derivable from the existing
summary columns* (peak/mean, peak/area, CV, edge-fill) is weak (0.16–0.39 separation).
This is structural: "sharp cutoff vs. haze" is a property of the **radial intensity
profile at the particle edge**, which mean/max/std over interior pixels cannot capture —
and the detection pipeline presmooths (σ=1px), erasing edge steepness. Smoking gun: the
`intermediate_mask` area distribution runs from a 0.130 µm² floor to a **2.5 µm² tail**;
a diffraction-limited assembly cannot be that large, so those large-but-dim particles are
out-of-focus P-bodies mis-binned as "intermediate."

**Conclusion:** add a **spatial focus/sharpness axis measured on raw pixels** — the
missing second dimension of a size × sharpness feature space — as export-only per-particle
columns. The researcher explores the distributions and sets filter thresholds by eye
(eye is ground truth). No automatic classification in this scope.

---

## Requirements

**New per-particle sharpness metrics (raw-pixel, export-only)**

- R1. Compute three per-particle focus/sharpness metrics on the **raw** (un-presmoothed) channel crop inside `domain/measure/particle.py::_iter_particles`, using the per-particle boolean mask (`this_particle`), its bbox, and the raw `channel_crops` already available there.
- R2. **Edge-skirt ratio** — mean raw intensity in a thin annulus (~1–2px) immediately *outside* the particle mask, divided by the particle's peak intensity. Low = sharp cutoff (in-focus); high = haze leaks past the edge (out-of-focus). Exclude pixels belonging to other particles and outside the host cell from the annulus.
- R3. **Boundary gradient (Tenengrad)** — mean Sobel gradient magnitude on the raw crop, sampled at the particle boundary pixels, normalized by `(peak − local background)`. High = steep edge (in-focus); low = shallow (out-of-focus).
- R4. **Laplacian variance** — variance of the Laplacian of the raw crop over the particle's (optionally slightly dilated) bbox, normalized so the value is intensity-scale-invariant across cells/datasets. High = sharp high-frequency content (in-focus); low = blurred (out-of-focus).
- R5. Compute each metric **per channel**, matching the existing `{channel}_<metric>` column convention, so the researcher can read the detection channel's (mNG) columns and ignore the rest.
- R6. Surface the three metrics as new columns in the per-particle detail export (`analyze_particles_detail` → `particles.csv`). Handle degenerate inputs (single-pixel particle, flat region, particle touching the cell edge) by emitting a well-defined sentinel (e.g. `NaN` or `0.0`, consistent with the existing `try/except → 0.0` convention) rather than raising.

**Explicitly no filtering logic**

- R7. This scope adds **measurement only** — no threshold, no new mask, no reclassification, no changes to `cnr_classification.py` or the `_low`/`_high` split. The researcher applies filters downstream by eye.

---

## Acceptance Examples

- AE1. **Covers R1, R2, R5, R6.** Given the NP199 datasets re-run through particle analysis, when the CSV is opened, then it contains `mNG_edge_skirt_ratio`, `mNG_boundary_gradient`, `mNG_laplacian_variance` (and Halo equivalents), one value per particle.
- AE2. **Covers R2–R4.** Given a hand-picked in-focus P-body and a hand-picked out-of-focus P-body of similar integrated intensity, when their metrics are compared, then at least one of the three metrics separates them in the expected direction (in-focus: lower edge-skirt, higher boundary gradient, higher Laplacian variance).
- AE3. **Covers R6.** Given a 1-pixel particle or a particle on the cell border, when metrics are computed, then the row is emitted with sentinel values and the export does not error.

---

## Success Criteria

- The three sharpness columns appear per channel in `particles.csv`, computed on raw pixels.
- On the researcher's own eye-labeled in-focus vs. out-of-focus examples, at least one metric shows a visible, thresholdable separation — enough to draw a size × sharpness gate that yields a clean population.
- Priority is **precision (population purity), not recall** — dropping some true particles to guarantee no out-of-focus contamination is acceptable and expected.
- A downstream implementer can add the metrics from this doc without re-deriving product intent: extension point, raw-pixel requirement, per-channel convention, and export-only boundary are all specified.

---

## Scope Boundaries

- No automatic out-of-focus classification or filtering — export columns only (researcher's explicit choice; thresholds set by eye downstream).
- No new mask creation and no changes to CNR classification or the intermediate/P-body split.
- No changes required to the per-cell summary (`analyze_particles`); adding a mean-aggregated version of these metrics there is optional and deferred.
- Detection/masking is untouched — particles come from the existing masks as-is.
- Size (area / area_um2) is already exported; this scope adds only the missing sharpness axis, not new size features.

---

## Key Decisions

- **Add a raw-pixel sharpness axis rather than tune CNR.** Data showed the two populations overlap on every existing feature and that summary-stat sharpness proxies fail. Out-of-focus rejection is orthogonal to the contrast axis CNR already occupies, so it needs its own feature measured where the signal survives (raw pixels, at the edge).
- **Ship all three metrics, no threshold logic.** Intuitive proxies already failed once in the data; rather than commit to one metric on physics reasoning alone, export all three and let eye-validation on real distributions pick the winner. Keeps carrying cost low and matches the eye-is-ground-truth workflow.
- **Per-channel, following the existing column convention** — consistent with every other particle metric; the researcher selects the detection channel.

---

## Dependencies / Assumptions

- **Verified:** the images passed to `analyze_particles_detail` are raw channel intensities (CSV values are integer raw counts up to ~1300, un-presmoothed). Sobel/Laplacian read this raw signal directly.
- **Verified:** `_iter_particles` already exposes the raw per-channel crop (`channel_crops`), the per-particle boolean mask (`this_particle`), and the bbox slice — no new plumbing needed to reach raw pixels.
- `scipy.ndimage` (sobel, laplace, binary_dilation) and `skimage` are already dependencies of `domain/measure`.
- Pixel size ≈ 0.1204 µm/px (confirmed from `area_um2 / area`), matching prior P-body work.

---

## Outstanding Questions

### Deferred to Planning

- [Affects R2][Technical] Exact annulus width for edge-skirt (1px vs 2px) and how aggressively to exclude neighbor particles from it.
- [Affects R4][Technical] Normalization scheme for Laplacian variance to make it intensity-scale-invariant (e.g. divide by mean² or peak²) and the window (bbox vs bbox+dilation).
- [Affects R3][Needs research] Whether boundary-gradient normalization by `(peak − background)` is stable on the dimmest out-of-focus particles, or whether a different denominator behaves better — validate on real crops.
- [Affects R5][Technical] Whether to compute all three metrics for every channel or only the detection channel (per-channel is the safe default; may be trimmed for performance if the export slows materially).

---

## Next Steps

-> /ce-plan for structured implementation planning
