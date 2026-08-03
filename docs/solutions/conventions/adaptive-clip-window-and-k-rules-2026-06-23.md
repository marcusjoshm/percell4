---
title: "Setting the Adaptive Local Clipping knobs: window and k as a band-pass + per-cell z-score"
date: 2026-06-23
category: conventions
module: src/percell4/domain/measure
problem_type: convention
component: adaptive_local_clipping
severity: medium
canonical_source: src/percell4/domain/measure/adaptive_clip.py
applies_to:
  - "src/percell4/domain/measure/adaptive_clip.py"
  - "src/percell4/domain/measure/window_finders.py"
  - "src/percell4/gui/adaptive_clip_panel.py"
  - "src/percell4/gui/_adaptive_clip_settings.py"
applies_when:
  - "Changing how the adaptive-clip window_px is derived (PARTICLE_WINDOW_FACTOR, window finders, d_min mapping)"
  - "Changing or exposing the adaptive-clip k value"
  - "Adding an auto-window finder or a k-selection rule"
  - "Explaining why a large particle hollows out (rings/annuli) or why junk gets picked up"
tags: [adaptive-clip, thresholding, dog, band-pass, z-score, window, k, puncta]
---

# Setting the Adaptive Local Clipping knobs: window and k

The two knobs of `detect_adaptive_per_cell`
(`src/percell4/domain/measure/adaptive_clip.py`) — the **window** and **k** — are
not two arbitrary dials. The detector is a **Difference-of-Gaussians band-pass
followed by a per-cell z-score**, and once you see it that way each knob has one
job, and the eye-validated constants in the code (`PARTICLE_WINDOW_FACTOR = 6.0`,
`k = 1.0`) fall out of the math. This doc is the canonical explanation of *why*
those constants are what they are and how to reason about changing them.

## What the operation actually is

The core (`adaptive_clip.py:230-251`):

```python
work = apply_gaussian_smoothing(img, presmooth_sigma_px)   # blur at σ_pre = 1 px
diff = work - gaussian_filter(work, (window_px - 1) / 6.0)  # subtract local mean at σ_bg
sigma = 1.4826 * median(|vals - median(vals)|)             # robust noise, PER CELL
out |= (diff > k * sigma) & cell                           # z-score test
```

`work` is the image blurred at `σ_pre`; `background` is `work` blurred again at
`σ_bg = (window_px−1)/6`. Convolving two Gaussians adds variances, so

- `background` = image blurred at `σ_eff = √(σ_pre² + σ_bg²)`
- `diff = work − background = image ⊛ [G(σ_pre) − G(σ_eff)]`

That bracket is a **band-pass kernel** (a Difference-of-Gaussians). It keeps
structure whose size sits between `σ_pre` (smallest resolvable, set by
presmoothing) and `σ_bg` (largest preserved, set by the window). Then
`diff > k·σ_cell` is a **z-score test**: a pixel is signal when its band-pass
contrast clears `k` robust noise-sigmas *of its own cell*.

So the two knobs are **orthogonal axes**:

| Knob | Code | Axis | Job |
|------|------|------|-----|
| **window** (`σ_bg`) | `window_px`, fixed pixels | length-scale | which object *sizes* survive the band-pass |
| **k** | `k`, z-score on per-cell `1.4826·MAD` | contrast/stringency | how many noise-sigmas of contrast a pixel must clear |

## Why the window controls hollow-vs-solid (the F formula)

For an idealized feature — a Gaussian bump of width `σ_f`, peak contrast `A` — the
fraction of the feature's (working-scale) peak that survives the band-pass **at
the feature's center** has a closed form:

> **F = σ_bg² / (σ_f² + σ_pre² + σ_bg²)**

This one expression explains every window behavior we see:

- **`σ_bg ≫ σ_f` (large window): F → 1.** The local mean is taken over a region
  much bigger than the feature, so the feature stands fully above its surround →
  **solid fill**.
- **`σ_bg ≪ σ_f` (small window): F → σ_bg²/σ_f² → 0.** The local mean sits *inside*
  the feature and tracks its intensity, so the interior subtracts itself away →
  the center drops below threshold → **holes**. Edges always survive (they carry a
  gradient), so an under-windowed large feature shows up as a **ring / annulus** —
  a recognizable signature.
- **F = 0.5 at `σ_bg ≈ σ_f`.** You need `σ_bg` comparable to the feature width to
  keep half the center contrast, and ~2× for ~80%.

A center pixel is detected **solid** when its surviving contrast beats the bar:

> **F · CNR > k**,  where CNR = (working-scale feature contrast) / σ_cell

That inequality *is* the window–k trade-off. To fill a large feature, raise `F`
(bigger window) **or** lower the bar (smaller `k`). To reject an unwanted broad
structure, do the opposite — shrink the window (its `F` drops) **or** raise `k`.
This is why "too large a window picks up junk, fixed by raising `k` *or* shrinking
the window," and why it is dataset-dependent: it only bites when a *nuisance*
structure has contrast in the range where `F·CNR` crosses `k` as the window grows.

## Why the constants in the code are right

### Window rule — `window_px = 6 · d_min / pixel` (`PARTICLE_WINDOW_FACTOR = 6.0`)

