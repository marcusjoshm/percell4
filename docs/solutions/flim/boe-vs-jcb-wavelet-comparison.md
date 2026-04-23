---
title: BOE 2021 vs. JCB 2025 FLIM wavelet filter — empirical comparison
category: flim
tags: [flim, phasor, wavelet, dtcwt, bishrink, denoising]
date: 2026-04-23
---

# BOE 2021 vs. JCB 2025 FLIM wavelet filter — empirical comparison

## TL;DR

percell4 now ships two FLIM complex-wavelet-filter implementations behind
a single GUI selector and use-case dispatch. **BOE 2021 is the default**
(strict Sendur-Selesnick BiShrink with `(σ_n² − σ_g²)_+`, from Wang et
al. 2021 *Biomed. Opt. Express*). **JCB 2025** (a faithful port of
`LeeLabBCM/ComplexWaveletFilter`, the code behind Fahim & Marcus 2025
*JCB*) remains available to reproduce the published paper.

The two algorithms are **not interchangeable**. Pick based on the
downstream task:

- **Reproducing the JCB 2025 paper, or any result that used the Lee-Lab
  python tool**: set **JCB**.
- **New analyses, phasor segmentation, visualisation of mixed
  populations**: use **BOE** — it is faster (3-4× on multi-megapixel
  images), paper-accurate, and less aggressive about smoothing real
  biological spread in the phasor cluster.

Both algorithms use the same DTCWT filter banks (`biort='legall'`,
`qshift='qshift_a'`); they differ in how σ_g is estimated and how the
shrinkage factor is computed from the wavelet coefficients. See the
"What's different" section below for the exact math.

## What's different between the two filters

| Aspect | BOE 2021 | JCB 2025 | Consequence |
|---|---|---|---|
| Global σ_g | `median(\|Φ(lvl 1, ±45°)\|) / 0.6745` (MAD on diagonal-detail bands at the finest level only) | `mean_over_all_bands(median(\|Φ(l, b)\|)) / 0.6745` | BOE estimates σ_g from noise-dominated coefficients only. JCB averages across levels that carry real signal, which biases σ_g upward. |
| Shrinkage factor | `(1 − √3·σ_g² / √((\|Φ_l\|² + \|Φ_parent\|²) · (σ_n² − σ_g²)_+))_+ · Φ_l` | `(1 − √3·σ_g / √(\|Φ_l\|² + \|Φ_parent\|² + √3·σ_g))_+ · Φ_l` (simpler; drops `(σ_n² − σ_g²)_+`) | The JCB formula ignores local variance in the shrinkage magnitude — the result is uniform aggressive shrinkage everywhere the sum of coefficient magnitudes is small. BOE's local-variance factor makes shrinkage adaptive: where local variance is barely above the noise floor, BOE barely shrinks. |
| Local-variance window N | `3` (uniform — 7×7 window) | `min(3, flevel)` when `flevel > 10`, else `flevel` (e.g. 19×19 at flevel=9) | JCB's large window smears local-variance estimates over a broad region; BOE's Sendur-Selesnick-standard 7×7 tracks local structure more closely. |
| Padding | `next_multiple(n, 2**L)` (tighter) | `next_pow2(n)` (matches upstream repo) | BOE pads less, saving memory on non-pow2 inputs. Numerically identical on pow2-sized images. |
| 3-channel execution | `ThreadPoolExecutor(max_workers=3)` over F_real / F_imag / I | sequential | BOE is 3-4× faster on large inputs. |
| Inverse Anscombe | Mäkitalo-Foi 6th-order closed-form | same | no difference |

### How we ended up with two filters

The JCB-style algorithm in percell4 is a vectorised port of
`LeeLabBCM/ComplexWaveletFilter` — the Python code published alongside
Fahim, Marcus et al. 2025 *JCB*. That reference implementation itself
deviates from the original Wang et al. 2021 *BOE* (Leica) paper it is
based on in two ways: (1) global σ_g is estimated across all bands
rather than just the diagonal bands at level 1, and (2) the Sendur-
Selesnick BiShrink shrinkage formula is simplified by dropping the
`(σ_n² − σ_g²)_+` term, so the local noise variance acts only as a gate.

Separately, the percell4 port introduced a silent regression:
`biort='near_sym_a'` (a completely different biorthogonal filter bank)
instead of `biort='legall'` (the LeGall 5/3 used by both the paper and
the upstream repo). That regression is **fixed in this release**;
percell4's JCB mode now matches upstream `LeeLabBCM/ComplexWaveletFilter`
exactly.

## Empirical results

### Synthetic — Siemens-star spoke phantom (BOE Fig. S2 replica)

512×512, 256 time bins, 80 MHz, 0.1 photons/pulse, 3 µs dwell, 1.5 ns /
2.5 ns bi-exponential, `filter_level=9`, reference = 500-frame
accumulation, seed=0. Reproducible:

```
python -m percell4.interfaces.cli.bench_wavelet synthetic --out ./out --seed 0
```

| Metric | Unfiltered | BOE | JCB | BOE/JCB |
|---|---|---|---|---|
| Whole-image G/S MSE | 2.3e-2 | **3.0e-3** | 6.2e-3 | **0.48 — BOE wins** |
| High-freq G-MSE (≥ 0.25 c/px) | — | 2.1e-3 | **1.7e-3** | 1.22 — JCB wins |
| BOE filter runtime | — | ~0.2 s | — | |
| JCB filter runtime | — | — | ~0.2 s | |