`window_min_spot_for_particle` sets `window_px ≈ 6·D` where `D = d_min` in pixels.
Since `σ_bg = (window_px−1)/6`, this means

> **σ_bg ≈ D ≈ 2 × the feature half-width.**

The eye-validated "6×" is really "`σ_bg` at ~2× the half-width" in disguise — the
band-pass large-scale cutoff sits a factor of ~2 above the particle, putting the
targeted particle **in the solid-fill regime**. Concretely (σ_pre = 1 px,
disk→Gaussian `σ_f ≈ D/4`, pixel = 0.120369 µm/px — the two eye-validated
condensate types):

| Feature | d_min | D (px) | window | σ_bg | σ_bg / half-width | **F_center** | min_spot_px |
|---------|-------|--------|--------|------|-------------------|--------------|-------------|
| Stress granule (G3BP1) | 0.40 µm | 3.3 | 21 px | 3.33 | 2.0× | **0.87** | 9 |
| Mid (illustrative) | 0.80 µm | 6.6 | 41 px | 6.67 | 2.0× | **0.92** | 35 |
| P-body (DDX6) | 0.14 µm | 1.2 | 7 px | 1.00 | 1.7× | **0.48** | 1 (off) |

Resolved particles (granules, mid) land at **F ≈ 0.87–0.92** — solidly filled, no
hollow centers. P-bodies drop to **F ≈ 0.5** *not* because the window is wrong but
because they are **diffraction-limited**: `σ_pre = 1 px` is comparable to the
feature itself, so presmoothing — not the window — caps the fill fraction. That is
the regime where the next two design choices earn their keep.

**Why the window must be set in physical units.** `σ_bg` is a fixed *pixel*
length, so it does **not** auto-scale; but the feature size you care about is fixed
in *microns*. Hence the rule is written in µm and converted per image via the
pixel size — which is exactly what `detect_adaptive_by_particle_size` /
`window_min_spot_for_particle` do, and why it transfers across magnifications.

### k rule — `k = 1.0`, a per-cell z-score

`σ_cell = 1.4826·MAD(work)` is computed **per cell**, so `k` is a z-score on each
cell's *own* noise scale. **The same `k` therefore delivers the same statistical
stringency across cells of wildly different brightness** (observed 3× within a
field, 40× across datasets) — this is the property that lets one number transfer.
`k ≈ 1` is the **permissive** end (k≈2–2.5 typical, k≈3+ strict for Gaussian
band-pass noise). Permissive is correct here because:

1. The band-pass **attenuates a sliver of low-frequency noise**, so the *realized*
   false-positive rate is stricter than nominal `k = 1` implies.
2. Diffraction-limited features (P-bodies, F ≈ 0.5) only keep half their contrast
   through the band-pass — they need the bar low to be detected at all.
3. The per-cell σ already normalizes brightness, so `k` carries pure stringency.

**Caveat (know before you trust it):** `1.4826·MAD ≈ noise` only when the cell is
noise-dominated. A cell with strong **fine texture** inflates MAD, so the *same*
`k` is effectively *stricter* there. If detection looks unexpectedly conservative
in textured cells, this — not the window — is the likely cause.

## The rule, in one line

> **Measure size → window** (`window_px = 6·d_min/pixel`, set from physical
> feature size, never eyeballed in pixels). **Choose stringency → k** (`k = 1`
> permissive default; raise toward 2–3 to reject nuisance structure that overlaps
> your features in size). The window controls *hollow-vs-solid*; `k` controls
> *how much junk*; they are independent.

## Failure modes & what to reach for

- **Large particles hollow out / show rings** → window too small for *those*
  particles (`F` low). This is the motivation for the multi-scale routine
  (`detect_adaptive_multiscale`): OR-union over a doubling window sequence so each
  size band gets a window that fills it. See
  `docs/brainstorms/2026-06-22-multiscale-adaptive-clip-routine-requirements.md`.
- **Broad junk picked up** → raise `k`, *or* shrink the window (drops the
  nuisance's `F`). Prefer `k` when the junk and your features differ in contrast;
  prefer window when they differ in size.
- **Wanted and unwanted features overlap in *both* size and contrast** → no
  `(window, k)` separates them. Fall back to the `min_spot_px` area filter
  (`_filter_by_area`) or shape features. This is the one genuine dead end.

## Optional: pinning the constants empirically

The constants above are **eye-validated** (2026-06-12, four datasets, two
condensate types) and adopted as-is — this rule stands alone. If higher precision
is ever wanted, a **synthetic phantom sweep** pins them from ground truth: place
Gaussian/disk features of known `σ_f`, contrast, and noise; run
`detect_adaptive_per_cell` over a `window × k` grid; record detected-area÷true-area
(hole metric), center-solid, and false-positive area. Plotting fill fraction vs
`σ_bg/σ_f` should collapse onto the **F** curve (validating the model and
calibrating the exact disk-vs-Gaussian multiplier in `window_px ≈ N·diameter`),
and the realized FP(k) curve gives the empirical `k ↔ false-positive-rate` map
that accounts for the texture caveat. Not required for the rule to be usable.