BOE cuts whole-image MSE to less than half of JCB's, confirming that
the Sendur-Selesnick full-formula does better on average. At high
spatial frequencies, however, JCB edges out BOE — the opposite of what
we expected going in. One interpretation: on a sharp-edged synthetic
phantom, BOE's local-variance factor becomes very large near edges,
which *reduces* shrinkage there and lets more high-frequency error
survive. JCB's simpler formula smooths uniformly and happens to suit
this phantom's statistics.

### Real — 3072×3072 FLIM dataset (A549, mNG-tagged, 78 MHz)

`/Users/leelab/Documents/As.h5`, channel `ch2`, `filter_level=9`.

```
python -m percell4.interfaces.cli.bench_wavelet real \
    --h5 /Users/leelab/Documents/As.h5 --channel ch2 \
    --filter-level 9 --out ./out
```

| Metric | Unfiltered | BOE | JCB |
|---|---|---|---|
| Filter runtime | — | **5.5 s** (3 threads) | 21.0 s (sequential) |
| Phasor cluster IQR (G) | 0.203 | 0.176 (−13%) | **0.132 (−35%)** |
| Phasor cluster IQR (S) | 0.175 | 0.172 (−2%) | **0.076 (−57%)** |

JCB produces a **dramatically tighter** phasor cluster. On single-
acquisition real data where the photon budget is actually decent
(this is a 3072×3072 tile scan), the "less is more" behaviour of JCB's
simpler shrinkage over-smooths the phasor distribution — but that may
or may not be desirable depending on what you're trying to do:

- **For visualisation / single-population phasor segmentation** (one
  fluorophore dominates), JCB's tighter cluster reads more cleanly.
- **For multi-population analysis** (FRET, autofluorescence,
  bound/unbound states), the natural spread in the phasor cluster
  *carries biology*. Over-tight clustering can erase subpopulation
  structure that BOE would preserve.

We have not run a rigorous multi-population recovery test to quantify
this tradeoff on a dataset where we know the answer.

## When which filter wins

Based on the experiments above and the algorithmic differences:

| Scenario | Recommendation |
|---|---|
| New analysis, mixed fluorophore populations, FLIM-FRET | **BOE** (paper-accurate; preserves natural spread) |
| Reproducing a JCB 2025 analysis or downstream plots | **JCB** (matches the published code) |
| Single dominant lifetime, cluster segmentation for masks | Either works; JCB gives a visually tighter cluster. Validate against a ground-truth population count if that matters. |
| Very low photon budget (<5 photons/pixel/frame, e.g. τ-STED) | **BOE** — the BiShrink `(σ_n² − σ_g²)_+` factor is specifically designed for this regime; JCB's simplification is the source of the drop that the BOE paper was written to fix. |
| Large (≥ 2048²) images, runtime-sensitive | **BOE** — 3-4× faster thanks to ThreadPoolExecutor over the three channels. |

## Caveats and future work

1. **No multi-population recovery benchmark.** Most of what we observe
   on real data is "JCB over-tightens the cluster more than BOE." We
   can't yet say "JCB erases real biology" without a controlled test
   on a dataset with known subpopulations. This is a good candidate
   for a Phase 5 writeup.
2. **Single real dataset tested.** `As.h5` is one acquisition; results
   may not generalise to samples with different photon budgets or
   intrinsic phasor spreads.
3. **Paper reproduction finding differs from the paper.** The BOE
   paper claims the filter outperforms simpler wavelet variants at
   high spatial frequencies. Our synthetic benchmark shows the
   opposite at flevel=9 on a Siemens-star phantom. Plausible
   explanations: (a) paper used a different ground-truth construction,
   (b) the phantom we generate has sharper edges than the paper's,
   (c) the flevel=9 is over-decomposing for a 512² input. Would be
   worth reproducing the paper's exact MSE-vs-spatial-frequency setup
   more carefully before calling this a discrepancy.
4. **Upstream LeeLabBCM/ComplexWaveletFilter is unchanged.** The JCB
   mode in percell4 is faithful to the upstream repo today. If a
   future change realigns that repo with the BOE paper, we'd want to
   reflect that here.

## How to switch filters

Open the FLIM panel, find the **Algorithm** dropdown in the Wavelet
Filter section, pick "BOE" (default) or "JCB", click Apply Wavelet
Filter. The selection is remembered across sessions via
`QSettings("leelab", "percell4")`. If the channel already has filtered
data from a different algorithm, a warning dialog asks before
overwriting.

Programmatically:

```python
from percell4.application.use_cases.apply_wavelet import ApplyWavelet

ApplyWavelet(repo, session).execute(
    channel="ch2",
    filter_level=9,
    algorithm="boe_2021",  # or "jcb_2025"
)
```

All filtered datasets now carry provenance attrs (`algorithm`, `biort`,
`qshift`, `n_local_window`, `sigma_g_estimator`, `shrinkage`,
`dtcwt_version`, `percell4_version`) so it's always clear from an
HDF5 file which algorithm produced which result. Datasets written
before this change are treated as `"jcb_2025"` by default (the
`read_wavelet_algorithm` helper in `percell4.store_schema`).
